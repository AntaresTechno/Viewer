"""Regression tests: source jsLib injection + search detail-page fallback.

Mirrors legado's SharedJsScope (BaseSource.getShareScope used by
AnalyzeRule.evalJS / AnalyzeUrl.evalJS) and BookList.analyzeBookList's
bookUrlPattern redirect handling.
"""
from __future__ import annotations

import pytest

from app.legado_rule.analyze_rule import AnalyzeRule
from app.legado_rule.analyze_url import AnalyzeUrl
from app.legado_rule.web_book import _parse_book_list

pytest.importorskip("dukpy", reason="需要 JS 引擎（dukpy 或 quickjs）")


SOURCE_WITH_JSLIB = {
    "bookSourceUrl": "https://www.example.com/",
    "jsLib": (
        "function cover(href) {"
        "  var m = String(href).match(/\\/(\\d+)\\/?$/);"
        "  if (!m) return '';"
        "  return 'https://img.example.com/' + m[1] + '.jpg';"
        "}"
        "function shout(s) { return String(s).toUpperCase(); }"
    ),
}

LIST_HTML = """
<html><body>
<div class="bookbox">
  <h4 class="bookname"><a href="/books/100/">没钱修什么仙？</a></h4>
</div>
<div class="bookbox">
  <h4 class="bookname"><a href="/books/200/">凡人修仙传</a></h4>
</div>
</body></html>
"""

INFO_HTML = """
<html><head>
<title>没钱修什么仙？</title></head>
<body>
<h1>没钱修什么仙？</h1>
<span class="author">熊狼狗</span>
<p class="bookintro">一个没钱的修仙者。</p>
</body></html>
"""

RULES = {
    "bookList": "class.bookbox",
    "name": "class.bookname@a@text",
    "coverUrl": "class.bookname@a@href@js:cover(result)",
}


def test_jslib_callable_from_rule_js():
    ar = AnalyzeRule(source=dict(SOURCE_WITH_JSLIB), base_url="https://www.example.com/")
    ar.set_content("<p>x</p>")
    assert ar.get_string("@js:shout('abc')") == "ABC"


def test_no_jslib_leaves_others_unaffected():
    src = {"bookSourceUrl": "https://www.example.com/"}
    ar = AnalyzeRule(source=src, base_url="https://www.example.com/")
    ar.set_content(LIST_HTML)
    ar.set_content(ar.get_elements("class.bookbox")[0])
    assert ar.get_string("class.bookname@a@text") == "没钱修什么仙？"


def test_analyze_url_uses_jslib():
    aurl = AnalyzeUrl(
        "https://www.example.com/s/{{shout(key)}}",
        key="ab",
        source=dict(SOURCE_WITH_JSLIB),
    )
    assert aurl.url.endswith("/s/AB")


def test_search_list_items_use_jslib_fields():
    books = _parse_book_list(SOURCE_WITH_JSLIB, RULES,
                             "https://www.example.com/search", LIST_HTML,
                             is_search=True)
    assert [b["name"] for b in books] == ["没钱修什么仙？", "凡人修仙传"]
    assert books[0]["coverUrl"] == "https://img.example.com/100.jpg"
    assert books[1]["coverUrl"] == "https://img.example.com/200.jpg"


def test_search_redirect_to_info_page_via_book_url_pattern():
    src = dict(SOURCE_WITH_JSLIB)
    src["ruleBookInfo"] = {
        "name": "tag.h1@text",
        "author": "class.author@text##作者[：:]",
        "intro": "class.bookintro@text",
    }
    # 精确搜索唯一命中：站点重定向到详情页，列表规则必然匹配 0 个元素
    books = _parse_book_list(src, RULES,
                             "https://www.example.com/books/100/", INFO_HTML,
                             is_search=True)
    assert len(books) == 1
    assert books[0]["name"] == "没钱修什么仙？"
    assert books[0]["author"] == "熊狼狗"
    assert books[0]["bookUrl"] == "https://www.example.com/books/100/"


def test_explore_does_not_apply_book_url_pattern():
    # bookUrlPattern 检查仅搜索有效；但配置了 pattern 后空列表也不再走详情兜底
    src = dict(SOURCE_WITH_JSLIB)
    src["ruleBookInfo"] = {"name": "tag.h1@text"}
    src["bookUrlPattern"] = r"https?://www\.example\.com/(books|index)/\d+.*"
    books = _parse_book_list(src, RULES,
                             "https://www.example.com/books/100/", INFO_HTML,
                             is_search=False)
    assert books == []


def test_empty_list_falls_back_to_info_when_no_pattern():
    src = {"bookSourceUrl": "https://www.example.com/"}
    src["ruleBookInfo"] = {"name": "tag.h1@text", "author": "class.author@text"}
    books = _parse_book_list(src, RULES,
                             "https://www.example.com/books/100/", INFO_HTML,
                             is_search=True)
    assert len(books) == 1
    assert books[0]["name"] == "没钱修什么仙？"
