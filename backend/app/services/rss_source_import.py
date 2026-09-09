"""Shared RSS-source import used by the RSS UI and protocol adapters."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import RssSourceRow


class RssSourceImportError(ValueError):
    pass


def normalize_rss_source_payload(value) -> list:
    if isinstance(value, dict):
        value = value.get("items", value.get("data", value))
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise RssSourceImportError("需要订阅源对象或数组")
    return value


async def import_rss_sources(
    db: AsyncSession,
    value,
    *,
    commit: bool = True,
) -> dict[str, int]:
    items = normalize_rss_source_payload(value)
    existing = {
        row.source_url: row
        for row in (await db.execute(select(RssSourceRow))).scalars().all()
    }
    added = updated = skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        source_url = str(item.get("sourceUrl") or "").strip()
        if not source_url:
            skipped += 1
            continue
        raw = json.dumps(item, ensure_ascii=False)
        try:
            custom_order = int(item.get("customOrder") or 0)
        except (TypeError, ValueError):
            custom_order = 0
        values = {
            "source_name": str(item.get("sourceName") or ""),
            "source_icon": str(item.get("sourceIcon") or ""),
            "source_group": str(item.get("sourceGroup") or ""),
            "source_comment": str(item.get("sourceComment") or ""),
            "custom_order": custom_order,
            "raw_json": raw,
        }
        row = existing.get(source_url)
        if row is not None:
            for key, field_value in values.items():
                setattr(row, key, field_value)
            updated += 1
        else:
            row = RssSourceRow(
                source_url=source_url,
                enabled=bool(item.get("enabled", True)),
                **values,
            )
            db.add(row)
            existing[source_url] = row
            added += 1
    if commit:
        await db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}
