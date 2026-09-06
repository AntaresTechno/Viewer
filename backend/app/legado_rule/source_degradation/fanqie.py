"""Fanqie (番茄小说) search and guest-read adapter.

The fanqie reading API gates its detail/toc endpoints behind a login
(returns a 200 empty body for an anonymous session), which breaks the generic
rule pipeline's ``JSON.parse`` and leaves content empty. The source itself has
visitor-usable web/relay endpoints; this adapter supplies them as a fallback so
a device-only registration can still read free books' toc & content.

Its configured search also depends on a third-party signing service. Search is
served through Fanqie's unsigned mobile endpoint here, while ``id:`` lookups and
unrecognized/error cases fall back to the source's original legado rules.

This module is the *only* place that may mention fanqie/byteimg domains. The
engine core talks to it solely through registered capability interfaces.
"""
from __future__ import annotations

import html as html_mod
import json
import re
from typing import Any
from urllib.parse import urlencode

from ..net import fetch
from .registry import register

_DOMAINS = ("reading.snssdk.com", "snssdk.com", "fanqienovel.com")
_RELAY_HOSTS = ("https://gofq.52dns.cc", "https://pyfq.52dns.cc")
_GUEST_UA = "Mozilla/5.0 (legado) Chrome/120.0.0.0"
# Guest-degraded toc chapters carry their itemId bare (a pure digit url);
# the API-rebuilt chapter dict drops the `_fq_*` marker, so match by shape.
_ITEM_ID_RE = re.compile(r"^\d{6,}$")
_SEARCH_ENDPOINT = (
    "https://novel.snssdk.com/api/novel/channel/homepage/"
    "search/search/v1/"
)
_SEARCH_PAGE_SIZE = 10


def _fq_domain(url: str) -> bool:
    base = str(url or "").split(",")[0]
    return any(d in base for d in _DOMAINS)


def _fq_book_id(url: str) -> str | None:
    m = re.search(r"[?&]book_id=(\d{6,})", str(url or ""))
    return m.group(1) if m else None


def _fq_unescape_url(u: str) -> str:
    return (u.replace("\\u002F", "/").replace("\\u002f", "/")
            .replace("\\/", "/"))


def _fq_replace_cover(u: str) -> str:
    """Replica of the source jsLib ``replaceCover``: turn a relative/thumbnail
    thumb_url into the ``https://p6-novel.byteimg.com/origin/...`` original.

    Key difference from the python side: the result has no ``?``/``&`` signature
    parameters, so the cover request is not truncated by ``&`` into a 403/400.
    """
    u = (u or "").strip()
    if not u:
        return ""
    if re.search(r"origin|reading", u):
        return u
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("https://"):
        u = u[8:]
    elif u.startswith("http://"):
        u = u[7:]
    arr: list[str] = u.split("/")
    if arr:
        arr[0] = "https://p6-novel.byteimg.com/origin"
    return "/".join(seg.split("~")[0] for seg in arr)


async def _fq_guest_fetch(url: str) -> str | None:
    try:
        resp = await fetch(
            url, method="GET",
            headers={"User-Agent": _GUEST_UA,
                     "Referer": "https://fanqienovel.com/"},
            charset="utf-8", cookie_jar=False,
        )
    except Exception:  # noqa: BLE001 - network trouble means not degraded
        return None
    if resp.error or not (resp.body or "").strip():
        return None
    return resp.body


