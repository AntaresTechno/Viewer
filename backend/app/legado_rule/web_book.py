"""Web-book pipeline: search / book info / toc / content on top of AnalyzeRule.

Mirrors model/webBook/{BookList,BookInfo,BookChapterList,BookContent}.kt.
"""
from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import re
import time
from typing import Any

from .analyze_rule import AnalyzeRule
from .analyze_url import AnalyzeUrl, get_absolute_url
from .exceptions import FetchError, RuleError
from .explore_ui import normalize_style
from .net import StrResponse, fetch
from .source_degradation import guest_reader_for, load_builtin

# Import/register bundled source-capability adapters (e.g. guest-read fallback).
# The engine core only asks "is there an adapter for this source?" and never
# branches on concrete book-source domains.
load_builtin()

_LOG = logging.getLogger("viewer.legado.web_book")


def _log_list_response(source: dict, res: StrResponse, *, kind: str) -> None:
    """请求级日志：最终 URL / HTTP 状态 / 响应长度，空响应高亮为 WARNING。

    空 body 是"<js> 列表规则 JSON.parse 炸成 unexpected end of input / 搜不到
    东西"的常见根因（接口门禁、签名失败、限流都会回空）。这里把它显式打出来，
    便于一眼分辨是"请求 URL 不对"还是"接口回空"。与书源无关，通用记录。
    """
    name = str(source.get("bookSourceName")
               or source.get("bookSourceUrl") or "")
    body = res.body or ""
    size = len(body)
    if size and body.strip():
        _LOG.debug("[books] %s 响应: source=%s status=%s bytes=%d url=%s",
                   kind, name, res.status, size, res.url)
        return
    # 空/纯空白：WARNING（未配置 handler 时也会经 lastResort 输出到 stderr）
    # 响应头里常有风控/限流线索（content-length、x-tt-logid 等），一并附上。
    hdrs = res.headers or {}
    summary = "; ".join(f"{k}={v}" for k, v in list(hdrs.items())[:12])
    head_txt = f" | 响应头: {summary[:600]}" if summary else ""
    _LOG.warning(
        "[books] %s 响应为空: source=%s status=%s bytes=%d url=%s "
        "-> 空 body 无法解析，已短路为空列表%s",
        kind, name, res.status, size, res.url, head_txt)


