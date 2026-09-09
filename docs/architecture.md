# 系统架构

## 组成

Viewer 由四个主要层次组成：

| 层次 | 位置 | 职责 |
| --- | --- | --- |
| Web 前端 | `frontend/src` | Vue 路由、页面、状态、API 客户端和阅读器 |
| HTTP 应用 | `backend/app/main.py` | FastAPI 生命周期、中间件、插件路由和静态前端托管 |
| 组件系统 | `backend/app/plugins` | 核心模块、可选插件、规则引擎和插件自带 UI |
| 数据与服务 | `backend/app/models`、`backend/app/services` | SQLAlchemy 模型、导入、缓存、队列、净化和定时任务 |

生产模式下，浏览器只连接 FastAPI。开发模式下，浏览器连接 Vite，Vite 将 `/api` 转发给 FastAPI。

## 请求流

```text
浏览器
  ├─ 页面与静态资源 ───────> Vite（开发）或 FastAPI StaticFiles（生产）
  └─ /api/* + Bearer JWT ─> FastAPI
                              ├─ 核心组件路由
                              ├─ 可选插件路由
                              ├─ 规则引擎
                              └─ SQLAlchemy / 远程来源 / WebDAV
```

未匹配的生产页面路径会回退到 `frontend/dist/index.html`，未匹配的 `/api` 路径始终返回接口 404，不会误回退成 HTML。

## 组件发现与装配

注册器扫描 `backend/app/plugins/*/plugin.py`，读取 `PLUGIN` 声明，并按依赖关系排序。组件分为：

- `core`：应用基础能力，始终启用且不能从管理界面关闭。
- `plugin`：可选业务能力，可以启停和通过 ZIP 安装。
- `engine`：规则解析引擎，提供 `ENGINE` 与 `create_engine`。

启动阶段读取 `plugin_states`，过滤停用组件和缺少前置的组件，然后将每个 `create_router` 挂载到 `/api/<mount>`。规则引擎按 key 延迟创建并缓存实例。

组件启停状态会立即影响注册器判断，但 HTTP 路由只在应用创建时装配，因此启停带 API 的组件后应重启后端。

## 前端结构

Vue Router 提供书架、首页、搜索、发现、订阅、本地库、媒体、阅读器和管理页面。Pinia 保存认证与主题等跨页面状态；Axios 客户端统一使用 `/api` 并添加 Bearer Token。

插件管理界面采用一个固定宿主页。插件只声明自己的 HTML 入口，主前端根据元数据出现“打开界面”按钮，不需要导入插件专用 Vue 组件。

## 数据层

默认数据库是异步 SQLite。启动时执行 `create_all`，并对少量历史字段执行仅新增列的迁移。主要数据域包括：

- 用户、权限组和插件状态。
- 书源、书籍档案、书架、目录、章节内容和阅读进度。
- RSS 来源、文章、已读状态和收藏。
- 净化规则包、规则和净化缓存。
- WebDAV 配置、DAV 资源与应用键值状态。
- 媒体源、媒体库、媒体单元和播放/阅读进度。

个人状态通过 `user_id` 隔离；来源元数据和部分抓取缓存由所有用户共享。

## 后台任务

应用生命周期会启动每日刷新服务。它按配置时间：

1. 为书架中的书创建目录刷新任务。
2. 刷新媒体库。
3. 执行已开启的 WebDAV 自动备份。
4. 记录当天完成状态，避免重复运行。

目录刷新和整书下载使用有界并发或队列，相关上限由环境变量控制。
