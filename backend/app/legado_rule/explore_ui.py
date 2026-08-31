"""发现页二级菜单交互（legado ``ExploreKindUiUseCase`` 移植）。

legado 的发现页不只是「分类列表」：``exploreUrl`` 可以返回
``type`` 为 ``url`` / ``text`` / ``button`` / ``toggle`` / ``select`` 的
**控件**，每个控件带一条 ``action`` JS，点击时在书源上下文里求值
（``ExploreKindUiUseCase.executeAction``）。番茄书源正是这么用的：

.. code-block:: js

    { title: "⚙", type: "button",
      action: "java.open('login','http',null);saveKeys(infoMap)" }
    { title: "分类：", type: "select",
      action: "z(Z.indexOf(String(infoMap['分类：'])))",
      chars: ['女生','男生','出版','自定义多选'], default: "自定义多选" }

所以动作**只能在服务端求值**——它调的是书源 jsLib 里的 ``z()`` /
``saveKeys()``，要签名、发请求、读写 ``infoMap``，前端拿不到这些。

本模块负责：

- :func:`normalize_style`  ``style`` 补默认值（对齐 ``FlexChildStyle``）
- :func:`run_kind_action`  在带 ``infoMap`` + 信号捕获 ``java`` 桥的上下文里
  执行一条 ``action``，并把宿主该做的事（刷新发现页 / 打开登录 / 跳搜索）
  作为**信号**返回给前端。

信号而非副作用：服务端没有宿主 UI，``java.refreshExplore()`` /
``java.open('login')`` / ``java.searchBook(key)`` 在 legado 里是 Activity
跳转，这里只能转成结构化返回，由前端决定怎么走。
"""
from __future__ import annotations

import json
from typing import Any

from .source_bridge import InfoMapBridge, SourceLoginBridge, bridges_for, source_key
from .source_login import get_login_info, get_login_js

# legado ``FlexChildStyle`` 的默认值（data class 默认参数）。书源只写
# layout_flexGrow / layout_flexBasisPercent 两个，其余必须补默认，否则
# 前端算 span 时 `undefined >= 1` 一类的比较会静默走错分支。
DEFAULT_STYLE: dict[str, Any] = {
    "layout_flexGrow": 0.0,
    "layout_flexShrink": 1.0,
    "layout_alignSelf": "auto",
    "layout_flexBasisPercent": -1.0,
    "layout_wrapBefore": False,
    "layout_justifySelf": "auto",
}

_STYLE_FIELDS = frozenset(DEFAULT_STYLE)


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            return default
    return default


def normalize_style(style: Any) -> dict[str, Any]:
    """把书源给的 ``style`` 补全成完整的 FlexChildStyle 形状。

    只保留 legado 认识的 ``layout_*`` 字段；缺的用默认值填，类型统一成
    float / bool，前端不必再判空。
    """
    out = dict(DEFAULT_STYLE)
    if not isinstance(style, dict):
        return out
    for key in _STYLE_FIELDS:
        if key not in style:
            continue
        raw = style[key]
        if key == "layout_wrapBefore":
            out[key] = bool(raw) and raw is not False
        elif key in ("layout_alignSelf", "layout_justifySelf"):
            out[key] = str(raw) if raw not in (None, "") else DEFAULT_STYLE[key]
        else:
            out[key] = _as_float(raw, float(DEFAULT_STYLE[key]))  # type: ignore[arg-type]
    return out


class ExploreJavaBridge(SourceLoginBridge):
    """发现页控件 ``action`` 的 ``java`` 桥。

    legado 里这个位置是 ``SourceLoginJsExtensions``：``open()`` /
    ``searchBook()`` / ``refreshExplore()`` 都会跳 Activity。服务端没有
    Activity，改成往 ``signals`` 里写信号，随响应回给前端。
    """

    def __init__(self, source: dict | None = None, base_url: str = "",
                 log_sink: list[str] | None = None,
                 signals: dict[str, Any] | None = None,
                 on_rebuild: Any = None):
        super().__init__(source, base_url=base_url,
                         on_rebuild=on_rebuild, log_sink=log_sink)
        self._signals = signals if signals is not None else {}

    def open(self, name: Any = "", url: Any = None, title: Any = None,
             origin: Any = None) -> None:
        """``java.open('login', …)``：番茄 ⚙ 按钮就是用它打开登录页。"""
        self.log("[open]", name, url, title, origin)
        if str(name or "").strip().lower() == "login":
            self._signals["openLogin"] = True

    def searchBook(self, key: Any = "", searchScope: Any = None) -> None:
        """``java.searchBook(key, scope)``：发现页的「搜索」按钮。"""
        self.log("[searchBook]", key, searchScope)
        text = str(key or "").strip()
        if text:
            self._signals["searchKey"] = text

    def refreshExplore(self) -> None:
        """请求重建发现页 —— 经 ``on_rebuild`` 变成 ``refresh`` 信号。"""
        self.log("[refreshExplore]")
        super().refreshExplore()


