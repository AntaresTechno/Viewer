# Antares Viewer 媒体库完整实施方案

> 交付对象：DeepSeek V4  
> 方案日期：2026-09-07  
> 本文件只描述实施方案；除本文件外，本轮不修改任何代码、配置或数据。

## 1. 最终目标

在现有“书架”下方增加一个一级入口“媒体库”。媒体库不是把小说书架改个名字，而是一个独立、可扩展、按用户隔离的媒体收藏与阅览系统。

- 桌面左侧栏顺序固定为：`书架` → `媒体库`。
- “媒体库”一级菜单可点击，进入 `/media`，展示总览、三个分类统计和最近阅览情况。
- “媒体库”下有三个下级菜单，顺序固定为：`漫画` → `音频` → `视频`。
- 三类分别进入 `/media/comic`、`/media/audio`、`/media/video`。
- 媒体条目支持加入/移出媒体库、详情、分集/章节、继续阅览、最近阅览、进度同步、更新提示和排序。
- 漫画使用图片阅读器，音频使用原生音频播放器，视频优先使用原生视频播放器。
- 必须让仓库根目录现有的 `fqdj0512.json` 能作为“视频源”导入和使用；不能要求用户先手工改 JSON。
- 原有小说书架、阅读器、订阅、WebDAV 和权限行为必须保持兼容。

## 2. 已核实的现状与关键结论

### 2.1 当前“书架”链路

现有书架由以下内容组成：

- 前端导航：`frontend/src/layouts/AppShell.vue`
- 路由：`frontend/src/router/index.ts`
- 页面：`frontend/src/pages/ShelfPage.vue`、`BookPage.vue`、`ReaderPage.vue`
- API 类型与客户端：`frontend/src/api/client.ts`
- 后端书籍插件：`backend/app/plugins/books/plugin.py`
- 数据模型：`ShelfItem`、`ReadProgress`、`BookRef`、`BookChapter`
- 后台目录队列和每日刷新：`backend/app/services/toc_queue.py`、`daily_refresh.py`

这些模型和接口都深度使用“书、章、正文、字符偏移”等小说概念。直接在 `ShelfItem` 上加一个媒体类型会导致：

- 视频的秒数进度被迫塞进正文 offset；
- 漫画页码、音频时长、视频分集无法清晰表达；
- 目录刷新、正文缓存、净化、WebDAV Legado 同步互相污染；
- 后续播放器和代理逻辑继续堆在 books 插件里。

因此媒体库必须建立独立领域模型，但可以复用已有 Legado 分析器、封面组件、卡片样式和部分 RSS 列表解析能力。

### 2.2 `fqdj0512.json` 的真实类型

该文件不是常规 Legado `BookSource`，而是 Legado `RssSource`，判断依据是它使用了：

- `sourceUrl` / `sourceName`
- `sortUrl`
- `ruleArticles`
- `ruleTitle` / `ruleImage` / `ruleLink`
- `ruleContent`
- `articleStyle`

项目当前已能通过“订阅源”导入这类 JSON，并能用 `backend/app/plugins/rss/parser.py` 拉取列表。

必须注意两个容易误判的字段：

- `articleStyle: 2` 只表示“双列列表布局”，不是视频类型。
- Legado `RssSource.type` 才是内容类型：`0=网页，1=图片，2=视频`。`fqdj0512.json` 没有 `type`，按 Legado 默认值会被当作普通网页。

### 2.3 当前为什么不能播放番茄短剧

`fqdj0512.json` 的 `ruleContent` 做了两件事：

1. 从 `/catalog?book_id=...` 响应中取得 `data.item_data_list[*].item_id`；
2. 拼出一整页包含 `<video>`、选集按钮和脚本的自定义 HTML 播放器，再请求 `{{host}}/video?item_ids=...`，从 `video_model` 解出真正 MP4 地址。

当前 RSS 正文接口会调用 `safe_html()`，明确删除 `script`、`style`、`button`、`iframe` 等标签，而且允许标签中没有 `video`。所以现状只能看到被清洗后的空壳，播放器不可能工作。

同时，直接把这段 HTML 放进 Vue 的 `v-html` 也不可接受：它会让管理员导入的任意脚本运行在主站上下文中，可读取 JWT、本地存储和页面数据。

### 2.4 `fqdj0512.json` 缺失或不适合媒体库的能力

