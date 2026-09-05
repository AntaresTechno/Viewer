"""Regression tests for the 番茄(fq0826) book source defects.

Every case here fails (or errors) against the pre-fix code and passes after.
Network-dependent cases are skipped when the host cannot reach the endpoints.

Run:  backend\\.venv\\Scripts\\python.exe -m pytest backend/tests -q
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.legado_rule import js_bridge  # noqa: E402
from app.legado_rule.source_bridge import InfoMapBridge, bridges_for  # noqa: E402


def _engines() -> list[str]:
    """Engines actually installed — dukpy is usually absent."""
    import importlib.util

    found = []
    for mod, name in (("quickjs", "quickjs"), ("STPyV8", "stpyv8"),
                      ("dukpy", "dukpy")):
        try:
            if importlib.util.find_spec(mod) is not None:
                found.append(name)
        except (ImportError, ValueError):
            continue
    return found or ["quickjs"]


ENGINES = _engines()
all_engines = pytest.mark.parametrize("engine", ENGINES, ids=ENGINES)

@pytest.fixture(autouse=True)
def _restore_engine():
    """`_engine_name` is process-global; leaving it pinned to stpyv8 makes
    unrelated tests (and their thread pools) inherit it."""
    saved = js_bridge._engine_name
    yield
    js_bridge._engine_name = saved


RAW = json.loads((ROOT / "fq0826_e50d60ac.json").read_text(encoding="utf-8"))
SOURCE = RAW[0] if isinstance(RAW, list) else RAW

BASE_URL = SOURCE["bookSourceUrl"]


def _net_ok() -> bool:
    """Whether the 番茄 API host is reachable (explore cases need it)."""
    try:
        socket.create_connection(("api-sinfonlinec.fanqiesdk.com", 443),
                                 timeout=3).close()
    except OSError:
        return False
    return True


def _signer_ok() -> bool:
    """番茄不自己算签名，委托给远端 `sg.91loli.cc`（jsLib:1 的 sixgodHost）。

    这个共享签名服务是第三方单点：TCP 能连通不代表能出签（实测会 504
    网关超时）。不探测的话 explore 类用例会以「JS 执行出错」失败，看起来
    像代码回归，实际是外部服务不可用。
    """
    try:
        import httpx

        r = httpx.post(
            "https://sg.91loli.cc/api/sign",
            json={"user": "fq0826",
                  "auth": "cd0ca0a47a9b453b5b2c8229609eddf0bc1f4b02",
                  "url": "https://reading.snssdk.com/x", "params": {},
                  "device": {}, "body": None, "cookie": "", "header": None},
            timeout=20,
        )
        return r.status_code == 200
    except Exception:  # noqa: BLE001 - 探不通就跳过，不因此判定失败
        return False


NET_OK = _net_ok()
SIGNER_OK = _signer_ok()
requires_net = pytest.mark.skipif(
    not NET_OK, reason="番茄 API 不可达（离线环境跳过）"
)


def evaluator(engine: str = "quickjs", **extra):
    js_bridge._engine_name = engine
    bindings = {
        "__bridge__": js_bridge.JavaBridge(owner=None, base_url=BASE_URL),
        "cookie": {}, "cache": {},
        "source": SOURCE,
        "book": {"bookUrl": "https://reading.snssdk.com/x?book_id=1",
                 "name": "T", "group": 0},
        "result": None,
        "baseUrl": BASE_URL,
        "__ns__": bridges_for(SOURCE),
    }
    bindings.update(extra)
    return js_bridge.JsEvaluator(bindings)


def typeof(ev, expr: str) -> str:
    try:
        return str(ev.eval(f"typeof {expr};"))
    except Exception as exc:  # noqa: BLE001
        return "ERR:" + type(exc).__name__


# --------------------------------------------------------------- 1. jsLib
@all_engines
def test_jslib_symbols_are_functions(engine):
    """Rhino's `with(javaImport(...)){}` leaks top-level const/let to script
    scope; ES engines drop them, so jsLib helpers vanish."""
    ev = evaluator(engine)
    for sym in ("gzip", "ungz", "b64En", "b64De", "md5", "rStr", "Hex",
                "rHex"):
        assert typeof(ev, sym) == "function", f"{sym} is {typeof(ev, sym)}"


@all_engines
def test_jslib_top_level_functions_present(engine):
    ev = evaluator(engine)
    for sym in ("xGod", "Get", "device_register", "Map", "sleep"):
        assert typeof(ev, sym) == "function", f"{sym} is {typeof(ev, sym)}"


@all_engines
def test_jslib_this_destructuring_reaches_bridges(engine):
    """`const { java, source, cookie, cache } = this;` — Rhino binds top-level
    `this` to the global scope; ES leaves it undefined."""
    ev = evaluator(engine)
    assert ev.eval("Map('token：');") is not None


# ------------------------------------------------------------ 2. eval leak
@all_engines
def test_eval_loginurl_leaks_ck(engine):
    """`eval(String(source.loginUrl))` declares `let ck`; Rhino leaks it into
    the calling rule's scope, ES does not."""
    ev = evaluator(engine)
    assert typeof(ev, "ck") == "undefined"
    ev.eval("eval(String(source.loginUrl));")
    assert typeof(ev, "ck") == "string"


