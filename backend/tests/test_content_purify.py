"""Tests for 正文净化管线：规则指纹、净化执行与 process_chapter 缓存流程。

DB 场景与 test_toc_queue 相同约束：aiosqlite + StaticPool 绑定创建循环，
所有步骤跑在同一个 asyncio loop 里。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models
from app.models import PurifiedContent, PurifyPack, PurifyRule


def _rule(pack_id: int = 1, **kw) -> PurifyRule:
    base = dict(
        id=kw.pop("id", 1), pack_id=pack_id, name="r", order=0,
        is_active=True, pattern="广告", replacement="", scope="",
        regex=False, case_sensitive=True,
        scope_content=True, scope_title=False,
    )
    base.update(kw)
    return PurifyRule(**base)


class TestFingerprint:
    def test_stable_for_same_rules(self):
        from app.services.content_purify import rules_fingerprint

        rules = [_rule(id=1, pattern="a"), _rule(id=2, pattern="b")]
        assert rules_fingerprint(rules) == rules_fingerprint(rules)

    def test_changes_with_pattern(self):
        from app.services.content_purify import rules_fingerprint

        a = rules_fingerprint([_rule(pattern="a")])
        b = rules_fingerprint([_rule(pattern="b")])
        assert a != b

    def test_changes_with_order(self):
        from app.services.content_purify import rules_fingerprint

        a = rules_fingerprint([_rule(id=1), _rule(id=2)])
        b = rules_fingerprint([_rule(id=2), _rule(id=1)])
        assert a != b

    def test_empty_rules(self):
        from app.services.content_purify import rules_fingerprint

        assert len(rules_fingerprint([])) == 32


class TestPurifyText:
    def _run(self, text, rules, **kw):
        from app.services.content_purify import purify_text

        return asyncio.run(purify_text(text, rules, **kw))

    def test_plain_replace(self):
        out, applied = self._run("前广告内容后", [_rule(pattern="广告内容")])
        assert out == "前后"
        assert applied == ["r"]

    def test_regex_chain_ordering(self):
        # order=1 的规则先执行：B->C 先于 A->B
        rules = [
            _rule(id=1, order=2, pattern="A", replacement="B"),
            _rule(id=2, order=1, pattern="B", replacement="C"),
        ]
        out, applied = self._run("AB", rules)
        assert out == "BC"
        assert len(applied) == 2

    def test_scope_skip(self):
        out, applied = self._run(
            "aaa", [_rule(scope="-本书", pattern="a", replacement="b")],
            book_name="本书",
        )
        assert out == "aaa" and applied == []

    def test_case_insensitive_plain(self):
        out, _ = self._run(
            "adAD", [_rule(pattern="Ad", replacement="x", case_sensitive=False)]
        )
        assert out == "xx"

    def test_bad_regex_skipped(self):
        out, applied = self._run(
            "text", [_rule(pattern="([unclosed", replacement="x", regex=True)]
        )
        assert out == "text" and applied == []


class TestPresets:
    def test_wuyun_pack_loads(self):
        from app.plugins.content_purify.presets import (
            BUILTIN_SOURCES,
            load_wuyun_rules,
            preset_by_key,
        )

        rules = load_wuyun_rules()
        assert len(rules) == 20
        assert all(r.get("pattern") for r in rules)
        groups = {r.get("group") for r in rules}
        assert groups == {"格式", "净化", "可选"}
        keys = [s["key"] for s in BUILTIN_SOURCES]
        assert keys == ["builtin-md3", "wuyun"]
        assert preset_by_key("wuyun")["installable"] is True
        assert preset_by_key("builtin-md3")["installable"] is False

    def test_parse_rule_payload_new_format(self):
        from app.services.content_purify import parse_rule_payload

        norm = parse_rule_payload([{
            "name": "#05 标点", "group": "格式", "order": 5,
            "isEnabled": True, "isRegex": True,
            "pattern": r"[\s　]+", "replacement": " ",
            "scopeContent": True, "scopeTitle": False,
        }])
        assert len(norm) == 1
        r = norm[0]
        assert r["is_active"] is True and r["regex"] is True
        assert r["scope_content"] is True and r["scope_title"] is False
        assert r["case_sensitive"] is True  # 新格式缺省

    def test_parse_rule_payload_legacy_format(self):
        from app.services.content_purify import parse_rule_payload

        norm = parse_rule_payload({
            "name": "去广告", "isActive": False, "regex": False,
            "caseSensitive": False, "pattern": "广告", "replacement": "",
        })
        assert len(norm) == 1
        r = norm[0]
        assert r["is_active"] is False and r["regex"] is False
        assert r["case_sensitive"] is False

    def test_parse_drops_blank_patterns(self):
        from app.services.content_purify import parse_rule_payload

        assert parse_rule_payload([{"name": "x"}, {"pattern": "  "}]) == []


class TestMd3Builtin:
    def test_entities_and_invisible_chars(self):
        from app.services.content_purify import md3_builtin_clean

        # nbsp 连串压成一个空格；ensp 同理。
        # 注意：忠实移植 legado 的 noPrintRegex（只含 thinsp/zwnj/zwj/
        # u2009/u200C/u200D），不含 u200b —— 与 MD3 版行为完全一致。
        out = md3_builtin_clean("a&nbsp;&nbsp;&nbsp;b&ensp;c\u200bd")
        assert out == "a b c\u200bd"
        out2 = md3_builtin_clean("x\u2009y\u200cz\u200dw")
        assert out2 == "xyzw"

    def test_html_stripped_with_paragraphs(self):
        from app.services.content_purify import md3_builtin_clean

        out = md3_builtin_clean("<div>第一段</div><div>第二段</div>")
        assert "第一段" in out and "第二段" in out
        assert "<div>" not in out
        # 换行后补全角缩进
        assert "\n　　第二段" in out.replace("\r", "")

    def test_comment_removed_img_kept(self):
        from app.services.content_purify import md3_builtin_clean

        out = md3_builtin_clean('<!--ad-->前<img src="x.png">后<span>x</span>')
        assert "ad" not in out and "<span>" not in out
        assert '<img src="x.png">' in out

    def test_empty(self):
        from app.services.content_purify import md3_builtin_clean

        assert md3_builtin_clean("") == ""


class TestJsReplacement:
    def test_js_replacement_executed(self):
        """@js: 替换经 JS 引擎执行（dukpy/quickjs 任一可用）。"""
        from app.legado_rule.js_bridge import detect_engine
        from app.services.content_purify import purify_text

        if detect_engine() is None:
            pytest.skip("无 JS 引擎")

        rule = _rule(
            name="大写化", pattern=r"[a-z]+", replacement="@js:result.toUpperCase()",
            regex=True,
        )
        out, applied = asyncio.run(purify_text("abc END", [rule]))
        assert out == "ABC END"
        assert applied == ["大写化"]

    def test_js_replacement_without_engine_skipped(self, monkeypatch):
        import app.services.content_purify as cp

        monkeypatch.setattr(cp, "detect_engine", lambda: None)
        rule = _rule(pattern=r"a", replacement="@js:result", regex=True)
        out, applied = asyncio.run(cp.purify_text("aaa", [rule]))
        assert out == "aaa" and applied == []

    def test_scope_content_false_skipped(self):
        from app.services.content_purify import purify_text

        rule = _rule(pattern="广告", replacement="", scope_content=False)
        out, applied = asyncio.run(purify_text("有广告", [rule]))
        assert out == "有广告" and applied == []


@pytest.fixture()
def db_env(monkeypatch):
    """Patch core.db singletons to a fresh temp in-memory sqlite."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.core import db as core_db

    monkeypatch.setattr(core_db.db, "engine", engine, raising=False)
    monkeypatch.setattr(core_db.db, "session_factory", factory, raising=False)

    async def make_tables():
        async with core_db.get_engine().begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)

    return {"factory": factory, "make_tables": make_tables}


