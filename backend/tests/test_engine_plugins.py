"""Tests for the pluggable source-engine architecture."""
from __future__ import annotations

from app.plugins.registry import (
    PluginContext,
    all_engines,
    discover_plugins,
    engine_keys,
    get_engine,
    set_disabled_plugins,
)


class TestEngineRegistry:
    def test_legado_engine_discovered(self):
        discover_plugins(force=True)
        keys = engine_keys()
        assert "legado" in keys
        info = next(e for e in all_engines() if e.key == "legado")
        assert info.plugin_name == "engine_legado"
        assert info.title

    def test_get_engine_instance_and_fallback(self):
        ctx = PluginContext(settings=None)
        eng = get_engine("legado", ctx)
        # interface contract required by the books plugin
        for op in ("search_book", "book_info", "get_toc", "get_content"):
            assert callable(getattr(eng, op, None)), op
        # same instance is cached
        assert get_engine("legado", ctx) is eng
        # unknown key falls back to legado
        assert get_engine("does-not-exist", ctx) is eng
        assert type(eng).__name__ == "LegadoEngine"

    def test_disabled_plugin_blocks_engine(self):
        set_disabled_plugins({"engine_legado"})
        try:
            try:
                get_engine("legado", PluginContext(settings=None))
                raised = False
            except KeyError:
                raised = True
            assert raised, "disabled engine plugin must raise KeyError"
        finally:
            set_disabled_plugins(set())

    def test_matches_sniffing(self):
        eng = get_engine("legado", PluginContext(settings=None))
        assert eng.matches({"bookSourceUrl": "http://x"}) is True
        assert eng.matches({"foo": 1}) is False
        assert eng.matches("nope") is False

    def test_engine_only_plugin_has_no_mount_router(self):
        discover_plugins(force=True)
        info = discover_plugins()["engine_legado"]
        assert info.create_router is None
