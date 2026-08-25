"""Background TOC fetch queue.

Adding a book to the shelf enqueues a ``TocJob``; a single worker drains the
queue sequentially (one book at a time), resolves the real toc url via
``book_info``, stores chapters into ``book_chapters`` and refreshes shelf
metadata. Failures are recorded on the job row for UI display.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db import get_session_factory
from ..models import BookChapter, BookRef, BookSourceRow, ShelfItem, TocJob
from ..models import utcnow
from ..plugins.registry import PluginContext, get_engine

# keep this many finished job rows around for status display
_JOB_HISTORY_LIMIT = 800


class TocQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[int] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    # ------------------------------------------------------------- lifecycle
    def ensure_worker(self) -> None:
        loop = asyncio.get_running_loop()
        if self._worker is not None and not self._worker.done():
            if self._worker.get_loop() is loop:
                return
        self._worker = loop.create_task(self._run())

    def enqueue(self, job_id: int) -> None:
        self.ensure_worker()
        self._q.put_nowait(job_id)

    async def _run(self) -> None:
        while True:
            job_id = await self._q.get()
            try:
                await process_job(job_id)
            except Exception as exc:  # noqa: BLE001 - worker must never die
                try:
                    await _mark_error(job_id, f"{type(exc).__name__}: {exc}")
                except Exception:  # noqa: BLE001
                    pass
            finally:
                self._q.task_done()

    # -------------------------------------------------------------- helpers
    @staticmethod
    async def has_active(session: AsyncSession, source_url: str, book_url: str) -> bool:
        row = await session.scalar(
            select(TocJob).where(
                TocJob.source_url == source_url,
                TocJob.book_url == book_url,
                TocJob.status.in_(("queued", "running")),
            )
        )
        return row is not None

    @staticmethod
    async def create_job(
        session: AsyncSession, source_url: str, book_url: str
    ) -> TocJob | None:
        """Create + enqueue a job unless one is already active for this book."""
        if await TocQueue.has_active(session, source_url, book_url):
            return None
        job = TocJob(source_url=source_url, book_url=book_url, status="queued")
        session.add(job)
        await session.commit()
        await _prune_history(session)
        return job


toc_queue = TocQueue()


# ----------------------------------------------------------------- processing
async def process_job(job_id: int) -> None:
    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(TocJob, job_id)
        if job is None or job.status != "queued":
            return
        job.status = "running"
        await session.commit()

        source_url, book_url = job.source_url, job.book_url
        row = await session.scalar(
            select(BookSourceRow).where(BookSourceRow.source_url == source_url)
        )
        if row is None or not row.enabled:
            job.status = "error"
            job.error = "书源不存在或已停用"
            await session.commit()
            return
        src = json.loads(row.raw_json)

        # seed known fields from any shelf entry or cached ref of this book
        shelf = await session.scalar(
            select(ShelfItem).where(
                ShelfItem.source_url == source_url,
                ShelfItem.book_url == book_url,
            )
        )
        ref = await session.scalar(
            select(BookRef).where(
                BookRef.source_url == source_url,
                BookRef.book_url == book_url,
            ).limit(1)
        )

        def _seed(attr: str, ref_attr: str) -> str:
            v = (getattr(shelf, attr, "") or "") if shelf is not None else ""
            if not v and ref is not None:
                v = (getattr(ref, ref_attr, "") or "")
            return v

        book = {
            "bookUrl": book_url,
            "name": _seed("name", "name"),
            "author": _seed("author", "author"),
            "coverUrl": _seed("cover_url", "cover_url"),
        }

    ctx = PluginContext(settings=None)
    eng = get_engine(getattr(row, "engine", None), ctx)
    try:
        try:
            info = await eng.book_info(src, dict(book))
        except Exception:  # noqa: BLE001 - detail page failed; toc page may still work
            info = dict(book)
        toc_url = str(info.get("tocUrl") or "").strip() or book_url
        chapters = await eng.get_toc(src, info, toc_url)
    except Exception as exc:  # noqa: BLE001
        async with factory() as s2:
            j2 = await s2.get(TocJob, job_id)
            if j2 is not None:
                j2.status = "error"
                j2.error = f"{type(exc).__name__}: {exc}"[:2000]
                await s2.commit()
        return

    async with factory() as session:
        # 记录刷新前的目录状态，用于检测书源是否有内容变化（新章/换章）
        prev_count = await session.scalar(
            select(func.count(BookChapter.id)).where(
                BookChapter.source_url == source_url,
                BookChapter.book_url == book_url,
            )
        )
        prev_last = await session.scalar(
            select(BookChapter).where(
                BookChapter.source_url == source_url,
                BookChapter.book_url == book_url,
            ).order_by(BookChapter.idx.desc(), BookChapter.id.desc()).limit(1)
        )
        await session.execute(
            delete(BookChapter).where(
                BookChapter.source_url == source_url,
                BookChapter.book_url == book_url,
            )
        )
        for i, ch in enumerate(chapters):
            session.add(BookChapter(
                source_url=source_url,
                book_url=book_url,
                idx=int(ch.get("index", i)),
                title=str(ch.get("title") or ""),
                url=str(ch.get("url") or ""),
                base_url=str(ch.get("baseUrl") or toc_url),
                is_volume=bool(ch.get("isVolume")),
                is_vip=bool(ch.get("isVip")),
            ))
        # refresh shelf metadata for every user holding this book
        shelves = (await session.execute(
            select(ShelfItem).where(
                ShelfItem.source_url == source_url,
                ShelfItem.book_url == book_url,
            )
        )).scalars().all()
        last_chapter = str(info.get("lastChapter") or "").strip() \
            or (chapters[-1]["title"] if chapters else "")

        # 更新检测：仅当此前已有目录且章节数或最新一章发生变化才算书源更新
        # （首次抓取是初始填充，不应当作「有更新」打扰用户）
        new_last_title = str(chapters[-1].get("title") or "") if chapters else ""
        new_last_url = str(chapters[-1].get("url") or "") if chapters else ""
        source_changed = bool(prev_count) and (
            prev_count != len(chapters)
            or (prev_last is not None
                and (prev_last.title or "", prev_last.url or "")
                != (new_last_title, new_last_url))
        )
        now = utcnow()
        for it in shelves:
            it.toc_url = toc_url
            if last_chapter:
                it.last_chapter = last_chapter
            if not it.cover_url and info.get("coverUrl"):
                it.cover_url = str(info["coverUrl"])
            if source_changed:
                it.updated_at = now
                it.has_update = True
        # 同步回填书籍短链档案：阅读器/详情页即可全缓存展示（含简介）
        refs = (await session.execute(
            select(BookRef).where(
                BookRef.source_url == source_url,
                BookRef.book_url == book_url,
            )
        )).scalars().all()
        for ref in refs:
            ref.toc_url = toc_url
            for key in ("intro", "kind"):
                v = str(info.get(key) or "").strip()
                if v:
                    setattr(ref, key, v)
            if last_chapter:
                ref.last_chapter = last_chapter
            if not ref.cover_url and info.get("coverUrl"):
                ref.cover_url = str(info["coverUrl"])
        j3 = await session.get(TocJob, job_id)
        if j3 is not None:
            j3.status = "done"
            j3.chapters = len(chapters)
            j3.error = ""
        await session.commit()


async def _mark_error(job_id: int, message: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(TocJob, job_id)
        if job is not None:
            job.status = "error"
            job.error = message[:2000]
            await session.commit()


async def _prune_history(session: AsyncSession) -> None:
    total = await session.scalar(select(func.count(TocJob.id)))
    if total is None or total <= _JOB_HISTORY_LIMIT:
        return
    threshold = await session.scalar(
        select(TocJob.id).order_by(TocJob.id.desc()).offset(_JOB_HISTORY_LIMIT).limit(1)
    )
    if threshold is not None:
        await session.execute(delete(TocJob).where(TocJob.id < threshold))
        await session.commit()


async def latest_job_map(session: AsyncSession) -> dict[tuple[str, str], TocJob]:
    """Newest job per (source_url, book_url), for shelf status display."""
    out: dict[tuple[str, str], TocJob] = {}
    rows = (await session.execute(
        select(TocJob).order_by(TocJob.id.desc()).limit(_JOB_HISTORY_LIMIT)
    )).scalars()
    for j in rows:
        key = (j.source_url, j.book_url)
        if key not in out:
            out[key] = j
    return out


def chapters_to_dicts(rows: Iterable) -> list[dict]:
    return [
        {
            "url": r.url,
            "baseUrl": r.base_url,
            "title": r.title,
            "index": r.idx,
            "isVolume": r.is_volume,
            "isVip": r.is_vip,
        }
        for r in rows
    ]
