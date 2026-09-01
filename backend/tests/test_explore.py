"""Tests for the discover/explore crawling pipeline (explore_kinds + explore_book)."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.legado_rule import web_book
from app.legado_rule.js_bridge import detect_engine
from app.legado_rule.net import StrResponse


# ---------------------------------------------------------------- explore kinds
class TestExploreKinds:
    def test_empty_explore_url(self):
        assert web_book.explore_kinds({}) == []
        assert web_book.explore_kinds({"exploreUrl": "  "}) == []

    def test_line_split_format(self):
        src = {
            "bookSourceUrl": "http://x",
            "exploreUrl": "玄幻::http://x/list1\n仙侠::http://x/list2"
            "&&都市::http://x/list3",
        }
        kinds = web_book.explore_kinds(src)
        assert kinds == [
            {"title": "玄幻", "url": "http://x/list1", "type": "url"},
            {"title": "仙侠", "url": "http://x/list2", "type": "url"},
            {"title": "都市", "url": "http://x/list3", "type": "url"},
        ]

    def test_line_without_url(self):
        src = {"bookSourceUrl": "http://x", "exploreUrl": "热门::"}
        kinds = web_book.explore_kinds(src)
        assert kinds == [{"title": "热门", "url": None, "type": "url"}]

    def test_json_array_format(self):
        src = {
            "bookSourceUrl": "http://x",
            "exploreUrl": json.dumps([
                {"title": "热门", "url": "/hot", "type": "url"},
                {"title": "输入", "type": "text", "chars": ["a", "b"]},
            ], ensure_ascii=False),
        }
        kinds = web_book.explore_kinds(src)
        assert kinds[0] == {"title": "热门", "url": "/hot", "type": "url"}
        assert kinds[1]["type"] == "text"
        assert kinds[1]["chars"] == ["a", "b"]

    def test_js_driven_explore_url(self):
        if detect_engine() is None:
            pytest.skip("no JS engine installed")
        src = {
            "bookSourceUrl": "http://x",
            "exploreUrl": "<js>JSON.stringify([{'title':'玄幻','url':'/hot'}])</js>",
        }
        kinds = web_book.explore_kinds(src)
        assert kinds == [{"title": "玄幻", "url": "/hot", "type": "url"}]


# ---------------------------------------------------------------- explore books
SOURCE = {
    "bookSourceUrl": "http://s.example.com",
    "bookSourceName": "测试源",
    "exploreUrl": "http://s.example.com/explore.html",
}

EXPLORE_HTML = """
<html><body>
<div class="e-item"><a href="/book/x.html">探索书X</a></div>
<div class="e-item"><a href="/book/y.html">探索书Y</a></div>
</body></html>
"""


def _patch_fetch(monkeypatch, body: str, status: int = 200,
                 url: str = "http://s.example.com/explore.html"):
    async def fake_fetch(_aurl):
        return StrResponse(url, body, status)
    monkeypatch.setattr(web_book, "fetch_str", fake_fetch)
    return url


class TestExploreBooks:
    def test_uses_explore_rule(self, monkeypatch):
        _patch_fetch(monkeypatch, EXPLORE_HTML)
        src = dict(SOURCE)
        src["ruleExplore"] = {
            "bookList": "class.e-item",
            "name": "tag.a@text",
            "bookUrl": "tag.a@href",
        }
        books = asyncio.run(
            web_book.explore_book(src, "http://s.example.com/explore.html", 1)
        )
        names = [b["name"] for b in books]
        assert names == ["探索书X", "探索书Y"]
        assert books[0]["bookUrl"] == "http://s.example.com/book/x.html"

    def test_falls_back_to_search_rule(self, monkeypatch):
        _patch_fetch(monkeypatch, EXPLORE_HTML)
        src = dict(SOURCE)
        # ruleExplore.bookList blank -> fall back to ruleSearch
        src["ruleExplore"] = {"name": "class.search-fallback"}
        src["ruleSearch"] = {
            "bookList": "class.e-item",
            "name": "tag.a@text",
            "bookUrl": "tag.a@href",
        }
        books = asyncio.run(
            web_book.explore_book(src, "http://s.example.com/explore.html", 1)
        )
        assert [b["name"] for b in books] == ["探索书X", "探索书Y"]

    def test_rejects_missing_url(self):
        with pytest.raises(Exception):
            asyncio.run(web_book.explore_book(SOURCE, "", 1))


# ----------------------------------------------------------- engine interface
class TestEngineExploreInterface:
    def test_engine_exposes_explore_ops(self):
        from app.plugins.registry import PluginContext, get_engine

        eng = get_engine("legado", PluginContext(settings=None))
        for op in ("explore_kinds", "explore_book", "search_book"):
            assert callable(getattr(eng, op, None)), op

    def test_book_explore_permission_declared(self):
        from app.plugins.registry import all_permission_keys

        keys = {k for k, _ in all_permission_keys()}
        assert "books.explore" in keys


# --------------------------------------------------- stale-login auto-clean
class TestStaleLoginAutoClean:
    """发现页 JS 崩溃时，引擎按书源域名清掉残留 sessionid 并重试一次。"""

    def test_source_domains_extracts_url_hosts(self):
        from app.legado_rule import web_book

        src = {
            "bookSourceUrl": "https://reading.snssdk.com#mgz0326",
            "loginUrl": "token：/@js:",
            "searchUrl": "@js: xGod('https://fanqienovel.com/api/...')",
            "exploreUrl": "https://www.fanqienovel.com/x\n@js:",
        }
        assert web_book._source_domains(src) == {"snssdk.com", "fanqienovel.com"}

    def test_remove_cookie_key_drops_single_key(self, monkeypatch):
        from pathlib import Path

        from app.legado_rule import source_state

        state_file = (
            Path(__file__).resolve().parents[1] / ".tmp_autoclean_test.json"
        )
        monkeypatch.setattr(source_state, "_STATE_PATH", state_file)
        monkeypatch.setattr(source_state, "_EMPTY", {
            k: dict(v) for k, v in source_state._EMPTY.items()
        })
        try:
            source_state.set_cookie("https://fanqienovel.com",
                                    "sessionid=abc; &x=1", groups=[])
            # case-insensitive key lookup
            assert source_state.remove_cookie_key(
                "https://fanqienovel.com", "SessionID") is True
            assert source_state.get_cookie("https://fanqienovel.com") == "&x=1"
            # already gone -> False
            assert source_state.remove_cookie_key(
                "https://fanqienovel.com", "sessionid") is False
        finally:
            state_file.unlink(missing_ok=True)

    def test_explore_js_crash_cleans_then_retries(self, monkeypatch):
        from app.legado_rule import source_state, web_book
        from app.legado_rule.js_bridge import RuleJSError
        import json as _json

        calls = {"n": 0, "dropped": []}

        def fake_run(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuleJSError("JS 执行出错: forEach of undefined")
            return _json.dumps([{"title": "恢复", "url": "/y"}], ensure_ascii=False)

        src = {
            "bookSourceUrl": "https://reading.snssdk.com#x",
            "exploreUrl": "@js:boom()",
        }
        monkeypatch.setattr("app.legado_rule.js_bridge.eval_js", fake_run)
        monkeypatch.setattr(
            source_state, "remove_cookie_key",
            lambda url, key: (
                calls["dropped"].append(url),
                calls["dropped"].append(key),
                True,
            )[2],
        )
        kinds = web_book.explore_kinds(src)
        # 第一次崩溃 -> 清洗 sessionid -> 第二次成功
        assert calls["n"] == 2
        assert calls["dropped"] == ["https://snssdk.com", "sessionid"]
        assert kinds == [{"title": "恢复", "url": "/y", "type": "url"}]

    def test_explore_js_crash_without_clean_reraises(self, monkeypatch):
        from app.legado_rule import web_book
        from app.legado_rule.js_bridge import RuleJSError

        def fake_run(*a, **k):
            raise RuleJSError("JS 执行出错")

        src = {"bookSourceUrl": "http://plain.example.com", "exploreUrl": "@js:x()"}
        monkeypatch.setattr("app.legado_rule.js_bridge.eval_js", fake_run)
        with pytest.raises(RuleJSError):
            web_book.explore_kinds(src)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))