def _as_dict(value: Any) -> dict:
    """Rule objects may be dicts or JSON strings (double-parse)."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            obj = json.loads(value)
            return obj if isinstance(obj, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _settings():
    from app.core.config import settings

    return settings


async def fetch_str(analyze_url: AnalyzeUrl) -> StrResponse:
    spec = analyze_url.spec()
    resp = await fetch(
        spec.url,
        method=spec.method,
        headers=spec.headers,
        body=spec.body,
        charset=spec.charset,
        retries=spec.retry,
        cookie_jar=spec.cookie_jar,
    )
    if spec.web_view:
        resp.error = resp.error or "该 URL 需要 webView 渲染，服务端暂不支持"
    if spec.body_js and resp.error is None:
        from .js_bridge import eval_js

        ev = eval_js(
            spec.body_js,
            {"result": resp.body, "baseUrl": analyze_url.base_url,
             "source": analyze_url.source or None,
             "__bridge__": _StubBridge()},
        )
        resp.body = "" if ev is None else str(ev)
    if resp.error is None:
        await asyncio.to_thread(
            _apply_login_check_js, analyze_url.source or {}, analyze_url, resp
        )
    return resp


def _apply_login_check_js(source: dict, analyze_url: AnalyzeUrl,
                          resp: StrResponse) -> None:
    """loginCheckJs：每次抓取后检测登录态，JS 可整体替换响应 body。

    legado 把 StrResponse 对象交给 JS 并取回（``evalJS(checkJs, res)``）；
    Python 侧引擎无法回传活对象，这里绑定 ``result = {url, body, code}``，
    返回非空字符串/含 body 的对象时替换 resp.body —— 覆盖「未登录则
    重取登录页」类规则的常见用法。
    """
    check_js = str(source.get("loginCheckJs") or "").strip()
    if not check_js:
        return
    from .js_bridge import eval_js
    from .source_bridge import SourceLoginBridge, bridges_for

    result = {"url": resp.url, "body": resp.body, "code": resp.status}
    try:
        ev = eval_js(check_js, {
            "result": result,
            "baseUrl": analyze_url.base_url,
            "source": source or None,
            "book": analyze_url.rule_data,
            "page": analyze_url.page,
            "key": analyze_url.key,
            "__bridge__": SourceLoginBridge(
                source, base_url=analyze_url.base_url
            ),
            "__ns__": bridges_for(source),
        })
    except Exception:  # noqa: BLE001 - 检测脚本失败不影响原响应
        return
    if isinstance(ev, str) and ev.strip():
        resp.body = ev
    elif isinstance(ev, dict) and isinstance(ev.get("body"), str) and ev["body"]:
        resp.body = ev["body"]


class _StubBridge:
    pass


# --------------------------------------------------------------------- util
_IMG_RE = re.compile(r"<img[^>]*>", re.IGNORECASE)
_SRC_RE = re.compile(r"""src\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<(script|style)[\s\S]*?</\1>", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BLOCK_RE = re.compile(
    r"</?(?:p|div|section|article|h[1-6]|li|tr|blockquote|pre|td|th)[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def format_content_keep_img(raw: str, base_url: str) -> str:
    images: list[str] = []

    def _img(m: re.Match) -> str:
        sm = _SRC_RE.search(m.group(0))
        src = sm.group(1) if sm else ""
        absu = get_absolute_url(base_url, src) if src else ""
        images.append(f'<img src="{absu}">')
        return f"\x00IMG{len(images) - 1}\x00"

    text = _SCRIPT_RE.sub("", raw)
    text = _IMG_RE.sub(_img, text)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    if "&" in text:
        text = html_mod.unescape(text)

    out: list[str] = []
    blank = 0
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        out.append(re.sub(r"\x00IMG(\d+)\x00", lambda m: images[int(m.group(1))], ln))
    return "\n".join(out).strip("\n")


# -------------------------------------------------------------------- search
async def _fetch_book_list(
    source: dict, url: str, *, key: str | None, page: int, rules: dict,
    is_search: bool = False,
) -> list[dict]:
    """Fetch one list page and parse it as books with the given rule map.

    ``url`` is a raw legado URL rule (may contain {{key}} / {{page}} / options).
    """
    if not url:
        raise RuleError("书源未配置列表/搜索地址")
    aurl = AnalyzeUrl(
        url,
        key=key,
        page=page,
        base_url=source.get("bookSourceUrl", ""),
        source=source,
    )
    res = await fetch_str(aurl)
    if res.error:
        raise FetchError(res.error, res.status)
    # 先记录请求级信息，再解析：空响应/异常 URL 一眼可见。
    _log_list_response(source, res,
                       kind="search" if is_search else "explore")
    # 规则求值可能内嵌同步 java.ajax（dukpy 无法 await），放到线程池执行，
    # 避免阻塞事件循环拖慢所有并发请求。
    return await asyncio.to_thread(
        _parse_book_list, source, rules, res.url, res.body,
        is_search=is_search,
    )


def _parse_info_page(source: dict, body: str, url: str) -> dict:
    """Parse an already-fetched page as a single BookInfo (ruleBookInfo only).

    Mirrors the getInfoItem fallback of legado's BookList: a fresh book whose
    bookUrl is the final (possibly redirected) page url, fields from
    ruleBookInfo. Returns {} when no name could be extracted.
    """
    book = {
        "bookUrl": url,
        "origin": source.get("bookSourceUrl", ""),
        "originName": source.get("bookSourceName", ""),
    }
    info = _apply_book_info_rules(source, book, body, url)
    if not str(info.get("name") or "").strip():
        return {}
    return info


