# 插件开发

## 组件类型

每个组件位于 `backend/app/plugins/<package>/`，入口固定为 `plugin.py`。

| `kind` | 用途 | 可停用 | 必需能力 |
| --- | --- | --- | --- |
| `core` | 应用基础模块 | 否 | 通常提供 `create_router` |
| `plugin` | 可选业务功能 | 是 | API、插件 UI 至少一种 |
| `engine` | 来源规则引擎 | 是 | `ENGINE` 与 `create_engine` |

外部 ZIP 不能声明 `core`。

## 最小 API 插件

```text
backend/app/plugins/example/
├── __init__.py
└── plugin.py
```

```python
from typing import TYPE_CHECKING
from fastapi import APIRouter, Depends

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

PLUGIN = {
    "kind": "plugin",
    "name": "example",
    "mount": "example",
    "title": "示例插件",
    "version": "1.0.0",
    "description": "提供一个示例接口",
    "order": 100,
    "permissions": [("example.read", "查看示例")],
}

def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.deps import require_perm

    router = APIRouter(tags=["example"])

    @router.get("/status")
    async def status(current=Depends(require_perm("example.read"))):
        return {"ok": True}

    return router
```

最终接口路径是 `/api/example/status`。

## `PLUGIN` 字段

| 字段 | 说明 |
| --- | --- |
| `kind` | `core`、`plugin` 或 `engine` |
| `name` | 全局稳定标识，也是启停状态的数据库键 |
| `mount` | API 在 `/api` 下的单段挂载名；无 API 时可以省略 |
| `title` | 管理界面显示名称 |
| `version` | 组件版本 |
| `description` | 简短能力说明 |
| `order` | 基础装配顺序，数值越小越靠前 |
| `permissions` | `(权限键, 中文标题)` 列表 |
| `requires` | 硬前置组件名称列表 |
| `ui` | 插件自带管理界面声明 |
| `mount_root` | 根路径挂载名，只用于确实不能位于 `/api` 下的协议服务 |

注册器会在 `order` 排序之上执行稳定的依赖排序。缺失或停用任一前置时，依赖插件也会视为不可用。存在已启用依赖方时，管理接口会拒绝停用前置插件。

## 插件上下文

`create_router(ctx)` 与 `create_engine(ctx)` 接收同一个 `PluginContext`：

- `ctx.settings`：应用配置。
- `ctx.engine`：异步 SQLAlchemy 引擎。
- `ctx.session_factory()`：异步会话工厂。

模块导入阶段不要发起网络请求、写数据库、注册系统协议或启动线程。运行期初始化应放入路由调用、显式用户操作或应用服务中。

## 插件自带 UI

插件可以把界面放在自己的包内：

```text
example/
├── __init__.py
├── plugin.py
└── ui/
    └── index.html
```

声明：

```python
PLUGIN = {
    # 其他字段省略
    "ui": {
        "entry": "ui/index.html",
        "title": "示例插件设置",
    },
}
```

约束：

- `entry` 必须是插件包内的相对 `.html` 路径，不能包含 `..` 或反斜杠。
- 当前只发布单文件 HTML；CSS 和 JavaScript 应内联。
- 文件最大 2 MiB。
- 只有超级管理员能获取插件 UI，停用插件不能打开。
- 页面运行在不具备同源权限的沙箱 iframe 中，无法直接读取主应用令牌。

宿主页会在插件脚本运行前注入：

```javascript
window.viewerPlugin.context
// { name, title, baseUrl }

const data = await window.viewerPlugin.request("GET", "/status")
await window.viewerPlugin.request("POST", "/settings", { enabled: true })
window.viewerPlugin.close()
```

`request` 仅转发到当前插件的 `/api/<mount>`，支持 `GET`、`POST`、`PUT`、`PATCH` 和 `DELETE`。宿主页负责附加登录态，但插件 API 仍必须使用 `require_perm` 或 `require_superuser` 做服务端授权。

## 规则引擎插件

引擎还需声明：

```python
ENGINE = {
    "key": "example",
    "title": "Example 规则",
    "version": "1.0.0",
    "description": "解析 Example 来源",
}

def create_engine(ctx):
    return ExampleEngine(ctx)
```

引擎实例应可复用，并实现搜索、发现、详情、目录和正文接口。完整方法集合见[书源、订阅源与规则引擎](sources-and-engines.md)。

## ZIP 安装

ZIP 可以直接把 `plugin.py` 放在根目录，也可以只包含一个顶层插件目录。安装器会自动补缺失的 `__init__.py`，导入并校验声明，失败时恢复旧版本。

安全限制：

- ZIP 文件最大 50 MiB。
- 解压总量最大 128 MiB。
- 最多 2000 个文件。
- 拒绝绝对路径与 `..` 路径。
- 不自动安装 Python 第三方依赖。

引擎注册表会在安装后刷新；新 API 路由需要重启后端才会装配。更新插件前应备份数据库与原插件包。

## 开发检查

```bash
cd backend
python -m pytest tests/test_engine_plugins.py
python -m compileall -q app
```

至少验证插件发现、依赖启停、权限拒绝、输入错误和成功路径。插件 UI 还应验证元数据、入口文件和宿主请求通道。
