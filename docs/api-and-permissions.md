# API、认证与权限

## 接口入口

后端默认监听 `8000`，业务接口位于 `/api`。运行中的准确接口、请求模型和响应模型以 FastAPI 自动生成页面为准：

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/openapi.json
```

公开健康检查：

```http
GET /api/health
```

## 认证

登录：

```http
POST /api/auth/login
Content-Type: application/json

{"username":"admin","password":"..."}
```

受保护请求使用：

```http
Authorization: Bearer <token>
```

部分图片、WebSocket 或协议场景允许 `?token=`，普通 API 客户端应优先使用 Authorization 请求头，避免令牌进入 URL、日志和浏览器历史。

密码以 PBKDF2-HMAC-SHA256 和随机盐保存。JWT 包含用户标识、用户名、超级管理员标志、签发与过期时间；服务端仍会读取数据库确认用户存在且处于启用状态。

## 权限模型

用户可以关联多个权限组，权限取并集。授权顺序为：

1. 超级管理员直接通过。
2. 全局 `*` 通过。
3. 精确权限键通过。
4. 命名空间通配符通过，例如 `books.*`。
5. 其他情况返回 403。

权限键由组件的 `PLUGIN.permissions` 注册。常用命名空间：

| 命名空间 | 能力 |
| --- | --- |
| `auth.*` | 认证、注册与资料 |
| `home.*` | 首页统计与阅读心跳 |
| `users.*` | 用户管理 |
| `roles.*` | 权限组与权限目录 |
| `plugins.*` | 插件管理 |
| `books.*` | 书源、搜索、阅读、书架和净化规则入口 |
| `rss.*` | 订阅与文章 |
| `purify.*` | 正文净化与缓存 |
| `media.*` | 媒体源、媒体库和进度 |
| `webdav.*` | WebDAV 备份与同步 |
| `protocol_bridge.*` | 协议解析与系统注册 |
| `legado.*` | Legado 来源登录 |
| `js.*` | QuickJS 状态 |

插件管理、插件 UI、ZIP 安装和系统协议注册还要求超级管理员，而不只是一项普通权限。

## 接口分组

| 前缀 | 内容 |
| --- | --- |
| `/api/auth` | 登录、注册、当前用户、资料和密码 |
| `/api/home` | 首页摘要、每日统计和阅读心跳 |
| `/api/users`、`/api/roles` | 用户和权限组管理 |
| `/api/plugins` | 组件列表、启停、ZIP 安装和插件 UI |
| `/api/books` | 书源、搜索、发现、详情、目录、正文、书架、本地库和进度 |
| `/api/rss` | 订阅源、分类、文章、已读和收藏 |
| `/api/media` | 媒体源、目录、媒体库、单元解析与进度 |
| `/api/purify` | 净化规则包、测试和内容缓存 |
| `/api/webdav` | WebDAV 客户端、服务端和 Legado 同步 |
| `/api/legado` | Legado 来源登录流程 |
| `/api/protocol-bridge` | 协议处理器、解析和系统注册 |
| `/api/protocol-legado` | Legado 协议检查，以及书源、订阅源和媒体源的自动分类导入 |
| `/api/js` | QuickJS 运行时状态 |

WebDAV 服务端另有站点根路径 `/dav`，它使用自己的认证和协议语义。

## 状态码约定

- `200`：查询或更新成功。
- `201`：导入或创建成功。
- `400`：输入、来源内容、规则或外部请求不符合要求。
- `401`：未登录或令牌失效。
- `403`：账号有效但缺少权限。
- `404`：资源、插件或路由不存在。
- `409`：当前状态冲突，例如停用插件后尝试打开其 UI。
- `500`：服务端内部错误；响应不应暴露密码、令牌或本地路径。

## 调试示例

```bash
curl -X POST http://127.0.0.1:8000/api/protocol-bridge/resolve \
  -H "Authorization: Bearer $VIEWER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"uri":"legado://import/bookSource?src=https%3A%2F%2Fexample.com%2Fsources.json"}'
```

协议解析只返回待确认动作。真正导入时还会在执行端点重新验证来源地址，并根据内容动态检查权限：普通书源需要 `books.sources.manage`，普通订阅源需要 `rss.manage`，漫画、音频和视频源需要 `media.sources.manage`。混合文件需要同时拥有其中实际涉及的权限。