def _kind_bindings(source: dict, info: dict[str, str],
                   info_map: InfoMapBridge, bridge: ExploreJavaBridge,
                   long_click: bool) -> dict[str, Any]:
    """对齐 legado ``evalButtonClick`` 的 bindings（java / infoMap / result）。

    ``result`` 绑登录表单数据（``SourceLoginViewModel.evaluate`` 的语义），
    番茄 jsLib 的 ``Map(e)`` 走的是 ``source.getLoginInfoMap()``，
    ``infoMap`` 则是发现页输入（关键词/分类/偏好…）的持久化容器，
    两者不是一回事，必须都给。
    """
    ns = bridges_for(source)
    ns["infoMap"] = info_map
    return {
        "baseUrl": source_key(source),
        "result": dict(info),
        "book": None,
        "chapter": None,
        "isLongClick": long_click,
        "source": source,
        "__bridge__": bridge,
        "__ns__": ns,
    }


def current_values(source: dict) -> dict[str, str]:
    """发现页输入（infoMap）的当前值，供前端回显 select / toggle / text。"""
    try:
        return json.loads(InfoMapBridge(source).toJSON())
    except Exception:  # noqa: BLE001 - 回显失败不该影响主流程
        return {}


def run_kind_action(source: dict, kind: dict | None, value: str | None = None,
                    long_click: bool = False) -> dict[str, Any]:
    """执行一个发现页控件的 ``action``。

    顺序对齐 legado ``ToggleTypeItem`` / ``SelectTypeItem``：
    **先写值再执行动作**——番茄的 action 读 ``infoMap['分类：']`` 取当前
    选中项，值没落盘动作就是空转。

    返回 ``{"refresh", "openLogin", "searchKey", "log", "values", "error"}``：
    ``refresh`` 表示书源请求重建发现页（分类切换后按钮集合会变），
    前端据此重拉 kinds。
    """
    from .js_bridge import eval_js

    kind = kind if isinstance(kind, dict) else {}
    signals: dict[str, Any] = {"refresh": False, "openLogin": False,
                               "searchKey": None}
    log: list[str] = []

    def _on_rebuild(_delta: bool) -> None:
        signals["refresh"] = True

    bridge = ExploreJavaBridge(
        source, base_url=source_key(source), log_sink=log,
        signals=signals, on_rebuild=_on_rebuild,
    )
    info_map = InfoMapBridge(source)
    title = str(kind.get("title") or "").strip()
    if value is not None and title:
        info_map.put(title, value)

    bindings = _kind_bindings(source, get_login_info(source), info_map,
                              bridge, long_click)

    # legado 的 evalButtonClick 只 eval action 本身（loginUrl 不前置）。
    # 但番茄的 z()/w() 读 loginUrl 注入的 $$$ 配置包，不前置会
    # ReferenceError；番茄自己也把 eval(String(source.loginUrl)) 写在
    # 每条规则首行。前置失败不阻断动作——action 可能并不依赖它。
    login_js = get_login_js(source) or ""
    action = str(kind.get("action") or "").strip()
    code = f"{login_js}\n{action}" if login_js else action

    error: str | None = None
    if action:
        try:
            eval_js(code, bindings)
        except Exception as exc:  # noqa: BLE001 - 动作失败要回传而非炸接口
            error = f"{type(exc).__name__}: {exc}"
            log.append(f"[error] {error}")

    return {
        "refresh": bool(signals["refresh"]),
        "openLogin": bool(signals["openLogin"]),
        "searchKey": signals["searchKey"],
        "log": log,
        "values": current_values(source),
        "error": error,
    }
