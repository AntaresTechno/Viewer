"""首页插件 / WebDAV 插件 / 目录更新检测 的行为测试。

DB 场景与 test_toc_queue 相同约束：aiosqlite + StaticPool 绑定创建循环，
所有步骤（含 httpx ASGITransport 调 FastAPI 应用）跑在同一个 asyncio loop。
"""
from __future__ import annotations

import asyncio
import json
import types
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models
from app.models import (
    BookChapter,
    BookSourceRow,
    ReadingStat,
    ReadProgress,
    ShelfItem,
    TocJob,
)
from app.services import toc_queue as tq


# ----------------------------------------------------------------- fixtures
class FakeEngine:
    """可编排目录输出的假书源引擎。"""

    def __init__(self, n_chapters: int = 3, last_title: str = "第九章"):
        self.n_chapters = n_chapters
        self.last_title = last_title

    async def book_info(self, src, book):
        return {**book, "tocUrl": "http://s.example/toc",
                "lastChapter": self.last_title}

    async def get_toc(self, src, book, toc_url):
        return [
            {"title": f"第{i}章", "url": f"http://s.example/c{i}",
             "baseUrl": "http://s.example/toc"}
            for i in range(self.n_chapters)
        ]


@pytest.fixture()
def db_env(monkeypatch):
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

    return {"factory": factory, "make_tables": make_tables}


@pytest.fixture()
def api_app(db_env, monkeypatch):
    """挂载 home + webdav 插件路由；认证/权限放行为固定 fake user。"""
    from app.core import deps as core_deps

    user = types.SimpleNamespace(id=7, username="tester", is_superuser=False)

    async def fake_current_user():
        return user, ["*"]

    def real_require_perm(perm_key: str):
        async def checker(current=core_deps.Depends(fake_current_user)):
            return current

        return checker

    monkeypatch.setattr(core_deps, "get_current_user", fake_current_user)
    monkeypatch.setattr(core_deps, "require_perm", real_require_perm)

    from app.plugins.home.plugin import create_router as home_router
    from app.plugins.webdav.plugin import create_router as webdav_router

    app = FastAPI()
    app.include_router(home_router(None), prefix="/api/home")
    app.include_router(webdav_router(None), prefix="/api/webdav")
    return {"app": app, "user": user, "factory": db_env["factory"],
            "make_tables": db_env["make_tables"]}


async def _call(app, method: str, url: str, **kw) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, url, **kw)


# ------------------------------------------------- 目录刷新 → 更新检测
def test_process_job_marks_update_on_change(db_env, monkeypatch):
    holder = {"engine": FakeEngine(n_chapters=3)}
    monkeypatch.setattr(
        tq, "get_engine", lambda key=None, ctx=None: holder["engine"]
    )
    factory = db_env["factory"]

    async def scenario():
        await db_env["make_tables"]()
        async with factory() as s:
            s.add(BookSourceRow(
                source_url="http://s.example", source_name="测",
                raw_json=json.dumps({"bookSourceUrl": "http://s.example"}),
                enabled=True,
            ))
            s.add(ShelfItem(user_id=1, book_url="http://s.example/b1",
                            name="测试书", source_url="http://s.example"))
            await s.commit()

        async def run_job():
            async with factory() as s:
                job = await tq.TocQueue.create_job(s, "http://s.example",
                                                   "http://s.example/b1")
                assert job is not None
                jid = job.id
            await tq.process_job(jid)
            async with factory() as s:
                return (await s.execute(
                    select(ShelfItem).where(ShelfItem.user_id == 1)
                )).scalars().first()

        # 第一次：初始填充，不算「有更新」
        shelf = await run_job()
        assert shelf.has_update is False

        # 第二次：内容没变，仍然不算
        shelf = await run_job()
        assert shelf.has_update is False

        # 第三次：书源多了一章 → 标记「有更新」
        holder["engine"] = FakeEngine(n_chapters=4)
        shelf = await run_job()
        assert shelf.has_update is True
        assert shelf.last_chapter == "第九章"

    asyncio.run(scenario())


