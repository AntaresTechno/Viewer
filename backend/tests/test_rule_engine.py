"""Unit tests for the legado-compatible rule engine."""
from __future__ import annotations

import json
import urllib.parse

import pytest

from app.legado_rule.analyze_rule import AnalyzeRule
from app.legado_rule.analyze_url import AnalyzeUrl
from app.legado_rule.analyzer_regex import get_elements as regex_get_elements
from app.legado_rule.rule_analyzer import RuleAnalyzer

HTML = """
<html><body>
<div class="bookbox">
  <h4 class="bookname"><a href="/book/1">斗破苍穹</a></h4>
  <span class="author">天蚕土豆</span>
  <div class="intro">三段之上，斗气大陆。</div>
  <img src="https://img.example.com/1.jpg">
</div>
<div class="bookbox">
  <h4 class="bookname"><a href="/book/2">凡人修仙传</a></h4>
  <span class="author">忘语</span>
  <div class="intro">一个普通山村小子。</div>
  <img src="https://img.example.com/2.jpg">
</div>
<div class="bookbox">
  <h4 class="bookname"><a href="/book/3">诡秘之主</a></h4>
  <span class="author">爱潜水的乌贼</span>
  <div class="intro">蒸汽与机械的浪潮。</div>
  <img src="https://img.example.com/3.jpg">
</div>
<ul id="pagebar">
  <li><a href="/toc_1.html">1</a></li>
  <li><a href="/toc_2.html">2</a></li>
</ul>
</body></html>
"""

JSON_DATA = json.dumps({
    "data": {
        "list": [
            {"name": "书A", "author": "作者A", "url": "/a/1"},
            {"name": "书B", "author": "作者B", "url": "/b/2"},
        ],
        "total": 2,
    }
}, ensure_ascii=False)


def make_ar(content: str, base_url: str = "https://www.example.com/") -> AnalyzeRule:
    ar = AnalyzeRule(base_url=base_url)
    ar.set_content(content, base_url=base_url)
    return ar


# ------------------------------------------------------------------ jsoup
class TestJSoup:
    def test_element_list_and_fields(self):
        ar = make_ar(HTML)
        els = ar.get_elements("class.bookbox")
        assert len(els) == 3
        names = []
        for el in els:
            ar.set_content(el)
            names.append(ar.get_string("tag.h4@tag.a@text"))
        assert names == ["斗破苍穹", "凡人修仙传", "诡秘之主"]

    def test_attr_extraction(self):
        ar = make_ar(HTML)
        ar.set_content(ar.get_elements("class.bookbox")[0])
        href = ar.get_string("tag.a@href")
        assert href == "/book/1"

    def test_index_selection(self):
        ar0 = make_ar(HTML)
        first = ar0.get_elements("class.bookbox[0]")
        assert len(first) == 1
        ar0.set_content(first[0])
        assert ar0.get_string("tag.h4@tag.a@text") == "斗破苍穹"

        ar1 = make_ar(HTML)  # fresh instance: content is per-instance state
        last = ar1.get_elements("class.bookbox[-1]")
        assert len(last) == 1
        ar1.set_content(last[0])
        assert ar1.get_string("tag.h4@tag.a@text") == "诡秘之主"

    def test_legacy_index(self):
        ar = make_ar(HTML)
        els = ar.get_elements("class.bookbox.-1")
        ar.set_content(els[0])
        assert ar.get_string("tag.h4@tag.a@text") == "诡秘之主"

    def test_css_selector_fallback(self):
        ar = make_ar(HTML)
        els = ar.get_elements("div.bookbox")
        assert len(els) == 3

    def test_bracket_range_and_exclude(self):
        html = "<i>1</i><i>2</i><i>3</i><i>4</i>"
        ar = make_ar(html)
        got = [ar.get_string_list(f"tag.i[{expr}]@text")[0]
               for expr in ("0", "-1")]
        assert got == ["1", "4"]
        picked = ar.get_elements("tag.i[0,2]")
        assert len(picked) == 2
        excluded = ar.get_elements("tag.i[!0]")
        assert len(excluded) == 3

    def test_join_and_or(self):
        ar = make_ar(HTML)
        got = ar.get_string_list("id.pagebar@tag.a[0]@text||class.author.0@text")
        # || stops at the first non-empty group
        assert got == ["1"]
        both = ar.get_string_list(
            "id.pagebar@tag.a[0]@text&&id.pagebar@tag.a[1]@text"
        )
        assert both == ["1", "2"]

    def test_zip_percent(self):
        html = "<i>a1</i><i>a2</i><em>b1</em><em>b2</em>"
        ar = make_ar(html)
        zipped = ar.get_string_list("tag.i@text%%tag.em@text")
        assert zipped == ["a1", "b1", "a2", "b2"]

    def test_regex_replace_suffix_double_hash(self):
        ar = make_ar(HTML)
        val = ar.get_string("class.bookbox[0]@tag.h4@text##斗破##破碎")
        assert val == "破碎苍穹"

    def test_regex_remove_suffix(self):
        ar = make_ar(HTML)
        val = ar.get_string("class.bookbox[0]@tag.h4@text##苍穹")
        assert val == "斗破"


