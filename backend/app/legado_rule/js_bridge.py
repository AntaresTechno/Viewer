"""JavaScript engine integration for legado rules.

Prefers quickjs (pip install quickjs); falls back to dukpy. The ``java``
object mirrors the commonly used subset of legado's JsExtensions that is
available to rule scripts.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.parse
from typing import Any

from .exceptions import JsUnavailableError

_engine_name: str | None = None


def detect_engine() -> str | None:
    """Returns 'quickjs', 'dukpy', or None."""
    global _engine_name
    if _engine_name is not None:
        return _engine_name
    try:
        import quickjs  # noqa: F401

        _engine_name = "quickjs"
        return _engine_name
    except ImportError:
        pass
    try:
        import dukpy  # noqa: F401

        _engine_name = "dukpy"
    except ImportError:
        _engine_name = ""
    return _engine_name or None


def js_unwrap(val: Any) -> Any:
    """Normalize quickjs return values into plain Python data."""
    if val is not None and hasattr(val, "json") and not isinstance(
        val, (str, int, float, bool, bytes)
    ):
        try:
            return json.loads(val.json)
        except Exception:  # noqa: BLE001
            return str(val)
    return val


def source_js_lib(source: Any) -> str:
    """The book-source ``jsLib`` (legado SharedJsScope) bound into rule JS.

    Legado evaluates the source's ``jsLib`` into a shared scope that acts as
    the prototype of every rule-JS scope, so functions defined there (cover(),
    words(), …) are callable from any @js/{{}} rule of that source.
    """
    if isinstance(source, dict):
        lib = source.get("jsLib")
        if isinstance(lib, str) and lib.strip():
            return lib
    return ""


class JavaBridge:
    """Bound as ``java`` inside rule scripts."""

    def __init__(self, owner: Any = None, base_url: str = ""):
        self._owner = owner          # AnalyzeRule / AnalyzeUrl for delegation
        self.base_url = base_url
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
        src = getattr(self._owner, "source", None)
        return src if isinstance(src, dict) else None

    def _fetch_analyze_url(self, url_str: str, call_timeout=None) -> Any:
        """java.ajax/connect honor legado's `url,{option-json}` syntax by
        routing through AnalyzeUrl (charset / headers / method / body)."""
        from .analyze_url import AnalyzeUrl
        from .net import fetch_sync_ex

        aurl = AnalyzeUrl(
            url_str,
            base_url=self.base_url or url_str.split(",")[0],
            source=self._owner_source(),
        )
        spec = aurl.spec()
        return fetch_sync_ex(
            spec.url,
            method=spec.method,
            headers=spec.headers,
            body=spec.body,
            charset=spec.charset,
            timeout=float(call_timeout) if call_timeout else None,
            retries=spec.retry,
        )

    def ajax(self, url, call_timeout=None) -> str:
        url_str = url[0] if isinstance(url, list) else str(url)
        try:
            return self._fetch_analyze_url(url_str, call_timeout).body
        except Exception as exc:  # noqa: BLE001
            return f"ajax({url_str}) error: {exc}"

    def ajaxAll(self, urls, size_limit: int = 0):
        out = []
        for u in urls:
            u_str = str(u)
            try:
                out.append(self._fetch_analyze_url(u_str).body)
            except Exception as exc:  # noqa: BLE001
                out.append(str(exc))
        return out

    def post(self, url: str, body: str, headers=None) -> str:
        from .net import post_sync

        try:
            resp = post_sync(url, body=body,
                             headers=headers if isinstance(headers, dict) else None)
            return resp.body
        except Exception as exc:  # noqa: BLE001
            return f"post({url}) error: {exc}"

    def connect(self, urlStr: str):
        """Minimal StrResponse-like dict for java.connect()."""
        try:
            resp = self._fetch_analyze_url(urlStr)
            return {"url": resp.url, "body": resp.body, "code": resp.status}
        except Exception as exc:  # noqa: BLE001
            return {"url": urlStr, "body": "", "code": -1, "error": str(exc)}

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
        t = time.time() if tms in (None, "") else float(tms)
        fmt = (
            format_.replace("yyyy", "%Y").replace("MM", "%m").replace("dd", "%d")
            .replace("HH", "%H").replace("mm", "%M").replace("ss", "%S")
        )
        return time.strftime(fmt, time.localtime(t))

    def timeFormatNS(self, tns=None, format_="yyyy-MM-dd HH:mm") -> str:
        t = time.time() if tns in (None, "") else float(tns) / 1000
        return self.timeFormat(format_, t)

    def androidId(self) -> str:
        return "viewer-web-android-id"

    def getAndroidId(self) -> str:
        return self.androidId()

    def getPackageName(self) -> str:
        return "io.viewer.web"

    def getSource(self):
        src = getattr(self._owner, "source", None)
        return _to_jsonable(src)


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
        self.engine = detect_engine()
        self.bridge: JavaBridge = bindings.pop("__bridge__", None) or JavaBridge()
        self.js_lib = source_js_lib(bindings.get("source"))
        if self.engine == "quickjs":
            self._ctx_quickjs(bindings)
        elif self.engine == "dukpy":
            self._ctx_dukpy(bindings)
        else:
            raise JsUnavailableError(
                "未找到可用的 JavaScript 引擎：请安装 quickjs 或 dukpy"
                "（pip install quickjs / pip install dukpy）以启用书源中的 @js/{{}} 规则"
            )

    # ------------------------------------------------------------- quickjs
    def _ctx_quickjs(self, bindings: dict[str, Any]) -> None:
        import quickjs

        ctx = quickjs.Context()
        lines: list[str] = ["var java = {};"]
        for name in _BRIDGE_METHODS:
            fn = getattr(self.bridge, name, None)
            if fn is None or not callable(fn):
                continue
            try:
                ctx.add_callable(f"__py_{name}", fn)
            except Exception:  # noqa: BLE001
                continue
            lines.append(
                f"java.{name} = function() {{ var r = __py_{name}.apply(null, "
                f"Array.prototype.slice.call(arguments)); return r === undefined ? null : r; }};"
            )
        lines.append("var cookie = {}; cookie.getKey = function(){ return ''; };")
        lines.append("var cache = java;")
        if self.js_lib:
            # source jsLib first so rules can call its functions; it may use java.*
            lines.append(self.js_lib)
        for k, v in bindings.items():
            if k.isidentifier():
                lines.append(
                    f"var {k} = {json.dumps(_safe_json(v), ensure_ascii=False)};"
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
        for name in _BRIDGE_METHODS:
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
        lines.append("var cookie = {}; cookie.getKey = function(){ return ''; };")
        lines.append("var cache = java;")
        if self.js_lib:
            # dukpy keeps a persistent global scope per interpreter: evaluating
            # the jsLib during the validation run defines its functions for all
            # subsequent evaljs() calls on this interpreter.
            lines.append(self.js_lib)
        for k, v in bindings.items():
            if k.isidentifier():
                lines.append(f"var {k} = dukpy['{k}'];")
        self._dukpy_interp = interp
        self._dukpy_prelude = "\n".join(lines)
        self._dukpy_vars = {k: _safe_json(v) for k, v in bindings.items()}
        # validate prelude compiles by running it once with vars present
        try:
            interp.evaljs(self._dukpy_prelude + "\n0;", **self._dukpy_vars)
        except Exception as exc:  # noqa: BLE001
            raise JsUnavailableError(f"JS 绑定初始化失败: {exc}") from exc

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
                    f"var {key} = {json.dumps(value, ensure_ascii=False)};"
                )
            except Exception as exc:  # noqa: BLE001
                raise JsUnavailableError(f"JS 绑定更新失败: {exc}") from exc
        else:
            self._dukpy_vars[key] = value

    def eval(self, code: str) -> Any:
        try:
            if self.engine == "quickjs":
                result = js_unwrap(self._quickjs_ctx.eval(code))
            else:
                # dukpy 的全局作用域跨 evaljs 持久，但预检只执行过一次
                # 「var x = dukpy['x']」绑定；set_binding 更新的是传入的
                # vars 字典。因此每次求值前先把当前 vars 重绑到全局，
                # 保证规则读到的是最新值。
                binds = ";".join(
                    f"{k} = dukpy[{json.dumps(k)}]"
                    for k in self._dukpy_vars if k.isidentifier()
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
