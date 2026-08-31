"""JavaScript engine integration for legado rules.

Handles multiple JS backends: ``quickjs``, ``dukpy`` (QuickJS-backed fallback)
and ``stpyv8`` (STPyV8 / V8). The ``java`` object mirrors the commonly used
subset of legado's JsExtensions that is available to rule scripts, and a Rhino
compatibility prelude (``rhino_compat.js``) supplies ``JavaImporter`` /
``Packages`` / ``importClass`` / ``importPackage`` so Android-style book sources
(e.g. 番茄小说) no longer fail with ``ReferenceError: JavaImporter is not
defined`` on initialization.

Which engine is used is configurable: ``settings.js_engine`` (env
``VIEWER_JS_ENGINE``, default ``auto``) plus a runtime override written by the
JS-engine settings API (``backend/data/js_engine.json``). See
``list_engines`` / ``set_active_engine``.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import time
import urllib.parse
import zlib
from pathlib import Path
from typing import Any

from .exceptions import JsUnavailableError
from .rhino_dialect import (
    LEAK_HELPER_JS,
    normalize_eval_leak,
    normalize_js_lib,
)

# Rhino(Java) 兼容预置脚本（JavaImporter / Packages / okhttp3 / hutool / …）。
# 在创建每个 JS 上下文时、求值书源 jsLib 之前注入，引擎无关（duktape/QuickJS/V8
# 均为非严格模式），解决 `JavaImporter is not defined`。
_HERE = Path(__file__).resolve().parent
_RHINO_COMPAT = (_HERE / "rhino_compat.js").read_text(encoding="utf-8")

# legado 响应/MAP 对象外形（StrResponse / java.util.Map）。Python 桥只能跨边界
# 传标量，这里在 JS 侧把 JSON 串还原成带 `.body()` / `.raw().request().url()` /
# `.get(k)` 的对象，书源才不用改。须在 rhino_compat 之后、jsLib 之前注入。
_LEGADO_OBJECTS = (_HERE / "legado_objects.js").read_text(encoding="utf-8")

# java.base64DecodeToByteArray 的 JS 侧实现（覆盖 Python 桥）。
# 番茄书源的 device_register 用 `okhttpPost(url, java.base64DecodeToByteArray(b64), h)`
# 把 base64 的 gzip 字节还原成请求体再 POST 给签名回收站。Python 桥直接
# `return list(bytes)`——quickjs 桥**不能把 Python list 转成 JS 数组**
# （InternalError: Can not convert Python result to JS），于是这个吐数组的调用
# 一求值必炸，device_register 的 catch 再包一层就成了 "network error"。这里在
# JS 侧用纯 JS 解码 base64，返回 JS number 数组，对 quickjs / dukpy / stpyv8
# 一视同仁；httpRequest 桥拿到该数组后再还原成字节。
_B64_BYTES_JS = r"""
var __vB64Tab = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
var __vB64Map = {};
(function () { for (var i = 0; i < 64; i++) __vB64Map[__vB64Tab.charAt(i)] = i; })();
function __vB64ToBytes(b64) {
    var s = String(b64 == null ? "" : b64).replace(/=+$/, "");
    var out = [], buffer = 0, bits = 0, i, v;
    for (i = 0; i < s.length; i++) {
        v = __vB64Map[s.charAt(i)];
        if (v === undefined) continue;
        buffer = (buffer << 6) | v;
        bits += 6;
        if (bits >= 8) { bits -= 8; out.push((buffer >> bits) & 0xFF); }
    }
    return out;
}
if (typeof java !== "undefined" && java) java.base64DecodeToByteArray = __vB64ToBytes;
"""

# 可用 JS 引擎注册表：(key, 显示名, import 名)
_ENGINE_SPECS = [
    ("quickjs", "QuickJS", "quickjs"),
    ("stpyv8", "STPyV8 (V8)", "STPyV8"),  # 官方包 import 区分大小写（import STPyV8）
    ("dukpy", "dukpy (QuickJS 后备)", "dukpy"),
]
_ENGINE_KEYS = {k for k, _, _ in _ENGINE_SPECS}
_ENGINE_TITLES = {k: t for k, t, _ in _ENGINE_SPECS}

_engine_name: str | None = None


def _default_ajax_timeout() -> float:
    """JS 层 java.ajax/connect 的默认请求超时（秒），缺省 45s。

    legado 的 java.ajax 不带超时（阻塞直到响应），而 book 源预热/签名端点
    远慢于常规抓取；独立超时比通用 ``request_timeout`` 更宽，避免番茄等
    书源在发现页预热时误报 ``The read operation timed out``。
    """
    try:
        from ..core.config import settings

        val = getattr(settings, "js_ajax_timeout", 45.0)
        return float(val) if val else 45.0
    except Exception:  # noqa: BLE001
        return 45.0


def _available(key: str) -> bool:
    """Whether a JS engine module is importable."""
    pkg = dict((k, p) for k, _, p in _ENGINE_SPECS).get(key)
    if not pkg:
        return False
    try:
        importlib.import_module(pkg)  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _override_file() -> Path:
    from ..core.config import DATA_DIR

    return DATA_DIR / "js_engine.json"


def _read_override() -> str | None:
    try:
        data = json.loads(_override_file().read_text(encoding="utf-8"))
        val = data.get("engine")
        return val if isinstance(val, str) else None
    except Exception:  # noqa: BLE001
        return None


def set_active_engine(name: str) -> None:
    """Runtime 级切换 JS 引擎（写 backend/data/js_engine.json 并清缓存）。

    下次创建 JsEvaluator 即生效；持久化（含 ``auto``）。
    """
    global _engine_name
    name = (name or "auto").strip().lower()
    if name not in _ENGINE_KEYS and name != "auto":
        raise ValueError(
            f"未知 JS 引擎: {name}（可选: auto, {', '.join(sorted(_ENGINE_KEYS))}）"
        )
    _override_file().write_text(json.dumps({"engine": name}), encoding="utf-8")
    _engine_name = None


def _requested_engine() -> str:
    """运行时覆盖 > 配置项(VIEWER_JS_ENGINE) > auto。"""
    ov = _read_override()
    if ov in _ENGINE_KEYS or ov == "auto":
        return ov
    from ..core.config import settings

    req = getattr(settings, "js_engine", "auto")
    return req if (req in _ENGINE_KEYS or req == "auto") else "auto"


def _resolve_engine(requested: str) -> str | None:
    """把「请求/自动」映射到真正可用的引擎 key；装上驱动可安装引擎。"""
    if requested in _ENGINE_KEYS:
        return requested if _available(requested) else None
    # auto：按偏好顺序探测（quickjs > stpyv8 > dukpy）
    for key, _, _ in _ENGINE_SPECS:
        if _available(key):
            return key
    return None


def detect_engine() -> str | None:
    """Returns effective engine key: 'quickjs', 'stpyv8', 'dukpy', or None."""
    global _engine_name
    if _engine_name is not None:
        return _engine_name
    _engine_name = _resolve_engine(_requested_engine())
    return _engine_name


def list_engines() -> dict:
    """安装信息 + 当前生效/请求的引擎，供设置 API / 前端展示。"""
    try:
        current = detect_engine()
    except Exception:  # noqa: BLE001
        current = None
    return {
        "requested": _requested_engine(),
        "current": current,
        "items": [
            {
                "key": key,
                "title": title,
                "installed": _available(key),
                "current": current == key,
            }
            for key, title, _ in _ENGINE_SPECS
        ],
    }


def js_unwrap(val: Any) -> Any:
    """Normalize quickjs return values into plain Python data.

    quickjs 对 JS 对象/数组返回 ``_quickjs.Object``，其上 ``.json`` 是**方法**
    （``obj.json()`` → JSON 字符串）；对基本类型（int/str/bool/float）直接返回
    Python 基本类型，不会走到这里。
    """
    if val is None or isinstance(val, (str, int, float, bool, bytes)):
        return val
    js = getattr(val, "json", None)
    if callable(js):
        try:
            return json.loads(js())
        except Exception:  # noqa: BLE001
            return str(val)
    if isinstance(js, str):
        try:
            return json.loads(js)
        except Exception:  # noqa: BLE001
            pass
    return val


def _unwrap_arg(a: Any) -> Any:
    """把 JS 传给 Python 回调的单个实参归一化：Sc再转字符串/数字，对象/数组转 dict/list。"""
    if a is None or isinstance(a, (str, int, float, bool, bytes)):
        return a
    js = getattr(a, "json", None)
    if callable(js):
        try:
            return json.loads(js())
        except Exception:  # noqa: BLE001
            return a
    if isinstance(js, str):
        try:
            return json.loads(js)
        except Exception:  # noqa: BLE001
            pass
    return a


def _js_args_unwrapped(fn: Callable[..., Any]) -> Callable[..., Any]:
    """包装 Python 桥方法：把 quickjs 传入的 JS 对象/数组实参转成 dict/list。"""
    def _inner(*args: Any) -> Any:
        return fn(*[_unwrap_arg(a) for a in args])
    return _inner


def _stpyv8_unwrap(val: Any, stpyv8: Any) -> Any:
    """把 STPyV8 求值返回的 JSObject/JSArray 递归转成 dict/list。

    STPyV8：对象 → ``_STPyV8.JSObject``（有 ``keys()``），数组 → 可迭代的
    ``_STPyV8.JSArray``；基本类型直接是 Python 值。
    """
    array_t = getattr(stpyv8, "JSArray", ())
    obj_t = getattr(stpyv8, "JSObject", ())
    if array_t and isinstance(val, array_t):
        return [_stpyv8_unwrap(item, stpyv8) for item in val]
    if obj_t and isinstance(val, obj_t):
        return {
            k: _stpyv8_unwrap(val[k], stpyv8) for k in val.keys()
        }
    return val


def source_js_lib(source: Any) -> str:
    """The book-source ``jsLib`` (legado SharedJsScope) bound into rule JS.

    Legado evaluates the source's ``jsLib`` into a shared scope that acts as
    the prototype of every rule-JS scope, so functions defined there (cover(),
    words(), …) are callable from any @js/{{}} rule of that source.

    The text is passed through :func:`rhino_dialect.normalize_js_lib` first:
    Rhino gives ``const``/``let`` script scope, so names declared inside a
    ``with (...) { ... }`` block stay visible afterwards; ES engines give them
    block scope and the names disappear. See that module for details.
    """
    if isinstance(source, dict):
        lib = source.get("jsLib")
        if isinstance(lib, str) and lib.strip():
            try:
                return normalize_js_lib(lib)
            except Exception:  # noqa: BLE001 - 方言改写失败就用原文，不让书源整体失效
                return lib
    return ""


class JavaBridge:
    """Bound as ``java`` inside rule scripts."""

    def __init__(self, owner: Any = None, base_url: str = "",
                 source: dict | None = None):
        self._owner = owner          # AnalyzeRule / AnalyzeUrl for delegation
        self.base_url = base_url
        self._explicit_source = source if isinstance(source, dict) else None
        self._cache: dict[str, str] = {}

    # ---------------------------------------------------------- rule access
    def getString(self, rule: str) -> str:
        if self._owner is None or not hasattr(self._owner, "get_string"):
            raise RuleErrorNotAvailable("java.getString 需要 AnalyzeRule 上下文")
        return self._owner.get_string(rule)

    def getStringList(self, rule: str) -> list:
        if self._owner is None or not hasattr(self._owner, "get_string_list"):
            raise RuleErrorNotAvailable("java.getStringList 需要 AnalyzeRule 上下文")
        return self._owner.get_string_list(rule) or []

    def getElements(self, rule: str) -> list:
        if self._owner is None or not hasattr(self._owner, "get_elements"):
            raise RuleErrorNotAvailable("java.getElements 需要 AnalyzeRule 上下文")
        return [_to_jsonable(e) for e in self._owner.get_elements(rule)]

    def getElement(self, rule: str):
        if self._owner is None or not hasattr(self._owner, "get_element"):
            raise RuleErrorNotAvailable("java.getElement 需要 AnalyzeRule 上下文")
        el = self._owner.get_element(rule)
        return _to_jsonable(el)

    # -------------------------------------------------------------- network
    def _owner_source(self) -> dict | None:
        if isinstance(self._explicit_source, dict):
            return self._explicit_source
        src = getattr(self._owner, "source", None)
        return src if isinstance(src, dict) else None

    def _fetch_analyze_url(self, url_str: str, call_timeout=None) -> Any:
        """java.ajax/connect honor legado's `url,{option-json}` syntax by
        routing through AnalyzeUrl (charset / headers / method / body).

        AnalyzeUrl 对 ``@js:`` / ``{{}}`` URL 规则的求值走独立的 eval_js
        （一次性上下文），因此看不到书源 jsLib 里的函数。番茄书源把
        ``xGod()`` 定义在 jsLib 里、再让二级 URL 规则调用它，缺了 jsLib
        就会 ``ReferenceError: 'xGod' is not defined``。URL 以 ``@js:``
        开头时先把规则在当前上下文里展开成最终 URL。
        """
        from .analyze_url import AnalyzeUrl
        from .net import fetch_sync_ex

        aurl = AnalyzeUrl(
            self._expand_js_url(url_str),
            base_url=self.base_url or url_str.split(",")[0],
            source=self._owner_source(),
        )
        spec = aurl.spec()
        timeout = (float(call_timeout) if call_timeout
                   else _default_ajax_timeout())
        return fetch_sync_ex(
            spec.url,
            method=spec.method,
            headers=spec.headers,
            body=spec.body,
            charset=spec.charset,
            timeout=timeout,
            retries=spec.retry,
            cookie_jar=spec.cookie_jar,
        )

    def _expand_js_url(self, url_str: str) -> str:
        """把 ``@js:…`` URL 规则在**当前 JS 上下文**里求值成最终 URL。

        当前上下文带有书源 jsLib（xGod / device_register 等），而
        AnalyzeUrl 的一次性 eval_js 没有。仅对纯 ``@js:`` 规则生效，
        其余（含 ``{{}}``、``,{options}``）原样返回交给 AnalyzeUrl。
        """
        text = str(url_str or "")
        stripped = text.lstrip()
        if not stripped.startswith("@js:"):
            return text
        # 尾部可能有 ",{option-json}" —— 先剥下来，求值后再拼回
        from .analyze_url import PARAM_PATTERN

        m = PARAM_PATTERN.search(text)
        option = text[m.start():] if m else ""
        rule = (text[: m.start()] if m else text).strip()
        if not rule.startswith("@js:"):
            return text
        try:
            code = normalize_eval_leak(rule[4:])
            out = self._owner_eval(code)
        except Exception:  # noqa: BLE001
            return text
        if out is None:
            return text
        return f"{out}{option}"

    def _owner_eval(self, code: str) -> Any:
        """在宿主上下文里求值；宿主不存在时自建一个带 jsLib 的上下文。

        ``@js:`` URL 规则常调用 jsLib 里的函数（番茄书源的 ``xGod()``），
        一次性 eval_js 上下文没有 jsLib，会 ``ReferenceError``。这里退而
        求其次：，用当前书源再建一个 JsEvaluator（自带 jsLib）来求值，
        并在实例内缓存，避免同一条规则反复重建上下文。
        """
        ev = getattr(self._owner, "_js_evaluator", None)
        if ev is not None:
            try:
                return ev.eval(code)
            except Exception:  # noqa: BLE001
                return None
        cached = getattr(self, "_url_evaluator", None)
        if cached is None:
            try:
                from .source_bridge import bridges_for

                src = self._owner_source() or {}
                cached = JsEvaluator({
                    "__bridge__": JavaBridge(owner=None, base_url=self.base_url,
                                             source=src),
                    "source": src,
                    "cookie": {}, "cache": {},
                    "book": None, "result": None,
                    "baseUrl": self.base_url,
                    "__ns__": bridges_for(src),
                })
            except Exception:  # noqa: BLE001
                return None
            self._url_evaluator = cached          # type: ignore[attr-defined]
        try:
            return cached.eval(code)
        except Exception:  # noqa: BLE001
            return None

    def ajax(self, url, call_timeout=None) -> str:
        url_str = url[0] if isinstance(url, list) else str(url)
        try:
            resp = self._fetch_analyze_url(url_str, call_timeout)
        except Exception:  # noqa: BLE001
            return self._ajax_failed(url_str, None)
        # net 层失败时不抛异常，而是回一个带 error 的 StrResponse、body 里
        # 塞中文错误描述。legado 的 ajax 失败返回 null（JsExtensions 用
        # runCatching 吞异常），书源普遍以 ``!res`` / ``res == null`` 判断
        # 是否拿到数据；把错误文本当正文返回会让 ``JSON.parse(res)`` 抛
        # SyntaxError，掩盖真实原因。
        if getattr(resp, "error", None) or (
                getattr(resp, "status", 200) == 0):
            return self._ajax_failed(url_str, resp)
        return resp.body

    def _ajax_failed(self, url_str: str, resp: Any) -> str:
        """legado 语义：ajax 失败返回空串（JS 侧读作 falsy）。"""
        detail = getattr(resp, "error", None) or (
            resp.body if resp is not None else None)
        if detail:
            self.log(f"[ajax] {url_str[:120]} failed: {detail}")
        return ""

    def ajaxAll(self, urls, size_limit: int = 0) -> str:
        """legado returns ``Array<StrResponse>``; JS wraps them into objects.

        Python bridges cannot hand a ``list`` across to quickjs
        ("Can not convert Python result to JS"), so this returns a JSON string
        that ``legado_objects.js`` turns into objects exposing ``.body()``.

        URLs are normalized to strings: sources pass ``@js:`` rule strings
        (and sometimes arrays) here, and legado's ``ajaxAll`` takes
        ``Array<String>``.
        """
        out = []
        for u in urls if isinstance(urls, (list, tuple)) else [urls]:
            u_str = u[0] if isinstance(u, (list, tuple)) and u else str(u)
            try:
                resp = self._fetch_analyze_url(u_str)
                out.append({"url": resp.url, "body": resp.body,
                            "code": resp.status})
            except Exception:  # noqa: BLE001
                out.append({"url": u_str, "body": "", "code": -1})
        return json.dumps(out, ensure_ascii=False)

    def post(self, url: str, body: str, headers=None) -> str:
        from .net import post_sync

        src = self._owner_source()
        try:
            resp = post_sync(
                url, body=body,
                headers=headers if isinstance(headers, dict) else None,
                cookie_jar=src.get("enabledCookieJar") is not False
                if isinstance(src, dict) else False,
            )
            return resp.body
        except Exception as exc:  # noqa: BLE001
            return f"post({url}) error: {exc}"

    def connect(self, urlStr: str) -> str:
        """legado returns a ``StrResponse``; JSON string for the JS wrapper.

        Sources use ``java.connect(url).raw().request().url()`` to read the
        final URL after redirects, so the resolved ``url`` is included.
        """
        try:
            resp = self._fetch_analyze_url(urlStr)
            return json.dumps({"url": resp.url, "body": resp.body,
                               "code": resp.status}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"url": urlStr, "body": "", "code": -1,
                               "error": str(exc)}, ensure_ascii=False)

    # ---------------------------------------------------------------- cache
    def get(self, key) -> str:
        return self._cache.get(str(key), "")

    def put(self, key, value) -> str:
        self._cache[str(key)] = str(value)
        return str(value)

    def cacheGet(self, key) -> str:
        return self.get(key)

    def cachePut(self, key, value) -> str:
        return self.put(key, value)

    def delete(self, key) -> None:
        self._cache.pop(str(key), None)

    # --------------------------------------------------------------- crypto
    def md5Encode(self, s) -> str:
        return hashlib.md5(str(s).encode("utf-8")).hexdigest()

    def md5Encode16(self, s) -> str:
        return self.md5Encode(s)[8:-8]

    def base64Encode(self, s) -> str:
        return base64.b64encode(str(s).encode("utf-8")).decode()

    def base64Decode(self, s) -> str:
        raw = str(s).strip()
        pad = raw + "=" * (-len(raw) % 4)
        try:
            return base64.b64decode(pad).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            try:
                return base64.urlsafe_b64decode(pad + "==").decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return ""

    def base64DecodeToByteArray(self, s):
        raw = str(s).strip() + "=" * (-len(str(s).strip()) % 4)
        return list(base64.b64decode(raw))

    def hexDecodeToString(self, h) -> str:
        try:
            return bytes.fromhex(str(h)).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""

    def hexEncodeToString(self, s) -> str:
        return str(s).encode("utf-8").hex()

    # ------------------------------------------------------------- encoding
    def encodeURI(self, s) -> str:
        return urllib.parse.quote(str(s), safe="~@#$&=*+;/:,.?!'()")

    def decodeURI(self, s) -> str:
        return urllib.parse.unquote(str(s))

    def encodeURIComponent(self, s) -> str:
        return urllib.parse.quote(str(s), safe="~-_.!*'()")

    def decodeURIComponent(self, s) -> str:
        return urllib.parse.unquote(str(s))

    def urlEncode(self, s) -> str:
        return urllib.parse.quote_plus(str(s))

    # ------------------------------------------------------------ misc utils
    def log(self, *args) -> None:
        print("[source-js]", *args)

    def timeFormat(self, format_="yyyy-MM-dd HH:mm", tms=None) -> str:
        """兼容 legado 的两种重载。

        legado: ``timeFormat(time: Long)`` —— **毫秒**，固定 ``yyyy/MM/dd HH:mm``
        （AppConst.dateFormat）。本项目旧签名是 ``timeFormat(format, time)``
        且按秒解释。书源两种写法都在用，按首参类型嗅探，再把毫秒 → 秒
        （毫秒值直接喂 localtime 会抛 OSError: Invalid argument）。
        """
        if isinstance(format_, (int, float)) and not isinstance(format_, bool):
            format_, tms = "yyyy/MM/dd HH:mm", format_
        t = time.time() if tms in (None, "") else _as_seconds(tms)
        return self._strftime(t, format_, utc=False)

    def timeFormatNS(self, tns=None, format_="yyyy-MM-dd HH:mm") -> str:
        t = time.time() if tns in (None, "") else _as_seconds(tns)
        return self._strftime(t, format_, utc=False)

    def timeFormatUTC(self, tms: Any = None, format_: str = "yyyy-MM-dd HH:mm",
                      sh: Any = 0) -> str:
        """legado ``timeFormatUTC(time: Long, format: String, sh: Int)``。

        ``sh`` 是 SimpleTimeZone 的小时偏移（番茄书源传 8）。参数是「毫秒在前」。
        """
        if isinstance(format_, (int, float)) and not isinstance(format_, bool):
            # 兼容 timeFormatUTC(format, tms) 这类反序写法
            format_, tms = tms, format_
        t = time.time() if tms in (None, "") else _as_seconds(tms)
        try:
            t += float(sh or 0) * 3600
        except (TypeError, ValueError):
            pass
        return self._strftime(t, format_, utc=True)

    @staticmethod
    def _strftime(t: float, format_: Any, utc: bool) -> str:
        fmt = (
            str(format_ or "yyyy-MM-dd HH:mm")
            .replace("yyyy", "%Y").replace("MM", "%m").replace("dd", "%d")
            .replace("HH", "%H").replace("mm", "%M").replace("ss", "%S")
        )
        tm = time.gmtime(t) if utc else time.localtime(t)
        return time.strftime(fmt, tm)

    def androidId(self) -> str:
        return "viewer-web-android-id"

    def getAndroidId(self) -> str:
        return self.androidId()

    def getPackageName(self) -> str:
        return "io.viewer.web"

    def getSource(self):
        src = getattr(self._owner, "source", None)
        return _to_jsonable(src)

    # --------------------------------------------------- host-UI / 能力桩
    # 以下方法在 legado 里是 Android 侧能力（Toast / 打开页面 / 刷新发现 /
    # 主题配置 / WebView）。服务端没有宿主 UI，按「不抛异常、尽量可观察」的
    # 原则桩掉：缺一个就足以让整条规则 TypeError 中断（番茄书源每条规则
    # 都调 java.toast / java.longToast）。
    def toast(self, *args) -> None:
        self.log("[toast]", *args)

    def longToast(self, *args) -> None:
        self.log("[longToast]", *args)

    def searchBook(self, key: Any = "", searchScope: Any = None) -> None:
        """legado: RssJsExtensions.searchBook —— 服务端无法直接跳搜索页。

        记录到日志即可；番茄书源在「点击书籍标签」时用它重新搜索。
        """
        self.log("[searchBook]", key, searchScope)

    def open(self, name: Any = "", url: Any = None, title: Any = None,
             origin: Any = None) -> None:
        """legado: RssJsExtensions.open —— 打开宿主页面（这里是登录页）。"""
        self.log("[open]", name, url, title, origin)

    def refreshExplore(self) -> None:
        """legado: BaseSource.refreshExplore / SourceLoginJsExtensions。"""
        self.log("[refreshExplore]")

    def reLoginView(self, deltaUp: bool = False) -> None:  # noqa: N803
        self.log("[reLoginView]", deltaUp)

    def upLoginData(self, data: Any = None) -> None:
        self.log("[upLoginData]", data)

    def upConfig(self, data: Any = None) -> None:
        self.log("[upConfig]", data)

    def getThemeMode(self) -> int:
        return 0

    def getThemeConfigMap(self) -> str:
        """亮/暗/墨水屏三套段评配色；无宿主配置时返回空对象（书源有默认值）。"""
        return "{}"

    def getReadBookConfigMap(self) -> str:
        return "{}"

    def removeCookie(self, url: str, key: str | None = None) -> None:
        """legado 把 removeCookie 放在 CookieStore；个别书源写在 java 上。"""
        try:
            from . import source_state

            source_state.remove_cookie(str(url))
        except Exception as exc:  # noqa: BLE001
            self.log("[removeCookie] failed:", exc)

    def getCookie(self, url: str, key: str | None = None) -> str:
        """``getCookie(tag)`` / ``getCookie(tag, key)``（legado 两个重载）。"""
        try:
            from . import source_state

            return source_state.get_cookie(str(url))
        except Exception:  # noqa: BLE001
            return ""

    # --------------------------------------------------- Rhino/okhttp 桥
    # rhino_compat.js 里的 okhttp3 / hutool 类会经这些成员回 Python 真正执行。
    # 用公开方法名（httpRequest/strBytes），避免 STPyV8 的 JSClass 不暴露
    # 下划线方法的问题；_http/_strBytes 留作别名。
    def httpRequest(self, method="GET", url="", headers=None, body=None) -> str:
        """okhttp 兼容：真实 HTTP 请求，返回 JSON 字符串 {"code","body","error"}。

        body 可为 str、bytes 或 JS 字节数组（base64DecodeToByteArray 的结果）。
        """
        import json as _json

        from .net import decode_body, get_sync_client

        try:
            content: bytes | None = None
            if body is not None:
                if isinstance(body, (list, tuple)):
                    content = bytes(int(x) & 0xFF for x in body)
                elif isinstance(body, str):
                    content = body.encode("utf-8")
                elif isinstance(body, bytes):
                    content = body
                else:
                    content = str(body).encode("utf-8")
            hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
            resp = get_sync_client().request(
                str(method or "GET").upper(),
                str(url or ""),
                headers=hdrs,
                content=content,
                timeout=30.0,
            )
            return _json.dumps(
                {"code": resp.status_code, "body": decode_body(resp.content)}
            )
        except Exception as exc:  # noqa: BLE001
            return _json.dumps({"code": 0, "error": str(exc), "body": ""})

    # 别名：个别书源/兼容层直接引用 java._http
    def _http(self, method="GET", url="", headers=None, body=None) -> str:
        return self.httpRequest(method, url, headers, body)

    def gzip(self, s) -> str:
        """Hutool ZipUtil.gzip 的 Python 实现（base64 传输 gzip 字节）。"""
        return base64.b64encode(zlib.compress(str(s).encode("utf-8"))).decode()

    def ungzip(self, s) -> str:
        try:
            return zlib.decompress(base64.b64decode(str(s).encode()))
        except Exception:  # noqa: BLE001
            return ""

    def sha1Encode(self, s) -> str:
        return hashlib.sha1(str(s).encode("utf-8")).hexdigest()

    def sha256Encode(self, s) -> str:
        return hashlib.sha256(str(s).encode("utf-8")).hexdigest()

    def strBytes(self, s) -> list:
        return list(str(s).encode("utf-8"))

    # 别名
    def _strBytes(self, s) -> list:
        return self.strBytes(s)


def _as_seconds(tms: Any) -> float:
    """把书源传来的时间戳归一化为秒。

    legado 的 ``Date(time)`` 收毫秒，本项目旧桥按秒解释，两边书源都有；
    毫秒值（>= 1e11，约 1973 年后）直接喂 localtime 会抛 OSError，
    所以统一按量级判断。
    """
    try:
        t = float(tms)
    except (TypeError, ValueError):
        return time.time()
    if abs(t) >= 1e11:  # 毫秒
        return t / 1000.0
    return t


def _to_jsonable(obj: Any) -> Any:
    """Convert arbitrary python objects into JSON-safe data for the JS layer."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    # lxml 元素 → 外层 HTML（与旧 bs4 引擎的 str/repr 行为保持一致，
    # 否则 JS 规则拿到的是 "<Element div at 0x...>" 对象描述）。
    from .analyzer_css import HTML_ELEMENT, _outer_html

    if isinstance(obj, HTML_ELEMENT):
        return _outer_html(obj)
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    return repr(obj)[:2000]