DeepSeek V4 实现时必须逐项处理，而不是只把其 HTML 原样显示：

1. 缺少 `type: 2` 或明确的 `mediaType: "video"`。
2. “搜索”被放在 `sortUrl` 的一个分类项中，没有标准 `searchUrl`，所以现有 `hasSearch` 会是 false。
3. 简介被写入 `rulePubDate` 并通过 `java.put("desc", result)` 暂存，没有独立 `ruleDescription`。
4. 没有创作者、标签、状态、总集数、最新集等结构化元数据规则。
5. 没有结构化的分集规则；分集 ID 隐藏在 `ruleContent` 的首段 JS 中。
6. 没有结构化的播放地址、清晰度、格式、请求头、Referer、Cookie、字幕、过期时间规则。
7. 播放进度仅保存在该 HTML 自己的 localStorage 中，不能跨浏览器、跨设备或出现在媒体库总览。
8. 没有统一的更新检测规则；`ruleNextPage` 只递增 offset，没有显式终止条件。
9. `jsLib` 依赖一个明文 HTTP IP 服务，既有单点失效风险，也可能在 HTTPS 部署时触发混合内容限制。
10. 自定义播放器只适用于视频，无法作为漫画和音频的通用抽象。

结论：需要“结构化媒体规则 + 受隔离的旧式 HTML 播放器兼容层”双轨方案。原文件通过兼容层立即可用，新媒体源应优先使用结构化规则。

## 3. 设计原则与明确边界

### 3.1 必须遵守

- 不改动 `fqdj0512.json` 原文件；导入时做类型识别和运行时归一化。
- 不把媒体脚本塞进小说 `ReaderPage.vue`。
- 不复用 `ShelfItem` 和 `ReadProgress` 存媒体数据。
- 不在主页面用 `v-html` 执行媒体源返回的脚本。
- 不把长期 JWT 直接写入视频、音频或漫画资源 URL。
- 不把可能很快过期的真实流地址长期写进数据库。
- 所有库条目、进度、最近阅览必须按用户隔离；所有按 ID 的接口必须校验归属。
- 现有 RSS 文章清洗策略保持不变，不能为了番茄短剧放开 `safe_html()`。

### 3.2 第一版不要求

- 不做视频转码、音频转码或 DRM 破解。
- 不承诺播放浏览器本身不支持的编码；应给出清楚错误和“在原网页打开”兜底。
- 不做离线下载整部媒体；先把在线浏览、收藏、播放/阅读和同步进度做完整。
- 不把媒体条目同步到 Legado 的小说 `bookshelf.json`；两者语义不同。

## 4. 总体架构

新增独立后端插件 `media`，挂载到 `/api/media`。插件内部包含三个层次：

1. **源适配层**：识别并运行 RSS Source、Book Source 两种 Legado 源格式。
2. **媒体领域层**：统一输出目录项、媒体条目、分集/章节和可播放内容。
3. **接口层**：媒体源管理、发现/搜索、媒体库 CRUD、进度、总览、更新和资源代理。

推荐目录：

```text
backend/app/plugins/media/
├── __init__.py
├── plugin.py                 # PLUGIN、Pydantic DTO、API 路由
├── schemas.py                # MediaKind 与规范化返回结构
├── service.py                # 媒体库、详情、进度、总览业务
├── source_service.py         # 导入识别、源 CRUD、归一化
├── adapters/
│   ├── base.py               # MediaSourceAdapter 协议
│   ├── rss.py                # RssSource 适配器，fqdj 走这里
│   └── book.py               # BookSourceType 音频/图片适配器
├── legacy_document.py        # 旧式 HTML 播放页隔离与桥接
├── tickets.py                # 短期资源票据
└── proxy.py                  # Range 流式代理与上游头处理

backend/app/services/
└── media_refresh.py          # 独立的媒体目录更新任务
```

不要让 media 插件直接依赖“rss 插件必须启用”。应把以下纯解析能力从 `backend/app/plugins/rss/parser.py` 抽到共享的 `backend/app/legado_rule/rss_source.py`：

- `runtime_source`
- `parse_sorts`
- `fetch_articles`
- 规则正文求值的未清洗版本

RSS 插件继续调用共享解析器并在自己的边界执行 `safe_html()`；media 插件调用未清洗求值，但只把结果交给隔离文档处理器。重构前后的 RSS API 输出必须由原测试保证不变。

