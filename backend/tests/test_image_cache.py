"""Tests for the disk-backed image cache (path mapping, LRU, single-flight)."""
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import image_cache as ic


@pytest.fixture()
def cache_dir(monkeypatch) -> Path:
    """Private cache dir; pytest tmp_path is unusable here (cross-process ACL)."""
    p = Path(r"D:\Project\antares\.cache\ic-tests") / uuid.uuid4().hex[:8]
    p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ic, "CACHE_DIR", p)
    yield p
    shutil.rmtree(p, ignore_errors=True)


def _fetcher(calls: list[str], body: bytes = b"\xff\xd8fake", ctype: str = "image/jpeg"):
    async def _f(url, *, headers=None, timeout=None):
        calls.append(url)
        await asyncio.sleep(0.01)
        if url.endswith("#404"):
            raise ValueError("HTTP 404")
        return body, ctype
    return _f


class TestGetImage:
    def test_miss_then_hit(self, cache_dir):
        calls: list[str] = []
        f = _fetcher(calls)
        b1, mt1 = asyncio.run(
            ic.get_image("http://x/a.jpg", _fetcher=f))
        b2, mt2 = asyncio.run(
            ic.get_image("http://x/a.jpg", _fetcher=f))
        assert b1 == b2 and mt1 == mt2 == "image/jpeg"
        assert len(calls) == 1  # second served from disk
        assert any(p.suffix == ".jpg" for p in cache_dir.iterdir())

    def test_distinct_urls_distinct_files(self, cache_dir):
        calls: list[str] = []
        f = _fetcher(calls)
        asyncio.run(ic.get_image("http://x/a.png", _fetcher=f))
        asyncio.run(ic.get_image("http://x/b.png", _fetcher=f))
        assert len(list(cache_dir.iterdir())) == 2

    def test_failure_raises_and_no_cache(self, cache_dir):
        calls: list[str] = []
        f = _fetcher(calls)

        async def run():
            try:
                await ic.get_image("http://x/broken.jpg#404", _fetcher=f)
            except ValueError:
                return True
            return False

        assert asyncio.run(run())
        assert not list(cache_dir.iterdir())

    def test_single_flight_dedupes(self, cache_dir):
        calls: list[str] = []
        f = _fetcher(calls)

        async def run():
            return await asyncio.gather(*[
                ic.get_image("http://x/same.webp", _fetcher=f) for _ in range(5)
            ])

        results = asyncio.run(run())
        assert len(calls) == 1
        assert all(r[0] == results[0][0] for r in results)


class TestEvict:
    def test_lru_eviction(self, cache_dir, monkeypatch):
        # floor in code is max(10, mb) MB; mb=1 -> 10MB cap vs ~12MB written
        monkeypatch.setattr(settings, "image_cache_mb", 1)
        for i in range(3):
            p = cache_dir / f"key{i}.jpg"
            p.write_bytes(b"x" * (4 * 1024 * 1024))
            p.touch()
        ic._evict_locked()
        remaining = list(cache_dir.iterdir())
        assert len(remaining) < 3
        # oldest removed first
        names = {p.name for p in remaining}
        assert "key2.jpg" in names


class TestPathFor:
    def test_ext_from_cached_file(self, cache_dir, monkeypatch):
        key = ic._key_for("http://x/c.jpg")
        f = cache_dir / f"{key}.webp"
        f.write_bytes(b"x")
        assert ic.path_for("http://x/c.jpg").suffix == ".webp"

    def test_ext_from_url_when_missing(self, cache_dir):
        p = ic.path_for("http://x/d.gif?token=1")
        assert p.suffix == ".gif"

    def test_fallback_bin(self, cache_dir):
        p = ic.path_for("http://x/noext")
        assert p.name.endswith(".bin")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
