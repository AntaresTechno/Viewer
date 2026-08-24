"""Port of AnalyzeRule.kt — the main rule evaluation pipeline."""
from __future__ import annotations

import html
import json
import re
import threading
from typing import Any

from .analyzer_css import AnalyzeByJSoup
from .analyzer_json import AnalyzeByJSonPath, json_loads_lenient
from .analyzer_regex import get_element as regex_get_element
from .analyzer_regex import get_elements as regex_get_elements
from .analyzer_xpath import AnalyzeByXPath
from .analyze_url import get_absolute_url
from .exceptions import RuleError
from .js_bridge import JavaBridge, detect_engine, eval_js
from .rule_analyzer import RuleAnalyzer

JS_PATTERN = re.compile(r"<js>([\w\W]*?)</js>|@js:([\w\W]*)", re.IGNORECASE)
WEBJS_PATTERN = re.compile(r"@webjs:([\w\W]{5,})", re.IGNORECASE)
PUT_PATTERN = re.compile(r"@put:(\{[^}]+?\})", re.IGNORECASE)
EVAL_PATTERN = re.compile(r"@get:\{([^}]+?)\}|\{\{([\w\W]*?)\}\}", re.IGNORECASE)
REGEX_TOKEN = re.compile(r"\$\d{1,2}")

MODE_XPATH = "xpath"
MODE_JSON = "json"
MODE_DEFAULT = "default"
MODE_JS = "js"
MODE_REGEX = "regex"
MODE_WEBJS = "webjs"

_GET_TYPE = -2
_JS_TYPE = -1
_DEFAULT_TYPE = 0


def _looks_like_json(text: str) -> bool:
    t = text.strip()
    if not t or t[0] not in "{[":
        return False
    try:
        json.loads(t)
        return True
    except Exception:
        return False


class SourceRule:
    """One pipeline stage produced by split_source_rule."""

    __slots__ = (
        "mode", "raw", "rule", "replace_regex", "replacement", "replace_first",
        "put_map", "rule_param", "rule_type",
    )

    def __init__(self, rule_str: str, mode: str = MODE_DEFAULT,
                 parent_is_json: bool = False):
        self.mode = mode
        self.raw = rule_str
        self.replace_regex = ""
        self.replacement = ""
        self.replace_first = False
        self.put_map: dict[str, str] = {}
        self.rule_param: list[str] = []
        self.rule_type: list[int] = []

        rule = rule_str
        if mode in (MODE_JS, MODE_REGEX):
            self.rule = rule
            return
        if rule[:5].lower() == "@css:":
            self.mode = MODE_DEFAULT
        elif rule.startswith("@@"):
            self.mode = MODE_DEFAULT
            rule = rule[2:]
        elif rule[:7].lower() == "@xpath:":
            self.mode = MODE_XPATH
            rule = rule[7:]
        elif rule[:6].lower() == "@json:":
            self.mode = MODE_JSON
            rule = rule[6:]
        elif parent_is_json or rule.startswith("$.") or rule.startswith("$["):
            self.mode = MODE_JSON
        elif rule.startswith("/"):
            self.mode = MODE_XPATH

        # @put:{...}
        for m in PUT_PATTERN.finditer(rule):
            rule = rule.replace(m.group(0), "")
            try:
                obj = json.loads(m.group(1))
                if isinstance(obj, dict):
                    self.put_map.update({str(k): str(v) for k, v in obj.items()})
            except Exception:  # noqa: BLE001
                continue

        # @get / {{ }} / $N segmentation
        start = 0
        matches = list(EVAL_PATTERN.finditer(rule))
        if matches:
            first = matches[0]
            head_text = rule[:first.start()]
            if self.mode not in (MODE_JS, MODE_REGEX) and (
                first.start() == 0 or "##" not in head_text
            ):
                self.mode = MODE_REGEX
        for m in matches:
            if m.start() > start:
                self._split_regex(rule[start:m.start()])
            token = m.group(0)
            if token.lower().startswith("@get:"):
                inner = token[len("@get:{"):-1]
                self.rule_type.append(_GET_TYPE)
                self.rule_param.append(inner)
            elif token.startswith("{{") and token.endswith("}}"):
                self.rule_type.append(_JS_TYPE)
                self.rule_param.append(token[2:-2])
            else:  # unreachable per EVAL_PATTERN, kept for fidelity
                self._split_regex(token)
            start = m.end()
        if len(rule) > start:
            self._split_regex(rule[start:])
        self.rule = rule

    def _split_regex(self, rule_str: str) -> None:
        parts = rule_str.split("##")
        if REGEX_TOKEN.search(parts[0]):
            if self.mode not in (MODE_JS, MODE_REGEX):
                self.mode = MODE_REGEX
        start = 0
        for m in REGEX_TOKEN.finditer(parts[0]):
            if m.start() > start:
                self.rule_type.append(_DEFAULT_TYPE)
                self.rule_param.append(rule_str[start:m.start()])
            self.rule_type.append(int(m.group(0)[1:]))
            self.rule_param.append(m.group(0))
            start = m.end()
        if len(rule_str) > start:
            self.rule_type.append(_DEFAULT_TYPE)
            self.rule_param.append(rule_str[start:])

    def param_size(self) -> int:
        return len(self.rule_param)

    def is_rule(self, s: str) -> bool:
        return (
            s.startswith("@")
            or s.startswith("$.")
            or s.startswith("$[")
            or s.startswith("//")
        )