def _parse_book_list(
    source: dict, rules: dict, base_url: str, body: str,
    *, is_search: bool = False,
) -> list[dict]:
    books: list[dict] = []
    pattern = str(source.get("bookUrlPattern") or "")

    # 空/纯空白响应：无内容可解析。对 `<js>` 型 list_rule 而言，其内置的
    # `JSON.parse(result)` 会把空串直接抛成隐晦的 "SyntaxError: unexpected
    # end of input"（把整源标记为失败，如搜索接口空返回时）。这里干净地
    # 短路为空列表，语义即"该接口这次没回数"。
    if not str(body or "").strip():
        return []

    # legado BookList: 搜索结果重定向到详情页（URL 命中 bookUrlPattern）时，
    # 直接按详情页规则解析出单本结果。
    if is_search and pattern:
        try:
            redirected = re.fullmatch(pattern, base_url) is not None
        except re.error:
            redirected = False
        if redirected:
            one = _parse_info_page(source, body, base_url)
            return [one] if one else []

    ar = AnalyzeRule(source=source, base_url=base_url)
    ar.set_content(body, base_url=base_url)
    list_rule = rules.get("bookList") or ""

    def _fields(ar: AnalyzeRule) -> dict:
        return {
            "name": (ar.get_string(rules.get("name") or "") or "").strip(),
            "author": (ar.get_string(rules.get("author") or "") or "").strip(),
            "kind": ", ".join(ar.get_string_list(rules.get("kind") or "") or []),
            "wordCount": (ar.get_string(rules.get("wordCount") or "") or "").strip(),
            "intro": (ar.get_string(rules.get("intro") or "") or "").strip(),
            "coverUrl": ar.get_string(rules.get("coverUrl") or "", is_url=True),
            "lastChapter": (ar.get_string(rules.get("lastChapter") or "") or "").strip(),
            "bookUrl": ar.get_string(rules.get("bookUrl") or "", is_url=True),
            "origin": source.get("bookSourceUrl", ""),
            "originName": source.get("bookSourceName", ""),
        }

    if not list_rule:
        # bookList 为空：整页按单条结果兜底（legado 兼容行为）
        one = _fields(ar)
        if one["name"]:
            books.append(one)
        return books

    for el in ar.get_elements(list_rule):
        try:
            item_ar = AnalyzeRule(source=source, base_url=base_url)
            item_ar.set_content(el, base_url=base_url)
            fields = _fields(item_ar)
            if not fields["name"]:
                continue
            books.append(fields)
        except Exception:  # noqa: BLE001 - per-item tolerance like legado
            continue

    # legado BookList: 列表为空且书源未配 bookUrlPattern 时，按详情页兜底解析
    if not books and not pattern:
        one = _parse_info_page(source, body, base_url)
        if one:
            books.append(one)
    return books


async def search_book(source: dict, key: str, page: int = 1,
                      explore_url: str | None = None) -> list[dict]:
    search_url = explore_url if explore_url else source.get("searchUrl", "")
    if not search_url:
        raise RuleError("书源未配置搜索地址")
    rules = _as_dict(source.get("ruleSearch"))
    return await _fetch_book_list(source, search_url, key=key, page=page,
                                  rules=rules, is_search=True)


async def explore_book(source: dict, url: str, page: int = 1) -> list[dict]:
    """发现版面抓取：规则优先 ruleExplore，bookList 空白时回退 ruleSearch。"""
    rules = _as_dict(source.get("ruleExplore"))
    if not (rules or {}).get("bookList"):
        rules = _as_dict(source.get("ruleSearch"))
    return await _fetch_book_list(source, url, key=None, page=page, rules=rules)


# ------------------------------------------------------------------ explore
_KIND_SPLIT_RE = re.compile(r"(?:&&|\n)+")
_KIND_KEYS = ("title", "url", "type", "action", "chars", "default",
              "viewName", "style")

# 发现分类缓存：legado 用 aCache + exploreKindsMap 做同样的事（见
# explore_kinds 的 docstring）。进程内即可 —— 它只是为了避免同一用户在
# 发现页里点一次控件就重跑整页签名请求，不做跨进程共享。
_KINDS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_KINDS_TTL = 600.0


