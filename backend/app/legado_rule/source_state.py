"""Per-source persistent state for the legado engine (login & cookies).

Mirrors the legado keys that live in CacheManager / CookieStore:

- ``userInfo_<sourceKey>``   登录表单数据（loginUi 填写结果，JSON dict）
- ``loginHeader_<sourceKey>`` 登录头（JSON map，访问时自动附加）
- ``sourceVariable_<sourceKey>`` source 变量（js: source.getVariable/putVariable）
- ``v_<sourceKey>_<k>``      source.put/get 数据
- ``<domain>_cookie``        按「二级域名」保存的站点 Cookie

legado 用 androidId 前 16 字节做 AES 加密保存登录信息；viewer 按移植规格
（docs/spec/source-flow.md §4）改为本地明文 JSON，接口形状保持一致。

存储介质是一个带锁的 JSON 文件（backend/data/source_state.json）：
登录状态是低频小数据，但需要在异步请求路径（net.fetch）与 JS 桥的
工作线程（java.ajax / login() 执行）两侧都能同步读写，直接复用
async SQLAlchemy 会在工作线程里反复造事件循环；文件 KV 是两条路径
的最小公共实现。
"""
from __future__ import annotations

import json
import threading
from urllib.parse import urlsplit

from ..core.config import DATA_DIR

_STATE_PATH = DATA_DIR / "source_state.json"
_LOCK = threading.RLock()

# eTLD+1 近似：这些后缀前需保留两段标签（co.uk / com.cn 一类）。
# legado 用 PublicSuffixDatabase；这里用常见列表近似即可，Cookie 归属
# 只影响「存哪个键」，不影响请求语义。
_SECOND_LEVEL_TLD = {
    "com", "net", "org", "gov", "edu", "co", "ac", "int",
}

_EMPTY: dict = {
    "login_info": {},
    "login_header": {},
    "source_variable": {},
    "source_data": {},
    "cookies": {},
    "cache": {},
}


# --------------------------------------------------------------------- io
def _load() -> dict:
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {k: dict(v) for k, v in _EMPTY.items()}
    except Exception:  # noqa: BLE001 - 损坏时重置，不让书源整体不可用
        return {k: dict(v) for k, v in _EMPTY.items()}
    if not isinstance(data, dict):
        return {k: dict(v) for k, v in _EMPTY.items()}
    out = {k: dict(v) for k, v in _EMPTY.items()}
    for k in out:
        v = data.get(k)
        if isinstance(v, dict):
            out[k] = v
    return out


def _save(data: dict) -> None:
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(_STATE_PATH)


def _mutate(fn) -> None:
    with _LOCK:
        data = _load()
        fn(data)
        _save(data)


# ---------------------------------------------------------------- cookies
def subdomain(url: str) -> str:
    """域名归一（NetworkUtils.getSubDomain 的近似实现）。

    http://1.2.3.4 -> 1.2.3.4；https://www.example.com -> example.com；
    无法解析时返回原串（与 legado 行为一致）。
    """
    if not url:
        return ""
    try:
        host = urlsplit(url if "//" in url else f"//{url}").hostname
    except ValueError:
        return url
    if not host:
        return url
    if host.replace(".", "").isdigit():  # IPv4
        return host
    if ":" in host:  # IPv6 字面量
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    tail = ".".join(labels[-2:])
    if labels[-2] in _SECOND_LEVEL_TLD and len(labels) >= 3:
        return ".".join(labels[-3:])
    return tail


def get_cookie(url: str) -> str:
    """读取 url 所属二级域名的 Cookie 串。"""
    domain = subdomain(url)
    if not domain:
        return ""
    with _LOCK:
        return str(_load()["cookies"].get(domain, ""))


def set_cookie(url: str, cookie: str) -> None:
    """整体覆盖该域名的 Cookie（CookieStore.setCookie 语义）。"""
    domain = subdomain(url)
    if not domain:
        return

    def _set(data: dict) -> None:
        if str(cookie or "").strip():
            data["cookies"][domain] = str(cookie)
        else:
            data["cookies"].pop(domain, None)

    _mutate(_set)


def replace_cookie(url: str, cookie: str) -> None:
    """合并写入：新旧 Cookie 逐项 merge，新值覆盖旧值。"""
    if not str(cookie or "").strip():
        return
    domain = subdomain(url)
    if not domain:
        return
    with _LOCK:
        data = _load()
        old = str(data["cookies"].get(domain, ""))
        merged = map_to_cookie({**cookie_to_map(old), **cookie_to_map(cookie)})
        data["cookies"][domain] = merged or ""
        _save(data)


