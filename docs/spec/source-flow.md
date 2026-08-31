# 书源（BookSource）解析执行流程规格

> 依据仓库 `legado-with-MD3-main` 当前源码整理，供 Python 移植镜像 Pydantic 模型与执行器实现。
> 源码位置：`app/src/main/java/io/legado/app/data/entities/`、`model/webBook/`、`model/analyzeRule/`。
> 注意：本 fork 中目录步骤文件为 `BookChapterList.kt`（上游旧名 BookToc），逻辑一致。

---

## 1. BookSource JSON 字段全表（Pydantic 镜像用）

### 1.1 顶层 BookSource（`data/entities/BookSource.kt`）

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| bookSourceUrl | String | `""` | 主键；同时也是 js 里 `source.getKey()`、baseUrl 来源 |
| bookSourceName | String | `""` | 名称；`getTag()` 返回它 |
| bookSourceGroup | String? | null | 分组，逗号分隔多组 |
| bookSourceType | Int | 0 | 0 文本 / 1 音频 / 2 图片 / 3 文件(仅下载) / 4 视频 |
| bookUrlPattern | String? | null | 详情页 url 正则；命中则搜索结果页按详情页解析 |
| customOrder | Int | 0 | 手动排序 |
| enabled | Boolean | true | |
| enabledExplore | Boolean | true | |
| jsLib | String? | null | 共享 JS 库（SharedJsScope） |
| enabledCookieJar | Boolean? | **JSON 默认 true**（Room 列默认"0"） | okhttp CookieJar 自动存 cookie |
| concurrentRate | String? | null | 并发率，如 `"次数@间隔毫秒"` 或分组写法 |
| header | String? | null | 请求头 JSON，或 `@js:` / `<js>` 动态生成 |
| loginUrl | String? | null | 登录 url 或登录 JS（`@js:` 前缀时为 JS） |
| loginUi | String? | null | 登录 UI 定义（RowUi JSON 数组） |
| loginCheckJs | String? | null | 每次响应后执行的登录检测 JS（返回 StrResponse） |
| coverDecodeJs | String? | null | 封面 bytes 解密 JS（ImageUtils 用） |
| bookSourceComment | String? | null | 注释（校验失败会追加 `// Error:` 段） |
| variableComment | String? | null | 自定义变量说明 |
| lastUpdateTime | Long | 0 | 排序用 |
| respondTime | Long | 180000 | 响应时间(ms)，排序用 |
| weight | Int | 0 | 智能排序权重 |
| exploreUrl | String? | null | 发现入口（见 §2.2 解析方式） |
| exploreScreen | String? | null | 发现筛选规则声明（本 fork 核心流程未消费） |
| ruleExplore | ExploreRule? | null | 可为对象**或 JSON 字符串** |
| searchUrl | String? | null | 搜索 url 规则（见 §2.1） |
| ruleSearch | SearchRule? | null | 同上 |
| ruleBookInfo | BookInfoRule? | null | |
| ruleToc | TocRule? | null | |
| ruleContent | ContentRule? | null | |
| ruleReview | ReviewRule? | null | **字段存在但 TypeConverter 恒写 "null"/读 null，实际不持久化** |
| eventListener | Boolean | false | 事件回调开关（fork 特有） |
| customButton | Boolean | false | 书源自定义按钮（fork 特有） |
| homepageModules | String? | null | 首页模块 JSON 数组字符串（fork 特有） |

非持久化但运行期重要：`temporaryVariable: String?`（内存态，调试变量优先于 CacheManager 的 sourceVariable）。

**规则对象的容错解析**：每个 rule 类都有 `jsonDeserializer`——JSON 是对象则直接反序列化；是 **JsonPrimitive（字符串）则把字符串内容再当 JSON 解析一次**。Python 端应接受 `dict | str`。

### 1.2 SearchRule（含 checkKeyWord）

| 字段 | 类型 | 默认 |
|---|---|---|
| checkKeyWord | String? | null | 校验关键字；`getCheckKeyword(default)` 仅当非空白且不含 `http`/`::`/`++`/`--` 时采用 |
| bookList | String? | null | 列表规则，前缀 `-`=保持源顺序(倒序站点) / `+`=显式正序 |
| name / author / intro / kind / lastChapter / updateTime / bookUrl / coverUrl / wordCount | String? | null |