@all_engines
def test_eval_loginurl_defines_helpers(engine):
    ev = evaluator(engine)
    ev.eval("eval(String(source.loginUrl));")
    for sym, want in (("test", "function"), ("login", "function"),
                      ("look", "function"), ("original", "object"),
                      ("Page1", "number"), ("Tagnum", "string")):
        assert typeof(ev, sym) == want, f"{sym} is {typeof(ev, sym)}"


@all_engines
def test_nested_eval_inside_loginurl_still_works(engine):
    """loginUrl contains `eval('i=$$$.' + e)` — an ordinary dynamic statement
    that must NOT be rewritten into a leaking form."""
    ev = evaluator(engine)
    ev.eval("eval(String(source.loginUrl));")
    assert typeof(ev, "$$$") == "object"


@all_engines
def test_repeated_eval_of_same_loginurl_is_idempotent(engine):
    """Sources eval loginUrl in `init` and again in field rules."""
    ev = evaluator(engine)
    ev.eval("eval(String(source.loginUrl));")
    ev.eval("eval(String(source.loginUrl));")
    assert typeof(ev, "ck") == "string"


@all_engines
def test_rule_declaring_its_own_ck_does_not_break(engine):
    """番茄's intro rule evals loginUrl then declares `let ck` itself — the
    eval'd declaration must not collide with the outer lexical binding."""
    ev = evaluator(engine)
    out = ev.eval(
        "eval(String(source.loginUrl));\n"
        'let ck = "sessionid=" + Map(\'token：\');\n'
        "ck;"
    )
    assert "sessionid=" in str(out)


# -------------------------------------------------------- 3. login info map
@all_engines
def test_get_login_info_map_is_a_map(engine):
    ev = evaluator(engine)
    ev.eval("source.getLoginInfoMap();")
    assert typeof(ev, "source.getLoginInfoMap().get") == "function"
    assert ev.eval("typeof source.getLoginInfoMap().get('x');") in (
        "string", "object", "undefined")


@all_engines
def test_login_info_map_put_set(engine):
    ev = evaluator(engine)
    ev.eval("var infoMap = source.getLoginInfoMap();"
            "infoMap.set({'k': 'v'});")
    assert str(ev.eval("infoMap.get('k');")) == "v"


# -------------------------------------------------- 4. ajaxAll / connect
@all_engines
def test_connect_returns_strresponse(engine):
    ev = evaluator(engine)
    assert typeof(ev, "java.connect('http://127.0.0.1:1/x').body") == "function"
    assert typeof(ev,
                  "java.connect('http://127.0.0.1:1/x').raw().request().url"
                  ) == "function"


@all_engines
def test_connect_exposes_resolved_url(engine):
    ev = evaluator(engine)
    url = ev.eval("java.connect('http://127.0.0.1:1/x')"
                  ".raw().request().url();")
    assert str(url).startswith("http")


@all_engines
def test_ajax_all_returns_response_objects(engine):
    """legado's `StrResponse` exposes `body()`/`code()` as methods."""
    ev = evaluator(engine)
    for prop in ("body", "url", "code"):
        assert typeof(
            ev, f"java.ajaxAll(['http://127.0.0.1:1/x'])[0].{prop}"
        ) == "function"


@all_engines
def test_ajax_returns_empty_string_on_failure(engine):
    """legado returns null on failure; returning the error text would break
    `JSON.parse(res)` in every source."""
    ev = evaluator(engine)
    out = ev.eval("java.ajax('http://127.0.0.1:1/nope');")
    assert out == ""


@all_engines
def test_ajax_accepts_array_argument(engine):
    ev = evaluator(engine)
    assert ev.eval("java.ajax(['http://127.0.0.1:1/x']);") == ""


# ---------------------------------------------------------- 5. timeFormat
@all_engines
def test_timeformat_takes_milliseconds(engine):
    """legado: `timeFormat(time: Long)` — milliseconds first."""
    ev = evaluator(engine)
    out = ev.eval("java.timeFormat(1700000000000);")
    assert "2023" in str(out), f"got {out!r}"