def remove_cookie(url: str) -> None:
    domain = subdomain(url)
    if not domain:
        return

    def _rm(data: dict) -> None:
        data["cookies"].pop(domain, None)

    _mutate(_rm)


def cookie_to_map(cookie: str) -> dict[str, str]:
    """``a=1; b=2`` -> dict（CookieStore.cookieToMap）。"""
    out: dict[str, str] = {}
    for pair in str(cookie or "").split(";"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k and (v or v == ""):
            out[k] = v
    return out


def map_to_cookie(cookie_map: dict[str, str] | None) -> str:
    if not cookie_map:
        return ""
    return "; ".join(f"{k}={cookie_map[k]}" for k in cookie_map)


def save_response_cookies(url: str, set_cookie_headers: list[str]) -> None:
    """从 Set-Cookie 头数组提取 name=value 并合并进该域名的 Cookie。

    legado 区分「会话 Cookie（内存）/ 持久 Cookie（数据库）」；viewer 是
    常驻服务端，会话丢失比保留过期能力更伤登录态，因此全部持久保存。
    """
    got: dict[str, str] = {}
    for raw in set_cookie_headers or []:
        first = str(raw).split(";", 1)[0]
        if "=" not in first:
            continue
        k, v = first.split("=", 1)
        k = k.strip()
        if k:
            got[k] = v.strip()
    if got:
        replace_cookie(url, map_to_cookie(got))


# ------------------------------------------------- login info / header 等
def get_login_info(source_key: str) -> dict[str, str] | None:
    with _LOCK:
        info = _load()["login_info"].get(source_key)
    if isinstance(info, dict):
        return {str(k): str(v) for k, v in info.items()}
    return None


def put_login_info(source_key: str, info: dict[str, str]) -> None:
    def _put(data: dict) -> None:
        data["login_info"][source_key] = {
            str(k): str(v) for k, v in (info or {}).items()
        }

    _mutate(_put)


def remove_login_info(source_key: str) -> None:
    def _rm(data: dict) -> None:
        data["login_info"].pop(source_key, None)

    _mutate(_rm)


def get_login_header(source_key: str) -> str | None:
    """登录头原始 JSON 串（getLoginHeader）。"""
    with _LOCK:
        header = _load()["login_header"].get(source_key)
    return None if header is None else str(header)


def get_login_header_map(source_key: str) -> dict[str, str] | None:
    header = get_login_header(source_key)
    if not header:
        return None
    try:
        obj = json.loads(header)
    except Exception:  # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


def put_login_header(source_key: str, header: str) -> None:
    """保存登录头；内含 Cookie 时合并进 CookieStore（putLoginHeader 语义）。"""
    header = str(header or "")
    try:
        header_map = json.loads(header)
    except Exception:  # noqa: BLE001
        header_map = None
    if isinstance(header_map, dict):
        cookie = header_map.get("Cookie") or header_map.get("cookie")
        if isinstance(cookie, str) and cookie.strip():
            replace_cookie(source_key, cookie)

    def _put(data: dict) -> None:
        data["login_header"][source_key] = header

    _mutate(_put)


def remove_login_header(source_key: str) -> None:
    """清除登录头 + 该源域名 Cookie（removeLoginHeader 语义）。"""
    remove_cookie(source_key)

    def _rm(data: dict) -> None:
        data["login_header"].pop(source_key, None)

    _mutate(_rm)


def get_source_variable(source_key: str) -> str:
    with _LOCK:
        return str(_load()["source_variable"].get(source_key, "") or "")


def put_source_variable(source_key: str, variable: str | None) -> None:
    def _put(data: dict) -> None:
        if variable is None:
            data["source_variable"].pop(source_key, None)
        else:
            data["source_variable"][source_key] = str(variable)

    _mutate(_put)


def get_source_data(source_key: str, key: str) -> str:
    with _LOCK:
        bag = _load()["source_data"].get(source_key)
    if not isinstance(bag, dict):
        return ""
    return str(bag.get(str(key), "") or "")


def put_source_data(source_key: str, key: str, value: str) -> str:
    value = "" if value is None else str(value)

    def _put(data: dict) -> None:
        bag = data["source_data"].setdefault(source_key, {})
        if isinstance(bag, dict):
            bag[str(key)] = value

    _mutate(_put)
    return value


def cache_get(key: str) -> str:
    with _LOCK:
        return str(_load()["cache"].get(str(key), "") or "")


def cache_put(key: str, value: str) -> str:
    value = "" if value is None else str(value)

    def _put(data: dict) -> None:
        data["cache"][str(key)] = value

    _mutate(_put)
    return value


def cache_delete(key: str) -> None:
    def _rm(data: dict) -> None:
        data["cache"].pop(str(key), None)

    _mutate(_rm)