def _kind_js_body(explore_url: str) -> str:
    """Extract the JS body from a <js>/@js: exploreUrl (mirrors BookSourceExtensions)."""
    if explore_url[0] == "@":
        return explore_url[4:]
    end = explore_url.rfind("<")
    return explore_url[4:] if end <= 4 else explore_url[4:end]


def _kind_from_obj(it: dict) -> dict:
    """JSON 数组里的一项 → ExploreKind。

    只在书源**真的给了** style 时才补全成完整 FlexChildStyle：多数书源不带
    style，凭空补一个会塞满无意义的默认值，也给既有调用方/测试造成噪音。
    """
    kind = {k: it[k] for k in _KIND_KEYS if k in it}
    kind.setdefault("type", "url")
    if "style" in it:
        kind["style"] = normalize_style(it["style"])
    return kind


def _kinds_cache_key(source: dict) -> str:
    explore_url = source.get("exploreUrl")
    if not isinstance(explore_url, str) or not explore_url.strip():
        return ""
    return f"{source.get('bookSourceUrl') or ''}::{explore_url}"


def explore_kinds(source: dict) -> list[dict]:
    """Parse a book source's exploreUrl into its category (ExploreKind) list.

    Mirrors BookSourceExtensions.exploreKinds(): optional <js>/@js: to build the
    string, then JSON array or ``标题::url`` lines separated by ``&&``/newline.

    结果按 (bookSourceUrl, exploreUrl) 缓存：legado 用 ``aCache`` +
    ``exploreKindsMap`` 做同样的事（key 是 md5(sourceUrl+exploreUrl)），
    只在 ``refreshExplore`` 时失效。番茄这类书源的动态发现页要跑十几次
    签名请求（实测 15s+），不缓存的话每次点控件都重跑整页。
    """
    cache_key = _kinds_cache_key(source)
    if cache_key:
        hit = _KINDS_CACHE.get(cache_key)
        if hit is not None:
            stamp, kinds = hit
            if time.monotonic() - stamp < _KINDS_TTL:
                return [dict(k) for k in kinds]

    kinds = _explore_kinds_uncached(source)
    if cache_key and kinds:
        _KINDS_CACHE[cache_key] = (time.monotonic(), [dict(k) for k in kinds])
    return kinds


def invalidate_explore_kinds(source: dict | None = None) -> None:
    """失效发现分类缓存（``refreshExplore`` / 书源编辑后调用）。

    ``source`` 为 None 时清空全部（书源被删/改时逐条失效更麻烦，
    且发现分类缓存本就是短期缓存）。
    """
    if source is None:
        _KINDS_CACHE.clear()
        return
    key = _kinds_cache_key(source)
    if key:
        _KINDS_CACHE.pop(key, None)


def _explore_kinds_uncached(source: dict) -> list[dict]:
    explore_url = source.get("exploreUrl")
    if not explore_url or not isinstance(explore_url, str) or not explore_url.strip():
        return []

    rule_str: str = explore_url.strip()
    low = rule_str.lower()
    if low.startswith("<js>") or low.startswith("@js:"):
        from .js_bridge import JavaBridge, eval_js
        from .source_bridge import InfoMapBridge, bridges_for

        ns = bridges_for(source)
        # legado 把 InfoMap 实例绑成 `infoMap`（AnalyzeUrl.evalJS）；发现页
        # JS 会读 infoMap['关键词：'] 并在 saveKeys(infoMap) 里调用
        # .set()/.save()，纯 dict 会直接 TypeError。
        ns["infoMap"] = InfoMapBridge(source)
        # 发现页 JS 常直接 java.ajax(...) 预热榜单/分类（番茄书源即如此），
        # 这里必须是真 JavaBridge —— 传空壳会让 java.ajax 变成 undefined。
        base_url = str(source.get("bookSourceUrl") or "")
        val = eval_js(
            _kind_js_body(rule_str),
            {"infoMap": {}, "source": source,
             "baseUrl": base_url,
             "__bridge__": JavaBridge(owner=None, base_url=base_url,
                                      source=source),
             "__ns__": ns},
        )
        rule_str = "" if val is None else str(val)

    text = rule_str.strip()
    kinds: list[dict] = []
    if text.startswith("["):
        try:
            arr = json.loads(text)
        except Exception:  # noqa: BLE001 - fall through to line split
            arr = None
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict):
                    kinds.append(_kind_from_obj(it))
                elif isinstance(it, str):
                    kinds.append({"title": it, "url": None, "type": "url"})
            return kinds

    for line in _KIND_SPLIT_RE.split(text):
        line = line.strip()
        if not line:
            continue
        parts = line.split("::")
        kinds.append({
            "title": parts[0].strip(),
            "url": parts[1].strip() if len(parts) > 1 and parts[1].strip() else None,
            "type": "url",
        })
    return kinds


