"""Shared book-source import service used by UI and protocol plugins."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import BookSourceRow


class BookSourceImportError(ValueError):
    pass


def parse_book_source_json(text: str) -> list:
    if not text or not text.strip():
        raise BookSourceImportError("内容为空")
    try:
        value = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise BookSourceImportError(f"JSON 解析失败: {exc}") from exc
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise BookSourceImportError("需要书源对象或数组")
    return value


async def import_book_sources(db: AsyncSession, values: list, *,
                              default_engine: str,
                              known_engines: set[str],
                              commit: bool = True) -> dict[str, int]:
    if default_engine not in known_engines:
        raise BookSourceImportError(f"未知引擎: {default_engine}")
    existing = {
        row.source_url: row
        for row in (await db.execute(select(BookSourceRow))).scalars().all()
    }
    added = updated = skipped = 0
    for source in values:
        if not isinstance(source, dict):
            skipped += 1
            continue
        source_url = str(source.get("bookSourceUrl") or "").strip()
        if not source_url:
            skipped += 1
            continue
        engine = str(source.get("viewEngine") or default_engine).strip()
        if engine not in known_engines:
            skipped += 1
            continue
        group_raw = source.get("bookSourceGroup") or ""
        group = (
            str(group_raw.split(",")[0]).strip()
            if isinstance(group_raw, str) else ""
        )
        raw = json.dumps(source, ensure_ascii=False)
        if source_url in existing:
            row = existing[source_url]
            row.raw_json = raw
            row.source_name = str(source.get("bookSourceName") or "")
            row.source_group = group
            row.engine = engine
            updated += 1
        else:
            row = BookSourceRow(
                source_url=source_url,
                source_name=str(source.get("bookSourceName") or ""),
                source_group=group,
                raw_json=raw,
                enabled=True,
                engine=engine,
            )
            db.add(row)
            existing[source_url] = row
            added += 1
    if commit:
        await db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}
