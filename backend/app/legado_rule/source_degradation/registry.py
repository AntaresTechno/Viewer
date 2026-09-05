"""Registry for source-capability adapters.

Adapters self-register at module import (``register(adapter)``), so a book
source gets its escape hatches with zero configuration by default. A single
source can override/stamp-off an adapter through its ``extra`` JSON; each
capability matcher is responsible for honouring that switch.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .interfaces import GuestReadAdapter, SearchAdapter

_registry: list[Any] = []


def register(adapter: Any) -> None:
    """Add an adapter once. Idempotent by identity."""
    if not any(a is adapter for a in _registry):
        _registry.append(adapter)


def guest_reader_for(source: dict[str, Any]) -> "GuestReadAdapter | None":
    """First adapter whose ``matches(source)`` returns True, else None."""
    from .interfaces import GuestReadAdapter

    for adapter in _registry:
        try:
            if isinstance(adapter, GuestReadAdapter) and adapter.matches(source):
                return adapter
        except Exception:  # noqa: BLE001 - a broken matcher must not sink a request
            continue
    return None


def searcher_for(source: dict[str, Any]) -> "SearchAdapter | None":
    """首个声明可接管该书源搜索的适配器；核心层不感知具体来源。"""
    from .interfaces import SearchAdapter

    for adapter in _registry:
        try:
            if (
                isinstance(adapter, SearchAdapter)
                and adapter.matches_search(source)
            ):
                return adapter
        except Exception:  # noqa: BLE001 - 匹配器异常不能拖垮通用搜索
            continue
    return None


def registered() -> tuple[Any, ...]:
    return tuple(_registry)


def load_builtin() -> None:
    """Import all bundled adapters so they self-register."""
    from . import fanqie  # noqa: F401, PLC0415