## 5. 统一媒体协议

### 5.1 媒体类型

后端、数据库和前端统一只使用以下字符串枚举：

```text
comic | audio | video
```

不要在领域层继续传播 Legado 的数字类型。数字只在导入适配器中转换。

### 5.2 适配器接口

`MediaSourceAdapter` 至少提供：

```text
list_sorts(source) -> MediaSort[]
list_items(source, sort, page) -> MediaCatalogPage
search_items(source, keyword, page) -> MediaCatalogPage
get_detail(source, item_locator) -> MediaCatalogItem
get_units(source, item_locator, refresh=False) -> MediaUnit[]
resolve_unit(source, item, unit) -> ResolvedMedia
render_legacy_document(source, item) -> LegacyDocument | None
```

统一 DTO 至少包含：

```text
MediaCatalogItem:
  itemKey, itemUrl, title, creator, coverUrl, intro,
  latestUnit, totalUnits, tags, sourceId, sourceName, mediaKind

MediaUnit:
  unitKey, index, title, locator, durationMs?, publishedAt?, locked?

ResolvedMedia(video/audio):
  kind, streams[{url, quality, mime, headers, expiresAt?}],
  subtitles[], posterUrl?, lyrics?

ResolvedMedia(comic):
  kind, images[{url, headers}], readingDirection?, nextUnitKey?
```

真实上游 URL 只在 `resolve_unit` 的内存结果中短暂存在。返回前端时换成本站短期票据 URL。

## 6. 媒体源格式与导入识别

### 6.1 支持两种 Legado 源方言

**RssSource 方言**：有 `sourceUrl`，并通常有 `ruleArticles` / `sortUrl`。用于 `fqdj0512.json` 和其他网页、图片、视频订阅源。

**BookSource 方言**：有 `bookSourceUrl`，并通常有 `ruleSearch` / `ruleBookInfo` / `ruleToc` / `ruleContent`。用于 Legado 音频书源和图片/漫画书源。

导入时记录 `source_format = rss | book`，原始 JSON 必须无损存储。

### 6.2 类型判断优先级

按以下严格顺序归一化为 `media_kind`：

1. 导入请求中管理员显式选择的 `mediaKind`；
2. 源内新增的字符串 `mediaType`；
3. RssSource 的 `type`：`1 -> comic`、`2 -> video`，扩展值 `3 -> audio`；
4. BookSource 的 `bookSourceType`：`1 -> audio`、`2 -> comic`；
5. 仅在前三者缺失时启用启发式：`ruleContent` 包含 `<video` 判为 video，包含 `<audio` 判为 audio，以大量图片规则为主判为 comic；
6. 仍不能确定时拒绝静默导入，返回 `needsKind=true`，由管理页让用户选择。

`fqdj0512.json` 会在第 5 步被识别为 video，同时返回警告：“源未声明 type，已根据 ruleContent 识别为视频”。数据库保存归一化类型，但导出时仍保留原始 JSON，不私自写入 `type`。

### 6.3 新增的结构化媒体规则

在不破坏 Legado 原字段的前提下，允许源增加 `mediaRules`：

```json
{
  "mediaType": "video",
  "mediaRules": {
    "detail": {
      "creator": "",
      "intro": "",
      "tags": "",
      "latestUnit": "",
      "totalUnits": ""
    },
    "units": {
      "requestUrl": "",
      "list": "",
      "key": "",
      "title": "",
      "url": "",
      "duration": "",
      "nextPage": ""
    },
    "content": {
      "requestUrl": "",
      "streamList": "",
      "streamUrl": "",
      "quality": "",
      "mime": "",
      "headers": "",
      "subtitleUrl": "",
      "imageList": "",
      "imageUrl": "",
      "lyrics": ""
    }
  }
}
```

规则仍由现有 `AnalyzeUrl` / `AnalyzeRule` / QuickJS 执行。运行上下文中明确提供 `source`、`item`、`unit`、`result`、`baseUrl`，避免源作者再把所有状态藏进一整页 HTML。

结构化规则优先；不存在时才走旧式 HTML 兼容层。实现时为番茄短剧做一份测试用的“等价结构化 fixture”，验证其目录 ID 和 MP4 解码逻辑，但不要覆盖根目录原文件。

## 7. `fqdj0512.json` 兼容方案

### 7.1 列表阶段

