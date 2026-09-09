# 配置参考

后端使用 Pydantic Settings。所有设置都可通过带 `VIEWER_` 前缀的环境变量覆盖；按仓库脚本从 `backend` 启动时，可将变量写入 `backend/.env`。

## 推荐的生产配置

```dotenv
VIEWER_SECRET_KEY=替换为长期固定的高强度随机值
VIEWER_FIRST_ADMIN_USERNAME=admin
VIEWER_FIRST_ADMIN_PASSWORD=替换为首次初始化密码
VIEWER_DATABASE_URL=sqlite+aiosqlite:///./data/viewer.db
VIEWER_TOKEN_EXPIRE_MINUTES=20160
VIEWER_REQUEST_TIMEOUT=15
VIEWER_JS_AJAX_TIMEOUT=45
```

`VIEWER_SECRET_KEY` 未设置时每次进程启动都会生成新值，因此已有 JWT 会全部失效。生产部署必须固定该值并限制 `.env` 的读取权限。

## 通用设置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VIEWER_APP_NAME` | `Viewer` | 应用名称 |
| `VIEWER_SECRET_KEY` | 每次启动随机生成 | JWT 签名密钥 |
| `VIEWER_TOKEN_ALGORITHM` | `HS256` | JWT 算法 |
| `VIEWER_TOKEN_EXPIRE_MINUTES` | `20160` | 登录令牌有效期，单位分钟 |
| `VIEWER_DATABASE_URL` | `backend/data/viewer.db` 对应的异步 SQLite URL | SQLAlchemy 异步数据库地址 |
| `VIEWER_FIRST_ADMIN_USERNAME` | `admin` | 空数据库的首个管理员用户名 |
| `VIEWER_FIRST_ADMIN_PASSWORD` | `view123456` | 空数据库的首个管理员密码 |
| `VIEWER_REQUEST_TIMEOUT` | `15` | 常规网络请求超时，单位秒 |
| `VIEWER_JS_AJAX_TIMEOUT` | `45` | 书源 JavaScript 网络桥超时，单位秒 |
| `VIEWER_DEFAULT_USER_AGENT` | Chrome 风格 UA | 远程书源请求默认 User-Agent |

## 解析与并发

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VIEWER_TOC_PAGE_LIMIT` | `40` | 目录连续翻页上限 |
| `VIEWER_CONTENT_PAGE_LIMIT` | `30` | 正文连续翻页上限 |
| `VIEWER_SEARCH_PER_SOURCE_LIMIT` | `20` | 每个书源的搜索结果上限 |
| `VIEWER_PARSER_CONCURRENCY` | `4` | 解析器多页并发上限 |
| `VIEWER_SEARCH_CONCURRENCY` | `6` | 跨书源搜索并发数 |
| `VIEWER_PREFETCH_CONCURRENCY` | `3` | 阅读器正文预取并发数 |
| `VIEWER_LIBRARY_DOWNLOAD_CONCURRENCY` | `4` | 整书下载章节并发数 |
| `VIEWER_REPLACE_REGEX_TIMEOUT` | `5` | 单条净化正则超时，单位秒 |
| `VIEWER_IMAGE_CACHE_MB` | `300` | 图片磁盘缓存容量上限，单位 MiB |

并发值过高会提高远端站点压力，也可能触发限流；优先逐项调整并观察日志。

## 定时刷新

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VIEWER_DAILY_REFRESH_ENABLED` | `true` | 是否启动每日刷新任务 |
| `VIEWER_DAILY_REFRESH_HOUR` | `4` | 按服务器本地时间执行的小时，范围会收敛到 0–23 |
| `VIEWER_DAILY_REFRESH_CATCH_UP` | `true` | 当天尚未执行时，启动后补跑一次 |

任务会刷新书架目录与媒体库，并为开启自动备份的用户执行 WebDAV 备份。最后运行日期保存在数据库的 `app_kv` 表中。

## 列表配置

列表类型使用 JSON：

```dotenv
VIEWER_CORS_ORIGINS=["https://reader.example.com"]
VIEWER_SESSION_SSO_GROUPS=[["snssdk.com","fanqienovel.com"]]
```

`VIEWER_SESSION_SSO_GROUPS` 用于把同一会话 Cookie 镜像到同组域名。清空为 `[]` 可关闭全局镜像；书源也可以用自身扩展字段覆盖。
