"""Media library / catalog / progress / overview business logic."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    MediaLibraryItem,
    MediaProgress,
    MediaSourceRow,
    MediaUnit,
)
from . import schemas
from .adapters import adapter_for
from .adapters.rss import MediaNotResolvable, fingerprint_legacy_document
from .source_service import normalize_source_dict, source_dict

log = logging.getLogger("viewer.media")


async def get_source(db: AsyncSession, source_id: int, enabled: bool = True) -> MediaSourceRow:
    row = await db.get(MediaSourceRow, source_id)
    if row is None:
        raise HTTPException(404, "媒体源不存在")
    if enabled and not row.enabled:
        raise HTTPException(400, "该媒体源已停用")
    return row


# ------------------------------------------------------------- catalog
class CatalogService:
    def __init__(self, db: AsyncSession, source_row: MediaSourceRow):
        self.db = db
        self.row = source_row
        self.adapter = adapter_for(normalize_source_dict(source_row))

    async def sorts(self) -> list[dict]:
        return [s.to_dict() for s in self.adapter.list_sorts()]

    async def page(self, sort: dict, page: int) -> dict:
        result = await self.adapter.list_items(sort, max(1, page))
        items = _attach_source(result.items, self.row)
        return {"items": [i.to_dict() for i in items], "nextUrl": result.next_url,
                "warning": result.warning}

    async def search(self, keyword: str, page: int) -> dict:
        result = await self.adapter.search_items(keyword, max(1, page))
        items = _attach_source(result.items, self.row)
        return {"items": [i.to_dict() for i in items], "nextUrl": result.next_url,
                "warning": result.warning}

    async def detail(self, item: dict) -> dict:
        if self.row.source_format == "book":
            out = await self.adapter.fetch_detail(item)
        else:
            out = self.adapter.get_detail(item)
        out = _attach_source([out], self.row)[0]
        return out.to_dict()


def _attach_source(items: list, source_row: MediaSourceRow) -> list:
    for it in items:
        it.source_id = source_row.id
        it.source_name = source_row.source_name or source_row.source_key
        it.media_kind = source_row.media_kind
    return items


# ------------------------------------------------------------- library
async def add_to_library(
    db: AsyncSession, user_id: int, source_id: int, item: dict, media_kind: str | None = None
) -> tuple[MediaLibraryItem, bool]:
    source = await get_source(db, source_id)
    key = str(
        item.get("item_key") or item.get("itemKey")
        or item.get("item_url") or item.get("itemUrl")
        or ""
    ).strip()
    if not key:
        raise HTTPException(400, "缺少媒体条目标识")
    kind = media_kind or source.media_kind or ""
    item_url = str(item.get("item_url") or item.get("itemUrl") or "")
    title = str(item.get("title") or "")
    creator = str(item.get("creator") or "")
    cover_url = str(item.get("cover_url") or item.get("coverUrl") or "")
    intro = str(item.get("intro") or "")
    tags = item.get("tags") or item.get("tags_json") or []
    latest_unit = str(item.get("latest_unit") or item.get("latestUnit") or "")
    total_units = int(item.get("total_units") or item.get("totalUnits") or 0)
    row = await db.scalar(select(MediaLibraryItem).where(
        MediaLibraryItem.user_id == user_id,
        MediaLibraryItem.source_id == source_id,
        MediaLibraryItem.item_key == key,
    ))
    existed = row is not None
    if row is None:
        row = MediaLibraryItem(
            user_id=user_id, source_id=source_id, item_key=key,
            media_kind=kind,
            item_url=item_url,
            title=title,
            creator=creator,
            cover_url=cover_url,
            intro=intro,
            tags_json=tags,
            latest_unit=latest_unit,
            total_units=total_units,
        )
        db.add(row)
    else:
        for field, val in (("title", title), ("creator", creator),
                           ("cover_url", cover_url), ("intro", intro),
                           ("latest_unit", latest_unit)):
            if val:
                setattr(row, field, val)
        if tags:
            row.tags_json = tags
        if total_units:
            row.total_units = total_units
    return row, existed


async def library_item(db: AsyncSession, user_id: int, library_id: int) -> MediaLibraryItem:
    row = await db.get(MediaLibraryItem, library_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "媒体条目不存在")
    return row


def lib_item_json(row: MediaLibraryItem, progress: MediaProgress | None = None,
                  source: MediaSourceRow | None = None) -> dict:
    return {
        "id": row.id,
        "sourceId": row.source_id,
        "sourceName": source.source_name if source else "",
        "mediaKind": row.media_kind,
        "itemKey": row.item_key,
        "itemUrl": row.item_url,
        "title": row.title,
        "creator": row.creator,
        "coverUrl": row.cover_url,
        "intro": row.intro,
        "tags": row.tags_json or [],
        "latestUnit": row.latest_unit,
        "totalUnits": row.total_units,
        "hasUpdate": row.has_update,
        "contentUpdatedAt": _iso(row.content_updated_at),
        "createdAt": _iso(row.created_at),
        "progress": _progress_json(progress, row.media_kind),
    }


def _iso(value) -> str | None:
    return value.isoformat() if value else None


async def list_library(db: AsyncSession, user_id: int, kind: str | None,
                       sort: str, order: str) -> list[dict]:
    stmt = select(MediaLibraryItem).where(MediaLibraryItem.user_id == user_id)
    if kind and schemas.valid_kind(kind):
        stmt = stmt.where(MediaLibraryItem.media_kind == kind)
    col = {
        "updated": MediaLibraryItem.content_updated_at,
        "viewed": MediaProgress.updated_at,
        "added": MediaLibraryItem.created_at,
    }.get(sort or "added", MediaLibraryItem.created_at)
    if sort == "viewed":
        stmt = stmt.outerjoin(MediaProgress,
                              MediaProgress.library_item_id == MediaLibraryItem.id)
    ord_d = col.desc() if order != "asc" else col.asc()
    stmt = stmt.order_by(ord_d, MediaLibraryItem.id)
    rows = (await db.execute(stmt)).scalars().all()

    source_ids = {r.source_id for r in rows}
    sources = {
        s.id: s for s in (await db.execute(
            select(MediaSourceRow).where(MediaSourceRow.id.in_(source_ids))
        )).scalars().all()
    } if source_ids else {}
    ids = [r.id for r in rows]
    progresses = {}
    if ids:
        prog_rows = (await db.execute(
            select(MediaProgress).where(
                MediaProgress.user_id == user_id,
                MediaProgress.library_item_id.in_(ids),
            )
        )).scalars().all()
        progresses = {p.library_item_id: p for p in prog_rows}
    out = []
    for r in rows:
        prog = progresses.get(r.id)
        row = lib_item_json(r, prog, sources.get(r.source_id))
        # viewed 排序时把进度时间放到顶层供前端使用
        row["lastViewedAt"] = _iso(prog.updated_at) if prog else None
        out.append(row)
    return out


async def delete_library(db: AsyncSession, user_id: int, library_id: int) -> int:
    row = await library_item(db, user_id, library_id)
    # 同一事务删除自己的进度；共享 media_units 保留
    await db.execute(delete(MediaProgress).where(
        MediaProgress.user_id == user_id,
        MediaProgress.library_item_id == row.id,
    ))
    await db.delete(row)
    return row.id


async def refresh_library(db: AsyncSession, user_id: int, library_id: int) -> dict:
    row = await library_item(db, user_id, library_id)
    source = await get_source(db, row.source_id, enabled=False)
    adapter = adapter_for(normalize_source_dict(source))
    try:
        units = await adapter.get_units(_lib_item_to_adapter(row, source), refresh=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("media refresh units failed for lib %s: %r", row.id, exc)
        units = []
    changed = False
    if units:
        await _sync_units(db, source.id, row.item_key, units)
        latest = units[-1].title if units else row.latest_unit
        if latest and latest != row.latest_unit:
            row.latest_unit = latest
            row.has_update = not await _progress_on_latest(db, user_id, row.id, units[-1])
            changed = True
        if len(units) != row.total_units:
            row.total_units = len(units)
            changed = True
    else:
        # 旧式源：重算业务指纹
        item_adapter = _lib_item_to_adapter(row, source)
        try:
            doc = await adapter.render_legacy_document(item_adapter)
            if doc and doc.html:
                fp = fingerprint_legacy_document(source_dict(source), item_adapter, doc.html)
                if fp and fp != row.content_fingerprint:
                    row.content_fingerprint = fp
                    row.has_update = True
                    changed = True
                row.content_updated_at = datetime.now(timezone.utc)
        except Exception as exc:  # noqa: BLE001
            log.warning("media refresh legacy fingerprint failed: %r", exc)
    await db.commit()
    return {"changed": changed, "units": len(units)}


async def _progress_on_latest(db: AsyncSession, user_id: int, lib_id: int, unit) -> bool:
    p = await progress_get(db, user_id, lib_id)
    return bool(p and p.unit_key == unit.unit_key and p.completed)


def _lib_item_to_adapter(row: MediaLibraryItem, source: MediaSourceRow) -> dict:
    return {
        "id": row.id, "item_key": row.item_key, "item_url": row.item_url,
        "title": row.title, "creator": row.creator, "cover_url": row.cover_url,
        "intro": row.intro, "tags": row.tags_json or [], "source_id": row.source_id,
        "latest_unit": row.latest_unit, "total_units": row.total_units,
    }


async def _sync_units(db: AsyncSession, source_id: int, item_key: str,
                      units: list[schemas.MediaUnitDto]) -> None:
    # 保留 DB 中已有但本次缺失的单元（多页/搜索合并），maintain by upsert
    existing = {
        u.unit_key: u for u in (await db.execute(
            select(MediaUnit).where(
                MediaUnit.source_id == source_id, MediaUnit.item_key == item_key)
        )).scalars().all()
    }
    for unit in units:
        prev = existing.get(unit.unit_key)
        if prev is None:
            db.add(MediaUnit(
                source_id=source_id, item_key=item_key, unit_key=unit.unit_key,
                unit_index=unit.index, title=unit.title, locator=unit.locator,
                duration_ms=unit.duration_ms, locked=unit.locked,
            ))
        else:
            prev.unit_index = unit.index
            prev.title = unit.title
            prev.locator = unit.locator or prev.locator
            prev.locked = unit.locked


async def get_units(db: AsyncSession, source_id: int, item_key: str) -> list[MediaUnit]:
    return (await db.execute(
        select(MediaUnit).where(
            MediaUnit.source_id == source_id, MediaUnit.item_key == item_key
        ).order_by(MediaUnit.unit_index)
    )).scalars().all()


# ------------------------------------------------------------- progress
async def progress_get(db: AsyncSession, user_id: int, library_id: int) -> MediaProgress | None:
    return await db.scalar(select(MediaProgress).where(
        MediaProgress.user_id == user_id,
        MediaProgress.library_item_id == library_id,
    ))


def _progress_json(p: MediaProgress | None, kind: str) -> dict | None:
    if p is None:
        return None
    return {
        "unitKey": p.unit_key, "unitIndex": p.unit_index, "unitTitle": p.unit_title,
        "positionMs": p.position_ms, "durationMs": p.duration_ms,
        "pageIndex": p.page_index, "pageCount": p.page_count,
        "completed": p.completed, "legacyState": p.legacy_state_json or {},
        "updatedAt": _iso(p.updated_at),
    }


async def upsert_progress(
    db: AsyncSession, user_id: int, library_id: int, body: dict,
) -> MediaProgress:
    row = await library_item(db, user_id, library_id)
    p = await progress_get(db, user_id, library_id)
    if p is None:
        p = MediaProgress(user_id=user_id, library_item_id=row.id)
        db.add(p)
    unit_key = str(body.get("unitKey") or "")
    if unit_key:
        p.unit_key = unit_key
        p.unit_index = int(body.get("unitIndex") or 0)
        p.unit_title = str(body.get("unitTitle") or "")
    is_kind = row.media_kind
    if is_kind == "comic":
        if body.get("pageIndex") is not None:
            p.page_index = int(body["pageIndex"])
        if body.get("pageCount") is not None:
            p.page_count = int(body["pageCount"])
    else:
        if body.get("positionMs") is not None:
            p.position_ms = max(0, int(body["positionMs"]))
        if body.get("durationMs") is not None:
            p.duration_ms = max(0, int(body["durationMs"]))
    if body.get("completed") is not None:
        p.completed = bool(body["completed"])
    elif is_kind != "comic" and p.duration_ms and p.position_ms:
        p.completed = p.position_ms >= p.duration_ms * 0.95
    if body.get("legacyState") is not None:
        from .legacy_document import sanitize_legacy_state

        p.legacy_state_json = sanitize_legacy_state(body["legacyState"])
    p.updated_at = datetime.now(timezone.utc)
    # 进度到达最新一集后清除「有更新」
    if p.completed and unit_key and row.latest_unit and (
        unit_key in row.latest_unit or (p.unit_index and p.unit_index >= row.total_units - 1)
    ):
        row.has_update = False
    await db.commit()
    return p


# ------------------------------------------------------------- resolve
async def resolve_unit(db: AsyncSession, user_id: int, library_id: int, unit_key: str):
    row = await library_item(db, user_id, library_id)
    source = await get_source(db, row.source_id, enabled=False)
    units = await get_units(db, row.source_id, row.item_key)
    unit = next((u for u in units if u.unit_key == unit_key), None)
    ax_unit = {"url": unit.locator, "locator": unit.locator, "title": unit.title,
               "index": unit.unit_index, "unit_key": unit.unit_key} if unit else {"url": ""}
    try:
        resolved = await adapter_for(normalize_source_dict(source)).resolve_unit(
            _lib_item_to_adapter(row, source), ax_unit
        )
    except MediaNotResolvable:
        raise HTTPException(409, "该源条目需使用兼容播放器")
    return resolved, row, source


# ------------------------------------------------------------- overview
async def overview(db: AsyncSession, user_id: int) -> dict:
    lib_rows = (await db.execute(select(MediaLibraryItem).where(
        MediaLibraryItem.user_id == user_id))).scalars().all()
    counts = {"comic": 0, "audio": 0, "video": 0, "total": len(lib_rows)}
    for r in lib_rows:
        if r.media_kind in counts:
            counts[r.media_kind] += 1

    ids = [r.id for r in lib_rows]
    progresses = {}
    if ids:
        pr = (await db.execute(select(MediaProgress).where(
            MediaProgress.user_id == user_id,
            MediaProgress.library_item_id.in_(ids),
        ))).scalars().all()
        progresses = {p.library_item_id: p for p in pr}

    source_ids = {r.source_id for r in lib_rows}
    sources = {
        s.id: s for s in (await db.execute(
            select(MediaSourceRow).where(MediaSourceRow.id.in_(source_ids))
        )).scalars().all()
    } if source_ids else {}

    recent = sorted(
        lib_rows, key=lambda r: progresses[r.id].updated_at if r.id in progresses else _epoch(),
        reverse=True,
    )
    update_items = [r for r in lib_rows if r.has_update]
    added_items = sorted(lib_rows, key=lambda r: r.created_at, reverse=True)
    seen = max(1, len(lib_rows))

    def view_item(r: MediaLibraryItem):
        p = progresses.get(r.id)
        return lib_item_json(r, p, sources.get(r.source_id))

    return {
        "counts": counts,
        "recent": [view_item(r) for r in recent[:12]],
        "updates": [view_item(r) for r in update_items[:10]],
        "recentlyAdded": [view_item(r) for r in added_items[:10]],
    }


def _epoch():
    return datetime(1970, 1, 1, tzinfo=timezone.utc)