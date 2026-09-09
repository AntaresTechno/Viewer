"""Media plugin API lifecycle on an isolated in-memory database.

Covers source import (fqdj → video), library add idempotency, multi-user
isolation, progress merge and delete-with-progress.
"""
from __future__ import annotations

import asyncio
import json
import types

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models


def _make_app(monkeypatch, user):
    from app.core import db as core_db
    from app.core import deps as core_deps

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(core_db.db, "engine", engine, raising=False)
    monkeypatch.setattr(core_db.db, "session_factory", factory, raising=False)

    holder = {"user": user}

    def perms():
        u = holder["user"]
        if u.is_superuser:
            return ["*"]
        return ["media.read", "media.library.write",
                "media.sources.read", "media.sources.manage"]

    async def fake_current_user():
        return holder["user"], perms()

    def fake_require_perm(_permission: str):
        async def checker(current=core_deps.Depends(fake_current_user)):
            return current

        return checker

    monkeypatch.setattr(core_deps, "get_current_user", fake_current_user)
    monkeypatch.setattr(core_deps, "require_perm", fake_require_perm)

    from app.plugins.media import plugin

    app = FastAPI()
    app.include_router(plugin.create_router(None), prefix="/api/media")
    return engine, factory, app, holder


async def _run(client, method, path, **kw):
    return await client.request(method, path, **kw)


def test_media_library_api_import_add_progress_delete(monkeypatch):
    user = types.SimpleNamespace(id=1, username="u1", is_superuser=False)
    engine, factory, app, _holder = _make_app(monkeypatch, user)

    fqdj = {
        "sourceUrl": "番茄短剧", "sourceName": "番茄短剧", "articleStyle": 2,
        "ruleArticles": "$.data", "ruleContent": "<video>...",
    }

    async def scenario():
        async with engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.create_all)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 导入 fqdj → 自动识别为视频
            imp = await _run(client, "POST", "/api/media/sources/import",
                             json={"data": json.dumps([fqdj], ensure_ascii=False)})
            assert imp.status_code == 201, imp.text
            body = imp.json()
            assert body["added"] == 1
            assert body["needsKind"] is False

            sources = (await _run(client, "GET", "/api/media/sources")).json()["items"]
            assert len(sources) == 1
            src = sources[0]
            assert src["mediaKind"] == "video"
            assert src["sourceFormat"] == "rss"
            assert src["sourceName"] == "番茄短剧"

            disabled = await _run(
                client, "POST", "/api/media/sources/batch-enabled",
                json={"ids": [src["id"]], "enabled": False},
            )
            assert disabled.json() == {"updated": 1, "enabled": False}
            sources = (await _run(client, "GET", "/api/media/sources")).json()["items"]
            assert sources[0]["enabled"] is False
            await _run(
                client, "POST", "/api/media/sources/batch-enabled",
                json={"ids": [src["id"]], "enabled": True},
            )

            # 幂等加入
            add1 = await _run(client, "POST", "/api/media/library", json={
                "sourceId": src["id"],
                "item": {"itemKey": "https://x/catalog?book_id=1", "title": "短剧A",
                         "itemUrl": "https://x/catalog?book_id=1", "coverUrl": "",
                         "mediaKind": "video"},
            })
            assert add1.status_code == 201
            lib_id = add1.json()["id"]
            assert add1.json()["existed"] is False
            add2 = await _run(client, "POST", "/api/media/library", json={
                "sourceId": src["id"],
                "item": {"itemKey": "https://x/catalog?book_id=1", "title": "短剧A"},
            })
            assert add2.json()["existed"] is True
            assert add2.json()["id"] == lib_id

            # 总览计数
            ov = (await _run(client, "GET", "/api/media/overview")).json()
            assert ov["counts"]["video"] == 1
            assert ov["counts"]["total"] == 1

            # rss 无 mediaRules → legacy resolve 返回 409
            resolve = await _run(
                client, "POST", f"/api/media/library/{lib_id}/units/ep1/resolve")
            assert resolve.json()["mode"] == "legacy"

            # 进度
            put1 = await _run(client, "PUT", f"/api/media/library/{lib_id}/progress", json={
                "unitKey": "ep1", "unitIndex": 0, "unitTitle": "第1集",
                "positionMs": 60_000, "durationMs": 100_000, "completed": False,
            })
            assert put1.status_code == 200
            got = await _run(client, "GET", f"/api/media/library/{lib_id}/progress")
            prog = got.json()["progress"]
            assert prog["positionMs"] == 60_000
            assert prog["unitKey"] == "ep1"

            # 95% → completed
            put2 = await _run(client, "PUT", f"/api/media/library/{lib_id}/progress", json={
                "unitKey": "ep1", "positionMs": 96_000, "durationMs": 100_000,
            })
            assert put2.json()["progress"]["completed"] is True

            # 删除条目回到空库
            cursor = await _run(client, "DELETE", f"/api/media/library/{lib_id}")
            assert cursor.status_code == 200
            lst = (await _run(client, "GET", "/api/media/library")).json()["items"]
            assert lst == []

        await engine.dispose()

    asyncio.run(scenario())


def test_media_multi_user_isolation(monkeypatch):
    user1 = types.SimpleNamespace(id=11, username="uA", is_superuser=False)
    user2 = types.SimpleNamespace(id=12, username="uB", is_superuser=False)
    engine, factory, app, holder = _make_app(monkeypatch, user1)

    async def scenario():
        async with engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.create_all)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            imp = await _run(client, "POST", "/api/media/sources/import", json={
                "data": json.dumps({"sourceUrl": "S", "ruleContent": "<video>"},
                                   ensure_ascii=False)})
            src_id = (await _run(client, "GET", "/api/media/sources")).json()["items"][0]["id"]

            add = await _run(client, "POST", "/api/media/library", json={
                "sourceId": src_id,
                "item": {"itemKey": "k1", "title": "H", "itemUrl": "u"},
            })
            lib_id = add.json()["id"]

            # 另一个用户看不到/删不到 user1 的条目
            holder["user"] = user2
            got = await _run(client, "GET", f"/api/media/library/{lib_id}")
            assert got.status_code == 404
            deleted = await _run(client, "DELETE", f"/api/media/library/{lib_id}")
            assert deleted.status_code == 404
            ov = (await _run(client, "GET", "/api/media/overview")).json()
            assert ov["counts"]["total"] == 0
            assert (await _run(client, "GET", "/api/media/library")).json()["items"] == []
        await engine.dispose()

    asyncio.run(scenario())
