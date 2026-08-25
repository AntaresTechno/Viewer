"""正文净化管线（纯应用逻辑）：规则匹配 → 净化 → 数据库缓存 → 调用。

数据流（与需求一一对应）：

1. **获取内容**  ``fetch_raw`` 由调用方注入（books 插件传引擎抓取函数）；
2. **净化**      先跑「内置净化 · MD3 版」（``md3_builtin_clean``，移植自
   legado MD3 版 ``HtmlFormatter.formatKeepImg`` 的内置净化层），再按
   (规则序) 套用启用规则包内的规则；替换式支持纯文本、Python 正则与
   legado 的 ``@js:`` JS 替换（经 quickjs/dukpy 桥执行）；
3. **存入缓存**  结果连同原文写入 ``purified_contents``，并记录规则指纹；
4. **调用**      再次请求时指纹一致直接返回缓存；规则变化时用原文本地
   重新净化（不回源）；抓取失败时兜底返回旧净化结果/本地书库正文。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..legado_rule.js_bridge import JsEvaluator, detect_engine
from ..models import PurifiedContent, PurifyPack, PurifyRule, utcnow
from .replace_rules import scope_allows

__all__ = [
    "BUILTIN_STAGE_TAG",
    "js_engine_available",
    "active_rules",
    "rules_fingerprint",
    "purify_text",
    "process_chapter",
    "parse_rule_payload",
    "pack_dict",
    "rule_dict",
]

# 内置层的版本标记：进入指纹。升级内置清洗逻辑时 bump 这个值即可让
# 全部缓存失效重净化。
BUILTIN_STAGE_TAG = "md3b1"

_JS_REPLACEMENT = re.compile(r"^@js:([\w\W]*)|<js>([\w\W]*?)</js>\s*$", re.IGNORECASE)


def js_engine_available() -> bool:
    return detect_engine() is not None


# --------------------------------------------------------------------- 内置
# 移植自 legado MD3 版 utils/HtmlFormatter.kt（formatKeepImg 的文本清洗部分）
_NBS = re.compile(r"(&nbsp;)+")
_ESP = re.compile(r"&ensp;|&emsp;")
_NO_PRINT = re.compile(r"&thinsp;|&zwnj;|&zwj;|\u2009|\u200c|\u200d")
_WRAP_HTML = re.compile(r"</?(?:div|p|br|hr|h\d|article|dd|dl)[^>]*>")
_COMMENT = re.compile(r"<!--[^>]*-->")  # noqa: DUO110 - 清洗用途
_NOT_IMG_HTML = re.compile(r"</?(?!img)[a-zA-Z]+(?=[ >])[^<>]*>")
_INDENT1 = re.compile(r"\s*\n+\s*")
_INDENT2 = re.compile(r"^[\n\s]+")
_TAIL_WS = re.compile(r"[\n\s]+$")

PARAGRAPH_INDENT = "　　"


def md3_builtin_clean(text: str) -> str:
    """内置净化层：MD3 版 formatKeepImg 的文本清洗（管线固定第一步）。"""
    if not text:
        return ""
    text = _NBS.sub(" ", text)
    text = _ESP.sub(" ", text)
    text = _NO_PRINT.sub("", text)
    text = _WRAP_HTML.sub("\n", text)
    text = _COMMENT.sub("", text)
    text = _NOT_IMG_HTML.sub("", text)
    text = _INDENT1.sub(f"\n{PARAGRAPH_INDENT}", text)
    text = _INDENT2.sub(PARAGRAPH_INDENT, text)
    text = _TAIL_WS.sub("", text)
    return text


# --------------------------------------------------------------- 规则读取
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


def _is_js_replacement(replacement: str) -> re.Match | None:
    return _JS_REPLACEMENT.match(replacement.strip())


def _run_js_replacement(matched: str, js_body: str, *, book_name: str,
                        chapter_title: str, source_url: str) -> str:
    """legado 语义：绑定 result=命中文本，脚本返回值替换命中部分。

    同步函数（在线程池里跑），JS 出错抛异常由调用方按坏规则跳过。
    """
    bindings: dict = {
        "result": matched,
        "book": {"name": book_name, "origin": source_url},
        "chapter": {"title": chapter_title},
    }
    evaluator = JsEvaluator(bindings)
    out = evaluator.eval(js_body)
    return "" if out is None else str(out)


async def active_rules(db: AsyncSession) -> list[PurifyRule]:
    """启用规则包中的激活规则，按 (包 order, 包 id, 规则 order, 规则 id) 排序。"""
    stmt = (
        select(PurifyRule)
        .join(PurifyPack, PurifyRule.pack_id == PurifyPack.id)
        .where(PurifyPack.enabled, PurifyRule.is_active)
        .order_by(PurifyPack.order, PurifyPack.id, PurifyRule.order, PurifyRule.id)
    )
    return list((await db.execute(stmt)).scalars().all())


def rules_fingerprint(rules: list[PurifyRule]) -> str:
    """规则集指纹：任一规则的内容/顺序变化都会改变缓存键。"""
    payload = [
        [r.pack_id, r.id, r.name, r.order, r.pattern, r.replacement,
         r.scope, bool(r.regex), bool(r.case_sensitive),
         bool(r.scope_content), bool(r.scope_title)]
        for r in rules
    ]
    blob = json.dumps([BUILTIN_STAGE_TAG] + payload,
                      ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


async def purify_text(
    text: str,
    rules: list[PurifyRule],
    *,
    book_name: str = "",
    source_url: str = "",
    source_name: str = "",
    chapter_title: str = "",
) -> tuple[str, list[str]]:
    """先内置净化，再按顺序应用规则；返回 (净化后文本, 命中规则名列表)。

    内置净化层（MD3 版 formatKeepImg 那一套）无条件先跑（管线第一步，
    版本标记进入指纹）；规则按 (order, id) 排序依次套用；单条规则有
    超时保护（防灾难性正则回溯 / 卡死 JS），坏规则只跳过自身。
    """
    text = md3_builtin_clean(text)
    applied: list[str] = []
    for r in sorted(rules, key=lambda x: (x.order, x.id)):
        if not (r.pattern or "").strip():
            continue
        if r.scope_content is False:
            continue  # 仅作用于标题的规则不进正文管线
        if not scope_allows(
            r.scope, book_name=book_name, source_url=source_url,
            source_name=source_name,
        ):
            continue
        try:
            replacement = r.replacement or ""
            js_match = _is_js_replacement(replacement)
            if js_match and not js_engine_available():
                continue  # 无 JS 引擎：跳过 JS 规则
            if r.regex:
                flags = 0 if r.case_sensitive else re.IGNORECASE

                def _sub(m: re.Match) -> str:
                    if js_match:
                        return _run_js_replacement(
                            m.group(0),
                            js_match.group(1) or js_match.group(2) or "",
                            book_name=book_name, chapter_title=chapter_title,
                            source_url=source_url,
                        )
                    return m.expand(replacement)

                text = await asyncio.wait_for(
                    asyncio.to_thread(_regex_sub, r.pattern, _sub, text, flags),
                    timeout=settings.replace_regex_timeout,
                )
            elif js_match:
                # 非正则 + JS 替换：把整段文本交给 JS 处理一次
                text = await asyncio.wait_for(
                    asyncio.to_thread(
                        _run_js_replacement, text,
                        js_match.group(1) or js_match.group(2) or "",
                        book_name=book_name, chapter_title=chapter_title,
                        source_url=source_url,
                    ),
                    timeout=settings.replace_regex_timeout,
                )
            elif r.case_sensitive:
                text = text.replace(r.pattern, replacement)
            else:
                text = _plain_replace_ci(text, r.pattern, replacement)
        except Exception:  # noqa: BLE001 - 坏规则/超时只影响自己
            continue
        applied.append(r.name or f"#{r.id}")
    return text, applied


async def process_chapter(
    db: AsyncSession,
    *,
    source_url: str,
    url: str,
    book_url: str = "",
    title: str = "",
    book_name: str = "",
    source_name: str = "",
    fetch_raw=None,
    local_fallback=None,
) -> tuple[str, bool]:
    """一章正文的完整管线，返回 (最终文本, 是否命中缓存)。

    - ``fetch_raw``       async () -> str：回源抓取原始正文（调用方注入）；
    - ``local_fallback``  async () -> str | None：抓取失败时的本地兜底
      （books 插件传本地书库 BookChapterContent）。
    """
    rules = await active_rules(db)
    fp = rules_fingerprint(rules)

    row = await db.scalar(
        select(PurifiedContent).where(
            PurifiedContent.source_url == source_url,
            PurifiedContent.url == url,
        )
    )

    # ---- 调用：指纹一致的缓存直接返回 ----
    if row is not None and row.fingerprint == fp and row.content:
        return row.content, True

    # ---- 规则已变化但存有原文：本地重新净化，不回源 ----
    if row is not None and row.fingerprint != fp and row.raw:
        text, applied = await purify_text(
            row.raw, rules, book_name=book_name,
            source_url=source_url, source_name=source_name,
            chapter_title=title,
        )
        row.content = text
        row.fingerprint = fp
        row.applied = applied
        row.updated_at = utcnow()
        await db.commit()
        return text, True

    # ---- 缓存未命中：获取内容 ----
    try:
        raw = await fetch_raw() if fetch_raw else ""
    except Exception:  # noqa: BLE001 - 断网兜底：旧净化结果 / 本地书库
        if row is not None and row.content:
            return row.content, True
        if local_fallback is not None:
            stale = await local_fallback()
            if stale:
                return stale, True
        raise

    # ---- 净化 + 存入数据库缓存 ----
    text, applied = await purify_text(
        raw, rules, book_name=book_name,
        source_url=source_url, source_name=source_name,
        chapter_title=title,
    )
    if row is None:
        db.add(PurifiedContent(
            source_url=source_url, book_url=book_url or "", url=url,
            title=title or "", raw=raw, content=text,
            fingerprint=fp, applied=applied,
        ))
    else:
        row.book_url = book_url or row.book_url
        row.title = title or row.title
        row.raw = raw
        row.content = text
        row.fingerprint = fp
        row.applied = applied
        row.updated_at = utcnow()
    await db.commit()
    return text, False


# ------------------------------------------------------------- 导入解析
def parse_rule_payload(obj: object) -> list[dict]:
    """把导入的规则 JSON 规范化为 PurifyRule 行字典。

    同时兼容两种导出格式：
    - 新版（乌云净化包）：isEnabled / isRegex / scopeContent / scopeTitle；
    - 旧版（全局替换规则）：isActive / regex / caseSensitive / scope。
    """
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
            "order": _as_int(it.get("order")),
            "is_active": _as_bool(it.get("isEnabled"), True)
            if "isEnabled" in it else _as_bool(it.get("isActive"), True),
            "pattern": pattern,
            "replacement": str(it.get("replacement") or ""),
            "scope": str(it.get("scope") or "")[:512],
            "regex": _as_bool(it.get("isRegex"), True)
            if "isRegex" in it else _as_bool(it.get("regex"), True),
            "case_sensitive": _as_bool(it.get("caseSensitive"), True),
            "scope_content": _as_bool(it.get("scopeContent"), True),
            "scope_title": _as_bool(it.get("scopeTitle"), False),
        })
    return out


def pack_dict(p: PurifyPack, rule_count: int | None = None) -> dict:
    d: dict = {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "origin": p.origin,
        "enabled": p.enabled,
        "order": p.order,
    }
    if rule_count is not None:
        d["ruleCount"] = rule_count
    return d


def rule_dict(r: PurifyRule) -> dict:
    return {
        "id": r.id,
        "packId": r.pack_id,
        "name": r.name,
        "order": r.order,
        "isActive": r.is_active,
        "pattern": r.pattern,
        "replacement": r.replacement,
        "scope": r.scope,
        "regex": r.regex,
        "caseSensitive": r.case_sensitive,
        "scopeContent": r.scope_content,
        "scopeTitle": r.scope_title,
    }
