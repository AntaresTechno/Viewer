"""Custom protocol bridge and Legado book-source import integration."""
from __future__ import annotations

import asyncio
import json
import shutil
import types
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models
from app.legado_rule.net import StrResponse


def test_native_launcher_builds_web_confirmation_url():
    from app.plugins.protocol_bridge.native_launcher import bridge_url

    uri = "legado://import/bookSource?src=https%3A%2F%2Fexample.test%2Fs.json"
    target = bridge_url("http://127.0.0.1:8000/", uri, ["legado", "yuedu"])
    assert target.startswith("http://127.0.0.1:8000/protocol?uri=legado%3A")
    with pytest.raises(ValueError):
        bridge_url("http://127.0.0.1:8000", "other://open", ["legado"])


def test_linux_native_registration_lifecycle(monkeypatch):
    from app.plugins.protocol_bridge import native_registration as native

    root = Path.cwd() / ".tmp-protocol-tests" / uuid.uuid4().hex
    state = root / "config"
    desktop = root / "apps" / "viewer-protocol-bridge.desktop"
    monkeypatch.setattr(native, "_platform", lambda: "linux")
    monkeypatch.setattr(native, "_paths", lambda system=None: (state, desktop))
    monkeypatch.setattr(native.shutil, "which", lambda name: None)
    try:
        status = native.install_native_bridge(
            "http://127.0.0.1:8000", ["legado", "yuedu"]
        )
        assert status["installed"] is True
        assert status["autoStart"] is True
        text = desktop.read_text(encoding="utf-8")
        assert "x-scheme-handler/legado;" in text
        assert "%u" in text

        removed = native.uninstall_native_bridge()
        assert removed["installed"] is False
        assert not desktop.exists()
    finally:
        if root.exists():
            shutil.rmtree(root)


def test_legado_resolver_matches_md3_uri_shape():
    from app.plugins.protocol_bridge.service import ProtocolBridgeError, resolve_uri
    from app.plugins.protocol_legado import plugin

    plugin.create_router(None)
    source = "https://qyyuapi.com/sy/hx/V3.0.json"
    action = resolve_uri(
        "legado://import/bookSource?src=" + quote(source, safe="")
    )
    assert action.handler == "protocol_legado"
    assert action.action == "source.import"
    assert action.payload == {"src": source}
    assert action.execute_path == "/api/protocol-legado/import/sources"

    with pytest.raises(ProtocolBridgeError):
        resolve_uri("legado://import/rssSource?src=" + quote(source, safe=""))
    with pytest.raises(ProtocolBridgeError):
        resolve_uri("legado://import/bookSource?src=file%3A%2F%2Fsecret.json")


def test_protocol_plugins_ship_separate_management_uis():
    from app.plugins.registry import discover_plugins, load_plugin_ui

    plugins = discover_plugins(force=True)
    bridge = plugins["protocol_bridge"]
    legado = plugins["protocol_legado"]
    bridge_html = load_plugin_ui(bridge)
    legado_html = load_plugin_ui(legado)

    assert "协议桥设置" in bridge_html
    assert "注册到当前系统" in bridge_html
    assert "Legado 协议工具" in legado_html
    assert "/import/sources" in legado_html
    assert bridge_html != legado_html


def test_plugin_admin_exposes_generic_ui_endpoint():
    from app.core.deps import require_superuser
    from app.plugins.plugins_admin import plugin as admin_plugin

    app = FastAPI()
    app.include_router(admin_plugin.create_router(None), prefix="/api/plugins")
    app.dependency_overrides[require_superuser] = lambda: object()

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            listed = await client.get("/api/plugins")
            assert listed.status_code == 200
            by_name = {item["name"]: item for item in listed.json()["items"]}
            assert by_name["protocol_bridge"]["ui"] == {"title": "协议桥设置"}
            assert by_name["protocol_legado"]["ui"] == {
                "title": "Legado 协议工具"
            }

            opened = await client.get("/api/plugins/protocol_legado/ui")
            assert opened.status_code == 200
            assert opened.json()["apiBase"] == "/protocol-legado"
            assert "Legado 协议工具" in opened.json()["html"]

    asyncio.run(scenario())