class AnalyzeRule:
    def __init__(
        self,
        rule_data: dict | None = None,
        source: dict | None = None,
        base_url: str | None = None,
        book: dict | None = None,
        fetcher=None,
    ):
        self.rule_data = rule_data or {}
        self.source = source or {}
        self.book = book or {}
        self.base_url = base_url or ""
        self.fetcher = fetcher  # optional async callable(spec)->StrResponse
        self.content: Any = ""
        self.is_json = False
        self.is_regex = False
        self.variable_map: dict[str, str] = {}
        self.next_chapter_url: str | None = None
        self.chapter_title: str | None = None
        self._string_cache: dict[str, list[SourceRule]] = {}
        self._cache_lock = threading.Lock()
        self.bridge = JavaBridge(owner=self, base_url=self.base_url)

    # --------------------------------------------------------------- set up
    def set_content(self, content: Any, base_url: str | None = None) -> "AnalyzeRule":
        if content is None:
            raise RuleError("内容不可空")
        self.content = content
        self.is_json = isinstance(content, str) and _looks_like_json(content)
        if base_url:
            self.base_url = base_url
        return self

    def put_variable(self, key: str, value: str) -> None:
        self.variable_map[key] = value

    def get_variable(self, key: str) -> str:
        return self.variable_map.get(key, "")

    def put(self, key: str, value: str) -> str:
        self.variable_map[key] = value
        return value

    def get(self, key: str) -> str:
        if key == "bookName":
            return str(self.book.get("name", ""))
        if key == "title" and self.chapter_title:
            return self.chapter_title
        return self.variable_map.get(key, "") or str(self.source.get(key, "") or "")

    # ------------------------------------------------------------ splitting
    def _split_source_rule(self, rule_str: str | None, all_in_one: bool = False) -> list[SourceRule]:
        if not rule_str:
            return []
        rule_list: list[SourceRule] = []
        mode = MODE_DEFAULT
        start = 0
        if all_in_one and rule_str.startswith(":"):
            mode = MODE_REGEX
            self.is_regex = True
            start = 1
        elif self.is_regex:
            mode = MODE_REGEX

        segments: list[tuple[str, str]] = []  # (text, mode)
        pos = start
        for m in WEBJS_PATTERN.finditer(rule_str):
            if m.start() < pos:
                continue
            if m.start() > pos:
                segments.append((rule_str[pos:m.start()].strip(), mode))
            segments.append((m.group(1), MODE_WEBJS))
            pos = m.end()
        if len(rule_str) > pos:
            segments.append((rule_str[pos:].strip(), mode))

        # now carve out <js>/@js: inside each plain segment
        final: list[tuple[str, str]] = []
        for text, seg_mode in segments:
            if seg_mode != MODE_DEFAULT:
                final.append((text, seg_mode))
                continue
            p = 0
            for jm in JS_PATTERN.finditer(text):
                if jm.start() > p:
                    t = text[p:jm.start()].strip()
                    if t:
                        final.append((t, mode))
                code = jm.group(2) or jm.group(1)
                final.append((code, MODE_JS))
                p = jm.end()
            if len(text) > p:
                t = text[p:].strip()
                if t:
                    final.append((t, mode))
        for text, seg_mode in final:
            rule_list.append(SourceRule(text, seg_mode, parent_is_json=self.is_json))
        return rule_list

    def _split_cached(self, rule_str: str) -> list[SourceRule]:
        with self._cache_lock:
            cached = self._string_cache.get(rule_str)
            if cached is None:
                cached = self._split_source_rule(rule_str)
                self._string_cache[rule_str] = cached
            return cached

    # ---------------------------------------------------------- makeUpRule
    def _make_up_rule(self, sr: SourceRule, result: Any) -> None:
        if sr.rule_param:
            info_val: list[str] = []
            for idx in range(len(sr.rule_param) - 1, -1, -1):
                reg_type = sr.rule_type[idx]
                param = sr.rule_param[idx]
                if reg_type > _DEFAULT_TYPE:
                    if isinstance(result, list) and len(result) > reg_type:
                        item = result[reg_type]
                        if item is not None:
                            info_val.insert(0, str(item))
                    else:
                        info_val.insert(0, param)
                elif reg_type == _JS_TYPE:
                    if sr.is_rule(param.strip()):
                        info_val.insert(0, self.get_string(param))
                    else:
                        ev = self.eval_js(param, result)
                        if ev is None:
                            continue
                        if isinstance(ev, float) and ev == int(ev) and abs(ev) < 1e15:
                            info_val.insert(0, "%.0f" % ev)
                        else:
                            info_val.insert(0, ev if isinstance(ev, str) else _to_str(ev))
                elif reg_type == _GET_TYPE:
                    info_val.insert(0, self.get(param))
                else:
                    info_val.insert(0, param)
            sr.rule = "".join(info_val)

        parts = sr.rule.split("##")
        sr.rule = parts[0].strip()
        if len(parts) > 1:
            sr.replace_regex = parts[1]
        if len(parts) > 2:
            sr.replacement = parts[2]
        if len(parts) > 3:
            sr.replace_first = True

    def _apply_replace_regex(self, sr: SourceRule, value: str) -> str:
        if not sr.replace_regex:
            return value
        try:
            rx = re.compile(sr.replace_regex)
        except re.error:
            return value.replace(sr.replace_regex, sr.replacement)
        if sr.replace_first:
            m = rx.search(value)
            if m is None:
                return ""
            return rx.sub(sr.replacement, m.group(0), count=1)
        return rx.sub(sr.replacement, value)

    # ------------------------------------------------------------- dispatch
    def _analyzer_for(self, result: Any, mode: str):
        if mode == MODE_JSON:
            data = result
            if isinstance(data, str):
                data = json_loads_lenient(data)
            return AnalyzeByJSonPath(data)
        return AnalyzeByJSoup(result)

    def _eval_bindings(self, result: Any) -> dict[str, Any]:
        return {
            "__bridge__": self.bridge,
            "cookie": {},
            "cache": {},
            "source": self.source or None,
            "book": self.book or None,
            "result": result,
            "baseUrl": self.base_url,
            "chapter": {"title": self.chapter_title} if self.chapter_title else None,
            "title": self.chapter_title,
            "src": self.content if isinstance(self.content, str) else None,
            "nextChapterUrl": self.next_chapter_url,
            "rssArticle": None,
            "fromBookInfo": False,
        }

    def eval_js(self, js_code: str, result: Any = None) -> Any:
        if detect_engine() is None:
            raise RuleError("未安装 quickjs，无法执行书源中的 JS 规则（pip install quickjs）")
        return eval_js(js_code, self._eval_bindings(result))

    # -------------------------------------------------------------- results
    def get_string(self, rule_str: str | None, m_content: Any = None,
                   is_url: bool = False, unescape: bool = True) -> str:
        if not rule_str:
            return ""
        return self._get_string_impl(self._split_cached(rule_str), m_content,
                                     is_url, unescape=unescape)

    def get_string_from_list(self, rules: list[SourceRule], m_content: Any = None,
                             is_url: bool = False, unescape: bool = True) -> str:
        return self._get_string_impl(rules, m_content, is_url, unescape)

    def _get_string_impl(self, rule_list: list[SourceRule], m_content: Any = None,
                         is_url: bool = False, unescape: bool = True) -> str:
        result: Any = None
        content = m_content if m_content is not None else self.content
        if content is not None and rule_list:
            result = content
            for sr in rule_list:
                self._run_put_map(sr)
                self._make_up_rule(sr, result)
                if result is None:
                    continue
                rule = sr.rule
                if rule.strip() or not sr.replace_regex:
                    result = self._dispatch(sr, rule, result, is_url=is_url,
                                            want="string")
                if result is not None and sr.replace_regex:
                    items = result if isinstance(result, list) else [result]
                    items = [
                        self._apply_replace_regex(sr, i if isinstance(i, str) else _to_str(i))
                        for i in items
                    ]
                    result = items if isinstance(result, list) else items[0]
        if result is None:
            result = ""
        result_str = result if isinstance(result, str) else _to_str(result)
        if unescape and "&" in result_str:
            result_str = html.unescape(result_str)
        if is_url:
            if not result_str.strip():
                return self.base_url
            return get_absolute_url(self.base_url, result_str.strip())
        return result_str

    def get_string_list(self, rule_str: str | None, m_content: Any = None,
                        is_url: bool = False) -> list[str] | None:
        if not rule_str:
            return None
        return self._get_string_list_impl(self._split_cached(rule_str), m_content, is_url)

    def _get_string_list_impl(self, rule_list: list[SourceRule], m_content: Any = None,
                              is_url: bool = False) -> list[str] | None:
        result: Any = None
        content = m_content if m_content is not None else self.content
        if content is not None and rule_list:
            result = content
            for sr in rule_list:
                self._run_put_map(sr)
                self._make_up_rule(sr, result)
                if result is None:
                    continue
                rule = sr.rule
                if rule:
                    result = self._dispatch(sr, rule, result, is_url=is_url,
                                            want="list")
                if sr.replace_regex and isinstance(result, list):
                    result = [
                        self._apply_replace_regex(sr, i if isinstance(i, str) else _to_str(i))
                        for i in result
                    ]
                elif sr.replace_regex and isinstance(result, str):
                    result = self._apply_replace_regex(sr, result)
        if result is None:
            return None
        if isinstance(result, str):
            result = result.split("\n")
        if is_url and isinstance(result, list):
            urls: list[str] = []
            for u in result:
                absu = get_absolute_url(self.base_url, _to_str(u))
                if absu and absu not in urls:
                    urls.append(absu)
            return urls
        return [_to_str(x) for x in result] if isinstance(result, list) else [_to_str(result)]

    def get_element(self, rule_str: str) -> Any:
        if not rule_str:
            return None
        result: Any = self.content
        rule_list = self._split_source_rule(rule_str, all_in_one=True)
        for sr in rule_list:
            self._run_put_map(sr)
            self._make_up_rule(sr, result)
            if result is None:
                continue
            rule = sr.rule
            if sr.mode == MODE_REGEX:
                regs = [r for r in rule.split("&&") if r.strip()]
                got = regex_get_element(_to_str(result), regs)
                result = got
                continue
            if sr.mode == MODE_WEBJS:
                continue
            if rule:
                result = self._dispatch(sr, rule, result, want="element_single")
            if sr.replace_regex:
                result = self._apply_replace_regex(sr, _to_str(result))
        return result

    def get_elements(self, rule_str: str) -> list[Any]:
        result: Any = self.content
        rule_list = self._split_source_rule(rule_str, all_in_one=True)
        for sr in rule_list:
            self._run_put_map(sr)
            if result is None:
                continue
            rule = sr.rule
            if sr.mode == MODE_REGEX:
                regs = [r for r in rule.split("&&") if r.strip()]
                result = regex_get_elements(_to_str(result), regs)
                continue
            if sr.mode == MODE_WEBJS:
                continue
            # NOTE: legado's getElements does NOT run makeUpRule (bug-compatible)
            if rule:
                result = self._dispatch(sr, rule, result, want="elements")
        if isinstance(result, list):
            return result
        if result is None:
            return []
        return [result]

    def _dispatch(self, sr: SourceRule, rule: str, result: Any,
                  is_url: bool = False, want: str = "string") -> Any:
        if sr.mode == MODE_JS:
            ev = self.eval_js(rule, result)
            return ev
        if sr.mode == MODE_WEBJS:
            return result  # WebView execution unsupported server-side
        if sr.mode == MODE_REGEX:
            # legado: template segments fall through to `else -> rule`
            if want == "string":
                return rule
            return [rule]
        if sr.mode == MODE_JSON:
            analyzer = self._analyzer_for(result, MODE_JSON)
            if want == "list":
                return analyzer.get_string_list(rule)
            if want == "elements":
                return analyzer.get_list(rule)
            if want == "element_single":
                return analyzer.get_object(rule)
            return analyzer.get_string(rule)
        if sr.mode == MODE_XPATH:
            analyzer = AnalyzeByXPath(result if isinstance(result, str) else _to_str(result))
            if want == "list":
                return analyzer.get_string_list(rule)
            if want in ("elements", "element_single"):
                return analyzer.get_elements(rule)
            return analyzer.get_string(rule)
        # Default — jsoup
        analyzer = AnalyzeByJSoup(result)
        if want == "list":
            if is_url:
                v = analyzer.get_string0(rule)
                return [v] if v else []
            return analyzer.get_string_list(rule)
        if want == "elements":
            return analyzer.get_elements(rule)
        if want == "element_single":
            els = analyzer.get_elements(rule)
            return els
        if is_url:
            return analyzer.get_string0(rule)
        return analyzer.get_string(rule)

    def _run_put_map(self, sr: SourceRule) -> None:
        for k, v in sr.put_map.items():
            self.put(k, self.get_string(v))


def _to_str(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, float) and obj == int(obj) and abs(obj) < 1e15:
        return str(int(obj))
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    if isinstance(obj, (dict, list)):
        try:
            return json.dumps(obj, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return str(obj)
    return str(obj)
