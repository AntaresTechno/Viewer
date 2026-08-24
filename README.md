# Viewer

一个以 **Miuix / Material Design 3 双设计系统（运行时可切换，默认 MD3 蓝白，可切换深色/跟随系统）** 为前端、**FastAPI 插件化后端** 的在线阅读站点，内置与 [Legado（阅读）](https://github.com/gedoor/legado) 书源规则兼容的解析引擎。

```
viewer/
├── backend/            # FastAPI + SQLAlchemy(async aiosqlite)
│   ├── app/
│   │   ├── core/       # 配置 / 数据库 / 安全(JWT+pbkdf2) / 依赖注入权限
│   │   ├── models/     # User, Role, PluginState, BookSourceRow, ShelfItem, ReadProgress
│   │   ├── plugins/    # 插件目录：auth users roles plugins dashboard books
│   │   └── legado_rule/# Legado 规则引擎 Python 移植（AnalyzeRule/AnalyzeUrl/WebBook…）
│   └── tests/          # 规则引擎单测 + e2e_smoke.ps1 全链路冒烟脚本
├── frontend/           # Vite + Vue3 + TS + pinia + vue-router + miuix-vue（Miuix/MD3 双设计可切换）
└── docs/spec/          # 从 Kotlin 源码提炼的三份规则语义规格书
    ├── analyzer.md     # AnalyzeRule / 各分析器
    ├── analyze-url.md  # AnalyzeUrl / CustomUrl
    └── source-flow.md  # WebBook 搜索→详情→目录→正文流程
```

## 快速开始

### 后端（Python ≥3.11，已在 3.14 验证）

```powershell
cd viewer/backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- 数据库自动创建于 `backend/data/viewer.db`，并种子三个系统权限组与默认管理员：
  - **admin / view123456**（超级管理员，`*` 权限）
- JS 规则依赖 `dukpy`（自带 QuickJS；Windows/Python 3.14 无 quickjs 官方轮子时的替代）。

### 前端

```powershell
cd viewer/frontend
npm install
npm run build        # 产物输出到 frontend/dist，由后端 SPA 挂载直接服务
npm run dev          # 或开发模式（5173 端口代理 /api → 8000）
```

构建完成后访问 **http://127.0.0.1:8000/** 即为完整站点（登录页 → 书架/搜索/阅读/管理）。

### 一键脚本

双击或命令行运行（均以脚本所在目录为基准）：

| 脚本 | 作用 |
| --- | --- |
| `build.bat` | 首次运行自动创建 venv、pip 安装后端依赖、npm install，然后 vite 构建前端到 `frontend/dist` |
| `start.bat` | 一键启动：确保依赖与 dist 后用 uvicorn 在 **http://127.0.0.1:8000/** 服务整站并自动打开浏览器 |
| `start.bat dev` | 开发模式：同时启动 vite 热更新（5173，/api 代理到 8000）与后端（--reload） |

## 功能一览

| 区块 | 说明 |
| --- | --- |
| 外观 | 设计风格 **Miuix ⇄ Material You** 运行时切换（顶栏调色板按钮 / 「我的 → 外观」），浅色 / 深色 / 跟随系统，选择持久化于 localStorage 且首帧无闪烁 |
| 认证 | 注册、登录（JWT 14 天）、个人资料（昵称/邮箱/简介/头像色相）、改密 |
| 权限组 | 角色 CRUD、按插件聚合的权限目录（`ns.key` 命名空间 + 通配 `ns.*` 与全局 `*`）、用户↔多角色 |
| 用户管理 | 关键字分页搜索、启停、超管开关（自我保护约束）、重置密码、删除 |
| 插件管理 | 列出全部 API 插件并启停（重启生效），仅超级管理员 |
| 仪表盘 | 用户/书源/书架/角色/插件统计 + 最近注册 |
| 书城 | 书源导入（URL 或粘贴 legado JSON）、启停/删除；并发搜索（信号量 6、单源 25s 超时）；详情/目录（含 nextTocUrl 分页）/正文（含 nextContentUrl 合并翻页）；封面代理；书架与阅读进度 |

## 插件架构

每个插件是 `app/plugins/<name>/plugin.py`，支持两种形态（可同时具备）：

**API 插件** —— 暴露 `meta` + `create_router(ctx)`，挂载到 `/api/<mount>`：

```python
meta = {"name": "auth", "mount": "auth", "permissions": [("auth.basic", "基础登录权限")], ...}
def create_router(ctx) -> APIRouter: ...
```

**源规则引擎插件** —— 暴露 `ENGINE` + `create_engine(ctx)`，为书源提供解析能力：

```python
ENGINE = {"key": "legado", "title": "Legado 书源", ...}
def create_engine(ctx) -> LegadoEngine: ...
# 引擎对象需实现异步方法：
#   search_book(src, key, page) / book_info(src, book)
#   get_toc(src, book, toc_url) / get_content(src, book, chapter, ...)
```

内置引擎插件 **engine_legado** 包装了 `app/legado_rule/` 的全部解析实现。
每个书源行记录自己的 `engine` 字段（导入时可用请求参数或源内 `"viewEngine"`
键指定），books 插件按它把搜索/详情/目录/正文分发给对应引擎；
`GET /api/books/engines` 列出所有已注册引擎及书源数量，前端导入对话框中可直接
选择。要接入新的规则体系（私有 JSON 协议、其他 App 的书源格式等），只需新增
一个引擎插件包，无需改动 books 插件。

启动时注册器扫描并挂载 API 插件；`meta.permissions` 聚合成站点权限目录供「权限组」界面勾选。停用状态存于 `plugin_states` 表（引擎插件停用后其解析立即不可用）。

## Legado 规则引擎兼容性

`app/legado_rule/` 是对 Legado Kotlin 分析器的行为级移植：

- `RuleAnalyzer`（首现顶层分隔符优先、括号保护、代码平衡花括号）
- jsoup 选择器方言（`class./tag./id./text.`、迷你关键字、`@` 链、终端提取、`[i:start:end:step]`/`[!...]` 索引）
- JSONPath（jsonpath-ng + 内嵌 `{$.rule}` 替换回退）、XPath（lxml）、Regex 组列表
- `##正则##替换`、`@get:{}`/`{{}}` 模板、`<js></js>` 与 `<js>...</js>` URL 管道、`@js:` 
- `AnalyzeUrl`：POST 表单/JSON 默认头、charset、`<a,b,c>` 页码占位、headers 合并
- WebBook 流程：搜索去重、目录双反转+去重（bug 兼容）、nextTocUrl 单/多链接策略、正文缩进排版

语义依据见 `docs/spec/*.md`（直接从本仓库 `legado-with-MD3-main` 源码整理，未改动该目录）。

## 本地联调夹具

`dev-fixtures/site/` 是一个静态"迷你书站"（含两页目录与跨页正文），配合
`dev-fixtures/sample-source.json` 可在完全离线情况下验证整条链路：

```powershell
python -m http.server 8901 --directory viewer/dev-fixtures/site
# 登录后 → 管理 → 书源管理 → 导入 → URL 填 http://127.0.0.1:8901/sample-source.json
# → 搜索任意关键词即可命中两本书
```

## 测试

```powershell
cd viewer/backend
.\.venv\Scripts\python -m pytest -q      # 32 个测试（规则引擎 27 + 引擎插件架构 5）
.\tests\e2e_smoke.ps1                    # 需先启动 backend(8000) 与夹具站(8901)
```

## 已知限制

- 会话撤销：JWT 无服务端黑名单，禁用/删号后旧 token 至自然过期前仍可用。
- 插件停用需重启后端生效（挂载发生在 import 时）。
- 引擎以"行为兼容"为目标，个别 Kotlin 边角（如部分 JS 桥的 Android 专属方法）为桩实现。
- 封面代理出于简化使用查询串传 token（img 标签无法带 Authorization 头）。