@all_engines
def test_timeformat_utc_takes_milliseconds(engine):
    ev = evaluator(engine)
    out = ev.eval("java.timeFormatUTC(1700000000000, 'yyyy-MM-dd', 8);")
    assert out and "1970" not in str(out), f"got {out!r}"


@all_engines
def test_timeformat_accepts_seconds_too(engine):
    ev = evaluator(engine)
    out = ev.eval("java.timeFormat(1700000000);")
    assert "2023" in str(out), f"got {out!r}"


@all_engines
def test_timeformat_accepts_swapped_args(engine):
    """Sources written against other readers pass (format, time)."""
    ev = evaluator(engine)
    out = ev.eval("java.timeFormat('yyyy-MM-dd', 1700000000000);")
    assert "2023" in str(out), f"got {out!r}"


# ------------------------------------------------------------- 6. cache
@all_engines
def test_cache_put_with_ttl(engine):
    ev = evaluator(engine)
    ev.eval("cache.put('t_k', 'v', 60);")
    assert str(ev.eval("cache.get('t_k');")) == "v"


@all_engines
def test_cache_put_without_ttl(engine):
    ev = evaluator(engine)
    ev.eval("cache.put('t_k2', 'v2');")
    assert str(ev.eval("cache.get('t_k2');")) == "v2"


def test_cache_expired_entry_is_dropped():
    ev = evaluator()
    ev.eval("cache.put('t_exp', 'v', 1);")
    import time as _t
    _t.sleep(1.1)
    assert ev.eval("cache.get('t_exp');") in ("", None)


# -------------------------------------------------------------- 7. book
@all_engines
def test_book_put_get_custom_variable(engine):
    """legado: `putCustomVariable(value)` / `getCustomVariable()` — no key."""
    ev = evaluator(engine)
    ev.eval("book.putCustomVariable('v');")
    assert str(ev.eval("book.getCustomVariable();")) == "v"


@all_engines
def test_book_get_variable_defaults_to_empty_string(engine):
    ev = evaluator(engine)
    assert str(ev.eval("book.getVariable('nope');")) == ""


@all_engines
def test_book_properties_are_readable(engine):
    ev = evaluator(engine)
    assert "reading.snssdk.com" in str(ev.eval("book.bookUrl;"))


@all_engines
def test_book_write_propagates_to_python(engine):
    ev = evaluator(engine)
    holder = {"book": {"name": ""}}
    ev2 = evaluator(engine, book=holder["book"])
    ev2.eval("book.putName('from-js');")
    assert holder["book"]["name"] == "from-js"


# ------------------------------------------------------- 8. java.* stubs
@all_engines
def test_missing_java_apis_are_stubbed(engine):
    ev = evaluator(engine)
    for call in ("java.toast('x')", "java.longToast('x')",
                 "java.refreshExplore()", "java.getThemeMode()",
                 "java.removeCookie('a', 'b')", "java.getCookie('a', 'b')"):
        ev.eval(call + ";")  # must not raise AttributeError


@all_engines
def test_theme_config_returns_json_string(engine):
    ev = evaluator(engine)
    out = ev.eval("java.getThemeConfigMap();")
    assert isinstance(out, str) and out.startswith("{")


# ----------------------------------------------------------- 9. infoMap
def test_explore_infomap_bridge_surface():
    im = InfoMapBridge(SOURCE)
    im.set({"关键词：": "abc"})
    assert im.get("关键词：") == "abc"


# --------------------------------------------------- 10. explore kinds
# 这些用例要真跑番茄的 exploreUrl，而它把签名委托给远端 sg.91loli.cc。
# 签名服务挂了（实测 504）时整条链都会失败，那不是回归，所以一并要求
# 签名服务可用，否则跳过。
requires_signer = pytest.mark.skipif(
    not (NET_OK and SIGNER_OK),
    reason="番茄 API 或远端签名服务不可达（离线环境跳过）",
)


@requires_signer
def test_explore_kinds_returns_items():
    from app.legado_rule.web_book import explore_kinds

    kinds = explore_kinds(SOURCE)
    assert len(kinds) > 20, f"only {len(kinds)} kinds"


@requires_signer
def test_explore_has_second_level_js_kinds():
    from app.legado_rule.web_book import explore_kinds

    kinds = explore_kinds(SOURCE)
    js = [k for k in kinds if str(k.get("url") or "").startswith("@js:")]
    assert js, "no @js: second-level kinds"


