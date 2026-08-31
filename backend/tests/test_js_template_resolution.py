r"""Regression tests for resolving ``{{…}}`／ ``@get:{…}` templates inside
``@js:`` rule code.

番茄(fq0826) 的 bookName 规则把嵌套模板写在 @js: 块里：

    $.book_name||$.title##\s*<\/?em>\s*
    @js:
    gender = String({{$.gender}});
    …

legado 在求值 JS 前会把整条规则串里的 ``{{…}}`` 模板替换掉（位置不限，
包括 @js: 块内部）；我们的引擎之前不替换，`String({{$.gender}})` 在 quickjs
下报 ``SyntaxError: invalid property name``，导致该字段全空、整本书被跳过，
发现/详情页一台书都出不来。这里锁定这个行为。
"""
from __future__ import annotations

from app.legado_rule import js_bridge
from app.legado_rule.analyze_rule import AnalyzeRule

ELEMENT = {
    "book_name": "盗墓",
    "gender": 1,
    "author": "张三",
}


def _rule() -> AnalyzeRule:
    ar = AnalyzeRule(source={})
    ar.set_content(dict(ELEMENT), base_url="http://host/")
    return ar


def test_resolve_js_template_direct():
    out = _rule()._resolve_js_templates(
        "gender = String({{$.gender}});", result="盗墓"
    )
    assert out == "gender = String(1);"


def test_resolve_js_template_in_get_string():
    rule = "$.book_name@js:result ? result + '【' + String({{$.gender}}) + '】' : '';"
    assert _rule().get_string(rule) == "盗墓【1】"


def test_resolve_get_template_inside_js():
    # author 是字符串，若直接替换会变成裸标识符（String(张三)）→ 语法错；
    # 只放数值型的 gender，验证 @js 块内模板确实被替换后再求值。
    rule = "@js:'v=' + String({{$.gender}});"
    assert _rule().get_string(rule) == "v=1"


def test_missing_template_value_does_js_var_lookup():
    # @js 块里用到 jsLib 变量 / 表达式而非规则时按 JS 求值，不抛语法错
    ar = _rule()
    out = ar._resolve_js_templates(
        "x = {{ 1 + 2 }};", result=None
    )
    assert out == "x = 3;"


def test_fanqie_name_rule_shape_smoke(monkeypatch):
    """番茄真实 bookName 规则的骨架不再产生 SyntaxError。"""
    import json

    from app.legado_rule import web_book

    p = __import__("pathlib").Path(r"D:\Project\antares\viewer\fq0826_e50d60ac.json")
    if not p.exists():
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    src = data[0] if isinstance(data, list) else data
    rules = src.get("ruleExplore") or src.get("ruleSearch") or {}
    if isinstance(rules, str):
        rules = json.loads(rules)
    name_rule = rules.get("name") or ""
    if not name_rule:
        return
    monkeypatch.setattr(js_bridge, "_engine_name", None)
    ar = AnalyzeRule(source=src, base_url="http://host/")
    ar.set_content(dict(ELEMENT), base_url="http://host/")
    try:
        val = ar.get_string(name_rule)
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"name 规则不应因模板解析抛错: {exc}") from exc
    assert val.strip(), "name 规则应解析出书名"