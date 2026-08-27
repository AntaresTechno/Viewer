"""legado 进度自动入库 — 让"两端数据同步"真正成立。

legado 的 BookProgress JSON 只有 书名/作者/章节位置，没有书源信息。
当上传的进度在本站书架找不到同名书时，仅存文件无法在网页端阅读。
这里做一次尽力而为的自动匹配：

1. 用本站启用的书源按书名搜索（并发受限、单源超时受控）；
2. 候选要求书名完全一致（归一化后），作者一致优先、有封面优先；
3. 命中则创建书架条目（保留 legado 上传的原始 书名/作者，
   保证进度文件名与 legado 端一致）+ 写入阅读进度 + 投递目录抓取任务；
4. 未命中记录标记（6 小时内不重复搜索），可在「待匹配」列表里查看。

匹配结果记入 AppKV（davmatch:<user>:<file>），避免每次上传都触发全源搜索。
"""
from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ...models import BookSourceRow as SourceRow

_RETRY_NONE_AFTER = timedelta(hours=6)
_MAX_SOURCES = 12


def _norm(s: str | None) -> str:
    return (s or "").strip().casefold()


def _marker_key(user_id: int, fname: str) -> str:
    return f"davmatch:{user_id}:{fname}"


def _read_marker(marker_value: str | None) -> dict:
    try:
        data = json.loads(marker_value or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# 单飞行锁：同一本书的并发上传只触发一次匹配
_ingest_locks: dict[str, asyncio.Lock] = {}
_INGEST_TASKS: set[asyncio.Task] = set()


def spawn_ingest(
    user_id: int, *, name: str, author: str, idx: int, pos: int,
    tms: int, title: str,
) -> None:
    """后台执行自动入库（不阻塞 PUT 响应；异常只记日志）。"""
    lock = _ingest_locks.setdefault(f"{user_id}:{name}:{author}",
                                    asyncio.Lock())
    if lock.locked():
        return

    async def runner() -> None:
        async with lock:
            try:
                await auto_match_and_ingest(
                    user_id, name=name, author=author, idx=idx, pos=pos,
                    tms=tms, title=title,
                )
            except Exception:  # noqa: BLE001 - 后台任务绝不影响主流程
                try:
                    print("[webdav] legado 进度自动入库失败:\n"
                          + traceback.format_exc())
                except UnicodeEncodeError:
                    print("[webdav] legado ingest failed (unicode)")
            finally:
                _ingest_locks.pop(f"{user_id}:{name}:{author}", None)

    task = asyncio.get_running_loop().create_task(runner())
    _INGEST_TASKS.add(task)
    task.add_done_callback(_INGEST_TASKS.discard)


async def auto_match_and_ingest(
    user_id: int, *, name: str, author: str, idx: int, pos: int,
    tms: int, title: str,
) -> str:
    """尝试把一本"只在 legado 存在"的书带入本站。返回动作结果。"""
    from ...core.db import get_session_factory
    from ...models import AppKV, ReadProgress, ShelfItem
    from ...services.toc_queue import toc_queue as _tq
    from .dav_server import progress_filename

    name = (name or "").strip()
    author = (author or "").strip()
    fname = progress_filename(name, author)

    factory = get_session_factory()

    async def _merge_progress(session, item: ShelfItem) -> bool:
        """把上传进度写进该书（仅当云端更新时）。返回是否写入。"""
        cur = (await session.execute(
            select(ReadProgress).where(
                ReadProgress.user_id == user_id,
                ReadProgress.book_url == item.book_url,
            )
        )).scalars().first()
        incoming_ms = tms if tms > 0 else int(
            datetime.now(timezone.utc).timestamp() * 1000)
        if cur is not None:
            cur_ms = _naive_ms(cur.updated_at)
            newer = (incoming_ms > cur_ms
                     or (incoming_ms == cur_ms
                         and (idx, pos) > (cur.chapter_index, cur.offset)))
            if not newer:
                return False
        if cur is None:
            session.add(ReadProgress(
                user_id=user_id, book_url=item.book_url,
                chapter_index=idx, chapter_title=title, offset=pos,
                updated_at=_ms_to_dt(incoming_ms),
            ))
        else:
            cur.chapter_index = idx
            cur.chapter_title = title
            cur.offset = pos
            cur.updated_at = _ms_to_dt(max(incoming_ms,
                                           _naive_ms(cur.updated_at)))
        return True

    # ------------------------------------------------------ 标记与竞态预检
    async with factory() as s:
        marker = await s.get(AppKV, _marker_key(user_id, fname))
        if marker is not None:
            data = _read_marker(marker.value)
            ts = _parse_iso(data.get("ts"))
            if data.get("status") == "matched":
                return "already"
            if data.get("status") == "none" and ts is not None \
                    and datetime.now(timezone.utc) - ts < _RETRY_NONE_AFTER:
                return "recent-none"

        # 搜索期间书架可能已被手动添加 / 并发上传处理过 —— 先查一次
        hit = await _find_shelf_match(s, user_id, name, author)
        if hit is not None:
            changed = await _merge_progress(s, hit)
            await _write_marker(s, user_id, fname, "matched")
            await s.commit()
            return "mirrored" if changed else "kept"

    # ------------------------------------------------------------ 多源搜索
    async with factory() as s:
        rows = (await s.execute(
            select(SourceRow).where(SourceRow.enabled)
            .order_by(SourceRow.custom_order, SourceRow.id)
            .limit(_MAX_SOURCES)
        )).scalars().all()

    candidates = await _search_sources(rows, name)

    # ------------------------------------------------------------ 入库落库
    async with factory() as s:
        best = _pick_best(candidates, name, author)
        hit = await _find_shelf_match(s, user_id, name, author)
        if hit is not None:  # 搜索期间出现，优先复用
            changed = await _merge_progress(s, hit)
            await _write_marker(s, user_id, fname, "matched")
            await s.commit()
            return "mirrored" if changed else "kept"
        if best is None or not (best.get("bookUrl") or ""):
            await _write_marker(s, user_id, fname, "none")
            await s.commit()
            return "none"

        item = ShelfItem(
            user_id=user_id,
            book_url=str(best["bookUrl"]),
            toc_url="",
            # 保留 legado 上传的原始 书名/作者：进度文件名必须与 legado 一致
            name=name,
            author=author,
            cover_url=str(best.get("coverUrl") or ""),
            intro=str(best.get("intro") or ""),
            last_chapter=str(best.get("lastChapter") or ""),
            source_url=str(best.get("origin") or ""),
        )
        s.add(item)
        await s.flush()
        await _merge_progress(s, item)
        job = await _tq.create_job(
            s, str(best.get("origin") or ""), str(best["bookUrl"]))
        await _write_marker(s, user_id, fname, "matched")
        await s.commit()
        if job is not None:
            _tq.enqueue(job.id)
        return "matched"


# ------------------------------------------------------------------ 内部工具
def _naive_ms(dt) -> int:
    if dt is None:
        return 0
    dt = dt if dt.tzinfo else dt.astimezone()
    return int(dt.timestamp() * 1000)


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _parse_iso(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


async def _write_marker(session, user_id: int, fname: str,
                        status: str) -> None:
    from ...models import AppKV

    key = _marker_key(user_id, fname)
    row = await session.get(AppKV, key)
    value = json.dumps({"status": status,
                        "ts": datetime.now(timezone.utc).isoformat()})
    if row is None:
        session.add(AppKV(key=key, value=value))
    else:
        row.value = value


async def _find_shelf_match(session, user_id: int, name: str,
                            author: str):
    """书名归一化相同即视为同一本书；作者一致者优先。"""
    from ...models import ShelfItem

    shelf = (await session.execute(
        select(ShelfItem).where(ShelfItem.user_id == user_id)
    )).scalars().all()
    exact = [it for it in shelf if _norm(it.name) == _norm(name)]
    if not exact:
        return None
    same_author = [it for it in exact if _norm(it.author) == _norm(author)]
    return same_author[0] if same_author else exact[0]


async def _search_sources(rows, key: str) -> list[dict]:
    from ...plugins.registry import get_engine

    sem = asyncio.Semaphore(4)

    async def one(row) -> list[dict]:
        try:
            src = json.loads(row.raw_json)
        except Exception:  # noqa: BLE001
            return []
        try:
            eng = get_engine(getattr(row, "engine", None))
        except KeyError:
            return []
        async with sem:
            try:
                return await asyncio.wait_for(
                    eng.search_book(src, key, 1), timeout=20)
            except Exception:  # noqa: BLE001 - 单源失败不影响整体
                return []

    results = await asyncio.gather(*(one(r) for r in rows))
    flat: list[dict] = []
    for group in results:
        flat.extend(group or [])
    return [c for c in flat if isinstance(c, dict) and (c.get("name") or "").strip()]


def _pick_best(items: list[dict], name: str, author: str) -> dict | None:
    exact = [c for c in items if _norm(c.get("name")) == _norm(name)]
    if not exact:
        return None

    def score(c: dict) -> tuple[int, int]:
        return (
            1 if _norm(c.get("author")) == _norm(author) else 0,
            1 if (c.get("coverUrl") or "").strip() else 0,
        )

    return max(exact, key=score)
