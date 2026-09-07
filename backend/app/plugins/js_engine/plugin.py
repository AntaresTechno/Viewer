"""QuickJS 状态核心模块。"""
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

PLUGIN = {
    "kind": "core",
    "name": "js_engine",
    "mount": "js",
    "title": "QuickJS",
    "version": "1.1.0",
    "description": "书源 @js/{{}} 与 jsLib 规则固定使用 QuickJS",
    "order": 99,
    "permissions": [
        ("js.read", "查看 QuickJS 状态"),
    ],
}


def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.deps import require_perm
    from ...legado_rule import js_bridge

    router = APIRouter(tags=["js"])

    @router.get("/engines")
    async def get_engines(current=Depends(require_perm("js.read"))):
        """返回 QuickJS 的安装与生效状态。"""
        return js_bridge.list_engines()

    return router
