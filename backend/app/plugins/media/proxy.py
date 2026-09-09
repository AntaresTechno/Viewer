"""Range-supporting, SSRF-guarded streaming proxy for media resources.

Proxies upstream video/audio/images for entries already authorized by a ticket.
- Only http/https; rejects file:, data:, ftp:, etc.
- Blocks loopback, link-local, cloud-metadata and non-public private addresses.
- Forwards a safe allow-list of upstream headers; never hop-by-hop/sensitive ones.
- It is a responsible memory-steam proxy for the plugin by hooking an
  ``httpx`` ASGI stream into a ``StreamingResponse`` (no full-buffer vids).
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import httpx

log = logging.getLogger("viewer.media.proxy")

_ALLOWED_SCHEMES = ("http", "https")

# 逐跳或用不到的上游请求头：不允许源配置覆盖
_BLOCKED_HEADERS = {
    "host", "connection", "content-length", "content-type", "accept-encoding",
    "transfer-encoding", "authorization", "cookie",
}

# 默认只透传这些上游响应头
_PASS_RESPONSE_HEADERS = {
    "content-type", "content-length", "content-range", "accept-ranges", "etag",
    "last-modified", "cache-control", "expires",
}


def _is_blocked_target(url: str) -> str | None:
    """Return reason string if the upstream URL is unsafe, else None (SSRF 防线)."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return "仅允许 http/https 资源地址"
    host = parsed.hostname
    if not host:
        return "缺少主机名"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # 域名：阻止环回/元数据专用名
        low = host.lower()
        if low in ("localhost", "127.0.0.1", "::1") or low.endswith(".local"):
            return "不允许访问本地/回环地址"
        try:
            ips = [r for r in _resolve(host)]
        except OSError:
            ips = []
        for addr in ips:
            reason = _check_ip(addr)
            if reason:
                return reason
        return None
    return _check_ip(ip)


def _resolve(host: str):
    return [item[4][0] for item in socket.getaddrinfo(host, None)]


def _check_ip(ip: str) -> str | None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_loopback:
        return "不允许环回地址"
    if addr.is_link_local:
        return "不允许链路本地地址"
    if addr.is_multicast:
        return "不允许组播地址"
    if addr.is_private and not _is_explicitly_allowed_private(addr):
        return "不允许访问私网地址"
    # 云元数据地址²⁵⁴ 防护
    if addr.version == 4 and str(addr).startswith(("169.254.169.254", "100.100.100.200")):
        return "不允许访问云元数据地址"
    if addr.is_reserved or addr.is_unspecified:
        return "不允许访问保留/未指定地址"
    return None


def _is_explicitly_allowed_private(addr: ipaddress._BaseAddress) -> bool:
    """默认关闭私网；显式管理员配置白名单时才放行（见 core/config）。"""
    allowed = getattr(_conf(), "media_allow_private", [])
    for cidr in allowed:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if addr in net:
            return True
    return False


def _conf():
    try:
        from ...core.config import settings

        return settings
    except Exception:  # noqa: BLE001
        return type("_S", (), {"media_allow_private": []})


def sanitize_headers(headers: dict | None) -> dict:
    out: dict = {}
    for k, v in (headers or {}).items():
        lk = k.lower()
        if lk in _BLOCKED_HEADERS:
            continue
        out[k] = v
    return out


def pick_response_headers(headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in _PASS_RESPONSE_HEADERS:
        value = headers.get(name)
        if value:
            out[name] = value
    return out


async def open_upstream(*, url: str, headers: dict | None,
                        range_header: str | None) -> tuple[httpx.Response, httpx.AsyncClient]:
    """Open a streaming upstream response (caller must ``aclose`` both).

    Returns ``(resp, client)``; wrap ``resp.aiter_raw()`` in a
    ``StreamingResponse`` and finally ``await resp.aclose(); await client.aclose()``.
    """
    hdrs = sanitize_headers(headers or {}) or {}
    has_ua = any(k.lower() == "user-agent" for k in hdrs)
    if not has_ua:
        hdrs.setdefault("User-Agent", _conf().default_user_agent)
    if range_header:
        hdrs["Range"] = range_header
    client = httpx.AsyncClient(follow_redirects=True, verify=False, timeout=30.0)
    try:
        req = client.build_request("GET", url, headers=hdrs)
        resp = await client.send(req, stream=True)
        if resp.status_code in (401, 403):
            log.debug("media proxy upstream 401/403 for %s", url)
        return resp, client
    except Exception:
        await client.aclose()
        raise