复用 RssSource 解析逻辑：

- `sortUrl` 生成热剧、新剧、逆袭、总裁等分类；
- `ruleArticles` 取每个短剧条目；
- `ruleTitle`、`ruleImage`、`ruleLink` 分别形成标题、封面和 itemUrl；
- 当前 `rulePubDate` 的结果可作为 intro 兜底，因为该源实际把简介放在这里；UI 不应把它显示为日期。

“搜索”分类应做兼容转换：当一个 sort 名为“搜索”且 URL 中出现查询模板，而源没有 `searchUrl` 时，将它识别为兼容搜索模板，不在普通分类列表中重复显示，并让媒体发现页出现搜索框。

### 7.2 旧式 HTML 播放阶段

为了让原文件无需修改即可使用，新增 `legacy document` 模式：

1. 后端获取 itemUrl，并执行原 `ruleContent`，得到完整 HTML。
2. 绝不能经 RSS 的 `safe_html()`，也绝不能注入主页面。
3. 前端仅通过 `<iframe srcdoc>` 显示，sandbox 最小权限为 `allow-scripts allow-presentation`，不要加 `allow-same-origin`、`allow-top-navigation` 或弹窗权限；需要全屏时单独启用 `allowfullscreen`。
4. 在源 HTML 之前注入本站控制的 bootstrap：
   - 提供容量受限的内存 localStorage 替身；
   - 监听 localStorage 的 get/set/remove，并通过 `postMessage` 上报；
   - 监听 iframe 内 `video` / `audio` 的 play、pause、timeupdate、ended、durationchange；
   - 上报当前媒体 URL、秒数、时长及源写入的“当前集”键值；
   - 接收父页面下发的上次 legacy 状态，以恢复原播放器自己的进度。
5. 父页面只接受 `event.source === iframe.contentWindow` 且符合固定 schema 的消息；忽略源 HTML 自造的其他消息。
6. legacy 状态保存到进度记录的 `legacy_state_json`，限制总大小（建议 16 KiB）、键数量和单值长度，禁止把任意大对象写进数据库。
7. iframe 不接触 JWT；父页面通过正常 Axios 请求保存进度。

这样既能跑原短剧播放器，又把它与主站身份数据隔离。此模式是兼容兜底，不是新媒体源的推荐写法。

### 7.3 更新检测

旧式源没有结构化分集时，后台重新执行 `ruleContent`，对“规则求值的业务结果”计算稳定指纹。至少去除时间戳等明显易变部分；若能识别首段 JS 返回的数组，则直接对分集 ID 数组计算 hash 和数量。指纹变化时更新 `content_updated_at` 并设置 `has_update=true`。

不能简单对整份 HTML 做 hash，因为静态模板、会话时间或随机参数可能造成假更新。

## 8. 数据模型

在 `backend/app/models/__init__.py` 新增以下表。所有唯一约束必须显式声明，避免并发重复加入。

### 8.1 `media_sources`

- `id`
- `source_key`：原 `sourceUrl` 或 `bookSourceUrl`
- `source_name`、`source_icon`、`source_group`、`source_comment`
- `source_format`：`rss | book`
- `media_kind`：`comic | audio | video`
- `enabled`、`custom_order`
- `raw_json`：无损保存
- `created_at`、`updated_at`
- 唯一约束：`(source_format, source_key)`

### 8.2 `media_library_items`

- `id`、`user_id`、`source_id`
- `media_kind`
- `item_key`、`item_url`
- `title`、`creator`、`cover_url`、`intro`、`tags_json`
- `latest_unit`、`total_units`
- `content_fingerprint`
- `created_at`、`content_updated_at`、`has_update`
- 唯一约束：`(user_id, source_id, item_key)`

`item_key` 优先使用源规则给出的稳定 ID；没有时才使用规范化后的 itemUrl。不要只用标题去重。

### 8.3 `media_units`

- `id`、`source_id`、`item_key`
- `unit_key`、`unit_index`、`title`
- `locator`：供源适配器再次解析的逻辑地址或 ID
- `duration_ms`、`published_at`、`locked`
- `updated_at`
- 唯一约束：`(source_id, item_key, unit_key)`

这里只缓存稳定定位信息，不保存短时效 MP4/M3U8/音频签名地址。

### 8.4 `media_progress`