@requires_signer
def test_explore_second_level_url_resolves():
    """The actual reported bug: clicking a second-level menu produced no URL
    because the `@js:` rule needs the source's jsLib (`xGod`) in scope."""
    from app.legado_rule.analyze_url import AnalyzeUrl
    from app.legado_rule.web_book import explore_kinds

    kinds = explore_kinds(SOURCE)
    js = [k for k in kinds if str(k.get("url") or "").startswith("@js:")]
    assert js
    a = AnalyzeUrl(js[0]["url"], page=1, base_url=BASE_URL, source=SOURCE)
    assert a.url.startswith(("http://", "https://")), a.url


@requires_signer
def test_explore_kinds_keep_style_metadata():
    from app.legado_rule.web_book import explore_kinds

    kinds = explore_kinds(SOURCE)
    assert any(isinstance(k.get("style"), dict) for k in kinds)


# --------------------------------------------- 12. engine robustness
@pytest.mark.skipif("stpyv8" not in ENGINES, reason="STPyV8 未安装")
def test_stpyv8_used_off_main_thread_falls_back():
    """STPyV8 embeds one V8 isolate; touching it from a worker thread after a
    main-thread context exists is an access violation that kills the process
    (cloudflare/stpyv8#100). Callers like content_purify / source_login run JS
    in a thread pool, so the evaluator must fall back there.
    """
    from concurrent.futures import ThreadPoolExecutor

    saved = js_bridge._engine_name
    js_bridge._engine_name = "stpyv8"
    try:
        assert js_bridge.JsEvaluator({"result": "x"}).engine == "stpyv8"

        def work(_i):
            ev = js_bridge.JsEvaluator({"result": "x"})
            return ev.engine, ev.eval("result.toUpperCase();")

        with ThreadPoolExecutor(3) as ex:
            got = list(ex.map(work, range(3)))
    finally:
        js_bridge._engine_name = saved
    assert [g[0] for g in got] != ["stpyv8"] * 3, "worker used stpyv8"
    assert all(g[1] == "X" for g in got)


def test_engines_agree_on_js_semantics():
    """The Rhino-emulation transforms must not be engine-specific."""
    if len(ENGINES) < 2:
        pytest.skip("只安装了一个引擎")
    outs = []
    for engine in ENGINES:
        ev = evaluator(engine)
        outs.append(str(ev.eval("typeof xGod;")))
    assert len(set(outs)) == 1, f"engines disagree: {dict(zip(ENGINES, outs))}"


# -------------------------------------------------- 11. ruleBookInfo.init
def test_rule_book_info_init_is_executed():
    """番茄 does short-link resolution and API selection in `init`; without it
    every field rule sees the untouched response."""
    from app.legado_rule import web_book as wb
    from app.legado_rule.analyze_rule import AnalyzeRule as AR

    src2 = dict(SOURCE)
    rbi = dict(SOURCE.get("ruleBookInfo") or {})
    rbi["init"] = "@js:var INIT_MARKER_ZZZ = 1; result"
    src2["ruleBookInfo"] = rbi

    hits: list[int] = []
    orig = wb.AnalyzeRule

    class SpyRule(AR):
        def get_element(self, rule_str=None):
            if rule_str and "INIT_MARKER_ZZZ" in str(rule_str):
                hits.append(1)
            return super().get_element(rule_str)

    wb.AnalyzeRule = SpyRule
    try:
        wb._apply_book_info_rules(
            src2, {"name": "x"}, "{}", "https://example.invalid/x")
    finally:
        wb.AnalyzeRule = orig
    assert hits, "init rule was never evaluated"


def test_rule_book_info_init_can_replace_content():
    """`init` may rewrite the response that the remaining field rules see."""
    from app.legado_rule import web_book as wb

    src2 = dict(SOURCE)
    rbi = dict(SOURCE.get("ruleBookInfo") or {})
    rbi["init"] = "@js:'{\"name\": \"REPLACED\"}'"
    rbi["name"] = "$.name"
    src2["ruleBookInfo"] = rbi
    out = wb._apply_book_info_rules(
        src2, {"name": ""}, "{}", "https://example.invalid/x")
    assert out.get("name") == "REPLACED"


