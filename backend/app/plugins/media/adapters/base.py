"""Media source adapter protocol and per-format factory."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import schemas


@dataclass
class MediaCatalogPage:
    items: list[schemas.MediaCatalogItem] = field(default_factory=list)
    next_url: str | None = None
    warning: str = ""


@dataclass
class ResolvedMedia:
    """一次 resolve_unit 的规范化结果。kind 决定使用哪个字段组。

    video/audio：``streams`` 列表；comic：``images`` 列表。
    """
    kind: str
    streams: list[dict] = field(default_factory=list)
    subtitles: list[dict] = field(default_factory=list)
    poster_url: str = ""
    lyrics: str = ""
    images: list[dict] = field(default_factory=list)
    reading_direction: str = ""
    next_unit_key: str = ""
    warning: str = ""


@dataclass
class LegacyDocument:
    """旧式 HTML 播放器兼容层返回给前端的隔离文档。"""
    html: str = ""
    iframe_key: str = ""
    warning: str = ""
    # 是否需要在 srcdoc 前注入本站 bootstrap（LegacyMediaFrame 用）
    inject_bootstrap: bool = True


class MediaSourceAdapter:
    """统一媒体源适配器协议（草案见 plan §5.2）。"""

    kind: str  # 'rss' | 'book'
    media_kind: str = "video"

    def __init__(self, source: dict):
        self.source = source

    def list_sorts(self) -> list[schemas.MediaSort]:  # pragma: no cover - abstract
        raise NotImplementedError

    async def list_items(self, sort: dict, page: int) -> MediaCatalogPage:  # pragma: no cover
        raise NotImplementedError

    async def search_items(self, keyword: str, page: int) -> MediaCatalogPage:  # pragma: no cover
        raise NotImplementedError

    def get_detail(self, item: dict) -> schemas.MediaCatalogItem:  # pragma: no cover
        raise NotImplementedError

    async def get_units(self, item: dict, refresh: bool = True) -> list[schemas.MediaUnitDto]:  # pragma: no cover
        raise NotImplementedError

    async def resolve_unit(self, item: dict, unit: dict) -> ResolvedMedia:  # pragma: no cover
        raise NotImplementedError

    async def render_legacy_document(self, item: dict) -> LegacyDocument | None:  # pragma: no cover
        return None


def adapter_for(source: dict) -> MediaSourceAdapter:
    """Return the adapter for a runtime-normalized media source dict."""
    fmt = source.get("_sourceFormat") or schemas.source_format(source)
    if fmt == "book":
        from .book import BookMediaAdapter

        return BookMediaAdapter(source)
    from .rss import RssMediaAdapter

    return RssMediaAdapter(source)