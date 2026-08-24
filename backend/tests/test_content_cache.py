"""Tests for the chapter content LRU cache and prefetch dedup."""
from __future__ import annotations

import asyncio

from app.services import content_cache as cc


def test_get_or_fetch_caches():
    calls = []

    async def factory() -> str:
        calls.append(1)
        return "text"

    async def run():
        a = await cc.get_or_fetch("k1", factory)
        b = await cc.get_or_fetch("k1", factory)
        return a, b

    (t1, c1), (t2, c2) = asyncio.run(run())
    assert t1 == t2 == "text"
    assert c1 is False and c2 is True
    assert len(calls) == 1


def test_concurrent_same_key_single_fetch():
    calls = []

    async def factory() -> str:
        calls.append(1)
        await asyncio.sleep(0.02)
        return "x"

    async def run():
        return await asyncio.gather(*[
            cc.get_or_fetch("k2", factory) for _ in range(5)
        ])

    results = asyncio.run(run())
    assert len(calls) == 1
    assert all(t == "x" for t, _ in results)


def test_lru_eviction():
    async def run():
        for i in range(cc._MAX_ENTRIES + 5):
            await cc.get_or_fetch(f"e{i}", lambda i=i: _ret(i))

    async def _ret(i: str) -> str:
        return i

    asyncio.run(run())
    keys = list(cc._cache.keys())
    assert len(keys) <= cc._MAX_ENTRIES
    assert "e0" not in cc._cache          # 最旧的被逐出
    assert f"e{cc._MAX_ENTRIES + 4}" in cc._cache


def test_spawn_prefetch_dedup_and_fill():
    calls = []

    async def factory() -> str:
        calls.append(1)
        await asyncio.sleep(0.01)
        return "p"

    async def run():
        q1 = await cc.spawn_prefetch("pf", factory)
        q2 = await cc.spawn_prefetch("pf", factory)
        await asyncio.sleep(0.15)         # 等后台任务完成
        cached = await cc.peek("pf")
        # 已缓存后再 spawn 不再排队
        q3 = await cc.spawn_prefetch("pf", factory)
        return q1, q2, cached, q3

    q1, q2, cached, q3 = asyncio.run(run())
    assert q1 is True and q2 is False     # 进行中不重复排队
    assert cached == "p"
    assert q3 is False                    # 已缓存不再排队
    assert len(calls) == 1


def setup_function() -> None:
    cc.clear()


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
