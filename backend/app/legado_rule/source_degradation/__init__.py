"""Pluggable source-capability adapters for the legado engine.

See ``interfaces``/``registry`` modules. ``load_builtin()`` imports all bundled
adapters (self-registering), so the engine can stay book-source-agnostic.
"""
from __future__ import annotations

from .interfaces import GuestReadAdapter
from .registry import guest_reader_for, load_builtin, register, registered

__all__ = [
    "GuestReadAdapter",
    "guest_reader_for",
    "load_builtin",
    "register",
    "registered",
]