def _fq_search_books(
    payload: str, source: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """把免签搜索接口映射成引擎统一 BookList；协议异常时返回 None。"""
    try:
        root = json.loads(payload)
        if root.get("code") != 0 or not isinstance(root.get("data"), dict):
            return None
        rows = root["data"].get("ret_data")
        if not isinstance(rows, list):
            return None
    except (AttributeError, TypeError, ValueError):
        return None

    books = [
        book for row in rows
        if isinstance(row, dict)
        for book in [_fq_book_from_row(row, source)]
        if book is not None
    ]
    return books


def _fq_category(row: dict[str, Any]) -> str:
    schema = row.get("category_schema")
    if isinstance(schema, str) and schema.strip():
        try:
            parsed = json.loads(schema)
            names = [
                str(item.get("name") or "").strip()
                for item in parsed if isinstance(item, dict)
            ]
            names = list(dict.fromkeys(name for name in names if name))
            if names:
                return ", ".join(names)
        except (TypeError, ValueError):
            pass
    tags = row.get("tags")
    if isinstance(tags, list):
        text = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
        if text:
            return text
    return str(tags or row.get("category") or "").strip()


def _fq_book_from_row(
    row: dict[str, Any], source: dict[str, Any]
) -> dict[str, Any] | None:
    book_id = str(
        row.get("relate_book_id") or row.get("book_id") or row.get("series_id") or ""
    ).strip()
    name = re.sub(
        r"</?em>", "", str(row.get("book_name") or row.get("title") or ""),
        flags=re.IGNORECASE,
    ).strip()
    if not book_id or not name:
        return None

    status = {"0": "完结", "1": "连载", "4": "断更", "-1": "未知"}.get(
        str(row.get("creation_status") or row.get("post_type") or ""), ""
    )
    score = str(row.get("score") or "").strip()
    kind = ", ".join(
        part for part in (_fq_category(row), status, f"{score}分" if score else "")
        if part
    )
    cover_info = row.get("cover_info")
    nested_cover = (
        cover_info.get("web_url") if isinstance(cover_info, dict) else ""
    )
    intro = str(
        row.get("abstract") or row.get("video_desc") or row.get("content") or ""
    ).strip()
    if "<" in intro:
        intro = html_mod.unescape(re.sub(r"<[^>]+>", "", intro)).strip()
    return {
        "name": name,
        "author": str(
            row.get("author") or row.get("copyright") or row.get("user_name") or ""
        ).strip(),
        "kind": kind,
        "wordCount": str(row.get("word_number") or "").strip(),
        "intro": intro,
        # The source rules apply replaceCover() too. p6-tt shrink URLs often
        # answer 403; the signature-free origin URL remains readable.
        "coverUrl": _fq_replace_cover(str(
            row.get("thumb_url") or row.get("cover") or nested_cover or ""
        ).strip()),
        "lastChapter": str(row.get("last_chapter_title") or "").strip(),
        "bookUrl": (
            "https://reading.snssdk.com/reading/bookapi/detail/v/"
            f"?book_id={book_id}"
        ),
        "origin": source.get("bookSourceUrl", ""),
        "originName": source.get("bookSourceName", ""),
    }


def _fq_nested_books(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Mirror ruleExplore's extraction of nested cell/book/video data."""
    for key in ("book_data", "video_data", "post_data"):
        value = item.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            return [value]
    groups = item.get("book_group_list")
    if isinstance(groups, list) and groups and isinstance(groups[0], dict):
        rows = groups[0].get("book_list")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return [item]


def _fq_explore_rows(root: dict[str, Any]) -> list[dict[str, Any]] | None:
    data = root.get("data")
    if not isinstance(data, dict):
        return None
    cell_view = data.get("cell_view")
    if isinstance(cell_view, dict):
        cells = cell_view.get("cell_data")
        if isinstance(cells, list) and cells:
            rows: list[dict[str, Any]] = []
            for cell in cells:
                if not isinstance(cell, dict):
                    continue
                nested = cell.get("cell_data")
                if isinstance(nested, list):
                    for sub in nested:
                        if isinstance(sub, dict):
                            rows.extend(_fq_nested_books(sub))
                else:
                    rows.extend(_fq_nested_books(cell))
            return rows
        direct = cell_view.get("book_data")
        if isinstance(direct, list):
            return [row for row in direct if isinstance(row, dict)]

    for value in (
        root.get("book_info"), root.get("detail_list"), data.get("video_info"),
        data.get("book_info"), data.get("book_data"), data.get("result"),
    ):
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return None


def _fq_parse_explore_books(
    payload: str, source: dict[str, Any]
) -> list[dict[str, Any]] | None:
    try:
        root = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(root, dict) or root.get("code", 0) != 0:
        return None
    rows = _fq_explore_rows(root)
    if rows is None:
        return None
    books = [
        book for row in rows
        for book in [_fq_book_from_row(row, source)]
        if book is not None
    ]
    # A recognized empty list is authoritative. A non-empty but entirely
    # unknown shape falls back to the original JS rules for compatibility.
    return books if books or not rows else None


async def _fq_search(
    source: dict[str, Any], key: str, page: int
) -> list[dict[str, Any]] | None:
    query = str(key or "").strip()
    # id: 搜索由原配置直接走详情规则，不应被关键词接口接管。
    if not query or query.lower().startswith("id:"):
        return None
    query = re.sub(r"^[smtd]:", "", query, flags=re.IGNORECASE).strip()
    if not query:
        return []
    params = urlencode({
        "device_platform": "android",
        "parent_enterfrom": "novel_channel_search.tab.",
        "offset": (max(1, page) - 1) * _SEARCH_PAGE_SIZE,
        "aid": "1967",
        "q": query,
    })
    body = await _fq_guest_fetch(f"{_SEARCH_ENDPOINT}?{params}")
    if body is None:
        return None
    return _fq_search_books(body, source)


async def _fq_guest_cover(source: dict[str, Any], book_url: str) -> str | None:
    """Visitor detail cover: when the detail API login-gates thumb_url away &
    no cover can be extracted, read the web page's __INITIAL_STATE__ thumbUri
    and rewrite it into a signature-free original via replaceCover."""
    if not _fq_domain(str(source.get("bookSourceUrl") or "")):
        return None
    bid = _fq_book_id(book_url)
    if not bid:
        return None
    page = await _fq_guest_fetch("https://fanqienovel.com/page/" + bid)
    if not page:
        return None
    m = re.search(r'"thumbUri"\s*:\s*"([^"]+)"', page)
    if not m:
        return None
    cover = _fq_replace_cover(_fq_unescape_url(m.group(1)))
    return cover if cover.startswith("http") else None


async def _fq_guest_toc(source: dict[str, Any], book: dict[str, Any],
                        toc_url: str, base_url: str) -> list[dict] | None:
    """Visitor web toc. Returns volume+chapter list, or None on failure."""
    bid = _fq_book_id(toc_url) if _fq_domain(toc_url) else None
    if not bid:
        return None
    body = await _fq_guest_fetch(
        "https://fanqienovel.com/api/reader/directory/detail?bookId=" + bid)
    if not body:
        return None
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return None
    chv = ((data or {}).get("data") or {}).get("chapterListWithVolume") or []
    chapters: list[dict] = []
    for vol in chv:
        if not isinstance(vol, list):
            continue
        for idx, entry in enumerate(vol):
            title = str((entry or {}).get("title") or "").strip()
            if not title:
                continue
            if idx == 0:
                vname = str((entry or {}).get("volume_name") or "").strip()
                if vname:
                    chapters.append({
                        "title": vname, "url": vname, "baseUrl": base_url,
                        "isVolume": True, "isVip": False,
                        "_fq_guest": True, "_fq_item_id": "",
                    })
            item_id = str((entry or {}).get("itemId") or "")
            chapters.append({
                "title": title, "url": item_id, "baseUrl": base_url,
                "isVolume": False,
                "isVip": bool((entry or {}).get("needPay")),
                "_fq_guest": True, "_fq_item_id": item_id,
            })
    return chapters or None


def _fq_xhtml_to_paragraphs(xhtml: str) -> str:
    paras = re.findall(r"<p[^>]*>([\s\S]*?)</p>", xhtml)
    if not paras:
        body = re.sub(r"^<\?xml[^>]*\?>", "", xhtml or "").strip()
        return re.sub(r"<[^>]+>", "", body).strip()
    out: list[str] = []
    for p in paras:
        t = html_mod.unescape(re.sub(r"<[^>]+>", "", p)).strip()
        t = re.sub(r"[\u3000\s]+", " ", t).lstrip()
        if t:
            out.append("\u3000\u3000" + t)
    return "\n".join(out)


async def _fq_guest_content(item_id: str) -> str | None:
    """Visitor content via relay endpoint(s). None when all fail."""
    if not item_id:
        return None
    for relay in _RELAY_HOSTS:
        body = await _fq_guest_fetch(relay + "/content?item_id=" + item_id)
        if not body:
            continue
        try:
            data = json.loads(body)
        except Exception:  # noqa: BLE001
            continue
        if data.get("code") != 0:
            continue
        cont = ((data or {}).get("data") or {}).get("content") or ""
        text = _fq_xhtml_to_paragraphs(cont)
        if text.strip():
            return text
    return None


class FanqieAdapter:
    """Search and guest-read capabilities for fanqie-domain sources."""

    def matches(self, source: dict[str, Any]) -> bool:
        if not _fq_domain(str(source.get("bookSourceUrl") or "")):
            return False
        extra = source.get("extra")
        if isinstance(extra, dict):
            adapters = extra.get("adapters")
            if isinstance(adapters, dict) and "guestRead" in adapters:
                return bool(adapters["guestRead"])
        return True  # default on: keeps legacy behaviour for fanqie sources

    def matches_search(self, source: dict[str, Any]) -> bool:
        if not _fq_domain(str(source.get("bookSourceUrl") or "")):
            return False
        extra = source.get("extra")
        if isinstance(extra, dict):
            adapters = extra.get("adapters")
            if isinstance(adapters, dict) and "search" in adapters:
                return bool(adapters["search"])
        return True

    def matches_explore(self, source: dict[str, Any]) -> bool:
        if not _fq_domain(str(source.get("bookSourceUrl") or "")):
            return False
        extra = source.get("extra")
        if isinstance(extra, dict):
            adapters = extra.get("adapters")
            if isinstance(adapters, dict) and "exploreParser" in adapters:
                return bool(adapters["exploreParser"])
        return True

    def parse_explore(self, source, payload, base_url):
        return _fq_parse_explore_books(payload, source)

    async def search(self, source, key, page):
        return await _fq_search(source, key, page)

    async def guest_cover(self, source, book_url):
        return await _fq_guest_cover(source, book_url)

    async def guest_toc(self, source, book, toc_url, base_url):
        return await _fq_guest_toc(source, book, toc_url, base_url)

    async def guest_content(self, source, chapter):
        ch_url = str(chapter.get("url") or "")
        item_id = str(chapter.get("_fq_item_id") or ch_url)
        return await _fq_guest_content(item_id)

    def is_guest_chapter(self, source, chapter, ch_url):
        return bool(chapter.get("_fq_guest")) or (
            _fq_domain(str(source.get("bookSourceUrl") or ""))
            and bool(_ITEM_ID_RE.match(ch_url))
        )


register(FanqieAdapter())
