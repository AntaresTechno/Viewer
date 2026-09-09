"""RSS subscriptions — Legado source import, feed browsing and reading."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .parser import fetch_article_content, fetch_articles, parse_sorts, safe_html

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

PLUGIN = {
    "kind": "plugin",
    "name": "rss",
    "mount": "rss",
    "title": "订阅",
    "version": "1.0.0",
    "description": "兼容 Legado 订阅源，支持 RSS/Atom 与自定义规则",
    "order": 35,
    "permissions": [
        ("rss.read", "查看订阅与文章"),
        ("rss.manage", "导入、删除及启停订阅源"),
        ("rss.favorite", "管理订阅文章收藏"),
    ],
}


class ImportBody(BaseModel):
    data: str | None = Field(default=None, description="Legado RssSource JSON")
    url: str | None = None


class IdsBody(BaseModel):
    ids: list[int]


class SourceEnabledBody(IdsBody):
    enabled: bool


def _source_dict(row) -> dict:
    try:
        source = json.loads(row.raw_json)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "订阅源 JSON 损坏") from exc
    if not isinstance(source, dict):
        return {}
    # Runtime/user-managed fields live in columns and override the imported
    # snapshot. This also makes exported JSON reflect later enable/order edits.
    source.update({
        "sourceUrl": row.source_url,
        "sourceName": row.source_name,
        "sourceIcon": row.source_icon,
        "sourceGroup": row.source_group,
        "sourceComment": row.source_comment,
        "enabled": row.enabled,
        "customOrder": row.custom_order,
    })
    return source


def _source_json(row) -> dict:
    source = _source_dict(row)
    return {
        "id": row.id,
        "sourceUrl": row.source_url,
        "sourceName": row.source_name,
        "sourceIcon": row.source_icon,
        "sourceGroup": row.source_group,
        "sourceComment": row.source_comment,
        "enabled": row.enabled,
        "customOrder": row.custom_order,
        "articleStyle": int(source.get("articleStyle") or 0),
        "singleUrl": bool(source.get("singleUrl", False)),
        "hasSearch": bool(str(source.get("searchUrl") or "").strip()),
    }


def _plain_excerpt(value: str, limit: int = 240) -> str:
    text = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.db import get_db
    from ...core.deps import require_perm
    from ...legado_rule.net import fetch
    from ...models import RssArticleRow, RssFavorite, RssReadState, RssSourceRow
    from ...services.rss_source_import import (
        RssSourceImportError,
        import_rss_sources,
    )

    router = APIRouter(tags=["rss"])

    async def load_source(db: AsyncSession, source_url: str, enabled: bool = True):
        row = await db.scalar(select(RssSourceRow).where(RssSourceRow.source_url == source_url))
        if row is None:
            raise HTTPException(404, "订阅源不存在")
        if enabled and not row.enabled:
            raise HTTPException(400, "该订阅源已停用")
        return row

    @router.get("/sources")
    async def list_sources(
        current=Depends(require_perm("rss.read")),
        db: AsyncSession = Depends(get_db),
    ):
        rows = (await db.execute(
            select(RssSourceRow).order_by(RssSourceRow.custom_order, RssSourceRow.id)
        )).scalars().all()
        groups = sorted({
            group.strip()
            for row in rows
            for group in re.split(r"[,，;；]", row.source_group or "")
            if group.strip()
        })
        return {"items": [_source_json(r) for r in rows], "groups": groups}

    @router.post("/sources/import", status_code=201)
    async def import_sources(
        body: ImportBody,
        current=Depends(require_perm("rss.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        text = body.data
        if body.url:
            response = await fetch(body.url)
            if response.error or not response.ok:
                raise HTTPException(400, f"拉取失败: {response.error or response.status}")
            text = response.body
        if not text or not text.strip():
            raise HTTPException(400, "内容为空")
        try:
            obj = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"JSON 解析失败: {exc}") from exc
        try:
            return await import_rss_sources(db, obj)
        except RssSourceImportError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/sources/delete")
    async def delete_sources(
        body: IdsBody,
        current=Depends(require_perm("rss.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        rows = (await db.execute(select(RssSourceRow).where(RssSourceRow.id.in_(body.ids)))).scalars().all()
        origins = [r.source_url for r in rows]
        article_ids = (await db.execute(
            select(RssArticleRow.id).where(RssArticleRow.origin.in_(origins))
        )).scalars().all() if origins else []
        if article_ids:
            await db.execute(delete(RssReadState).where(RssReadState.article_id.in_(article_ids)))
            await db.execute(delete(RssFavorite).where(RssFavorite.article_id.in_(article_ids)))
        if origins:
            await db.execute(delete(RssArticleRow).where(RssArticleRow.origin.in_(origins)))
        await db.execute(delete(RssSourceRow).where(RssSourceRow.id.in_(body.ids)))
        await db.commit()
        return {"deleted": len(rows)}

    @router.post("/sources/{source_id}/toggle")
    async def toggle_source(
        source_id: int,
        current=Depends(require_perm("rss.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        row = await db.get(RssSourceRow, source_id)
        if row is None:
            raise HTTPException(404, "订阅源不存在")
        row.enabled = not row.enabled
        await db.commit()
        return {"enabled": row.enabled}

    @router.post("/sources/batch-enabled")
    async def set_sources_enabled(
        body: SourceEnabledBody,
        current=Depends(require_perm("rss.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        rows = (
            await db.execute(
                select(RssSourceRow).where(RssSourceRow.id.in_(body.ids))
            )
        ).scalars().all()
        for row in rows:
            row.enabled = body.enabled
        await db.commit()
        return {"updated": len(rows), "enabled": body.enabled}

    @router.get("/sources/export")
    async def export_sources(
        ids: list[int] = Query(default=[]),
        current=Depends(require_perm("rss.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        stmt = select(RssSourceRow).order_by(RssSourceRow.custom_order, RssSourceRow.id)
        if ids:
            stmt = stmt.where(RssSourceRow.id.in_(ids))
        rows = (await db.execute(stmt)).scalars().all()
        return [_source_dict(r) for r in rows]

    @router.get("/sorts")
    async def source_sorts(
        source_url: str,
        current=Depends(require_perm("rss.read")),
        db: AsyncSession = Depends(get_db),
    ):
        row = await load_source(db, source_url)
        return {"items": await asyncio.to_thread(parse_sorts, _source_dict(row))}

    async def state_sets(db: AsyncSession, user_id: int, article_ids: list[int]):
        if not article_ids:
            return set(), set()
        read_ids = set((await db.execute(
            select(RssReadState.article_id).where(
                RssReadState.user_id == user_id, RssReadState.article_id.in_(article_ids)
            )
        )).scalars().all())
        favorite_ids = set((await db.execute(
            select(RssFavorite.article_id).where(
                RssFavorite.user_id == user_id, RssFavorite.article_id.in_(article_ids)
            )
        )).scalars().all())
        return read_ids, favorite_ids

    async def serialize_articles(db: AsyncSession, rows: list, user_id: int):
        read_ids, favorite_ids = await state_sets(db, user_id, [r.id for r in rows])
        return [{
            "id": r.id, "origin": r.origin, "sort": r.sort, "title": r.title,
            "link": r.link, "pubDate": r.pub_date,
            "description": _plain_excerpt(r.description or r.content),
            "image": r.image, "read": r.id in read_ids, "favorite": r.id in favorite_ids,
        } for r in rows]

    @router.get("/articles")
    async def articles(
        source_url: str,
        sort_name: str = "",
        sort_url: str = "",
        page: int = Query(default=1, ge=1),
        search_key: str = "",
        current=Depends(require_perm("rss.read")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        source_row = await load_source(db, source_url)
        source = _source_dict(source_row)
        target_url = (
            sort_url if search_key and page > 1 and sort_url
            else str(source.get("searchUrl") or "") if search_key
            else sort_url
        )
        if not target_url:
            sorts = await asyncio.to_thread(parse_sorts, source)
            target_url = sorts[0]["url"] if sorts else source_url
        warning = ""
        next_url: str | None = None
        try:
            parsed, next_url = await fetch_articles(
                source, sort_name, target_url, page, search_key or None
            )
            out_rows = []
            for index, item in enumerate(parsed):
                link = str(item.get("link") or "").strip()
                if not link:
                    continue
                row = await db.scalar(select(RssArticleRow).where(
                    RssArticleRow.origin == source_url,
                    RssArticleRow.link == link,
                    RssArticleRow.sort == sort_name,
                ))
                values = {
                    "title": str(item.get("title") or ""),
                    "pub_date": str(item.get("pubDate") or ""),
                    "description": str(item.get("description") or ""),
                    "image": str(item.get("image") or ""),
                    "position": (page * 10000) + index,
                    "fetched_at": datetime.now(timezone.utc),
                }
                incoming_content = str(item.get("content") or "")
                if row is None:
                    row = RssArticleRow(
                        origin=source_url, sort=sort_name, link=link,
                        content=incoming_content, **values,
                    )
                    db.add(row)
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                    if incoming_content:
                        row.content = incoming_content
                out_rows.append(row)
            await db.commit()
            for row in out_rows:
                await db.refresh(row)
        except Exception as exc:  # noqa: BLE001 - cached articles are the offline fallback
            warning = str(exc)
            out_rows = (await db.execute(
                select(RssArticleRow).where(
                    RssArticleRow.origin == source_url, RssArticleRow.sort == sort_name
                ).order_by(RssArticleRow.position).limit(100)
            )).scalars().all()
            if not out_rows:
                raise HTTPException(502, f"获取订阅失败: {exc}") from exc
        return {
            "items": await serialize_articles(db, out_rows, user.id),
            "nextUrl": next_url, "warning": warning,
        }

    @router.get("/articles/{article_id}/content")
    async def article_content(
        article_id: int,
        current=Depends(require_perm("rss.read")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        row = await db.get(RssArticleRow, article_id)
        if row is None:
            raise HTTPException(404, "文章不存在")
        source_row = await load_source(db, row.origin, enabled=False)
        article = {
            "origin": row.origin, "sort": row.sort, "title": row.title,
            "link": row.link, "pubDate": row.pub_date,
            "description": row.description, "content": row.content, "image": row.image,
        }
        content = await fetch_article_content(_source_dict(source_row), article)
        if content and not row.content:
            row.content = content
        state = await db.scalar(select(RssReadState).where(
            RssReadState.user_id == user.id, RssReadState.article_id == row.id
        ))
        if state is None:
            db.add(RssReadState(user_id=user.id, article_id=row.id))
        else:
            state.read_at = datetime.now(timezone.utc)
        favorite = await db.scalar(select(RssFavorite).where(
            RssFavorite.user_id == user.id, RssFavorite.article_id == row.id
        ))
        await db.commit()
        return {
            **article, "id": row.id, "content": safe_html(content, row.link),
            "read": True, "favorite": favorite is not None,
        }

    @router.post("/articles/{article_id}/read")
    async def mark_read(
        article_id: int,
        current=Depends(require_perm("rss.read")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        if await db.get(RssArticleRow, article_id) is None:
            raise HTTPException(404, "文章不存在")
        state = await db.scalar(select(RssReadState).where(
            RssReadState.user_id == user.id, RssReadState.article_id == article_id
        ))
        if state is None:
            db.add(RssReadState(user_id=user.id, article_id=article_id))
        await db.commit()
        return {"read": True}

    @router.post("/articles/{article_id}/favorite")
    async def toggle_favorite(
        article_id: int,
        current=Depends(require_perm("rss.favorite")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        if await db.get(RssArticleRow, article_id) is None:
            raise HTTPException(404, "文章不存在")
        favorite = await db.scalar(select(RssFavorite).where(
            RssFavorite.user_id == user.id, RssFavorite.article_id == article_id
        ))
        enabled = favorite is None
        if favorite is None:
            db.add(RssFavorite(user_id=user.id, article_id=article_id))
        else:
            await db.delete(favorite)
        await db.commit()
        return {"favorite": enabled}

    @router.get("/favorites")
    async def favorites(
        current=Depends(require_perm("rss.favorite")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        rows = (await db.execute(
            select(RssArticleRow).join(RssFavorite, RssFavorite.article_id == RssArticleRow.id)
            .where(RssFavorite.user_id == user.id)
            .order_by(RssFavorite.created_at.desc())
        )).scalars().all()
        return {"items": await serialize_articles(db, rows, user.id)}

    return router
