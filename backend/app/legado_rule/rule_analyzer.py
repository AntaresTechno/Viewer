"""Port of RuleAnalyzer.kt — rule tokenizer.

Splits rules by top-level separators ("&&", "||", "%%") while skipping
balanced bracket groups; supports inner-rule replacement for {{$....}}
style blocks with code-balanced braces.
"""
from __future__ import annotations

from collections.abc import Callable

ESC = "\\"


class RuleAnalyzer:
    def __init__(self, data: str, code: bool = False):
        self.queue = data
        self.pos = 0
        self.start = 0
        self.start_x = 0
        self.step = 0
        self.elements_type = ""
        self._code = code

    # ------------------------------------------------------------------ trim
    def trim(self) -> None:
        """Strip leading '@' or whitespace/control chars."""
        q = self.queue
        if self.pos < len(q):
            c = q[self.pos]
            if c == "@" or c < "!":
                self.pos += 1
                while self.pos < len(q) and (
                    q[self.pos] == "@" or q[self.pos] < "!"
                ):
                    self.pos += 1
                self.start = self.pos
                self.start_x = self.pos

    def reset_pos(self) -> None:
        self.pos = 0
        self.start_x = 0

    # ------------------------------------------------------- consume helpers
    def _consume_to(self, seq: str) -> bool:
        """Advance pos to (but not including) seq; start=pos. True if found."""
        self.start = self.pos
        offset = self.queue.find(seq, self.pos)
        if offset != -1:
            self.pos = offset
            return True
        return False

    def _consume_to_any(self, *seps: str) -> bool:
        pos = self.pos
        q = self.queue
        while pos != len(q):
            for s in seps:
                if q.startswith(s, pos):
                    self.step = len(s)
                    self.pos = pos
                    return True
            pos += 1
        return False

    @staticmethod
    def _find_to_any(q: str, pos: int, chars: str) -> int:
        while pos != len(q):
            if q[pos] in chars:
                return pos
            pos += 1
        return -1

    # ------------------------------------------------------- balanced groups
    def _chomp_code_balanced(self, open_ch: str, close_ch: str, pos: int) -> tuple[bool, int]:
        """Balance [...] nesting and open/close at depth 0; quote aware; ESC always escapes."""
        q = self.queue
        depth = 0
        other_depth = 0
        in_single = in_double = False
        n = len(q)
        while True:
            if pos == n:
                break
            c = q[pos]
            pos += 1
            if c != ESC:
                if c == "'" and not in_double:
                    in_single = not in_single
                elif c == '"' and not in_single:
                    in_double = not in_double
                if in_single or in_double:
                    continue
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                elif depth == 0:
                    if c == open_ch:
                        other_depth += 1
                    elif c == close_ch:
                        other_depth -= 1
            else:
                pos += 1
            if depth <= 0 and other_depth <= 0:
                break
        ok = depth <= 0 and other_depth <= 0
        return ok, pos

    def _chomp_rule_balanced(self, open_ch: str, close_ch: str, pos: int) -> tuple[bool, int]:
        """Quote aware; backslash outside quotes escapes; balances open/close only."""
        q = self.queue
        depth = 0
        in_single = in_double = False
        n = len(q)
        while True:
            if pos == n:
                break
            c = q[pos]
            pos += 1
            if c == "'" and not in_double:
                in_single = not in_single
            elif c == '"' and not in_single:
                in_double = not in_single
            if in_single or in_double:
                continue
            if c == ESC:
                pos += 1
                continue
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
            if depth <= 0:
                break
        ok = depth <= 0
        return ok, pos

    def _chomp_balanced(self, ch: str, pos: int) -> tuple[bool, int]:
        nxt = "]" if ch == "[" else ")"
        if self._code:
            return self._chomp_code_balanced(ch, nxt, pos)
        return self._chomp_rule_balanced(ch, nxt, pos)

    # ------------------------------------------------------------ splitRule
    def split_rule(self, *seps: str) -> list[str]:
        """Return rule segments split by the first-found separator.

        The first separator occurrence outside balanced [ ] / ( ) groups wins;
        later occurrences are matched naively (bug-compatible with legado).
        """
        rule: list[str] = []
        sep_pos = -1
        found_sep = ""
        i = self.start_x
        q = self.queue
        while True:
            # find nearest separator and nearest bracket opener from i
            best_sep = -1
            best_s = ""
            for s in seps:
                j = q.find(s, i)
                if j != -1 and (best_sep == -1 or j < best_sep):
                    best_sep = j
                    best_s = s
            st = self._find_to_any(q, i, "[(")
            if best_sep == -1:
                # no separator at all
                rule.append(q[self.start_x:])
                self.elements_type = ""
                return rule
            if st != -1 and st < best_sep:
                # skip the balanced group then rescan after it
                ok, after = self._chomp_balanced(q[st], st)
                if not ok:
                    raise ValueError(f"rule unbalanced near: {q[max(0, st - 10):st + 20]!r}")
                i = after
                continue
            found_sep = best_s
            sep_pos = best_sep
            break

        self.elements_type = found_sep
        step = len(found_sep)

        # first segment
        rule.append(q[self.start_x:sep_pos])
        pos = sep_pos + step
        # subsequent occurrences matched naively (same as legado)
        while True:
            j = q.find(found_sep, pos)
            if j == -1:
                break
            rule.append(q[pos:j])
            pos = j + step
        rule.append(q[pos:])
        return rule

    # ------------------------------------------------------------- innerRule
    def inner_rule_braced(self, inner: str, fr: Callable[[str], str | None]) -> str:
        """Replace ``{$.rule}`` style inner rules using code-balanced braces."""
        out: list[str] = []
        replaced_any = False
        start_step = 1   # '{' of {$.
        end_step = 1     # closing '}'
        search_from = self.start_x
        q = self.queue
        result_parts: list[str] = []
        cursor = self.start_x
        while True:
            idx = q.find(inner, cursor)
            if idx == -1:
                break
            ok, after = self._chomp_code_balanced("{", "}", idx)
            if ok:
                content = q[idx + start_step: after - end_step]
                frv = fr(content)
                if frv is not None and frv != "":
                    result_parts.append(q[cursor:idx])
                    result_parts.append(frv)
                    replaced_any = True
                    cursor = after
                    continue
            cursor = idx + len(inner)
        if not replaced_any and self.start_x == 0:
            return ""
        result_parts.append(q[cursor:])
        return "".join(result_parts)

    def inner_rule(self, start_str: str, end_str: str, fr: Callable[[str], str | None]) -> str:
        """Replace {{...}} style inner rules (naive end matching)."""
        q = self.queue
        parts: list[str] = []
        cursor = self.start_x
        replaced = False
        while True:
            idx = q.find(start_str, cursor)
            if idx == -1:
                break
            content_start = idx + len(start_str)
            end_idx = q.find(end_str, content_start)
            if end_idx == -1:
                break
            frv = fr(q[content_start:end_idx])
            parts.append(q[cursor:idx])
            if frv is None:
                frv = ""
            parts.append(frv)
            replaced = True
            cursor = end_idx + len(end_str)
        if not replaced and self.start_x == 0:
            return q
        parts.append(q[cursor:])
        return "".join(parts)


def split_top_level(rule: str, seps: tuple[str, ...] = ("&&", "||", "%%"), code: bool = False) -> tuple[list[str], str]:
    """Convenience: returns (segments, elements_type)."""
    ra = RuleAnalyzer(rule, code=code)
    segments = ra.split_rule(*seps)
    return segments, ra.elements_type