# ------------------------------------------------------------ 首页插件
def test_home_heartbeat_and_summary(api_app):
    app, factory = api_app["app"], api_app["factory"]

    async def scenario():
        await api_app["make_tables"]()

        # 心跳：同一本书两次上报累加
        r1 = await _call(app, "POST", "/api/home/heartbeat", json={
            "bookUrl": "http://s.example/b1", "sourceUrl": "http://s.example",
            "seconds": 30,
        })
        assert r1.status_code == 200
        assert r1.json()["secondsToday"] == 30
        r2 = await _call(app, "POST", "/api/home/heartbeat", json={
            "bookUrl": "http://s.example/b1", "seconds": 90,
        })
        assert r2.json()["secondsToday"] == 120

        # 书架 + 阅读进度 + 一条历史统计（600s，非今天）
        async with factory() as s:
            s.add(ShelfItem(user_id=7, book_url="http://s.example/b1",
                            name="测试书", author="作者", cover_url="c.jpg",
                            source_url="http://s.example"))
            s.add(ReadProgress(user_id=7, book_url="http://s.example/b1",
                               chapter_index=2, chapter_title="第三章"))
            s.add(ReadingStat(user_id=7, day="2024-01-01",
                              book_url="http://s.example/b1", seconds=600))
            await s.commit()

        rs = await _call(app, "GET", "/api/home/summary")
        assert rs.status_code == 200
        data = rs.json()
        assert data["todaySeconds"] == 120          # 只有心跳计入今天
        assert data["totalSeconds"] == 720          # 心跳 120 + 历史种子 600
        assert data["totalBooks"] == 1
        assert data["totalDays"] == 2               # 今天 + 2024-01-01
        assert data["streakDays"] == 1              # 只有今天连续
        rec = data["recents"]
        assert len(rec) == 1 and rec[0]["name"] == "测试书"
        assert rec[0]["chapterTitle"] == "第三章"

        rd = await _call(app, "GET", "/api/home/daily?days=7")
        assert rd.status_code == 200
        points = rd.json()["items"]
        assert len(points) == 7
        assert points[-1]["seconds"] == 120         # 最后一天是今天

    asyncio.run(scenario())


# ------------------------------------------------------------ WebDAV 插件
@pytest.fixture()
def dav_store(monkeypatch):
    """把 dav_request 换成内存对象存储，模拟一个 WebDAV 服务器。"""
    store: dict[str, bytes] = {}

    async def fake_dav(method, url, *, username="", password="", data=None,
                       headers=None, timeout=25.0):
        if method == "PUT":
            store[url] = data or b""
            return 201, b"", {}
        if method == "GET":
            if url in store:
                return 200, store[url], {}
            return 404, b"not found", {}
        if method == "DELETE":
            return (204, b"", {}) if store.pop(url, None) else (404, b"", {})
        if method == "PROPFIND":
            depth = (headers or {}).get("Depth", "0")
            base = url.rstrip("/")
            ns = "DAV:"
            root = ET.Element(f"{{{ns}}}multistatus")
            if depth == "0":
                paths = [u for u in store if u.rstrip("/") == base]
            else:
                paths = [u for u in store if u.startswith(base + "/")]
            for u in paths:
                resp = ET.SubElement(root, f"{{{ns}}}response")
                href = ET.SubElement(resp, f"{{{ns}}}href")
                href.text = "/" + unquote(u.split("://", 1)[-1])
                propstat = ET.SubElement(resp, f"{{{ns}}}propstat")
                prop = ET.SubElement(propstat, f"{{{ns}}}prop")
                ET.SubElement(prop, f"{{{ns}}}getcontentlength").text = str(
                    len(store.get(u, b"")))
                ET.SubElement(prop, f"{{{ns}}}getlastmodified").text = (
                    "Mon, 01 Jan 2024 00:00:00 GMT")
            return 207, ET.tostring(root), {}
        if method == "MKCOL":
            return 201, b"", {}
        return 400, b"bad request", {}

    import app.plugins.webdav.plugin as wd

    monkeypatch.setattr(wd, "dav_request", fake_dav)
    return store


