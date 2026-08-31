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
import time
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


# 番茄/头条的账号会话是「跨域单点登录」：同一个 sessionid 同时下发到
# fanqienovel.com 与 snssdk.com。书源分别从 fanqienovel.com 读 sessionid
# 拼 ck（搜索/详情走 URL），又要求 java.ajax 在 snssdk.com 挂上 sessionid
# Cookie（目录/正文走 Cookie）。若只存到其中一个 eTLD+1，登录态就「局部
# 生存」——发现页只靠设备签名能过，目录/正文/搜索却因另一域名读不到
# sessionid 而拿到空 body。故把这两个 SSO 域名的 sessionid 保持镜像，
# 一份登录态全局可用。
_FQ_SESSION_DOMAINS = ("snssdk.com", "fanqienovel.com")


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


def _session_targets(domain: str, cmap: dict) -> set[str]:
    """写入目标域名集合：本域名 + （含 sessionid 时）同域番茄/头条 SSO 镜像。

    只有写入的 cookie 里带 ``sessionid`` 且落在 SSO 域名上才会扩散到兄弟域
    （fanqienovel.com <-> snssdk.com）；其它情况只写本域名，行为不变。
    """
    targets: set[str] = {domain}
    if "sessionid" in cmap and domain in _FQ_SESSION_DOMAINS:
        targets.update(_FQ_SESSION_DOMAINS)
    return targets


def set_cookie(url: str, cookie: str) -> None:
    """整体覆盖该域名的 Cookie（CookieStore.setCookie 语义）。

    sessionid 会自动镜像到番茄/头条 SSO 兄弟域名，保证一份登录态全局可用。
    """
    domain = subdomain(url)
    if not domain:
        return
    cmap = cookie_to_map(cookie) if str(cookie or "").strip() else {}
    targets = _session_targets(domain, cmap)

    def _set(data: dict) -> None:
        if not cmap:
            data["cookies"].pop(domain, None)
            return
        for dom in targets:
            if dom == domain:
                # 本域名整体覆盖
                data["cookies"][dom] = map_to_cookie(cmap)
            else:
                # 兄弟域名仅同步 sessionid，保留其原有其它字段
                oldm = cookie_to_map(str(data["cookies"].get(dom, "")))
                oldm["sessionid"] = cmap["sessionid"]
                data["cookies"][dom] = map_to_cookie(oldm)

    _mutate(_set)


def replace_cookie(url: str, cookie: str) -> None:
    """合并写入：新旧 Cookie 逐项 merge，新值覆盖旧值。

    sessionid 会自动镜像到番茄/头条 SSO 兄弟域名，保证一份登录态全局可用。
    """
    if not str(cookie or "").strip():
        return
    domain = subdomain(url)
    if not domain:
        return
    cmap = cookie_to_map(cookie)
    targets = _session_targets(domain, cmap)
    with _LOCK:
        data = _load()
        for dom in targets:
            old = str(data["cookies"].get(dom, ""))
            # 本域名合入全部新字段；兄弟域名仅同步 sessionid
            to_write = {**cmap} if dom == domain else {"sessionid": cmap["sessionid"]}
            merged = map_to_cookie({**cookie_to_map(old), **to_write})
            data["cookies"][dom] = merged or ""
        _save(data)


def ensure_session_global(tok: str = "") -> None:
    """把番茄账号 session 镜像到全部 SSO 域名。

    主要给登录流用：书源 ``[账号登录]``（login_()）用 ``token：`` 表单值或
    已有 cookie 拼出 ``sessionid=…``。此后目录/正文（走 snssdk.com Cookie）
    与搜索/详情（走 fanqienovel.com 的 ck）都能拿到同一份登录态。
    """
    val = str(tok or "").strip()
    if not val:
        with _LOCK:
            data = _load()
            for dom in _FQ_SESSION_DOMAINS:
                m = cookie_to_map(str(data["cookies"].get(dom, "")))
                if m.get("sessionid"):
                    val = m["sessionid"]
                    break
    if not val:
        return
    replace_cookie("https://fanqienovel.com", f"sessionid={val}")


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


def _cache_unpack(entry: Any) -> tuple[str, float]:
    """读取 cache 条目：兼容新旧两种存储格式。

    新格式 ``{"v": <值>, "exp": <到期时间戳或 0>}``（带 TTL）；
    旧格式是裸字符串（永不过期）。
    """
    if isinstance(entry, dict):
        value = str(entry.get("v", "") or "")
        try:
            exp = float(entry.get("exp", 0) or 0)
        except (TypeError, ValueError):
            exp = 0.0
        return value, exp
    return ("" if entry is None else str(entry)), 0.0


def cache_get(key: str) -> str:
    with _LOCK:
        entry = _load()["cache"].get(str(key))
    if entry is None:
        return ""
    value, exp = _cache_unpack(entry)
    if exp and time.time() > exp:
        # 过期：惰性删除，避免每次读都要落盘
        cache_delete(key)
        return ""
    return value


def cache_put(key: str, value: str, save_time: int = 0) -> str:
    """写入 cache；``save_time`` > 0 表示 TTL 秒数（CacheManager.put 语义）。"""
    value = "" if value is None else str(value)
    try:
        ttl = int(save_time or 0)
    except (TypeError, ValueError):
        ttl = 0
    entry: Any = value
    if ttl > 0:
        entry = {"v": value, "exp": time.time() + ttl}

    def _put(data: dict) -> None:
        data["cache"][str(key)] = entry

    _mutate(_put)
    return value


def cache_delete(key: str) -> None:
    def _rm(data: dict) -> None:
        data["cache"].pop(str(key), None)

    _mutate(_rm)
