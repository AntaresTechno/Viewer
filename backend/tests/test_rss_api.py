"""RSS plugin API lifecycle on an isolated in-memory database."""
from __future__ import annotations

import asyncio
import json
import types

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import models


def test_rss_import_browse_read_favorite_and_delete(monkeypatch):
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

    user = types.SimpleNamespace(id=31, username="rss-user", is_superuser=False)

    async def fake_current_user():
        return user, ["*"]

    def fake_require_perm(_permission: str):
        async def checker(current=core_deps.Depends(fake_current_user)):
            return current

        return checker

    monkeypatch.setattr(core_deps, "get_current_user", fake_current_user)
    monkeypatch.setattr(core_deps, "require_perm", fake_require_perm)

    from app.plugins.rss import plugin

    async def fake_articles(source, sort_name, sort_url, page=1, search_key=None):
        assert source["sourceUrl"] == "https://rss.test/feed"
        assert sort_url == "https://rss.test/news.xml"
        return ([{
            "origin": source["sourceUrl"], "sort": sort_name,
            "title": "测试文章", "link": "https://rss.test/post/1",
            "pubDate": "2026-09-07", "description": "摘要",
            "content": "", "image": "https://rss.test/cover.jpg",
        }], None)

    async def fake_content(source, article):
        assert source["sourceName"] == "测试订阅"
        return "<p>正文</p>"

    monkeypatch.setattr(plugin, "fetch_articles", fake_articles)
    monkeypatch.setattr(plugin, "fetch_article_content", fake_content)

    app = FastAPI()
    app.include_router(plugin.create_router(None), prefix="/api/rss")

    async def call(client, method, path, **kwargs):
        return await client.request(method, path, **kwargs)

    async def scenario():
        async with engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.create_all)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            source = {
                "sourceUrl": "https://rss.test/feed",
                "sourceName": "测试订阅",
                "sourceGroup": "新闻,技术",
                "sortUrl": "新闻::https://rss.test/news.xml",
                "ruleArticles": "class.item",
                "ruleTitle": "tag.a@text",
                "ruleLink": "tag.a@href",
            }
            imported = await call(client, "POST", "/api/rss/sources/import", json={
                "data": json.dumps(source, ensure_ascii=False),
            })
            assert imported.status_code == 201, imported.text
            assert imported.json()["added"] == 1

            listed = (await call(client, "GET", "/api/rss/sources")).json()
            assert listed["groups"] == ["技术", "新闻"]
            source_id = listed["items"][0]["id"]

            sorts = await call(client, "GET", "/api/rss/sorts", params={
                "source_url": source["sourceUrl"],
            })
            assert sorts.json()["items"][0]["name"] == "新闻"

            feed = await call(client, "GET", "/api/rss/articles", params={
                "source_url": source["sourceUrl"],
                "sort_name": "新闻", "sort_url": "https://rss.test/news.xml",
            })
            assert feed.status_code == 200
            article = feed.json()["items"][0]
            assert article["read"] is False

            content = await call(client, "GET", f"/api/rss/articles/{article['id']}/content")
            assert content.json()["content"] == "<p>正文</p>"
            assert content.json()["read"] is True

            favorite = await call(client, "POST", f"/api/rss/articles/{article['id']}/favorite")
            assert favorite.json()["favorite"] is True
            favorites = (await call(client, "GET", "/api/rss/favorites")).json()
            assert [item["title"] for item in favorites["items"]] == ["测试文章"]

            deleted = await call(client, "POST", "/api/rss/sources/delete", json={"ids": [source_id]})
            assert deleted.json()["deleted"] == 1
            assert (await call(client, "GET", "/api/rss/sources")).json()["items"] == []
        await engine.dispose()

    asyncio.run(scenario())
