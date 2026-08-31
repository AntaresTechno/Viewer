"""Registry for source-capability adapters.

Adapters self-register at module import (``register(adapter)``), so a book
source gets its escape hatches with zero configuration by default. A single
source can override/stamp-off an adapter through its ``extra`` JSON, which the
adapter's ``matches()`` is responsible for honouring.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .interfaces import GuestReadAdapter

_registry: list["GuestReadAdapter"] = []


def register(adapter: "GuestReadAdapter") -> None:
    """Add an adapter once. Idempotent by identity."""
    if not any(a is adapter for a in _registry):
        _registry.append(adapter)


def guest_reader_for(source: dict[str, Any]) -> "GuestReadAdapter | None":
    """First adapter whose ``matches(source)`` returns True, else None."""
    for adapter in _registry:
        try:
            if adapter.matches(source):
                return adapter
        except Exception:  # noqa: BLE001 - a broken matcher must not sink a request
            continue
    return None


def registered() -> tuple["GuestReadAdapter", ...]:
    return tuple(_registry)


def load_builtin() -> None:
    """Import all bundled adapters so they self-register."""
    from . import fanqie  # noqa: F401, PLC0415