- `id`、`user_id`、`library_item_id`
- `unit_key`、`unit_index`、`unit_title`
- `position_ms`、`duration_ms`
- `page_index`、`page_count`（漫画使用）
- `completed`
- `legacy_state_json`
- `updated_at`
- 唯一约束：`(user_id, library_item_id)`

媒体库的“最近阅览”直接按 `media_progress.updated_at DESC` 取得。移出媒体库时，同一事务删除对应进度；共享的 `media_units` 不立即删除，可由以后维护任务清理。

### 8.5 迁移策略

项目目前使用 `create_all + _migrate_sqlite`。新表可由 `create_all` 创建，但必须补一个迁移测试：用旧版表结构启动后，新表可创建且原数据不变。若实施过程中给既有表加列，继续使用幂等的 `PRAGMA table_info` 加法迁移，禁止重建或清空旧表。

## 9. 后端 API 合约

所有响应字段统一 camelCase，枚举只返回字符串。

### 9.1 媒体源管理

```text
GET    /api/media/sources?kind=
POST   /api/media/sources/import
POST   /api/media/sources/{id}/toggle
POST   /api/media/sources/delete
GET    /api/media/sources/export?ids=
```

导入请求支持 `{data, url, mediaKind?}`，响应包含 `{added, updated, skipped, warnings, needsKind}`。导入 URL 仍由后端抓取。

### 9.2 发现、分类和搜索

```text
GET /api/media/catalog/sorts?sourceId=
GET /api/media/catalog?sourceId=&sortName=&sortUrl=&page=
GET /api/media/catalog/search?sourceId=&keyword=&page=
GET /api/media/catalog/detail?sourceId=&itemKey=&itemUrl=
```

分页响应统一为 `{items, nextUrl, warning}`。单个源失败时显示该源错误，不应让整个页面无响应。

### 9.3 媒体库与总览

```text
GET    /api/media/overview
GET    /api/media/library?kind=&sort=added|updated|viewed&order=asc|desc
POST   /api/media/library
GET    /api/media/library/{id}
DELETE /api/media/library/{id}
POST   /api/media/library/{id}/refresh
GET    /api/media/library/{id}/units
```

`overview` 返回：

- `counts: {comic, audio, video, total}`
- `recent[]`：最近阅览，包含进度和继续入口
- `updates[]`：最近有更新
- `recentlyAdded[]`

`POST /library` 必须幂等；重复加入返回同一 ID 和 `existed=true`。

### 9.4 解析、票据和资源代理

```text
POST /api/media/library/{id}/units/{unitKey}/resolve
GET  /api/media/resource/{ticket}
POST /api/media/library/{id}/legacy-document
```

- `resolve` 需 Bearer 鉴权，并返回 2～5 分钟有效的本站资源 URL。
- `ticket` 至少绑定 userId、libraryItemId、unitKey、资源序号、过期时间和随机 nonce，并用服务端密钥签名。
- `/resource/{ticket}` 不依赖 Authorization 请求头，方便 `<video>`、`<audio>`、`<img>` 直接使用；验证票据后仍需再次检查用户和条目存在。
- 代理必须支持并正确转发 `Range`、`If-Range`，返回 `206`、`Content-Range`、`Accept-Ranges`、`Content-Length` 和上游 `Content-Type`，用 `StreamingResponse` 分块发送，禁止把整部视频读入内存。
- 上游 401/403 且地址可能过期时，只允许重新 resolve 一次，避免循环。

### 9.5 进度

```text
GET /api/media/library/{id}/progress
PUT /api/media/library/{id}/progress
```

请求体按媒体类型校验：

- 视频/音频：`unitKey, unitIndex, unitTitle, positionMs, durationMs, completed`
- 漫画：再允许 `pageIndex, pageCount`
- legacy：再允许受限的 `legacyState`

前端播放时每 10～15 秒节流保存一次，并在 pause、ended、切集、路由离开和页面隐藏时补保存。到达 95% 或 ended 视为完成。进度到达当前最新一集/章后清除 `has_update`。

## 10. 资源代理安全要求

这是媒体功能最容易被忽略、风险最高的部分，必须和页面一起交付：

