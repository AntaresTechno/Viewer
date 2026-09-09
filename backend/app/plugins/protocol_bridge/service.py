"""Stable registration and dispatch contract for custom protocol plugins."""
from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass
from typing import Callable
from urllib.parse import SplitResult, quote, urlsplit


MAX_PROTOCOL_URI = 8192
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]{0,31}$", re.I)


class ProtocolBridgeError(ValueError):
    """A custom URI is invalid, unsupported, or has no matching action."""


@dataclass(frozen=True)
class ProtocolAction:
    handler: str
    scheme: str
    action: str
    title: str
    description: str
    payload: dict[str, str]
    execute_path: str
    requires_confirmation: bool = True

    def to_dict(self, original_uri: str) -> dict:
        data = asdict(self)
        data.update({
            "executePath": data.pop("execute_path"),
            "requiresConfirmation": data.pop("requires_confirmation"),
            "bridgePath": "/protocol?uri=" + quote(original_uri, safe=""),
        })
        return data


Resolver = Callable[[SplitResult], ProtocolAction | None]


@dataclass(frozen=True)
class _Handler:
    owner: str
    resolver: Resolver


_handlers: dict[str, _Handler] = {}
_lock = threading.RLock()


def register_handler(owner: str, schemes: set[str] | tuple[str, ...],
                     resolver: Resolver) -> None:
    """Register or idempotently refresh one plugin's protocol schemes."""
    owner = str(owner).strip()
    if not owner or not callable(resolver):
        raise ProtocolBridgeError("协议处理器声明无效")
    normalized = {str(scheme).strip().lower() for scheme in schemes}
    if not normalized or any(not _SCHEME_RE.fullmatch(s) for s in normalized):
        raise ProtocolBridgeError("协议名无效")
    with _lock:
        collisions = [
            scheme for scheme in normalized
            if scheme in _handlers and _handlers[scheme].owner != owner
        ]
        if collisions:
            raise ProtocolBridgeError(
                "协议已被其他插件注册：" + "、".join(sorted(collisions))
            )
        for scheme in normalized:
            _handlers[scheme] = _Handler(owner=owner, resolver=resolver)


def handler_catalog() -> list[dict[str, str]]:
    with _lock:
        return [
            {"scheme": scheme, "handler": item.owner}
            for scheme, item in sorted(_handlers.items())
        ]


def resolve_uri(uri: str) -> ProtocolAction:
    text = str(uri or "").strip()
    if not text:
        raise ProtocolBridgeError("协议链接为空")
    if len(text) > MAX_PROTOCOL_URI:
        raise ProtocolBridgeError("协议链接过长")
    if any(ord(char) < 0x20 for char in text):
        raise ProtocolBridgeError("协议链接包含控制字符")
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise ProtocolBridgeError("协议链接格式错误") from exc
    scheme = parsed.scheme.lower()
    if not _SCHEME_RE.fullmatch(scheme):
        raise ProtocolBridgeError("协议名无效")
    with _lock:
        handler = _handlers.get(scheme)
    if handler is None:
        raise ProtocolBridgeError(f"没有插件处理 {scheme}:// 协议")
    action = handler.resolver(parsed)
    if action is None:
        raise ProtocolBridgeError("该协议路径或动作暂不支持")
    if action.handler != handler.owner or not action.execute_path.startswith("/api/"):
        raise ProtocolBridgeError("协议插件返回了无效动作")
    return action
