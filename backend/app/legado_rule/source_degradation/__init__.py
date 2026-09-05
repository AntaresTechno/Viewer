"""Pluggable source-capability adapters for the legado engine.

See ``interfaces``/``registry`` modules. ``load_builtin()`` imports all bundled
adapters (self-registering), so the engine can stay book-source-agnostic.
"""
from __future__ import annotations

from .interfaces import GuestReadAdapter, SearchAdapter
from .registry import (
    guest_reader_for,
    load_builtin,
    register,
    registered,
    searcher_for,
)

__all__ = [
    "GuestReadAdapter",
    "SearchAdapter",
    "guest_reader_for",
    "load_builtin",
    "register",
    "registered",
    "searcher_for",
]
