"""Port of AnalyzeByJSoup.kt — legado's mini HTML selector grammar."""
from __future__ import annotations

import copy
import re
from typing import Any

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


def parse_doc(doc: Any) -> Tag:
    if isinstance(doc, Tag):
        return doc
    text = doc if isinstance(doc, str) else str(doc)
    if text.lstrip().lower().startswith("<?xml"):
        return BeautifulSoup(text, "html.parser")
    try:
        return BeautifulSoup(text, "lxml")
    except Exception:  # noqa: BLE001 - lxml unavailable
        return BeautifulSoup(text, "html.parser")


def _children(el: Tag) -> list[Tag]:
    return [c for c in el.children if isinstance(c, Tag)]


def _own_text(el: Tag) -> str:
    parts = []
    for c in el.children:
        if isinstance(c, NavigableString) and not isinstance(c, Comment):
            t = str(c).strip()
            if t:
                parts.append(t)
    return " ".join(parts)


def _text(el: Tag) -> str:
    return " ".join(el.get_text().split())


def _data(el: Tag) -> str:
    parts = []
    for c in el.descendants:
        if isinstance(c, Tag) and c.name in ("script", "style") and c.string:
            parts.append(str(c.string))
    joined = "\n".join(p.strip() for p in parts if p.strip())
    return joined


def _outer_html(el: Tag, drop_script_style: bool = False) -> str:
    node = copy.copy(el)
    if drop_script_style:
        for bad in node.find_all(["script", "style"]):
            bad.decompose()
    return node.decode()