（updateTime 为接口字段，BookList 流程未消费。）

### 1.3 ExploreRule

与 SearchRule 相同的 10 个列表字段（**无 checkKeyWord**）：bookList, name, author, intro, kind, lastChapter, updateTime, bookUrl, coverUrl, wordCount。

### 1.4 BookInfoRule

| 字段 | 类型 | 默认 |
|---|---|---|
| init | String? | null | 预处理规则：结果整体替换当前 content |
| name / author / intro / kind / lastChapter / updateTime / coverUrl / tocUrl / wordCount | String? | null |
| canReName | String? | null | **存在性开关**：非空且调用方允许时才允许覆盖已有书名/作者（值本身不参与匹配） |
| downloadUrls | String? | null | webFile 类型书的下载链接列表规则 |
| relatedBooks | String? | null | 相关书籍（本流程未消费） |

### 1.5 TocRule

| 字段 | 类型 | 默认 |
|---|---|---|
| preUpdateJs | String? | null | 抓目录前的全局 JS（AnalyzeRule.evalJS） |
| chapterList | String? | null | 前缀 `-`/`+` 语义同 bookList |
| chapterName | String? | null | |
| chapterUrl | String? | null | **不做绝对化**（原样存储，后续 getAbsoluteURL 再补全） |
| formatJs | String? | null | 逐章标题格式化 JS，绑定 gInt/index/chapter/title |
| isVolume | String? | null | 结果 `isTrue()` → 卷标志 |
| isVip / isPay | String? | null | 同上 |
| updateTime | String? | null | 存入 chapter.tag（即章节"时间/信息"，无独立 isUpdateTime 字段） |
| nextTocUrl | String? | null | 目录翻页规则（可返回多个 url → 并发模式） |

### 1.6 ContentRule

| 字段 | 类型 | 默认 |
|---|---|---|
| content | String? | null | 正文规则；为空时整个getContent 直接返回章节 url |
| subContent | String? | null | 副文（歌词等）；以 http 开头则再发一次请求 |
| title | String? | null | 从正文取标题覆盖 chapter.title（支持图片正则特例） |
| nextContentUrl | String? | null | 正文翻页规则（单个→串行循环，多个→并发） |
| webJs | String? | null | WebView 内执行的 JS（配合 sourceRegex 提取） |
| sourceRegex | String? | null | WebView 结果提取正则 |
| replaceRegex | String? | null | 源级全文替换规则（见 §4.2） |
| imageStyle | String? | null | DEFAULT/FULL/TEXT/SINGLE |
| imageDecode | String? | null | 图片 bytes 二次解密 JS |
| payAction | String? | null | 购买操作 |
| callBackJs | String? | null | 事件回调 JS |

### 1.7 ReviewRule（段评，基本废弃）

reviewUrl, avatarRule, contentRule, postTimeRule, reviewQuoteUrl, voteUpUrl, voteDownUrl, postReviewUrl, postQuoteUrl, deleteUrl —— 全部 String? = null。因 Converter 恒置 null，移植时可省略。

### 1.8 ExploreKind（发现分类项，exploreUrl 的结构化产物）

| 字段 | 类型 | 默认 |
|---|---|---|
| title | String | `""` |
| url | String? | null |
| type | String | `"url"`；可选 url/text/button/toggle/select |
| action | String? | null |
| chars | Array<String?>? | null（筛选字符集） |
| default | String? | null |
| viewName | String? | null |
| style | FlexChildStyle? | null |

### 1.9 Book 实体（简要，规则结果落点）

