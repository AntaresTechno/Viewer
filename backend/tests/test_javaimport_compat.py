"""Regression tests: Rhino JavaImporter compatibility on QuickJS.

番茄类书源在 jsLib 里 `new JavaImporter()` + `with(javaImport){...}` + 用
okhttp3/hutool，本测试确保：
1. 初始化不再抛 `ReferenceError: JavaImporter is not defined`；
2. okhttp3 / hutool 兼容类可经 java._http 回到 Python 真正发请求；
3. 状态接口只声明 QuickJS。
"""
from __future__ import annotations

import json

import pytest

from app.legado_rule import js_bridge as jb

if jb.detect_engine() is None:
    pytest.skip("需要 QuickJS")

# 复刻番茄 jsLib 的初始化段（JavaImporter + importPackage + with 作用域）
MINI_JSLIB = """
javaImport = new JavaImporter()
javaImport.importPackage(
    Packages.okhttp3,
    Packages.cn.hutool.core.util,
    Packages.cn.hutool.core.codec,
    Packages.cn.hutool.crypto.digest
)
with(javaImport) {
    brand = String(Packages.android.os.Build.BRAND);
    sdkInt = Packages.android.os.Build.VERSION.SDK_INT;
    function okhttpGet(url, map) {
        const { java } = this;
        let request = new Request.Builder().url(url).get();
        if (map) { for (let n in map) request.addHeader(n, map[n]); }
        request.addHeader("Accept-Encoding", "identity");
        let client = new OkHttpClient();
        let response = client.newCall(request.build()).execute();
        return JSON.parse(response.body().string());
    }
    const md5 = (str) => String(DigestUtil.md5Hex(str));
    const rStr = (str) => String(StrUtil.reverse(str));
    const b64 = (s) => Base64.encode(s);
}
"""


def _make_source(jslib=MINI_JSLIB) -> dict:
    return {"bookSourceUrl": "https://fanqie.example.com", "jsLib": jslib}


def test_java_importer_init_no_crash():
    ev = jb.JsEvaluator({"source": _make_source()})
    # 初始化不再抛 JavaImporter is not defined
    assert ev.eval("typeof JavaImporter") == "function"
    assert ev.eval("typeof Packages") == "object"
    assert ev.eval("typeof javaImport") == "object"
    assert ev.eval("brand")  # with 作用域变量已就绪
    assert ev.eval("typeof Packages.okhttp3.Request.Builder") == "function"


def test_with_scope_helpers_callable():
    ev = jb.JsEvaluator({"source": _make_source()})
    assert ev.eval("Packages.cn.hutool.core.util.StrUtil.reverse('abc')") == "cba"
    # md5 经 java.md5Encode 桥
    assert ev.eval("java.md5Encode('abc')") == "900150983cd24fb0d6963f7d28e17f72"


def test_okhttp_roundtrip_through_python_bridge(monkeypatch):
    seen = {}

    class FakeBridge(jb.JavaBridge):
        def httpRequest(self, method="GET", url="", headers=None, body=None):
            seen["call"] = (method, url, headers)
            return json.dumps({"code": 200, "body": json.dumps({"ok": 1, "m": method})})

    ev = jb.JsEvaluator({
        "__bridge__": FakeBridge(),
        "source": _make_source(),
    })
    out = ev.eval('okhttpGet.call({java: java}, "https://m.x.com/api?q=1", {"Host":"m.x.com"})')
    assert isinstance(out, dict)
    assert out.get("ok") == 1
    method, url, headers = seen["call"]
    assert method == "GET"
    assert url == "https://m.x.com/api?q=1"
    assert headers.get("Accept-Encoding")


def test_list_engines_is_quickjs_only():
    state = jb.list_engines()
    assert state["requested"] == "quickjs"
    keys = {i["key"] for i in state["items"]}
    assert keys == {"quickjs"}
    assert state["current"] == "quickjs"


def test_rhino_compat_asset_present():
    assert len(jb._RHINO_COMPAT) > 500
    assert "JavaImporter" in jb._RHINO_COMPAT
    assert "Packages" in jb._RHINO_COMPAT
