"""Port of AnalyzeUrl.kt — URL template expansion and request options."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .exceptions import RuleError
from .js_bridge import eval_js
from .rule_analyzer import RuleAnalyzer

JS_PATTERN = re.compile(r"<js>([\w\W]*?)</js>|@js:([\w\W]*)", re.IGNORECASE)
PARAM_PATTERN = re.compile(r"\s*,\s*(?=\{)")
PAGE_PATTERN = re.compile(r"<(.*?)>")

SAFE_QUERY_CHARS = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~!$%&()*+,/:;=?@[\\]^`{|}"
)


@dataclass
class RequestSpec:
    """Everything needed to execute one HTTP request."""

    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    charset: str | None = None
    type: str | None = None
    retry: int = 0
    body_js: str | None = None
    web_view: bool = False
    web_js: str | None = None
    proxy: str | None = None
    # 登录/Cookie 支撑：发起方书源主键 + 是否启用 cookieJar
    # （legado JSON 语义：enabledCookieJar 缺省即 true）
    source_key: str = ""
    cookie_jar: bool = False


def _ns_bridges(source: dict | None) -> dict:
    """legado evalJS 公共命名空间桥（source/cookie/cache），惰性导入避免环。"""
    if not isinstance(source, dict) or not source:
        return {}
    from .source_bridge import bridges_for

    return bridges_for(source)


def _login_header_map(source: dict | None) -> dict[str, str] | None:
    if not isinstance(source, dict) or not source:
        return None
    from . import source_state

    return source_state.get_login_header_map(str(source.get("bookSourceUrl") or ""))


def get_absolute_url(base_url: str | None, relative: str) -> str:
    rel = (relative or "").strip()
    if not base_url:
        return rel.split(",")[0] if rel.startswith("http") else rel
    base = base_url.split(",")[0]
    if rel.startswith(("http://", "https://", "data:", "ws://", "wss://")):
        return rel
    if rel.lower().startswith("javascript"):
        return ""
    from urllib.parse import urljoin

    try:
        return urljoin(base, rel)
    except Exception:  # noqa: BLE001
        return rel


def get_base_url(url: str) -> str | None:
    lower = url.lower()
    if lower.startswith("http://") or lower.startswith("https://"):
        idx = url.find("/", 9)
        return url if idx == -1 else url[:idx]
    return None


def _encode_query(query: str) -> str:
    out: list[str] = []
    for ch in query:
        if ord(ch) < 128 and ch in SAFE_QUERY_CHARS:
            out.append(ch)
        else:
            for b in ch.encode("utf-8"):
                out.append(f"%{b:02X}")
    return "".join(out)


def _encode_form(params: str, charset: str | None) -> str:
    """Encode key=value pairs; keep parts that already look encoded."""
    from urllib.parse import quote_plus

    enc = (charset or "utf-8").lower()

    def enc_char(ch: str) -> str:
        o = ord(ch)
        if o < 128:
            return ch if ch in SAFE_QUERY_CHARS else f"%{o:02X}"
        try:
            return "".join(f"%{b:02X}" for b in ch.encode(charset or "utf-8"))
        except LookupError:
            return "".join(f"%{b:02X}" for b in ch.encode("utf-8"))

    def encode_component(s: str) -> str:
        # keep components that are clearly already percent-encoded
        if re.search(r"%[0-9A-Fa-f]{2}", s):
            return s
        if enc.startswith("gb"):
            return "".join(enc_char(c) for c in s)
        return quote_plus(s, safe="")

    pairs: list[str] = []
    for part in params.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            pairs.append(f"{encode_component(k)}={encode_component(v)}")
        else:
            pairs.append(encode_component(part))
    return "&".join(pairs)


class AnalyzeUrl:
    """Parses a legado URL rule into an executable request."""

    def __init__(
        self,
        m_url: str,
        *,
        key: str | None = None,
        page: int | None = None,
        speak_text: str | None = None,
        speak_speed: int | None = None,
        base_url: str = "",
        source: dict | None = None,
        rule_data: dict | None = None,
        header_map_f: dict[str, str] | None = None,
        info_map: dict[str, str] | None = None,
    ):
        self.m_url = m_url
        self.key = key
        self.page = page
        self.speak_text = speak_text
        self.speak_speed = speak_speed
        self.base_url = base_url or ""
        self.source = source or {}
        self.rule_data = rule_data
        self.info_map = info_map

        # strip option JSON from baseUrl
        bm = PARAM_PATTERN.search(self.base_url)
        if bm:
            self.base_url = self.base_url[: bm.start()]

        self.header_map: dict[str, str] = {}
        headers = header_map_f if header_map_f is not None else self._source_headers()
        if headers:
            self.header_map.update(headers)
            if "proxy" in self.header_map:
                self.proxy: str | None = self.header_map.pop("proxy")
            else:
                self.proxy = None
        else:
            self.proxy = None

        self.rule_url = m_url
        self.url = ""
        self.url_no_query = ""
        self.type: str | None = None
        self.body: str | None = None
        self.encoded_form: str | None = None
        self.encoded_query: str | None = None
        self.charset: str | None = None
        self.method = "GET"
        self.retry = 0
        self.use_web_view = False
        self.web_js: str | None = None
        self.body_js: str | None = None

        self._init_url()

    # ------------------------------------------------------------- pipeline
    def _init_url(self) -> None:
        self.rule_url = self.m_url
        self._analyze_js()
        self._replace_key_page_js()
        self._analyze_url()

    def _analyze_js(self) -> None:
        start = 0
        result = self.rule_url
        for m in JS_PATTERN.finditer(self.rule_url):
            if m.start() > start:
                before = self.rule_url[start:m.start()].strip()
                if before:
                    result = before.replace("@result", str(result))
            js_code = m.group(2) or m.group(1)
            ev = self._eval(js_code, result)
            result = "" if ev is None else str(ev)
            start = m.end()
        if len(self.rule_url) > start:
            tail = self.rule_url[start:].strip()
            if tail:
                result = tail.replace("@result", str(result))
        self.rule_url = result

    def _replace_key_page_js(self) -> None:
        if "{{" in self.rule_url and "}}" in self.rule_url:
            ra = RuleAnalyzer(self.rule_url)

            def _fmt(code: str) -> str:
                ev = self._eval(code)
                if ev is None:
                    return ""
                if isinstance(ev, float) and ev == int(ev) and abs(ev) < 1e15:
                    return str(int(ev))
                return str(ev)

            replaced = ra.inner_rule("{{", "}}", _fmt)
            if replaced:
                self.rule_url = replaced

        if self.page is not None:
            for m in list(PAGE_PATTERN.finditer(self.rule_url)):
                pages = [p.strip() for p in m.group(1).split(",")]
                page_idx = min(max(self.page - 1, 0), len(pages) - 1)
                self.rule_url = self.rule_url.replace(m.group(0), pages[page_idx])

    def _analyze_url(self) -> None:
        m = PARAM_PATTERN.search(self.rule_url)
        url_no_option = self.rule_url[: m.start()] if m else self.rule_url
        self.url = get_absolute_url(self.base_url, url_no_option.strip())
        bu = get_base_url(self.url)
        if bu:
            self.base_url = bu

        if m:
            option_str = self.rule_url[m.end():]
            option = self._parse_option(option_str)
            if option:
                method = (option.get("method") or "").strip().upper()
                if method in ("POST", "HEAD"):
                    self.method = method
                else:
                    self.method = "GET"

                def _flatten(value: Any) -> str:
                    if isinstance(value, dict):
                        return json.dumps(value, ensure_ascii=False)
                    return str(value)

                hdrs = option.get("headers")
                if isinstance(hdrs, str):
                    try:
                        hdrs = json.loads(hdrs)
                    except Exception:  # noqa: BLE001
                        hdrs = None
                if isinstance(hdrs, dict):
                    for hk, hv in hdrs.items():
                        self.header_map[str(hk)] = _flatten(hv)
                if option.get("body") is not None:
                    self.body = _flatten(option["body"])
                self.type = (str(option["type"]) if option.get("type") else None)
                self.charset = (str(option["charset"]) if option.get("charset") else None)
                try:
                    self.retry = int(option.get("retry") or 0)
                except (TypeError, ValueError):
                    self.retry = 0
                wv = option.get("webView")
                self.use_web_view = bool(wv) and str(wv).lower() != "false"
                if option.get("webJs"):
                    self.web_js = str(option["webJs"])
                if option.get("bodyJs"):
                    self.body_js = str(option["bodyJs"])
                js_opt = option.get("js")
                if js_opt:
                    ev = self._eval(str(js_opt), self.url)
                    if ev is not None and str(ev):
                        self.url = str(ev)

        self.url_no_query = self.url
        if self.method == "GET":
            pos = self.url.find("?")
            if pos != -1:
                self.encoded_query = _encode_query(self.url[pos + 1:])
                self.url_no_query = self.url[:pos]
        elif self.method == "POST":
            if self.body and not _looks_structured(self.body) and \
                    "Content-Type" not in {k.title(): k for k in self.header_map} and \
                    not any(k.lower() == "content-type" for k in self.header_map):
                self.encoded_form = _encode_form(self.body, self.charset)

    # -------------------------------------------------------------- helpers
    def _parse_option(self, text: str) -> dict | None:
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        # GSON-lenient fallback: quote unquoted keys/values crudely
        try:
            fixed = re.sub(r"(?<!\\)'", '"', text)
            obj = json.loads(fixed)
            return obj if isinstance(obj, dict) else None
        except Exception:
            raise RuleError(f"URL 参数解析失败: {text[:80]}…") from None

    def _source_headers(self) -> dict[str, str] | None:
        """解析书源请求头并合并登录头（BaseSource.getHeaderMap 语义）。

        顺序对齐 legado：header 规则（JSON 或 @js 动态）→ 缺省 UA →
        登录头覆盖合并。
        """
        raw = self.source.get("header") if isinstance(self.source, dict) else None
        out: dict[str, str] = {}
        if raw:
            rule_text = str(raw)
            # ligand 书源 header 常写成 @js: / <js> 动态生成（如按浏览器拼 UA）。
            # 旧实现只认纯 JSON，遇到 @js: 直接丢弃，导致请求用默认 UA 被站点拒/限。
            low = rule_text.strip().lower()
            if low.startswith("@js:") or low.startswith("<js>"):
                try:
                    m = JS_PATTERN.search(rule_text)
                    if not m:
                        return self._finish_headers(out)
                    code = m.group(2) or m.group(1)
                    val = eval_js(code, {
                        "source": self.source or None,
                        "baseUrl": self.base_url,
                        "result": None,
                        "page": self.page,
                        "key": self.key,
                        "__ns__": _ns_bridges(self.source),
                    })
                    if isinstance(val, str):
                        val = json.loads(val)
                    if isinstance(val, dict):
                        for k, v in val.items():
                            out[str(k)] = str(v)
                except Exception:  # noqa: BLE001 - 头失败不应让请求整体崩掉
                    return self._finish_headers(out)
            else:
                try:
                    obj = json.loads(rule_text) if isinstance(rule_text, str) else rule_text
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            out[str(k)] = str(v)
                except Exception:  # noqa: BLE001
                    return self._finish_headers(out)
        return self._finish_headers(out)

    def _finish_headers(self, out: dict[str, str]) -> dict[str, str]:
        """补默认 UA 并合并登录头（getHeaderMap 的 UA/登录头两步）。"""
        if not any(k.lower() == "user-agent" for k in out):
            from ..core.config import settings as _settings

            out.setdefault("User-Agent", _settings.default_user_agent)
        login_header = _login_header_map(self.source)
        if login_header:
            for k, v in login_header.items():
                out[str(k)] = str(v)
        return out or None

    def _eval(self, code: str, result: Any = None) -> Any:
        bindings: dict[str, Any] = {
            "baseUrl": self.base_url,
            "page": self.page,
            "key": self.key,
            "speakText": self.speak_text,
            "speakSpeed": self.speak_speed,
            "book": self.rule_data,
            "source": self.source or None,
            "result": result,
            "infoMap": self.info_map,
            "__bridge__": _UrlBridge(self),
            "__ns__": _ns_bridges(self.source),
        }
        return eval_js(code, bindings)

    # --------------------------------------------------------------- output
    def spec(self) -> RequestSpec:
        headers = dict(self.header_map)
        body: str | None = None
        if self.method == "POST":
            content_type = next(
                (v for k, v in self.header_map.items() if k.lower() == "content-type"),
                None,
            )
            if self.encoded_form is not None or not (self.body or "").strip():
                body = self.encoded_form or ""
                headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            elif content_type:
                body = self.body
            else:
                body = self.body
                headers.setdefault("Content-Type", "application/json; charset=UTF-8")
        src = self.source if isinstance(self.source, dict) else {}
        return RequestSpec(
            url=self.url_no_query if self.method in ("POST", "HEAD") else self.url,
            method=self.method,
            headers=headers,
            body=body,
            charset=self.charset,
            type=self.type,
            retry=self.retry,
            body_js=self.body_js,
            web_view=self.use_web_view,
            web_js=self.web_js,
            proxy=self.proxy,
            source_key=str(src.get("bookSourceUrl") or ""),
            cookie_jar=src.get("enabledCookieJar") is not False if src else False,
        )


def _looks_structured(text: str) -> bool:
    t = text.strip()
    return (
        (t.startswith("{") and t.endswith("}"))
        or (t.startswith("[") and t.endswith("]"))
        or (t.startswith("<") and t.rstrip().endswith(">"))
    )


class _UrlBridge:
    """Bridge exposing AnalyzeUrl-specific java.* methods."""

    def __init__(self, owner: AnalyzeUrl):
        from .js_bridge import JavaBridge

        self._base = JavaBridge(owner=None)
        self._owner = owner

    def __getattr__(self, name):  # delegate everything to base bridge
        return getattr(self._base, name)

    def put(self, key: str, value: str) -> str:
        return self._base.put(key, value)

    def get(self, key: str) -> str:
        return self._base.get(key)
