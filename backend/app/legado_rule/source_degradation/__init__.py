"""Pluggable source-capability adapters for the legado engine.

See ``interfaces``/``registry`` modules. ``load_builtin()`` imports all bundled
adapters (self-registering), so the engine can stay book-source-agnostic.
"""
from __future__ import annotations

from .interfaces import ExploreParserAdapter, GuestReadAdapter, SearchAdapter
from .registry import (
    explore_parser_for,
    guest_reader_for,
    load_builtin,
    register,
    registered,
    searcher_for,
)

__all__ = [
    "ExploreParserAdapter",
    "GuestReadAdapter",
    "SearchAdapter",
    "explore_parser_for",
    "guest_reader_for",
    "load_builtin",
    "register",
    "registered",
    "searcher_for",
]
