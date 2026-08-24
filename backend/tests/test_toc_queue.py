"""Tests for the background TOC queue (process_job) against a temp DB.

All steps of a case run inside ONE asyncio loop: aiosqlite + StaticPool bind
the pooled connection to the creating loop and must not cross loops.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models
from app.models import BookChapter, BookRef, BookSourceRow, ShelfItem, TocJob
from app.services import toc_queue as tq


class FakeEngine:
    def __init__(self, fail_toc: bool = False):
        self.fail_toc = fail_toc

    async def book_info(self, src, book):
        return {
            **book,
            "tocUrl": "http://s.example/toc",
            "lastChapter": "第九章",
            "intro": "一个测试简介。",
            "kind": "玄幻, 系统",
        }

    async def get_toc(self, src, book, toc_url):
        if self.fail_toc:
            raise RuntimeError("toc page blocked")
        assert toc_url == "http://s.example/toc"
        return [
            {"title": f"第{i}章", "url": f"http://s.example/c{i}",
             "baseUrl": "http://s.example/toc"}
            for i in range(3)
        ]


@pytest.fixture()
def db_env(monkeypatch):
    """Patch core.db singletons to a fresh temp in-memory sqlite."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.core import db as core_db

    monkeypatch.setattr(core_db.db, "engine", engine, raising=False)
    monkeypatch.setattr(core_db.db, "session_factory", factory, raising=False)

    async def make_tables():
        async with core_db.get_engine().begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)

    async def seed() -> int:
        async with factory() as s:
            s.add(BookSourceRow(
                source_url="http://s.example", source_name="测",
                source_group="",
                raw_json=json.dumps({"bookSourceUrl": "http://s.example"}),
                enabled=True, engine="legado",
            ))
            shelf = ShelfItem(
                user_id=1, book_url="http://s.example/b1", name="测试书",
                author="作者", cover_url="", source_url="http://s.example",
            )
            s.add(shelf)
            s.add(BookRef(
                source_url="http://s.example", book_url="http://s.example/b1",
                name="测试书", author="作者", cover_url="",
            ))
            await s.commit()
            return shelf.id

    return {"factory": factory, "make_tables": make_tables, "seed": seed}


def test_process_job_success(db_env, monkeypatch):
    monkeypatch.setattr(tq, "get_engine", lambda key=None, ctx=None: FakeEngine())
    factory = db_env["factory"]

    async def scenario():
        await db_env["make_tables"]()
        shelf_id = await db_env["seed"]()

        async with factory() as s:
            job = await tq.TocQueue.create_job(s, "http://s.example",
                                               "http://s.example/b1")
            assert job is not None
            jid = job.id
        await tq.process_job(jid)
        async with factory() as s:
            j = await s.get(TocJob, jid)
            rows = (await s.execute(
                select(BookChapter).order_by(BookChapter.idx)
            )).scalars().all()
            shelf = await s.get(ShelfItem, shelf_id)
            ref = (await s.execute(
                select(BookRef).where(BookRef.book_url == "http://s.example/b1")
            )).scalars().first()
            return j, rows, shelf, ref

    j, rows, shelf, ref = asyncio.run(scenario())
    assert j.status == "done"
    assert j.chapters == 3
    assert [c.title for c in rows] == ["第0章", "第1章", "第2章"]
    assert shelf.last_chapter == "第九章"
    assert shelf.toc_url == "http://s.example/toc"
    # 书籍短链档案同步回填：阅读器/详情页可全缓存展示
    assert ref.intro == "一个测试简介。"
    assert ref.kind == "玄幻, 系统"
    assert ref.last_chapter == "第九章"
    assert ref.toc_url == "http://s.example/toc"


def test_process_job_error_recorded(db_env, monkeypatch):
    monkeypatch.setattr(
        tq, "get_engine", lambda key=None, ctx=None: FakeEngine(fail_toc=True)
    )
    factory = db_env["factory"]

    async def scenario():
        await db_env["make_tables"]()
        await db_env["seed"]()

        async with factory() as s:
            job = await tq.TocQueue.create_job(s, "http://s.example",
                                               "http://s.example/b1")
            jid = job.id
        await tq.process_job(jid)
        async with factory() as s:
            j = await s.get(TocJob, jid)
            rows = (await s.execute(select(BookChapter))).scalars().all()
            return j, list(rows)

    j, rows = asyncio.run(scenario())
    assert j.status == "error"
    assert "toc page blocked" in j.error
    assert rows == []


def test_create_job_dedups_active(db_env):
    factory = db_env["factory"]

    async def scenario():
        await db_env["make_tables"]()
        await db_env["seed"]()
        async with factory() as s:
            a = await tq.TocQueue.create_job(s, "http://s.example", "u1")
            b = await tq.TocQueue.create_job(s, "http://s.example", "u1")
            return a, b

    a, b = asyncio.run(scenario())
    assert a is not None and b is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
