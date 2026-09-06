# Viewer 插件说明与开发规范

本文描述 Viewer 当前实现支持的插件能力。插件是运行在后端进程中的 Python 包，可提供 API、书源引擎，或同时提供两者。

> 插件与后端拥有相同权限，不是沙箱。只安装可信代码。

## 1. 能力边界

| 类型 | 入口 | 用途 | 生效方式 |
| --- | --- | --- | --- |
| API 插件 | `create_router(ctx)` | 增加 FastAPI 接口 | 重启后端后挂载 |
| 书源引擎插件 | `ENGINE` + `create_engine(ctx)` | 实现搜索、发现、详情、目录和正文解析 | 安装后可立即发现 |
| 混合插件 | 同时提供以上入口 | 引擎能力附带管理接口 | 分别遵循上述规则 |

当前插件系统不加载前端代码。前端页面、菜单和路由仍需在 `frontend` 中正常开发和构建。

## 2. 目录结构

每个插件是 `backend/app/plugins` 下的独立包：

```text
backend/app/plugins/example/
├── __init__.py
├── plugin.py          # 必需
├── service.py         # 可选：业务实现
└── data/              # 可选：静态数据
```

约束：

- 目录名使用小写字母、数字和下划线，不得以 `_` 开头。
- `plugin.py` 只放声明、工厂和轻量装配；复杂逻辑拆到包内其他模块。
- 模块导入时不得连接网络、修改数据库或启动后台任务。
- 插件不得修改注册器、应用工厂或其他插件的内部状态。

## 3. 发现与生命周期

启动时系统扫描 `app.plugins` 下的包，并导入：

```text
app.plugins.<目录名>.plugin
```

处理顺序如下：

1. 读取 `meta`。
2. 识别 API 和引擎工厂。
3. 按 `order`、`name` 排序。
4. 读取数据库中的启停状态。
5. 挂载已启用的 API 路由。
6. 在首次请求时创建并缓存引擎实例。

单个插件导入失败时会记录错误并跳过，不阻止其他插件启动。插件实例按引擎 `key` 缓存在进程内，因此实例必须可复用，并自行保证并发安全。

## 4. 元数据规范

所有插件都必须导出 `meta`：

```python
meta = {
    "name": "example",
    "mount": "example",
    "title": "示例插件",
    "version": "1.0.0",
    "description": "一句话说明插件能力",
    "order": 100,
    "permissions": [
        ("example.read", "查看示例数据"),
        ("example.manage", "管理示例数据"),
    ],
}
```

| 字段 | 必需 | 规范 |
| --- | --- | --- |
| `name` | 是 | 全局唯一、稳定；使用小写字母、数字和下划线 |
| `mount` | API 插件必需 | `/api/` 后的单段路径，不含前后斜杠 |
| `title` | 否 | 面向用户的名称，默认等于 `name` |
| `version` | 否 | 建议使用语义化版本，默认 `0.0.0` |
| `description` | 否 | 简短说明，不写安装教程 |
| `order` | 否 | 列表顺序，数字越小越靠前，默认 `100` |
| `permissions` | 否 | `(权限键, 标题)` 列表，默认空 |
| `mount_root` | 特殊场景 | 站点根路径，仅与 `create_root_router` 配套使用 |

`name`、`mount`、引擎 `key` 和权限键发布后不得随意改变。不同插件不得声明相同名称、挂载路径或引擎键。

## 5. API 插件

API 插件导出同步工厂 `create_router(ctx)`，返回 `APIRouter`：

```python
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

if TYPE_CHECKING:
    from ..registry import PluginContext

meta = {
    "name": "example",
    "mount": "example",
    "title": "示例插件",
    "version": "1.0.0",
    "permissions": [("example.read", "查看示例数据")],
}


def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.deps import require_perm

    router = APIRouter(tags=["example"])

    @router.get("")
    async def index(current=Depends(require_perm("example.read"))):
        return {"ok": True}

    return router
```

