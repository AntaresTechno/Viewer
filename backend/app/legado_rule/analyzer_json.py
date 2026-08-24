"""Port of AnalyzeByJSonPath.kt using jsonpath-ng."""
from __future__ import annotations

import json
from typing import Any

from jsonpath_ng import parse as jp_parse
from jsonpath_ng.ext import parse as ext_parse

from .exceptions import RuleError
from .rule_analyzer import RuleAnalyzer

_USE_EXT = True


def json_loads_lenient(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        # common legado sources ship slightly-broken JSON
        cleaned = text.strip().lstrip("\ufeff")
        try:
            return json.loads(cleaned)
        except Exception:
            raise RuleError("JSON 解析失败") from None


class _Ctx:
    def __init__(self, data: Any):
        self.data = data


def compile_path(rule: str):
    global _USE_EXT
    if _USE_EXT:
        try:
            return ext_parse(rule)
        except Exception:
            _USE_EXT = False
    return jp_parse(rule)


def read_path(data: Any, rule: str) -> Any:
    """Jayway-ish read: missing path yields None instead of raising."""
    expr = compile_path(rule)
    matches = [m.value for m in expr.find(data)]
    if not matches:
        return None
    return matches if len(matches) > 1 or isinstance(matches[0], list) else matches[0]


class AnalyzeByJSonPath:
    def __init__(self, json_data: Any):
        self.ctx = _Ctx(json_data)

    # -------------------------------------------------------------- strings
    def get_string(self, rule: str) -> str | None:
        if not rule:
            return None
        ra = RuleAnalyzer(rule, code=True)
        rules = ra.split_rule("&&", "||")

        if len(rules) == 1:
            ra.reset_pos()
            result = ra.inner_rule_braced("{$.", lambda inner: self.get_string(inner))
            if result == "":
                ob = read_path(self.ctx.data, rule)
                if ob is None:
                    return None
                if isinstance(ob, list):
                    return "\n".join(_scalar_str(o) for o in ob)
                return _scalar_str(ob) if not isinstance(ob, str) else ob
            return result

        parts: list[str] = []
        for rl in rules:
            temp = self.get_string(rl)
            if temp:
                parts.append(temp)
                if ra.elements_type == "||":
                    break
        return "\n".join(parts) if parts else None

    def get_string_list(self, rule: str) -> list[str]:
        result: list[str] = []
        if not rule:
            return result
        ra = RuleAnalyzer(rule, code=True)
        rules = ra.split_rule("&&", "||", "%%")

        if len(rules) == 1:
            ra.reset_pos()
            st = ra.inner_rule_braced("{$.", lambda inner: self.get_string(inner))
            if st == "":
                obj = read_path(self.ctx.data, rule)
                if obj is None:
                    return result
                if isinstance(obj, list):
                    result.extend(_scalar_str(o) for o in obj)
                else:
                    result.append(_scalar_str(obj))
            elif st != "":
                result.append(st)
            return result

        results: list[list[str]] = []
        for rl in rules:
            temp = self.get_string_list(rl)
            if temp:
                results.append(temp)
                if ra.elements_type == "||":
                    break
        if results:
            if ra.elements_type == "%%":
                for i in range(len(results[0])):
                    for group in results:
                        if i < len(group):
                            result.append(group[i])
            else:
                for group in results:
                    result.extend(group)
        return result

    # -------------------------------------------------------------- objects
    def get_object(self, rule: str) -> Any:
        return read_path(self.ctx.data, rule)

    def get_list(self, rule: str) -> list[Any]:
        result: list[Any] = []
        if not rule:
            return result
        ra = RuleAnalyzer(rule, code=True)
        rules = ra.split_rule("&&", "||", "%%")
        if len(rules) == 1:
            try:
                got = read_path(self.ctx.data, rule)
            except Exception:  # noqa: BLE001
                return result
            if isinstance(got, list):
                return got
            if got is not None:
                result.append(got)
            return result

        results: list[list[Any]] = []
        for rl in rules:
            temp = self.get_list(rl)
            if temp:
                results.append(temp)
                if ra.elements_type == "||":
                    break
        if results:
            if ra.elements_type == "%%":
                for i in range(len(results[0])):
                    for group in results:
                        if i < len(group):
                            result.append(group[i])
            else:
                for group in results:
                    result.extend(group)
        return result


def _scalar_str(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, float) and obj == int(obj) and abs(obj) < 1e15:
        return str(int(obj))
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (dict, list)):
        return json.dumps(obj, ensure_ascii=False)
    return str(obj)