# ------------------------------------------------------------------ bookinfo
def _meta_contents(html_text: str) -> dict[str, str]:
    """All <meta> tag contents keyed by lowercased name/property."""
    out: dict[str, str] = {}
    for m in re.finditer(r"<meta\b[^>]*>", html_text or "", re.I):
        tag = m.group(0)
        key = re.search(r"""(?:property|name)=["']([^"']+)["']""", tag, re.I)
        val = re.search(r"""content=["']([^"']*)["']""", tag, re.I)
        if key and val:
            out.setdefault(key.group(1).lower(), val.group(1))
    return out


def _fallback_intro(html_text: str, book_name: str) -> str:
    """书源未配 intro 规则时的通用兜底：取页面 og:description / description。

    不少站点把简介放在 meta 标签里；顺带去掉常见的「书名简介：」前缀。
    """
    metas = _meta_contents(html_text)
    text = (metas.get("og:description") or metas.get("description") or "").strip()
    if not text:
        return ""
    text = html_mod.unescape(text)
    if book_name:
        text = re.sub(rf"^{re.escape(book_name)}\s*简介\s*[:：]\s*", "", text).strip()
        # 「XX最新章节无错更新，…提供XX全文免费在线阅读」一类的推广句不算简介
        if f"{book_name}最新章节" in text or "全文免费在线阅读" in text:
            return ""
    if len(text) < 10:
        return ""
    return text


def _apply_book_info_rules(source: dict, book: dict, body: str,
                           url: str) -> dict:
    """Run ruleBookInfo over an already-fetched page and merge into ``book``."""
    ar = AnalyzeRule(book=book, source=source, base_url=url)
    ar.set_content(body, base_url=url)
    rbi = _as_dict(source.get("ruleBookInfo"))

    # legado 的 BookInfo 先跑 init（AnalyzeRule.getElement，可重写 result /
    # book.bookUrl / 抛错中断），再逐字段解析。番茄书源把「短链跳转取
    # book_id、选择详情接口、自动发书评」都放在 init 里 —— 不执行 init，
    # 后面的字段规则拿到的是未经处理的原始响应，name 等规则会全部落空。
    init_rule = rbi.get("init") or ""
    if init_rule.strip():
        try:
            init_out = ar.get_element(init_rule)
            if init_out is not None and not (
                isinstance(init_out, str) and not init_out.strip()
            ):
                # init 可以整体替换后续字段规则所见的响应内容
                ar.set_content(init_out, base_url=url)
        except RuleError:
            raise
        except Exception as exc:  # noqa: BLE001 - legado 也允许 init 抛错中断
            raise RuleError(f"ruleBookInfo.init 执行失败: {exc}") from exc

    def gs(field: str, is_url: bool = False) -> str:
        return ar.get_string(rbi.get(field) or "", is_url=is_url) if field else ""

    info = dict(book)
    can_re_name = bool(gs("canReName").strip()) if rbi.get("canReName") else False
    name = gs("name").strip()
    author = gs("author").strip()
    if name and (can_re_name or not info.get("name")):
        info["name"] = name
    if author and (can_re_name or not info.get("author")):
        info["author"] = author
    info["kind"] = ", ".join(ar.get_string_list(rbi.get("kind") or "") or [])
    intro = gs("intro")
    if "&" in intro:
        intro = html_mod.unescape(intro)
    if intro.strip():
        info["intro"] = intro.strip()
    elif not rbi.get("intro"):
        # 书源没配简介规则（或规则为空）时，退回页面 meta 描述
        fb = _fallback_intro(body, str(info.get("name") or book.get("name") or ""))
        if fb:
            info["intro"] = fb
    cover = gs("coverUrl", is_url=True)
    if cover:
        info["coverUrl"] = cover
    last_chapter = gs("lastChapter")
    if last_chapter:
        info["lastChapter"] = last_chapter
    toc_url = gs("tocUrl", is_url=True)
    info["tocUrl"] = toc_url.strip() or (url or book.get("bookUrl") or "")
    return info


