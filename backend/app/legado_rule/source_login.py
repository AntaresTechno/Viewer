"""Source login flow — port of legado's BaseSource login + SourceLoginViewModel.

登录语义对照（legado-with-MD3-main）：

- ``BaseSource.getLoginJs()``    loginUrl 去掉 ``@js:`` / ``<js></js>`` 前缀
- ``BaseSource.login()``         执行 loginJs + ``login()`` 函数
- ``SourceLoginViewModel.buildForm()``  loginUi 解析（支持 @js:/<js> 动态表单）
- ``SourceLoginViewModel.confirm()``    保存 loginInfo 后执行 login()
- ``SourceLoginViewModel.runRowAction()`` 登录页按钮动作（绝对 url 直接打开）

服务端没有 WebView：``loginUi`` 为空而 ``loginUrl`` 是网址的源（Web 模式）
由登录接口返回该地址并允许手工粘贴 Cookie，不做无头渲染伪造。
"""
from __future__ import annotations

import json
from typing import Any

from . import source_state
from .source_bridge import SourceLoginBridge, bridges_for, source_key

_JS_PREFIXES = ("@js:", "<js>")


def _strip_js_prefix(text: str) -> str:
    """去掉 @js: / <js></js> 包装（BaseSource.getLoginJs）。"""
    low = text.lower()
    if low.startswith("@js:"):
        return text[4:]
    if low.startswith("<js>"):
        end = text.rfind("<")
        return text[4:end] if end > 4 else text[4:]
    return text


def get_login_js(source: dict | None) -> str | None:
    login_url = (source or {}).get("loginUrl")
    if not login_url or not isinstance(login_url, str):
        return None
    return _strip_js_prefix(login_url)


def login_mode(source: dict | None) -> str:
    """``none`` / ``form``（有 loginUi）/ ``web``（仅 loginUrl 网址）。"""
    src = source or {}
    login_ui = src.get("loginUi")
    if isinstance(login_ui, str) and login_ui.strip():
        return "form"
    if get_login_js(src) is not None:
        return "web"
    return "none"


def web_login_url(source: dict | None) -> str | None:
    """Web 模式可打开的登录网址（JS 型 loginUrl 不算网址，返回 None）。"""
    src = source or {}
    if login_mode(src) != "web":
        return None
    login_url = str(src.get("loginUrl") or "").strip()
    low = login_url.lower()
    if low.startswith(_JS_PREFIXES):
        return None
    return login_url


# ------------------------------------------------------------------ loginUi
def _eval_login_snippet(source: dict, code: str, login_info: dict[str, str],
                        bridge: SourceLoginBridge) -> Any:
    """登录上下文的 JS 求值（bindings 对齐 SourceLoginViewModel.evaluate）。"""
    from .js_bridge import eval_js

    bindings: dict[str, Any] = {
        "baseUrl": source_key(source),
        "result": dict(login_info),
        "book": None,
        "chapter": None,
        "isLongClick": False,
        "source": source or None,
        "__bridge__": bridge,
        "__ns__": bridges_for(source),
    }
    return eval_js(code, bindings)


def login_rows(source: dict, login_info: dict[str, str] | None = None,
               bridge: SourceLoginBridge | None = None) -> list[dict]:
    """解析 loginUi 为 RowUi 行列表（name/type/action/chars/default/viewName）。"""
    login_ui = source.get("loginUi")
    if not login_ui or not isinstance(login_ui, str) or not login_ui.strip():
        return []
    info = dict(login_info or {})
    bridge = bridge or SourceLoginBridge(source)
    text = login_ui.strip()
    low = text.lower()
    if low.startswith(_JS_PREFIXES):
        code = _strip_js_prefix(text)
        login_js = get_login_js(source) or ""
        ev = _eval_login_snippet(source, f"{login_js}\n{code}", info, bridge)
        text = "" if ev is None else str(ev)
    try:
        rows = json.loads(text)
    except Exception:  # noqa: BLE001 - 解析失败按空表单处理（记日志）
        return []
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        chars = row.get("chars")
        out.append({
            "name": name,
            "type": str(row.get("type") or "text"),
            "action": row.get("action"),
            "chars": [c for c in chars if isinstance(c, str)] if isinstance(chars, list) else None,
            "default": row.get("default"),
            "viewName": row.get("viewName"),
        })
    return out


def row_title(row: dict) -> str:
    """viewName 解析：``'字面量'`` 直接取引号内容（resolveTitle）。"""
    view_name = row.get("viewName")
    if not view_name or not isinstance(view_name, str):
        return str(row.get("name") or "")
    view_name = view_name.strip()
    if 3 <= len(view_name) <= 19 and view_name.startswith("'") \
            and view_name.endswith("'"):
        return view_name[1:-1]
    return str(row.get("name") or "")


def default_login_info(source: dict, rows: list[dict] | None = None) -> dict[str, str]:
    """无已存登录信息时按 loginUi 取默认值（getLoginInfoMap 兜底语义）。"""
    rows = rows if rows is not None else login_rows(source)
    info: dict[str, str] = {}
    for row in rows:
        if row["type"] == "button":
            continue
        info[str(row["name"])] = str(row.get("default") or "")
    return info