bookUrl(PK), tocUrl, origin(=书源url), originName, name, author, kind, customTag, coverUrl, customCoverUrl, intro, customIntro, remark, charset, type(Int 位标志), group, latestChapterTitle, latestChapterTime, lastCheckTime, lastCheckCount, totalChapterNum, durChapterTitle/Index/Pos/Time, wordCount, canUpdate, order, originOrder, variable(JSON map 字符串), readConfig(reverseToc/useReplaceRule/splitLongChapter/fixedType…), syncTime, listIntro。
init 块截断：kind≤1000、intro/listIntro/customIntro≤5000、remark/customTag≤1000、latestChapterTitle/durChapterTitle≤200。
SearchBook 为搜索/发现行模型，字段与 Book 子集一致 + originOrder + variable 快照。

---

## 2. 各步骤的 AnalyzeRule 调用与变量绑定

### 2.0 公共机制

**AnalyzeRule 构造**：`AnalyzeRule(ruleData, bookSource)`。ruleData 在搜索/发现时是空 `RuleData()`，在 bookinfo/toc/content 时就是 `Book` 本身。
- `setContent(body)`：自动识别 JSON（isJSON）；随后 `setBaseUrl(...)`、`setRedirectUrl(res.url)`（重定向最终地址，作为 URL 绝对化的基准 `URL` 对象）、`setCoroutineContext`。
- 规则串语法（splitSourceRule）：`@CSS:`/`@@`(默认 jsoup)、`@XPath:`/`/`(xpath)、`@Json:`/`$.`/`$[`、`<js>…</js>`/`@js:`、纯正则（含 `$n` 引用）、`##匹配##替换##[仅首次]` 后处理管道、`{{js}}` 内嵌、`@get:{key}` 取变量、`put:{key:value}` 存变量。
- `getString(rule, isUrl=true)`：结果经 `NetworkUtils.getAbsoluteURL(redirectUrl, str)` 绝对化；**结果为空白时回退 baseUrl**；`javascript:` 开头拼出空串。
- `getStringList(rule, isUrl=true)`：逐条绝对化 + 去重（保序）。

**JS 变量绑定**（Rhino，两套）：

| AnalyzeRule.evalJS | AnalyzeUrl.evalJS（URL 规则阶段） |
|---|---|
| java=this, cookie=CookieStore, cache=CacheManager, source, book(BaseBook), result, baseUrl, chapter, title(=chapter?.title), src(=content), nextChapterUrl, rssArticle, fromBookInfo | java=this, baseUrl, cookie, cache, page, key(搜索关键字), speakText, speakSpeed, book(ruleData as? Book), source, result, infoMap |

**变量存取链**（`java.put(k,v)` / `java.get(k)`）：chapter.variableMap → book.variableMap（即 `book.variable` 的 JSON map）→ ruleData.variableMap → source（CacheManager 键 `v_{sourceUrl}_{k}`；`source.getVariable()` 读 `sourceVariable_{sourceUrl}`，temporaryVariable 优先）。特殊 key：`get("bookName")`=book.name、`get("title")`=chapter.title。

### 2.1 搜索（WebBook.searchBookAwait → BookList.analyzeBookList）

1. `searchUrl` 空 → 直接抛错。构造 `AnalyzeUrl(mUrl=searchUrl, key=关键字, page=页码, baseUrl=bookSourceUrl, source=bookSource, ruleData=RuleData())`。
2. **searchUrl 规则展开顺序**：
   - 先执行 `@js:`/`<js></js>` 片段（`@result` 引用累积结果）；
   - 再替换内嵌 `{{js}}`；
   - 再替换页数占位 `<...>`：`pagePattern = <(.*?)>`，逗号分页表 `page<start,end,…>`，`page-1` 越界取最后一项；
   - 最后按 `\s*,\s*(?=\{)` 切出 UrlOption JSON：`{method(GET/POST/HEAD), charset, headers{…}, body, webView(bool), webJs, dnsIp, js(解析后改写url), retry, type, bodyJs(响应二次JS), serverID, webViewDelayTime, origin}`。
3. GET：query 按 charset 编码（charset=`escape` 用 JS escape 语义；已编码则不动）；POST：body 非 JSON/XML 且无 Content-Type 时按表单编码。
4. 请求（并发率限制器包裹）；`loginCheckJs` 非空则 `evalJS(checkJs, res)` 可整体替换响应；检测重定向仅打日志。
5. `baseUrl = res.url`（重定向后地址）传入 analyzeBookList；body 为 null 抛错。