async def book_info(source: dict, book: dict) -> dict:
    book_url = book.get("bookUrl") or ""
    aurl = AnalyzeUrl(
        book_url,
        base_url=source.get("bookSourceUrl", ""),
        source=source,
        rule_data=book,
    )
    res = await fetch_str(aurl)
    if res.error:
        raise FetchError(res.error, res.status)
    info = await asyncio.to_thread(
        _apply_book_info_rules, source, book, res.body, res.url)
    # 访客封面兜底：detail API 登录门禁无 thumb_url 时，交给本源适配器
    ad = guest_reader_for(source)
    if not info.get("coverUrl") and ad is not None:
        fb = await ad.guest_cover(source, book_url)
        if fb:
            info["coverUrl"] = fb
    return info


# ----------------------------------------------------------------------- toc
def _parse_toc_page_sync(
    source: dict, book: dict, rule_toc: dict, list_rule: str,
    base_url: str, body: str, redirect_url: str,
    collect_next: bool,
) -> tuple[list[dict], list[str]]:
    """Parse one toc page into (chapters_of_this_page, next_page_urls).

    Sync on purpose: runs inside a worker thread via asyncio.to_thread so any
    embedded JS (chapterName @js / nextTocUrl <js>) cannot stall the loop.
    """
    ar = AnalyzeRule(book=book, source=source, base_url=redirect_url)
    ar.set_content(body, base_url=base_url)

    name_rules = ar._split_source_rule(rule_toc.get("chapterName") or "")  # noqa: SLF001
    url_rules = ar._split_source_rule(rule_toc.get("chapterUrl") or "")  # noqa: SLF001
    vol_rules = ar._split_source_rule(rule_toc.get("isVolume") or "")  # noqa: SLF001
    vip_rules = ar._split_source_rule(rule_toc.get("isVip") or "")  # noqa: SLF001

    page_chapters: list[dict] = []
    elements = ar.get_elements(list_rule)
    for index, el in enumerate(elements):
        try:
            ar.set_content(el, base_url=redirect_url)
            title = ar.get_string_from_list(name_rules)
            url = ar.get_string_from_list(url_rules)
            is_volume = bool(ar.get_string_from_list(vol_rules).strip())
            if not url:
                url = f"{title}{index}" if is_volume else redirect_url
            if not title:
                continue
            page_chapters.append({
                "title": title,
                "url": url,
                "baseUrl": redirect_url,
                "isVolume": is_volume,
                "isVip": bool(ar.get_string_from_list(vip_rules).strip()),
            })
        except Exception:  # noqa: BLE001
            continue

    next_rule = rule_toc.get("nextTocUrl") or ""
    nxt: list[str] = []
    if collect_next and next_rule:
        ar.set_content(body, base_url=base_url)
        got = ar.get_string_list(next_rule, is_url=True) or []
        nxt = [u for u in got if u != redirect_url]
    return page_chapters, nxt


