"""engine_legado — 阅读(legado)书源规则引擎插件。

这是第一个「源规则引擎」插件：books 插件不再直接绑定解析实现，
而是按 BookSourceRow.engine 字段把搜索/详情/目录/正文请求分发给
对应引擎。未来接入其他规则体系（如不同站点私有 JSON 协议）只需
再写一个带 ``ENGINE`` + ``create_engine`` 的插件包。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

meta = {
    "name": "engine_legado",
    "title": "Legado 书源引擎",
    "version": "1.0.0",
    "description": "阅读(legado)兼容书源规则引擎（jsoup/JSONPath/XPath/Regex + AnalyzeUrl）",
    "order": 5,
}

ENGINE = {
    "key": "legado",
    "title": "Legado 书源",
    "version": "1.0.0",
    "description": "阅读(legado)书源规则：AnalyzeRule / AnalyzeUrl / WebBook 全流程",
}


class LegadoEngine:
    """Adapter implementing the source-engine interface over legado_rule."""

    key = "legado"

    def __init__(self, ctx):
        self.ctx = ctx

    # -- introspection -------------------------------------------------
    def matches(self, raw: dict) -> bool:
        """Whether a raw source JSON blob looks like a legado book source."""
        if not isinstance(raw, dict):
            return False
        return bool(raw.get("bookSourceUrl"))

    async def search_book(self, src: dict, key: str, page: int = 1) -> list[dict]:
        from ...legado_rule import web_book

        return await web_book.search_book(src, key, max(1, page))

    async def explore_kinds(self, src: dict) -> list[dict]:
        from ...legado_rule import web_book

        return web_book.explore_kinds(src)

    async def explore_book(self, src: dict, url: str, page: int = 1) -> list[dict]:
        from ...legado_rule import web_book

        return await web_book.explore_book(src, url, max(1, page))

    async def book_info(self, src: dict, book: dict) -> dict:
        from ...legado_rule import web_book

        return await web_book.book_info(src, book)

    async def get_toc(self, src: dict, book: dict,
                      toc_url: str | None = None) -> list[dict]:
        from ...legado_rule import web_book

        return await web_book.get_toc(src, book, toc_url)

    async def get_content(self, src: dict, book: dict, chapter: dict,
                          next_chapter_url: str | None = None,
                          base_url: str | None = None) -> str:
        from ...legado_rule import web_book

        return await web_book.get_content(
            src, book, chapter, next_chapter_url, base_url=base_url
        )


def create_engine(ctx: PluginContext) -> LegadoEngine:
    return LegadoEngine(ctx)
