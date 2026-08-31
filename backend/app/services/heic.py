"""Generic HEIC/HEIF → web-safe image transcoding.

Browsers (Windows in particular) cannot render HEIC/HEIF: delivering a `.heic`
to the client turns it into a file download. Any HEIC that reaches this service
is guaranteed to be re-encoded before it is served.

The pipeline is source-agnostic and extensible:

  1. **remote web preprocessor** — an optional, *registered* CDN that can
     re-encode HEIC on request through a URL convention (keeps source quality).
     Byteimg is the default registered provider; other CDNs can be added with
     :func:`register_preprocessor` without touching callers.
  2. **local decode** — always-on `pillow-heif` → JPEG (works for any HEIC).
  3. **failure** — returns ``None``; the caller decides on a placeholder (never
     ship the original `.heic`).

Used by the cover proxy today, and reusable by any image pipeline.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from .image_cache import get_image

# HEIC/HEIF MIME types plus the ISOBMFF ``ftyp`` brands that identify a HEIC,
# regardless of the (sometimes empty / wrong) reported content-type.
_HEIC_MIME = {
    "image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence",
}
_HEIC_BRANDS = (
    b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis",
    b"hevm", b"hevs", b"heif", b"mif1", b"mif2", b"msf1",
)


def is_heic(content: bytes, media_type: str) -> bool:
    """Whether ``content`` is HEIC/HEIF, by MIME or ISOBMFF brand sniffing."""
    if (media_type or "").lower() in _HEIC_MIME:
        return True
    head = content[:64]
    return len(head) > 12 and head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS


def heic_to_jpeg(content: bytes) -> bytes | None:
    """Decode HEIC into JPEG via pillow-heif; None if it's unavailable/fails."""
    try:
        import pillow_heif  # noqa: PLC0415

        pillow_heif.register_heif_opener()
        from PIL import Image  # noqa: PLC0415
        import io as _io  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - missing dependency → placeholder on caller
        return None
    try:
        im = Image.open(_io.BytesIO(content))
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = _io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Remote "web preprocessor" registry: a CDN that re-encodes HEIC on request
# via a URL convention. Tried first (keeps source quality) with a web ``Accept``
# header; the local pillow-heif decode is the always-on generic fallback.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _WebPreprocessor:
    match: Callable[[str], bool]
    rewrite: Callable[[str], str | None]
    accept: str | None = None


_PREPROCESSORS: list[_WebPreprocessor] = []


def register_preprocessor(
    *,
    match: Callable[[str], bool],
    rewrite: Callable[[str], str | None],
    accept: str | None = None,
) -> None:
    """Register a remote HEIC web-preprocessor provider (ran in registration order)."""
    pp = _WebPreprocessor(match=match, rewrite=rewrite, accept=accept)
    if not any(p is pp for p in _PREPROCESSORS):
        _PREPROCESSORS.append(pp)


def _byteimg_match(url: str) -> bool:
    return "byteimg" in str(url or "").lower()


def _byteimg_rewrite(url: str) -> str | None:
    """Rewrite a byteimg original into its ``.image`` preprocessed reference
    (server re-encodes the HEIC into a web-displayable format, no signature, no
    conversion library). Returns None for non-byteimg originals."""
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    novel = "/novel-pic/"
    idx = p.path.find(novel)
    if idx < 0:
        return None
    base = p.path[idx + len(novel):].split("~", 1)[0]
    if not base:
        return None
    return (f"{p.scheme}://{p.netloc}{novel}{base}"
            f"~tplv-resize:225:300.image")


# Byteimg is the default provider (keeps source quality); others may register.
register_preprocessor(
    match=_byteimg_match,
    rewrite=_byteimg_rewrite,
    accept="image/avif,image/webp,image/jpeg,image/png",
)


async def webify_heic(
    url: str,
    content: bytes,
    media_type: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str] | None:
    """Transcode a HEIC into a browser-safe image.

    Returns ``(bytes, media_type)`` on success, ``None`` when nothing worked —
    the caller must then fall back to a placeholder and never ship the original
    ``.heic``. Non-HEIC input is returned unchanged.
    """
    if not is_heic(content, media_type):
        return content, media_type
    # 1) remote web preprocessors (registered CDNs)
    for pp in _PREPROCESSORS:
        if not pp.match(url):
            continue
        web_url = pp.rewrite(url)
        if not web_url:
            continue
        hdrs = dict(headers or {})
        if pp.accept:
            hdrs["Accept"] = pp.accept
        try:
            wc, wm = await get_image(web_url, headers=hdrs)
        except Exception:  # noqa: BLE001 - preprocessor may fail; try next path
            continue
        if wc and not is_heic(wc, wm):
            return wc, wm
    # 2) local HEIC decode → JPEG (thread-pool so the loop stays responsive)
    loop = asyncio.get_running_loop()
    jpg = await loop.run_in_executor(None, heic_to_jpeg, content)
    if jpg:
        return jpg, "image/jpeg"
    # 3) total failure → caller placeholder
    return None