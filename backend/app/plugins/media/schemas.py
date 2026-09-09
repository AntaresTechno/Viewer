"""Media plugin — shared schemas, media kinds and source kind detection."""
from __future__ import annotations

import re
from typing import Any, Literal

MEDIA_KINDS: list[str] = ["comic", "audio", "video"]
MediaKind = Literal["comic", "audio", "video"]


def valid_kind(value: str | None) -> str | None:
    return value if value in MEDIA_KINDS else None


def _numeric_type(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_rss_source(source: dict) -> bool:
    """RssSource 方言：有 sourceUrl（且通常有 ruleArticles / sortUrl）。"""
    key = str(source.get("sourceUrl") or "").strip()
    return bool(key) and not str(source.get("bookSourceUrl") or "").strip()


def is_book_source(source: dict) -> bool:
    return bool(str(source.get("bookSourceUrl") or "").strip())


# ---------------------------------------------------------------------------
# Kind detection (章节 6.2 的优先级)
# ---------------------------------------------------------------------------
def _detect_rss_heuristic(source: dict) -> str | None:
    """只能在前三都缺失时启用的启发式识别."""
    content = str(source.get("ruleContent") or "").lower()
    if "<video" in content:
        return "video"
    if "<audio" in content:
        return "audio"
    # 大量图片规则为主 → comic
    image_rules = str(source.get("ruleImage") or "").strip()
    if image_rules and not re.search(r"<js>|\s,\s|&&", image_rules):
        return "comic"
    return None


def detect_media_kind(source: dict, explicit: str | None = None) -> tuple[str, list[str]]:
    """按严格优先级归一化 media_kind，返回 (kind, warnings)。

    kind 为 None 等价于 needsKind=true，由调用方让用户显式选择。
    """
    warnings: list[str] = []
    if explicit:
        kind = valid_kind(explicit.strip().lower())
        if kind:
            return kind, warnings
        warnings.append(f"指定的媒体类型 '{explicit}' 无效，改用自动识别")

    # 源内新增的字符串 mediaType
    static = str(source.get("mediaType") or "").strip().lower()
    kind = valid_kind(static)
    if kind:
        return kind, warnings

    # RssSource.type：1->comic、2->video、扩展 3->audio
    if is_rss_source(source):
        t = _numeric_type(source.get("type"))
        if t == 1:
            return "comic", warnings
        if t == 2:
            return "video", warnings
        if t == 3:
            return "audio", warnings
        # type=0 或缺失表示普通文章订阅；仅在未声明时尝试规则启发式。
        if t == 0:
            return None, warnings
        if "type" not in source or source.get("type") in (None, ""):
            heuristic = _detect_rss_heuristic(source)
            if heuristic is not None:
                label = {"comic": "漫画", "audio": "音频", "video": "视频"}.get(
                    heuristic, heuristic)
                warnings.append(
                    f"源未声明 type，已根据规则内容识别为{label}"
                )
                return heuristic, warnings
            return None, warnings
        raise ValueError("暂不支持的 RssSource.type")
    elif is_book_source(source):
        t = _numeric_type(source.get("bookSourceType"))
        if t == 1:
            return "audio", warnings
        if t == 2:
            return "comic", warnings
        if t == 3:
            return "video", warnings
        heuristic = _detect_book_heuristic(source)
        if heuristic is not None:
            return heuristic, warnings
        return None, warnings

    heuristic = _detect_rss_heuristic(source) or _detect_book_heuristic(source)
    return heuristic, warnings


def _detect_book_heuristic(source: dict) -> str | None:
    content = str(source.get("ruleContent") or "").lower()
    if isinstance(source.get("ruleContent"), dict):
        content = str(source["ruleContent"].get("content") or "").lower()
    if "<audio" in content or content.startswith("(\\.mp3|") or ".mp3" in content:
        return "audio"
    image_rules = str(
        (source.get("ruleContent") or {}).get("imageList")  # type: ignore[union-attr]
        or "".join(
            str(v) for k, v in (source.get("ruleContent") or {}).items()  # type: ignore[union-attr]
            if "image" in str(k).lower()
        )
        or ""
    )
    if image_rules:
        return "comic"
    return None


def source_format(source: dict) -> str:
    return "book" if is_book_source(source) else "rss"


def source_key(source: dict) -> str:
    """原 sourceUrl 或 bookSourceUrl."""
    return str(
        source.get("bookSourceUrl")
        or source.get("sourceUrl")
        or source.get("sourceName")
        or ""
    ).strip()


# ---------------------------------------------------------------------------
# Normalized DTO objects
# ---------------------------------------------------------------------------
def _tag_split(tags: str | list[str] | None) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, list):
        return [str(t).strip() for t in tags if str(t).strip()]
    return [t.strip() for t in re.split(r"[\s,，、]+", str(tags)) if t.strip()]


class MediaSort:
    __slots__ = ("name", "url")

    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url

    def to_dict(self) -> dict:
        return {"name": self.name, "url": self.url}


class MediaCatalogItem:
    __slots__ = (
        "item_key", "item_url", "title", "creator", "cover_url", "intro",
        "latest_unit", "total_units", "tags", "source_id", "source_name",
        "media_kind",
    )

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot, "" if slot != "total_units" else 0))
        self.tags = kw.get("tags") or []

    def to_dict(self) -> dict:
        return {
            "itemKey": self.item_key,
            "itemUrl": self.item_url,
            "title": self.title,
            "creator": self.creator,
            "coverUrl": self.cover_url,
            "intro": self.intro,
            "latestUnit": self.latest_unit,
            "totalUnits": self.total_units,
            "tags": self.tags,
            "sourceId": self.source_id,
            "sourceName": self.source_name,
            "mediaKind": self.media_kind,
        }


class MediaUnitDto:
    __slots__ = ("unit_key", "index", "title", "locator", "duration_ms", "published_at", "locked")

    def __init__(self, unit_key: str, index: int, title: str, locator: str = "",
                 duration_ms: int = 0, published_at: Any = None, locked: bool = False):
        self.unit_key = unit_key
        self.index = index
        self.title = title
        self.locator = locator
        self.duration_ms = duration_ms
        self.published_at = published_at
        self.locked = locked

    def to_dict(self) -> dict:
        return {
            "unitKey": self.unit_key,
            "index": self.index,
            "title": self.title,
            "locator": self.locator,
            "durationMs": self.duration_ms,
            "publishedAt": self.published_at.isoformat() if self.published_at else None,
            "locked": self.locked,
        }


def normalize_tags(value: Any) -> list[str]:
    return _tag_split(value)


def make_catalog_item(**kw) -> MediaCatalogItem:
    return MediaCatalogItem(**kw)
