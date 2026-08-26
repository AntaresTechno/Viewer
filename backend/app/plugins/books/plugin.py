"""books 插件 — 书源管理 + 搜索/详情/目录/正文/书架（legado 兼容引擎）。"""

import asyncio
import json
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...legado_rule.exceptions import FetchError
from ...services.image_cache import get_image
from ...services.replace_rules import apply_rules, parse_legado_import
from ...services.toc_queue import (
    chapters_to_dicts,
    latest_job_map,
    toc_queue,
)

# 预下载到本地库的进行中作业（进程内内存态，仅用于进度展示）
_DL_JOBS: dict[str, dict] = {}


meta = {
    "name": "books",
    "mount": "books",
    "title": "书城",
    "version": "1.0.0",
    "description": "legado 兼容书源解析、搜索、阅读与个人书架",
    "order": 30,
    "permissions": [
        ("books.sources.read", "查看书源"),
        ("books.sources.manage", "导入/编辑/删除/启停书源"),
        ("books.search", "在线搜索书籍"),
        ("books.explore", "发现分类浏览"),
        ("books.info", "获取书籍详情"),
        ("books.toc", "获取章节目录"),
        ("books.content", "阅读正文"),
        ("books.shelf.read", "查看我的书架"),
        ("books.shelf.write", "添加/移除书架"),
        ("books.progress.write", "保存阅读进度"),
        ("books.replace.read", "查看净化规则"),
        ("books.replace.manage", "导入/编辑/删除净化规则"),
    ],
}

# in-memory toc cache: key -> (timestamp, chapters)
_TOC_CACHE: dict[str, tuple[float, list[dict]]] = {}
_TOC_TTL = 1800