class AnalyzeByJSoup:
    def __init__(self, doc: Any):
        self.element: Tag = parse_doc(doc)

    # -------------------------------------------------------------- strings
    def get_string(self, rule_str: str) -> str | None:
        if not rule_str:
            return None
        lst = self.get_string_list(rule_str)
        if not lst:
            return None
        if len(lst) == 1:
            return lst[0]
        return "\n".join(lst)

    def get_string0(self, rule_str: str) -> str:
        lst = self.get_string_list(rule_str)
        return lst[0] if lst else ""

    def get_string_list(self, rule_str: str) -> list[str]:
        texts: list[str] = []
        if not rule_str:
            return texts

        css_flag = rule_str[:5].lower() == "@css:"
        elements_rule = rule_str[5:].strip() if css_flag else rule_str

        if not elements_rule:
            d = _data(self.element)
            if d:
                texts.append(d)
            return texts

        from .rule_analyzer import RuleAnalyzer

        ra = RuleAnalyzer(elements_rule)
        segments = ra.split_rule("&&", "||", "%%")

        results: list[list[str]] = []
        for seg in segments:
            if css_flag:
                last_at = seg.rfind("@")
                if last_at == -1:
                    sel, attr = seg, "text"
                else:
                    sel, attr = seg[:last_at], seg[last_at + 1:]
                temp = self._result_last(self._select_css(sel), attr)
            else:
                temp = self._result_list(seg)
            if temp:
                results.append(temp)
                if ra.elements_type == "||":
                    break

        if results:
            if ra.elements_type == "%%":
                for i in range(len(results[0])):
                    for group in results:
                        if i < len(group):
                            texts.append(group[i])
            else:
                for group in results:
                    texts.extend(group)
        return texts

    def _select_css(self, selector: str) -> list[Tag]:
        # 1) 先按标准 CSS 走一遭（绝大多数规则不含 legado 专属伪类）
        try:
            res = list(self.element.select(selector))
            if res:
                return res
        except Exception:  # noqa: BLE001 - :eq 之类 soupsieve 不认
            pass
        # 2) legado 专属：:eq(n) 取第 n 个命中元素（0 基），按链式逐步解析
        return self._select_legado(selector)

    def _select_legado(self, selector: str) -> list[Tag]:
        m = re.search(r":eq\(\s*(-?\d+)\s*\)", selector)
        if not m:
            return []
        base = selector[: m.start()].strip()
        idx = int(m.group(1))
        rest = selector[m.end():]
        try:
            elements = list(self.element.select(base)) if base else [self.element]
        except Exception:  # noqa: BLE001
            elements = []
        if idx < 0:
            idx += len(elements)
        if idx < 0:
            return []
        if idx >= len(elements):
            # legado/jsoup 对越界正下标收敛到最后一个命中元素
            # （典型如 @css:.prenext span:eq(2) a@href，.prenext 只有 2 个 span）
            idx = len(elements) - 1
        target = elements[idx]
        if not rest.strip():
            return [target]
        return AnalyzeByJSoup(target)._select_css(rest.strip())

    # ------------------------------------------------------------- elements
    def get_elements(self, rule: str) -> list[Tag]:
        if not rule:
            return []
        css_flag = rule[:5].lower() == "@css:"
        elements_rule = rule[5:].strip() if css_flag else rule

        from .rule_analyzer import RuleAnalyzer

        ra = RuleAnalyzer(elements_rule)
        segments = ra.split_rule("&&", "||", "%%")

        groups: list[list[Tag]] = []
        for seg in segments:
            if css_flag:
                el = self._select_css(seg)
                groups.append(el)
                if el and ra.elements_type == "||":
                    break
                continue
            ra_seg = RuleAnalyzer(seg)
            ra_seg.trim()
            parts = _split_naive(ra_seg.queue, "@")
            if len(parts) > 1:
                current: list[Tag] = [self.element]
                for p in parts:
                    nxt: list[Tag] = []
                    for et in current:
                        nxt.extend(ElementsSingle().get_elements_single(et, p))
                    current = nxt
                el = current
            else:
                el = ElementsSingle().get_elements_single(self.element, seg.strip())
            groups.append(el)
            if el and ra.elements_type == "||":
                break

        merged: list[Tag] = []
        if groups:
            if ra.elements_type == "%%":
                for i in range(len(groups[0])):
                    for g in groups:
                        if i < len(g):
                            merged.append(g[i])
            else:
                for g in groups:
                    merged.extend(g)
        return merged

    # --------------------------------------------------------------- result
    def _result_list(self, rule_str: str) -> list[str] | None:
        if not rule_str:
            return None
        elements: list[Tag] = [self.element]

        from .rule_analyzer import RuleAnalyzer

        ra = RuleAnalyzer(rule_str)
        ra.trim()
        rules = _split_naive(ra.queue, "@")

        last = len(rules) - 1
        for i in range(last):
            es: list[Tag] = []
            for elt in elements:
                es.extend(ElementsSingle().get_elements_single(elt, rules[i]))
            elements = es
        if not elements:
            return None
        return self._result_last(elements, rules[last])

    def _result_last(self, elements: list[Tag], last_rule: str) -> list[str]:
        texts: list[str] = []
        if last_rule == "text":
            for el in elements:
                t = _text(el)
                if t:
                    texts.append(t)
        elif last_rule == "textNodes":
            for el in elements:
                tn: list[str] = []
                for item in el.children:
                    if isinstance(item, NavigableString) and not isinstance(item, Comment):
                        t = str(item).strip()
                        if t:
                            tn.append(t)
                if tn:
                    texts.append("\n".join(tn))
        elif last_rule == "ownText":
            for el in elements:
                t = _own_text(el)
                if t:
                    texts.append(t)
        elif last_rule == "html":
            html = "".join(_outer_html(e, drop_script_style=True) for e in elements)
            if html:
                texts.append(html)
        elif last_rule == "all":
            texts.append("".join(_outer_html(e) for e in elements))
        else:
            for el in elements:
                url = el.get(last_rule, "")
                if isinstance(url, list):
                    url = " ".join(str(x) for x in url)
                url = str(url).strip()
                if not url or url in texts:
                    continue
                texts.append(url)
        return texts


