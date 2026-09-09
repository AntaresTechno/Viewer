"""Media plugin — media library for 漫画/音频/视频 backed by Legado sources."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

PLUGIN = {
    "kind": "plugin",
    "name": "media",
    "mount": "media",
    "title": "媒体库",
    "version": "1.0.0",
    "description": "独立媒体收藏与阅览：漫画 / 音频 / 视频，兼容番茄短剧旧式播放器",
    "order": 36,
    "permissions": [
        ("media.read", "查看媒体库、详情和进度"),
        ("media.library.write", "加入/移出媒体库、更新进度"),
        ("media.sources.read", "浏览媒体源与目录"),
        ("media.sources.manage", "导入、启停、导出、删除媒体源"),
    ],
}


class ImportBody(BaseModel):
    data: str | None = Field(default=None, description="Legado RssSource / BookSource JSON")
    url: str | None = None
    mediaKind: str | None = None


class IdsBody(BaseModel):
    ids: list[int]


class SourceEnabledBody(IdsBody):
    enabled: bool


class KindBody(BaseModel):
    mediaKind: str


class LibraryAddBody(BaseModel):
    sourceId: int
    item: dict
    mediaKind: str | None = None


class ProgressBody(BaseModel):
    unitKey: str | None = None
    unitIndex: int | None = None
    unitTitle: str | None = None
    positionMs: int | None = None
    durationMs: int | None = None
    pageIndex: int | None = None
    pageCount: int | None = None
    completed: bool | None = None
    legacyState: dict | None = None


class LegacyBody(BaseModel):
    itemKey: str | None = None
    itemUrl: str | None = None


def _http_err(e: BaseException, status: int = 400) -> HTTPException:
    return HTTPException(status, str(e))


def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.db import get_db
    from ...core.deps import require_perm
    from ...legado_rule.net import fetch
    from ...models import MediaLibraryItem, MediaSourceRow
    from . import service as svc
    from .adapters import MediaNotResolvable
    from .legacy_document import build_srcdoc
    from .source_service import import_sources, set_kind, source_dict, source_json
    from .tickets import make_ticket, verify_ticket

    router = APIRouter(tags=["media"])

    # ========================================================= 源管理
    @router.get("/sources")
    async def list_sources(
        kind: str = "",
        current=Depends(require_perm("media.sources.read")),
        db=Depends(get_db),
    ):
        from sqlalchemy import select

        stmt = select(MediaSourceRow).order_by(
            MediaSourceRow.custom_order, MediaSourceRow.id
        )
        if kind and kind in ("comic", "audio", "video", ""):
            if kind:
                stmt = stmt.where(MediaSourceRow.media_kind == kind)
        rows = (await db.execute(stmt)).scalars().all()
        return {"items": [source_json(r) for r in rows]}

    @router.post("/sources/import", status_code=201)
    async def import_media_sources(
        body: ImportBody,
        current=Depends(require_perm("media.sources.manage")),
        db=Depends(get_db),
    ):
        text = body.data
        if body.url:
            response = await fetch(body.url)
            if response.error or not response.ok:
                raise HTTPException(400, f"拉取失败: {response.error or response.status}")
            text = response.body
        if not text or not text.strip():
            raise HTTPException(400, "内容为空")
        try:
            obj = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"JSON 解析失败: {exc}") from exc
        result = await import_sources(db, obj, body.mediaKind)
        await db.commit()
        return result

    @router.post("/sources/delete")
    async def delete_sources(
        body: IdsBody,
        current=Depends(require_perm("media.sources.manage")),
        db=Depends(get_db),
    ):
        from sqlalchemy import delete

        # 级联清理分享的单位与条目（media_units 由 DB FK 级联处理）
        await db.execute(delete(MediaLibraryItem).where(
            MediaLibraryItem.source_id.in_(body.ids)))
        await db.execute(delete(MediaSourceRow).where(MediaSourceRow.id.in_(body.ids)))
        await db.commit()
        return {"deleted": len(body.ids)}

    @router.post("/sources/{source_id}/toggle")
    async def toggle_source(
        source_id: int,
        current=Depends(require_perm("media.sources.manage")),
        db=Depends(get_db),
    ):
        row = await db.get(MediaSourceRow, source_id)
        if row is None:
            raise HTTPException(404, "媒体源不存在")
        row.enabled = not row.enabled
        await db.commit()
        return {"enabled": row.enabled}

    @router.post("/sources/batch-enabled")
    async def set_sources_enabled(
        body: SourceEnabledBody,
        current=Depends(require_perm("media.sources.manage")),
        db=Depends(get_db),
    ):
        from sqlalchemy import select

        rows = (
            await db.execute(
                select(MediaSourceRow).where(MediaSourceRow.id.in_(body.ids))
            )
        ).scalars().all()
        for row in rows:
            row.enabled = body.enabled
        await db.commit()
        return {"updated": len(rows), "enabled": body.enabled}

    @router.post("/sources/{source_id}/kind")
    async def set_source_kind(
        source_id: int,
        body: KindBody,
        current=Depends(require_perm("media.sources.manage")),
        db=Depends(get_db),
    ):
        try:
            row = await set_kind(db, source_id, body.mediaKind)
            await db.commit()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"id": row.id, "mediaKind": row.media_kind}

    @router.get("/sources/export")
    async def export_sources(
        ids: list[int] = Query(default=[]),
        current=Depends(require_perm("media.sources.manage")),
        db=Depends(get_db),
    ):
        from sqlalchemy import select

        stmt = select(MediaSourceRow).order_by(MediaSourceRow.custom_order, MediaSourceRow.id)
        if ids:
            stmt = stmt.where(MediaSourceRow.id.in_(ids))
        rows = (await db.execute(stmt)).scalars().all()
        return [source_dict(r) for r in rows]

    # ========================================================= 目录 / 发现 / 搜索
    @router.get("/catalog/sorts")
    async def catalog_sorts(
        sourceId: int,
        current=Depends(require_perm("media.sources.read")),
        db=Depends(get_db),
    ):
        source = await svc.get_source(db, sourceId)
        return {"items": await svc.CatalogService(db, source).sorts()}

    @router.get("/catalog")
    async def catalog_page(
        sourceId: int,
        sortName: str = "",
        sortUrl: str = "",
        page: int = Query(default=1, ge=1),
        current=Depends(require_perm("media.sources.read")),
        db=Depends(get_db),
    ):
        source = await svc.get_source(db, sourceId)
        return await svc.CatalogService(db, source).page(
            {"name": sortName, "url": sortUrl}, page
        )

    @router.get("/catalog/search")
    async def catalog_search(
        sourceId: int,
        keyword: str = "",
        page: int = Query(default=1, ge=1),
        current=Depends(require_perm("media.sources.read")),
        db=Depends(get_db),
    ):
        source = await svc.get_source(db, sourceId)
        return await svc.CatalogService(db, source).search(keyword, page)

    @router.get("/catalog/detail")
    async def catalog_detail(
        sourceId: int,
        itemKey: str = "",
        itemUrl: str = "",
        current=Depends(require_perm("media.sources.read")),
        db=Depends(get_db),
    ):
        source = await svc.get_source(db, sourceId)
        return await svc.CatalogService(db, source).detail(
            {"item_key": itemKey, "item_url": itemUrl}
        )

    # ========================================================= 总览
    @router.get("/overview")
    async def overview(
        current=Depends(require_perm("media.read")),
        db=Depends(get_db),
    ):
        user, _ = current
        return await svc.overview(db, user.id)

    # ========================================================= 媒体库
    @router.get("/library")
    async def library(
        kind: str = "",
        sort: str = "added",
        order: str = "desc",
        current=Depends(require_perm("media.read")),
        db=Depends(get_db),
    ):
        user, _ = current
        items = await svc.list_library(db, user.id, kind, sort, order)
        return {"items": items}

    @router.post("/library", status_code=201)
    async def library_add(
        body: LibraryAddBody,
        current=Depends(require_perm("media.library.write")),
        db=Depends(get_db),
    ):
        user, _ = current
        row, existed = await svc.add_to_library(db, user.id, body.sourceId, body.item, body.mediaKind)
        await db.commit()
        await db.refresh(row)
        return {"id": row.id, "existed": existed}

    @router.get("/library/{library_id}")
    async def library_get(
        library_id: int,
        current=Depends(require_perm("media.read")),
        db=Depends(get_db),
    ):
        user, _ = current
        row = await svc.library_item(db, user.id, library_id)
        source = await db.get(MediaSourceRow, row.source_id) if row.source_id else None
        progress = await svc.progress_get(db, user.id, row.id)
        return svc.lib_item_json(row, progress, source)

    @router.delete("/library/{library_id}")
    async def library_delete(
        library_id: int,
        current=Depends(require_perm("media.library.write")),
        db=Depends(get_db),
    ):
        user, _ = current
        await svc.delete_library(db, user.id, library_id)
        await db.commit()
        return {"deleted": library_id}

    @router.post("/library/{library_id}/refresh")
    async def library_refresh(
        library_id: int,
        current=Depends(require_perm("media.library.write")),
        db=Depends(get_db),
    ):
        user, _ = current
        return await svc.refresh_library(db, user.id, library_id)

    @router.get("/library/{library_id}/units")
    async def library_units(
        library_id: int,
        current=Depends(require_perm("media.read")),
        db=Depends(get_db),
    ):
        user, _ = current
        row = await svc.library_item(db, user.id, library_id)
        units = await svc.get_units(db, row.source_id, row.item_key)
        return {"items": [{
            "unitKey": u.unit_key, "index": u.unit_index, "title": u.title,
            "durationMs": u.duration_ms, "locked": u.locked,
        } for u in units]}

    # ========================================================= 解析 / 进度 / legacy
    @router.post("/library/{library_id}/units/{unit_key}/resolve")
    async def unit_resolve(
        library_id: int,
        unit_key: str,
        current=Depends(require_perm("media.read")),
        db=Depends(get_db),
    ):
        user, _ = current
        row = await svc.library_item(db, user.id, library_id)
        try:
            resolved, row, source = await svc.resolve_unit(db, user.id, library_id, unit_key)
        except HTTPException as exc:
            if exc.status_code == 409:
                # 无结构化内容 → 前台应调 legacy-document
                return {"mode": "legacy", "kind": row.media_kind}
            raise

        def ticket_for(url: str) -> str:
            if not url or not url.startswith(("http://", "https://")):
                return url
            return f"/api/media/resource/{make_ticket(user_id=user.id, library_item_id=row.id, unit_key=unit_key, resource_index=0)}"

        out = {"mode": "native", "kind": resolved.kind}
        if resolved.kind == "comic":
            out["images"] = [{
                **img, "url": ticket_for(img.get("url") or ""),
            } for img in resolved.images]
            out["readingDirection"] = resolved.reading_direction
            out["nextUnitKey"] = resolved.next_unit_key
            return out
        out["streams"] = [{
            **s, "url": ticket_for(s.get("url") or ""),
        } for s in resolved.streams]
        out["subtitles"] = [{
            **s, "url": ticket_for(s.get("url") or ""),
        } for s in resolved.subtitles]
        out["posterUrl"] = ticket_for(resolved.poster_url)
        out["lyrics"] = resolved.lyrics
        out["warning"] = resolved.warning
        return out

    @router.get("/library/{library_id}/progress")
    async def progress_get(
        library_id: int,
        current=Depends(require_perm("media.read")),
        db=Depends(get_db),
    ):
        user, _ = current
        p = await svc.progress_get(db, user.id, library_id)
        if p is None:
            return {"progress": None}
        row = await db.get(MediaLibraryItem, library_id)
        return {"progress": svc._progress_json(p, row.media_kind if row else "video")}

    @router.put("/library/{library_id}/progress")
    async def progress_put(
        library_id: int,
        body: ProgressBody,
        current=Depends(require_perm("media.library.write")),
        db=Depends(get_db),
    ):
        user, _ = current
        p = await svc.upsert_progress(db, user.id, library_id, body.model_dump(exclude_none=True))
        row = await db.get(MediaLibraryItem, library_id)
        return {"progress": svc._progress_json(p, row.media_kind if row else "video")}

    @router.post("/library/{library_id}/legacy-document")
    async def legacy_document(
        library_id: int,
        body: LegacyBody,
        current=Depends(require_perm("media.read")),
        db=Depends(get_db),
    ):
        user, _ = current
        row = await svc.library_item(db, user.id, library_id)
        source = await db.get(MediaSourceRow, row.source_id)
        from .adapters import adapter_for
        from .source_service import normalize_source_dict

        adapter = adapter_for(normalize_source_dict(source))
        item = svc._lib_item_to_adapter(row, source)
        if body.itemKey:
            item["item_key"] = body.itemKey
        if body.itemUrl:
            item["item_url"] = body.itemUrl
        try:
            doc = await adapter.render_legacy_document(item)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"渲染兼容播放器失败: {exc}") from exc
        if doc is None or not doc.html:
            raise HTTPException(404, "该条目没有可用的兼容播放器文档")
        progress = await svc.progress_get(db, user.id, library_id)
        return {
            "html": build_srcdoc(doc.html),
            "iframeKey": doc.iframe_key or str(row.id),
            "warning": doc.warning,
            "restore": (progress.legacy_state_json or {}) if progress else {},
        }

    # ========================================================= 资源代理
    @router.get("/resource/{ticket}")
    async def resource(
        ticket: str,
        request: Request,
        db=Depends(get_db),
    ):
        payload = verify_ticket(ticket)
        if payload is None:
            raise HTTPException(401, "票据无效或已过期")
        row = await db.get(MediaLibraryItem, int(payload["li"]))
        if row is None or row.user_id != int(payload["u"]):
            raise HTTPException(403, "无权访问该资源")
        source = await db.get(MediaSourceRow, row.source_id)
        if source is None:
            raise HTTPException(404, "媒体源不存在")
        unit_key = str(payload["uKey"])
        try:
            resolved, _, _ = await svc.resolve_unit(db, row.user_id, row.id, unit_key)
        except HTTPException as exc:
            if exc.status_code == 409:
                raise HTTPException(404, "该资源需使用兼容播放器") from None
            raise
        idx = int(payload.get("i") or 0)
        streams = resolved.streams
        if idx >= len(streams):
            raise HTTPException(404, "资源序号超出范围")
        st = streams[idx]
        url = str(st.get("url") or "")
        if not url or not url.startswith(("http://", "https://")):
            raise HTTPException(404, "无可用资源地址")
        from . import proxy

        reason = proxy._is_blocked_target(url)
        if reason:
            raise HTTPException(400, f"不允许的媒体地址: {reason}")
        range_header = request.headers.get("range") if request else None
        if_range = request.headers.get("if-range") if request else None
        resp, client = await proxy.open_upstream(
            url=url, headers=st.get("headers"), range_header=range_header,
        )
        resp_headers = proxy.pick_response_headers(resp.headers)
        content_type = resp_headers.pop("content-type", None)
        status = resp.status_code
        if status in (401, 403):
            # 上游 401/403：允许重试一次（票据层面已过期时返回 502 提示重解析）
            await resp.aclose()
            await client.aclose()
            raise HTTPException(502, "上游拒绝访问（可能需要重新解析）")

        async def gen():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(gen, status_code=status, headers=resp_headers,
                                 media_type=content_type)

    return router