def test_process_chapter_pipeline(db_env):
    """获取 → 净化 → 存库 → 调用 的完整流程。"""
    from app.services.content_purify import (
        active_rules,
        process_chapter,
        rules_fingerprint,
    )

    factory = db_env["factory"]
    calls = {"n": 0}

    async def fetch_raw() -> str:
        calls["n"] += 1
        return "正文开始。天才一秒记住本站地址 www.ad.com 正文结束。"

    async def full():
        await db_env["make_tables"]()
        async with factory() as s:
            pack = PurifyPack(name="测试包", origin="manual", enabled=True)
            s.add(pack)
            await s.flush()
            s.add(PurifyRule(
                pack_id=pack.id, name="去天才一秒", order=0,
                pattern=r"天才一秒记住\S*", replacement="", regex=True,
            ))
            await s.commit()

        url = "http://s.example/c1"
        results = []

        # 1) 首次读取：回源 + 净化 + 入库
        async with factory() as s:
            results.append(await process_chapter(
                s, source_url="http://s.example", url=url,
                book_url="http://s.example/b1", title="第一章",
                fetch_raw=fetch_raw,
            ))

        # 2) 二次读取：直接调用缓存，不再回源
        async with factory() as s:
            results.append(await process_chapter(
                s, source_url="http://s.example", url=url,
                fetch_raw=fetch_raw,
            ))

        # 3) 规则变化：用原文本地重新净化（仍不回源）
        async with factory() as s:
            s.add(PurifyRule(
                pack_id=1, name="去裸域名", order=1,
                pattern=r"(?:https?://)?www\.\S+com", replacement="", regex=True,
            ))
            await s.commit()
            results.append(await process_chapter(
                s, source_url="http://s.example", url=url,
                fetch_raw=fetch_raw,
            ))

        # 4) 抓取失败：兜底返回旧净化结果
        async def broken() -> str:
            raise RuntimeError("network down")

        async with factory() as s:
            results.append(await process_chapter(
                s, source_url="http://s.example", url=url,
                fetch_raw=broken,
            ))

        rows = []
        async with factory() as s:
            rows = list((await s.execute(
                select(PurifiedContent)
            )).scalars().all())
            rules_now = await active_rules(s)
        fp = rules_fingerprint(rules_now)
        return results, rows, fp

    results, rows, fp = asyncio.run(full())

    t1, c1 = results[0]
    assert c1 is False and calls["n"] == 1
    assert "天才一秒记住" not in t1

    t2, c2 = results[1]
    assert c2 is True and calls["n"] == 1  # 命中缓存，未回源
    assert t2 == t1

    t3, c3 = results[2]
    assert c3 is True and calls["n"] == 1  # 规则变化本地重净化，仍未回源
    assert "www.ad.com" not in t3 and "天才一秒记住" not in t3

    t4, c4 = results[3]
    assert c4 is True and t4 == t3  # 断网兜底旧结果

    assert len(rows) == 1  # 一章一条缓存
    assert rows[0].fingerprint == fp
    assert set(rows[0].applied) == {"去天才一秒", "去裸域名"}
    # raw 保留原始正文（含广告），content 是净化后的
    assert "天才一秒记住" in rows[0].raw
    assert "天才一秒记住" not in rows[0].content


def test_process_chapter_local_fallback(db_env):
    """无缓存且抓取失败时回退到本地书库正文。"""
    from app.services.content_purify import process_chapter

    factory = db_env["factory"]

    async def broken() -> str:
        raise RuntimeError("down")

    async def scenario() -> tuple[str, bool]:
        await db_env["make_tables"]()
        async with factory() as s:
            s.add(models.BookChapterContent(
                source_url="http://s.example", url="c1",
                title="t", content="本地书库正文",
            ))
            await s.commit()
        async with factory() as s:
            return await process_chapter(
                s, source_url="http://s.example", url="c1",
                fetch_raw=broken,
                local_fallback=lambda: _local(s),
            )

    async def _local(s):
        return "本地书库正文"

    text, cached = asyncio.run(scenario())
    assert text == "本地书库正文"
    assert cached is True


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
