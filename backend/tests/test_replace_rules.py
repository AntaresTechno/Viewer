"""Tests for 净化/替换规则 scope matching, application and legado import."""
from __future__ import annotations

import asyncio
import json

from app.models import ReplaceRule
from app.services.replace_rules import (
    apply_rules,
    parse_legado_import,
    scope_allows,
)


def _rule(**kw) -> ReplaceRule:
    base = dict(
        id=kw.pop("id", 1), name="r", group="", group_order=0, order=0,
        is_active=True, pattern="广告", replacement="", scope="",
        regex=False, case_sensitive=True,
    )
    base.update(kw)
    return ReplaceRule(**base)


class TestScopeAllows:
    def test_empty_scope_matches_all(self):
        assert scope_allows("", book_name="任意", source_url="http://x",
                            source_name="源")

    def test_include_substring(self):
        s = "斗破苍穹||wcshuba.com"
        assert scope_allows(s, book_name="从斗破苍穹签到开始",
                            source_url="https://www.wcshuba.com/b", source_name="")
        assert not scope_allows(s, book_name="遮天",
                                source_url="https://other.com", source_name="")

    def test_exclude_wins(self):
        s = "wcshuba.com\n-都市"
        assert scope_allows(s, book_name="玄幻书",
                            source_url="https://www.wcshuba.com", source_name="")
        assert not scope_allows(s, book_name="都市之巅",
                                source_url="https://www.wcshuba.com", source_name="")


class TestApplyRules:
    def test_plain_replace(self):
        rules = [_rule(pattern="广告内容", replacement="")]
        out, applied = asyncio.run(apply_rules("前广告内容后", rules))
        assert out == "前后"
        assert applied == ["r"]

    def test_plain_replace_case_insensitive(self):
        rules = [_rule(pattern="Ad", replacement="x", case_sensitive=False)]
        out, _ = asyncio.run(apply_rules("adAD", rules))
        assert out == "xx"

    def test_regex_with_group_ref(self):
        rules = [_rule(pattern=r"第(\d+)章", replacement=r"Chapter \1", regex=True)]
        out, _ = asyncio.run(apply_rules("第12章 开端", rules))
        assert out == "Chapter 12 开端"

    def test_inactive_and_blank_pattern_skipped(self):
        rules = [
            _rule(id=1, is_active=False, pattern="a", replacement="b"),
            _rule(id=2, pattern="  ", replacement="b"),
        ]
        out, applied = asyncio.run(apply_rules("aaa", rules))
        assert out == "aaa" and applied == []

    def test_scope_mismatch_skipped(self):
        rules = [_rule(scope="-本书", pattern="a", replacement="b")]
        out, applied = asyncio.run(
            apply_rules("aaa", rules, book_name="本书", source_url="")
        )
        assert out == "aaa" and applied == []

    def test_bad_regex_skipped_not_fatal(self):
        rules = [_rule(pattern="([unclosed", replacement="x", regex=True)]
        out, applied = asyncio.run(apply_rules("text", rules))
        assert out == "text" and applied == []

    def test_ordering_by_group_then_order(self):
        # group_order=0 的规则先执行：B->C 先于 A->B
        rules = [
            _rule(id=1, group_order=1, order=0, pattern="A", replacement="B"),
            _rule(id=2, group_order=0, order=1, pattern="B", replacement="C"),
        ]
        out, _ = asyncio.run(apply_rules("AB", rules))
        assert out == "BC"


class TestParseLegadoImport:
    def test_legado_shape(self):
        payload = [{
            "name": "去广告", "group": "基础", "groupOrder": 1, "order": 2,
            "isActive": True, "pattern": "广告.*?结束", "replacement": "",
            "scope": "", "regex": True,
        }]
        norm = parse_legado_import(payload)
        assert len(norm) == 1
        assert norm[0]["group_order"] == 1
        assert norm[0]["is_active"] is True
        assert norm[0]["regex"] is True

    def test_single_object_and_missing_pattern(self):
        # dict without pattern -> dropped
        assert parse_legado_import({"name": "x"}) == []
        # dict with pattern -> kept as single rule
        norm = parse_legado_import({"pattern": "x", "replacement": "y"})
        assert len(norm) == 1
        assert norm[0]["replacement"] == "y"

    def test_invalid_types(self):
        assert parse_legado_import("not a rule") == []
        assert parse_legado_import([42, None]) == []


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
