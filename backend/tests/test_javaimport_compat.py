"""Regression tests: Rhino JavaImporter compat + JS 引擎切换。

番茄类书源在 jsLib 里 `new JavaImporter()` + `with(javaImport){...}` + 用
okhttp3/hutool，本测试确保：
1. 初始化不再抛 `ReferenceError: JavaImporter is not defined`；
2. okhttp3 / hutool 兼容类可经 java._http 回到 Python 真正发请求；
3. list_engines / set_active_engine 的引擎切换逻辑可用。
"""
from __future__ import annotations

import json

from pathlib import Path

import pytest

from app.core.config import DATA_DIR
from app.legado_rule import js_bridge as jb

if jb.detect_engine() is None:
    pytest.skip("需要 JS 引擎（quickjs/stpyv8/dukpy 任一）")

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


def test_list_engines_shape(monkeypatch):
    state = jb.list_engines()
    assert state["requested"] in ("auto", "quickjs", "stpyv8", "dukpy")
    keys = {i["key"] for i in state["items"]}
    assert {"quickjs", "stpyv8", "dukpy"} <= keys


def test_set_active_engine_override(monkeypatch):
    # 沙箱下 tmp_path 不可写，改写到仓库内 data 目录的临时文件并清理
    tmp_file = DATA_DIR / "js_engine_test.json"
    try:
        if tmp_file.exists():
            tmp_file.unlink()
        monkeypatch.setattr(jb, "_override_file", lambda: tmp_file)
        monkeypatch.setattr(jb, "_engine_name", None)
        jb.set_active_engine("quickjs")
        assert jb._read_override() == "quickjs"
        assert jb._requested_engine() == "quickjs"
        # 未知引擎被拒
        with pytest.raises(ValueError):
            jb.set_active_engine("nope")
        # 恢复 auto 兜底
        monkeypatch.setattr(jb, "_engine_name", None)
        jb.set_active_engine("auto")
        assert jb._requested_engine() == "auto"
    finally:
        if tmp_file.exists():
            tmp_file.unlink()


def test_rhino_compat_asset_present():
    assert len(jb._RHINO_COMPAT) > 500
    assert "JavaImporter" in jb._RHINO_COMPAT
    assert "Packages" in jb._RHINO_COMPAT