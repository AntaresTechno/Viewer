"""Declarations for source-capability adapters (see package docstring)."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GuestReadAdapter(Protocol):
    """备用读取路径：当通用规则流程吞到空/异常时，用它兜底。

    - ``matches``: 本源是否属于本适配器管辖（可同时看域名与 ``extra`` 开关）。
    - ``guest_cover`` / ``guest_toc`` / ``guest_content``: 备用读取，失败返回 None。
    - ``is_guest_chapter``: 主流程拿到的章节是否由本适配器产出（据此改走备用正文）。
    """

    def matches(self, source: dict[str, Any]) -> bool: ...

    async def guest_cover(
        self, source: dict[str, Any], book_url: str
    ) -> str | None: ...

    async def guest_toc(
        self,
        source: dict[str, Any],
        book: dict[str, Any],
        toc_url: str,
        base_url: str,
    ) -> list[dict[str, Any]] | None: ...

    async def guest_content(
        self, source: dict[str, Any], chapter: dict[str, Any]
    ) -> str | None: ...

    def is_guest_chapter(
        self, source: dict[str, Any], chapter: dict[str, Any], ch_url: str
    ) -> bool: ...