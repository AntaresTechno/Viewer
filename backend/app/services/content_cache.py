"""In-memory LRU cache for fetched chapter content + background prefetch.

Raw engine output is cached (before per-request replace rules) keyed by
everything that influences it. Prefetch jobs run in the background with a
small concurrency cap; failures are silently dropped — the real read will
fetch on demand as before.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from ..core.config import settings

_MAX_ENTRIES = 40

_cache: OrderedDict[str, str] = OrderedDict()
_lock = asyncio.Lock()
_inflight: dict[str, asyncio.Future[str]] = {}
_sem = asyncio.Semaphore(max(1, settings.prefetch_concurrency))


def cache_key(
    source_url: str,
    url: str,
    *,
    base: str = "",
    title: str = "",
    next_chapter_url: str = "",
    is_volume: bool = False,
) -> str:
    return "\x1f".join([
        source_url, url, base, title, next_chapter_url,
        "1" if is_volume else "0",
    ])


async def get_or_fetch(
    key: str, factory: Callable[[], Awaitable[str]]
) -> tuple[str, bool]:
    """Return (content, from_cache). Concurrent same-key calls share one fetch."""
    async with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key], True
        fut = _inflight.get(key)
        if fut is None:
            fut = asyncio.get_running_loop().create_future()
            _inflight[key] = fut

            async def _run() -> None:
                try:
                    text = await factory()
                    async with _lock:
                        _cache[key] = text
                        _cache.move_to_end(key)
                        while len(_cache) > _MAX_ENTRIES:
                            _cache.popitem(last=False)
                    if not fut.done():
                        fut.set_result(text)
                except Exception as exc:  # noqa: BLE001
                    if not fut.done():
                        fut.set_exception(exc)
                finally:
                    _inflight.pop(key, None)

            asyncio.ensure_future(_run())
    # await outside the lock; waiters joining an inflight fetch report False
    text = await fut
    return text, False


async def peek(key: str) -> str | None:
    async with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
        return None


async def spawn_prefetch(key: str, factory: Callable[[], Awaitable[str]]) -> bool:
    """Queue a background fetch if not cached and not already running."""
    async with _lock:
        if key in _cache or key in _inflight:
            return False
        # 同步登记 inflight，连续多次 spawn 不会重复排队
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        _inflight[key] = fut

    async def _job() -> None:
        try:
            async with _sem:
                text = await factory()
            async with _lock:
                _cache[key] = text
                _cache.move_to_end(key)
                while len(_cache) > _MAX_ENTRIES:
                    _cache.popitem(last=False)
            if not fut.done():
                fut.set_result(text)
        except Exception as exc:  # noqa: BLE001 - prefetch best-effort
            if not fut.done():
                fut.set_exception(exc)
        finally:
            async with _lock:
                if _inflight.get(key) is fut:
                    _inflight.pop(key)

    asyncio.ensure_future(_job())
    return True


def clear() -> None:
    _cache.clear()
