# Viewer 插件规范（Plugin Specification）

> 适用范围：`backend/app/plugins/` 下全部插件。目标是让每个插件有**统一的结构、
> 统一的元数据、统一的合约**，注册器只靠约定识别，不需要每加一个插件就改框架。
> 版本：v1

---

## 1. 插件是什么

一枚插件是 `app/plugins/<name>/` 这个包目录（必然是**包**，不是单文件）。可以同时承担
一到三种角色：

| 角色 | 必要条件 | 挂载位置 |
|---|---|---|
| **API 插件** | `meta` + `create_router(ctx)` | `/api/<mount>` |
| **根路径插件** | 额外 `create_root_router(ctx)` + `meta["mount_root"]` | 站点根 `/`（如 WebDAV 服务端 `/dav`） |
| **源规则引擎插件** | `ENGINE` + `create_engine(ctx)` | 不挂路由，供 books 分发书源解析 |

引擎插件和 API 插件可并存，但**引擎插件通常不需要 `mount`**（如 `engine_legado`）。

---

## 2. 目录与命名

```
app/plugins/<name>/
├── plugin.py        # 唯一入口：meta / ENGINE / create_*
└── <helpers>.py     # 可选私有实现（体积大时拆开，如 webdav 拆 dav_server / sync_ingest）
```

- 目录名与 `meta["name"]` **保持一致**。（例外：管理插件因避免与 `app/plugins` 包名冲突，
  目录叫 `plugins_admin`，`name` 仍为 `plugins`。）
- 任何以 `_` 开头的包会被注册器**跳过**，不要用 `_` 前缀放插件。
- 辅助模块放插件自己的包内，用相对导入（`from .dav_server import ...`）。

---

## 3. `meta` 元数据（必填字段、顺序如下）

```python
meta = {
    "name": "xxx",                  # 唯一标识；也是启停状态表 plugin_states 的键
    "mount": "xxx",                 # API 插件挂载前缀 /api/<mount>；纯引擎插件可省略
    "title": "中文标题",
    "version": "1.0.0",
    "description": "一句话描述插件职责",
    "order": 30,                    # 排序，越小越靠前；并列时按 name 稳定排序
    "permissions": [                # 权限目录条目：( ns.key, 中文说明 )
        ("xxx.read", "查看 xxx"),
    ],
}
```

**字段规则**

- `name`：小写、下划线分隔、全局唯一。
- `mount`：API 插件必填，取小写字符串，作为 `/api/<mount>` 与 `permissions` 命名空间前缀。
- `version`：语义化 `MAJOR.MINOR.PATCH`。
- `description`：一句话，控制在合理长度。
- `permissions`：每个条目 `(权限键, 中文说明)`，权限键 **必须** 是 `<mount>.<action>`
  命名空间形态，如 `books.read`、`purify.manage`。这决定权限目录按命名空间分组。
- **可选字段**：`author` / `license` / `homepage` / `icon`（展示用）/ `mount_root`（根路径插件）。

---

## 4. `ENGINE`（源规则引擎插件专属）

```python
ENGINE = {
    "key": "legado",               # 书源行 engine 字段使用的标识；缺省回退为 name
    "title": "引擎标题",
    "version": "1.0.0",
    "description": "引擎说明",
}
```

`books` 插件按书源行的 `engine` 字段（缺省 `legado`）把解析分发给对应用引；因此后台引擎的
`key` 越小写、一旦对外发布尽量不要改（会破坏已导入书源的绑定）。

---

## 5. 工厂函数契约

```python
def create_router(ctx: PluginContext) -> APIRouter:        # API 插件
def create_engine(ctx: PluginContext) -> SourceEngine:      # 引擎插件
def create_root_router(ctx: PluginContext) -> APIRouter:    # 根路径插件（可选）
```

- 每个 `create_router` 内部：`router = APIRouter(tags=["<mount>"])` —— `tags` 与 `mount`
  一致，Swagger 按插件分组。
- `ctx` 类型统一为 `PluginContext`（见 §7 的惰性引入方式）。

`PluginContext` 暴露给插件的共享服务：

```python
ctx.settings           # config.Settings（含全部 VIEWER_* 配置）
ctx.session_factory()  # -> 一个 async SQLAlchemy Session 工厂
ctx.engine             # 默认源引擎实例（等价 get_engine()）
```

---

## 6. 源引擎（SourceEngine）接口契约

引擎对象需实现以下**异步**方法（books 插件按此调用）：

