"""engine_legado — 阅读(legado)书源规则引擎插件。

这是第一个「源规则引擎」插件：books 插件不再直接绑定解析实现，
而是按 BookSourceRow.engine 字段把搜索/详情/目录/正文请求分发给
对应引擎。未来接入其他规则体系（如不同站点私有 JSON 协议）只需
再写一个带 ``ENGINE`` + ``create_engine`` 的插件包。

本插件同时承担 API 角色（``/api/legado``）：暴露书源登录端点，
镜像 legado 的 SourceLogin 流程（loginUi 表单 / login() JS /
登录头 / Cookie）。端点清单：

- GET  /login/form?source_url=    登录表单（rows + 已存值 + 登录头 + Cookie）
- POST /login/submit              保存登录信息并执行 login() JS
- POST /login/action              执行登录页按钮动作
- POST /login/cookie              手工写入站点 Cookie（Web 模式登录）
- POST /login/header/remove       清除登录头与域名 Cookie
- POST /login/info/remove         退出登录（清除登录信息）
"""
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

meta = {
    "name": "engine_legado",
    "mount": "legado",
    "title": "Legado 书源引擎",
    "version": "1.1.0",
    "description": "阅读(legado)兼容书源规则引擎（jsoup/JSONPath/XPath/Regex + AnalyzeUrl）与书源登录",
    "order": 5,
    "permissions": [
        ("legado.login", "管理书源登录（登录/退出/登录头/Cookie）"),
    ],
}

ENGINE = {
    "key": "legado",
    "title": "Legado 书源",
    "version": "1.1.0",
    "description": "阅读(legado)书源规则：AnalyzeRule / AnalyzeUrl / WebBook 全流程",
}


class LegadoEngine:
    """Adapter implementing the source-engine interface over legado_rule."""

    key = "legado"

    def __init__(self, ctx):
        self.ctx = ctx

    # -- introspection -------------------------------------------------
    def matches(self, raw: dict) -> bool:
        """Whether a raw source JSON blob looks like a legado book source."""
        if not isinstance(raw, dict):
            return False
        return bool(raw.get("bookSourceUrl"))

    async def search_book(self, src: dict, key: str, page: int = 1) -> list[dict]:
        from ...legado_rule import web_book

        return await web_book.search_book(src, key, max(1, page))

    async def explore_kinds(self, src: dict) -> list[dict]:
        from ...legado_rule import web_book

        return web_book.explore_kinds(src)

    async def explore_book(self, src: dict, url: str, page: int = 1) -> list[dict]:
        from ...legado_rule import web_book

        return await web_book.explore_book(src, url, max(1, page))

    async def book_info(self, src: dict, book: dict) -> dict:
        from ...legado_rule import web_book

        return await web_book.book_info(src, book)

    async def get_toc(self, src: dict, book: dict,
                      toc_url: str | None = None) -> list[dict]:
        from ...legado_rule import web_book

        return await web_book.get_toc(src, book, toc_url)

    async def get_content(self, src: dict, book: dict, chapter: dict,
                          next_chapter_url: str | None = None,
                          base_url: str | None = None) -> str:
        from ...legado_rule import web_book

        return await web_book.get_content(
            src, book, chapter, next_chapter_url, base_url=base_url
        )


def create_engine(ctx: "PluginContext") -> LegadoEngine:
    return LegadoEngine(ctx)


