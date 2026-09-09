from __future__ import annotations

import pytest

from app.legado_rule.exceptions import FetchError
from app.plugins.media.adapters import rss as rss_adapter


SOURCE = {
    "sourceUrl": "short-drama",
    "sourceName": "短剧源",
    "mediaKind": "video",
    "sortUrl": (
        "热剧::https://feed.example.test/hot\n"
        "搜索::{{host}}/search?query="
        "{{java.getVerificationCode(source.sourceIcon)}}&tab_type=11"
    ),
    "jsLib": 'let host = "https://search.example.test"',
    "ruleArticles": "$.data[*]",
    "ruleTitle": "$.title",
}


def test_interactive_search_sort_is_not_rendered_as_category():
    adapter = rss_adapter.RssMediaAdapter(dict(SOURCE))
    assert [(sort.name, sort.url) for sort in adapter.list_sorts()] == [
        ("热剧", "https://feed.example.test/hot")
    ]


@pytest.mark.asyncio
async def test_interactive_search_prompt_uses_media_search_keyword(monkeypatch):
    captured = {}

    async def fake_fetch(source, sort_name, sort_url, page, search_key):
        captured.update(
            url=sort_url, page=page, search_key=search_key, sort_name=sort_name
        )
        return [], None

    monkeypatch.setattr(rss_adapter, "fetch_articles", fake_fetch)
    adapter = rss_adapter.RssMediaAdapter(dict(SOURCE))
    result = await adapter.search_items("逆袭", 2)

    assert result.warning == ""
    assert captured == {
        "url": "{{host}}/search?query={{key}}&tab_type=11",
        "page": 2,
        "search_key": "逆袭",
        "sort_name": "搜索",
    }


@pytest.mark.asyncio
async def test_rule_based_feed_reports_empty_http_body(monkeypatch):
    from app.legado_rule import rss_source

    class EmptyResponse:
        error = None
        ok = True
        status = 200
        body = ""
        url = "https://feed.example.test/hot"

    async def fake_fetch(_analyze_url):
        return EmptyResponse()

    monkeypatch.setattr(rss_source, "fetch_str", fake_fetch)
    with pytest.raises(FetchError, match="接口返回空内容"):
        await rss_source.fetch_articles(
            SOURCE, "热剧", "https://feed.example.test/hot", 1, None
        )