async def get_toc(source: dict, book: dict, toc_url: str) -> list[dict]:
    rule_toc = _as_dict(source.get("ruleToc"))
    list_rule = rule_toc.get("chapterList") or ""
    reverse_prefix = list_rule.startswith("-")
    if list_rule[:1] in ("-", "+"):
        list_rule = list_rule[1:]

    chapters: list[dict] = []
    seen_pages = [toc_url]
    limit = _settings().toc_page_limit

    first = AnalyzeUrl(toc_url, base_url=toc_url.split(",")[0],
                       source=source, rule_data=book)
    res = await fetch_str(first)
    if res.error:
        raise FetchError(res.error, res.status)
    try:
        page_chs, nxt = await asyncio.to_thread(
            _parse_toc_page_sync, source, book, rule_toc, list_rule,
            res.url, res.body, res.url, True,
        )
    except Exception:  # noqa: BLE001 - 登录门禁的空 body 会让 JSON.parse 抛错
        page_chs, nxt = [], []
    chapters.extend(page_chs)

    # 本源适配器提供的访客降级（如登录门禁的详情/目录）在主线拿空时兜底
    ad = guest_reader_for(source)
    if not chapters and ad is not None:
        guest = await ad.guest_toc(source, book, res.url or toc_url,
                                   res.url or toc_url)
        if guest:
            for i, ch in enumerate(guest):
                ch["index"] = i
            return guest
    if not chapters:
        raise RuleError("章节列表为空")

    guard = 0
    while nxt:
        if len(nxt) == 1:
            nu = nxt[0]
            if not nu or nu in seen_pages or guard >= limit:
                break
            guard += 1
            seen_pages.append(nu)
            aurl = AnalyzeUrl(nu, base_url=nu.split(",")[0], source=source,
                              rule_data=book)
            r2 = await fetch_str(aurl)
            if r2.error:
                break
            page_chs, nxt = await asyncio.to_thread(
                _parse_toc_page_sync, source, book, rule_toc, list_rule,
                r2.url, r2.body, r2.url, True,
            )
            chapters.extend(page_chs)
        else:
            # 多页并行抓取：每页各自解析成块，gather 按页序合并，避免乱序
            fresh = [u for u in nxt if u and u not in seen_pages]
            for u in fresh:
                seen_pages.append(u)
            sem = asyncio.Semaphore(max(1, _settings().parser_concurrency))

            async def one(u: str) -> list[dict]:
                async with sem:
                    a3 = AnalyzeUrl(u, base_url=u.split(",")[0], source=source,
                                    rule_data=book)
                    r3 = await fetch_str(a3)
                    if r3.error:
                        return []
                    chs, _ = await asyncio.to_thread(
                        _parse_toc_page_sync, source, book, rule_toc,
                        list_rule, r3.url, r3.body, r3.url, False,
                    )
                    return chs

            for chunk in await asyncio.gather(*[one(u) for u in fresh]):
                chapters.extend(chunk)
            break

    if not chapters:
        raise RuleError("章节列表为空")

    if not reverse_prefix:
        chapters.reverse()
    seen: set[str] = set()
    deduped: list[dict] = []
    for ch in chapters:
        marker = ch["url"]
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(ch)
    reverse_toc = bool(book.get("reverseToc"))
    if not reverse_toc:
        deduped.reverse()
    for i, ch in enumerate(deduped):
        ch["index"] = i
    return deduped


