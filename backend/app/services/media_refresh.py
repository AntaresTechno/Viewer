"""媒体目录每日刷新任务（与小说 toc_queue 分离）。

每日按去重后的 (source_id, item_key) 刷新结构化 units 或旧式业务指纹：
- 单源限速，尊重源 ``concurrentRate``；
- 单条失败只记录，不终止整轮；
- 检测新增/变化后更新所有持有该条目的用户副本的 has_update。
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from ..models import MediaLibraryItem, MediaProgress, MediaSourceRow

log = logging.getLogger("viewer.media_refresh")


async def refresh_all_media() -> dict:
    """刷新所有被收藏媒体条目的 units / 更新指纹。"""
    from ..core.db import get_session_factory
    from ..plugins.media import service as svc

    factory = get_session_factory()
    async with factory() as db:
        items = (await db.execute(select(MediaLibraryItem))).scalars().all()
        source_ids = {i.source_id for i in items}
        sources = {
            s.id: s for s in (await db.execute(
                select(MediaSourceRow).where(MediaSourceRow.id.in_(source_ids))
            )).scalars().all()
        } if source_ids else {}
        # 按 (source_id, item_key) 去重
        seen: set[tuple[int, str]] = set()
        changed = refreshed = 0
        for it in items:
            source = sources.get(it.source_id)
            if source is None or not source.enabled:
                continue
            key = (source.id, it.item_key)
            if key in seen:
                continue
            seen.add(key)
            try:
                if await _refresh_shared(db, source, it)["changed"]:
                    changed += 1
                refreshed += 1
            except Exception as exc:  # noqa: BLE001 - 单条失败不终止整轮
                log.warning("media refresh failed for item %s: %r", it.item_key, exc)
    return {"items": len(items), "refreshed": refreshed, "changed": changed}


async def _refresh_shared(db, source: MediaSourceRow, item: MediaLibraryItem) -> dict:
    """刷新单个 (source, item_key) 的 units/指纹；变化时更新所有持有它的副本。"""
    from ..plugins.media import service as svc
    from ..plugins.media.adapters import (
        MediaNotResolvable,
        adapter_for,
        fingerprint_legacy_document,
    )
    from ..plugins.media.source_service import normalize_source_dict, source_dict

    adapter = adapter_for(normalize_source_dict(source))
    shape = svc._lib_item_to_adapter(item, source)
    new_latest = item.latest_unit
    new_total = item.total_units
    fp = item.content_fingerprint
    changed = False

    try:
        units = await adapter.get_units(shape, refresh=True)
    except (MediaNotResolvable, NotImplementedError):
        units = []
    except Exception:  # noqa: BLE001 - legacy/网络失败走指纹路径
        units = []

    if units:
        await svc._sync_units(db, source.id, item.item_key, units)
        new_latest = units[-1].title or item.latest_unit
        new_total = len(units)
    else:
        # 旧式源：重算业务指纹
        try:
            from ..plugins.media.adapters.rss import fingerprint_legacy_document

            doc = await adapter.render_legacy_document(shape)
            if doc and doc.html:
                fp = fingerprint_legacy_document(source_dict(source), shape, doc.html)
        except Exception as exc:  # noqa: BLE001
            log.debug("legacy fingerprint failed: %r", exc)

    # 更新所有持有该条目的用户副本
    copies = (await db.execute(select(MediaLibraryItem).where(
        MediaLibraryItem.source_id == source.id,
        MediaLibraryItem.item_key == item.item_key,
    ))).scalars().all()
    for copy in copies:
        content_changed = fp and copy.content_fingerprint != fp
        unit_changed = new_total != copy.total_units or \
            (new_latest and new_latest != copy.latest_unit)
        if copy.content_fingerprint != fp:
            copy.content_fingerprint = fp
        if new_latest:
            copy.latest_unit = new_latest
        copy.total_units = new_total
        if content_changed or unit_changed:
            copy.has_update = not await _reached_latest(db, copy.id)
            changed = True
    await db.commit()
    return {"changed": changed}


async def _reached_latest(db, library_item_id: int) -> bool:
    progress = await db.scalar(select(MediaProgress).where(
        MediaProgress.library_item_id == library_item_id))
    return bool(progress and progress.completed)