**analyzeBookList**：
- `AnalyzeRule.setContent(body).setBaseUrl(res.url).setRedirectUrl(res.url)`。
- `bookUrlPattern` 与 res.url 匹配 → 整页按详情页解析（走 §2.3 的 analyzeBookInfo，canReName=false，name 非空才收），**提前 return**。
- 规则选择：搜索用 ruleSearch；发现时若 ruleExplore.bookList 空白则**回退 ruleSearch**。
- bookList 前缀 `-`→reverse=true，`+`→仅剥前缀；`getElements(listRule)` 取元素列表。
- **容错**：列表为空且无 bookUrlPattern → 整页按详情页兜底解析；有 pattern 则接受空结果返回空列表。
- 每个元素：`analyzeRule.setRuleData(searchBook)` 后 `setContent(item)`（注意 item 级 setContent 不带 baseUrl，沿用原 baseUrl）。
  - name = formatBookName(getString(name))；**name 为空 ⇒ 丢弃该条目**（唯一硬性门槛）。
  - author = formatBookAuthor(...)；kind = getStringList(kind)?.join(",")?.take(1000)；wordCount = wordCountFormat(...)；lastChapter → latestChapterTitle；intro = HtmlFormatter.format(...).take(5000)。这五项各自 try/catch，失败仅记 Debug 日志继续。
  - coverUrl：手动 `NetworkUtils.getAbsoluteURL(baseUrl, it)`（非空才赋值）。
  - bookUrl = `getString(ruleBookUrl, isUrl=true)`（相对 redirectUrl 绝对化；空白自动回退 baseUrl）。
  - 元数据固定：type=source.getBookType()（由 bookSourceType 映射 BookType 位标志）、origin=bookSourceUrl、originName、originOrder=customOrder、variable=ruleData.getVariable()（搜索期 put 的变量快照随书携带）。
- 收尾：LinkedHashSet 去重（保首现），reverse 时再反转。外部 filter 回调（name/author/kind）与 shouldBreak（聚合搜索凑够即断）在循环中生效。

### 2.2 发现（explore）

- exploreUrl 的"分类表"解析（`help/source/BookSourceExtensions.exploreKinds()`）：
  - 以 `<js>`/`@js:` 开头 → 执行 JS 得到文本或 JSON 数组（结果按 `md5(bookSourceUrl+exploreUrl)` 缓存）；
  - 是 JSON 数组 → 反序列化为 `List<ExploreKind>`；
  - 否则按 `&&|\n` 切行，每行 `标题::url` → `ExploreKind(title, url)`。
- UI 选定具体 kind.url 后，流程与 §2.1 完全相同（`exploreBookAwait`，isSearch=false），仅 ruleData/变量上下文不同。**本 fork 无 sortUrl 概念用于书源**（sortUrls 只属于 RssSource）；也没有旧版 2.x 的 `bookUrlPrefix` 字段。

### 2.3 详情（WebBook.getBookInfoAwait → BookInfo.analyzeBookInfo）

1. 未 `fixedType` 时先重置 type 为 `source.getBookType()`。
2. `book.infoHtml` 已缓存则直接解析（baseUrl=redirectUrl=book.bookUrl）；否则 `AnalyzeUrl(book.bookUrl, baseUrl=bookSourceUrl, ruleData=book)` 抓取，baseUrl=book.bookUrl，redirectUrl=res.url。
3. 规则应用顺序：
   - `init` 非空 → `setContent(analyzeRule.getElement(init))` 替换解析上下文；
   - `mCanReName = canReName && infoRule.canReName 非空`；name/author 仅在「结果非空 且 (mCanReName 或 当前值为空)」时写入（formatBookName/formatBookAuthor 清洗 `作者:`、括号别名等）；
   - kind：getStringList join(",") take(1000)，非空覆盖；
   - wordCount：wordCountFormat；lastChapter → latestChapterTitle；intro：HtmlFormatter.format take(5000)；
   - coverUrl：绝对化基准是 **redirectUrl**；
   - 以上均 try/catch 容错（缺字段静默跳过）。