# ------------------------------------------------------------------- content
async def get_content(source: dict, book: dict, chapter: dict,
                      next_chapter_url: str | None = None,
                      base_url: str | None = None) -> str:
    # 分卷标题没有正文；点了分卷头（如「第一卷：初到提瓦特」）直接返回空，
    # 避免把分卷名当 URL 去抓而 502。
    if chapter.get("isVolume"):
        return ""
    # 本源适配器产出的访客章节：正文直接走备用读取路径（免登录）。
    # 须在跑 JS 前拦下（书源 content JS 自带 `let result` 与引擎注入绑定冲突）。
    ch_url = str(chapter.get("url") or "")
    ad = guest_reader_for(source)
    if ad is not None and ad.is_guest_chapter(source, chapter, ch_url):
        text = await ad.guest_content(source, chapter)
        if not text:
            raise RuleError("内容为空")
        return text
    rule_content = _as_dict(source.get("ruleContent"))
    chapter_url = chapter.get("url") or ""
    # chapter urls may be relative; resolve against the toc page they came from
    aurl = AnalyzeUrl(chapter_url, base_url=base_url or chapter_url.split(",")[0],
                      source=source, rule_data=book)
    if not aurl.url.startswith(("http://", "https://")):
        origin = str(source.get("bookSourceUrl") or "").split(",")[0]
        aurl.url = get_absolute_url(origin, aurl.url)
    res = await fetch_str(aurl)
    if res.error:
        raise FetchError(res.error, res.status)

    texts: list[str] = []

    def parse_page(url: str, body: str, redirect: str,
                   collect_next: bool) -> tuple[str, list[str]]:
        """Sync on purpose: content rules often embed java.ajax (blocking);
        run inside a worker thread so the event loop stays responsive."""
        ar = AnalyzeRule(book=book, source=source, base_url=url)
        ar.chapter_title = chapter.get("title")
        ar.next_chapter_url = next_chapter_url
        ar.set_content(body, base_url=url)
        raw = ar.get_string(rule_content.get("content") or "", unescape=False)
        text = format_content_keep_img(raw, redirect or url)
        nxt: list[str] = []
        if collect_next and rule_content.get("nextContentUrl"):
            got = ar.get_string_list(rule_content["nextContentUrl"], is_url=True) or []
            nxt = [u for u in got if u and u != redirect]
        return text, nxt

    text1, nxt1 = await asyncio.to_thread(parse_page, res.url, res.body, res.url, True)
    texts.append(text1)
    page_count = 1

    def append(text: str) -> None:
        nonlocal page_count
        if page_count > 0 and text:
            texts.append("")
        if text:
            texts.append(text)
        page_count += 1

    visited = [chapter_url]
    if len(nxt1) == 1:
        next_url = nxt1[0]
        guard = 0
        while next_url and next_url not in visited \
                and guard <= _settings().content_page_limit:
            guard += 1
            base_for_abs = res.url
            if next_chapter_url and get_absolute_url(base_for_abs, next_url) == \
                    get_absolute_url(base_for_abs, next_chapter_url):
                break
            visited.append(next_url)
            a2 = AnalyzeUrl(next_url, base_url=next_url.split(",")[0],
                            source=source, rule_data=book)
            r2 = await fetch_str(a2)
            if r2.error:
                break
            t2, n2 = await asyncio.to_thread(parse_page, r2.url, r2.body, r2.url, True)
            append(t2)
            next_url = n2[0] if n2 else ""
    elif len(nxt1) > 1:
        fresh = [u for u in nxt1 if u not in visited]
        visited.extend(fresh)
        sem = asyncio.Semaphore(max(1, _settings().parser_concurrency))

        async def one(u: str) -> str:
            async with sem:
                a3 = AnalyzeUrl(u, base_url=u.split(",")[0], source=source,
                                rule_data=book)
                r3 = await fetch_str(a3)
                if r3.error:
                    return ""
                t3, _ = await asyncio.to_thread(parse_page, r3.url, r3.body, r3.url, False)
                return t3

        for t in await asyncio.gather(*[one(u) for u in fresh]):
            append(t)

    content_str = "\n".join(texts)

    replace_regex = rule_content.get("replaceRegex") or ""
    if replace_regex:
        content_str = "\n".join(l.rstrip() for l in content_str.splitlines())
        ar = AnalyzeRule(book=book, source=source, base_url=res.url)
        ar.set_content(content_str)
        try:
            content_str = ar.get_string(replace_regex)
        except Exception:  # noqa: BLE001
            pass
        content_str = "\n".join(
            "　　" + l for l in content_str.splitlines() if l.strip()
        )
    if not chapter.get("isVolume") and not content_str.strip():
        raise RuleError("内容为空")
    return content_str
