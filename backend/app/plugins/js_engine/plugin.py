"""js_engine 插件 — 查看/切换书源 JS 引擎（QuickJS / STPyV8 / dukpy）。

书源规则里的 @js/{{}} / jsLib 由 JS 引擎执行；不同引擎对书源脚本的
方言/性能/可用性不同，这里提供运行时切换（持久化到 data/js_engine.json）。
"""
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

meta = {
    "name": "js_engine",
    "mount": "js",
    "title": "JS 引擎",
    "version": "1.0.0",
    "description": "书源 @js/{{}} 规则所用 JS 引擎（QuickJS / STPyV8 / dukpy）的查看与切换",
    "order": 99,
    "permissions": [
        ("js.read", "查看 JS 引擎"),
        ("js.manage", "切换书源 JS 引擎"),
    ],
}


def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.deps import require_perm
    from ...legado_rule import js_bridge

    router = APIRouter(tags=["js"])

    @router.get("/engines")
    async def get_engines(current=Depends(require_perm("js.read"))):
        """当前生效引擎 + 各引擎安装状态 + 请求值（前端设置控件驱动源）。"""
        return js_bridge.list_engines()

    class SetEngineBody(BaseModel):
        engine: str

    @router.put("/engine")
    async def set_engine(
        body: SetEngineBody,
        current=Depends(require_perm("js.manage")),
    ):
        """运行期切换 JS 引擎并持久化（engine: auto/quickjs/stpyv8/dukpy）。

        新值对之后创建的 JsEvaluator 生效；返回最新状态。
        """
        try:
            js_bridge.set_active_engine(body.engine)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return js_bridge.list_engines()

    return router