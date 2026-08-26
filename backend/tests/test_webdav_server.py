"""WebDAV 服务端（legado 进度同步专用路径）行为测试。

模拟 legado AppWebDav 的同步方式：Basic 认证 + bookProgress/*.json 的
PUT/GET/PROPFIND/MKCOL，验证与书架 ReadProgress 的双向合并语义。
DB 场景与 test_home_webdav 相同约束：aiosqlite + StaticPool 单 loop。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import types
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models
from app.models import (
    BookSourceRow,
    DavResource,
    ReadProgress,
    Role,
    ShelfItem,
    TocJob,
    User,
    WebDavConfig,
)


NS = "{DAV:}"
USER = "tester"
SECRET = "s3cret-dav-pass"


def basic(user: str, pwd: str) -> dict[str, str]:
    tok = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


def auth_headers() -> dict[str, str]:
    return basic(USER, SECRET)


def payload(name: str, author: str, idx: int, pos: int = 3,
            tms: int | None = None, title: str = "") -> dict:
    return {
        "name": name, "author": author,
        "durChapterIndex": idx, "durChapterPos": pos,
        "durChapterTime": tms if tms is not None else int(time.time() * 1000),
        "durChapterTitle": title,
    }


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

    # /api/webdav/server 配置端点依赖登录态 —— 固定 fake 用户
    from app.core import deps as core_deps

    fake_user = types_user()
    async def fake_current_user():
        return fake_user, ["*"]
    monkeypatch.setattr(core_deps, "get_current_user", fake_current_user)
    def real_require_perm(_perm):
        async def checker(current=core_deps.Depends(fake_current_user)):
            return current
        return checker
    monkeypatch.setattr(core_deps, "require_perm", real_require_perm)

    async def build_app():
        async with core_db.get_engine().begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
        from app.plugins.webdav.dav_server import create_root_router
        from app.plugins.webdav.plugin import create_router

        app = FastAPI()
        app.include_router(create_router(None), prefix="/api/webdav")
        app.include_router(create_root_router(None), prefix="/dav")
        return app

    return {"factory": factory, "build_app": build_app}


class types_user:
    """require_perm fake 用的最小用户对象。"""
    id = 7
    username = USER
    is_superuser = False


async def _seed(env, *, dav_enabled: bool = True):
    """种子：用户/角色/配置/书架/进度。返回 factory。"""
    factory = env["factory"]
    from app.core.security import hash_password

    async with factory() as s:
        role = Role(name="admin", description="", permissions=["*"],
                    is_system=True)
        s.add(role)
        await s.flush()
        s.add(User(id=7, username=USER, password_hash="x",
                   display_name="Tester", is_superuser=False,
                   is_active=True, role_ids=[role.id]))
        cfg = WebDavConfig(user_id=7)
        cfg.dav_enabled = dav_enabled
        cfg.dav_secret_hash = hash_password(SECRET)
        s.add(cfg)
        # 书架两本书：一本有旧进度（供 legado 上传合并），一本有新进度（反向合成）
        s.add(ShelfItem(user_id=7, book_url="http://s.example/b1",
                        name="测试书", author="作者一",
                        source_url="http://s.example"))
        s.add(ReadProgress(user_id=7, book_url="http://s.example/b1",
                           chapter_index=5, chapter_title="第五章",
                           updated_at=datetime(2024, 1, 1)))
        s.add(ShelfItem(user_id=7, book_url="http://s.example/b2",
                        name="网页书", author="网作",
                        source_url="http://s.example"))
        s.add(ReadProgress(user_id=7, book_url="http://s.example/b2",
                           chapter_index=9, chapter_title="第九章"))
        await s.commit()
    return factory


async def _call(app, method: str, url: str, **kw) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, url, **kw)


# ------------------------------------------------------------------ 认证
def test_dav_auth_rules(env):
    async def scenario():
        app = await env["build_app"]()
        await _seed(env)

        # 无凭据 → 401 且带 WWW-Authenticate
        r = await _call(app, "PROPFIND", "/dav/legado/")
        assert r.status_code == 401
        assert r.headers["WWW-Authenticate"].lower().startswith("basic")

        # 密码错误 → 401；用户不存在 → 401
        assert (await _call(app, "PROPFIND", "/dav/legado/",
                            headers=basic(USER, "wrong"))).status_code == 401
        assert (await _call(app, "PROPFIND", "/dav/legado/",
                            headers=basic("ghost", SECRET))).status_code == 401

        # 服务端未开启 → 403
        factory = env["factory"]
        async with factory() as s:
            cfg = await s.get(WebDavConfig, 7)
            cfg.dav_enabled = False
            await s.commit()
        r = await _call(app, "PROPFIND", "/dav/legado/", headers=auth_headers())
        assert r.status_code == 403

        # OPTIONS 无需认证，暴露 DAV 能力
        r = await _call(app, "OPTIONS", "/dav/legado/")
        assert r.status_code == 200
        assert "PROPFIND" in r.headers["Allow"]

    asyncio.run(scenario())


# ---------------------------------------------------- legado 上传 → 合并本地
def test_upload_mirrors_to_read_progress(env):
    async def scenario():
        app = await env["build_app"]()
        factory = await _seed(env)
        h = auth_headers()

        # 初始化探测：check() 用 PROPFIND Depth:0
        r = await _call(app, "PROPFIND", "/dav/legado/", headers={
            **h, "Depth": "0"})
        assert r.status_code == 207
        # makeAsDir 前置的 MKCOL 一律成功
        for col in ("books", "background", "bookProgress"):
            rm = await _call(app, "MKCOL", f"/dav/legado/{col}/", headers=h)
            assert rm.status_code == 201

        # legado 上传进度（文件名规则与 UrlUtil.replaceReservedChar 一致）
        fname = "测试书_作者一.json"
        body = payload("测试书", "作者一", idx=42, pos=120, title="第四十二章")
        ru = await _call(
            app, "PUT", f"/dav/legado/bookProgress/{quote(fname, safe='')}",
            headers={**h, "Content-Type": "application/json"},
            content=json.dumps(body, ensure_ascii=False).encode(),
        )
        assert ru.status_code == 201
        assert ru.headers.get("X-Antares-Mirror", "").startswith("ok")

        # 已按 书名+作者 合并到书架对应书籍
        async with factory() as s:
            prog = (await s.execute(select(ReadProgress).where(
                ReadProgress.book_url == "http://s.example/b1"))).scalars().first()
            assert prog.chapter_index == 42
            assert prog.offset == 120
            assert prog.chapter_title == "第四十二章"

        # 读回：内容原样可读（其他 legado 设备同步用）
        rg = await _call(app, "GET",
                         f"/dav/legado/bookProgress/{quote(fname, safe='')}",
                         headers=h)
        assert rg.status_code == 200
        data = rg.json()
        assert data["durChapterIndex"] == 42
        assert data["name"] == "测试书"
        assert "Last-Modified" in rg.headers

    asyncio.run(scenario())


# ---------------------------------------------- 网页端进度 → legado 拉取（合成）
def test_local_progress_served_to_legado(env):
    async def scenario():
        app = await env["build_app"]()
        factory = await _seed(env)
        h = auth_headers()

        # 未上传过、但书架有更新的书：GET 直接由网页端进度合成
        fname = "网页书_网作.json"
        rg = await _call(app, "GET",
                         f"/dav/legado/bookProgress/{quote(fname, safe='')}",
                         headers=h)
        assert rg.status_code == 200
        data = rg.json()
        assert data["durChapterIndex"] == 9
        assert data["durChapterTitle"] == "第九章"
        assert data["name"] == "网页书"

        # GET 后落库，PROPFIND 列表可见（downloadAllBookProgress 流程）
        async with factory() as s:
            assert await s.scalar(select(DavResource.path).where(
                DavResource.path == f"bookProgress/{fname}")) is not None

        rl = await _call(app, "PROPFIND", "/dav/legado/bookProgress/",
                         headers={**h, "Depth": "1"})
        assert rl.status_code == 207
        root = ET.fromstring(rl.content)
        hrefs = [e.text for e in root.iter(f"{NS}href")]
        assert any(h and h.endswith(f"/bookProgress/{quote(fname, safe='')}")
                   for h in hrefs)
        assert any(e.text and "GMT" in e.text
                   for e in root.iter(f"{NS}getlastmodified"))

    asyncio.run(scenario())


# ------------------------------------------------------------- 新者胜语义
def test_stale_upload_does_not_downgrade_local(env):
    async def scenario():
        app = await env["build_app"]()
        await _seed(env)
        h = auth_headers()

        # 过期上传（durChapterTime 远早于本地进度更新时间）
        fname = "网页书_网作.json"
        body = payload("网页书", "网作", idx=1, pos=0, tms=1000, title="第一章")
        ru = await _call(
            app, "PUT", f"/dav/legado/bookProgress/{quote(fname, safe='')}",
            headers=h,
            content=json.dumps(body, ensure_ascii=False).encode())
        assert ru.status_code == 201
        assert ru.headers["X-Antares-Mirror"] == "kept"

        # GET 返回的是较新的本地值，而不是刚上传的过期值
        rg = await _call(app, "GET",
                         f"/dav/legado/bookProgress/{quote(fname, safe='')}",
                         headers=h)
        data = rg.json()
        assert data["durChapterIndex"] == 9
        assert data["durChapterTitle"] == "第九章"

    asyncio.run(scenario())


# ------------------------------------------------------------ 路径与写保护
def test_rebase_and_write_protection(env):
    async def scenario():
        app = await env["build_app"]()
        factory = await _seed(env)
        h = auth_headers()

        # 不带 legado 前缀（用户直接配 /dav/）也落到同一份存储
        fname = "换前缀书_某作.json"
        body = payload("换前缀书", "某作", idx=7, tms=int(time.time() * 1000))
        ru = await _call(app, "PUT",
                         f"/dav/bookProgress/{quote(fname, safe='')}",
                         headers=h,
                         content=json.dumps(body).encode())
        assert ru.status_code == 201
        rg = await _call(app, "GET",
                         f"/dav/legado/bookProgress/{quote(fname, safe='')}",
                         headers=h)
        assert rg.status_code == 200 and rg.json()["durChapterIndex"] == 7

        # 仅开放进度资源：其他路径写入被拒绝
        rb = await _call(app, "PUT", "/dav/legado/books/note.txt",
                         headers=h, content=b"x")
        assert rb.status_code == 403
        rb2 = await _call(app, "PUT", "/dav/legado/bookProgress/sub/a.json",
                          headers=h, content=b"x")
        assert rb2.status_code == 403

        # 删除：204 → 再读 404
        rd = await _call(app, "DELETE",
                         f"/dav/legado/bookProgress/{quote(fname, safe='')}",
                         headers=h)
        assert rd.status_code == 204
        assert (await _call(app, "GET",
                            f"/dav/legado/bookProgress/{quote(fname, safe='')}",
                            headers=h)).status_code == 404

        # 路径穿越被拒
        rt = await _call(app, "PUT", "/dav/legado/../evil.json",
                         headers=h, content=b"x")
        assert rt.status_code in (400, 403, 404)

    asyncio.run(scenario())


# --------------------------------------------------------- 服务端配置 API
def test_server_config_endpoints(env):
    async def scenario():
        app = await env["build_app"]()
        await _seed(env)

        rs = await _call(app, "GET", "/api/webdav/server")
        assert rs.status_code == 200
        info = rs.json()
        assert info["enabled"] is True
        assert info["hasSecret"] is True
        assert info["account"] == USER
        assert info["url"].endswith("/dav/legado/")

        # 关闭 → 状态同步；密码不回显
        rt = await _call(app, "PUT", "/api/webdav/server",
                         json={"enabled": False})
        assert rt.status_code == 200
        info = (await _call(app, "GET", "/api/webdav/server")).json()
        assert info["enabled"] is False

        # 重置密码：明文只出现一次
        rp = await _call(app, "POST", "/api/webdav/server/secret")
        assert rp.status_code == 200
        secret2 = rp.json()["secret"]
        assert secret2 and rp.json().keys() >= {"secret"}

        # 新密码可用、旧密码失效（生成密码自动重新启用）
        ok = await _call(app, "PROPFIND", "/dav/legado/",
                         headers=basic(USER, secret2))
        assert ok.status_code == 207
        bad = await _call(app, "PROPFIND", "/dav/legado/",
                          headers=basic(USER, SECRET))
        assert bad.status_code == 401

    asyncio.run(scenario())


# --------------------------------------------- 双端同步：自动入库 / 待匹配
class FakeSourceEngine:
    """可编排搜索结果的假书源引擎。"""

    def __init__(self, items):
        self.items = items

    async def search_book(self, src, key, page=1):
        return self.items


def test_auto_ingest_creates_shelf_entry(env, monkeypatch):
    async def scenario():
        app = await env["build_app"]()
        factory = await _seed(env)
        h = auth_headers()

        # 假书源：能搜到同名同作者的书；PUT 的后台任务改为 no-op 以便确定性断言
        from app.plugins import registry as reg
        from app.plugins.webdav import sync_ingest as si
        from app.services import toc_queue as tq

        cand = {"name": "新书", "author": "新作者", "bookUrl": "http://s.example/new",
                "origin": "http://s.example", "coverUrl": "http://c/1.jpg",
                "intro": "简介", "lastChapter": "第十章"}
        async with factory() as s:
            s.add(BookSourceRow(source_url="http://s.example",
                                source_name="测试源", raw_json="{}", enabled=True))
            await s.commit()
        monkeypatch.setattr(reg, "get_engine",
                            lambda key=None, ctx=None: FakeSourceEngine([cand]))
        monkeypatch.setattr(tq, "get_engine",
                            lambda key=None, ctx=None: types.SimpleNamespace(
                                get_toc=lambda *a, **k: asyncio.sleep(0, result=[]),
                                book_info=lambda *a, **k: asyncio.sleep(0, result={}),
                            ))
        monkeypatch.setattr(si, "spawn_ingest", lambda *a, **k: None)

        # legado 上传一本本站书架里没有的书 → 标记 no-book（真实场景触发后台入库）
        body = payload("新书", "新作者", idx=12, pos=66,
                       tms=int(time.time() * 1000), title="第十二章")
        ru = await _call(
            app, "PUT",
            f"/dav/legado/bookProgress/{quote('新书_新作者.json', safe='')}",
            headers=h,
            content=json.dumps(body, ensure_ascii=False).encode())
        assert ru.status_code == 201
        assert ru.headers["X-Antares-Mirror"] == "no-book"

        # 直接执行自动入库核心逻辑
        result = await si.auto_match_and_ingest(
            7, name="新书", author="新作者", idx=12, pos=66,
            tms=int(time.time() * 1000), title="第十二章")
        assert result == "matched"

        async with factory() as s:
            item = (await s.execute(select(ShelfItem).where(
                ShelfItem.name == "新书"))).scalars().first()
            assert item is not None
            assert item.book_url == "http://s.example/new"
            assert item.author == "新作者"          # 保留 legado 端原始信息
            prog = (await s.execute(select(ReadProgress).where(
                ReadProgress.book_url == "http://s.example/new"
            ))).scalars().first()
            assert prog is not None and prog.chapter_index == 12
            assert prog.offset == 66
            job = (await s.execute(select(TocJob))).scalars().first()
            assert job is not None and job.book_url.endswith("/new")

        # 入库后再次上传 → 直接命中书架镜像，不再走 no-book
        body2 = payload("新书", "新作者", idx=13, pos=10,
                        tms=int(time.time() * 1000) + 5000, title="第十三章")
        ru2 = await _call(
            app, "PUT",
            f"/dav/legado/bookProgress/{quote('新书_新作者.json', safe='')}",
            headers=h, content=json.dumps(body2, ensure_ascii=False).encode())
        assert ru2.headers["X-Antares-Mirror"].startswith("ok")

    asyncio.run(scenario())


def test_no_match_goes_to_pending(env, monkeypatch):
    async def scenario():
        app = await env["build_app"]()
        await _seed(env)
        h = auth_headers()

        from app.plugins import registry as reg
        from app.plugins.webdav import sync_ingest as si

        monkeypatch.setattr(reg, "get_engine",
                            lambda key=None, ctx=None: FakeSourceEngine([
                                {"name": "完全无关", "author": "谁",
                                 "bookUrl": "http://x/1", "origin": "http://x"}]))
        monkeypatch.setattr(si, "spawn_ingest", lambda *a, **k: None)

        from app.plugins.webdav.sync_ingest import auto_match_and_ingest

        res = await auto_match_and_ingest(
            7, name="搜不到的书", author="佚名", idx=3, pos=0,
            tms=int(time.time() * 1000), title="第三章")
        assert res == "none"

        # 先上传生成 dav 资源（PUT 会存原文件），再查待匹配列表
        body = payload("搜不到的书", "佚名", idx=3, tms=int(time.time() * 1000))
        await _call(app, "PUT",
                    f"/dav/legado/bookProgress/{quote('搜不到的书_佚名.json', safe='')}",
                    headers=h, content=json.dumps(body, ensure_ascii=False).encode())

        rp = await _call(app, "GET", "/api/webdav/server/pending")
        assert rp.status_code == 200
        data = rp.json()
        names = [it["name"] for it in data["items"]]
        assert "搜不到的书" in names
        # 书架已有的书不出现在待匹配里
        assert "测试书" not in names and "网页书" not in names

        # 短时间内重复尝试不再触发搜索（标记生效）
        res2 = await auto_match_and_ingest(
            7, name="搜不到的书", author="佚名", idx=4, pos=0,
            tms=int(time.time() * 1000), title="第四章")
        assert res2 in ("recent-none", "none")

    asyncio.run(scenario())


def test_get_reconciles_newer_cloud_into_local(env):
    async def scenario():
        app = await env["build_app"]()
        factory = await _seed(env)
        h = auth_headers()

        # 直接在存储层放一份比本地进度新的云端记录（模拟另一台设备上传）
        fname = "网页书_网作.json"
        newer_ms = int(time.time() * 1000) + 60_000
        async with factory() as s:
            s.add(DavResource(
                user_id=7, path=f"bookProgress/{fname}",
                content=json.dumps(payload("网页书", "网作", idx=20, pos=88,
                                           tms=newer_ms, title="第二十章"),
                                   ensure_ascii=False),
                size=180))
            await s.commit()

        rg = await _call(app, "GET",
                         f"/dav/legado/bookProgress/{quote(fname, safe='')}",
                         headers=h)
        assert rg.status_code == 200
        assert rg.json()["durChapterIndex"] == 20

        # 云端较新 → 已回写网页端 ReadProgress
        async with factory() as s:
            prog = (await s.execute(select(ReadProgress).where(
                ReadProgress.book_url == "http://s.example/b2"))).scalars().first()
            assert prog.chapter_index == 20
            assert prog.offset == 88
            assert prog.chapter_title == "第二十章"

    asyncio.run(scenario())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
