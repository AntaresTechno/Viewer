"""Protocol bridge — resolves custom URIs into explicit, confirmable actions."""

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext


PLUGIN = {
    "kind": "plugin",
    "name": "protocol_bridge",
    "mount": "protocol-bridge",
    "title": "协议桥",
    "version": "1.1.0",
    "description": "把自定义 App 协议解析为跨平台 Web 确认动作",
    "ui": {
        "entry": "ui/index.html",
        "title": "协议桥设置",
    },
    "order": 3,
    "permissions": [
        ("protocol_bridge.resolve", "打开并解析协议链接"),
        ("protocol_bridge.native", "管理当前系统的协议注册"),
    ],
}


def create_router(ctx: "PluginContext") -> APIRouter:
    from fastapi import Depends, HTTPException
    from pydantic import BaseModel, Field

    from ...core.deps import require_perm, require_superuser
    from .native_registration import (
        NativeRegistrationError,
        install_native_bridge,
        native_status,
        uninstall_native_bridge,
    )
    from .service import ProtocolBridgeError, handler_catalog, resolve_uri

    router = APIRouter(tags=["protocol-bridge"])

    class ResolveBody(BaseModel):
        uri: str = Field(min_length=1, max_length=8192)

    class NativeInstallBody(BaseModel):
        baseUrl: str = Field(min_length=1, max_length=2048)

    @router.get("/handlers")
    async def handlers(
        current=Depends(require_perm("protocol_bridge.resolve")),
    ):
        return {"items": handler_catalog()}

    @router.post("/resolve")
    async def resolve(
        body: ResolveBody,
        current=Depends(require_perm("protocol_bridge.resolve")),
    ):
        try:
            return resolve_uri(body.uri).to_dict(body.uri.strip())
        except ProtocolBridgeError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/native/status")
    async def get_native_status(current=Depends(require_superuser)):
        return native_status()

    @router.post("/native/install")
    async def install_native(
        body: NativeInstallBody,
        current=Depends(require_superuser),
    ):
        schemes = [item["scheme"] for item in handler_catalog()]
        try:
            return install_native_bridge(body.baseUrl, schemes)
        except (NativeRegistrationError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, "系统协议注册失败") from exc

    @router.delete("/native/install")
    async def uninstall_native(current=Depends(require_superuser)):
        try:
            return uninstall_native_bridge()
        except (NativeRegistrationError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, "移除系统协议注册失败") from exc

    return router
