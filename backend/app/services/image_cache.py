"""Disk-backed image cache (LRU, content-addressed by source URL).

Used by the cover proxy and chapter-content images so a flaky origin does not
break already-seen pictures, and repeated views do not re-download.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from ..core.config import DATA_DIR, settings

CACHE_DIR = DATA_DIR / "cache" / "img"

_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
}

# single-flight per cache key so parallel requests share one download
_inflight: dict[str, asyncio.Future[Path]] = {}


def _key_for(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def path_for(url: str) -> Path:
    """Cache file path for an URL regardless of whether it is cached yet."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _key_for(url)
    for ext in (".jpg", ".png", ".webp", ".gif", ".avif", ".svg"):
        p = CACHE_DIR / f"{key}{ext}"
        if p.exists():
            return p
    # not cached: pick extension from the URL when it has one
    tail = url.split("?", 1)[0].rsplit(".", 1)
    if len(tail) == 2 and tail[1].lower() in ("jpg", "jpeg", "png", "webp", "gif", "avif", "svg"):
        ext = "jpg" if tail[1].lower() == "jpeg" else tail[1].lower()
    else:
        ext = ".bin"
    return CACHE_DIR / f"{key}.{ext}"


def _media_type_of(p: Path) -> str:
    suffix = p.suffix.lower().lstrip(".")
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
        "avif": "image/avif",
        "svg": "image/svg+xml",
    }.get(suffix, "application/octet-stream")


async def _fetch_remote(
    url: str, *, headers: dict[str, str] | None, timeout: float | None
) -> tuple[bytes, str]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout or settings.request_timeout,
        verify=False,
    ) as client:
        resp = await client.get(url, headers=headers)
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}")
        if ctype and not (
            ctype.startswith("image/") or ctype == "application/octet-stream"
        ):
            raise ValueError(f"not an image: {ctype}")
        return resp.content, ctype


def _evict_locked() -> None:
    """Keep total cache size under the cap, oldest mtime first."""
    cap = max(10, settings.image_cache_mb) * 1024 * 1024
    try:
        files = [p for p in CACHE_DIR.iterdir() if p.is_file()]
    except FileNotFoundError:
        return
    total = sum(p.stat().st_size for p in files)
    if total <= cap:
        return
    for p in sorted(files, key=lambda x: x.stat().st_mtime):
        if total <= cap:
            break
        total -= p.stat().st_size
        try:
            p.unlink()
        except OSError:
            pass


async def get_image(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    _fetcher: Callable[..., Awaitable[tuple[bytes, str]]] | None = None,
) -> tuple[bytes, str]:
    """Return (bytes, media_type) for an image URL, disk-cache first.

    Raises on network/validation failure; caller decides on fallback.
    """
    fetcher = _fetcher or _fetch_remote
    target = path_for(url)
    if target.exists():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, os.utime, str(target), None)  # LRU touch
        return target.read_bytes(), _media_type_of(target)

    key = _key_for(url)
    fut: asyncio.Future[Path] | None = _inflight.get(key)
    if fut is not None:
        target = await fut
        return target.read_bytes(), _media_type_of(target)

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _inflight[key] = fut
    try:
        content, ctype = await fetcher(url, headers=headers, timeout=timeout)
        ext = _EXT_BY_TYPE.get(ctype) or target.suffix or ".bin"
        final = CACHE_DIR / f"{key}{ext}"
        tmp = final.with_suffix(final.suffix + ".part")
        tmp.write_bytes(content)
        os.replace(tmp, final)
        _evict_locked()
        if not fut.done():
            fut.set_result(final)
        return content, (ctype or _media_type_of(final))
    except Exception as exc:
        if not fut.done():
            fut.set_exception(exc)
        raise
    finally:
        # waiters keep their reference to fut; new requests start a fresh fetch
        _inflight.pop(key, None)
