"""Legado/yuedu protocol adapter built on the generic protocol bridge."""

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import APIRouter

from ..protocol_bridge.service import ProtocolAction, register_handler

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext


PLUGIN = {
    "kind": "plugin",
    "name": "protocol_legado",
    "mount": "protocol-legado",
    "title": "Legado 协议",
    "version": "1.2.0",
    "description": "通过协议桥处理 legado:// 与 yuedu://，自动分类导入书源、订阅源和媒体源",
    "ui": {
        "entry": "ui/index.html",
        "title": "Legado 协议工具",
    },
    "requires": ["protocol_bridge", "engine_legado", "rss", "media"],
    "order": 7,
    "permissions": [],
}


def _source_url(parsed) -> str | None:
    values = parse_qs(parsed.query, keep_blank_values=True).get("src", [])
    source = values[0].strip() if values else ""
    if not source or len(source) > 4096:
        return None
    try:
        target = urlsplit(source.removesuffix("#requestWithoutUA"))
    except ValueError:
        return None
    if target.scheme.lower() not in {"http", "https"} or not target.netloc:
        return None
    if target.username or target.password:
        return None
    return source


def _resolve_legado(parsed) -> ProtocolAction | None:
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/").lower()
    direct = host == "import" and path == "/booksource"
    legacy = host == "booksource" and path == "/importonline"
    if not (direct or legacy):
        return None
    source = _source_url(parsed)
    if source is None:
        return None
    return ProtocolAction(
        handler="protocol_legado",
        scheme=parsed.scheme.lower(),
        action="source.import",
        title="导入 Legado 来源",
        description="下载 JSON 后自动分类为书源、订阅源、漫画、音频或视频源。",
        payload={"src": source},
        execute_path="/api/protocol-legado/import/sources",
    )


def create_router(ctx: "PluginContext") -> APIRouter:
    from fastapi import Depends, HTTPException
    from pydantic import BaseModel, Field
    from sqlalchemy.ext.asyncio import AsyncSession

    from ...core.db import get_db
    from ...core.deps import get_current_user, require_perm
    from ...legado_rule.net import fetch as net_fetch
    from ...plugins.registry import engine_keys
    from .importer import (
        AutoSourceImportError,
        classify_sources,
        import_classified_sources,
        parse_source_payload,
    )

    register_handler("protocol_legado", {"legado", "yuedu"}, _resolve_legado)
    router = APIRouter(tags=["protocol-legado"])

    class ImportBody(BaseModel):
        src: str = Field(min_length=1, max_length=4096)

    class ResolveBody(BaseModel):
        uri: str = Field(min_length=1, max_length=8192)

    @router.post("/resolve")
    async def resolve_legado_uri(
        body: ResolveBody,
        current=Depends(require_perm("protocol_bridge.resolve")),
    ):
        try:
            parsed = urlsplit(body.uri.strip())
        except ValueError as exc:
            raise HTTPException(400, "协议链接格式不正确") from exc
        action = _resolve_legado(parsed)
        if action is None:
            raise HTTPException(400, "不是受支持的 Legado 书源导入协议")
        return action.to_dict(body.uri.strip())

    def has_permission(current, key: str) -> bool:
        user, permissions = current
        return bool(
            user.is_superuser
            or "*" in permissions
            or key in permissions
            or key.split(".")[0] + ".*" in permissions
        )

    async def run_source_import(
        body: ImportBody,
        current,
        db: AsyncSession,
    ):
        fake_uri = "legado://import/bookSource?src=" + quote(body.src, safe="")
        action = _resolve_legado(urlsplit(fake_uri))
        if action is None:
            raise HTTPException(400, "来源地址必须是无账号信息的 HTTP(S) URL")
        request_url = action.payload["src"]
        headers = None
        if request_url.endswith("#requestWithoutUA"):
            request_url = request_url.removesuffix("#requestWithoutUA")
            headers = {"User-Agent": "null"}
        response = await net_fetch(request_url, headers=headers)
        if response.error or response.status >= 400:
            detail = response.error or f"HTTP {response.status}"
            raise HTTPException(400, f"拉取失败: {detail}")
        try:
            values = parse_source_payload(response.body)
            plan = classify_sources(values)
        except AutoSourceImportError as exc:
            raise HTTPException(400, str(exc)) from exc
        required = []
        if plan["buckets"]["books"]:
            required.append("books.sources.manage")
        if plan["buckets"]["subscriptions"]:
            required.append("rss.manage")
        if plan["buckets"]["media"]:
            required.append("media.sources.manage")
        missing = [key for key in required if not has_permission(current, key)]
        if missing:
            raise HTTPException(403, "缺少来源导入权限：" + "、".join(missing))
        result = await import_classified_sources(
            db,
            values,
            known_engines=set(engine_keys()),
        )
        result.update({
            "source": request_url,
            "format": "auto",
            "engine": "legado",
        })
        return result

    @router.post("/import/sources", status_code=201)
    async def import_sources(
        body: ImportBody,
        current=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        return await run_source_import(body, current, db)

    @router.post("/import/book-source", status_code=201)
    async def import_book_source_compat(
        body: ImportBody,
        current=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """Backward-compatible endpoint; now uses automatic classification."""
        return await run_source_import(body, current, db)

    return router
