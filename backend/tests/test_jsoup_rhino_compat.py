"""Regression coverage for sources that use Rhino's ``org.jsoup.Jsoup``."""
from __future__ import annotations

import json

import pytest

from app.legado_rule.analyze_rule import AnalyzeRule
from app.legado_rule import js_bridge as jb
from app.legado_rule.web_book import _parse_book_list

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


def test_lxml_result_keeps_jsoup_element_api_for_bilinovel_fields():
    analyzer = AnalyzeRule(source={"bookSourceUrl": "https://www.bilinovel.net"})
    analyzer.set_content("""
        <li class="book-li">
          <p class="book-desc">作品简介</p>
          <span class="book-author">作者测试作者</span>
          <span class="tag-small-group"><em class="tag-small">校园</em></span>
        </li>
    """)
    item = analyzer.get_elements("li.book-li")[0]
    analyzer.set_content(item)
    assert analyzer.get_string(
        "@js:var t=result.select('span.book-author').text();"
        "var i=t.indexOf('作者');i>=0?t.substring(i+2):t;"
    ) == "测试作者"
    assert analyzer.get_string(
        "@js:var d=result.select('p.book-desc, p.book-intro');"
        "d.size()>0?d.first().text():'';"
    ) == "作品简介"


def test_lxml_result_keeps_jsoup_element_api_for_fushuw_author():
    analyzer = AnalyzeRule(source={"bookSourceUrl": "https://m.fushuw.org"})
    analyzer.set_content(
        '<li class="book-li"><span>分类</span><span>作者：福书作者</span></li>'
    )
    item = analyzer.get_elements("li.book-li")[0]
    analyzer.set_content(item)
    assert analyzer.get_string(
        "@js:var s=result.select('span').get(1).text();"
        "var i=s.indexOf('作者：');i>=0?s.substring(i+3):s;"
    ) == "福书作者"


def test_bilinovel_explore_list_is_not_silently_dropped():
    source = {
        "bookSourceUrl": "https://www.bilinovel.net",
        "bookSourceName": "哔哩轻小说",
    }
    rules = {
        "bookList": "ol.book-ol.book-ol-normal li.book-li||ol.jsBooks li.book-li",
        "name": "a h4.book-title@text",
        "author": (
            "@js:var t=result.select('span.book-author').text();"
            "var i=t.indexOf('作者');i>=0?t.substring(i+2):t;"
        ),
        "intro": (
            "@js:var d=result.select('p.book-desc, p.book-intro');"
            "d.size()>0?d.first().text():'';"
        ),
        "bookUrl": "a@href",
    }
    body = """
      <ol class="book-ol book-ol-normal"><li class="book-li">
        <a href="/novel/1.html"><h4 class="book-title">哔哩测试书</h4></a>
        <p class="book-desc">哔哩简介</p><span class="book-author">作者哔哩作者</span>
      </li></ol>
    """
    books = _parse_book_list(
        source, rules, "https://www.bilinovel.net/wenku/x.html", body
    )
    assert [(book["name"], book["author"]) for book in books] == [
        ("哔哩测试书", "哔哩作者")
    ]


def test_fushuw_explore_list_is_not_silently_dropped():
    source = {
        "bookSourceUrl": "https://m.fushuw.org",
        "bookSourceName": "福书网",
    }
    rules = {
        "bookList": "li.book-li",
        "name": "h4.book-title@text",
        "author": (
            "@js:var s=result.select('span').get(1).text();"
            "var i=s.indexOf('作者：');i>=0?s.substring(i+3):s;"
        ),
        "bookUrl": "a@href",
    }
    body = """
      <ul><li class="book-li"><a href="/book/2.html">
        <h4 class="book-title">福书测试书</h4>
        <span>分类：古代</span><span>作者：福书作者</span>
      </a></li></ul>
    """
    books = _parse_book_list(
        source, rules, "https://m.fushuw.org/gudaijiakong/", body
    )
    assert [(book["name"], book["author"]) for book in books] == [
        ("福书测试书", "福书作者")
    ]