最终路径为：

```text
/api/<meta.mount>/<router path>
```

只有确实需要脱离 `/api` 的协议端点才可使用根路由：

```python
meta["mount_root"] = "example-protocol"

def create_root_router(ctx: "PluginContext") -> APIRouter:
    ...
```

根路由挂载到 `/<mount_root>`，必须自行完成认证、防冲突和缓存控制。根路由仅对同时具有 `create_router` 的 API 插件有效。

## 6. 书源引擎插件

引擎插件还需导出：

```python
ENGINE = {
    "key": "example",
    "title": "Example 书源",
    "version": "1.0.0",
    "description": "Example 格式的书源解析器",
}

def create_engine(ctx):
    return ExampleEngine(ctx)
```

引擎对象应实现以下接口；不支持发现功能时返回空集合，不得缺少方法：

```python
class ExampleEngine:
    key = "example"

    def matches(self, raw: dict) -> bool: ...
    async def search_book(self, src: dict, key: str, page: int = 1) -> list[dict]: ...
    async def explore_kinds(self, src: dict) -> list[dict]: ...
    async def explore_kind_values(self, src: dict) -> dict: ...
    def explore_kind_action(
        self, src: dict, kind: dict, value: str | None = None,
        long_click: bool = False,
    ) -> dict: ...
    async def explore_book(self, src: dict, url: str, page: int = 1) -> list[dict]: ...
    async def book_info(self, src: dict, book: dict) -> dict: ...
    async def get_toc(
        self, src: dict, book: dict, toc_url: str | None = None,
    ) -> list[dict]: ...
    async def get_content(
        self, src: dict, book: dict, chapter: dict,
        next_chapter_url: str | None = None,
        base_url: str | None = None,
    ) -> str: ...
```

标准返回对象：

```python
# Book：name、bookUrl、origin 为必需字段
{
    "name": "书名",
    "author": "作者",
    "bookUrl": "详情页 URL",
    "coverUrl": "封面 URL",
    "intro": "简介",
    "kind": "分类",
    "wordCount": "字数",
    "lastChapter": "最新章节",
    "tocUrl": "目录 URL",
    "origin": "书源 URL",
    "originName": "书源名称",
}

# Chapter：title、url 为必需字段
{
    "title": "章节名",
    "url": "正文 URL",
    "baseUrl": "解析基准 URL",
    "isVolume": False,
    "isVip": False,
}
```

发现分类至少包含 `title`、`type` 和可选 `url`。动作返回值可使用 `refresh`、`openLogin`、`searchKey` 通知前端刷新、打开登录或发起搜索。

引擎规则：

- 网络和解析错误应抛出异常，不得伪造空成功结果。
- 同步、耗时或 CPU 密集逻辑必须放入线程，不能阻塞事件循环。
- 所有 URL 必须规范化为绝对地址。
- 不在实例中保存单次请求的可变状态；共享缓存必须有边界和并发保护。
- 未识别的引擎键目前会回退到 `legado`，插件不得依赖该回退作为正常流程。

## 7. FastAPI 与类型兼容

请求模型可以定义在 `create_router` 内，但这种插件不得启用：

```python
from __future__ import annotations
```

否则 FastAPI/Pydantic 可能无法解析工厂函数局部作用域中的模型。推荐使用 `TYPE_CHECKING` 和字符串形式的 `PluginContext` 注解，如第 5 节示例。

其他要求：

- 请求和响应字段使用 Pydantic 校验。
- 路由函数优先使用 `async def`。
- 数据库会话通过 `Depends(get_db)` 获取，不得创建全局会话。
- 工厂函数只负责装配，不执行异步初始化。

## 8. 上下文与依赖

`PluginContext` 提供：

| 成员 | 用途 |
| --- | --- |
| `ctx.settings` | 应用配置 |
| `ctx.engine` | 当前 SQLAlchemy 异步引擎 |
| `ctx.session_factory()` | 异步会话工厂 |

