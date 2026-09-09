"""Media kind detection tests (plan §6.2/§15.1)."""
from __future__ import annotations

from app.plugins.media import schemas


def test_fqdj_style_rss_detects_video_with_warning():
    # fqdj0512.json 的形态：rss 方言、无 type、ruleContent 含 <video>
    source = {
        "sourceUrl": "番茄短剧",
        "sourceName": "番茄短剧",
        "articleStyle": 2,
        "sortUrl": "热剧::https://x/catalog",
        "ruleArticles": "$.data",
        "ruleContent": "<js>..</js>\n<!doctype html>...<video ...></video>...",
    }
    kind, warnings = schemas.detect_media_kind(source)
    assert kind == "video"
    assert any("type" in w and "识别为" in w and "视频" in w for w in warnings)


def test_article_style_2_does_not_imply_video():
    # articleStyle 只表示双列布局，不应误判为视频
    source = {
        "sourceUrl": "图文源",
        "articleStyle": 2,
        "ruleImage": "$.cover",
        "ruleContent": "<p>纯图文</p>",
    }
    kind, warnings = schemas.detect_media_kind(source)
    # 无 <video>/<audio>，ruleImage 存在 → 启发式判为 comic
    assert kind == "comic"


def test_rss_type_explicit_numbers():
    assert schemas.detect_media_kind({"sourceUrl": "x", "type": 1})[0] == "comic"
    assert schemas.detect_media_kind({"sourceUrl": "x", "type": 2})[0] == "video"
    assert schemas.detect_media_kind({"sourceUrl": "x", "type": 3})[0] == "audio"
    assert schemas.detect_media_kind({"sourceUrl": "x", "type": "0"})[0] is None
    assert schemas.detect_media_kind({"sourceUrl": "x", "type": "2"})[0] == "video"


def test_book_source_type_mapping():
    assert schemas.detect_media_kind({"bookSourceUrl": "y", "bookSourceType": 1})[0] == "audio"
    assert schemas.detect_media_kind({"bookSourceUrl": "y", "bookSourceType": 2})[0] == "comic"
    assert schemas.detect_media_kind({"bookSourceUrl": "y", "bookSourceType": 3})[0] == "video"
    assert schemas.detect_media_kind({"bookSourceUrl": "y", "bookSourceType": "0"})[0] is None
    assert schemas.detect_media_kind({"bookSourceUrl": "y", "bookSourceType": "2"})[0] == "comic"


def test_static_media_type_wins_over_heuristic():
    source = {"sourceUrl": "x", "mediaType": "audio", "ruleContent": "<video>"}
    assert schemas.detect_media_kind(source)[0] == "audio"


def test_explicit_kind_from_admin_takes_top_priority():
    source = {"sourceUrl": "x", "type": 2}
    kind, _ = schemas.detect_media_kind(source, explicit="comic")
    assert kind == "comic"


def test_unknown_needs_kind_on_dead_source():
    # 完全无法判定的源 → None（needsKind=true）
    kind, _ = schemas.detect_media_kind({"sourceUrl": "x"})
    assert kind is None


def test_source_format_and_key():
    assert schemas.source_format({"bookSourceUrl": "a"}) == "book"
    assert schemas.source_format({"sourceUrl": "a"}) == "rss"
    assert schemas.source_key({"sourceUrl": "u", "bookSourceUrl": "b"}) == "b"
