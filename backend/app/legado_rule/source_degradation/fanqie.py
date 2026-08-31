"""Fanqie (番茄小说) guest-read adapter.

The fanqie reading API gates its detail/toc endpoints behind a login
(returns a 200 empty body for an anonymous session), which breaks the generic
rule pipeline's ``JSON.parse`` and leaves content empty. The source itself has
visitor-usable web/relay endpoints; this adapter supplies them as a fallback so
a device-only registration can still read free books' toc & content.

This module is the *only* place that may mention fanqie/byteimg domains. The
engine core talks to it solely through ``GuestReadAdapter``.
"""
from __future__ import annotations

import html as html_mod
import json
import re
from typing import Any

from ..net import fetch
from .interfaces import GuestReadAdapter
from .registry import register

_DOMAINS = ("reading.snssdk.com", "snssdk.com", "fanqienovel.com")
_RELAY_HOSTS = ("https://gofq.52dns.cc", "https://pyfq.52dns.cc")
_GUEST_UA = "Mozilla/5.0 (legado) Chrome/120.0.0.0"
# Guest-degraded toc chapters carry their itemId bare (a pure digit url);
# the API-rebuilt chapter dict drops the `_fq_*` marker, so match by shape.
_ITEM_ID_RE = re.compile(r"^\d{6,}$")


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


class FanqieGuestReadAdapter:
    """Guest-read fallback for fanqie-domain sources (self-registered)."""

    def matches(self, source: dict[str, Any]) -> bool:
        if not _fq_domain(str(source.get("bookSourceUrl") or "")):
            return False
        extra = source.get("extra")
        if isinstance(extra, dict):
            adapters = extra.get("adapters")
            if isinstance(adapters, dict) and "guestRead" in adapters:
                return bool(adapters["guestRead"])
        return True  # default on: keeps legacy behaviour for fanqie sources

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


register(FanqieGuestReadAdapter())