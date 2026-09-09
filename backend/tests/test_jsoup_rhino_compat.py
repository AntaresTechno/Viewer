"""Regression coverage for sources that use Rhino's ``org.jsoup.Jsoup``."""
from __future__ import annotations

import json

import pytest

from app.legado_rule.analyze_rule import AnalyzeRule
from app.legado_rule import js_bridge as jb

if jb.detect_engine() is None:
    pytest.skip("需要 QuickJS", allow_module_level=True)


def test_org_jsoup_is_available_with_common_element_methods():
    ev = jb.JsEvaluator({})
    out = ev.eval("""
        var page = org.jsoup.Jsoup.parse(
          '<ul class="volume-chapters"><li class="chapter-li chapter-bar">卷一</li>' +
          '<li class="chapter-li"><a href="/c1"> 第一章 </a></li></ul>'
        );
        var items = page.select('ul.volume-chapters li.chapter-li:not(.volume-cover)');
        var link = items.get(1).select('a').first();
        JSON.stringify({
          size: items.size(), volume: items.get(0).hasClass('chapter-bar'),
          title: link.text(), href: link.attr('href')
        });
    """)
    assert json.loads(out) == {
        "size": 2, "volume": True, "title": "第一章", "href": "/c1",
    }


def test_jsoup_metadata_selector_matches_bilinovel_search_rule():
    ev = jb.JsEvaluator({"result": '<meta property="og:novel:book_name" content="测试书">'})
    assert ev.eval(
        "org.jsoup.Jsoup.parse(result).select('meta[property=og:novel:book_name]').first().attr('content')"
    ) == "测试书"


def test_jsoup_result_fragment_can_replace_analyze_rule_content():
    source = {"bookSourceUrl": "https://www.bilinovel.net"}
    rule = """
        var doc = org.jsoup.Jsoup.parse(result);
        var name = doc.select('meta[property=og:novel:book_name]').first().attr('content');
        java.setContent('<li class="book-li"><h4 class="book-title">' + name + '</h4></li>');
        java.getElement('li.book-li');
    """
    analyzer = AnalyzeRule(source=source)
    analyzer.set_content('<meta property="og:novel:book_name" content="测试书">')
    items = analyzer.get_element("@js:" + rule)
    assert items and "测试书" in str(items[0])