- 只允许 `http` / `https`，拒绝 `file:`、`data:`、`ftp:` 等资源地址；data 图片仅能在前端已有安全分支中使用。
- 阻止访问回环、链路本地、云元数据地址和非预期私网地址，防止 SSRF。若确实要支持局域网媒体源，做显式管理员配置白名单，不要默认开放。
- 上游请求头只能来自源的允许集合，例如 User-Agent、Referer、Cookie、Accept；过滤 Host、Connection、Content-Length、Authorization 等逐跳或敏感头。
- 日志不得记录票据、Cookie、真实带签名的流 URL或 JWT。
- 限制重定向次数、连接超时、首字节超时和最大并发。
- 对漫画图片和封面可按源/条目建立有界缓存；视频与音频默认只流式转发，不落盘。
- iframe legacy 模式不允许 same-origin，不允许读取父窗口，不把 token 拼入 srcdoc。

## 11. 权限、插件与后台任务

`media` 插件声明：

```text
media.read             查看媒体库、详情和进度
media.library.write    加入/移出、更新进度
media.sources.read     浏览媒体源与目录
media.sources.manage   导入、启停、导出、删除媒体源
```

普通用户默认获得前三项，`media.sources.manage` 只给管理员。修改 `backend/app/core/db.py` 的默认权限种子版本，确保旧数据库的普通用户角色只自动补种一次，之后仍尊重管理员手工编辑。

新增 `media_refresh.py`，与小说 `toc_queue` 分离：

- 每日按去重后的 `(source_id, item_key)` 刷新结构化 units 或旧式业务指纹；
- 单源限速，尊重 `concurrentRate`；
- 单条失败只记录，不终止整轮；
- 检测新增/变化后更新所有持有该条目的用户副本；
- 可复用当前 daily refresh 的调度时钟，但任务函数和日志前缀必须独立。

## 12. 前端信息架构

### 12.1 路由

在 `frontend/src/router/index.ts` 增加：

```text
/media                         媒体库总览
/media/comic                   漫画库
/media/audio                   音频库
/media/video                   视频库
/media/:kind/discover          对应类型的媒体源浏览/搜索
/media/item/:id                媒体详情与分集/章节
/media/player/:id              视频/音频播放器
/media/comic-reader/:id        漫画阅读器
/admin/media-sources           媒体源管理
```

不要使用会与 `comic|audio|video` 冲突的模糊动态路由；静态路由应排在动态路由之前。

### 12.2 桌面侧栏

重构 `NavItem`，使其支持 `children` 和 `depth`，不要继续依赖 `nav.slice()` 的位置分组；位置切片在插入父子项后很容易错组。

- “媒体库”放在“阅读”分组、紧跟“书架”。
- 一级按钮本身点击进入 `/media`；右侧有展开箭头，箭头只切换展开状态，不阻止一级菜单可访问。
- 当前路由以 `/media` 开头时自动展开。
- 下级菜单固定顺序：漫画、音频、视频。
- 父项只在精确 `/media` 时拥有活动滑块；进入分类时由对应子项拥有活动滑块，避免两个 active。
- 子项缩进、字号和图标层级清晰，键盘 Tab、Enter、Space 和 `aria-expanded` 可用。
- 滑块测量 Map 的 key 不能只依赖可能重复的 `to`；用稳定 nav id。

### 12.3 移动端

用户只要求左侧栏出现下级菜单，移动端没有左栏。移动端底栏只增加/保留一个“媒体库”一级入口，不把三个子项全部摊进底栏。`/media` 总览顶部提供“漫画 / 音频 / 视频”分段控件，分类页也保留同一切换入口。

如果当前底栏项目过多，媒体入口仍必须可见；可将管理类入口收进“我的”，但不要在这次功能中无理由重做整个导航。

### 12.4 页面与组件

推荐新增：

```text
frontend/src/pages/media/MediaOverviewPage.vue
frontend/src/pages/media/MediaLibraryPage.vue
frontend/src/pages/media/MediaDiscoverPage.vue
frontend/src/pages/media/MediaDetailPage.vue
frontend/src/pages/media/MediaPlayerPage.vue
frontend/src/pages/media/ComicReaderPage.vue
frontend/src/pages/admin/MediaSourcesPage.vue
frontend/src/components/media/MediaCard.vue
frontend/src/components/media/MediaProgress.vue
frontend/src/components/media/LegacyMediaFrame.vue
frontend/src/components/media/NativeVideoPlayer.vue
frontend/src/components/media/NativeAudioPlayer.vue
```

卡片视觉可复用 `.cover-grid` / `.ctile`，但文案必须按类型变化：