4. 非 webFile：`tocUrl = getString(tocUrl规则, isUrl=true)`；空则回退 baseUrl；**tocUrl==baseUrl 时缓存 body 到 book.tocHtml**（目录与详情同页优化）。
5. webFile（type 含 webFile）：downloadUrls 必须非空，否则抛 `下载链接为空`。
6. 变量：AnalyzeRule 绑定的是 Book 本身，规则里 `book.xxx`、`java.put/get` 直接读写 book.variableMap（持久化到 `book.variable`）。

### 2.4 目录（WebBook.getChapterListAwait → BookChapterList.analyzeChapterList）

1. 可选执行 `ruleToc.preUpdateJs`（AnalyzeRule(book, source, preUpdateJs=true).evalJS，失败记日志不中断）。
2. `book.tocHtml` 缓存条件：`book.bookUrl == book.tocUrl`；否则 `AnalyzeUrl(book.tocUrl, baseUrl=book.bookUrl, ruleData=book)` 抓取；baseUrl=book.tocUrl，redirectUrl=res.url。
3. chapterList 前缀 `-`（reverse=true）/`+` 处理同 bookList。每页解析（私有 analyzeChapterList）：
   - `getElements(chapterList)`；nextTocUrl 非空时 `getStringList(nextTocUrl, isUrl=true)` 取下一页（相对 redirectUrl 绝对化、去重、**剔除等于当前 redirectUrl 的项**）。
   - 逐元素：`setChapter(bookChapter)` 后 title=chapterName 规则、url=chapterUrl 规则（**原样，不绝对化**）、tag=updateTime 规则、isVolume/isVip/isPay 经 `isTrue()`（"true/是/1/yes" 等）判定。
   - url 为空的兜底：卷 → `title+index`；普通章 → baseUrl。title 为空 ⇒ 丢弃该条目。
4. **翻页编排**：
   - 下一页列表长度 0：结束。
   - 长度 1：`while (nextUrl.isNotEmpty() && !visited.contains(nextUrl))` 串行抓取——**无次数上限，防环完全靠 visited 集合去重**；每次 baseUrl=nextUrl、redirectUrl=nextUrl（res.url）。
   - 长度 >1：flow + `mapAsync(OtherConfig.threadCount)` 并发抓全部页，各页不再递归取下一页；结果按原始 url 顺序 collect 追加。
5. 汇总后处理（顺序敏感）：
   ```kotlin
   if (chapterList.isEmpty()) throw TocEmptyException
   if (!reverse) chapterList.reverse()      // 第一次反转
   val list = ArrayList(LinkedHashSet(chapterList))  // 去重：保"反转后"的首现
   if (!book.getReverseToc()) list.reverse()         // 第二次反转
   list.forEachIndexed { i, c -> c.index = i }
   ```
   净效果（reverseToc=false）：默认/`+` 前缀 → 最终顺序=网页文档顺序；`-` 前缀 → 最终=文档逆序（适配"最新章在前"的站）。reverseToc=true（用户设置）再翻转一次。**去重发生在两次反转之间**，因此重复章节保留的是"反转后首现"的那一条（默认情况下即文档中较后的重复项）。
6. formatJs：Rhino 逐章执行，绑定 `{gInt:0, index:i+1, chapter, title}`，返回值覆盖 title，出错仅记日志。
7. 标题净化：ContentProcessor.getTitleReplaceRules()（书源级+用户级替换规则，受 `useReplaceRule` 开关与中文转换配置影响）作用于 durChapterTitle/latestChapterTitle 展示值；同时更新 totalChapterNum、lastCheckCount（新增章数）、lastCheckTime；`preserveChapterMetadata` 按 fileName 回填库中已有的 wordCount/variable/reviewImg。

### 2.5 正文（WebBook.getContentAwait → BookContent.analyzeContent）

