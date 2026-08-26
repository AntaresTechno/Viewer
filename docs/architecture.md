# Viewer（Antares Viewer）前后端与插件系统总览

> 本文是整个仓库的**一站式架构总结**：后端（FastAPI）、前端（Vue 3）与插件体系。
> 规则语法的逐条语义不在本文范围内，见 [docs/spec/](spec/) 三份规格书：
> [analyzer.md](spec/analyzer.md)（AnalyzeRule/各分析器）、[analyze-url.md](spec/analyze-url.md)（AnalyzeUrl/CustomUrl）、[source-flow.md](spec/source-flow.md)（WebBook 搜索→详情→目录→正文）。

---

## 目录

1. [项目概览](#一项目概览)
2. [后端架构](#二后端架构)
3. [插件系统](#三插件系统)
4. [前端架构](#四前端架构)
5. [前后端协作约定](#五前后端协作约定)
6. [运行 · 测试 · 已知限制](#六运行--测试--已知限制)

---

## 一、项目概览

一个在线阅读站：**插件化 FastAPI 后端** + **Vue 3 SPA 前端**，内置与
[Legado（阅读）](https://github.com/gedoor/legado) 书源规则兼容的 Python 解析引擎。

| 层 | 技术 |
|---|---|
| 后端 | Python ≥3.11、FastAPI、SQLAlchemy 2 (async) + aiosqlite、PyJWT、httpx、lxml/jsonpath-ng、dukpy(QuickJS) |
| 前端 | Vite 6、Vue 3.5、TypeScript、Pinia、vue-router 4、axios、miuix-vue（Miuix/MD3 双设计系统）、motion-v |
| 存储 | 单文件 SQLite：`backend/data/viewer.db`；磁盘缓存 `backend/data/cache/img`（封面图）与本地书库表 |
| 部署 | `npm run build` 产物由后端 SPA 挂载直接服务，单端口 `http://127.0.0.1:8000/` 即整站 |

```
viewer/
├── backend/
│   ├── app/
│   │   ├── core/        # 配置 / 数据库 / 安全(JWT+PBKDF2) / 依赖注入权限
│   │   ├── models/      # 全部 ORM 模型（18 张表）
│   │   ├── plugins/     # 插件注册器 + 10 个内置插件
│   │   ├── services/    # toc队列 / 每日刷新 / 正文缓存·净化·替换 / 图片缓存
│   │   └── legado_rule/ # Legado 规则引擎 Python 移植（AnalyzeRule/WebBook…）
│   └── tests/           # pytest 单测 + e2e_smoke.ps1 全链路冒烟
├── frontend/            # Vue3 SPA（src/api、stores、router、pages、theme）
├── dev-fixtures/        # 离线联调夹具（静态迷你书站 + 示例书源）
└── docs/spec/           # Legado 规则语义规格书（本文的上游依据）
```

---

## 二、后端架构

### 2.1 应用启动流程（`backend/app/main.py`）

`create_app()` 工厂 → 模块底部 `app = create_app()`：

1. **CORS**：放行 `settings.cors_origins + ["*"]` 及 localhost 正则；`allow_credentials=False`。
2. **插件挂载**：构造 `PluginContext(settings)` → `discover_plugins()` 扫描 →
   `enabled_plugin_names()` 读库得启用集 → 只对「启用且有 `create_router`」的插件
   以 `/api/<mount>` 前缀挂载路由（engine-only 插件不挂路由）。
3. **健康检查**：`GET /api/health` 返回 `{status, app, plugins:{名称:是否启用}}`。
4. **SPA 挂载**：`frontend/dist` 存在时，把自定义 `_SPA(StaticFiles)` 挂到 `/`，
   任何 404 回退 `index.html`（支持 history 路由刷新）。
5. **lifespan**：启动时 `init_db()`（建表 + 微迁移 + 种子数据），随后
   `daily_refresh.start()` 启动每日调度协程（目录自动更新 + WebDAV 自动备份）。

### 2.2 配置项（`backend/app/core/config.py`）

pydantic-settings，环境变量前缀 `VIEWER_`（大写字段名），支持 `.env`；
模块导入即实例化全局 `settings` 并创建 `backend/data/`。

| 配置 | 默认值 | 说明 |
|---|---|---|
| `secret_key` | **每次进程启动随机生成** | JWT 密钥；生产必须用环境变量固定，否则重启即全量掉登录 |
| `token_algorithm` / `token_expire_minutes` | HS256 / 20160（14 天） | JWT 签名算法与有效期 |
| `database_url` | `sqlite+aiosqlite:///…/data/viewer.db` | 异步 SQLite |
| `first_admin_username/password` | admin / view123456 | 首个超级管理员（仅库中无用户时种子） |
| `request_timeout` / `default_user_agent` | 15s / Chrome 124 UA | 书源抓取超时与 UA |
| `toc_page_limit` / `content_page_limit` | 40 / 30 | nextTocUrl / nextContentUrl 最大翻页次数 |
| `search_per_source_limit` | 20 | 单书源搜索结果上限 |
| `image_cache_mb` | 300 | 封面图片磁盘缓存 LRU 上限 |
| `replace_regex_timeout` | 5s | 单条净化/替换规则执行超时 |
| `parser_concurrency` / `search_concurrency` | 4 / 6 | 目录·正文多页并行 / 跨源搜索并发 |
| `prefetch_concurrency` / `library_download_concurrency` | 3 / 4 | 阅读预取 / 整本预下载并发 |
| `daily_refresh_enabled/hour/catch_up` | true / 4 / true | 每日刷新开关 / 触发小时（服务器本地时间）/ 重启补跑 |

### 2.3 数据库（`backend/app/core/db.py`）

- 引擎懒加载单例：`create_async_engine(database_url)` + `async_sessionmaker(expire_on_commit=False)`；
  `get_db()` 为 FastAPI 依赖，逐请求提供 `AsyncSession`。
- `init_db()`：`Base.metadata.create_all` 建表 → SQLite「仅加列」微迁移
  （旧库补 `book_sources.engine`、`book_refs.*`、`purify_rules.scope_*`、`shelf_items.updated_at/has_update` 等）→ 种子数据。
- 种子数据：三个系统权限组 **admin(`*`) / user（阅读基础权限集）/ guest(`auth.basic`)**；
  无任何用户时创建默认管理员 **admin / view123456**（超管 + admin 角色）。

### 2.4 认证与鉴权（`core/security.py`、`core/deps.py`）

- **密码**：PBKDF2-HMAC-SHA256，16 字节随机 salt、120,000 次迭代，
  存储格式 `pbkdf2$<iter>$<salt>$<digest>`；校验恒定时间比较。
- **JWT**：payload `{sub:user_id, name, su:is_superuser, iat, exp}`；
  取 token 支持 `Authorization: Bearer` 头与 `?token=` 查询串（后者供 `<img>`/WebSocket 场景）。
- **依赖链**：`get_current_user` → 解码并加载用户（须 `is_active`）→ 聚合 `role_ids`
  指向各角色的 permissions（去重排序），返回 `(user, perms)` 元组。
- **权限判定 `require_perm(key)`**：超管直通 → 含 `*` → 精确匹配 → 命名空间通配
  `<ns>.*` → 否则 403。`require_superuser` 仅校验 `is_superuser`。

### 2.5 数据模型（`backend/app/models/__init__.py`）

| 表 | 用途 / 关键字段 |
|---|---|
| `users` | 用户：username(unique)、password_hash、资料四项、avatar_hue(MD3 色调)、is_superuser、is_active、role_ids(JSON)、last_login_at |
| `roles` | 权限组：name(unique)、permissions(JSON 数组)、is_system |
| `plugin_states` | 插件启停持久化：name(PK) + enabled |
| `book_sources` | 书源：source_url(unique)、source_name/group、enabled、custom_order、raw_json(原始 JSON)、**engine**(解析引擎，默认 legado) |
| `shelf_items` | 书架：user_id+book_url、书目元数据、created_at、updated_at(书源侧新章时间)、has_update |
| `read_progress` | 进度：user_id+book_url → chapter_index/title/offset |
| `book_refs` | 打开过的书籍档案（短链 id → 定位信息 + 冗余最基本信息），支撑 `/book/ref/:id` 与 `/reader?id=` |
| `book_chapters` | 目录缓存（toc 队列写入）：(source_url, book_url) + idx/title/url/base_url/is_volume/is_vip |
| `book_chapter_contents` | 本地书库已下载正文（离线可读不再回源） |
| `book_assets` | 已下载二进制资源（封面/插图 blob） |
| `toc_jobs` | 后台目录任务：queued→running→done/error + chapters 计数 |
| `replace_rules` | 全局替换规则（legado 子集）：pattern/replacement/scope/regex/case_sensitive |
| `purify_packs` / `purify_rules` | 净化规则包及包内规则（FK CASCADE；多 `scope_content/scope_title` 作用域） |
| `purified_contents` | 净化缓存：raw + content + fingerprint(规则指纹) + applied(命中规则名)，一章一条 |
| `reading_stats` | 阅读时长：(user, day, book) 唯一，seconds；首页全部统计由此推导 |
| `webdav_configs` | 每用户 WebDAV 配置：url/username/password_enc(base64 混淆)/directory/auto_backup/上次备份 |
| `app_kv` | 极简 KV（每日任务「上次运行日期」等内部状态） |

### 2.6 服务层（`backend/app/services/`）

| 服务 | 机制要点 |
|---|---|
| `toc_queue.py` | 后台目录抓取：`asyncio.Queue` + **单 worker 串行消费**；同书 queued/running 去重；历史保留最近 800 条。成功后重写 `book_chapters`，批量刷新所有用户的 ShelfItem——**仅当此前已有目录且章节有变化才置 updated_at/has_update**（首抓不算更新），并回填 BookRef 档案 |
| `daily_refresh.py` | 每日调度协程：重启后 45s 补跑当天未执行的轮次，此后每天 `daily_refresh_hour`（默认凌晨 4 点）运行——①书架全部书籍入队目录刷新；②为开启 auto_backup 的用户逐个执行 WebDAV 备份；③写 `app_kv` 运行标记 |
| `content_cache.py` | 正文**内存 LRU（200 条）**，键含 source/url/base/title/next/is_volume；single-flight 合并同键并发；`spawn_prefetch()` 受信号量限流后台预取后续章节 |
| `image_cache.py` | 封面图片磁盘 LRU：`data/cache/img/sha256(url)[:32]`，`.part` 原子落盘，超 `image_cache_mb`(300MB) 从最旧 mtime 逐出；per-key single-flight |
| `content_purify.py` | 净化管线：固定第一步 MD3 内置文本清洗（移植 formatKeepImg）→ 启用包的激活规则按序应用（Python 正则 / 纯文本 / legado `@js:` JS 替换，单条超时保护）→ 结果连同 raw 写 `purified_contents`。指纹一致直返缓存；规则变化用原文**离线重净化**；抓取失败兜底旧净化结果或本地书库 |
| `replace_rules.py` | 全局替换规则的 scope 匹配（换行/`;`/`\|\|` 分隔、`-` 排除、对书名+源名+源 URL 子串匹配）与安全执行（线程内正则 + 超时，坏规则只跳过自身） |

### 2.7 Legado 规则引擎（`backend/app/legado_rule/`）

行为级 Kotlin 移植，供引擎插件包装：

| 模块 | 职责 |
|---|---|
| `rule_analyzer.py` | 规则分词器：顶层 `&&`/`\|\|`/`%%` 切分（括号保护）、`{{…}}` 内嵌规则替换 |
| `analyze_rule.py` | 主求值管线：default/json/xpath/regex/js/@webjs 模式分发与结果组装 |
| `analyze_url.py` | URL 模板展开（`<a,b,c>` 页码占位）、请求选项解析（method/headers/body/charset/retry/proxy）、相对→绝对 URL |
| `analyzer_css.py` | jsoup 迷你选择器方言（编译为 XPath 在 lxml 上求值，LRU 编译缓存） |
| `analyzer_json.py` | JSONPath（jsonpath-ng）+ 容错 JSON 加载 |
| `analyzer_regex.py` | 多段正则链式提取 |
| `analyzer_xpath.py` | XPath 取值器（JsoupXpath 方言子集） |
| `web_book.py` | 搜索/详情/目录/正文四大流程编排（对应 BookList/BookInfo/BookChapterList/BookContent.kt） |
| `js_bridge.py` | JS 引擎桥：优先 quickjs，回退 dukpy；提供镜像 legado JsExtensions 子集的 `java` 对象 |
| `net.py` | httpx 连接池 HTTP 层（keep-alive、charset 探测） |

语义权威依据：`docs/spec/*.md`（从本仓库 legado Kotlin 源码整理）。

---

## 三、插件系统

### 3.1 两种插件形态（`backend/app/plugins/registry.py`）

每个插件是 `app/plugins/<name>/plugin.py`，可声明其一或兼有：

```python
# 形态一：API 插件 → 挂载到 /api/<mount>
meta = {"name": "auth", "mount": "auth", "order": 10,
        "title": "...", "version": "...", "description": "...",
        "permissions": [("auth.basic", "基础登录权限"), ...]}
def create_router(ctx) -> APIRouter: ...

# 形态二：源规则引擎插件 → 注册进引擎表，无 HTTP 端点
ENGINE = {"key": "legado", "title": "Legado 书源", "version": "...", "description": "..."}
def create_engine(ctx) -> LegadoEngine: ...
# 引擎对象需实现 async 方法：search_book / book_info / get_toc / get_content
# （legado 实现另含 explore_kinds / explore_book）
```

- **扫描**：`pkgutil.iter_modules` 遍历包路径，导入 `<name>/plugin.py`；单个插件导入失败只打印告警，不影响其他插件；结果按 `(order, name)` 缓存。
- **PluginContext**：注入共享能力——`settings`、`ctx.engine`（SQLAlchemy 异步引擎，注意与书源引擎同名不同物）、`ctx.session_factory()`。
- **引擎实例**按 key 在 `_INSTANCE_CACHE` 单例化；`get_engine(key)` 缺省/兜底均为 `"legado"`，引擎所属插件停用时抛 `KeyError`。

### 3.2 启停机制（`plugin_states` 表）

- 启动时：启用集合 = 已发现插件 − 库中 `enabled=False` 行；被停用的 API 插件**不挂载路由**（需重启生效）。
- 运行时：toggle 端点写库并同步内存 `_DISABLED_PLUGINS` —— **引擎插件的解析能力立即不可用**；API 插件的路由卸载仍需重启（接口文案注明）。
- `plugin_enabled(name)`：零 DB 开销的活视图，供插件间软委派（books 检查 content_purify、webdav 自动备份检查自身）。

### 3.3 权限目录与 ZIP 安装

- **权限目录**：各插件 `meta.permissions` 经 `all_permission_keys()` 按 `(order, name)` 聚合去重，由 `GET /api/roles/permissions/catalog` 输出扁平 `items` + 按 `ns.` 首段分组的 `grouped`，供「权限组」界面勾选；运行期由 `require_perm` 校验（超管 / `*` / 精确 / `ns.*`）。
- **ZIP 安装**（plugins 插件）：≤50MB / 解压 ≤128MB / 成员 ≤2000、zip-slip 防护；自动推断布局（根目录或唯一顶层目录）、补空 `__init__.py`、失败自动回滚还原；成功后强制重扫插件表。**API 插件需重启挂载，引擎插件即时生效**。

### 3.4 内置插件一览

| 插件 | mount | order | 权限声明 |
|---|---|---|---|
| `engine_legado` | —（纯引擎） | 5 | — |
| `auth` | auth | 10 | auth.basic / auth.register / auth.admin.view |
| `users` | users | 20 | users.read/create/update/delete/reset_password |
| `home` | home | 20 | home.read / home.stats.write |
| `roles` | roles | 21 | roles.read / roles.manage / roles.catalog |
| `plugins_admin` | plugins | 22 | plugins.manage（实际全部端点强制超管） |
| `dashboard` | dashboard | 23 | dashboard.read |
| `books` | books | 30 | books.sources.read/manage、books.search/explore/info/toc/content、books.shelf.read/write、books.progress.write、books.replace.read/manage（12 项） |
| `content_purify` | purify | 36 | purify.read/manage/process、purify.cache.manage |
| `webdav` | webdav | 40 | webdav.use |

### 3.5 各插件要点

#### auth（认证）
注册（赋默认 user 角色、即时签发 JWT）、登录（验密 + 更新 last_login_at + 聚合角色权限）、
`GET /me`（附书架计数）、改资料、改密码（验旧密码，新密码 ≥6 位）。
> 注意：路由实际使用 `get_current_user`，声明的三个 permission key 未参与 require_perm 校验，主要供权限目录展示。

#### users（用户管理）
关键字分页搜索；创建；更新（禁止把自己停用）；删除（不能删自己、至少保留一个超管）；管理员重置密码。

#### roles（权限组）
角色 CRUD（系统内置不可改名/删除，删除时从所有用户摘除）；权限目录接口；每角色用户数统计。

#### dashboard（仪表盘）
用户/书源/书架/角色总数 + 插件启用数 + 最近 5 名注册用户 + 服务器时间。

#### plugins（插件管理，超管）
列表（含 DB 启用状态）；toggle（写库 + 内存同步，停用提示重启生效）；ZIP 安装（见 §3.3）。

#### books（书城，最大插件）
核心端点分组：

| 分组 | 端点（方法省略均为 REST 惯例） |
|---|---|
| 书源 | sources 列表/导入(URL 或粘贴 JSON，逐源 `viewEngine` 优先于请求级 engine)/更新/启停/批量删除/详情与规则快照；`GET engines` 引擎清单+各引擎书源数 |
| 搜索·发现 | `POST search` 多源并发（上限 60 源，Semaphore(search_concurrency)=6，单源 25s 超时，失败归入 errors 不中断，结果去重截断 200 条）；explore 分类与书目 |
| 详情·目录 | info（搜索已知字段作起点合并）、resolve/refs/profile（BookRef 短链档案）、chapters（缓存优先，fallback 可回源落库）、chapters/refresh + toc-status（排队 + 轮询）、toc 实时目录（内存缓存 TTL 1800s） |
| 正文·进度 | content 双路径（净化插件启用走净化管线并同步本地书库，否则本地库优先→LRU 回源→全局替换规则）；content/prefetch 预取（≤N+1 条保证缓存键一致）；progress 读写（追平最新章清除 has_update 徽标） |
| 书架 | shelf 列表（added/updated/read 三种排序）/加入（自动入队目录抓取）/移除/refresh-toc |
| 本地书库 | library 概览、library/download 整本预下载作业（并发可调、断点跳过已有章）、status 进度、DELETE 清除 |
| 封面代理 | `GET cover?url&token`：查询串 token 鉴权（img 无法带请求头）；sha256 落盘 `data/covers/`；UA 取归属书源、Referer 同站根；失败返回灰 SVG 占位 |
| 替换规则 | replace 列表/legado JSON 导入/编辑/启停/删除/test 试跑 |

#### home（首页）
`summary`：最近阅读（进度倒序取 12）+ 今日/累计时长 + 累计天数/在读本数/连续天数 + 有更新书架条目；
`heartbeat`：阅读器每 30s 上报在读秒数（1–300），按「日 × 书」累加进 reading_stats；
`daily`：近 N 天柱状图数据。统计口径全部由 reading_stats 推导，日期取服务器本地时区。

#### webdav（备份）
配置（密码 base64 混淆存储，留空不改、`"-clear"` 清除）、test（PROPFIND 连通性）、
backup（书架+进度+阅读统计打包 JSON 上传，409/404 自动 MKCOL 重试）、backups 列表（前 100）、
restore（合并语义：书架按 book_url 补齐、进度按 updatedAt 新者胜、统计按行取较大 seconds）、删除。
每日自动备份由 daily_refresh 在目录刷新后触发（条件：webdav 插件启用且该用户开启 auto_backup）。

#### content_purify（正文净化，mount=purify）
三来源目录：①`builtin-md3` 内置清洗层（代码实现，管线固定第一步）；②`wuyun` 乌云净化预设（随插件分发 `data/wuyun.json`，一键安装为规则包，默认停用）；③自定义包（粘贴 JSON / URL / 文件三种导入）。
规则包 CRUD + 包内规则 CRUD + test 试跑（返回命中明细与指纹）+ 独立净化阅读入口 `GET content` +
缓存统计/清理/指纹失效。books 的 `/content` 在本插件启用时委托同一管线（§3.6）。

#### engine_legado（Legado 引擎）
纯引擎形态（无路由）。`LegadoEngine` 是 `legado_rule.web_book` 的薄适配器，方法一一转发；
`matches(raw)` 以 JSON 含 `bookSourceUrl` 认定 legado 书源。

### 3.6 引擎分发机制

书源行自带 `engine` 字段（导入时可指定，缺省 legado，必须是已注册 key 否则跳过）。
books 所有需要解析的端点遵循同一序列：
`_load_source_row`（404/400）→ `json.loads(raw_json)` → `_engine_for(row.engine)`
→ 以统一形状调用引擎 async 方法。抓取异常统一转 502（连接失败/解析失败）。

要接入新的规则体系（私有 JSON 协议、其他 App 的书源格式），只需新增一个引擎插件包，
无需改动 books 插件；前端导入对话框经 `GET /api/books/engines` 直接选择目标引擎。

---

## 四、前端架构

### 4.1 技术栈与构建

Vue ^3.5 + Pinia ^2.2 + vue-router ^4.5 + axios + **miuix-vue 0.1.1**（固定版本的 UI 组件库）
+ motion-v（声明但暂未引用）。Vite 6 构建，产物输出 `frontend/dist`；
别名 `@ → src`；dev server 5173 端口并把 `/api` 代理到 `127.0.0.1:8000`。
`src/types/miuix-shim.d.ts` 为 miuix-vue 手写模块声明（其 types 入口损坏）。

### 4.2 入口与双设计系统（`index.html`、`src/main.ts`、`src/stores/theme.ts`）

- **首帧防闪烁**：`index.html` 头部内联脚本在 Vue 挂载前读 localStorage
  `viewer_design`（非 "miuix" 一律按 md3e，兼容历史 "md3"）写入 `<html data-design>`，
  并按 `viewer_theme_mode`（light/dark/system，system 经 matchMedia 解析）加 `.m-theme-dark` 类。
- **双设计切换**：design ∈ `miuix | md3e`、mode ∈ `light | dark | system`，选择持久化于 localStorage；
  `theme/design.css` 以 `html[data-design=…]` 作用域注入两套 token（md3e 为全套 M3 tonal 色板 +
  组件形制覆盖），共享弹簧动效 token（linear() 逼近欠阻尼弹簧，不支持时回退 cubic-bezier）。
  换肤用 View Transitions API 做「从点击处圆形扩散」动画（reduced-motion 直接生效）。
- `main.ts` 还注册全局 `animationstart` 监听修复 Chrome 自动填充不同步 v-model 的问题。

### 4.3 路由（`src/router/index.ts`）

history 模式，页面全部懒加载：

| 路径 | 页面 | 说明 |
|---|---|---|
| `/login` `/register` | Login/Register | 免登录页 |
| `/` → redirect `/shelf` | AppShell 布局 | 下述子路由 |
| `/home` `/shelf` `/search` `/explore` `/library` | Home/Shelf/Search/Explore/LocalLibrary | 首页统计·书架·跨源搜索·发现·本地书库 |
| `/book/:bookUrl`、`/book/ref/:refId` | BookPage | 详情页长链/短链双入口 |
| `/replace` `/purify` `/webdav` `/me` | ReplaceRules/Purify/WebDav/Me | 替换规则·净化·备份·个人中心 |
| `/admin` 及 `users/roles/plugins/sources` | Dashboard/Users/Roles/Plugins/Sources | 管理页 |
| `/reader` | ReaderPage | **独立全屏沉浸式**，不套 AppShell |

守卫只做**登录校验**（未登录 → `/login?next=…`）；权限不设在路由层，
而由 AppShell 导航项的 `show()`（如首页需 `home.read`、管理需 `dashboard.read`）
与页面内操作按钮的 `can(key)` 控制。

### 4.4 状态与 API 客户端

- **auth store**：token/user 直接以 localStorage（`viewer_token`/`viewer_user`）初始化；
  login/register 写回、refreshMe 刷新、logout 清空；`can(key)` 与后端 require_perm 同构
  （超管 / `*` / 精确 / `ns.*` 通配）。
- **api/client.ts**：axios 实例 baseURL `/api`、60s 超时；请求拦截器附加 Bearer；
  响应拦截器 401 时清 token 跳登录页。错误约定 `errMsg()`：取 `detail`（字符串直返、
  FastAPI 校验数组取第一条 msg、否则「网络错误」）。导出约百个类型化方法覆盖全部后端域；
  `coverProxyUrl()` 统一把封面/插图换成后端代理地址（token 放 query）。

### 4.5 页面与布局要点

- **AppShell**：桌面左侧 sticky 侧栏（232px，带滑动高亮滑块），移动端底部毛玻璃标签栏；
  导航项按权限显隐并分「阅读/探索/系统」三组（整组不可见则隐藏）。
- **BookPage**：先以 book_refs/bookProfile 缓存档案秒开再后台拉详情；「目录落后于最新章」
  正则检测，自动排队重抓（≤2 次，3s×20 轮询）；页尾折叠展示书源规则 JSON。
- **ReaderPage**：滑动模式（连续章节窗口上下追加 + 滚动补偿）/ 翻页模式（CSS 多列 +
  translateX，点击两侧/横滑/键盘翻页）双模式；进度 700ms 防抖保存（翻页存 pageIndex、
  滚动存 scrollTop）；**30s 心跳上报在读时长**（需 `home.stats.write` 权限且页面可见，
  卸载补报 ≥10s 余量）；向后预取 N+1 章；`<img>` 正文插图统一重写为 cover 代理。
- 其余：SearchPage 分组/源 chips 圈定搜索范围；ShelfPage 三种排序记忆与状态角标；
  LocalLibraryPage 整本下载 2s 轮询；PurifyPage 三来源卡片式管理；
  RolesPage 编辑权限时整组取消回退写入 `ns.*` 保持一致；PluginsPage 支持上传 .zip 安装。

### 4.6 组件与工具

| 文件 | 职责 |
|---|---|
| `components/AppearancePanel.vue` | 设计风格 × 外观模式两组分段控件 |
| `components/PasswordField.vue` | 手写密码框（miuix 0.1.x 不支持 type=password），复用 `.m-input` 保证双设计一致 |
| `components/LoadingImage.vue` | 占位微光→就绪淡入的统一图片替换件 |
| `components/BookDetailHero.vue` | 详情头（封面浮起+背景虚化、简介缺省补抓回写档案），详情页与阅读器共用 |
| `utils/reader.ts` | openDetail/openReader 统一入口：先 resolveBook 换短链，失败兜底长参数 |
| `utils/cover.ts` | 占位 SVG；封面加载失败先走代理重试一次再定格占位（防请求风暴） |
| `utils/sourceGroups.ts` | legado 自由文本分组拆分与统计 |
| `styles/base.css` | 工具类、入场错帧动画、路由过渡、autofill 配色修正 |

---

## 五、前后端协作约定

1. **认证**：登录/注册返回 `{token, user}`；此后所有请求带 `Authorization: Bearer`；
   唯二例外是封面代理与 WebSocket 场景用 `?token=` 查询串。JWT 有效期 14 天，
   服务端无黑名单（禁用/删号后旧 token 至自然过期前仍可用）。
2. **错误协议**：业务错误一律 FastAPI `HTTPException(detail="中文消息")`；
   前端 errMsg 兼容字符串与 422 校验数组两种形态；抓取类失败统一 502。
3. **开发联调**：vite(5173) 代理 `/api` → uvicorn(8000)；生产模式下后端直接服务
   `frontend/dist`（SPA fallback 到 index.html），单端口部署。
4. **长任务模式**：目录抓取、整本下载等耗时操作均为「POST 排队 → 前端轮询状态」
   （toc-status / library/download/status），避免长连接依赖。
5. **缓存键一致性**：正文缓存键包含 base/title/next/is_volume 等全部参数，
   预取接口与阅读接口传参必须完全一致才能命中（前端预取固定 N+1 条带 next 指针）。

## 六、运行 · 测试 · 已知限制

```powershell
# 后端
cd backend && python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000

# 前端（构建后由后端服务；或 npm run dev 走 5173 代理）
cd frontend && npm install && npm run build

# 一键脚本（仓库根）：build.bat 构建依赖+产物；start.bat 启动并开浏览器；start.bat dev 双端热更
# 测试
cd backend && .\.venv\Scripts\python -m pytest -q
.\tests\e2e_smoke.ps1   # 需先起 backend(8000) 与夹具站(8901)：python -m http.server 8901 --directory dev-fixtures/site
```

已知限制（详见根 README）：JWT 无服务端撤销；插件停用需重启后端才卸载路由；
个别 Android 专属 JS 桥方法为桩实现；封面代理以查询串传 token；WebDAV 密码为 base64
混淆存储（可还原）而非加密保管；`secret_key` 未通过环境变量固定时每次重启随机生成、全体会话失效。
