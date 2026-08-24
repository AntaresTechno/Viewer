"""Viewer — FastAPI application factory with plugin architecture."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
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

    uvicorn.run(app, host="127.0.0.1", port=8000)