1. 前置短路：content 规则为空 → 直接返回 `bookChapter.url`（音频外链型）；卷且 url 以 title 开头 → 返回 `tag`。
2. 抓取：`chapter.url == book.bookUrl` 且有 tocHtml 时复用 tocHtml；否则 `AnalyzeUrl(chapter.getAbsoluteURL(), baseUrl=book.tocUrl, ruleData=book, chapter=bookChapter)`，并以 `getStrResponseAwait(jsStr=contentRule.webJs, sourceRegex=contentRule.sourceRegex)` 支持 WebView 渲染提取。
3. 解析上下文：`AnalyzeRule.setContent(body, baseUrl)` + redirectUrl=res.url + setChapter + setNextChapterUrl（下一章 url：参数为空时从 DB 取 index+1 章，缺则第 0 章）——供 `{{}}` JS 与 `@get:` 使用。
4. 单页解析：content = `getString(contentRule.content, unescape=false)`；文本书做 HtmlFormatter.formatKeepImg(content, redirectUrl)（内置净化+图片转存占位）、含 `&` 则 unescapeHtml4；adaptSpecialStyle 开启时先用占位符保护特定标签再还原。
5. **正文翻页**：结构与目录一致——
   - 单个 next url：`while (nextUrl.isNotEmpty() && !nextUrlList.contains(nextUrl))`，另有一个额外 break：**绝对化后的 nextUrl == 绝对化后的 mNextChapterUrl 时停止**（防止把下一章正文并进来）。仍无数值上限，防环靠 visited 集。
   - 多个 next url：并发 `mapAsync(threadCount.coerceIn(1, maxNextPageConcurrency=4))`，各页 getNextPageUrl=false，按 flow 发射顺序追加。
   - 页与页之间以 `\n` 连接（appendContent 维护 pageCount 计数）。
6. subContent：规则结果 trim 后若以 http 开头则再发请求取体；在线文本书追加到正文，音频书写入 `chapter.putVariable("lyric", …)`。
7. title 规则：成功则覆盖 chapter.title 并清 titleMD5、写 DB；若结果是 `<img …>` 形式，img 正则 group1 非空用作标题，group2 存 `chapter.reviewImg`。
8. **replaceRegex（源级全文替换）**：非空时——先把整章按行 trim 再以 `\n` join，然后 `contentStr = analyzeRule.getString(replaceRegex, contentStr)`（整章文本作为规则上下文 result，规则可以是 `@js:`、正则管道等任意 AnalyzeRule 规则），最后每行加中文缩进 `　　`。
9. 收尾：卷以外正文空白抛 ContentEmptyException；needSave 时 `BookHelp.saveContent` 落盘（该路径内部再叠加用户替换规则/分段等 ContentProcessor 处理，属阅读端而非抓取端职责）。

---

## 3. ReplaceRule（用户净化规则，简列）

`data/entities/ReplaceRule.kt`：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| id | Long | System.currentTimeMillis() | PK 自增 |
| name | String | "" | |
| group | String? | null | |
| pattern | String | "" | 匹配（正则或字面量） |
| replacement | String | "" | 替换为（支持 `$1` 组引用） |
| scope | String? | null | 生效书名/书源范围 |
| scopeTitle | Boolean | false | 作用于标题 |
| scopeContent | Boolean | true | 作用于正文 |
| excludeScope | String? | null | 排除范围 |
| isEnabled | Boolean | true | |
| isRegex | Boolean | true | |
| timeoutMillisecond | Long | 3000 | 正则执行超时保护（≤0 归一 3000） |
| order | Int(列名 sortOrder) | Int.MIN_VALUE | 排序 |

与抓取流程的关系：BookContent **不直接读 ReplaceRule 表**；源级 inline 替换只有 `ruleContent.replaceRegex`（§2.5 第 8 步）。用户 ReplaceRule 在保存正文/展示标题时由 ContentProcessor 应用，超出本文范围。

---

## 4. Python 移植必须桩掉/替换的 Android 设施

