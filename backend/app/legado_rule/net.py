"""HTTP layer for the rule engine (httpx based).

Uses process-wide keep-alive connection pools: one ``httpx.AsyncClient`` per
running event loop and one thread-safe sync ``httpx.Client`` shared by worker
threads. Reusing connections skips the TCP+TLS handshake on every chapter
page / java.ajax call, which dominates latency against slow book sites.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

import httpx

from ..core.config import settings

_HTTP_LIMITS = httpx.Limits(
    max_connections=64,
    max_keepalive_connections=24,
    keepalive_expiry=120.0,
)

_async_clients: dict[int, tuple[asyncio.AbstractEventLoop, httpx.AsyncClient]] = {}
_sync_client: httpx.Client | None = None
_sync_lock = threading.Lock()


def _base_client_kwargs() -> dict:
    return {
        "follow_redirects": True,
        "verify": False,
        "limits": _HTTP_LIMITS,
        "headers": {"User-Agent": settings.default_user_agent},
    }


def get_async_client() -> httpx.AsyncClient:
    """Async client bound to the running loop (recreated if that loop died)."""
    loop = asyncio.get_running_loop()
    entry = _async_clients.get(id(loop))
    if entry is not None and not loop.is_closed():
        return entry[1]
    for key, (lp, client) in list(_async_clients.items()):
        try:
            if lp.is_closed():
                _async_clients.pop(key, None)
        except RuntimeError:
            _async_clients.pop(key, None)
    client = httpx.AsyncClient(**_base_client_kwargs())
    _async_clients[id(loop)] = (loop, client)
    return client


def get_sync_client() -> httpx.Client:
    """Shared sync client; httpx.Client.request is safe for concurrent threads."""
    global _sync_client
    if _sync_client is None:
        with _sync_lock:
            if _sync_client is None:
                _sync_client = httpx.Client(**_base_client_kwargs())
    return _sync_client


@dataclass
class StrResponse:
    url: str
    body: str
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 400


def decode_body(content: bytes, charset: str | None = None) -> str:
    """Decode response bytes: explicit charset > meta charset > utf-8 > gb18030."""
    if charset:
        try:
            return content.decode(charset, "replace")
        except LookupError:
            pass
    head = content[:2048].decode("ascii", "ignore").lower()
    for probe in ("charset=gb2312", "charset=gbk", "charset=gb18030", "charset=utf-8"):
        if probe in head:
            enc = probe.split("=")[1]
            try:
                return content.decode(enc, "replace")
            except LookupError:
                break
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return content.decode("gb18030")
        except UnicodeDecodeError:
            return content.decode("latin-1", "replace")


def _merge_headers(headers: dict[str, str] | None) -> dict[str, str]:
    base: dict[str, str] = {"User-Agent": settings.default_user_agent}
    if headers:
        for k, v in headers.items():
            base[k] = v
            if k.lower() == "user-agent":
                base[k] = v
    return base


async def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    charset: str | None = None,
    timeout: float | None = None,
    retries: int = 0,
) -> StrResponse:
    last_error: Exception | None = None
    attempts = max(1, retries + 1)
    client = get_async_client()
    for attempt in range(attempts):
        try:
            resp = await client.request(
                method.upper(),
                url,
                headers=_merge_headers(headers),
                content=body.encode("utf-8") if body and method.upper() == "POST" else None,
                timeout=timeout or settings.request_timeout,
            )
            text = decode_body(resp.content, charset)
            return StrResponse(str(resp.url), text, resp.status_code, dict(resp.headers))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    return StrResponse(url, f"请求失败: {last_error}", status=0, error=str(last_error))


def fetch_sync(url: str, *, timeout: float | None = None, retries: int = 0) -> StrResponse:
    """Synchronous GET used inside JS bridges."""
    return fetch_sync_ex(url, timeout=timeout, retries=retries)


def fetch_sync_ex(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    charset: str | None = None,
    timeout: float | None = None,
    retries: int = 0,
) -> StrResponse:
    """Synchronous request honoring an AnalyzeUrl RequestSpec (method/headers/
    body/charset), mirroring legado's AnalyzeUrl.getStrResponse for java.ajax."""
    last_error: Exception | None = None
    attempts = max(1, retries + 1)
    client = get_sync_client()
    merged = _merge_headers(headers)
    for _ in range(attempts):
        try:
            resp = client.request(
                method.upper(),
                url,
                headers=merged,
                content=body.encode("utf-8")
                if body and method.upper() == "POST" else None,
                timeout=timeout or settings.request_timeout,
            )
            text = decode_body(resp.content, charset)
            return StrResponse(str(resp.url), text, resp.status_code,
                               dict(resp.headers))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    return StrResponse(url, f"请求失败: {last_error}", status=0, error=str(last_error))


def post_sync(url: str, *, body: str = "", headers=None, timeout: float | None = None) -> StrResponse:
    merged = _merge_headers(headers if isinstance(headers, dict) else None)
    merged.setdefault("Content-Type", "application/x-www-form-urlencoded")
    try:
        resp = get_sync_client().post(
            url, headers=merged, content=body.encode("utf-8"),
            timeout=timeout or settings.request_timeout,
        )
        return StrResponse(str(resp.url), decode_body(resp.content), resp.status_code,
                           dict(resp.headers))
    except Exception as exc:  # noqa: BLE001
        return StrResponse(url, f"请求失败: {exc}", status=0, error=str(exc))


def run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("run_async called inside a running event loop")
