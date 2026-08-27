"""外部 WebDAV 服务器上的 legado 备份同步行为测试。

接线与 test_webdav_server 相同：aiosqlite + StaticPool 单 loop、fake 用户 id=7。
这里与内建 /dav 不同，连接的是"外部 WebDAV 服务器"（坚果云/Alist…），
因此用一个内存版假 WebDAV（替换 ``legado_sync._dav``）来编排 PROPFIND/GET/PUT。
"""
from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile
from datetime import datetime
from urllib.parse import urlparse

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models
from app.models import ReadProgress, Role, ShelfItem, User, WebDavConfig

USER = "tester"
USER_ID = 7
BASE = "/legado"  # legado_directory


def _propfind(paths):
    items = ""
    for p in paths:
        items += (
            f"<D:response><D:href>{p}</D:href><D:propstat><D:prop>"
            f"<D:getlastmodified>Fri, 01 Jan 2026 00:00:00 GMT</D:getlastmodified>"
            f"<D:getcontentlength>{len(store.get(p, b''))}</D:getcontentlength>"
            f"</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
        )
    return (
        '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">' + items
        + "</D:multistatus>"
    ).encode()


async def fake_dav(cfg, method, url, username="", password="", data=None,
                   headers=None, timeout=25.0, **kw):
    """签名与 legado_sync._dav(cfg, method, url, **kw) 一致的内存 WebDAV。"""
    path = urlparse(url).path.rstrip("/") or "/"
    if method == "GET":
        b = store.get(path)
        return (404, b"not found", {}) if b is None else (200, b, {})
    if method == "PUT":
        store[path] = data
        return (201, b"", {})
    if method == "PROPFIND":
        if path == f"{BASE}/bookProgress":
            ks = [k for k in store if k.startswith(f"{BASE}/bookProgress/")]
            return (207, _propfind(ks), {})
        if path == BASE:
            zips = [k for k in store if "/backup" in k]
            return (207, _propfind(zips), {}) if zips else (404, b"nf", {})
        return (404, b"not found", {})
    if method == "MKCOL":
        return (405, b"", {})
    return (405, b"", {})


store: dict[str, bytes] = {}


def put_cloud(name: str, author: str, idx: int, pos: int = 3,
              tms: int | None = None, title: str = "") -> str:
    fname = f"{name}_{author}.json"
    store[f"{BASE}/bookProgress/{fname}"] = json.dumps({
        "name": name, "author": author, "durChapterIndex": idx,
        "durChapterPos": pos,
        "durChapterTime": tms if tms is not None else int(time.time() * 1000),
        "durChapterTitle": title,
    }, ensure_ascii=False).encode()
    return fname


@pytest.fixture()
def env(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from app.core import db as core_db

    monkeypatch.setattr(core_db.db, "engine", engine, raising=False)
    monkeypatch.setattr(core_db.db, "session_factory", factory, raising=False)
    from app.plugins.webdav import legado_sync as ls

    monkeypatch.setattr(ls, "_dav", fake_dav)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)

    asyncio.run(_create())
    return {"factory": factory}


async def _seed(factory):
    async with factory() as s:
        role = Role(name="admin", description="", permissions=["*"],
                    is_system=True)
        s.add(role)
        await s.flush()
        s.add(User(id=USER_ID, username=USER, password_hash="x",
                   display_name="Tester", is_superuser=False,
                   is_active=True, role_ids=[role.id]))
        cfg = WebDavConfig(user_id=USER_ID, url="https://example.com/",
                           username="u", password_enc="cGFzcw==",
                           legado_enabled=True, legado_directory=("legado"))
        s.add(cfg)
        # 书架两本书
        s.add(ShelfItem(user_id=USER_ID, book_url="http://s.example/b1",
                        name="测试书", author="作者一"))
        s.add(ReadProgress(user_id=USER_ID, book_url="http://s.example/b1",
                           chapter_index=2, chapter_title="第二章",
                           updated_at=datetime(2024, 1, 1)))
        s.add(ShelfItem(user_id=USER_ID, book_url="http://s.example/b2",
                        name="网页书", author="网作"))
        s.add(ReadProgress(user_id=USER_ID, book_url="http://s.example/b2",
                           chapter_index=9, chapter_title="第九章"))
        await s.commit()
        return await s.get(WebDavConfig, USER_ID)


