"""Tests for the discover-page widget layer (explore_ui + explore_kinds style).

Covers the 番茄(fq0826) discover page: it is not a category list but a flex
grid of ~200 weighted buttons plus text/button/toggle/select widgets, each with
an ``action`` JS that must be evaluated server-side.

Run:  python -m pytest backend/tests/test_explore_ui.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.legado_rule import explore_ui, web_book  # noqa: E402
from app.legado_rule.explore_ui import (  # noqa: E402
    normalize_style,
    run_kind_action,
)
from app.legado_rule.js_bridge import detect_engine  # noqa: E402

needs_js = pytest.mark.skipif(
    detect_engine() is None, reason="no JS engine installed"
)


# ------------------------------------------------------------ style 归一化
class TestNormalizeStyle:
    def test_defaults_when_missing(self):
        s = normalize_style(None)
        assert s["layout_flexGrow"] == 0.0
        assert s["layout_flexBasisPercent"] == -1.0
        assert s["layout_wrapBefore"] is False
        assert s["layout_alignSelf"] == "auto"

    def test_keeps_legado_fields(self):
        s = normalize_style({"layout_flexGrow": 1,
                             "layout_flexBasisPercent": 0.45})
        assert s["layout_flexGrow"] == 1.0
        assert s["layout_flexBasisPercent"] == 0.45

    def test_drops_unknown_fields(self):
        # 书源可能塞 legado 也不认识的字段，不能透传给前端
        s = normalize_style({"layout_flexGrow": 1, "whatever": "x"})
        assert "whatever" not in s

    def test_numeric_strings_coerced(self):
        assert normalize_style({"layout_flexBasisPercent": "0.5"})[
            "layout_flexBasisPercent"] == 0.5

    def test_garbage_falls_back_to_default(self):
        assert normalize_style({"layout_flexBasisPercent": "abc"})[
            "layout_flexBasisPercent"] == -1.0


# ------------------------------------------------------- explore_kinds 结构
class TestExploreKindsWidgets:
    SRC = {"bookSourceUrl": "http://x"}

    def test_json_array_keeps_action_and_chars(self):
        src = {
            "bookSourceUrl": "http://x",
            "exploreUrl": json.dumps([
                {"title": "热门", "url": "/hot", "type": "url"},
                {"title": "分类：", "type": "select",
                 "action": "z(1)", "chars": ["女生", "男生"],
                 "default": "女生",
                 "style": {"layout_flexGrow": 1,
                           "layout_flexBasisPercent": 0.45}},
            ], ensure_ascii=False),
        }
        kinds = web_book.explore_kinds(src)
        assert kinds[0]["type"] == "url"
        sel = kinds[1]
        assert sel["type"] == "select"
        assert sel["action"] == "z(1)"
        assert sel["chars"] == ["女生", "男生"]
        assert sel["default"] == "女生"
        assert sel["style"]["layout_flexBasisPercent"] == 0.45

    def test_style_absent_when_source_omits_it(self):
        """书源没给 style 就别塞默认值。

        对齐 legado：``ExploreKind.style`` 是 nullable，只有 ``style()``
        才返回 ``FlexChildStyle.defaultStyle``。凭空给 200 个按钮各补 6 个
        默认字段既浪费带宽，也让「书源有没有指定布局」这件事不可分辨。
        缺 style 时前端由 ``flexLayout(null)`` 兜同样的默认值。
        """
        src = dict(self.SRC, exploreUrl="玄幻::http://x/1\n仙侠::http://x/2")
        for k in web_book.explore_kinds(src):
            assert "style" not in k
            # 前端兜底后仍是 legado 的默认值
            assert normalize_style(k.get("style"))["layout_flexBasisPercent"] == -1.0

    def test_style_normalized_when_present(self):
        src = dict(self.SRC, exploreUrl=json.dumps(
            [{"title": "甲", "style": {"layout_flexBasisPercent": "0.45"}}],
            ensure_ascii=False))
        style = web_book.explore_kinds(src)[0]["style"]
        assert style["layout_flexBasisPercent"] == 0.45
        assert style["layout_flexGrow"] == 0.0  # 缺省值补全

    def test_unknown_type_defaults_to_url(self):
        src = dict(self.SRC, exploreUrl=json.dumps(
            [{"title": "甲"}], ensure_ascii=False))
        assert web_book.explore_kinds(src)[0]["type"] == "url"


# ------------------------------------------------------------------ 缓存
class TestExploreKindsCache:
    def test_cache_reused_and_invalidated(self, monkeypatch):
        calls = []

        def fake_uncached(src):
            calls.append(1)
            return [{"title": "甲", "url": "/a", "type": "url",
                     "style": normalize_style(None)}]

        monkeypatch.setattr(web_book, "_explore_kinds_uncached", fake_uncached)
        web_book._KINDS_CACHE.clear()
        src = {"bookSourceUrl": "http://cached",
               "exploreUrl": "甲::http://x/a"}

        web_book.explore_kinds(src)
        web_book.explore_kinds(src)
        assert len(calls) == 1, "第二次应命中缓存（否则每次点控件都重跑签名）"

        web_book.invalidate_explore_kinds(src)
        web_book.explore_kinds(src)
        assert len(calls) == 2, "refreshExplore 后必须重跑"

    def test_empty_result_not_cached(self, monkeypatch):
        """空结果不缓存：书源临时失败后重试能拿到东西。"""
        monkeypatch.setattr(web_book, "_explore_kinds_uncached", lambda s: [])
        web_book._KINDS_CACHE.clear()
        src = {"bookSourceUrl": "http://empty", "exploreUrl": "甲::http://x/b"}
        web_book.explore_kinds(src)
        assert web_book._kinds_cache_key(src) not in web_book._KINDS_CACHE


# ------------------------------------------------------------ action 执行
# 一个不联网的 mini 书源：jsLib 提供 put/Get，action 改写 infoMap。
MINI = {
    "bookSourceUrl": "http://mini.test",
    "bookSourceName": "mini",
    "jsLib": (
        "var CFG = {z: 0};\n"
        "function put(o) { source.putVariable(JSON.stringify(o)); }\n"
        "function Get(k) { try { return JSON.parse(source.getVariable())[k]; }"
        " catch (e) { return 0; } }\n"
        "function pickZ() { var i = CHARS.indexOf(String(infoMap['分类：']));"
        " CFG.z = i < 0 ? 0 : i; put(CFG); java.refreshExplore(); }\n"
        "function askLogin() { java.open('login','http',null); }\n"
        "function doSearch() { java.searchBook("
        "infoMap['关键词：'] || '', 'x'); }\n"
        "var CHARS = ['女生','男生','出版'];\n"
    ),
    "loginUrl": "",
}


@needs_js
class TestRunKindAction:
    def setup_method(self):
        from app.legado_rule import source_state

        key = MINI["bookSourceUrl"]
        source_state.put_source_variable(key, json.dumps({"z": 0}))
        source_state.cache_delete(f"infoMap_{key}")

    def test_writes_value_before_running_action(self):
        """顺序必须是「先写值再执行」：action 读 infoMap 取当前选中项。"""
        kind = {"title": "分类：", "type": "select", "action": "pickZ()",
                "chars": ["女生", "男生", "出版"]}
        out = run_kind_action(MINI, kind, value="出版")
        assert out["error"] is None, out["log"]
        # pickZ 读 infoMap['分类：'] 得「出版」，index 2 → CFG.z = 2
        assert out["values"]["分类："] == "出版"
        from app.legado_rule import source_state

        assert json.loads(
            source_state.get_source_variable(MINI["bookSourceUrl"]))["z"] == 2

    def test_refresh_signal_from_refresh_explore(self):
        kind = {"title": "分类：", "type": "select", "action": "pickZ()",
                "chars": ["女生", "男生"]}
        out = run_kind_action(MINI, kind, value="男生")
        assert out["refresh"] is True, "java.refreshExplore() 要变成 refresh 信号"

    def test_gear_button_signals_open_login(self):
        kind = {"title": "⚙", "type": "button", "action": "askLogin()"}
        out = run_kind_action(MINI, kind)
        assert out["openLogin"] is True
        assert out["refresh"] is False

    def test_search_button_signals_search_key(self):
        """搜索按钮的关键词来自「关键词：」输入框，不是按钮自身的值。"""
        kind = {"title": "搜索", "type": "button", "action": "doSearch()"}
        # 先让「关键词：」输入框有值（用户填完点搜索）
        run_kind_action(MINI, {"title": "关键词：", "type": "text"},
                        value="庆余年")
        out = run_kind_action(MINI, kind)
        assert out["searchKey"] == "庆余年", out["log"]

    def test_infoMap_subscript_readable_in_js(self):
        """书源用下标读 infoMap（番茄通篇 `infoMap['关键词：']`）。

        桥对象只有 get/put 方法、没有数据字段，不包装的话下标读出来是
        undefined，每条 action 都会静默拿到空值。
        """
        from app.legado_rule.js_bridge import JsEvaluator
        from app.legado_rule.source_bridge import InfoMapBridge, bridges_for

        key = MINI["bookSourceUrl"]
        InfoMapBridge(MINI).put("关键词：", "斗破苍穹")
        ns = bridges_for(MINI)
        ns["infoMap"] = InfoMapBridge(MINI)
        ev = JsEvaluator({"source": MINI, "__bridge__": None, "__ns__": ns})
        got = ev.eval("String(infoMap['关键词：'])")
        assert got == "斗破苍穹", f"下标读取失效: {got!r}"
        # .get() 方法形态仍要可用（legado 两种取法都在用）
        assert ev.eval("String(infoMap.get('关键词：'))") == "斗破苍穹"

    def test_error_returned_not_raised(self):
        kind = {"title": "炸", "type": "button", "action": "noSuchFn()"}
        out = run_kind_action(MINI, kind)
        assert out["error"], "动作异常要回传，不能炸掉接口"

    def test_empty_action_is_noop(self):
        out = run_kind_action(MINI, {"title": "空", "type": "url"})
        assert out["error"] is None
        assert out["refresh"] is False

    def test_current_values_roundtrip(self):
        run_kind_action(MINI, {"title": "分类：", "action": "pickZ()"},
                        value="女生")
        assert explore_ui.current_values(MINI)["分类："] == "女生"


# ------------------------------------------------- 番茄真实结构（离线桩网）
# 番茄书源把签名委托给远端 sg.91loli.cc（jsLib 的 sixgodHost），该服务是
# 第三方单点、实测会 504。这里用它的**真实 exploreUrl 结构**另造一个不联网
# 的孪生源，验证「控件解析 → 点击 → action 求值 → 信号回传」整条链路。
FQ_SHAPED = {
    "bookSourceUrl": "http://fq-shaped.test",
    "bookSourceName": "番茄形状源（离线）",
    "jsLib": (
        "var CFG = {z: 3, w: 2};\n"
        "var Z = ['女生','男生','出版','自定义多选'];\n"
        "function put(o) { source.putVariable(JSON.stringify(o)); }\n"
        "function Get(k) { try { return JSON.parse(source.getVariable())[k]; }"
        " catch (e) { return 0; } }\n"
        "function saveKeys(m) { m.set(m); m.save(); }\n"
        "function z(e) {\n"
        "  CFG.z = e; put(CFG);\n"
        "  java.refreshExplore();\n"
        "}\n"
        "function w(e) { CFG.w = e; put(CFG); saveKeys(infoMap); }\n"
    ),
    "loginUrl": "",
    # 与番茄 exploreUrl 输出同构：标题占位行(1) + 榜单按钮(0.45) +
    # 关键词输入(0.6) + 搜索按钮(-1) + ⚙按钮(-1) + 两个下拉(0.45)
    "exploreUrl": json.dumps([
        {"title": "༺  ✨番茄榜单✨  ༻", "url": "", "type": "url",
         "style": {"layout_flexGrow": 1, "layout_flexBasisPercent": 1}},
        {"title": "推荐榜", "url": "@js:'http://x/rank'", "type": "url",
         "style": {"layout_flexGrow": 1, "layout_flexBasisPercent": 0.45}},
        {"title": "完本榜", "url": "@js:'http://x/end'", "type": "url",
         "style": {"layout_flexGrow": 1, "layout_flexBasisPercent": 0.45}},
        {"title": "关键词：", "type": "text",
         "style": {"layout_flexGrow": 1, "layout_flexBasisPercent": 0.6}},
        {"title": "搜索", "type": "button",
         "action": "java.searchBook(infoMap['关键词：'] || '',"
                   " `${source.bookSourceName}::${source.getKey()}`);"
                   "saveKeys(infoMap)",
         "style": {"layout_flexGrow": 1, "layout_flexBasisPercent": -1}},
        {"title": "⚙", "type": "button",
         "action": "java.open('login','http',null);saveKeys(infoMap)",
         "style": {"layout_flexGrow": 1, "layout_flexBasisPercent": -1}},
        {"title": "分类：", "type": "select",
         "action": "z(Z.indexOf(String(infoMap['分类：'])))",
         "style": {"layout_flexGrow": 1, "layout_flexBasisPercent": 0.45},
         "chars": ["女生", "男生", "出版", "自定义多选"],
         "default": "自定义多选"},
        {"title": "偏好：", "type": "select",
         "action": "w(0);saveKeys(infoMap)",
         "style": {"layout_flexGrow": 1, "layout_flexBasisPercent": 0.45},
         "chars": ["不限", "男生", "女生"], "default": "不限"},
    ], ensure_ascii=False),
}


class TestFanqieShapedExplore:
    """番茄形状源的离线端到端（不含签名网络）。"""

    def setup_method(self):
        from app.legado_rule import source_state

        key = FQ_SHAPED["bookSourceUrl"]
        source_state.put_source_variable(key, json.dumps({"z": 3, "w": 2}))
        source_state.cache_delete(f"infoMap_{key}")
        web_book._KINDS_CACHE.clear()

    def test_widgets_parsed_with_types(self):
        kinds = web_book.explore_kinds(FQ_SHAPED)
        by_type = {}
        for k in kinds:
            by_type.setdefault(k["type"], []).append(k)
        assert set(by_type) == {"url", "text", "button", "select"}
        assert [k["title"] for k in by_type["button"]] == ["搜索", "⚙"]
        assert by_type["text"][0]["title"] == "关键词："
        assert len(by_type["select"]) == 2

    def test_gear_and_search_actions_have_js(self):
        kinds = web_book.explore_kinds(FQ_SHAPED)
        gear = next(k for k in kinds if k["title"] == "⚙")
        assert "java.open('login'" in gear["action"]
        search = next(k for k in kinds if k["title"] == "搜索")
        assert "java.searchBook" in search["action"]

    def test_gear_click_opens_login(self):
        """用户报告的原始缺陷：⚙ 按钮点了没反应。"""
        gear = next(k for k in web_book.explore_kinds(FQ_SHAPED)
                    if k["title"] == "⚙")
        out = run_kind_action(FQ_SHAPED, gear)
        assert out["openLogin"] is True, out["log"]
        assert out["error"] is None

    def test_search_click_carries_typed_keyword(self):
        """关键词输入框的值要能被搜索按钮读到（都走 infoMap）。"""
        kinds = web_book.explore_kinds(FQ_SHAPED)
        box = next(k for k in kinds if k["title"] == "关键词：")
        search = next(k for k in kinds if k["title"] == "搜索")
        run_kind_action(FQ_SHAPED, box, value="庆余年")
        out = run_kind_action(FQ_SHAPED, search)
        assert out["searchKey"] == "庆余年", out["log"]

    def test_category_select_switches_and_refreshes(self):
        """切换分类：写值 → action 用下标读 infoMap → 触发 refresh。"""
        sel = next(k for k in web_book.explore_kinds(FQ_SHAPED)
                   if k["title"] == "分类：")
        out = run_kind_action(FQ_SHAPED, sel, value="出版")
        assert out["error"] is None, out["log"]
        assert out["refresh"] is True
        from app.legado_rule import source_state

        saved = json.loads(
            source_state.get_source_variable(FQ_SHAPED["bookSourceUrl"]))
        assert saved["z"] == 2, "「出版」在 Z 里的下标应是 2"

    def test_kinds_cache_invalidated_on_refresh(self, monkeypatch):
        from app.plugins.registry import PluginContext, get_engine

        eng = get_engine("legado", PluginContext(settings=None))
        sel = next(k for k in web_book.explore_kinds(FQ_SHAPED)
                   if k["title"] == "分类：")
        web_book.explore_kinds(FQ_SHAPED)      # 填缓存
        assert web_book._kinds_cache_key(FQ_SHAPED) in web_book._KINDS_CACHE
        eng.explore_kind_action(FQ_SHAPED, sel, "男生")
        assert web_book._kinds_cache_key(FQ_SHAPED) not in web_book._KINDS_CACHE


# ------------------------------------------------------------- 引擎接线
class TestEngineWiring:
    def test_engine_exposes_widget_ops(self):
        from app.plugins.registry import PluginContext, get_engine

        eng = get_engine("legado", PluginContext(settings=None))
        for op in ("explore_kind_values", "explore_kind_action"):
            assert callable(getattr(eng, op, None)), op

    def test_action_invalidates_kinds_cache(self, monkeypatch):
        from app.plugins.registry import PluginContext, get_engine

        eng = get_engine("legado", PluginContext(settings=None))
        called = []
        monkeypatch.setattr(
            explore_ui, "run_kind_action",
            lambda *a, **k: {"refresh": True, "openLogin": False,
                             "searchKey": None, "log": [], "values": {},
                             "error": None},
        )
        monkeypatch.setattr(
            web_book, "invalidate_explore_kinds",
            lambda src=None: called.append(src),
        )
        eng.explore_kind_action(MINI, {"title": "x", "action": "y"})
        assert called, "refresh 时必须失效分类缓存"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
