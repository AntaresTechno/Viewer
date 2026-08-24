"""净化/替换规则：存储模型之上的纯应用逻辑（scope 匹配 + 安全执行）。

规则按 (group_order, order, id) 排序依次套用到章节文本上。
- ``regex=True``  : Python 正则替换，replacement 支持 \\1 分组引用；
- ``regex=False`` : 纯文本替换（大小写不敏感选项用自写扫描，避免转义坑）。
单条规则执行有超时保护（防灾难性回溯卡死事件循环），失败/超时跳过该条。
"""
from __future__ import annotations

import asyncio
import re

from ..core.config import settings
from ..models import ReplaceRule

_SCOPE_SPLIT = re.compile(r"\n|;|\|\|")


def scope_allows(
    scope: str, *, book_name: str, source_url: str, source_name: str
) -> bool:
    include: list[str] = []
    exclude: list[str] = []
    for raw in _SCOPE_SPLIT.split(scope or ""):
        entry = raw.strip()
        if not entry:
            continue
        if entry.startswith("-"):
            exclude.append(entry[1:].strip().lower())
        else:
            include.append(entry.lower())
    hay = f"{book_name}\n{source_name}\n{source_url}".lower()
    if any(x and x in hay for x in exclude):
        return False
    if not include:
        return True
    return any(i and i in hay for i in include)


def _plain_replace_ci(text: str, old: str, new: str) -> str:
    low, old_l, n = text.lower(), old.lower(), len(old)
    out: list[str] = []
    i = 0
    while True:
        j = low.find(old_l, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        out.append(new)
        i = j + n
    return "".join(out)


def _regex_sub(pattern: str, repl: str, text: str, flags: int) -> str:
    return re.sub(pattern, repl, text, flags=flags)


async def apply_rules(
    text: str,
    rules: list[ReplaceRule],
    *,
    book_name: str = "",
    source_url: str = "",
    source_name: str = "",
) -> tuple[str, list[str]]:
    """Apply ordered rules; returns (new_text, names_of_applied_rules)."""
    applied: list[str] = []
    for r in sorted(rules, key=lambda x: (x.group_order, x.order, x.id)):
        if not r.is_active or not (r.pattern or "").strip():
            continue
        if not scope_allows(
            r.scope, book_name=book_name, source_url=source_url,
            source_name=source_name,
        ):
            continue
        try:
            if r.regex:
                flags = 0 if r.case_sensitive else re.IGNORECASE
                text = await asyncio.wait_for(
                    asyncio.to_thread(_regex_sub, r.pattern, r.replacement, text, flags),
                    timeout=settings.replace_regex_timeout,
                )
            elif r.case_sensitive:
                text = text.replace(r.pattern, r.replacement)
            else:
                text = _plain_replace_ci(text, r.pattern, r.replacement)
        except Exception:  # noqa: BLE001 - 坏规则/超时只影响自己，跳过继续
            continue
        applied.append(r.name or f"#{r.id}")
    return text, applied


def parse_legado_import(obj: object) -> list[dict]:
    """Normalize an imported legado replace-rule JSON payload into row dicts."""
    items: list[object]
    if isinstance(obj, dict):
        items = [obj]
    elif isinstance(obj, list):
        items = obj
    else:
        return []

    def _as_bool(v: object, default: bool) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes")
        return default

    def _as_int(v: object, default: int = 0) -> int:
        try:
            return int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        pattern = str(it.get("pattern") or "").strip()
        if not pattern:
            continue
        out.append({
            "name": str(it.get("name") or "")[:128],
            "group": str(it.get("group") or "")[:128],
            "group_order": _as_int(it.get("groupOrder")),
            "order": _as_int(it.get("order")),
            "is_active": _as_bool(it.get("isActive"), True),
            "pattern": pattern,
            "replacement": str(it.get("replacement") or ""),
            "scope": str(it.get("scope") or "")[:512],
            "regex": _as_bool(it.get("regex"), True),
            "case_sensitive": _as_bool(it.get("caseSensitive"), True),
        })
    return out
