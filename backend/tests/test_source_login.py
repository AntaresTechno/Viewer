"""legado 规则插件登录功能测试（source_state / source_login / 请求管线 / API）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.legado_rule import net, source_state
from app.legado_rule.analyze_url import AnalyzeUrl
from app.legado_rule.js_bridge import detect_engine
from app.legado_rule import source_login

from app.plugins.registry import PluginContext

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_LOGIN_JS = """
function login() {
    var res = java.post('https://www.example.com/login',
        'u=' + result.username + '&p=' + result.password);
    source.putLoginHeader(JSON.stringify({'Cookie': 'token=abc; uid=7'}));
    source.putVariable('vip=1');
    java.log('login done');
}
"""

_SOURCE = {
    "bookSourceUrl": "https://www.example.com/",
    "bookSourceName": "示例源",
    "loginUrl": "@js:" + _LOGIN_JS,
    "loginUi": '[{"name":"username","type":"text","default":""},'
               '{"name":"password","type":"password"},'
               '{"name":"level","type":"select","chars":["A","B"],"default":"B"},'
               '{"name":"go","type":"button","action":"java.log(1)"}]',
    "enabledCookieJar": True,
}


@pytest.fixture()
def state_file(monkeypatch):
    """把状态文件指到 workspace 内的一次性文件，避免污染真实数据。"""
    path = _DATA_DIR / f"_test_state_{uuid.uuid4().hex}.json"
    monkeypatch.setattr(source_state, "_STATE_PATH", path)
    yield path
    path.unlink(missing_ok=True)


def _js_available() -> bool:
    return detect_engine() is not None


# ------------------------------------------------------------------ cookie
class TestCookieStore:
    def test_subdomain(self):
        f = source_state.subdomain
        assert f("http://1.2.3.4/a") == "1.2.3.4"
        assert f("https://www.example.com/x?y=1") == "example.com"
        assert f("http://www.content.example.com") == "example.com"
        assert f("http://www.biquge.com.cn") == "biquge.com.cn"
        assert f("") == ""

    def test_set_get_remove(self, state_file):
        source_state.set_cookie("https://www.example.com/a", "a=1; b=2")
        assert source_state.get_cookie("https://example.com/b") == "a=1; b=2"
        source_state.remove_cookie("https://www.example.com")
        assert source_state.get_cookie("https://www.example.com") == ""

    def test_replace_merges(self, state_file):
        source_state.set_cookie("https://www.example.com", "a=1; b=2")
        source_state.replace_cookie("https://www.example.com", "b=9; c=3")
        assert source_state.get_cookie("https://www.example.com") == \
            "a=1; b=9; c=3"

    def test_cookie_map_roundtrip(self):
        m = source_state.cookie_to_map("a=1; b=2 ; =bad")
        assert m == {"a": "1", "b": "2"}
        assert source_state.map_to_cookie(m) == "a=1; b=2"

    def test_save_response_cookies(self, state_file):
        source_state.save_response_cookies(
            "https://www.example.com/login",
            ["sid=xyz; Path=/; HttpOnly", "k=v; Expires=Wed, 21 Oct 2099 07:28:00 GMT"],
        )
        assert source_state.get_cookie("https://www.example.com/") == "sid=xyz; k=v"

    def test_put_login_header_merges_cookie(self, state_file):
        source_state.set_cookie("https://www.example.com", "old=1")
        source_state.put_login_header(
            "https://www.example.com/", '{"Cookie": "token=abc"}')
        assert source_state.get_login_header("https://www.example.com/") == \
            '{"Cookie": "token=abc"}'
        assert source_state.get_cookie("https://www.example.com/") == \
            "old=1; token=abc"
        source_state.remove_login_header("https://www.example.com/")
        assert source_state.get_login_header("https://www.example.com/") is None
        assert source_state.get_cookie("https://www.example.com/") == ""


# ---------------------------------------------------------------- login ui
@pytest.mark.skipif(not _js_available(), reason="需要 quickjs/dukpy")
class TestLoginUi:
    def test_rows_and_defaults(self, state_file):
        rows = source_login.login_rows(_SOURCE)
        assert [r["name"] for r in rows] == \
            ["username", "password", "level", "go"]
        info = source_login.default_login_info(_SOURCE, rows)
        assert info == {"username": "", "password": "", "level": "B"}

    def test_login_mode(self):
        assert source_login.login_mode(_SOURCE) == "form"
        assert source_login.login_mode({"bookSourceUrl": "x"}) == "none"
        web = {"bookSourceUrl": "x", "loginUrl": "https://a.com/login"}
        assert source_login.login_mode(web) == "web"
        assert source_login.web_login_url(web) == "https://a.com/login"
        js_only = {"bookSourceUrl": "x", "loginUrl": "@js:function login(){}"}
        assert source_login.login_mode(js_only) == "web"
        assert source_login.web_login_url(js_only) is None

    def test_get_login_info_defaults_not_persisted(self, state_file):
        info = source_login.get_login_info(_SOURCE)
        assert info["level"] == "B"
        assert source_state.get_login_info(
            "https://www.example.com/") is None


@pytest.mark.skipif(not _js_available(), reason="需要 quickjs/dukpy")
class TestRunLogin:
    def test_login_flow(self, state_file):
        source_state.put_login_info(_SOURCE["bookSourceUrl"],
                                    {"username": "u1", "password": "p1"})
        result = source_login.run_login(_SOURCE)
        assert result["ok"] is True
        assert result["error"] is None
        assert result["log"] == ["login done"]
        # putLoginHeader 已写登录头并合并 Cookie
        header = json.loads(
            source_state.get_login_header(_SOURCE["bookSourceUrl"]))
        assert header == {"Cookie": "token=abc; uid=7"}
        assert source_state.get_cookie("https://www.example.com/book") == \
            "token=abc; uid=7"
        assert source_state.get_source_variable(
            _SOURCE["bookSourceUrl"]) == "vip=1"

    def test_missing_login_function(self, state_file):
        src = {**_SOURCE, "loginUrl": "@js:function notLogin(){}"}
        source_state.put_login_info(src["bookSourceUrl"], {"a": "1"})
        result = source_login.run_login(src)
        assert result["ok"] is False
        assert "login" in (result["error"] or "")

    def test_empty_info_removes(self, state_file):
        src = {**_SOURCE, "loginUi": '[{"name":"go","type":"button"}]'}
        source_state.put_login_info(src["bookSourceUrl"], {})
        result = source_login.run_login(src)
        assert result["ok"] is True
        assert source_state.get_login_info(src["bookSourceUrl"]) is None


@pytest.mark.skipif(not _js_available(), reason="需要 quickjs/dukpy")
class TestRunAction:
    def test_button_action_js(self, state_file):
        result = source_login.run_action(_SOURCE, "go")
        assert result == {"openUrl": None, "values": {"username": "", "password": "", "level": "B"},
                          "rebuild": False, "error": None, "log": ["1"]}

    def test_open_url_abs(self, state_file):
        src = {**_SOURCE, "loginUi":
               '[{"name":"reg","type":"button","action":"https://a.com/reg"}]'}
        result = source_login.run_action(src, "reg")
        assert result["openUrl"] == "https://a.com/reg"

    def test_up_login_data(self, state_file):
        src = {**_SOURCE, "loginUi":
               '[{"name":"fill","type":"button",'
               '"action":"java.upLoginData({username: \'js\', password: \'pw\'})"}]'}
        result = source_login.run_action(src, "fill")
        assert result["values"]["username"] == "js"
        assert source_state.get_login_info(src["bookSourceUrl"])["password"] == "pw"


# --------------------------------------------------------------- header/请求
class TestHeaderPipeline:
    def test_header_merge_ua_and_login_header(self, state_file):
        source_state.put_login_header(
            _SOURCE["bookSourceUrl"], '{"Token": "t1", "User-Agent": "UA/1"}')
        aurl = AnalyzeUrl("https://www.example.com/search", source=_SOURCE)
        headers = aurl.spec().headers
        # 登录头覆盖合并；UA 缺省补齐
        assert headers["Token"] == "t1"
        assert headers["User-Agent"] == "UA/1"

        source_state.remove_login_header(_SOURCE["bookSourceUrl"])
        aurl2 = AnalyzeUrl("https://www.example.com/search", source=_SOURCE)
        h2 = aurl2.spec().headers
        assert "Token" not in h2
        assert h2["User-Agent"]

    def test_source_header_json_and_login_header(self, state_file):
        source_state.put_login_header(
            _SOURCE["bookSourceUrl"], '{"Cookie": "c=1"}')
        src = {**_SOURCE, "header": '{"Referer": "https://www.example.com"}'}
        headers = AnalyzeUrl(
            "https://www.example.com/", source=src).spec().headers
        assert headers["Referer"] == "https://www.example.com"
        assert headers["Cookie"] == "c=1"

    def test_request_spec_cookie_jar(self):
        spec = AnalyzeUrl("https://www.example.com/s", source=_SOURCE).spec()
        assert spec.cookie_jar is True
        assert spec.source_key == _SOURCE["bookSourceUrl"]
        off = AnalyzeUrl("https://www.example.com/s",
                         source={**_SOURCE, "enabledCookieJar": False}).spec()
        assert off.cookie_jar is False
        # legado JSON 语义：字段缺省 = 启用
        implicit = AnalyzeUrl(
            "https://www.example.com/s",
            source={"bookSourceUrl": "https://www.example.com/"}).spec()
        assert implicit.cookie_jar is True


class TestNetCookieJar:
    def _install_client(self, monkeypatch):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["cookie"] = request.headers.get("cookie")
            seen["url"] = str(request.url)
            return httpx.Response(
                200, text="ok",
                headers=[("Set-Cookie", "sid=xyz; Path=/"),
                         ("Set-Cookie", "k=v")],
            )

        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            **net._base_client_kwargs(),
        )
        monkeypatch.setattr(net, "_sync_client", client)
        return seen

    def test_attach_and_capture(self, state_file, monkeypatch):
        seen = self._install_client(monkeypatch)
        resp = net.fetch_sync_ex(
            "https://www.example.com/login", cookie_jar=True)
        assert resp.ok
        # 首次无 Cookie；响应 Set-Cookie 已入库
        assert seen["cookie"] is None
        assert source_state.get_cookie("https://www.example.com/x") == \
            "sid=xyz; k=v"
        # 第二次请求自动附加
        net.fetch_sync_ex("https://www.example.com/page", cookie_jar=True)
        assert seen["cookie"] == "sid=xyz; k=v"

    def test_no_jar_no_cookie(self, state_file, monkeypatch):
        seen = self._install_client(monkeypatch)
        source_state.set_cookie("https://www.example.com", "a=1")
        net.fetch_sync_ex("https://www.example.com/page")
        assert seen["cookie"] is None

    def test_explicit_cookie_wins(self, state_file, monkeypatch):
        seen = self._install_client(monkeypatch)
        source_state.set_cookie("https://www.example.com", "stored=1")
        net.fetch_sync_ex("https://www.example.com/page",
                          headers={"Cookie": "explicit=1"}, cookie_jar=True)
        assert seen["cookie"] == "explicit=1"


# ------------------------------------------------------------------ API
@pytest.fixture()
def api_env(monkeypatch):
    """内存库 + 挂载 engine_legado 登录路由；权限放行。"""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app import models
    from app.core import db as core_db
    from app.core import deps as core_deps

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(core_db.db, "engine", engine, raising=False)
    monkeypatch.setattr(core_db.db, "session_factory", factory, raising=False)

    async def make_tables():
        async with core_db.get_engine().begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)

    user = types_simple()

    async def fake_current_user():
        return user, ["*"]

    def fake_require_perm(perm_key: str):
        async def checker(current=core_deps.Depends(fake_current_user)):
            return current

        return checker

    monkeypatch.setattr(core_deps, "get_current_user", fake_current_user)
    monkeypatch.setattr(core_deps, "require_perm", fake_require_perm)

    from app.plugins.engine_legado.plugin import create_router

    app = FastAPI()
    app.include_router(create_router(None), prefix="/api/legado")

    state_path = _DATA_DIR / f"_test_state_{uuid.uuid4().hex}.json"
    monkeypatch.setattr(source_state, "_STATE_PATH", state_path)

    async def seed():
        await make_tables()
        async with factory() as s:
            s.add(models.BookSourceRow(
                source_url=_SOURCE["bookSourceUrl"],
                source_name="示例源",
                raw_json=json.dumps(_SOURCE),
                enabled=True,
            ))
            await s.commit()

    asyncio.run(seed())
    yield {"app": app, "factory": factory}
    state_path.unlink(missing_ok=True)


def types_simple():
    import types

    return types.SimpleNamespace(id=7, username="tester", is_superuser=False)


async def _call(app, method: str, url: str, **kw) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, url, **kw)


class TestLoginApi:
    def test_form(self, api_env):
        app = api_env["app"]

        async def scenario():
            r = await _call(app, "GET", "/api/legado/login/form",
                            params={"source_url": _SOURCE["bookSourceUrl"]})
            assert r.status_code == 200
            data = r.json()
            assert data["mode"] == "form"
            assert data["sourceName"] == "示例源"
            names = [row["name"] for row in data["rows"]]
            assert names == ["username", "password", "level", "go"]
            titles = {row["name"]: row["title"] for row in data["rows"]}
            assert titles["username"] == "username"
            assert data["values"]["level"] == "B"
            assert data["hasInfo"] is False
            return data

        import asyncio

        asyncio.run(scenario())

    def test_submit_and_state(self, api_env):
        app = api_env["app"]

        async def scenario():
            r = await _call(app, "POST", "/api/legado/login/submit", json={
                "source_url": _SOURCE["bookSourceUrl"],
                "values": {"username": "u", "password": "p"},
            })
            assert r.status_code == 200
            data = r.json()
            assert data["ok"] is True
            assert data["log"] == ["login done"]
            # 登录头 / Cookie 落库
            r2 = await _call(app, "GET", "/api/legado/login/form",
                             params={"source_url": _SOURCE["bookSourceUrl"]})
            form = r2.json()
            assert form["hasInfo"] is True
            assert form["hasLoginHeader"] is True
            assert form["cookie"] == "token=abc; uid=7"
            # 退出登录
            r3 = await _call(app, "POST", "/api/legado/login/info/remove",
                             json={"source_url": _SOURCE["bookSourceUrl"]})
            assert r3.status_code == 200
            r4 = await _call(app, "GET", "/api/legado/login/form",
                             params={"source_url": _SOURCE["bookSourceUrl"]})
            assert r4.json()["hasInfo"] is False

        import asyncio

        asyncio.run(scenario())

    def test_manual_cookie(self, api_env):
        app = api_env["app"]

        async def scenario():
            r = await _call(app, "POST", "/api/legado/login/cookie", json={
                "source_url": _SOURCE["bookSourceUrl"],
                "cookie": "sid=manual",
            })
            assert r.status_code == 200
            assert r.json()["domain"] == "example.com"
            r2 = await _call(app, "GET", "/api/legado/login/form",
                             params={"source_url": _SOURCE["bookSourceUrl"]})
            assert r2.json()["cookie"] == "sid=manual"

        import asyncio

        asyncio.run(scenario())

    def test_form_unknown_source(self, api_env):
        app = api_env["app"]

        async def scenario():
            r = await _call(app, "GET", "/api/legado/login/form",
                            params={"source_url": "https://nope.example"})
            assert r.status_code == 404

        import asyncio

        asyncio.run(scenario())