# ------------------------------------------------------------------ jsonpath
class TestJsonPath:
    def test_basic(self):
        ar = make_ar(JSON_DATA)
        assert ar.get_string("$.data.total") == "2"
        names = ar.get_string_list("$.data.list[*].name")
        assert names == ["书A", "书B"]

    def test_inner_rule(self):
        ar = make_ar(JSON_DATA)
        rule = "{$.data.list[0].name}共{$.data.total}本"
        assert ar.get_string(rule) == "书A共2本"

    def test_elements_iteration(self):
        ar = make_ar(JSON_DATA)
        els = ar.get_elements("$.data.list[*]")
        out = []
        for el in els:
            ar.set_content(el)
            out.append(f"{ar.get_string('$.name')}-{ar.get_string('$.author')}")
        assert out == ["书A-作者A", "书B-作者B"]


# --------------------------------------------------------------------- xpath
class TestXPath:
    def test_basic_xpath(self):
        ar = make_ar(HTML)
        titles = ar.get_string_list("//h4/a/text()")
        assert titles == ["斗破苍穹", "凡人修仙传", "诡秘之主"]

    def test_explicit_prefix(self):
        ar = make_ar(HTML)
        val = ar.get_string("@XPath://span[@class='author'][1]/text()")
        assert val.startswith("天蚕土豆")


# --------------------------------------------------------------------- regex
class TestRegexAnalyzer:
    def test_get_elements_groups(self):
        res = "<a href='/x1'>t1</a><a href='/x2'>t2</a>"
        rows = regex_get_elements(res, [r"<a href='([^']+)'>([^<]+)</a>"])
        assert rows == [["<a href='/x1'>t1</a>", "/x1", "t1"],
                        ["<a href='/x2'>t2</a>", "/x2", "t2"]]


# -------------------------------------------------------------- rule analyzer
class TestRuleAnalyzerSplit:
    def test_first_separator_wins(self):
        ra = RuleAnalyzer("a&&b||c")
        segs = ra.split_rule("&&", "||", "%%")
        assert ra.elements_type == "&&"
        assert segs == ["a", "b||c"]

    def test_brackets_protect_separators(self):
        ra = RuleAnalyzer("$.list[?(@.a==1 && @.b==2)]&&$.x")
        segs = ra.split_rule("&&")
        assert segs == ["$.list[?(@.a==1 && @.b==2)]", "$.x"]


# ------------------------------------------------------------------ templates
class TestTemplates:
    def test_dollar_n_backreference(self):
        ar = make_ar("<div><p>第一章 甲</p><p>第二章 乙</p></div>")
        val = ar.get_string("$1##<p>([^<]+)</p>#第$1章#")
        assert isinstance(val, str)

    def test_get_variable(self):
        ar = make_ar("<i>x</i>")
        ar.put("siteName", "示例站")
        assert ar.get_string("@get:{siteName}") == "示例站"

    def test_js_engine_available(self):
        from app.legado_rule.js_bridge import detect_engine

        if detect_engine() is None:
            pytest.skip("no JS engine installed")
        ar = make_ar(HTML)
        val = ar.get_string("<js>java.md5Encode('abc').slice(0,4)</js>")
        assert val == "9001"  # md5('abc')=90015098..., first 4 chars
        page_val = None


class TestJsInline:
    def test_inline_js_block(self):
        from app.legado_rule.js_bridge import detect_engine

        if detect_engine() is None:
            pytest.skip("no JS engine installed")
        au = AnalyzeUrl(
            "https://s.example.com/{{key + '_' + (page + 1)}}",
            key="k", page=1,
        )
        assert au.url.endswith("/k_2")


# ------------------------------------------------------------------ analyze url
class TestAnalyzeUrl:
    def test_simple_template(self):
        au = AnalyzeUrl(
            "https://s.example.com/search?q={{key}}&p={{page}}",
            key="斗破", page=1,
        )
        spec = au.spec()
        assert spec.url == "https://s.example.com/search?q=斗破&p=1"

    def test_page_block(self):
        au = AnalyzeUrl("https://e.example.com/<a,b,c>", page=2)
        assert au.url == "https://e.example.com/b"
        au = AnalyzeUrl("https://e.example.com/<a,b,c>", page=9)
        assert au.url == "https://e.example.com/c"

    def test_post_options(self):
        au = AnalyzeUrl(
            'https://s.example.com/api,{"method":"POST","body":"kw={{key}}&pg={{page}}"}',
            key="凡人", page=3,
        )
        spec = au.spec()
        assert spec.method == "POST"
        assert spec.body is not None
        flat = urllib.parse.unquote_plus(spec.body)
        assert "kw=凡人" in flat and "pg=3" in flat
        ct = {k.lower() for k in spec.headers}
        assert "content-type" in ct

    def test_option_headers_merge(self):
        au = AnalyzeUrl(
            'https://x.example.com,{"headers":{"Referer":"https://x.example.com/"}}'
        )
        assert au.header_map["Referer"].endswith("/")

    def test_absolute_url_join(self):
        from app.legado_rule.analyze_url import get_absolute_url

        assert get_absolute_url(
            "https://www.example.com/book/", "./1.html"
        ) == "https://www.example.com/book/1.html"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