| 设施 | 使用点 | 建议 |
|---|---|---|
| OkHttp + CookieJar | AnalyzeUrl 全部请求；CookieStore 按域名存 cookie（数据库），enabledCookieJar 决定是否自动回写 | httpx/requests + 自实现 cookie 持久层；`CookieManager.cookieJarHeader` 标记头需保留语义 |
| Cronet | `AppConfig.isCronet` 分支、dnsIp 自定义解析（customIp 表） | 忽略 Cronet 分支；dnsIp 可用自定义 resolver 或跳过 |
| BackstageWebView（WebView） | urlOption `webView:true`/`webJs`、contentRule.webJs+sourceRegex、AnalyzeRule 的 `<js>` WebJs 模式 | 无法无头渲染：显式报"unsupported capability"，不可静默返空（AGENTS.md 红线） |
| Rhino JS（`:modules:rhino`） | 一切规则 JS：searchUrl/@js/{{}}/formatJs/preUpdateJs/loginCheckJs/coverDecodeJs/imageDecode | quickjs / dukpy / stpyv8 可切换（`settings.js_engine` + 运行期覆盖）；注入 `rhino_compat.js` 提供 `JavaImporter`/`Packages`/`importClass`/`importPackage`/okhttp3/hutool 兼容；注意 Double 整数化输出、`String.format("%.0f")` 语义与 bindings 全集对齐 |
| android.text.TextUtils / Parcelable / Room 注解 | 实体层 | 移植时丢弃；仅保留字段与默认值 |
| Jsoup / JsoupXpath / JsonPath | AnalyzeByJSoup/XPath/JSonPath（锁定 jsoup ≤1.16.2 行为） | Python: lxml/css-select/beautifulsoup4 + jsonpath；注意 text()、ownText、`!` 排除选择器等方言差异 |
| Glide / Media3 / ExoPlayer | 封面加载、音频 MediaItem（AnalyzeUrl 里的辅助方法） | 与抓取流程无关，可不移植 |
| ANDROID_ID 加密登录信息 | BaseSource.getLoginInfo/putLoginInfo（AES-128, key=androidId 前 16B） | 改用本地密钥文件；接口形状保留 |
| ACache / CacheManager | exploreKinds 缓存、sourceVariable、loginHeader、`v_` 变量 | 统一 KV 缓存层即可 |
| ConcurrentRateLimiter | concurrentRate 限流 | 纯算法，可直接移植（键为 sourceUrl） |
| EncoderUtils.escape（charset="escape"） | query/form 编码分支 | 等价 JS `escape()`，需自行实现 |
| wordCountFormat / HtmlFormatter | 字数归一（"万"单位）、正文 HTML 清洗 | 纯 JVM 逻辑，可直译 |
| NetworkUtils.getAbsoluteURL | 所有 URL 补全 | 语义要点：base 取 `,` 前部分；已绝对/data url 原样；`javascript*` 返回空串；拼接失败**返回原串不抛错** |

---

## 5. 易踩坑清单（gotchas 汇总）

1. `enabledCookieJar` JSON 默认 **true**，与 Room 列默认 false 不一致——以 JSON/GSON 侧为准。
2. 规则字段可写成字符串形式的嵌套 JSON（jsonDeserializer 双层解析），Pydantic 需要自定义 validator。
3. ruleReview 永远不会持久化；`bookUrlPrefix`、`ruleJs`、`sortUrl` 在本 fork 的书源中**不存在**（勿按旧版资料建模）。
4. 搜索条目唯一的硬丢弃条件是 name 为空；其余字段缺失一律容忍。
5. `getString(isUrl=true)` 空结果回退 baseUrl——目录/详情 url 兜底行为来自这里，不是显式代码。
6. TocRule.chapterUrl **不绝对化**；ContentRule.nextContentUrl/TocRule.nextTocUrl **会**绝对化并去重。
7. 目录/正文翻页均无硬性循环上限，防环仅靠 visited 集合（正文另有"到达下一章即停"检查）；多下一页 URL 时走一次性并发（目录 threadCount，正文上限 4），此时**不**再解析这些页的下一页。
8. 目录双重反转 + 中间去重的组合决定最终顺序与重复项取舍（§2.4 第 5 步），移植时不要简化成单次反转。
9. `canReName` 是"允许改名"的门禁字段，其字符串值不被使用。
10. 源变量有两套：book.variable（随书 JSON map）与 sourceVariable（CacheManager，按书源全局）；`java.put/get` 的解析顺序是 chapter → book → ruleData → source。