# ------------------------------------------------------------- 拉取（云端→本地）
def test_pull_merges_cloud_progress(env, monkeypatch):
    async def scenario():
        store.clear()
        factory = env["factory"]
        cfg = await _seed(factory)
        from app.plugins.webdav import legado_sync as ls
        from app.plugins.webdav import sync_ingest as si

        # 真实后台自动入库会独占 StaticPool 单连接，测试里改为 no-op
        monkeypatch.setattr(si, "spawn_ingest", lambda *a, **k: None)

        put_cloud("测试书", "作者一", idx=42, pos=120, title="第四十二章")
        put_cloud("完全无关", "某人", idx=1, title="第一章")

        r = await ls.sync_progress(USER_ID, cfg, "pull")
        assert r["pulled"] == 2
        assert r["progressUpdated"] >= 1
        # 云端较新的进度已合入本地（书名+作者匹配）
        async with factory() as s:
            prog = (await s.execute(select(ReadProgress).where(
                ReadProgress.book_url == "http://s.example/b1"))).scalars().first()
            assert prog.chapter_index == 42
            assert prog.chapter_title == "第四十二章"
            assert prog.offset == 120

    asyncio.run(scenario())


# ------------------------------------------------------------- 推送（本地→云端）
def test_push_writes_local_newer(env):
    async def scenario():
        store.clear()
        factory = env["factory"]
        cfg = await _seed(factory)
        from app.plugins.webdav import legado_sync as ls

        r = await ls.sync_progress(USER_ID, cfg, "push")
        assert r["pushed"] >= 1
        assert store.get(f"{BASE}/bookProgress/网页书_网作.json") is not None

    asyncio.run(scenario())


# -------------------------------------- 云端更新时推送不应降级（防乒乓）
def test_push_does_not_clobber_newer_cloud(env):
    async def scenario():
        store.clear()
        factory = env["factory"]
        cfg = await _seed(factory)
        from app.plugins.webdav import legado_sync as ls

        put_cloud("网页书", "网作", idx=100, pos=999,
                  tms=int(time.time() * 1000) + 60_000, title="第一百章")
        r = await ls.sync_progress(USER_ID, cfg, "push")
        # 云端较新的「网页书」不被本地旧进度覆盖（拒绝降级、避免乒乓）
        saved = json.loads(store[f"{BASE}/bookProgress/网页书_网作.json"])
        assert saved["durChapterIndex"] == 100
        assert saved["durChapterTitle"] == "第一百章"

    asyncio.run(scenario())


# ------------------------------------------------- 无匹配书 → 触发后台自动入库
def test_no_match_spawns_ingest(env, monkeypatch):
    async def scenario():
        store.clear()
        factory = env["factory"]
        cfg = await _seed(factory)
        from app.plugins.webdav import legado_sync as ls
        from app.plugins.webdav import sync_ingest as si

        calls = []
        monkeypatch.setattr(si, "spawn_ingest",
                            lambda *a, **k: calls.append((a, k)))

        put_cloud("云端新书", "云端作者", idx=12, title="第十二章")
        r = await ls.sync_progress(USER_ID, cfg, "pull")
        assert r["pendingMatch"] == 1
        assert len(calls) == 1
        assert calls[0][1]["name"] == "云端新书"

    asyncio.run(scenario())


# ------------------------------------------------- 从全量备份导入书架（可选）
def test_import_shelf_from_zip(env):
    async def scenario():
        store.clear()
        factory = env["factory"]
        cfg = await _seed(factory)
        from app.plugins.webdav import legado_sync as ls

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("bookshelf.json", json.dumps([
                {"name": "Z书", "author": "作者Z", "bookUrl": "http://z/book",
                 "tocUrl": "", "origin": "http://zsrc", "durChapterIndex": 3,
                 "durChapterPos": 5, "durChapterTime": 1704067200000,
                 "durChapterTitle": "第3章"},
            ], ensure_ascii=False))
        store[f"{BASE}/backup2026.zip"] = buf.getvalue()

        r = await ls.import_shelf(USER_ID, cfg)
        assert r["addedShelf"] == 1
        assert r["progressUpdated"] == 1
        assert r["backup"] == "backup2026.zip"

        async with factory() as s:
            item = (await s.execute(select(ShelfItem).where(
                ShelfItem.user_id == USER_ID, ShelfItem.name == "Z书"))).scalars().first()
            assert item is not None and item.book_url == "http://z/book"
            prog = (await s.execute(select(ReadProgress).where(
                ReadProgress.user_id == USER_ID,
                ReadProgress.book_url == "http://z/book"))).scalars().first()
            assert prog is not None and prog.chapter_index == 3

    asyncio.run(scenario())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))