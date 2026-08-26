"""Viewer — FastAPI application factory with plugin architecture."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.db import init_db
from .plugins.registry import (
    PluginContext,
    all_plugins,
    discover_plugins,
    enabled_plugin_names,
    set_disabled_plugins,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 每日自动刷新（书架目录更新检查 + WebDAV 自动备份）
    from .services import daily_refresh

    daily_refresh.start()
    yield


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins + ["*"],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    ctx = PluginContext(settings=settings)
    known = set(discover_plugins().keys())
    enabled = enabled_plugin_names()
    set_disabled_plugins(known - enabled)

    # 缓存策略：HTML 页面（含 SPA 回退页）强制协商缓存，更新后浏览器立刻
    # 拿到新版本；接口与 DAV 响应不缓存；带 hash 的 /assets 资源保持默认。
    @app.middleware("http")
    async def _cache_headers(request: Request, call_next):
        response = await call_next(request)
        p = request.url.path
        if p.startswith("/api/") or p == "/api" or p.startswith("/dav/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif "text/html" in response.headers.get("content-type", ""):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    api_root = f"/api"
    mounted: list[str] = []

    for info in all_plugins():
        if info.name not in enabled:
            continue
        if info.create_router is None:
            continue  # engine-only plugin, nothing to mount
        router = info.create_router(ctx)
        app.include_router(router, prefix=f"{api_root}/{info.mount}")
        mounted.append(info.mount)
        # 可选的站点根路径路由（如 WebDAV 服务端 /dav）
        if info.create_root_router is not None and info.mount_root:
            app.include_router(
                info.create_root_router(ctx), prefix=f"/{info.mount_root}"
            )
            mounted.append(f"{info.mount_root} (root)")

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "app": settings.app_name,
            "plugins": {p.name: (p.name in enabled) for p in all_plugins()},
        }

    # Serve built frontend when present (production mode).
    if FRONTEND_DIST.exists():
        from starlette.exceptions import HTTPException as StarletteHTTPException

        class _SPA(StaticFiles):
            async def get_response(self, path: str, scope):  # type: ignore[override]
                # 未匹配的 /api 路径不回退到前端页面：避免旧进程/未启用插件时
                # 接口请求被静默吞成 HTML（GET 返回 index.html、POST 变 405）
                # 注意 Windows 上 path 分隔符可能是 "\"
                rel = path.replace("\\", "/").lstrip("/")
                if rel == "api" or rel.startswith("api/"):
                    from starlette.responses import JSONResponse

                    return JSONResponse({"detail": "Not Found"}, status_code=404)
                try:
                    response = await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code != 404:
                        raise
                    response = await super().get_response("index.html", scope)
                else:
                    if response.status_code == 404:
                        response = await super().get_response("index.html", scope)
                return response

        app.mount("/", _SPA(directory=str(FRONTEND_DIST), html=True), name="spa")

    print(f"[viewer] mounted plugins: {', '.join(mounted) or '(none)'}")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