插件间协作必须通过稳定接口或共享 `services` 层完成。可选能力先用 `plugin_enabled(name)` 判断；禁止直接访问其他插件的私有变量、路由闭包或缓存。

插件 ZIP 安装器不会安装 Python 依赖。新增第三方依赖必须先加入项目依赖并经过统一部署，不能在插件导入时执行 `pip`。

## 9. 权限与安全

权限键使用 `<插件名>.<能力>`，例如 `example.read`、`example.manage`。

- `meta.permissions` 只负责登记权限目录，不会自动保护路由。
- 受保护端点必须显式使用 `Depends(require_perm("..."))`。
- 仅超级管理员可用 `require_superuser`。
- 超级管理员、`*` 和 `<插件名>.*` 可绕过单项权限检查。
- 公开端点必须是有意设计，并限制输入长度、频率和返回信息。
- 不记录令牌、密码、Cookie、请求正文或远端密钥。
- 文件和 URL 输入必须限制大小、协议和目标范围，防止路径穿越与 SSRF。
- 根路由不会自动继承 API 的认证约定，必须单独审查。

## 10. 安装、更新与启停

管理接口支持上传 ZIP。压缩包可以直接包含 `plugin.py`，也可以只有一个顶层目录且其中包含 `plugin.py`。

| 管理接口 | 作用 |
| --- | --- |
| `GET /api/plugins` | 查看已发现插件及启停状态 |
| `POST /api/plugins/{name}/toggle` | 保存启停状态 |
| `POST /api/plugins/install` | 上传 ZIP 安装或更新插件 |

以上接口仅超级管理员可访问。

安装限制：

- ZIP 最大 `50 MiB`。
- 解压后最大 `128 MiB`。
- 最多 `2000` 个文件条目。
- 拒绝绝对路径和包含 `..` 的路径。
- 缺少 `__init__.py` 时安装器会自动补充。
- 导入或元数据校验失败会自动回滚。
- 覆盖同名目录前会暂存旧版本，成功后替换。

生效规则：

- 新引擎会刷新注册表并清空实例缓存，可立即使用。
- API 路由只在应用创建时挂载，安装、启用或停用后均应重启后端。
- 启停状态保存在 `plugin_states` 表；默认启用未记录的已发现插件。
- 停用引擎会立即阻止新的引擎获取，但仍建议重启以获得一致状态。

## 11. 解耦规范

插件必须遵守以下边界：

1. `plugin.py` 只依赖注册契约和公共服务，不依赖具体页面。
2. 核心业务不得反向导入某个插件实现。
3. 插件不得直接修改其他插件的数据；跨域操作通过服务接口完成。
4. 可选插件缺失或停用时，调用方必须有明确降级路径。
5. 数据表、缓存键、任务名和权限键必须带插件命名空间。
6. API 返回结构保持向后兼容；破坏性变更必须提升主版本。
7. 前端通过 API 能力和权限判断展示功能，不通过插件内部实现判断。

## 12. 验收清单

发布前至少确认：

- [ ] 插件可被 `discover_plugins(force=True)` 发现。
- [ ] `name`、`mount`、引擎 `key` 和权限键无冲突。
- [ ] 导入阶段无网络、数据库写入或后台任务。
- [ ] 每个非公开端点都有权限校验。
- [ ] 输入有类型、长度和范围限制。
- [ ] 异常不会泄露密钥或内部路径。
- [ ] 引擎实现全部接口并返回标准字段。
- [ ] 停用依赖后仍能正常降级。
- [ ] 单元测试覆盖发现、权限、成功路径和失败路径。
- [ ] API 插件重启后验证路由，升级和失败回滚均已测试。

最小验证命令：

```bash
cd backend
python -m pytest tests/test_engine_plugins.py
python -m pytest tests
```