_BRIDGE_METHODS = [
    name for name in dir(JavaBridge)
    if not name.startswith("_") and callable(getattr(JavaBridge, name))
]
# 说明：JsEvaluator 现按传入桥实例的 dir() 动态收集方法（见 _ns_methods），
# 以支持 SourceLoginBridge 等 JavaBridge 子类追加的登录扩展。


class RuleJSError(Exception):
    pass


class RuleErrorNotAvailable(Exception):
    pass


def _safe_json(v: Any) -> Any:
    try:
        json.dumps(v, ensure_ascii=False)
        return v
    except Exception:  # noqa: BLE001
        return _to_jsonable(v)


class JsEvaluator:
    """Evaluates one JS snippet with legado-style bindings."""

    def __init__(self, bindings: dict[str, Any]):
        self.engine = self._engine_for_this_thread()
        self.bridge: JavaBridge = bindings.pop("__bridge__", None) or JavaBridge()
        self.js_lib = source_js_lib(bindings.get("source"))
        # __ns__: 命名空间桥 {name: object}。桥对象的公开方法挂到同名
        # JS 全局（source/cookie/cache），若 bindings 里另有同名 dict，
        # 其字段会合并进该对象（方法保留）—— 对齐 legado 把
        # BaseSource / CookieStore / CacheManager 实例绑进 Rhino 的语义。
        self.ns_bridges: dict[str, Any] = bindings.pop("__ns__", None) or {}
        # legado 把 book 绑成 Book 实体（可写属性 + putCustomVariable 等）。
        # 调用方若只把 book 当普通 dict 放进 bindings（历史写法），这里补上
        # BookBridge，避免书源的 book.xxx() 调用全部 TypeError。
        if "book" not in self.ns_bridges and isinstance(bindings.get("book"), dict):
            try:
                from .source_bridge import BookBridge

                self.ns_bridges["book"] = BookBridge(bindings["book"])
            except Exception:  # noqa: BLE001 - 建桥失败就退回原 dict
                pass
        if self.engine == "quickjs":
            self._ctx_quickjs(bindings)
        elif self.engine == "stpyv8":
            self._ctx_stpyv8(bindings)
        elif self.engine == "dukpy":
            self._ctx_dukpy(bindings)
        else:
            raise JsUnavailableError(
                "未找到可用的 JavaScript 引擎：请安装 quickjs、stpyv8 或 dukpy"
                "（pip install quickjs / pip install stpyv8 / pip install dukpy）"
                "以启用书源中的 @js/{{}} 规则"
            )

    # ------------------------------------------------- namespace bridges
    @staticmethod
    def _engine_for_this_thread() -> str | None:
        """Pick an engine that is safe on the *current* thread.

        STPyV8 embeds a single V8 isolate: once a context exists, touching
        STPyV8 from another thread is an access violation that kills the
        process outright (cloudflare/stpyv8#100) — no Python exception, no
        chance to clean up. Callers such as ``content_purify`` and
        ``source_login`` run JS inside a thread pool, so honouring an
        ``stpyv8`` selection there would take the whole server down.

        Off the main thread we therefore fall back to a thread-safe engine
        (quickjs, else dukpy) and keep working instead.
        """
        engine = detect_engine()
        if engine != "stpyv8":
            return engine
        try:
            import threading

            if threading.current_thread() is threading.main_thread():
                return engine
        except Exception:  # noqa: BLE001
            return engine
        for fallback in ("quickjs", "dukpy"):
            if _available(fallback):
                return fallback
        return engine

    @staticmethod
    def _ns_methods(obj: Any) -> list[str]:
        # 收集公开方法，并额外保留单下划线内部方法：rhino_compat.js 里的类
        # 需要 java._http / java._strBytes 这些内部桥来真正发 HTTP、转字节。
        # 仅收集可调用成员，_cache/_owner 等数据属性会被 callable 过滤掉。
        return [
            name for name in dir(obj)
            if not name.startswith("__")
            and callable(getattr(obj, name, None))
        ]

    # ------------------------------------------------------------- quickjs
    def _ctx_quickjs(self, bindings: dict[str, Any]) -> None:
        import quickjs

        ctx = quickjs.Context()
        lines: list[str] = ["var java = {};"]
        for name in self._ns_methods(self.bridge):
            fn = getattr(self.bridge, name, None)
            if fn is None or not callable(fn):
                continue
            try:
                ctx.add_callable(f"__py_{name}", _js_args_unwrapped(fn))
            except Exception:  # noqa: BLE001
                continue
            lines.append(
                f"java.{name} = function() {{ var r = __py_{name}.apply(null, "
                f"Array.prototype.slice.call(arguments)); return r === undefined ? null : r; }};"
            )
        self._ns_names: set[str] = {
            ns for ns in self.ns_bridges if ns.isidentifier()
        }
        for ns, obj in self.ns_bridges.items():
            if ns not in self._ns_names:
                continue
            lines.append(f"var {ns} = {{}};")
            for name in self._ns_methods(obj):
                fn = getattr(obj, name, None)
                if fn is None:
                    continue
                try:
                    ctx.add_callable(
                        f"__py_ns_{ns}_{name}", _js_args_unwrapped(fn)
                    )
                except Exception:  # noqa: BLE001
                    continue
                lines.append(
                    f"{ns}.{name} = function() {{ var r = __py_ns_{ns}_{name}"
                    f".apply(null, Array.prototype.slice.call(arguments)); "
                    f"return r === undefined ? null : r; }};"
                )
        if "cookie" not in self.ns_bridges:
            lines.append("var cookie = {}; cookie.getKey = function(){ return ''; };")
        if "cache" not in self.ns_bridges:
            lines.append("var cache = java;")
        # Rhino 兼容层（JavaImporter/Packages/okhttp3/hutool）：必须先于书源
        # jsLib 注入，否则番茄这类书源会报 `JavaImporter is not defined`。
        # legado_objects 紧随其后，把桥的 JSON 串还原成 StrResponse/Map 对象；
        # LEAK_HELPER_JS 供 normalize_eval_leak 改写后的 eval 调用使用。
        lines.append(_RHINO_COMPAT)
        lines.append(_LEGADO_OBJECTS)
        lines.append(LEAK_HELPER_JS)
        lines.append(_B64_BYTES_JS)
        if self.js_lib:
            # source jsLib first so rules can call its functions; it may use java.*
            # （jsLib 用到的类名经 with(javaImport) 从 Packages 解析到兼容实现）
            lines.append(self.js_lib)
        for k, v in bindings.items():
            if not k.isidentifier() or k.startswith("__"):
                continue
            if k in self.ns_bridges:
                if isinstance(v, dict):
                    fields = json.dumps(_safe_json(v), ensure_ascii=False)
                    # 合并字段进 ns 对象，保留已挂载的方法
                    lines.append(
                        f"(function(){{var d={fields};"
                        f"for(var p in d){{{k}[p]=d[p];}}}})();"
                    )
                continue
            lines.append(
                # 注入为可配置的全局对象属性而非 `var`：书源脚本顶层 `let url/result`
                # 才能覆盖而不报 `redeclaration`（番茄 getByTabIndex 即 `let url`）。
                f"globalThis.{k} = {json.dumps(_safe_json(v), ensure_ascii=False)};"
            )
        try:
            ctx.eval("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            raise JsUnavailableError(f"JS 绑定初始化失败: {exc}") from exc
        self._quickjs_ctx = ctx

    # --------------------------------------------------------------- dukpy
    def _ctx_dukpy(self, bindings: dict[str, Any]) -> None:
        import dukpy

        interp = dukpy.JSInterpreter()
        names: list[str] = []
        for name in self._ns_methods(self.bridge):
            fn = getattr(self.bridge, name, None)
            if fn is None or not callable(fn):
                continue
            interp.export_function(f"java.{name}", fn)
            names.append(name)
        lines: list[str] = ["var java = {};"]
        for name in names:
            lines.append(
                f"java['{name}'] = function() {{ var args = ['java.{name}']"
                f".concat(Array.prototype.slice.call(arguments)); "
                f"return globalThis.call_python.apply(null, args); }};"
            )
        self._ns_names: set[str] = set()
        for ns, obj in self.ns_bridges.items():
            if not ns.isidentifier():
                continue
            self._ns_names.add(ns)
            ns_names: list[str] = []
            for name in self._ns_methods(obj):
                fn = getattr(obj, name, None)
                if fn is None:
                    continue
                interp.export_function(f"{ns}.{name}", fn)
                ns_names.append(name)
            lines.append(f"var {ns} = {{}};")
            for name in ns_names:
                lines.append(
                    f"{ns}['{name}'] = function() {{ var args = ['{ns}.{name}']"
                    f".concat(Array.prototype.slice.call(arguments)); "
                    f"return globalThis.call_python.apply(null, args); }};"
                )
        if "cookie" not in self.ns_bridges:
            lines.append("var cookie = {}; cookie.getKey = function(){ return ''; };")
        if "cache" not in self.ns_bridges:
            lines.append("var cache = java;")
        # 兼容层：JavaImporter/Packages/okhttp3/hutool（须于 jsLib 之前）
        lines.append(_RHINO_COMPAT)
        lines.append(_LEGADO_OBJECTS)
        lines.append(LEAK_HELPER_JS)
        lines.append(_B64_BYTES_JS)
        if self.js_lib:
            # dukpy keeps a persistent global scope per interpreter: evaluating
            # the jsLib during the validation run defines its functions for all
            # subsequent evaljs() calls on this interpreter.
            lines.append(self.js_lib)
        for k, v in bindings.items():
            if not k.isidentifier():
                continue
            if k in self._ns_names:
                # 字段在预检运行时合并进 ns 对象（dukpy 全局作用域持久）
                lines.append(
                    f"(function(){{var d=dukpy['{k}'];"
                    f"if(d){{for(var p in d){{{k}[p]=d[p];}}}}}})();"
                )
                continue
            lines.append(f"var {k} = dukpy['{k}'];")
        self._dukpy_interp = interp
        self._dukpy_prelude = "\n".join(lines)
        self._dukpy_vars = {k: _safe_json(v) for k, v in bindings.items()}
        # validate prelude compiles by running it once with vars present
        try:
            interp.evaljs(self._dukpy_prelude + "\n0;", **self._dukpy_vars)
        except Exception as exc:  # noqa: BLE001
            raise JsUnavailableError(f"JS 绑定初始化失败: {exc}") from exc

    # -------------------------------------------------------------- stpyv8
    # STPyV8（V8 内核）后端。dukpy 与它都以「持久全局作用域」工作，因此把
    # 绑定变量并入 ``_dukpy_vars``，每次 eval 前重绑。
    def _ctx_stpyv8(self, bindings: dict[str, Any]) -> None:
        import STPyV8 as stpyv8  # noqa: N813 — 官方包名区分大小写

        self._dukpy_vars = {k: _safe_json(v) for k, v in bindings.items()}
        self._ns_names: set[str] = {
            ns for ns in self.ns_bridges if ns.isidentifier()
        }
        # STPyV8 用 JSClass 作为全局对象（JSContext(global_object=…)），其方法成为
        # JS 顶层可调用全局；这里是「桥 -> JSClass 方法」的转发器，保留桥实例状态
        # （如 _owner），且用 staticmethod 避免 JSClass 把实例自身塞进参数。
        # 注意：STPyV8 的 JSClass 不暴露下划线方法，内部桥统一用公开名
        # httpRequest/strBytes（见 rhino_compat.js）；上下文不可跨线程共享
        # （cloudflare/stpyv8#100），JsEvaluator 按解析独立创建以满足此约束。
        attrs: dict[str, Any] = {}

        def _forward(fn: Callable[..., Any]) -> Callable[..., Any]:
            # JS 对象实参经 STPyV8 会以 JSObject/JSArray 传入（而非 dict/list）；
            # 转发前递归转成 Python 基本类型，确保桥方法拿到 dict。
            def inner(*a: Any, **k: Any) -> Any:
                return fn(*[_stpyv8_unwrap(x, stpyv8) for x in a], **k)
            return inner

        for name in self._ns_methods(self.bridge):
            fn = getattr(self.bridge, name, None)
            if fn is None or not callable(fn):
                continue
            attrs[name] = staticmethod(_forward(fn))
        for ns, obj in self.ns_bridges.items():
            if ns not in self._ns_names:
                continue
            for name in self._ns_methods(obj):
                fn = getattr(obj, name, None)
                if fn is None or not callable(fn):
                    continue
                attrs[f"{ns}__{name}"] = staticmethod(_forward(fn))
        bridge_cls = type("ViewerStpyv8Bridge", (stpyv8.JSClass,), attrs)
        ctxt = stpyv8.JSContext(obj=bridge_cls())
        ctxt.enter()

        lines: list[str] = [self._ns_bridge_js_stpyv8(), _RHINO_COMPAT,
                            _LEGADO_OBJECTS, LEAK_HELPER_JS, _B64_BYTES_JS]
        if self.js_lib:
            lines.append(self.js_lib)
        for k in list(self._dukpy_vars):
            if not k.isidentifier():
                continue
            if k in self._ns_names:
                # 命名空间对象（source/cache/cookie/…）是**桥对象**：上面已经
                # 挂好了 .get/.put/.getLoginInfoMap 等方法，这里不能再
                # `var source = {...}` 整体覆盖，否则方法全丢。改为把书源
                # 数据字段逐个并进去（字段与方法同名时以桥方法为准）。
                lines.append(
                    f"(function (d) {{ for (var key in d) {{"
                    f" if (!Object.prototype.hasOwnProperty.call({k}, key))"
                    f" {k}[key] = d[key]; }} }})"
                    f"({json.dumps(self._dukpy_vars[k], ensure_ascii=False)});"
                )
                continue
            lines.append(
                f"var {k} = {json.dumps(self._dukpy_vars[k], ensure_ascii=False)};"
            )
        try:
            ctxt.eval("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            raise JsUnavailableError(f"JS 绑定初始化失败: {exc}") from exc
        self._stpyv8_ctx = ctxt

    def _ns_bridge_js_stpyv8(self) -> str:
        """用 JS 包装 STPyV8 暴露的全局可调用对象成 java / ns 对象成员。

        顶层可调用对象命名：桥方法 name → 全局 name；ns 方法 → 全局
        ``{ns}__{name}``。这里把它们挂到 java.{name} 与 {ns}.{name}。
        """
        lines = ["var java = {};"]
        for name in self._ns_methods(self.bridge):
            # 经 globalThis['name'] 取全局（桥方法可能是 JS 保留字如 delete，
            # 不能写成 `java['x'] = x;` 这种裸标识符引用）
            lines.append(f"java['{name}'] = globalThis['{name}'];")
        for ns in self.ns_bridges:
            if ns not in self._ns_names:
                continue
            lines.append(f"var {ns} = {{}};")
            for name in self._ns_methods(self.ns_bridges[ns]):
                lines.append(f"{ns}['{name}'] = {ns}__{name};")
        if "cookie" not in self.ns_bridges:
            lines.append("var cookie = {}; cookie.getKey = function(){ return ''; };")
        if "cache" not in self.ns_bridges:
            lines.append("var cache = java;")
        return "\n".join(lines)

    def _eval_stpyv8(self, code: str) -> Any:
        import STPyV8

        ns_names = getattr(self, "_ns_names", set())
        pre = ""
        for k, v in self._dukpy_vars.items():
            if not k.isidentifier() or k in ns_names:
                continue
            pre += f"var {k} = {json.dumps(v, ensure_ascii=False)};"
        raw = self._stpyv8_ctx.eval(pre + "\n" + code + "\n;")
        return _stpyv8_unwrap(raw, STPyV8)

    # ----------------------------------------------------------------- eval
    def set_binding(self, key: str, value: Any) -> None:
        """原地更新一个绑定变量（如逐条规则变化中的 result）。

        quickjs：直接在既有上下文里重定义 var；dukpy：vars 每次求值时
        传入，更新字典即可。两者都无需重建运行时。
        """
        if not key.isidentifier():
            return
        value = _safe_json(value)
        if self.engine == "quickjs":
            try:
                self._quickjs_ctx.eval(
                    f"globalThis.{key} = {json.dumps(value, ensure_ascii=False)};"
                )
            except Exception as exc:  # noqa: BLE001
                raise JsUnavailableError(f"JS 绑定更新失败: {exc}") from exc
        else:
            # dukpy / stpyv8：vars 每次求值时传入，更新字典即可
            self._dukpy_vars[key] = value

    def eval(self, code: str) -> Any:
        # Rhino 的直接 eval 会把 `let`/`const` 泄漏到外层作用域，书源靠这个
        # 注入状态（番茄：loginUrl 里的 `let ck`，后续规则直接读 ck）。
        # ES6 不泄漏，这里在求值前把 eval(...) 改写成等价的 __rhinoEval(...)。
        code = normalize_eval_leak(code)
        try:
            if self.engine == "quickjs":
                result = js_unwrap(self._quickjs_ctx.eval(code))
            elif self.engine == "stpyv8":
                result = self._eval_stpyv8(code)
            else:
                # dukpy 的全局作用域跨 evaljs 持久，但预检只执行过一次
                # 「var x = dukpy['x']」绑定；set_binding 更新的是传入的
                # vars 字典。因此每次求值前先把当前 vars 重绑到全局，
                # 保证规则读到的是最新值。ns 桥（source 等）的字段已在
                # 预检时合并进带方法的对象，重绑会抹掉方法，必须跳过。
                ns_names = getattr(self, "_ns_names", set())
                binds = ";".join(
                    f"{k} = dukpy[{json.dumps(k)}]"
                    for k in self._dukpy_vars
                    if k.isidentifier() and k not in ns_names
                )
                result = self._dukpy_interp.evaljs(
                    binds + "\n" + code + "\n;", **self._dukpy_vars
                )
        except JsUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuleJSError(f"JS 执行出错: {exc}") from exc
        if isinstance(result, float) and result == int(result) \
                and abs(result) < 1e15:
            return str(int(result))
        return result


def eval_js(code: str, bindings: dict[str, Any]) -> Any:
    """One-shot evaluation (fresh context per call)."""
    evaluator = JsEvaluator(bindings)
    return evaluator.eval(code)