class ElementsSingle:
    """One selector step, with optional legacy or bracket-style index suffix."""

    def __init__(self) -> None:
        self.split_char = "."
        self.before_rule = ""
        self.index_default: list[int] = []
        self.indexes: list[int | tuple[int | None, int, int]] = []

    # ------------------------------------------------------------------ api
    def get_elements_single(self, temp: Tag, rule: str) -> list[Tag]:
        self._find_index_set(rule.strip())

        if not self.before_rule:
            elements = _children(temp)
        else:
            parts = self.before_rule.split(".")
            head = parts[0]
            arg = parts[1] if len(parts) > 1 else ""
            if head == "children":
                elements = _children(temp)
            elif head == "class":
                elements = [temp] if arg in (temp.get("class") or []) else []
                elements += temp.find_all(class_=arg)
            elif head == "tag":
                elements = [temp] if temp.name == arg else []
                elements += temp.find_all(arg)
            elif head == "id":
                elements = [temp] if temp.get("id") == arg else []
                elements += temp.find_all(id=arg)
            elif head == "text":
                elements = self._containing_own_text(temp, arg)
            else:
                try:
                    elements = temp.select(self.before_rule)
                except Exception:  # noqa: BLE001
                    elements = []

        length = len(elements)
        chosen: dict[int, None] = {}

        if not self.indexes:
            for ix in reversed(range(len(self.index_default))):
                it = self.index_default[ix]
                if 0 <= it < length:
                    chosen[it] = None
                elif it < 0 and length >= -it:
                    chosen[it + length] = None
        else:
            for ix in reversed(range(len(self.indexes))):
                item = self.indexes[ix]
                if isinstance(item, tuple):
                    start_x, end_x, step_x = item
                    start = start_x if start_x is not None else 0
                    if start < 0:
                        start += length
                    end = end_x if end_x is not None else length - 1
                    if end < 0:
                        end += length
                    if (start < 0 and end < 0) or (start >= length and end >= length):
                        continue
                    if start >= length:
                        start = length - 1
                    elif start < 0:
                        start = 0
                    if end >= length:
                        end = length - 1
                    elif end < 0:
                        end = 0
                    if start == end or step_x >= length:
                        chosen[start] = None
                        continue
                    if step_x > 0:
                        step = step_x
                    elif -step_x < length:
                        step = step_x + length
                    else:
                        step = 1
                    if end > start:
                        vals = range(start, end + 1, max(step, 1))
                    else:
                        vals = range(start, end - 1, -(abs(step) or 1))
                    for v in vals:
                        chosen[v] = None
                else:
                    it = item
                    if 0 <= it < length:
                        chosen[it] = None
                    elif it < 0 and length >= -it:
                        chosen[it + length] = None

        if self.split_char == "!":
            return [e for i, e in enumerate(elements) if i not in chosen]
        if self.split_char == ".":
            return [elements[i] for i in chosen if 0 <= i < length]
        return elements

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _containing_own_text(temp: Tag, arg: str) -> list[Tag]:
        out: list[Tag] = []
        if arg in _own_text(temp):
            out.append(temp)
        for d in temp.descendants:
            if isinstance(d, Tag) and arg in _own_text(d):
                out.append(d)
        return out

    # ---------------------------------------------------------- index parse
    def _find_index_set(self, rus: str) -> None:
        self.index_default = []
        self.indexes = []
        self.split_char = "."
        self.before_rule = ""

        if not rus:
            self.split_char = " "
            return

        digits = ""
        cur_minus = False
        cur_list: list[int | None] = []

        def take_number() -> int | None:
            nonlocal digits, cur_minus
            if digits == "":
                return None
            val = int(digits)
            if cur_minus:
                val = -val
            digits = ""
            cur_minus = False
            return val

        head = rus[-1] == "]"
        # index of the first character to inspect (right to left)
        idx = len(rus) - 2 if head else len(rus) - 1

        while idx >= 0:
            rl = rus[idx]
            if rl == " ":
                idx -= 1
                continue
            if rl.isdigit():
                digits = rl + digits
                idx -= 1
                continue
            if rl == "-":
                cur_minus = True
                idx -= 1
                continue

            cur_int = take_number()

            if not head:
                # 阅读原有写法：分隔符为 '.' / '!' / ':'
                if rl in ("!", ".", ":"):
                    num = cur_int
                    if num is None:
                        break
                    self.index_default.append(num)
                    if rl != ":":
                        self.split_char = rl
                        self.before_rule = rus[:idx]
                        return
                    idx -= 1
                    continue
                break
            else:
                if rl == ":":
                    cur_list.append(cur_int)
                    idx -= 1
                    continue
                # any other terminator
                if not cur_list:
                    if cur_int is None:
                        break  # jsoup selector rather than an index list
                    self.indexes.append(cur_int)
                else:
                    end_v = cur_list[-1]
                    assert end_v is not None
                    step_v = cur_list[0] if len(cur_list) == 2 else 1
                    self.indexes.append((cur_int, end_v, int(step_v)))
                    cur_list.clear()
                if rl == "!":
                    self.split_char = "!"
                    idx -= 1
                    while idx > 0 and rus[idx] == " ":
                        idx -= 1
                    rl = rus[idx]
                if rl == "[":
                    self.before_rule = rus[:idx]
                    return
                if rl != ",":
                    break
                idx -= 1
                continue

        self.split_char = " "
        self.before_rule = rus


def _split_naive(text: str, sep: str) -> list[str]:
    return text.split(sep)