- 漫画：`读到第 N 话 · 第 M 页`
- 音频：`听到第 N 集 · mm:ss / mm:ss`
- 视频：`看到第 N 集 · mm:ss / mm:ss`

排序与书架一致，支持加入时间、最近更新、最后阅览和正/倒序。空状态按钮进入对应类型的 discover 页，而不是小说 `/search`。

### 12.5 总览页

总览首屏包含：

1. 三张分类统计卡，点击进入漫画/音频/视频；顺序始终一致。
2. “继续阅览”：取最近一条未完成进度，主按钮直接恢复。
3. “最近阅览”：跨三类按时间倒序，至少展示 8～12 条。
4. “最近更新”：仅展示 `hasUpdate`。
5. “最近加入”：没有阅览记录时作为兜底。

总览必须区分“从未阅览”和“已完成”，不能把 0% 当成接口异常。

### 12.6 三类阅读体验

**视频**：结构化源使用原生 `<video controls playsinline>`；支持选集、上一集/下一集、倍速、全屏、自动续播、错误重试。HLS 只有浏览器原生支持时直接播放；若后续引入 hls.js，应作为单独依赖决策，不在第一版暗中添加。

**音频**：使用 `<audio>`，提供封面、时间轴、上一集/下一集、倍速、定时停止；接入 Media Session API（浏览器支持时），页面隐藏仍按节流策略保存进度。

**漫画**：纵向连续图片、懒加载、当前页观察、适宽、上一话/下一话、下一话少量预取；单张失败可重试，不能因一张图失败让整章消失。

**legacy 视频**：只用 `LegacyMediaFrame.vue`，显示“兼容播放器”提示。原生结构化和 legacy 两条路径不能混在同一个 `v-html` 分支中。

## 13. WebDAV、首页与统计的处理

第一版媒体总览独立放在 `/media`，不要把媒体记录混入现有小说首页的“累计阅读本数”。如果要在全站首页展示，可后续单独定义“阅览时长”口径。

为了达到“类似书架”的数据可靠性，扩展现有 WebDAV 站点备份 payload：

- 增加版本号；
- 增加 `mediaLibrary` 和 `mediaProgress`；
- 恢复时按稳定 `(sourceKey, itemKey, user)` 合并，进度仍按更新时间新者胜；
- 旧备份没有媒体字段时正常恢复；
- 媒体源是管理员共享配置，默认不放入普通用户备份。

不要把媒体进度伪装成 Legado 小说 progress 文件，也不要影响现有 `/dav` 兼容行为。

## 14. 实施顺序

### 阶段 A：锁定行为和解析夹具

1. 为 `fqdj0512.json` 建立不联网的最小响应 fixture：分类列表、catalog 分集、video_model 三段响应。
2. 给当前 RSS parser 补回归测试，确保抽共享模块前后输出一致。
3. 写媒体类型识别测试，特别验证 `articleStyle=2` 不会被误当视频，而该文件通过 `<video>` 兼容识别为 video 并产生 warning。
4. 定义统一 DTO 和 adapter protocol。

### 阶段 B：模型、权限和媒体源

1. 新增四张表和幂等迁移测试。
2. 新增 media 插件声明、权限和默认角色补种。
3. 实现媒体源导入/导出/启停/删除。
4. 实现 RSS 与 BookSource 两个 adapter 的列表和搜索基础能力。
5. 完成管理页，可显式修正自动识别的 media kind。

### 阶段 C：媒体库主体

1. 实现 catalog、library CRUD、详情、units 和 overview API。
2. 实现前端路由、父子侧栏、移动端分类切换。
3. 实现总览页、分类页、发现页、详情页和共享卡片。
4. 加入/删除、排序、更新状态全部跑通。

### 阶段 D：播放器和进度

1. 先做结构化 comic/audio/video 解析与原生阅读器。
2. 实现短期票据和支持 Range 的资源代理。
3. 实现进度节流、退出补写、继续阅览和完成判定。
4. 实现 legacy sandbox、bootstrap、postMessage 和受限 legacy 状态。
5. 用原始 `fqdj0512.json` 完成番茄短剧端到端验收。

### 阶段 E：更新、备份与收尾

1. 增加媒体每日刷新和手动刷新。
2. 增加 WebDAV 媒体库/进度备份恢复，保持旧格式兼容。
3. 更新 README 功能表、架构说明和部署安全说明。
4. 执行后端全量 pytest、前端类型检查/构建和人工播放测试。

