"""Classify mixed Legado source payloads and import each destination atomically."""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from ...services.book_source_import import import_book_sources
from ...services.rss_source_import import import_rss_sources
from ..media import schemas as media_schemas
from ..media.source_service import import_sources as import_media_sources


class AutoSourceImportError(ValueError):
    pass


def parse_source_payload(text: str) -> list:
    if not text or not text.strip():
        raise AutoSourceImportError("内容为空")
    try:
        value = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise AutoSourceImportError(f"JSON 解析失败: {exc}") from exc
    if isinstance(value, dict):
        value = value.get("items", value.get("data", value))
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise AutoSourceImportError("需要 Legado 来源对象或数组")
    return value


def classify_sources(items: list) -> dict:
    buckets: dict[str, list[dict]] = {
        "books": [],
        "subscriptions": [],
        "media": [],
    }
    classified = {
        "books": 0,
        "subscriptions": 0,
        "comic": 0,
        "audio": 0,
        "video": 0,
    }
    skipped = 0
    warnings: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        is_book = media_schemas.is_book_source(item)
        is_rss = media_schemas.is_rss_source(item)
        if not is_book and not is_rss:
            skipped += 1
            warnings.append("跳过无法识别的来源：缺少 bookSourceUrl/sourceUrl")
            continue
        try:
            media_kind, kind_warnings = media_schemas.detect_media_kind(item)
        except ValueError as exc:
            skipped += 1
            name = item.get("bookSourceName") or item.get("sourceName") or "未命名来源"
            warnings.append(f"{name}: {exc}")
            continue
        warnings.extend(kind_warnings)
        if media_kind:
            buckets["media"].append(item)
            classified[media_kind] += 1
        elif is_book:
            buckets["books"].append(item)
            classified["books"] += 1
        else:
            buckets["subscriptions"].append(item)
            classified["subscriptions"] += 1

    return {
        "buckets": buckets,
        "classified": classified,
        "skipped": skipped,
        "warnings": warnings,
    }


async def import_classified_sources(
    db: AsyncSession,
    items: list,
    *,
    known_engines: set[str],
) -> dict:
    plan = classify_sources(items)
    buckets = plan["buckets"]
    empty = {"added": 0, "updated": 0, "skipped": 0}

    books = dict(empty)
    subscriptions = dict(empty)
    media: dict = {
        **empty,
        "warnings": [],
        "needsKind": False,
        "undecided": [],
    }
    try:
        if buckets["books"]:
            books = await import_book_sources(
                db,
                buckets["books"],
                default_engine="legado",
                known_engines=known_engines,
                commit=False,
            )
        if buckets["subscriptions"]:
            subscriptions = await import_rss_sources(
                db,
                buckets["subscriptions"],
                commit=False,
            )
        if buckets["media"]:
            media = await import_media_sources(db, buckets["media"])
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    results = {
        "books": books,
        "subscriptions": subscriptions,
        "media": media,
    }
    return {
        "added": sum(value["added"] for value in results.values()),
        "updated": sum(value["updated"] for value in results.values()),
        "skipped": plan["skipped"] + sum(
            value["skipped"] for value in results.values()
        ),
        "classified": plan["classified"],
        "categories": results,
        "warnings": (plan["warnings"] + media.get("warnings", []))[:20],
    }