def test_webdav_config_backup_restore_roundtrip(api_app, dav_store):
    app, factory = api_app["app"], api_app["factory"]

    async def scenario():
        await api_app["make_tables"]()

        # 种子用户数据
        async with factory() as s:
            s.add(ShelfItem(user_id=7, book_url="http://s.example/b1",
                            name="测试书", author="作者", source_url="src",
                            last_chapter="第一章"))
            s.add(ReadProgress(user_id=7, book_url="http://s.example/b1",
                               chapter_index=5, chapter_title="第五章"))
            s.add(ReadingStat(user_id=7, day="2024-01-01",
                              book_url="http://s.example/b1", seconds=300))
            await s.commit()

        # 未配置时备份应报错
        rb0 = await _call(app, "POST", "/api/webdav/backup")
        assert rb0.status_code == 400

        # 保存配置（密码不回显）
        rc = await _call(app, "PUT", "/api/webdav/config", json={
            "url": "https://dav.example", "username": "u",
            "password": "p", "directory": "Backups",
        })
        assert rc.status_code == 200
        body = rc.json()
        assert body["hasPassword"] is True
        assert "password_enc" not in json_keys(body)
        assert body.get("password") in (None, "") or "password" not in body

        # 备份
        rb = await _call(app, "POST", "/api/webdav/backup")
        assert rb.status_code == 200
        backup_res = rb.json()
        assert backup_res["shelf"] == 1 and backup_res["progress"] == 1
        assert any(k.endswith(".json") for k in dav_store)

        # 列表能列出该文件
        rl = await _call(app, "GET", "/api/webdav/backups")
        names = [it["name"] for it in rl.json()["items"]]
        assert backup_res["file"] in names

        # 清空本地书架/进度后恢复 → 数据回来
        async with factory() as s:
            for row in (await s.execute(select(ShelfItem))).scalars().all():
                await s.delete(row)
            for row in (await s.execute(select(ReadProgress))).scalars().all():
                await s.delete(row)
            await s.commit()

        rr = await _call(app, "POST", "/api/webdav/restore",
                         json={"file": backup_res["file"]})
        assert rr.status_code == 200
        res = rr.json()
        assert res["shelfAdded"] == 1
        assert res["progressUpdated"] == 1

        async with factory() as s:
            shelf = (await s.execute(select(ShelfItem))).scalars().first()
            prog = (await s.execute(select(ReadProgress))).scalars().first()
            stat = (await s.execute(select(ReadingStat))).scalars().first()
            assert shelf.name == "测试书"
            assert prog.chapter_index == 5
            assert stat.seconds >= 300

        # 非法文件名被拒绝（防路径穿越）
        rbad = await _call(app, "POST", "/api/webdav/restore",
                           json={"file": "../evil.json"})
        assert rbad.status_code == 400

    asyncio.run(scenario())


def json_keys(d):
    return list(d.keys())


def test_process_job_no_regression_on_error_path(db_env, monkeypatch):
    """错误路径回归：引擎抛错时任务记 error、不写章节。"""
    def boom(*a, **k):
        raise RuntimeError("detail page blocked")

    engine = types.SimpleNamespace(book_info=boom)
    monkeypatch.setattr(tq, "get_engine", lambda key=None, ctx=None: engine)
    factory = db_env["factory"]

    async def scenario():
        await db_env["make_tables"]()
        async with factory() as s:
            s.add(BookSourceRow(source_url="http://s.example", raw_json="{}",
                                enabled=True))
            job = TocJob(source_url="http://s.example",
                         book_url="http://s.example/b1", status="queued")
            s.add(job)
            await s.commit()
            jid = job.id
        await tq.process_job(jid)
        async with factory() as s:
            j = await s.get(TocJob, jid)
            rows = (await s.execute(select(BookChapter))).scalars().all()
            return j, list(rows)

    j, rows = asyncio.run(scenario())
    assert j.status == "error"
    assert rows == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