```python
async search_book(src, key, page=1) -> list[dict]
async explore_kinds(src) -> list[dict]
async explore_book(src, url, page=1) -> list[dict]
async book_info(src, book) -> dict
async get_toc(src, book, toc_url=None) -> list[dict]
async get_content(src, book, chapter, next_chapter_url=None, base_url=None) -> str
matches(src) -> bool        # 可选：判断一份书源 JSON 是否属于本引擎
```

`src` 为书源原始 JSON dict；`chapter`/`book` 为解析用 dict；内容返回**未净化原文串**
（净化/替换由 books/content_purify 在引擎之外处理）。

---

## 7. 编码约定（规范化强制项）

> **重要 —— 不要在 API 插件里用 `from __future__ import annotations`。**
> 本代码库的 pydantic 请求体模型（`class XxxBody(BaseModel)`）定义在 `create_router`
> **函数内部**（函数局部变量）。FastAPI 通过 `typing.get_type_hints()` 解析路由参数，
> 用的是**模块全局**命名空间；一旦开了 future-annotations，`body: XxxBody` 变成字符串，
> FastAPI 无法在全局里解析这个局部类，就会把 `body` 误判成 query 参数 → 所有带请求体的
> 路由统一返回 `422 {"loc":["query","body"]}`。实测即此故障（曾因此登录失败）。
>
> 所以：API 插件**保持注解运行时求值**（不开 future import）；引擎插件（无路由、无
> 请求体模型）可以安全地使用它。

- **工厂签名**用 `ctx: "PluginContext"`（加引号的字符串注解），配合 `TYPE_CHECKING`
  引入类型——因为 `create_router` / `create_engine` / `create_root_router` **不是** FastAPI
  路由函数，不会被 `get_type_hints` 解析，字符串注解零运行时开销且安全：
  ```python
  from typing import TYPE_CHECKING

  if TYPE_CHECKING:
      from ...plugins.registry import PluginContext

  def create_router(ctx: "PluginContext") -> APIRouter:
      ...
  ```
- **惰性导入**：DB 会话、模型、鉴权依赖等一律在 `create_router` 内部再
  `from ...core.deps/db/models import ...`，不在模块顶层拉取，避免 import 次序耦合。
- 模块顶部用 docstring 说明**职责 + 端点清单**。
- 私有辅助用 `_` 前缀（`_MAX_BYTES`、`_dec_pwd()` 等）。
- 不用 `from xxx import *`，保持显式导入。

---

## 8. 启停与插件间协作

- 启停状态持久化于 `plugin_states` 表（插件相互之间不直接写）。
- **停用**：启动时注册器过滤掉，不挂载；引擎停用后其解析立即不可用。
- **弱依赖协作**：需要“若某插件启用则委托它，否则走自身回退”时，用
  `registry.plugin_enabled("content_purify")` 判断，而**不要**顶层 import 对方插件——
  例：`books` 在启用 `content_purify` 时走净化管线，否则直接返回原文。

---

## 9. 新增插件模板

```python
"""xxx 插件 — 一句话职责。设置端 / 端点清单可在此列明。"""
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

meta = {
    "name": "xxx",
    "mount": "xxx",
    "title": "标题",
    "version": "1.0.0",
    "description": "描述",
    "order": 100,
    "permissions": [("xxx.read", "查看 xxx")],
}


def create_router(ctx: "PluginContext") -> APIRouter:
    # 请求体模型在函数内部定义（沿用现有约定），不开 future-annotations
    from fastapi import Depends
    from pydantic import BaseModel

    from ...core.deps import require_perm
    from ...core.db import get_db

    class XxxBody(BaseModel):
        ...

    router = APIRouter(tags=["xxx"])

    @router.post("/...")
    async def one(body: XxxBody, current=Depends(require_perm("xxx.read"))):
        ...

    return router
```

---

## 10. 注册器规则（与 registry 现状对齐）

- 扫描 `app/plugins/*/plugin.py`，跳过以 `_` 开头的包。
- 判定为插件需有 `meta` 且满足 `create_router` 存在**或**（`ENGINE` 存在且 `create_engine` 存在）。
- 挂载：API 插件挂 `/api/<mount>`；有 `mount_root` 的额外挂 `/<mount_root>`。
- 引擎缓存按 `ENGINE["key"]` 去重；同 key 后者覆盖。
- 单个插件加载失败只打印告警，不影响其他插件加载（“坏插件隔离”）。

---

*参照实现：`app/plugins/engine_legado/plugin.py`（引擎）、`app/plugins/webdav/plugin.py`
（API + 根路径）、`app/plugins/books/plugin.py`（最大型 API 插件）。注册器见
`app/plugins/registry.py`。*