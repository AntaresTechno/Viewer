"""Media source service — import recognition, source CRUD, normalization."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import MediaSourceRow
from . import schemas


def source_dict(row: MediaSourceRow) -> dict:
    """Runtime source dict: DB columns override the imported snapshot."""
    try:
        source = json.loads(row.raw_json)
    except Exception:  # noqa: BLE001
        source = {}
    if not isinstance(source, dict):
        source = {}
    if row.source_format == "rss":
        source.update({
            "sourceUrl": row.source_key,
            "sourceName": row.source_name,
            "sourceIcon": row.source_icon,
            "sourceGroup": row.source_group,
            "sourceComment": row.source_comment,
            "enabled": row.enabled,
            "customOrder": row.custom_order,
        })
    else:
        source.update({
            "bookSourceUrl": row.source_key,
            "bookSourceName": row.source_name,
            "sourceGroup": row.source_group,
            "sourceComment": row.source_comment,
            "enabled": row.enabled,
            "customOrder": row.custom_order,
        })
    source["mediaKind"] = row.media_kind
    return source


def normalize_source_dict(row: MediaSourceRow) -> dict:
    """``source_dict`` plus the rss alias fields the shared bridges expect."""
    src = source_dict(row)
    if row.source_format == "rss":
        src.setdefault("bookSourceUrl", src.get("sourceUrl") or "")
        src.setdefault("bookSourceName", src.get("sourceName") or "")
    # 固定真实来源格式，避免别名让 adapter_for 误判为 book
    src["_sourceFormat"] = row.source_format
    return src


def source_json(row: MediaSourceRow) -> dict:
    src = source_dict(row)
    return {
        "id": row.id,
        "sourceName": row.source_name,
        "sourceKey": row.source_key,
        "sourceFormat": row.source_format,
        "mediaKind": row.media_kind or None,
        "sourceGroup": row.source_group,
        "sourceComment": row.source_comment,
        "enabled": row.enabled,
        "customOrder": row.custom_order,
        "hasIcon": bool(src.get("sourceIcon")),
        "detectedType": src.get("type"),
        "detectedBookType": src.get("bookSourceType"),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _derive_all(source: dict, explicit: str | None) -> tuple[str, str, list[str]]:
    """Return (source_format, media_kind, warnings). kind='' means undecided."""
    fmt = schemas.source_format(source)
    try:
        kind, warnings = schemas.detect_media_kind(source, explicit)
    except ValueError as exc:  # 明确但暂不支持的数字类型
        return fmt, "", [str(exc)]
    return fmt, kind or "", warnings


async def import_sources(
    db: AsyncSession,
    items: list[dict] | dict,
    explicit_kind: str | None = None,
) -> dict:
    """Import media sources (rss + book dialects). Lossless raw storage.

    Returns {added, updated, skipped, warnings, needsKind, undecided[]}.
    """
    from ...models import MediaSourceRow as Row

    objs = items
    if isinstance(objs, dict):
        objs = objs.get("items", objs.get("data", [objs]))
    if isinstance(objs, dict):
        objs = [objs]
    if not isinstance(objs, list):
        objs = [objs]

    existing_rows = (await db.execute(select(Row))).scalars().all()
    existing: dict[tuple[str, str], Row] = {
        (r.source_format, r.source_key): r for r in existing_rows
    }

    added = updated = skipped = 0
    warnings: list[str] = []
    undecided: list[dict] = []
    needs_kind = False

    for item in objs:
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            fmt, kind, w_src = _derive_all(item, explicit_kind)
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            warnings.append(f"{item.get('sourceName') or item.get('sourceUrl')}: {exc}")
            continue
        key = schemas.source_key(item)
        if not key:
            skipped += 1
            continue
        warnings.extend(w_src)
        raw = json.dumps(item, ensure_ascii=False)
        try:
            custom_order = int(item.get("customOrder") or 0)
        except (TypeError, ValueError):
            custom_order = 0
        values = {
            "source_name": str(item.get("sourceName") or item.get("bookSourceName") or ""),
            "source_group": str(item.get("sourceGroup") or ""),
            "source_comment": str(item.get("sourceComment") or ""),
            "custom_order": custom_order,
            "raw_json": raw,
            "media_kind": kind,
        }
        row = existing.get((fmt, key))
        pushed_undecided = False
        if not kind:
            needs_kind = True
            undecided.append({
                "sourceFormat": fmt, "sourceKey": key,
                "sourceName": values["source_name"],
            })
            # 仍然落库（无损），管理页显式修正 media_kind
            values["media_kind"] = ""
            pushed_undecided = True
        if row:
            for k, v in values.items():
                setattr(row, k, v)
            updated += 1
            if pushed_undecided:
                updated -= 1
                if (fmt, key) not in existing:
                    updated += 1
        else:
            row = Row(source_format=fmt, source_key=key,
                      enabled=bool(item.get("enabled", True)), **values)
            db.add(row)
            existing[(fmt, key)] = row
            added += 1
            if pushed_undecided:
                added -= 1
                # 无类型来源仍计入 added（已落库待定）
                added += 1
    return {
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "warnings": warnings[:20],
        "needsKind": needs_kind,
        "undecided": undecided,
    }


async def set_kind(db: AsyncSession, source_id: int, media_kind: str) -> MediaSourceRow:
    kind = schemas.valid_kind(media_kind)
    if not kind:
        raise ValueError("无效的媒体类型")
    row = await db.get(MediaSourceRow, source_id)
    if row is None:
        raise KeyError("媒体源不存在")
    row.media_kind = kind
    return row