"""Port of AnalyzeByRegex.kt."""
from __future__ import annotations

import re
from collections.abc import Iterable


def get_element(res: str, regs: Iterable[str], index: int = 0) -> list[str] | None:
    regs = list(regs)
    regex = re.compile(regs[index])
    first = regex.search(res)
    if first is None:
        return None
    if index + 1 == len(regs):
        return [first.group(0)] + [g or "" for g in first.groups()]
    buf = "".join(m.group(0) for m in regex.finditer(res))
    return get_element(buf, regs, index + 1)


def get_elements(res: str, regs: Iterable[str], index: int = 0) -> list[list[str]]:
    regs = list(regs)
    regex = re.compile(regs[index])
    if regex.search(res) is None:
        return []
    if index + 1 == len(regs):
        books: list[list[str]] = []
        for m in regex.finditer(res):
            info = [m.group(0)] + [g or "" for g in m.groups()]
            books.append(info)
        return books
    buf = "".join(m.group(0) for m in regex.finditer(res))
    return get_elements(buf, regs, index + 1)
