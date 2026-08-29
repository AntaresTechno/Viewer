"""JS-facing bridges for source login state (legado BaseSource surface).

legado 在 evalJS 时把 ``source`` 绑定为 BaseSource 对象本身（带
getLoginHeader/putLoginHeader/getVariable/put… 等可调方法），把 ``cookie``
绑定为 CookieStore、``cache`` 绑定为 CacheManager。viewer 的规则求值里
``source`` 此前是纯 JSON dict，登录 JS（login()/header 规则/loginCheckJs）
需要这些方法，因此按 BaseSource 的 JS 可见面移植成命名空间桥，经
``js_bridge.JsEvaluator`` 的 ``__ns__`` 机制挂载。
"""
from __future__ import annotations

import json
from typing import Any, Callable

from . import source_state
from .js_bridge import JavaBridge


def enabled_cookie_jar(source: dict | None) -> bool:
    """``enabledCookieJar``：legado JSON 侧缺省即 true（仅显式 false 关闭）。"""
    if not isinstance(source, dict):
        return False
    return source.get("enabledCookieJar") is not False


def source_key(source: dict | None) -> str:
    """书源主键（BaseSource.getKey() == bookSourceUrl）。"""
    if not isinstance(source, dict):
        return ""
    return str(source.get("bookSourceUrl") or "")


class SourceBridge:
    """绑定在 ``source`` 上的 BaseSource 方法子集。"""

    def __init__(self, source: dict | None):
        self._source = source or {}
        self._key = source_key(self._source)

    # ------------------------------------------------------------- identity
    def getKey(self) -> str:
        return self._key

    def getTag(self) -> str:
        return str(self._source.get("bookSourceName") or self._key)

    # ----------------------------------------------------------- login info
    def getLoginInfo(self) -> str | None:
        info = source_state.get_login_info(self._key)
        return None if info is None else json.dumps(info, ensure_ascii=False)

    def putLoginInfo(self, info: str) -> bool:
        try:
            obj = json.loads(str(info or ""))
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(obj, dict):
            return False
        source_state.put_login_info(self._key, obj)
        return True

    def removeLoginInfo(self) -> None:
        source_state.remove_login_info(self._key)

    # ---------------------------------------------------------- login header
    def getLoginHeader(self) -> str | None:
        return source_state.get_login_header(self._key)

    def getLoginHeaderMap(self) -> dict[str, str] | None:
        return source_state.get_login_header_map(self._key)

    def putLoginHeader(self, header: str) -> None:
        source_state.put_login_header(self._key, header)

    def removeLoginHeader(self) -> None:
        source_state.remove_login_header(self._key)

    # -------------------------------------------------------------- variable
    def getVariable(self) -> str:
        return source_state.get_source_variable(self._key)

    def putVariable(self, variable: str | None) -> None:
        source_state.put_source_variable(self._key, variable)

    def setVariable(self, variable: str | None) -> None:
        self.putVariable(variable)

    def getVariableComment(self) -> str:
        return str(self._source.get("variableComment") or "")

    # ------------------------------------------------------------------ data
    def put(self, key: str, value: str) -> str:
        return source_state.put_source_data(self._key, key, value)

    def get(self, key: str) -> str:
        return source_state.get_source_data(self._key, key)

    # ------------------------------------------------------------------- js
    def getLoginJs(self) -> str | None:
        from .source_login import get_login_js

        return get_login_js(self._source)

    def getJsLib(self) -> str | None:
        lib = self._source.get("jsLib")
        return str(lib) if lib else None


class CookieBridge:
    """绑定在 ``cookie`` 上的 CookieStore 方法子集。"""

    def setCookie(self, url: str, cookie: str | None) -> None:
        source_state.set_cookie(url, str(cookie or ""))

    def replaceCookie(self, url: str, cookie: str) -> None:
        source_state.replace_cookie(url, str(cookie or ""))

    def getCookie(self, url: str) -> str:
        return source_state.get_cookie(url)

    def removeCookie(self, url: str) -> None:
        source_state.remove_cookie(url)

    def getKey(self, url: str, key: str) -> str:
        return source_state.cookie_to_map(source_state.get_cookie(url)).get(
            str(key), ""
        )

    def cookieToMap(self, cookie: str) -> dict[str, str]:
        return source_state.cookie_to_map(cookie)

    def mapToCookie(self, cookie_map: dict[str, str] | None) -> str | None:
        out = source_state.map_to_cookie(cookie_map)
        return out or None


class CacheBridge:
    """绑定在 ``cache`` 上的 CacheManager 方法子集（全局 KV）。"""

    def put(self, key: str, value: str) -> str:
        return source_state.cache_put(key, value)

    def get(self, key: str) -> str:
        return source_state.cache_get(key)

    def delete(self, key: str) -> None:
        source_state.cache_delete(key)

    def putString(self, key: str, value: str) -> str:
        return self.put(key, value)

    def getString(self, key: str) -> str:
        return self.get(key)


def bridges_for(source: dict | None) -> dict[str, Any]:
    """legado evalJS 的公共命名空间桥集合（``__ns__`` 约定）。"""
    return {
        "source": SourceBridge(source),
        "cookie": CookieBridge(),
        "cache": CacheBridge(),
    }


class SourceLoginBridge(JavaBridge):
    """登录界面 JS 的 ``java`` 桥（SourceLoginJsExtensions 子集 + 日志捕获）。

    在通用 JavaBridge 之上追加：

    - ``upLoginData(data)``  更新并持久化登录表单数据（回调到宿主 UI）
    - ``reLoginView(deltaUp)`` 请求宿主按 loginUi 重建表单
    - ``log(...)``           捕获到内存列表，供登录接口回传
    """

    def __init__(
        self,
        source: dict | None = None,
        base_url: str = "",
        on_login_data: Callable[[dict | None], None] | None = None,
        on_rebuild: Callable[[bool], None] | None = None,
        log_sink: list[str] | None = None,
    ):
        super().__init__(owner=None, base_url=base_url)
        self._source = source if isinstance(source, dict) else {}
        self._on_login_data = on_login_data
        self._on_rebuild = on_rebuild
        self._log_sink = log_sink if log_sink is not None else []

    def log(self, *args) -> None:  # noqa: D102 - 覆盖基类 print 行为
        text = " ".join(str(a) for a in args)
        self._log_sink.append(text)

    def upLoginData(self, data: dict | None) -> None:
        if self._on_login_data is not None:
            self._on_login_data(data)

    def reLoginView(self, deltaUp: bool = False) -> None:  # noqa: N803
        if self._on_rebuild is not None:
            self._on_rebuild(bool(deltaUp))

    def refreshExplore(self) -> None:
        if self._on_rebuild is not None:
            self._on_rebuild(False)
