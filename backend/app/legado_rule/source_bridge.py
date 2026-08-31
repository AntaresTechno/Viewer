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

    def getLoginInfoMap(self) -> str:
        """``BaseSource.getLoginInfoMap()``：登录表单数据（java.util.Map）。

        legado 返回 MutableMap，书源用 ``.get(key)`` 取值；Python 桥只能跨
        JS 边界传标量，因此这里返回 JSON 字符串，由 ``legado_objects.js``
        包装成带 ``.get()`` 的对象。未保存过时按 loginUi 构造默认值
        （对齐 BaseSource.getLoginInfoMap 的兜底语义）。
        """
        info = source_state.get_login_info(self._key)
        if info is None:
            try:
                from .source_login import default_login_info

                info = default_login_info(self._source)
            except Exception:  # noqa: BLE001
                info = {}
        return json.dumps(info or {}, ensure_ascii=False)

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

    # ------------------------------------------------------------- host UI
    def refreshExplore(self) -> None:
        """``BaseSource.refreshExplore``：请求宿主重建发现页（服务端为 no-op）。"""
        print("[source-js] [refreshExplore]", self._key)


class BookBridge:
    """绑定在 ``book`` 上的 BaseBook / RuleDataInterface 方法子集。

    legado 把 ``book`` 绑成 ``Book`` 实体：``book.putCustomVariable()``、
    ``book.getVariable("custom")``、``book.intro = …``、``book.durChapterIndex
    = …`` 在书源规则里非常常见（番茄书源的目录规则就用 ``book.intro`` 回写
    书评、用 ``book.durChapterIndex`` 同步阅读进度）。此前 ``book`` 只是纯
    dict，属性写回会丢失、方法调用直接 TypeError。
    """

    def __init__(self, book: dict | None = None):
        # 刻意共享调用方的 dict（不 copy）：书源写 book.intro / book
        # .durChapterIndex 后，web_book 的调用方能直接看到更新。
        self._book = book if isinstance(book, dict) else {}
        self._custom: dict = {}
        try:
            raw = self._book.get("variableMap")
            if isinstance(raw, str) and raw.strip():
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    self._custom = parsed
            elif isinstance(raw, dict):
                self._custom = dict(raw)
        except Exception:  # noqa: BLE001
            self._custom = {}

    # ------------------------------------------------------------- variables
    def getVariable(self, key: str | None = None) -> str:
        """"getVariable()" 取整体 JSON（Book 的变量包），getVariable(k) 取单项。"""
        if key is None:
            return json.dumps(self._custom, ensure_ascii=False)
        return str(self._custom.get(str(key), ""))

    def putVariable(self, key: str, value: str | None = None) -> None:
        if value is None:  # putVariable(json) —— 整体覆盖
            self._set_all(key)
            return
        self._custom[str(key)] = "" if value is None else str(value)
        self._flush()

    def setVariable(self, key: str, value: str | None = None) -> None:
        self.putVariable(key, value)

    def putCustomVariable(self, value: str | None) -> None:
        self._custom["custom"] = "" if value is None else str(value)
        self._flush()

    def getCustomVariable(self) -> str:
        return str(self._custom.get("custom", ""))

    def _set_all(self, raw: Any) -> None:
        try:
            obj = raw if isinstance(raw, dict) else json.loads(str(raw or ""))
        except Exception:  # noqa: BLE001
            obj = None
        if isinstance(obj, dict):
            self._custom = {str(k): "" if v is None else str(v)
                            for k, v in obj.items()}
            self._flush()

    def _flush(self) -> None:
        self._book["variableMap"] = json.dumps(self._custom, ensure_ascii=False)

    # ------------------------------------------------------------ accessors
    def getBookUrl(self) -> str:
        return str(self._book.get("bookUrl") or "")

    def getName(self) -> str:
        return str(self._book.get("name") or "")

    def getAuthor(self) -> str:
        return str(self._book.get("author") or "")

    def getIntro(self) -> str:
        return str(self._book.get("intro") or "")

    def getTocUrl(self) -> str:
        return str(self._book.get("tocUrl") or "")

    def getOrigin(self) -> str:
        return str(self._book.get("origin") or "")

    def getGroup(self) -> int:
        try:
            return int(self._book.get("group") or 0)
        except (TypeError, ValueError):
            return 0

    def getDurChapterIndex(self) -> int:
        try:
            return int(self._book.get("durChapterIndex") or 0)
        except (TypeError, ValueError):
            return 0

    def putDurChapterIndex(self, index: Any) -> None:
        try:
            self._book["durChapterIndex"] = int(index)
        except (TypeError, ValueError):
            pass

    def putIntro(self, intro: Any) -> None:
        self._book["intro"] = "" if intro is None else str(intro)

    def putName(self, name: Any) -> None:
        self._book["name"] = "" if name is None else str(name)

    def putBookUrl(self, url: Any) -> None:
        self._book["bookUrl"] = "" if url is None else str(url)

    def putTocUrl(self, url: Any) -> None:
        self._book["tocUrl"] = "" if url is None else str(url)

    # JS 侧 `book.intro = x` / `book.durChapterIndex = i` 这类属性赋值最终
    # 落到 __setattr__；legado 的 Book 是 JavaBean，属性可写。
    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        self._book[name] = value

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        v = self._book.get(name)
        return "" if v is None else v

    def to_dict(self) -> dict:
        self._flush()
        return self._book