def get_login_info(source: dict) -> dict[str, str]:
    """已保存的登录表单数据；没有时返回 loginUi 默认值（不入库）。"""
    key = source_key(source)
    stored = source_state.get_login_info(key)
    if stored is not None:
        return stored
    return default_login_info(source)


# ---------------------------------------------------------------- execution
def _login_bindings(source: dict, login_info: dict[str, str],
                    bridge: SourceLoginBridge, long_click: bool = False) -> dict:
    bindings: dict[str, Any] = {
        "baseUrl": source_key(source),
        "result": login_info,
        "book": None,
        "chapter": None,
        "isLongClick": long_click,
        "source": source or None,
        "__bridge__": bridge,
        "__ns__": bridges_for(source),
    }
    return bindings


def run_login(source: dict) -> dict:
    """confirm() 全流程：保存登录信息并执行 ``login()``。

    返回 ``{"ok": bool, "error": str|None, "log": [..], "values": {...}}``。
    """
    login_js = get_login_js(source) or ""
    log: list[str] = []
    info_updater = {"data": None}

    def _on_data(data: dict | None) -> None:
        info_updater["data"] = data

    bridge = SourceLoginBridge(
        source, base_url=source_key(source),
        on_login_data=_on_data, log_sink=log,
    )
    login_info = get_login_info(source)
    if not login_info:
        source_state.remove_login_info(source_key(source))
        return {"ok": True, "error": None, "log": log,
                "values": dict(login_info)}
    source_state.put_login_info(source_key(source), login_info)

    js = (
        f"{login_js}\n"
        "if (typeof login == 'function') {\n"
        "    login.apply(this);\n"
        "} else {\n"
        "    throw('Function login not implements!!!')\n"
        "}\n"
    )
    try:
        from .js_bridge import eval_js

        eval_js(js, _login_bindings(source, login_info, bridge))
    except Exception as exc:  # noqa: BLE001 - 登录失败要回传错误而非炸接口
        return {"ok": False, "error": str(exc), "log": log,
                "values": dict(login_info)}
    if isinstance(info_updater["data"], dict):
        login_info = {
            k: "" if v is None else str(v)
            for k, v in info_updater["data"].items()
        }
        source_state.put_login_info(source_key(source), login_info)
    _tok = str(login_info.get("token：") or "").strip()
    if _tok:
        source_state.ensure_session_global(_tok)
    return {"ok": True, "error": None, "log": log, "values": dict(login_info)}


def run_action(source: dict, key: str, long_click: bool = False) -> dict:
    """执行登录页按钮动作（runRowAction）。

    绝对 url 动作返回 ``{"openUrl": ...}`` 由前端打开；JS 动作原地求值，
    ``java.upLoginData`` 的数据会持久化并通过 ``values`` 返回。
    """
    action = str(key or "").strip()
    row = next((r for r in login_rows(source) if r["name"] == action), None)
    if row and row.get("action"):
        action = str(row["action"]).strip()
    if not action:
        return {"openUrl": None, "values": dict(get_login_info(source)),
                "rebuild": False, "error": "按钮未配置动作", "log": []}
    if action.startswith(("http://", "https://")):
        return {"openUrl": action, "values": dict(get_login_info(source)),
                "rebuild": False, "error": None, "log": []}

    log: list[str] = []
    login_info = dict(get_login_info(source))
    rebuild = {"flag": False}

    def _on_data(data: dict | None) -> None:
        nonlocal login_info
        if data is None:
            login_info = default_login_info(source)
            source_state.put_login_info(source_key(source), login_info)
        elif isinstance(data, dict):
            login_info.update({
                k: "" if v is None else str(v) for k, v in data.items()
            })

    def _on_rebuild(_delta: bool) -> None:
        rebuild["flag"] = True

    bridge = SourceLoginBridge(
        source, base_url=source_key(source),
        on_login_data=_on_data, on_rebuild=_on_rebuild, log_sink=log,
    )
    login_js = get_login_js(source) or ""
    try:
        from .js_bridge import eval_js

        eval_js(f"{login_js}\n{action}",
                _login_bindings(source, login_info, bridge, long_click))
    except Exception as exc:  # noqa: BLE001
        return {"openUrl": None, "values": dict(login_info),
                "rebuild": rebuild["flag"], "error": str(exc), "log": log}
    source_state.put_login_info(source_key(source), login_info)
    # 书源 [账号登录] 用 token：/cookie 拼 sessionid；把这份登录态镜像到番茄
    # 全部 SSO 域名，目录/正文/搜索才能全局用到（从局部走向全局）。
    _tok = str(login_info.get("token：") or "").strip()
    if _tok:
        source_state.ensure_session_global(_tok)
    return {"openUrl": None, "values": dict(login_info),
            "rebuild": rebuild["flag"], "error": None, "log": log}