def test_mixed_legado_sources_are_classified_into_separate_stores():
    from app.plugins.protocol_legado.importer import import_classified_sources

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    values = [
        {
            "bookSourceName": "普通小说",
            "bookSourceUrl": "https://book.example.test",
            "bookSourceType": 0,
        },
        {
            "bookSourceName": "漫画源",
            "bookSourceUrl": "https://comic.example.test",
            "bookSourceType": 2,
        },
        {
            "sourceName": "文章订阅",
            "sourceUrl": "https://rss.example.test/feed",
            "type": 0,
        },
        {
            "sourceName": "短剧源",
            "sourceUrl": "https://video.example.test/feed",
            "type": 2,
        },
    ]

    async def scenario():
        async with engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.create_all)
        async with factory() as session:
            result = await import_classified_sources(
                session, values, known_engines={"legado"}
            )
        assert result["added"] == 4
        assert result["classified"] == {
            "books": 1,
            "subscriptions": 1,
            "comic": 1,
            "audio": 0,
            "video": 1,
        }
        async with factory() as session:
            books = (await session.execute(select(models.BookSourceRow))).scalars().all()
            rss = (await session.execute(select(models.RssSourceRow))).scalars().all()
            media = (await session.execute(select(models.MediaSourceRow))).scalars().all()
        assert [row.source_name for row in books] == ["普通小说"]
        assert [row.source_name for row in rss] == ["文章订阅"]
        assert {row.source_name: row.media_kind for row in media} == {
            "漫画源": "comic",
            "短剧源": "video",
        }
        await engine.dispose()

    asyncio.run(scenario())


def test_protocol_resolve_confirm_and_import(monkeypatch):
    from app.core import db as core_db
    from app.core import deps as core_deps
    from app.legado_rule import net
    from app.plugins import registry

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(core_db.db, "engine", engine, raising=False)
    monkeypatch.setattr(core_db.db, "session_factory", factory, raising=False)

    user = types.SimpleNamespace(id=1, is_superuser=True)

    async def fake_current_user():
        return user, ["*"]

    def fake_require_perm(_permission: str):
        async def checker(current=core_deps.Depends(fake_current_user)):
            return current

        return checker

    monkeypatch.setattr(core_deps, "get_current_user", fake_current_user)
    monkeypatch.setattr(core_deps, "require_perm", fake_require_perm)
    monkeypatch.setattr(registry, "engine_keys", lambda: ["legado"])

    source_url = "https://qyyuapi.com/sy/hx/V3.0.json"
    source_json = json.dumps({
        "bookSourceName": "协议导入测试源",
        "bookSourceUrl": "https://books.example.test",
        "bookSourceGroup": "测试,协议",
    }, ensure_ascii=False)

    async def fake_fetch(url: str, **kwargs):
        assert url == source_url
        return StrResponse(url, source_json, 200, {})

    monkeypatch.setattr(net, "fetch", fake_fetch)

    from app.plugins.protocol_bridge import plugin as bridge_plugin
    from app.plugins.protocol_legado import plugin as legado_plugin

    app = FastAPI()
    app.include_router(bridge_plugin.create_router(None), prefix="/api/protocol-bridge")
    app.include_router(legado_plugin.create_router(None), prefix="/api/protocol-legado")

    async def scenario():
        async with engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.create_all)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            uri = "legado://import/bookSource?src=" + quote(source_url, safe="")
            resolved = await client.post(
                "/api/protocol-bridge/resolve", json={"uri": uri}
            )
            assert resolved.status_code == 200, resolved.text
            action = resolved.json()
            assert action["requiresConfirmation"] is True
            assert action["bridgePath"].startswith("/protocol?uri=")

            imported = await client.post(action["executePath"], json=action["payload"])
            assert imported.status_code == 201, imported.text
            assert imported.json()["added"] == 1
            assert imported.json()["classified"]["books"] == 1

        async with factory() as session:
            rows = (await session.execute(select(models.BookSourceRow))).scalars().all()
            assert len(rows) == 1
            assert rows[0].source_name == "协议导入测试源"
            assert rows[0].source_group == "测试"
            assert rows[0].engine == "legado"
        await engine.dispose()

    asyncio.run(scenario())
