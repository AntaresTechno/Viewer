"""BookSource-dialect media adapter — Legado 音频书源 & 图片/漫画书源.

Reuses the existing web-book pipeline (search / explore / book_info / get_toc)
and maps the book-shaped result onto the unified media DTOs.
"""
from __future__ import annotations

from typing import Any

from ....legado_rule.exceptions import FetchError, RuleError
from ....legado_rule.web_book import (
    book_info,
    explore_book,
    explore_kinds,
    get_content,
    get_toc,
    search_book,
)
from .. import schemas
from .base import MediaCatalogPage, MediaSourceAdapter, ResolvedMedia


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            obj = json.loads(value)
            return obj if isinstance(obj, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _book_to_item(source: dict, book: dict, source_id: int = 0) -> schemas.MediaCatalogItem:
    return schemas.MediaCatalogItem(
        item_key=str(book.get("bookUrl") or ""),
        item_url=str(book.get("bookUrl") or ""),
        title=str(book.get("name") or ""),
        creator=str(book.get("author") or ""),
        cover_url=str(book.get("coverUrl") or ""),
        intro=str(book.get("intro") or ""),
        latest_unit=str(book.get("lastChapter") or ""),
        total_units=0,
        tags=[t for t in str(book.get("kind") or "").replace(" ", ",").split(",") if t],
        source_id=source_id,
        source_name=str(source.get("bookSourceName") or source.get("sourceName") or ""),
        media_kind=schemas.valid_kind(str(source.get("mediaKind") or "")) or "video",
    )


class BookMediaAdapter(MediaSourceAdapter):
    kind = "book"

    def list_sorts(self) -> list[schemas.MediaSort]:
        try:
            kinds = explore_kinds(self.source)
        except Exception:  # noqa: BLE001
            kinds = []
        sorts = []
        for kd in kinds:
            if kd.get("type") != "url":
                continue  # 文本控件等无法作为普通分类
            sorts.append(schemas.MediaSort(str(kd.get("title") or ""), str(kd.get("url") or "")))
        if not sorts:
            sorts.append(schemas.MediaSort("默认", str(self.source.get("bookSourceUrl") or "")))
        return sorts

    def get_detail(self, item: dict) -> schemas.MediaCatalogItem:
        return _book_to_item(self.source, item, int(item.get("source_id") or 0))

    async def list_items(self, sort: dict, page: int) -> MediaCatalogPage:
        url = str(sort.get("url") or "")
        if not url:
            return MediaCatalogPage([], None, "该分类没有可用的地址")
        try:
            books = await explore_book(self.source, url, page)
        except Exception as exc:  # noqa: BLE001
            return MediaCatalogPage([], None, str(exc))
        rows = [_book_to_item(self.source, b) for b in books]
        return MediaCatalogPage(rows, None)

    async def search_items(self, keyword: str, page: int) -> MediaCatalogPage:
        try:
            books = await search_book(self.source, keyword, page)
        except Exception as exc:  # noqa: BLE001
            return MediaCatalogPage([], None, str(exc))
        return MediaCatalogPage([_book_to_item(self.source, b) for b in books], None)

    async def fetch_detail(self, item: dict) -> schemas.MediaCatalogItem:
        """现场请求书源详情页，刷新元数据。"""
        book = dict(item)
        try:
            info = await book_info(self.source, book)
        except Exception as exc:  # noqa: BLE001
            info = book
        return _book_to_item(self.source, info, int(item.get("source_id") or 0))

    async def get_units(self, item: dict, refresh: bool = True) -> list[schemas.MediaUnitDto]:
        toc_url = str(item.get("tocUrl") or item.get("toc_url") or item.get("item_url") or "")
        if not toc_url and item.get("item_url"):
            # 书源无独立目录时详情页本身即目录
            toc_url = str(item["item_url"])
        try:
            chapters = await get_toc(self.source, item, toc_url)
        except Exception:  # noqa: BLE001
            return []
        units = [
            schemas.MediaUnitDto(
                unit_key=str(ch.get("index") or i),
                index=int(ch.get("index") or i),
                title=str(ch.get("title") or ""),
                locator=str(ch.get("url") or ""),
            )
            for i, ch in enumerate(chapters)
        ]
        return units

    async def resolve_unit(self, item: dict, unit: dict) -> ResolvedMedia:
        chapter = {
            "url": str(unit.get("locator") or unit.get("url") or ""),
            "title": str(unit.get("title") or ""),
            "index": int(unit.get("index") or unit.get("unit_key") or 0),
            "isVolume": False,
        }
        kind = schemas.valid_kind(str(self.source.get("mediaKind") or "")) or "video"
        content_rules = _as_dict((self.source.get("mediaRules") or {}).get("content")
                                 if isinstance(self.source.get("mediaRules"), dict) else None)
        if kind == "comic":
            return await self._resolve_comic(item, chapter, content_rules)
        # audio / video：结构化内容规则 → 音频地址；否则暂不支持原生
        if content_rules.get("streamUrl"):
            from .rss import _parse_headers

            try:
                raw = await get_content(self.source, item, chapter)
            except (FetchError, RuleError) as exc:  # noqa: BLE001
                return ResolvedMedia(kind=kind, warning=str(exc))
            streams = [{
                "url": raw.strip(),
                "quality": "auto",
                "mime": "audio/mpeg" if kind == "audio" else "video/mp4",
                "headers": _parse_headers(content_rules.get("headers")),
            }] if raw else []
            return ResolvedMedia(kind=kind, streams=streams)
        return ResolvedMedia(kind=kind, warning="该源未提供结构化可播放内容")

    async def _resolve_comic(self, item: dict, chapter: dict, content_rules: dict) -> ResolvedMedia:
        from .rss import _parse_headers

        # 尝试按 mediaRules.content.imageList/imageUrl 解析图片列表
        if content_rules.get("imageList") and content_rules.get("imageUrl"):
            from ....legado_rule.analyze_rule import AnalyzeRule
            from ....legado_rule.analyze_url import AnalyzeUrl
            from ....legado_rule.rss_source import runtime_source
            from ....legado_rule.web_book import fetch_str

            url = str(chapter.get("url") or "")
            resp = await fetch_str(AnalyzeUrl(
                url, base_url=str(self.source.get("bookSourceUrl") or ""),
                source=runtime_source(self.source),
            ))
            if not (resp.error or not resp.ok):
                ar = AnalyzeRule(rule_data={**item, **chapter},
                                 source=runtime_source(self.source), base_url=resp.url)
                ar.set_content(resp.body, base_url=resp.url)
                images = []
                for el in ar.get_elements(content_rules["imageList"]):
                    ar.set_content(el)
                    src = ar.get_string(content_rules["imageUrl"], is_url=True)
                    if src:
                        images.append({"url": src, "headers": _parse_headers(content_rules.get("headers"))})
                if images:
                    return ResolvedMedia(kind="comic", images=images,
                                         reading_direction=content_rules.get("readingDirection") or "ltr")
        # 兜底：整章正文当作单张文本（漫画源以正文规则返回图片 URL 列表）
        try:
            raw = await get_content(self.source, item, chapter)
        except (FetchError, RuleError) as exc:  # noqa: BLE001
            return ResolvedMedia(kind="comic", warning=str(exc))
        return ResolvedMedia(kind="comic", images=[], warning="未解析到可读图片")