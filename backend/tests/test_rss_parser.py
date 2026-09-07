"""Legado subscription parser compatibility tests (offline)."""
from app.plugins.rss.parser import _parse_by_rule, parse_default_feed, parse_sorts, safe_html


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:media="http://search.yahoo.com/mrss/">
  <channel><title>Example</title><item>
    <title>第一篇</title><link>/posts/1</link>
    <pubDate>Sun, 06 Sep 2026 10:00:00 GMT</pubDate>
    <description><![CDATA[<p>摘要 <img src="/summary.jpg"></p>]]></description>
    <content:encoded><![CDATA[<p>正文</p>]]></content:encoded>
  </item></channel>
</rss>"""


ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom sample</title>
  <entry><title>Atom 文章</title><link href="/atom/1"/>
    <updated>2026-09-07T08:00:00Z</updated>
    <summary type="html">Atom summary</summary>
  </entry>
</feed>"""


def test_parse_rss_default_fields_and_absolute_urls():
    rows = parse_default_feed("技术", RSS, "https://example.test/feed", "https://example.test/rss.xml")
    assert len(rows) == 1
    assert rows[0]["title"] == "第一篇"
    assert rows[0]["link"] == "https://example.test/posts/1"
    assert rows[0]["image"] == "https://example.test/summary.jpg"
    assert "正文" in rows[0]["content"]


def test_parse_atom_namespace_and_href_link():
    rows = parse_default_feed("", ATOM, "https://atom.test/feed")
    assert rows[0]["title"] == "Atom 文章"
    assert rows[0]["link"] == "https://atom.test/atom/1"
    assert rows[0]["pubDate"] == "2026-09-07T08:00:00Z"


def test_parse_legado_sort_urls_and_json_map():
    source = {
        "sourceUrl": "https://example.test/feed/",
        "sortUrl": "推荐::top.xml&&稍后::../later.xml",
    }
    assert parse_sorts(source) == [
        {"name": "推荐", "url": "https://example.test/feed/top.xml"},
        {"name": "稍后", "url": "https://example.test/later.xml"},
    ]
    source["sortUrl"] = '{"新闻":"/news.xml","博客":"/blog.xml"}'
    assert parse_sorts(source)[1] == {"name": "博客", "url": "/blog.xml"}


def test_safe_html_removes_active_content_and_unsafe_attributes():
    value = safe_html(
        '<p onclick="steal()">safe<script>alert(1)</script>'
        '<a href="javascript:alert(1)">bad</a>'
        '<img src="/ok.png" onerror="steal()"></p>',
        "https://example.test/post/1",
    )
    assert "script" not in value
    assert "onclick" not in value
    assert "javascript:" not in value
    assert "onerror" not in value
    assert 'src="https://example.test/ok.png"' in value


def test_parse_legado_custom_article_rules_and_reverse():
    source = {
        "sourceUrl": "https://rules.test/feed",
        "ruleArticles": "-class.entry",
        "ruleTitle": "tag.a@text",
        "ruleLink": "tag.a@href",
        "rulePubDate": "tag.time@text",
        "ruleDescription": "tag.p@text",
        "ruleImage": "tag.img@src",
    }
    html = """
    <div class="entry"><a href="/one">第一篇</a><time>今天</time>
      <p>摘要一</p><img src="/one.jpg"></div>
    <div class="entry"><a href="/two">第二篇</a><time>昨天</time>
      <p>摘要二</p><img src="/two.jpg"></div>
    """
    rows = _parse_by_rule(
        source, "规则分类", "https://rules.test/feed", "https://rules.test/list", html
    )
    assert [row["title"] for row in rows] == ["第二篇", "第一篇"]
    assert rows[0]["link"] == "https://rules.test/two"
    assert rows[0]["image"] == "https://rules.test/two.jpg"
    assert rows[0]["sort"] == "规则分类"