def create_router(ctx) -> APIRouter:
    from ...core.config import settings
    from ...core.deps import get_current_user, require_perm
    from ...core.db import get_db, get_session_factory
    from ...core.deps import resolve_user
    from ...legado_rule.net import fetch as net_fetch
    from ...models import (
        BookAsset,
        BookChapter,
        BookChapterContent,
        BookRef,
        BookSourceRow,
        ReadProgress,
        ReplaceRule,
        ShelfItem,
        TocJob,
    )
    from ...plugins.registry import engine_keys, get_engine, plugin_enabled
    from ...services import content_cache

    router = APIRouter(tags=["books"])

    # ------------------------------------------------------------ helpers
    async def _load_source_row(db: AsyncSession, source_url: str) -> BookSourceRow:
        row = await db.scalar(
            select(BookSourceRow).where(BookSourceRow.source_url == source_url)
        )
        if not row:
            raise HTTPException(404, f"书源不存在: {source_url}")
        if not row.enabled:
            raise HTTPException(400, "该书源已停用")
        return row

    async def _load_source(db: AsyncSession, source_url: str) -> dict:
        row = await _load_source_row(db, source_url)
        return json.loads(row.raw_json)

    def _engine_for(engine_name: str | None):
        try:
            return get_engine(engine_name, ctx)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ------------------------------------------------------- source admin
    @router.get("/sources")
    async def list_sources(
        keyword: str = "",
        group: str = "",
        current=Depends(require_perm("books.sources.read")),
        db: AsyncSession = Depends(get_db),
    ):
        stmt = select(BookSourceRow).order_by(
            BookSourceRow.custom_order, BookSourceRow.id
        )
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(BookSourceRow.source_name.like(like))
        if group:
            stmt = stmt.where(BookSourceRow.source_group == group)
        rows = (await db.execute(stmt)).scalars().all()
        groups = (
            await db.execute(select(func.distinct(BookSourceRow.source_group)))
        ).scalars().all()
        return {
            "items": [
                {
                    "id": r.id,
                    "sourceUrl": r.source_url,
                    "sourceName": r.source_name,
                    "sourceGroup": r.source_group,
                    "enabled": r.enabled,
                    "engine": getattr(r, "engine", None) or "legado",
                }
                for r in rows
            ],
            "groups": sorted(g for g in groups if g),
        }

    class ImportBody(BaseModel):
        data: str | None = Field(default=None, description="JSON 字符串：对象或数组")
        url: str | None = None
        engine: str | None = Field(
            default=None, description="解析引擎 key（缺省 legado；源内 viewEngine 字段优先）"
        )

    @router.post("/sources/import", status_code=201)
    async def import_sources(
        body: ImportBody,
        current=Depends(require_perm("books.sources.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        text = body.data
        if body.url:
            resp = await net_fetch(body.url)
            if resp.error:
                raise HTTPException(400, f"拉取失败: {resp.error}")
            text = resp.body
        if not text or not text.strip():
            raise HTTPException(400, "内容为空")
        try:
            obj = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"JSON 解析失败: {exc}") from exc
        if isinstance(obj, dict):
            obj = [obj]
        if not isinstance(obj, list):
            raise HTTPException(400, "需要书源对象或数组")

        known_engines = set(engine_keys())
        default_engine = body.engine or "legado"
        if default_engine not in known_engines:
            raise HTTPException(400, f"未知引擎: {default_engine}")

        existing = {
            r.source_url: r
            for r in (await db.execute(select(BookSourceRow))).scalars().all()
        }
        added = updated = skipped = 0
        for src in obj:
            if not isinstance(src, dict):
                skipped += 1
                continue
            surl = str(src.get("bookSourceUrl") or "").strip()
            if not surl:
                skipped += 1
                continue
            # per-source engine hint wins over the request-level default
            eng = str(src.get("viewEngine") or default_engine).strip()
            if eng not in known_engines:
                skipped += 1
                continue
            raw = json.dumps(src, ensure_ascii=False)
            name = str(src.get("bookSourceName") or "")
            group_raw = src.get("bookSourceGroup") or ""
            group = (
                str(group_raw.split(",")[0]).strip()
                if isinstance(group_raw, str) else ""
            )
            if surl in existing:
                row = existing[surl]
                row.raw_json = raw
                row.source_name = name
                row.source_group = group
                row.engine = eng
                updated += 1
            else:
                db.add(BookSourceRow(
                    source_url=surl, source_name=name,
                    source_group=group, raw_json=raw, enabled=True,
                    engine=eng,
                ))
                added += 1
        await db.commit()
        return {"added": added, "updated": updated, "skipped": skipped}

    @router.get("/engines")
    async def list_engines(
        current=Depends(require_perm("books.sources.read")),
        db: AsyncSession = Depends(get_db),
    ):
        """Available source-rule engines + per-engine source counts."""
        from ...plugins.registry import all_engines

        counts: dict[str | None, int] = {}
        rows = (await db.execute(
            select(BookSourceRow.engine, func.count()).group_by(BookSourceRow.engine)
        )).all()
        for eng, cnt in rows:
            counts[eng] = cnt
        return {
            "items": [
                {
                    "key": e.key,
                    "title": e.title,
                    "version": e.version,
                    "description": e.description,
                    "pluginName": e.plugin_name,
                    "sources": counts.get(e.key, 0),
                }
                for e in all_engines()
            ]
        }

    class SourceUpdate(BaseModel):
        source: dict

    @router.put("/sources/{source_url:path}")
    async def update_source(
        source_url: str,
        body: SourceUpdate,
        current=Depends(require_perm("books.sources.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        row = await db.scalar(
            select(BookSourceRow).where(BookSourceRow.source_url == source_url)
        )
        if not row:
            raise HTTPException(404, "书源不存在")
        new_url = str(body.source.get("bookSourceUrl") or source_url)
        row.source_url = new_url
        row.source_name = str(body.source.get("bookSourceName") or "")
        grp = body.source.get("bookSourceGroup") or ""
        row.source_group = (
            str(grp.split(",")[0]).strip() if isinstance(grp, str) else ""
        )
        row.raw_json = json.dumps(body.source, ensure_ascii=False)
        ve = str(body.source.get("viewEngine") or "").strip()
        if ve:
            if ve not in set(engine_keys()):
                raise HTTPException(400, f"未知引擎: {ve}")
            row.engine = ve
        await db.commit()
        return {"ok": True}

    class IdsBody(BaseModel):
        ids: list[int]

    @router.post("/sources/delete")
    async def delete_sources(
        body: IdsBody,
        current=Depends(require_perm("books.sources.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        await db.execute(delete(BookSourceRow).where(BookSourceRow.id.in_(body.ids)))
        await db.commit()
        return {"ok": True}

    class ToggleBody(BaseModel):
        enabled: bool | None = None

    @router.post("/sources/{source_id}/toggle")
    async def toggle_source(
        source_id: int,
        body: ToggleBody,
        current=Depends(require_perm("books.sources.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        row = await db.get(BookSourceRow, source_id)
        if not row:
            raise HTTPException(404, "书源不存在")
        row.enabled = bool(body.enabled) if body.enabled is not None else not row.enabled
        await db.commit()
        return {"ok": True, "enabled": row.enabled}

    @router.get("/sources/{source_id}/detail")
    async def source_detail(
        source_id: int,
        current=Depends(require_perm("books.sources.read")),
        db: AsyncSession = Depends(get_db),
    ):
        row = await db.get(BookSourceRow, source_id)
        if not row:
            raise HTTPException(404, "书源不存在")
        return {
            "id": row.id,
            "sourceUrl": row.source_url,
            "sourceName": row.source_name,
            "enabled": row.enabled,
            "engine": getattr(row, "engine", None) or "legado",
            "raw": json.loads(row.raw_json),
        }

    # -------------------------------------------------------------- search
    class SearchBody(BaseModel):
        key: str = Field(min_length=1)
        page: int = 1
        source_ids: list[int] | None = None

    @router.post("/search")
    async def search(
        body: SearchBody,
        current=Depends(require_perm("books.search")),
        db: AsyncSession = Depends(get_db),
    ):
        stmt = select(BookSourceRow).where(BookSourceRow.enabled)
        if body.source_ids:
            stmt = stmt.where(BookSourceRow.id.in_(body.source_ids))
        stmt = stmt.order_by(BookSourceRow.custom_order, BookSourceRow.id)
        rows = (await db.execute(stmt.limit(60))).scalars().all()

        sem = asyncio.Semaphore(settings.search_concurrency)

        async def run_one(row: BookSourceRow) -> list[dict]:
            src = json.loads(row.raw_json)
            try:
                eng = _engine_for(getattr(row, "engine", None))
            except HTTPException as exc:
                return [{
                    "_error": True,
                    "origin": row.source_url,
                    "originName": row.source_name,
                    "message": str(exc.detail),
                }]
            async with sem:
                try:
                    return await asyncio.wait_for(
                        eng.search_book(src, body.key, max(1, body.page)),
                        timeout=25,
                    )
                except Exception as exc:  # noqa: BLE001
                    return [{
                        "_error": True,
                        "origin": row.source_url,
                        "originName": row.source_name,
                        "message": f"{type(exc).__name__}: {exc}",
                    }]

        results = await asyncio.gather(*[run_one(r) for r in rows])
        flat: list[dict] = []
        errors: list[dict] = []
        for group in results:
            for item in group:
                if item.get("_error"):
                    errors.append(item)
                else:
                    flat.append(item)
        return {"items": flat[:200], "errors": errors}

    # --------------------------------------------------------------- explore
    @router.get("/explore/kinds")
    async def explore_kinds_route(
        source_url: str,
        current=Depends(require_perm("books.explore")),
        db: AsyncSession = Depends(get_db),
    ):
        """某书源的发现分类（由 exploreUrl 解析得到的 ExploreKind 列表）。"""
        row = await _load_source_row(db, source_url)
        src = json.loads(row.raw_json)
        eng = _engine_for(getattr(row, "engine", None))
        kinds = await _fetch(eng.explore_kinds(src))
        return {"items": kinds}

    @router.get("/explore")
    async def explore_books_route(
        source_url: str,
        url: str,
        page: int = 1,
        current=Depends(require_perm("books.explore")),
        db: AsyncSession = Depends(get_db),
    ):
        """抓取某发现分类 url 下的书籍列表（单书源）。"""
        row = await _load_source_row(db, source_url)
        src = json.loads(row.raw_json)
        eng = _engine_for(getattr(row, "engine", None))
        books = await _fetch(eng.explore_book(src, url, max(1, page)))
        return {"items": books}

    # ------------------------------------------------------ info/toc/content
    async def _fetch(coro):
        """把书源抓取/解析异常转成可读的 502，避免裸 500。"""
        try:
            return await coro
        except FetchError as exc:
            raise HTTPException(502, f"书源连接失败：{exc}") from exc
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"书源解析失败：{type(exc).__name__}: {exc}") from exc

    @router.get("/info")
    async def info_route(
        source_url: str,
        book_url: str,
        name: str = "",
        author: str = "",
        cover: str = "",
        current=Depends(require_perm("books.info")),
        db: AsyncSession = Depends(get_db),
    ):
        row = await _load_source_row(db, source_url)
        src = json.loads(row.raw_json)
        eng = _engine_for(getattr(row, "engine", None))
        # 搜索阶段已知的字段作为起点：书源 ruleBookInfo 缺项时（如封面规则为空）
        # 保留已知值，与 legado 的 Book 实体行为一致。
        return await _fetch(eng.book_info(src, {
            "bookUrl": book_url,
            "name": name,
            "author": author,
            "coverUrl": cover,
            "origin": source_url,
        }))

    @router.get("/toc")
    async def toc_route(
        source_url: str,
        toc_url: str,
        reverse_toc: bool = False,
        current=Depends(require_perm("books.toc")),
        db: AsyncSession = Depends(get_db),
    ):
        row = await _load_source_row(db, source_url)
        src = json.loads(row.raw_json)
        eng = _engine_for(getattr(row, "engine", None))
        cache_key = f"{source_url}|{toc_url}|{reverse_toc}"
        cached = _TOC_CACHE.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < _TOC_TTL:
            return {"chapters": cached[1], "cached": True}
        chapters = await _fetch(eng.get_toc(src, {"reverseToc": reverse_toc}, toc_url))
        _TOC_CACHE[cache_key] = (now, chapters)
        if len(_TOC_CACHE) > 256:
            for k in sorted(_TOC_CACHE)[:128]:
                _TOC_CACHE.pop(k, None)
        return {"chapters": chapters, "cached": False}

    @router.get("/content")
    async def content_route(
        source_url: str,
        url: str,
        title: str = "",
        next_chapter_url: str = "",
        is_volume: bool = False,
        base: str = "",
        name: str = "",
        book_url: str = "",
        current=Depends(require_perm("books.content")),
        db: AsyncSession = Depends(get_db),
    ):
        row = await _load_source_row(db, source_url)
        src = json.loads(row.raw_json)
        eng = _engine_for(getattr(row, "engine", None))
        chapter = {
            "url": url, "title": title,
            "isVolume": is_volume,
        }
        key = content_cache.cache_key(
            source_url, url, base=base, title=title,
            next_chapter_url=next_chapter_url, is_volume=is_volume,
        )

        # 正文净化插件启用时走「获取 → 净化 → 存库 → 调用」管线：
        # purified_contents 缓存优先（指纹一致直接调用；规则变化用原文
        # 本地重净化；断网兜底旧结果/本地书库）。
        if plugin_enabled("content_purify"):
            from ...services import content_purify

            async def _fetch_engine() -> str:
                return await eng.get_content(
                    src, {}, chapter, next_chapter_url or None,
                    base_url=base or None,
                )

            async def _fetch_raw() -> str:
                # 回源走内容 LRU：预取接口写入的条目在这里也能命中，
                # 否则启用净化插件后预取完全不生效。
                text, _hit = await content_cache.get_or_fetch(key, _fetch_engine)
                return text

            async def _local_fallback() -> str | None:
                r = await db.scalar(
                    select(BookChapterContent).where(
                        BookChapterContent.source_url == source_url,
                        BookChapterContent.url == url,
                    )
                )
                return (r.content or "") or None if r else None

            try:
                text, cached = await content_purify.process_chapter(
                    db,
                    source_url=source_url, url=url, book_url=book_url or "",
                    title=title, book_name=name,
                    source_name=row.source_name,
                    fetch_raw=_fetch_raw,
                    local_fallback=_local_fallback,
                )
            except FetchError as exc:
                raise HTTPException(502, f"书源连接失败：{exc}") from exc
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    502, f"书源解析失败：{type(exc).__name__}: {exc}"
                ) from exc

            # 同步本地书库（离线可读），与返回内容保持一致
            local_row = await db.scalar(
                select(BookChapterContent).where(
                    BookChapterContent.source_url == source_url,
                    BookChapterContent.url == url,
                )
            )
            if local_row is None:
                db.add(BookChapterContent(
                    source_url=source_url, book_url=book_url,
                    url=url, title=title, content=text,
                ))
                await db.commit()
            elif (local_row.content or "") != text:
                local_row.content = text
                await db.commit()
            return {"content": text, "cached": cached, "purified": True}

        # 本地书库优先：已下载过的章节直接从 DB 返回，不再回源
        cached_row = await db.scalar(
            select(BookChapterContent).where(
                BookChapterContent.source_url == source_url,
                BookChapterContent.url == url,
            )
        )
        if cached_row is not None and cached_row.content:
            return {"content": cached_row.content, "cached": True}

        async def _fetch_raw() -> str:
            return await eng.get_content(
                src, {}, chapter, next_chapter_url or None, base_url=base or None
            )

        try:
            text, _cached = await content_cache.get_or_fetch(key, _fetch_raw)
        except FetchError as exc:
            raise HTTPException(502, f"书源连接失败：{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"书源解析失败：{type(exc).__name__}: {exc}") from exc

        # 全局净化/替换规则（书源自身的 ruleContent.replaceRegex 已在引擎内生效）
        rules = (await db.execute(
            select(ReplaceRule).where(ReplaceRule.is_active)
        )).scalars().all()
        if rules:
            src_name = row.source_name
            text, _applied = await apply_rules(
                text, list(rules),
                book_name=name, source_url=source_url, source_name=src_name,
            )

        # 下载入库：正文拉进数据库，构建本地书库
        if not cached_row:
            db.add(BookChapterContent(
                source_url=source_url, book_url=book_url,
                url=url, title=title, content=text,
            ))
            await db.commit()
        return {"content": text, "cached": False}

    class PrefetchItem(BaseModel):
        url: str
        title: str = ""
        base: str = ""
        isVolume: bool = False

    class PrefetchBody(BaseModel):
        source_url: str
        # 前端每次带「N 章 + 1 条下一章指针」，N 最大 20 → 上限 21
        items: list[PrefetchItem] = Field(max_length=21)

    @router.post("/content/prefetch")
    async def content_prefetch(
        body: PrefetchBody,
        current=Depends(require_perm("books.content")),
        db: AsyncSession = Depends(get_db),
    ):
        """预取后续章节（阅读器按设置带后面 N 章并多带 1 条指针）。"""
        row = await _load_source_row(db, body.source_url)
        src = json.loads(row.raw_json)
        eng = _engine_for(getattr(row, "engine", None))
        queued = 0
        for i, it in enumerate(body.items):
            nxt = body.items[i + 1].url if i + 1 < len(body.items) else ""
            # 键必须与 GET /content 完全一致（含 next_chapter_url），
            # 否则真实阅读时算出的键对不上，预取的缓存永远命中不了。
            key = content_cache.cache_key(
                body.source_url, it.url, base=it.base, title=it.title,
                next_chapter_url=nxt, is_volume=it.isVolume,
            )

            async def _factory(it=it, nxt=nxt) -> str:
                return await eng.get_content(
                    src, {},
                    {"url": it.url, "title": it.title, "isVolume": it.isVolume},
                    nxt or None, base_url=it.base or None,
                )

            if await content_cache.spawn_prefetch(key, _factory):
                queued += 1
        return {"queued": queued}

    # ------------------------------------------------------- book short id
    class ResolveBody(BaseModel):
        sourceUrl: str
        bookUrl: str
        name: str = ""
        author: str = ""
        coverUrl: str = ""
        intro: str = ""
        kind: str = ""
        lastChapter: str = ""
        tocUrl: str = ""

    def _ref_dict(ref: BookRef) -> dict:
        """书籍短链档案：定位信息 + 缓存的最基本信息（阅读器零请求展示）。"""
        return {
            "id": ref.id,
            "sourceUrl": ref.source_url,
            "bookUrl": ref.book_url,
            "name": ref.name,
            "author": ref.author,
            "coverUrl": ref.cover_url,
            "intro": ref.intro,
            "kind": ref.kind,
            "lastChapter": ref.last_chapter,
            "tocUrl": ref.toc_url,
        }

    @router.post("/resolve")
    async def resolve_book(
        body: ResolveBody,
        current=Depends(require_perm("books.info")),
        db: AsyncSession = Depends(get_db),
    ):
        """按先后顺序为书籍分配短 id；已存在则刷新最近打开时间。

        调用方已知的书名/封面/简介等随请求写入，形成本地缓存档案。
        """
        ref = await db.scalar(
            select(BookRef).where(
                BookRef.source_url == body.sourceUrl,
                BookRef.book_url == body.bookUrl,
            )
        )
        if ref is None:
            ref = BookRef(
                source_url=body.sourceUrl, book_url=body.bookUrl,
                name=body.name, author=body.author, cover_url=body.coverUrl,
                intro=body.intro, kind=body.kind,
                last_chapter=body.lastChapter, toc_url=body.tocUrl,
            )
            db.add(ref)
        else:
            if body.name:
                ref.name = body.name
            if body.author:
                ref.author = body.author
            if body.coverUrl:
                ref.cover_url = body.coverUrl
            if body.intro:
                ref.intro = body.intro
            if body.kind:
                ref.kind = body.kind
            if body.lastChapter:
                ref.last_chapter = body.lastChapter
            if body.tocUrl:
                ref.toc_url = body.tocUrl
        await db.commit()
        await db.refresh(ref)
        return _ref_dict(ref)

    @router.get("/refs/{ref_id}")
    async def book_ref(
        ref_id: int,
        current=Depends(require_perm("books.info")),
        db: AsyncSession = Depends(get_db),
    ):
        ref = await db.get(BookRef, ref_id)
        if ref is None:
            raise HTTPException(404, f"书籍 id 不存在: {ref_id}")
        return _ref_dict(ref)

    @router.get("/profile")
    async def book_profile(
        source_url: str,
        book_url: str,
        current=Depends(require_perm("books.info")),
        db: AsyncSession = Depends(get_db),
    ):
        """按 来源+书籍地址 查缓存档案；详情页用它即时渲染、再后台刷新。"""
        ref = await db.scalar(
            select(BookRef).where(
                BookRef.source_url == source_url,
                BookRef.book_url == book_url,
            )
        )
        if ref is None:
            return {"found": False}
        data = _ref_dict(ref)
        data["found"] = True
        return data

    # ------------------------------------------------- chapters (toc cache)
    class TocRefreshBody(BaseModel):
        source_url: str
        book_url: str

    @router.post("/chapters/refresh")
    async def refresh_chapters(
        body: TocRefreshBody,
        current=Depends(require_perm("books.toc")),
        db: AsyncSession = Depends(get_db),
    ):
        """为任意（可不加书架的）书籍排队一次后台目录抓取。"""
        job = await toc_queue.create_job(db, body.source_url, body.book_url)
        if job is None:
            return {"ok": False, "message": "已有抓取任务在进行中"}
        toc_queue.enqueue(job.id)
        return {"ok": True}

    @router.get("/toc-status")
    async def toc_status(
        source_url: str,
        book_url: str,
        current=Depends(require_perm("books.toc")),
        db: AsyncSession = Depends(get_db),
    ):
        jobs = await latest_job_map(db)
        j = jobs.get((source_url, book_url))
        count = await db.scalar(
            select(func.count(BookChapter.id)).where(
                BookChapter.source_url == source_url,
                BookChapter.book_url == book_url,
            )
        )
        if j is None:
            return {"status": "done" if count else "none", "error": "",
                    "chapters": int(count or 0)}
        return {
            "status": j.status,
            "error": j.error if j.status == "error" else "",
            "chapters": int(count or 0) or j.chapters,
        }

    @router.get("/chapters")
    async def cached_chapters(
        source_url: str,
        book_url: str,
        fallback: bool = True,
        current=Depends(require_perm("books.toc")),
        db: AsyncSession = Depends(get_db),
    ):
        """目录缓存优先；未命中时可选地实时抓一次并落库。"""
        rows = (await db.execute(
            select(BookChapter)
            .where(BookChapter.source_url == source_url,
                   BookChapter.book_url == book_url)
            .order_by(BookChapter.idx, BookChapter.id)
        )).scalars().all()
        if rows:
            return {"chapters": chapters_to_dicts(rows), "cached": True}

        if not fallback:
            return {"chapters": [], "cached": False}
        row = await _load_source_row(db, source_url)
        src = json.loads(row.raw_json)
        eng = _engine_for(getattr(row, "engine", None))
        # 起始地址优先用书架上已解析出的目录页（比书详情页更完整）
        toc_start = await db.scalar(
            select(ShelfItem.toc_url).where(
                ShelfItem.source_url == source_url,
                ShelfItem.book_url == book_url,
                ShelfItem.toc_url != "",
            ).limit(1)
        )
        start_url = toc_start or book_url
        chapters = await _fetch(eng.get_toc(src, {"reverseToc": False}, start_url))
        for i, ch in enumerate(chapters):
            db.add(BookChapter(
                source_url=source_url,
                book_url=book_url,
                idx=int(ch.get("index", i)),
                title=str(ch.get("title") or ""),
                url=str(ch.get("url") or ""),
                base_url=str(ch.get("baseUrl") or start_url),
                is_volume=bool(ch.get("isVolume")),
                is_vip=bool(ch.get("isVip")),
            ))
        await db.commit()
        return {"chapters": chapters, "cached": False}

    @router.get("/progress")
    async def get_progress(
        book_url: str,
        current=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        p = await db.scalar(
            select(ReadProgress).where(
                ReadProgress.user_id == user.id, ReadProgress.book_url == book_url
            )
        )
        if p is None:
            return {"progress": None}
        return {
            "progress": {
                "chapterIndex": p.chapter_index,
                "chapterTitle": p.chapter_title,
                "offset": p.offset,
                "updatedAt": p.updated_at.isoformat() if p.updated_at else None,
            }
        }

    # ---------------------------------------------------------------- shelf
    class ShelfAdd(BaseModel):
        bookUrl: str
        tocUrl: str = ""
        name: str
        author: str = ""
        coverUrl: str = ""
        intro: str = ""
        lastChapter: str = ""
        sourceUrl: str = ""

    @router.get("/shelf")
    async def my_shelf(
        sort: str = "added",
        order: str = "desc",
        current=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """我的书架。

        ``sort``：``added`` 加入时间（默认）｜ ``updated`` 最近更新
        （书源检测到新章的时间）｜ ``read`` 最后阅读（阅读进度时间，
        未读过的排最后）。
        ``order``：``desc`` 倒序（默认，新的在前）｜ ``asc`` 正序
        （旧的在前；``read`` 排序时未读过的排最前）。
        """
        user, perms = current
        allowed = (
            user.is_superuser or "*" in perms or "books.*" in perms
            or "books.shelf.read" in perms
        )
        if not allowed:
            raise HTTPException(403, "权限不足：books.shelf.read")

        base_stmt = select(ShelfItem).where(ShelfItem.user_id == user.id)
        if sort == "updated":
            rows = (
                await db.execute(
                    base_stmt.order_by(
                        ShelfItem.updated_at.desc(), ShelfItem.id.desc()
                    )
                )
            ).scalars().all()
        elif sort == "read":
            # 最后阅读排序：进度新的在前，从未读过的按加入时间垫底
            prog_rows = (
                await db.execute(
                    select(ReadProgress).where(ReadProgress.user_id == user.id)
                )
            ).scalars().all()
            read_at = {p.book_url: p.updated_at for p in prog_rows}
            rows = (
                await db.execute(base_stmt.order_by(ShelfItem.id.desc()))
            ).scalars().all()
            rows = sorted(
                rows,
                key=lambda r: (
                    read_at.get(r.book_url) is not None,
                    read_at.get(r.book_url) or r.created_at,
                ),
                reverse=True,
            )
        else:
            rows = (
                await db.execute(
                    base_stmt.order_by(ShelfItem.created_at.desc(), ShelfItem.id.desc())
                )
            ).scalars().all()

        # 正序：把默认的倒序结果整组翻转（各排序键的并列规则保持一致）
        if order == "asc":
            rows.reverse()

        prog_rows = (
            await db.execute(
                select(ReadProgress).where(ReadProgress.user_id == user.id)
            )
        ).scalars().all()
        prog_map = {p.book_url: p for p in prog_rows}
        jobs = await latest_job_map(db)
        chap_counts: dict[tuple[str, str], int] = {
            (r[0], r[1]): r[2]
            for r in (
                await db.execute(
                    select(
                        BookChapter.source_url,
                        BookChapter.book_url,
                        func.count(BookChapter.id),
                    ).group_by(BookChapter.source_url, BookChapter.book_url)
                )
            ).all()
        }

        def _progress(book_url: str):
            """Progress of one shelf entry; None when never opened."""
            p = prog_map.get(book_url)
            if p is None:
                return None
            return {
                "chapterIndex": p.chapter_index,
                "chapterTitle": p.chapter_title,
                "offset": p.offset,
                "updatedAt": p.updated_at.isoformat() if p.updated_at else None,
            }

        def _toc_status(it: ShelfItem):
            j = jobs.get((it.source_url, it.book_url))
            count = chap_counts.get((it.source_url, it.book_url), 0)
            return {
                "chapters": count,
                "status": j.status if j else ("done" if count else "none"),
                "error": j.error if (j and j.status == "error") else "",
            }

        return {
            "items": [
                {
                    "id": r.id,
                    "bookUrl": r.book_url,
                    "tocUrl": r.toc_url,
                    "name": r.name,
                    "author": r.author,
                    "coverUrl": r.cover_url,
                    "intro": r.intro,
                    "lastChapter": r.last_chapter,
                    "sourceUrl": r.source_url,
                    "updatedAt": r.updated_at.isoformat() if r.updated_at else None,
                    "hasUpdate": bool(r.has_update),
                    "progress": _progress(r.book_url),
                    "toc": _toc_status(r),
                }
                for r in rows
            ]
        }

    @router.post("/shelf", status_code=201)
    async def add_shelf(
        body: ShelfAdd,
        current=Depends(require_perm("books.shelf.write")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        exists = await db.scalar(
            select(ShelfItem).where(
                ShelfItem.user_id == user.id, ShelfItem.book_url == body.bookUrl
            )
        )
        if exists:
            # 已在书架：若目录从未抓到且没有进行中的任务，补一次抓取
            has_chapters = await db.scalar(
                select(func.count(BookChapter.id)).where(
                    BookChapter.source_url == body.sourceUrl,
                    BookChapter.book_url == body.bookUrl,
                )
            )
            job = None
            if not has_chapters:
                job = await toc_queue.create_job(db, body.sourceUrl, body.bookUrl)
            if job is not None:
                toc_queue.enqueue(job.id)
            return {"ok": True, "id": exists.id, "existed": True}
        item = ShelfItem(
            user_id=user.id, book_url=body.bookUrl, toc_url=body.tocUrl,
            name=body.name, author=body.author, cover_url=body.coverUrl,
            intro=body.intro, last_chapter=body.lastChapter,
            source_url=body.sourceUrl,
        )
        db.add(item)
        await db.commit()
        await db.refresh(item)

        # 加入书架即自动抓取目录（后台队列）
        job = await toc_queue.create_job(db, body.sourceUrl, body.bookUrl)
        if job is not None:
            toc_queue.enqueue(job.id)
        return {"ok": True, "id": item.id}

    class RefreshTocBody(BaseModel):
        source_url: str | None = None

    @router.post("/shelf/{item_id}/refresh-toc")
    async def refresh_toc(
        item_id: int,
        body: RefreshTocBody | None = None,
        current=Depends(require_perm("books.toc")),
        db: AsyncSession = Depends(get_db),
    ):
        """手动触发某本书的目录重新抓取（走同一队列）。"""
        row = await db.get(ShelfItem, item_id)
        if not row:
            raise HTTPException(404, "书架条目不存在")
        source_url = (body.source_url if body and body.source_url else row.source_url)
        job = await toc_queue.create_job(db, source_url, row.book_url)
        if job is None:
            return {"ok": False, "message": "已有抓取任务在进行中"}
        toc_queue.enqueue(job.id)
        return {"ok": True}

    @router.delete("/shelf/{item_id}")
    async def remove_shelf(
        item_id: int,
        current=Depends(require_perm("books.shelf.write")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        row = await db.get(ShelfItem, item_id)
        if not row or row.user_id != user.id:
            raise HTTPException(404, "书架条目不存在")
        await db.delete(row)
        await db.commit()
        return {"ok": True}

    # ------------------------------------------------------------- progress
    class ProgressBody(BaseModel):
        bookUrl: str
        chapterIndex: int = 0
        chapterTitle: str = ""
        offset: int = 0

    @router.post("/progress")
    async def save_progress(
        body: ProgressBody,
        current=Depends(require_perm("books.progress.write")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        row = await db.scalar(
            select(ReadProgress).where(
                ReadProgress.user_id == user.id,
                ReadProgress.book_url == body.bookUrl,
            )
        )
        if not row:
            row = ReadProgress(user_id=user.id, book_url=body.bookUrl)
            db.add(row)
        row.chapter_index = body.chapterIndex
        row.chapter_title = body.chapterTitle
        row.offset = body.offset
        # 用户已读到最新位置 → 清掉书架上的「有更新」徽标
        shelf_row = await db.scalar(
            select(ShelfItem).where(
                ShelfItem.user_id == user.id,
                ShelfItem.book_url == body.bookUrl,
            )
        )
        if shelf_row is not None and shelf_row.has_update:
            shelf_row.has_update = False
        await db.commit()
        return {"ok": True}

    # ---------------------------------------------------------------- cover
    # 封面抓取失败时的占位图（与前端 fallback 同色调），避免 img 反复触发 error。
    _COVER_PLACEHOLDER = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='160'>"
        "<rect width='100%' height='100%' rx='10' fill='#9aa7b8'/>"
        "</svg>"
    ).encode("utf-8")

    @router.get("/cover")
    async def cover_proxy(url: str, token: str = ""):
        """封面/插图服务：首次下载成文件存到 data/covers/，之后直接返回本地文件。

        下载时带上该书源配置的 UA（httpUserAgent，缺省回退默认 UA），并回源带
        同站根 Referer 以绕过防盗链；本地已有对应文件则直接返回，不再回源。
        """
        import hashlib

        from fastapi.responses import Response
        from ...core.config import DATA_DIR

        factory = get_session_factory()
        async with factory() as session:
            resolved = await resolve_user(session, token)
        if resolved is None:
            raise HTTPException(401, "未授权")
        if not url.lower().startswith(("http://", "https://")):
            return Response(content=_COVER_PLACEHOLDER, media_type="image/svg+xml")

        covers = DATA_DIR / "covers"
        covers.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]

        # 本地已有文件 -> 直接返回
        try:
            hit = sorted(covers.glob(f"{key}.*"))[0]
        except IndexError:
            hit = None
        if hit is not None and hit.is_file():
            return Response(
                content=hit.read_bytes(), media_type=_mime_for_cover(hit.suffix),
                headers={"Cache-Control": "public, max-age=86400"},
            )

        # 书源 UA：封面归属性书源若能采到 httpUserAgent 则采用
        ua = settings.default_user_agent
        async with factory() as s:
            ref = await s.scalar(
                select(BookRef).where(BookRef.cover_url == url).limit(1)
            )
            if ref is not None:
                srow = await s.scalar(
                    select(BookSourceRow).where(BookSourceRow.source_url == ref.source_url)
                )
                if srow is not None:
                    try:
                        if json.loads(srow.raw_json).get("httpUserAgent"):
                            ua = str(json.loads(srow.raw_json)["httpUserAgent"]).strip()
                    except Exception:  # noqa: BLE001
                        pass

        parts = urlsplit(url)
        referer = f"{parts.scheme}://{parts.netloc}/"
        try:
            content, media_type = await get_image(
                url,
                headers={"User-Agent": ua if ua else settings.default_user_agent,
                         "Referer": referer},
            )
        except Exception:  # noqa: BLE001 — 上游不可达/超时都走占位图
            return Response(content=_COVER_PLACEHOLDER, media_type="image/svg+xml")

        # 下载成文件存到 data/covers/
        dest = covers / f"{key}{_ext_for_cover(media_type, url)}"
        dest.write_bytes(content)
        return Response(
            content=content, media_type=media_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    def _ext_for_cover(media_type: str, url: str) -> str:
        mapping = {
            "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/gif": ".gif", "image/avif": ".avif", "image/svg+xml": ".svg",
        }
        if media_type in mapping:
            return mapping[media_type]
        tail = url.split("?", 1)[0].rsplit(".", 1)
        if len(tail) == 2 and tail[1].lower() in (
            "jpg", "jpeg", "png", "webp", "gif", "avif", "svg"
        ):
            return "." + ("jpg" if tail[1].lower() == "jpeg" else tail[1].lower())
        return ".bin"

    def _mime_for_cover(suffix: str) -> str:
        return {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif",
            ".svg": "image/svg+xml",
        }.get(suffix.lower(), "application/octet-stream")

    # ------------------------------------------------------- replace rules
    @router.get("/source")
    async def source_info(
        source_url: str,
        current=Depends(require_perm("books.sources.read")),
        db: AsyncSession = Depends(get_db),
    ):
        """返回单个书源的信息与规则快照（详情页「详情」选项卡展示用）。"""
        row = await db.scalar(
            select(BookSourceRow).where(BookSourceRow.source_url == source_url)
        )
        if row is None:
            raise HTTPException(404, f"书源不存在: {source_url}")
        try:
            rules = json.loads(row.raw_json)
        except Exception:  # noqa: BLE001
            rules = {}
        return {
            "name": row.source_name,
            "type": getattr(row, "engine", "") or "legado",
            "url": row.source_url,
            "enabled": row.enabled,
            "rules": rules,
        }

    # ------------------------------------------------------- local library
    class LibraryDownloadBody(BaseModel):
        sourceUrl: str
        bookUrl: str
        name: str = ""
        author: str = ""
        cover: str = ""
        intro: str = ""
        concurrency: int | None = None  # 并发抓取章节数，留空用服务默认

    async def _run_download(job: dict) -> None:
        """后台预下载整本：目录章节按可调并发逐章抓取正文写入本地书库。"""
        factory = get_session_factory()
        src_url, book_url = job["sourceUrl"], job["bookUrl"]
        job["status"] = "running"
        try:
            async with factory() as s:
                row = await s.scalar(
                    select(BookSourceRow).where(BookSourceRow.source_url == src_url)
                )
                if row is None or not row.enabled:
                    raise RuntimeError("书源不存在或已停用")
                src = json.loads(row.raw_json)
                eng = _engine_for(getattr(row, "engine", None))
                src_name = row.source_name

                chapters = (await s.execute(
                    select(BookChapter).where(
                        BookChapter.source_url == src_url,
                        BookChapter.book_url == book_url,
                    ).order_by(BookChapter.idx)
                )).scalars().all()
                if not chapters:
                    book = dict(
                        bookUrl=book_url, name=job["name"],
                        author=job["author"], coverUrl=job["cover"],
                    )
                    try:
                        info = await eng.book_info(src, dict(book))
                    except Exception:  # noqa: BLE001
                        info = dict(book)
                    toc_url = str(info.get("tocUrl") or "").strip() or book_url
                    parsed = await eng.get_toc(src, info, toc_url)
                    chapters = [
                        BookChapter(
                            source_url=src_url, book_url=book_url,
                            idx=int(ch.get("index", i)),
                            title=str(ch.get("title") or ""),
                            url=str(ch.get("url") or ""),
                            base_url=str(ch.get("baseUrl") or toc_url),
                            is_volume=bool(ch.get("isVolume")),
                            is_vip=bool(ch.get("isVip")),
                        )
                        for i, ch in enumerate(parsed)
                    ]
                    s.add_all(chapters)
                    await s.commit()

                rules = (await s.execute(
                    select(ReplaceRule).where(ReplaceRule.is_active)
                )).scalars().all()

                # 正文净化插件启用时：预下载同样走净化规则包（与阅读一致）
                purify_on = plugin_enabled("content_purify")
                purify_rules: list = []
                if purify_on:
                    from ...services import content_purify as _cp

                    purify_rules = await _cp.active_rules(s)

            job["total"] = len(chapters)
            try:
                concurrency = max(1, int(job.get("concurrency") or settings.library_download_concurrency))
            except (TypeError, ValueError):  # noqa: BLE001
                concurrency = settings.library_download_concurrency
            job["concurrency"] = concurrency
            sem = asyncio.Semaphore(concurrency)

            async def _one(ch: BookChapter, nxt: str | None) -> None:
                async with sem:
                    async with factory() as chk:
                        have = await chk.scalar(select(BookChapterContent).where(
                            BookChapterContent.source_url == src_url,
                            BookChapterContent.url == ch.url,
                        ))
                        if have is not None and have.content:
                            job["done"] = job["done"] + 1
                            return
                    job["current"] = ch.title or ""
                    try:
                        raw = await eng.get_content(
                            src, {}, {"url": ch.url, "title": ch.title or ""},
                            nxt, base_url=ch.base_url or None,
                        )
                    except Exception as exc:  # noqa: BLE001 - 单章失败不中断整本
                        job["error"] = f"第{job['done'] + 1}章「{ch.title}」失败：{type(exc).__name__}"
                        return
                    text = raw
                    if purify_on and purify_rules:
                        text, _ = await _cp.purify_text(
                            raw, purify_rules,
                            book_name=job["name"], source_url=src_url,
                            source_name=src_name,
                        )
                    elif rules:
                        text, _ = await apply_rules(
                            raw, list(rules),
                            book_name=job["name"], source_url=src_url, source_name=src_name,
                        )
                    async with factory() as s2:
                        have = await s2.scalar(select(BookChapterContent).where(
                            BookChapterContent.source_url == src_url,
                            BookChapterContent.url == ch.url,
                        ))
                        if have is None:
                            s2.add(BookChapterContent(
                                source_url=src_url, book_url=book_url,
                                url=ch.url, title=ch.title or "", content=text,
                            ))
                        await s2.commit()
                    job["done"] = job["done"] + 1

            # 真正并发抓取，并发数由 semaphore 卡上限；同时传入下一章 url
            # 让 get_content 的分页在章节边界处停下，避免串章。
            await asyncio.gather(*(
                _one(ch, chapters[i + 1].url if i + 1 < len(chapters) else None)
                for i, ch in enumerate(chapters)
            ))
            job["status"] = "done"
            job["current"] = ""
        except Exception as exc:  # noqa: BLE001
            job["status"] = "error"
            job["error"] = f"{type(exc).__name__}: {exc}"

    async def _spawn_download(source_url: str, book_url: str, name="", author="", cover="",
                              concurrency: int | None = None) -> dict:
        key = f"{source_url}\x1f{book_url}"
        existing = _DL_JOBS.get(key)
        if existing and existing.get("status") in ("queued", "running"):
            return existing
        job = {
            "key": key, "sourceUrl": source_url, "bookUrl": book_url,
            "name": name or book_url, "author": author, "cover": cover,
            "status": "queued", "total": 0, "done": 0, "current": "", "error": "",
            "concurrency": None,
        }
        if concurrency and concurrency > 0:
            job["concurrency"] = int(concurrency)
        _DL_JOBS[key] = job
        asyncio.get_running_loop().create_task(_run_download(job))
        return job

    @router.post("/library/download")
    async def library_download(
        body: LibraryDownloadBody,
        current=Depends(require_perm("books.content")),
    ):
        """一键预下载整本到本地书库（后台执行，返回作业进度）。

        body.concurrency 可调并发抓取的章节数（可调线程），默认取服务设置。
        """
        return await _spawn_download(
            body.sourceUrl, body.bookUrl,
            body.name, body.author, body.cover,
            body.concurrency,
        )

    @router.get("/library/download/status")
    async def library_download_status(
        current=Depends(require_perm("books.content")),
    ):
        return {"jobs": list(_DL_JOBS.values())}

    @router.get("/library")
    async def library_overview(
        current=Depends(require_perm("books.content")),
        db: AsyncSession = Depends(get_db),
    ):
        """本地书库概览：总章节数、图片数、每本书已下载/总数。"""
        chapters_total = await db.scalar(select(func.count(BookChapterContent.id))) or 0
        asset_rows = (await db.execute(
            select(BookAsset.url).group_by(BookAsset.url)
        )).all()
        images_total = len(asset_rows)
        covers_total = len([u for u in asset_rows if "cover" in u[0].lower()])

        content_map = {
            (s, b): c for s, b, c in (await db.execute(
                select(BookChapterContent.source_url,
                       BookChapterContent.book_url,
                       func.count(BookChapterContent.id))
                .where(BookChapterContent.book_url != "")
                .group_by(BookChapterContent.source_url,
                          BookChapterContent.book_url)
            )).all()
        }
        chapter_map = {
            (s, b): c for s, b, c in (await db.execute(
                select(BookChapter.source_url, BookChapter.book_url,
                       func.count(BookChapter.id))
                .group_by(BookChapter.source_url, BookChapter.book_url)
            )).all()
        }
        refs = (await db.execute(
            select(BookRef).order_by(BookRef.id.desc())
        )).scalars().all()
        ref_by = {(r.source_url, r.book_url): r for r in refs}

        books = []
        for (s, b), cnt in sorted(content_map.items(), key=lambda kv: -kv[1]):
            ref = ref_by.get((s, b))
            books.append({
                "sourceUrl": s,
                "bookUrl": b,
                "name": (ref.name if ref else "") or b,
                "author": (ref.author if ref else "") or "",
                "coverUrl": (ref.cover_url if ref else "") or "",
                "intro": (ref.intro if ref else "") or "",
                "storedChapters": cnt,
                "totalChapters": chapter_map.get((s, b), 0),
            })
        return {
            "chapters": chapters_total,
            "images": images_total,
            "covers": covers_total,
            "booksTotal": len(books),
            "books": books,
        }

    @router.delete("/library")
    async def library_clear(
        source_url: str,
        book_url: str,
        current=Depends(require_perm("books.content")),
        db: AsyncSession = Depends(get_db),
    ):
        """清除某本书的所有已缓存章节（本地库正文）。"""
        res = await db.execute(
            delete(BookChapterContent).where(
                BookChapterContent.source_url == source_url,
                BookChapterContent.book_url == book_url,
            )
        )
        await db.commit()
        return {"deleted": res.rowcount}

    @router.get("/replace")
    async def replace_list(
        current=Depends(require_perm("books.replace.read")),
        db: AsyncSession = Depends(get_db),
    ):
        rows = (await db.execute(
            select(ReplaceRule).order_by(
                ReplaceRule.group_order, ReplaceRule.order, ReplaceRule.id
            )
        )).scalars().all()
        return {
            "items": [
                {
                    "id": r.id,
                    "name": r.name,
                    "group": r.group,
                    "groupOrder": r.group_order,
                    "order": r.order,
                    "isActive": r.is_active,
                    "pattern": r.pattern,
                    "replacement": r.replacement,
                    "scope": r.scope,
                    "regex": r.regex,
                    "caseSensitive": r.case_sensitive,
                }
                for r in rows
            ]
        }

    class ReplaceImportBody(BaseModel):
        data: str = Field(description="legado 替换规则 JSON：对象或数组")

    @router.post("/replace/import")
    async def replace_import(
        body: ReplaceImportBody,
        current=Depends(require_perm("books.replace.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        try:
            obj = json.loads(body.data)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"JSON 解析失败: {exc}") from exc
        norm = parse_legado_import(obj)
        if not norm:
            raise HTTPException(400, "没有可导入的规则（缺少 pattern）")
        for it in norm:
            db.add(ReplaceRule(**it))
        await db.commit()
        return {"imported": len(norm)}

    class ReplaceUpdate(BaseModel):
        name: str | None = None
        group: str | None = None
        groupOrder: int | None = None
        order: int | None = None
        isActive: bool | None = None
        pattern: str | None = None
        replacement: str | None = None
        scope: str | None = None
        regex: bool | None = None
        caseSensitive: bool | None = None

    @router.put("/replace/{rule_id}")
    async def replace_update(
        rule_id: int,
        body: ReplaceUpdate,
        current=Depends(require_perm("books.replace.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        row = await db.get(ReplaceRule, rule_id)
        if not row:
            raise HTTPException(404, "规则不存在")
        mapping = {
            "name": "name", "group": "group", "groupOrder": "group_order",
            "order": "order", "isActive": "is_active", "pattern": "pattern",
            "replacement": "replacement", "scope": "scope",
            "regex": "regex", "caseSensitive": "case_sensitive",
        }
        for k, col in mapping.items():
            v = getattr(body, k)
            if v is not None:
                setattr(row, col, v)
        await db.commit()
        return {"ok": True}

    @router.post("/replace/{rule_id}/toggle")
    async def replace_toggle(
        rule_id: int,
        current=Depends(require_perm("books.replace.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        row = await db.get(ReplaceRule, rule_id)
        if not row:
            raise HTTPException(404, "规则不存在")
        row.is_active = not row.is_active
        await db.commit()
        return {"ok": True, "isActive": row.is_active}

    class IdsBody2(BaseModel):
        ids: list[int]

    @router.post("/replace/delete")
    async def replace_delete(
        body: IdsBody2,
        current=Depends(require_perm("books.replace.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        await db.execute(delete(ReplaceRule).where(ReplaceRule.id.in_(body.ids)))
        await db.commit()
        return {"ok": True}

    class ReplaceTestBody(BaseModel):
        text: str
        bookName: str = ""
        sourceUrl: str = ""

    @router.post("/replace/test")
    async def replace_test(
        body: ReplaceTestBody,
        current=Depends(require_perm("books.replace.read")),
        db: AsyncSession = Depends(get_db),
    ):
        rules = (await db.execute(
            select(ReplaceRule).where(ReplaceRule.is_active)
        )).scalars().all()
        out, applied = await apply_rules(
            body.text, list(rules),
            book_name=body.bookName, source_url=body.sourceUrl,
        )
        return {"content": out, "applied": applied}

    return router