def create_router(ctx: "PluginContext") -> APIRouter:
    """书源登录端点（/api/legado/login/*）。

    注意：按 docs/plugin-spec.md §7，API 插件不开 future-annotations，
    请求体模型定义在本函数内部。
    """
    import asyncio
    import json

    from fastapi import Depends, HTTPException
    from pydantic import BaseModel, Field
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from ...core.db import get_db
    from ...core.deps import require_perm
    from ...legado_rule import source_state, source_login
    from ...legado_rule.source_bridge import SourceLoginBridge
    from ...models import BookSourceRow

    router = APIRouter(tags=["legado"])

    async def _load_source(db: AsyncSession, source_url: str) -> dict:
        if not source_url:
            raise HTTPException(400, "缺少 source_url")
        row = await db.scalar(
            select(BookSourceRow).where(BookSourceRow.source_url == source_url)
        )
        if not row:
            raise HTTPException(404, f"书源不存在: {source_url}")
        try:
            src = json.loads(row.raw_json)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, "书源 JSON 解析失败") from exc
        if not isinstance(src, dict):
            raise HTTPException(500, "书源 JSON 不是对象")
        return src

    def _resolve_title(source: dict, row: dict, info: dict) -> str:
        """viewName 解析：'字面量' 直取；否则按 loginUi 的 JS 求值。"""
        view_name = row.get("viewName")
        if not view_name or not isinstance(view_name, str):
            return str(row.get("name") or "")
        view_name = view_name.strip()
        if 3 <= len(view_name) <= 19 and view_name.startswith("'") \
                and view_name.endswith("'"):
            return view_name[1:-1]
        login_js = source_login.get_login_js(source) or ""
        bridge = SourceLoginBridge(source, base_url=source_login.source_key(source))
        try:
            ev = source_login._eval_login_snippet(  # noqa: SLF001
                source, f"{login_js}\n{view_name}", info, bridge
            )
        except Exception:  # noqa: BLE001 - 标题求值失败回退字段名
            return str(row.get("name") or "")
        text = "" if ev is None else str(ev).strip()
        return text or str(row.get("name") or "")

    @router.get("/login/form")
    async def login_form(
        source_url: str,
        current=Depends(require_perm("legado.login")),
        db: AsyncSession = Depends(get_db),
    ):
        src = await _load_source(db, source_url)
        key = source_login.source_key(src)
        mode = source_login.login_mode(src)
        stored = source_state.get_login_info(key)
        info = stored if stored is not None \
            else source_login.default_login_info(src)
        rows = source_login.login_rows(src, info)
        login_header = source_state.get_login_header(key)
        return {
            "sourceUrl": key,
            "sourceName": str(src.get("bookSourceName") or ""),
            "mode": mode,
            "webUrl": source_login.web_login_url(src),
            "rows": [
                {
                    "name": r["name"],
                    "title": _resolve_title(src, r, info),
                    "type": r["type"],
                    "action": r.get("action"),
                    "chars": r.get("chars"),
                    "default": r.get("default"),
                }
                for r in rows
            ],
            "values": info,
            "hasInfo": stored is not None,
            "hasLoginHeader": bool(login_header),
            "loginHeader": login_header,
            "cookie": source_state.get_cookie(key),
        }

    class SubmitBody(BaseModel):
        source_url: str
        values: dict[str, str] | None = Field(
            default=None, description="登录表单数据；null 清除登录信息"
        )

    @router.post("/login/submit")
    async def login_submit(
        body: SubmitBody,
        current=Depends(require_perm("legado.login")),
        db: AsyncSession = Depends(get_db),
    ):
        src = await _load_source(db, body.source_url)
        key = source_login.source_key(src)
        if body.values is None or not body.values:
            source_state.remove_login_info(key)
            return {"ok": True, "error": None, "log": [], "values": {}}
        source_state.put_login_info(key, body.values)
        # login() 里是同步网络请求 + JS 求值，放线程池避免阻塞事件循环
        result = await asyncio.to_thread(source_login.run_login, src)
        return result

    class ActionBody(BaseModel):
        source_url: str
        key: str = Field(description="loginUi 行名或动作 JS/URL")
        long_click: bool = False

    @router.post("/login/action")
    async def login_action(
        body: ActionBody,
        current=Depends(require_perm("legado.login")),
        db: AsyncSession = Depends(get_db),
    ):
        src = await _load_source(db, body.source_url)
        return await asyncio.to_thread(
            source_login.run_action, src, body.key, body.long_click
        )

    class CookieBody(BaseModel):
        source_url: str
        cookie: str
        url: str = Field(default="", description="Cookie 归属地址，缺省用书源地址")

    @router.post("/login/cookie")
    async def login_cookie(
        body: CookieBody,
        current=Depends(require_perm("legado.login")),
        db: AsyncSession = Depends(get_db),
    ):
        src = await _load_source(db, body.source_url)
        target = body.url.strip() or source_login.source_key(src)
        if not body.cookie.strip():
            source_state.remove_cookie(target)
        else:
            source_state.replace_cookie(target, body.cookie)
        return {
            "ok": True,
            "cookie": source_state.get_cookie(target),
            "domain": source_state.subdomain(target),
        }

    class SourceBody(BaseModel):
        source_url: str

    @router.post("/login/header/remove")
    async def login_header_remove(
        body: SourceBody,
        current=Depends(require_perm("legado.login")),
        db: AsyncSession = Depends(get_db),
    ):
        src = await _load_source(db, body.source_url)
        source_state.remove_login_header(source_login.source_key(src))
        return {"ok": True}

    @router.post("/login/info/remove")
    async def login_info_remove(
        body: SourceBody,
        current=Depends(require_perm("legado.login")),
        db: AsyncSession = Depends(get_db),
    ):
        src = await _load_source(db, body.source_url)
        source_state.remove_login_info(source_login.source_key(src))
        return {"ok": True}

    return router
