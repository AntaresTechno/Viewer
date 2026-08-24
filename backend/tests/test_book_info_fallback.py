"""Tests for the generic intro fallback (meta og:description) in web_book."""
from __future__ import annotations

from app.legado_rule.web_book import _fallback_intro  # noqa: SLF001

HTML = """<html><head>
<meta property="og:type" content="novel"/>
<meta property="og:title" content="测试书"/>
<meta property="og:description" content="测试书简介： 少年穿越异界，踏上巅峰之路。"/>
<meta name="description" content="测试书最新章节无错更新，XX书吧提供全文免费在线阅读。"/>
</head><body></body></html>"""

HTML_NO_OG = HTML.replace('property="og:description" content="测试书简介： 少年穿越异界，踏上巅峰之路。"/>', "")


def test_fallback_intro_prefers_og_and_strips_prefix():
    intro = _fallback_intro(HTML, "测试书")
    assert intro == "少年穿越异界，踏上巅峰之路。"


def test_fallback_intro_rejects_promo_description():
    # 没有 og:description 时，纯推广句的 description 不算简介
    assert _fallback_intro(HTML_NO_OG, "测试书") == ""


def test_fallback_intro_empty_when_no_meta():
    assert _fallback_intro("<html><head></head></html>", "x") == ""


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