def test_fq_guest_fallback_helpers():
    """访客降级辅助函数：域名判定 / book_id 抽取 / XHTML->正文纯函数可离线测试。

    这些辅助已随来源去耦迁入 source_degradation.fanqie（web_book 不再持有）。
    """
    from app.legado_rule.source_degradation import fanqie as fq

    assert fq._fq_domain("https://reading.snssdk.com/reading/bookapi/x")
    assert fq._fq_domain("https://fanqienovel.com/book/1")
    assert not fq._fq_domain("https://www.example.com/a")

    assert fq._fq_book_id(
        "https://reading.snssdk.com?a=1&book_id=7276384138653862966&genre=4"
    ) == "7276384138653862966"
    assert fq._fq_book_id("https://x.com/book/123") is None

    xhtml = ('<?xml version="1.0"?><!DOCTYPE html><html><head></head><body>'
             '<p>第一段文本</p><p>Second line.</p></body></html>')
    out = fq._fq_xhtml_to_paragraphs(xhtml)
    assert "第一段文本" in out and "Second line." in out
    assert out.index("第一段文本") < out.index("Second line.")
    assert "\u3000\u3000" in out  # 全角缩进与源站论文对齐

    # 有 <p> 时剔除 <em> 等内联标签并还原实体
    out2 = fq._fq_xhtml_to_paragraphs("<p>ab &amp; <em>cd</em> ef</p>")
    assert "ab & cd ef" in out2


def test_fq_replace_cover_origin():
    """"replaceCover 语义：缩略/带签名参数 的封面 -> 无签名原图，避免 `&` 截断 403/400。"""
    from app.legado_rule.source_degradation import fanqie as fq

    assert fq._fq_replace_cover("").__class__ is str
    assert fq._fq_replace_cover("") == ""
    # 常见的 protocol-relative 缩略图 + ~resize + ?签名
    src = ("//p6-novel-sign.byteimg.com/novel-pic/"
           "d1ffe7fa1ae9d423e23dbd21779b006e~tplv-resize:225:300.image"
           "?lk3s=x&x-expires=1&x-signature=YQ%3D%3D")
    out = fq._fq_replace_cover(src)
    assert out == ("https://p6-novel.byteimg.com/origin/"
                   "novel-pic/d1ffe7fa1ae9d423e23dbd21779b006e")
    # 已是 origin/https 直接透传
    direct = "https://p6-novel.byteimg.com/origin/novel-pic/abc"
    assert fq._fq_replace_cover(direct) == direct


def test_fq_unsigned_search_payload_mapping():
    """远端签名服务不可用时，免签接口仍能映射成统一搜索结果。"""
    from app.legado_rule.source_degradation import fanqie as fq

    payload = json.dumps({
        "code": 0,
        "data": {"ret_data": [{
            "book_id": "7658604195917859902",
            "title": "<em>庆余年</em>同人",
            "author": "测试作者",
            "category": "男频衍生",
            "creation_status": "1",
            "thumb_url": "https://img.example/cover.jpg",
            "abstract": "简介",
        }]},
    }, ensure_ascii=False)
    out = fq._fq_search_books(payload, SOURCE)
    assert out is not None and len(out) == 1
    assert out[0]["name"] == "庆余年同人"
    assert out[0]["kind"] == "男频衍生, 连载"
    assert out[0]["author"] == "测试作者"
    assert out[0]["bookUrl"].endswith("book_id=7658604195917859902")
    assert out[0]["origin"] == SOURCE["bookSourceUrl"]


@pytest.mark.asyncio
async def test_fq_search_uses_unsigned_paginated_endpoint(monkeypatch):
    from app.legado_rule.source_degradation import fanqie as fq

    seen: list[str] = []

    async def fake_fetch(url: str):
        seen.append(url)
        return '{"code":0,"data":{"ret_data":[]}}'

    monkeypatch.setattr(fq, "_fq_guest_fetch", fake_fetch)
    assert await fq._fq_search(SOURCE, "s:庆余年", 3) == []
    assert len(seen) == 1
    assert "offset=20" in seen[0]
    assert "%E5%BA%86%E4%BD%99%E5%B9%B4" in seen[0]
    assert "sg.91loli.cc" not in seen[0]


@pytest.mark.asyncio
async def test_search_adapter_isolated_from_generic_engine(monkeypatch):
    """匹配适配器时不执行原签名规则；普通来源仍保持通用行为。"""
    from app.legado_rule import web_book as wb

    class FakeSearch:
        async def search(self, source, key, page):
            return [{"name": key, "page": page}]

    async def must_not_fetch(*args, **kwargs):
        raise AssertionError("generic signed search must not run")

    monkeypatch.setattr(wb, "searcher_for", lambda source: FakeSearch())
    monkeypatch.setattr(wb, "_fetch_book_list", must_not_fetch)
    assert await wb.search_book(SOURCE, "庆余年", 2) == [
        {"name": "庆余年", "page": 2}
    ]


def test_fq_search_adapter_can_be_disabled():
    from app.legado_rule.source_degradation import searcher_for

    source = dict(SOURCE)
    source["extra"] = {"adapters": {"search": False}}
    assert searcher_for(source) is None