class InfoMapBridge:
    """绑定在 ``infoMap`` 上的 InfoMap 方法子集。

    legado: ``class InfoMap(val sourceUrl) : MutableMap<String, String>``，
    发现页 JS 用它读写分类/关键词等持久输入：
    ``infoMap['关键词：']``、``infoMap.get(k)``、``infoMap.set(map)``、
    ``infoMap.save()``（jsLib 的 ``saveKeys(infoMap)`` 正是后两者的组合）。
    此前 binding 里是纯 ``{}``，``saveKeys`` 一调用就 TypeError。
    """

    def __init__(self, source: dict | None = None):
        from . import source_state

        self._state = source_state
        self._key = source_key(source)
        self._info = f"infoMap_{self._key}"

    def _map(self) -> dict[str, str]:
        try:
            raw = self._state.cache_get(self._info)
        except Exception:  # noqa: BLE001
            raw = ""
        if not raw:
            return {}
        try:
            obj = json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
        return obj if isinstance(obj, dict) else {}

    def get(self, key: str) -> str:
        return str(self._map().get(str(key), ""))

    def put(self, key: str, value: str) -> str:
        data = self._map()
        data[str(key)] = "" if value is None else str(value)
        self._state.cache_put(self._info, json.dumps(data, ensure_ascii=False))
        return "" if value is None else str(value)

    def set(self, value: dict | None) -> None:
        """``InfoMap.set(map)``：整体替换。"""
        data = value if isinstance(value, dict) else {}
        clean = {str(k): "" if v is None else str(v) for k, v in data.items()}
        self._state.cache_put(self._info, json.dumps(clean, ensure_ascii=False))

    def save(self, time_: Any = 0, need: Any = True) -> None:
        """``InfoMap.save(time, need)``：写盘（这里每次 put/set 已落盘）。"""
        return None

    def remove(self, key: str) -> None:
        data = self._map()
        data.pop(str(key), None)
        self._state.cache_put(self._info, json.dumps(data, ensure_ascii=False))

    def containsKey(self, key: str) -> bool:  # noqa: N802
        return str(key) in self._map()

    def isEmpty(self) -> bool:  # noqa: N802
        return not self._map()

    def size(self) -> int:
        return len(self._map())

    def keySet(self) -> list[str]:  # noqa: N802
        return list(self._map().keys())

    def values(self) -> list[str]:
        return list(self._map().values())

    def toJSON(self) -> str:  # noqa: N802
        return json.dumps(self._map(), ensure_ascii=False)

    def __getitem__(self, key: str) -> str:
        return self.get(key)

    def __setitem__(self, key: str, value: str) -> None:
        self.put(key, value)


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
    """绑定在 ``cache`` 上的 CacheManager 方法子集（全局 KV）。

    legado 的 ``CacheManager.put(key, value, saveTime)`` 第三参是 TTL（秒），
    书源（番茄 ruleExplore）用三参形式存分页 session_id，缺了会直接
    ``TypeError``。TTL 与取值一起存，读取时判过期。
    """

    def put(self, key: str, value: str, save_time: Any = 0) -> str:
        try:
            ttl = int(float(save_time or 0))
        except (TypeError, ValueError):
            ttl = 0
        return source_state.cache_put(key, value, ttl)

    def get(self, key: str) -> str:
        return source_state.cache_get(key)

    def delete(self, key: str) -> None:
        source_state.cache_delete(key)

    def putString(self, key: str, value: str, save_time: Any = 0) -> str:
        return self.put(key, value, save_time)

    def getString(self, key: str) -> str:
        return self.get(key)


def bridges_for(source: dict | None, book: dict | None = None) -> dict[str, Any]:
    """legado evalJS 的公共命名空间桥集合（``__ns__`` 约定）。

    ``book`` 传入时一并挂上 BookBridge：legado 把 book 绑成 Book 实体，
    书源会调 ``book.putCustomVariable`` / 写 ``book.intro`` / 写
    ``book.durChapterIndex``，纯 dict 会让这些调用全部 TypeError。
    """
    bridges: dict[str, Any] = {
        "source": SourceBridge(source),
        "cookie": CookieBridge(),
        "cache": CacheBridge(),
    }
    if book is not None:
        bridges["book"] = BookBridge(book)
    return bridges


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