## 15. 测试清单

### 15.1 后端自动化测试

建议新增：

```text
backend/tests/test_media_source_import.py
backend/tests/test_media_rss_adapter.py
backend/tests/test_media_book_adapter.py
backend/tests/test_media_library_api.py
backend/tests/test_media_progress.py
backend/tests/test_media_proxy.py
backend/tests/test_media_refresh.py
backend/tests/test_media_webdav.py
```

覆盖：

- 原始 fqdj 源被识别为 video，且警告缺失 type；
- `articleStyle=2` 只影响布局，不影响媒体类型；
- 分类、分页和兼容搜索模板正确；
- 多用户看不到、删不到、改不到彼此条目和进度；
- 重复加入幂等，并发加入不产生重复行；
- 移出条目同时删除自己的进度；
- 最近阅览跨类型正确排序；
- 进度 0、95%、100%、倒退、切集的合并规则正确；
- 票据篡改、过期、越权均为 401/403；
- Range 请求正确转发和响应，代理不整包缓冲；
- SSRF 地址和危险请求头被拒绝；
- 过期流地址最多重新解析一次；
- daily refresh 只在业务目录变化时打更新标记；
- RSS 插件原测试全部通过；
- WebDAV 老备份与新备份可双向兼容。

### 15.2 前端验证

- `npm run build` 和 `vue-tsc --noEmit` 通过；如 package script 尚无 typecheck，直接调用本地 vue-tsc。
- 桌面侧栏父项可点击、箭头可独立展开，子项顺序正确，活动滑块不重叠。
- 键盘可操作所有父子菜单，读屏属性正确。
- 移动端底栏只有一级媒体入口，分类从页内切换。
- 三类空状态、加载、失败、重试、删除确认完整。
- 视频/音频切集和漫画翻话后进度正确；刷新页面可恢复。
- reduced-motion 下不依赖动画表达状态。
- 深/浅色、Miuix/MD3 两套主题均可读。

### 15.3 `fqdj0512.json` 人工验收路径

1. 在“媒体源管理”直接导入根目录原 JSON，选择或自动识别为视频。
2. 打开“媒体库 → 视频 → 浏览媒体源”。
3. 能看到“热剧”等分类、封面和短剧标题。
4. 打开任意短剧，加入媒体库。
5. 兼容播放器能列出剧集并开始播放，主站 JWT 不可被 iframe 读取。
6. 播放 15 秒后退出，总览出现最近阅览；再次进入能恢复集数和时间。
7. 加入另一个漫画或音频 fixture，确认三类统计、分类过滤和跨类最近阅览正确。
8. 手动刷新/定时刷新后，新增剧集只产生一次“有更新”提示；阅览到最新集后清除。

## 16. 完成定义（DoD）

以下条件全部满足才算完成，不能以“页面已出现”代替：

- 媒体库一级与三个下级菜单按指定顺序存在，一级总览可独立访问。
- comic/audio/video 都有真实可用的库、详情、阅览器和服务器端进度。
- 原始 `fqdj0512.json` 无需手改即可浏览、加入、播放、恢复进度。
- 旧式媒体脚本未运行在主站上下文，RSS 的安全清洗未被放宽。
- 资源代理支持 Range、短期授权和基本 SSRF 防护。
- 多用户隔离、权限、更新、WebDAV 兼容和旧数据库迁移有自动化测试。
- 原有书架、小说阅读、订阅、首页和 WebDAV 测试不回归。
- README 和必要架构文档已同步，所有新增接口与源规则字段有说明。

## 17. 给 DeepSeek V4 的执行约束

- 先阅读本方案列出的现有文件和相关测试，再编码；不要凭空重建已有解析器。
- 分阶段小步提交，先测试再完成对应实现；每阶段结束运行相关 pytest 和前端构建。
- 发现工作区已有用户改动时保留它们，不使用 reset/checkout 覆盖。
- 不以修改 `fqdj0512.json`、关闭 HTML 清洗、把播放器放进 `v-html`、把 JWT 拼到长期资源 URL 等方式“快速通过”。
- 若原生结构化规则暂时无法覆盖某个旧源，必须明确落入 legacy sandbox，并在 UI 标识兼容模式，不能静默降低安全边界。
