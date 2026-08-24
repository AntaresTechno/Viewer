# AnalyzeUrl 复刻规范（Python 重实现）

> 源码基准：`legado-with-MD3-main/app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeUrl.kt`（下称 AU.kt）、
> `.../analyzeRule/CustomUrl.kt`。辅助引用：`AnalyzeRule.kt`、`help/http/*`、`utils/NetworkUtils.kt`、
> `utils/EncoderUtils.kt`、`help/storage/ImportOldData.kt`。
> 本文描述**当前代码的真实行为**，凡请求大纲与源码不符处均已显式标注差异。

---

## 0. 总体管线

`AnalyzeUrl(mUrl, key?, page?, speakText?, speakSpeed?, baseUrl="", source?, ruleData?, chapter?, …, headerMapF?=null, hasLoginHeader=true, infoMap?)`
构造即完成全部解析，之后 `getStrResponseAwait()/getResponseAwait()/getByteArrayAwait()` 只负责发请求。

构造顺序：

1. 从 `baseUrl` 中剥离 `,{…}` 选项后缀（`paramPattern`，见 §2），剩余部分作为相对解析的 base。
2. 组装 `headerMap`：显式 `headerMapF`，否则 `source.getHeaderMap(AppConfig.userAgent, hasLoginHeader)`；
   若其中含 `"proxy"` 键则提取为代理并从 headerMap 移除（仅此一处做 proxy 提取）。
3. `initUrl()` 三步，顺序固定：

```kotlin
fun initUrl() {
    ruleUrl = mUrl
    analyzeJs()        // ① 执行 @js: / <js></js> 片段
    replaceKeyPageJs() // ② 展开 {{…}} 内嵌 JS；再替换 <a,b,c> 页码块
    analyzeUrl()       // ③ 切分 url / ,{JSON} 选项，绝对化，编码 query/form
}
```

关键正则：

```kotlin
val paramPattern = Regex("\\s*,\\s*(?=\\{)")   // 首个“逗号(允许两侧空白)+紧跟 {”处切分
private val pagePattern = Regex("<(.*?)>")     // 页码块
// AppPattern.JS_PATTERN = Regex("<js>([\\w\\W]*?)</js>|@js:([\\w\\W]*)", IGNORE_CASE)
```

注意：`paramPattern` 要求逗号后必须是 `{`，否则整串都按 URL 处理（选项不存在）。多个 `,{` 时只认第一个，
其后所有内容（含更多逗号）都属于选项字符串。

---

## 1. URL 模板展开

### 1.1 `<js>…</js>` 与 `@js:` 片段（analyzeJs）

- `<js>` 版本懒惰匹配到第一个 `</js>`；`@js:` 版本贪婪匹配到**字符串末尾**（其后不能再有正文）。
- 顺序扫描：把原文切成「字面量块 / JS 块」交替序列。`result` 为累积值，初始为整个原串：

```kotlin
var start = 0
var result = ruleUrl
for (m in AppPattern.JS_PATTERN.findAll(ruleUrl)) {
    if (m.range.first > start) {
        ruleUrl.substring(start, m.range.first).trim().let {
            if (it.isNotEmpty()) result = it.replace("@result", result)
        }
    }
    result = evalJS(m.groupValues[2].ifEmpty { m.groupValues[1] }, result).toString()
    start = m.range.last + 1
}
if (ruleUrl.length > start) { /* 尾部字面量同样 trim 后 @result 替换 */ }
ruleUrl = result
```

语义：
- 每个 JS 的返回值 `.toString()` 后成为新的 `result`（JS 返回 null 会得到字符串 `"null"`）。
- JS 之后的非空字面量块中，`@result` 占位符被替换为当前 `result`；空白块不改变 `result`。
- 典型用法：`<js>...</js>{{key}},{"method":"POST"}` 或 `url@js:...`。
- JS 执行环境见 §1.4。

### 1.2 `{{…}}` 内嵌表达式（replaceKeyPageJs）

前置条件：`ruleUrl` 同时包含 `"{{"` 和 `"}}"`。用 `RuleAnalyzer.innerRule("{{","}}")` 找到每个 `{{`
到**最近的** `}}` 之间的内容，作为 JS 求值：

```kotlin
val url = analyze.innerRule("{{", "}}") {
    val jsEval = evalJS(it) ?: ""
    when (jsEval) {
        is String -> jsEval
        is Double if jsEval % 1.0 == 0.0 -> String.format("%.0f", jsEval)
        else -> jsEval.toString()
    }
}
if (url.isNotEmpty()) ruleUrl = url
```

- 结果转换规则：String 原样；整数 Double（`x % 1.0 == 0.0`）格式化为无小数点整数字符串（`2.0 → "2"`）；
  其余 `toString()`；null 视为空串。
- 此阶段 `{{}}` 内的 `result` 绑定为 **null**（不同于 1.1）。
- 匹配是“最近 `}}`”，**没有括号配平**：JS 里出现对象字面量且内部有 `}}` 连续序列会被截断。复刻时按
  「找下一个 `}}`」实现即可，不必支持嵌套花括号。
- 替换发生在**整条 ruleUrl 上，包括后面的 `,{JSON}` 选项部分**——因此 body/headers 里也能写 `{{page}}` 等。

### 1.3 变量与算术

`evalJS` 绑定表（`{{…}}`、`@js:`、选项 `js/bodyJs` 共用同一实现）：

| 绑定名 | 类型 | 说明 |
|---|---|---|
| `java` | JsExtensions | 全部 JS 扩展方法（java.ajax 等） |
| `baseUrl` | String | 当前 baseUrl（构造入参去选项后；③ 之后会更新为最终 url 的 scheme://authority） |
| `cookie` | CookieStore | |
| `cache` | CacheManager | |
| `page` | Int? | 页码；未传为 null |
| `key` | String? | 关键字（搜索词/字典词等）；未传为 null |
| `speakText`, `speakSpeed` | | TTS 场景 |
| `book` | Book? | ruleData as? Book |
| `source` | BaseSource? | |
| `result` | Any? | 仅 `@js:` 管线中有值；`{{}}` 阶段为 null |
| `infoMap` | Map? | |

因此：
- `{{key}}` = 直接插入关键字原文；`{{page}}` = 页码十进制字符串。
- 算术就是普通 JS：`{{page-1}}`、`{{page+1}}`、`{{page*10}}`、`{{key.length}}` 均可行。
- **没有任何引号/转义层**：展开结果不做 URL 编码（编码只发生在 §4/§5 的 query/form 阶段），
  关键字中的空格、中文、`&` 都会原样进入 URL 字符串。

### 1.4 页码块 `<a,b,c>`（pagePattern）

仅当构造时传入 `page != null` 才执行：

```kotlin
for (m in pagePattern.findAll(ruleUrl)) {
    val pages = m.groupValues[1].split(",")
    ruleUrl = if (page < pages.size) {
        ruleUrl.replace(m.value, pages[page - 1].trim { it <= ' ' })
    } else {
        ruleUrl.replace(m.value, pages.last().trim { it <= ' ' })
    }
}
```

- 语义：`pages[min(page-1, size-1)]`——page=1 取第 1 个（当列表长度>1 时），超出长度钳制到最后一个。
  例：`<10,20,50>`，page=7 → `50`；page=1 → `10`。
- 每个匹配项在全文**全局替换所有同名出现**。
- 执行顺序刻意为先 `{{}}` 后 `<…>`（注释：避免内嵌规则里的大于小于号把规则切错）。副作用：
  `{{…}}` 展开结果若含有 `<xxx>` 且此时 page!=null，仍会被页码块规则二次吞掉。
- 无 page 时 `<…>` 原样保留（如 body 里的 `<BASE64>` 标记不受影响）。

### 1.5 关于 `searchPage` / `searchKey`

**运行时不存在这两个变量**。它们是旧版书源语法，导入时由 `ImportOldData.toNewUrl()` 改写：

```kotlin
url = url.replace("{", "<").replace("}", ">")          // 旧 {} 占位 → <> 页码块
url = url.replace("searchKey", "{{key}}")
url = url.replace("<searchPage([-+]1)>".toRegex(), "{{page$1}}")
    .replace("searchPage([-+]1)".toRegex(), "{{page$1}}")
    .replace("searchPage", "{{page}}")
```

即在进入 AnalyzeUrl 之前就已变成 `{{key}}/{{page}}/{{page-1}}`。Python 复刻若要兼容老书源，
应把这层迁移做成独立的前置归一化函数，而不是塞进 AnalyzeUrl。

---

## 2. `, {JSON}` URL 选项（UrlOption）

`analyzeUrl()` 在首个 `\s*,\s*(?=\{)` 处切出选项字符串，先 `GSONStrict` 解析，失败再用宽松 `GSON`
重试并记录“链接参数 JSON 格式不规范”。**两遍都失败 → 所有选项被静默丢弃**（URL 保持逗号前部分）。
注意 Gson 反序列化要求整串恰好是一个完整 JSON 对象，尾部多余字符（如 `{"charset":"gbk"}&o=`）
会导致两遍解析均失败——不要指望“取前缀对象”。

`UrlOption` 数据类的**完整键清单**（未列出的键一律忽略）：

| 键 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `method` | String | `GET` | 大写后仅识别 `POST`→POST、`HEAD`→HEAD；**其余一切（含 PUT/DELETE）都回落 GET** |
| `charset` | String | null | 请求侧编码字符集；特殊值 `escape` 见 §5；空白视为 null |
| `headers` | Object\|String | — | 合并进 headerMap（覆盖同名源级头）；值为 String 时再按 JSON 对象解析一次；条目值 `toString()` 入 map |
| `body` | Any | null | 见 §4；JSON 对象/数组字符串会被解析后再规范化序列化，其余保持原文；空白视为 null |
| `origin` | String | null | 仅存储透传（`getOrigin()`），AU 内部不使用 |
| `retry` | Int | 0 | 请求失败（非 2xx）时的额外重试次数，共尝试 retry+1 次 |
| `type` | String | null | 响应类型标记（如 `zip`/`epub`/`txt`）：设置后 `getStrResponseAwait` 返回字节流的 hex 字符串而非文本；也用作下载文件扩展名。**不随请求发送** |
| `webView` | any | false | 真值判定：`null/""/false/"false"` 为假，**其余一律为真**（含 `true/"true"/1/"1"`） |
| `webJs` | String | null | webView 加载完成后执行的 JS（BackstageWebView） |
| `dnsIp` | String | null | 自定义域名 IP（仅 Cronet 生效） |
| `js` | String | null | URL 解析完成后的收尾 JS：以 `result=url` 求值，返回非 null 则 `url = 返回值` |
| `bodyJs` | String | null | 得到响应后执行的 JS：以 `result=响应体` 求值，结果作为新响应体 |
| `serverID` | Long | null | 服务器 id（WebDAV/远程书籍路由用） |
| `webViewDelayTime` | Long | null | webView 等待时长 ms，应用时 `max(0, v)` |

**`proxy` 不是 UrlOption 的键。** 代理只能写在 headers JSON 里：源级 `getHeaderMap` 结果中出现
`"proxy": "http(s)|socks4|socks5://host:port[@user@pass]"` 时，构造器把它从 headerMap 抽出存为代理。
⚠️ 选项级 `headers` 里的 `proxy` 键**不会**被抽取，会原样作为名为 `proxy` 的 HTTP 头发送（现状怪癖，需照抄）。

选项应用次序（`analyzeUrl()`）：method → headers 合并 → body → type → charset → retry → useWebView →
webJs → bodyJs → dnsIp → `js`（可改写 url）→ serverID → webViewDelayTime。

---

## 3. 旧版（无 JSON 花括号）选项格式

**事实声明：本代码库中不存在 `getUrlParameter*` 一类辅助函数**（全库 grep 无命中）。当前 AnalyzeUrl
对非 JSON 选项零兼容：`,charset=gbk` 这种写法因不满足 `,(?=\{)` 根本不会被切分，整个字符串按 URL 处理。

旧格式的唯一处理点是**书源导入迁移** `ImportOldData.toNewUrl()`（`help/storage/ImportOldData.kt:317`），
它把旧语法一次性改写成现代格式，规则如下（复刻兼容层照此实现）：

| 旧写法 | 迁移结果 |
|---|---|
| `url@post数据` | `,{"method":"POST","body":"…"}`（`@` 后为 body） |
| `url\|charset=gbk` | `,{"charset":"gbk"}`（`\|` 后 `charset=` 取值） |
| `@Header:{...}`（忽略大小写，正则 `@Header:\{.+?\}`） | 移出该段并入 `headers` |
| `searchKey` | `{{key}}` |
| `searchPage` / `searchPage-1` / `searchPage+1` | `{{page}}` / `{{page-1}}` / `{{page+1}}` |
| 旧的 `{…}` 占位符 | 先整体替换 `{`→`<`、`}`→`>`（配合上行得到页码块） |
| `<js>` 块内的 searchKey/searchPage | 改名为 `key`/`page`，块本身不动 |
| 多个发现地址 | 以 `&&` 或换行分隔逐条迁移 |

形如 `searchUrl= x,{"charset":"gbk"}&o=` 的混合串：能切出 `{"charset":"gbk"}&o=`，但尾部垃圾使 JSON
解析整体失败 → 选项丢弃。不存在“`&` 前/后 key=value 并入 option map”的逻辑。

另注：`,&page=<,page>` 若真的传入，`<,page>` 会被页码块规则当成 `["","page"]` 两元素列表处理
（page=1 时替换为空串）——这只是 §1.4 通用规则的副产品，并非专门的参数语法。

---

## 4. POST body

### 4.1 归一化

选项里的 `body` 经 `setBody/getBody` 往返：

```kotlin
fun setBody(value: String?) {
    body = when {
        value.isNullOrBlank() -> null
        value.isJsonObject() -> GSON.fromJsonObject<Map<String, Any>>(value).getOrNull()
        value.isJsonArray()  -> GSON.fromJsonArray<Map<String, Any>>(value).getOrNull()
        else -> value
    }
}
fun getBody(): String? = body?.let { it as? String ?: GSON.toJson(it) }
```

- `isJsonObject()` 仅判断首尾字符 `{`…`}`（数组同理 `[`…`]`）。因此 body 写成 JSON 时会被解析成
  Map/List 再规范化序列化（数字经 LONG_OR_DOUBLE、int/double 修正器）；写成其他文本则逐字保留。
- 解析失败（非法 JSON）时 getOrNull 得 null → body 丢失。

### 4.2 分流（analyzeUrl 构造期）

```kotlin
RequestMethod.POST -> body?.let {
    if (!it.isJson() && !it.isXml() && headerMap["Content-Type"].isNullOrEmpty()) {
        analyzeFields(it)   // 当作表单字段串编码 → encodedForm
    }
}
```

- body 非 JSON 非 XML（`isXml()` = 首尾 `<`…`>`）且未显式给 Content-Type → 表单模式。
- 显式 Content-Type 存在 → body 永远按原文发送。

### 4.3 发送期优先级（executeStrRequest / getResponseAwait 相同）

```kotlin
if (!encodedForm.isNullOrEmpty() || body.isNullOrBlank()) postForm(encodedForm ?: "")
else if (!contentType.isNullOrBlank()) post(body.toRequestBody(contentType.toMediaType()))
else postJson(body)
```

| 条件 | 实际请求体 | Content-Type |
|---|---|---|
| 有 encodedForm，或 body 空/blank | 表单串（可能为空串） | `application/x-www-form-urlencoded` |
| body 为 JSON（已规范化）且无 CT | 规范化 JSON 文本 | `application/json; charset=UTF-8`（postJson 固定） |
| body 其他文本 + 显式 CT | 原文 | 用户指定值 |

### 4.4 表单编码细节（encodeParams(isQuery=false)）

- 按 `&` 切分、每段按**第一个** `=` 分 key/value；无 `=` 则只有 key。
- 每个组件 `appendEncoded`：
  - 未指定 charset 时（checkEncoded=true）：若整体已是合法百分号编码（`NetworkUtils.encodedForm`，
    安全字符仅字母数字和 `* - . _`，外加成组合法 `%XX`）→ 原样输出，否则 URLEncoder.encode(UTF-8)。
  - charset="escape" → `EncoderUtils.escape`：保留 `[0-9A-Za-z]`，其余按 char code 输出 `%XX`（<256）
    或 `%uXXXX`（BMP），十六进制小写。
  - 指定其他 charset → `URLEncoder.encode(value, charset)`（空格变 `+`）。
  - ⚠️ 指定 charset 后 checkEncoded=false，**已编码值会被二次编码**；值内字面 `=`/`&` 也无法表达
    （安全集不含它们）。
- 结果存 `encodedForm`，发送时不再改动。

GET 的 query 编码走同函数 isQuery=true 分支，见 §5。

### 4.5 其他

- `upload(fileName, file, contentType)`：要求 body 是 JSON 对象；值等于字符串 `"fileRequest"` 的成员被
  替换为 `{fileName,file,contentType}` 描述符后走 multipart（`type` 用作 multipart 的 part 类型）。
- webView+POST：先用 OkHttp 按上述规则取回 HTML，再把该 HTML 交给 BackstageWebView 渲染执行 webJs。

---

## 5. Charset

职责边界：**AnalyzeUrl 只管请求方向**。

- 请求侧：`charset` 选项决定 GET query 与 POST form 组件的字节编码；`escape` 为特殊模式（§4.4）。
  未指定时 query/form 用 UTF-8（且带“疑似已编码就跳过”探测）。非法字符集名将抛异常（`Charset.forName`）。
- 响应侧解码不在 AnalyzeUrl：`OkHttpUtils.ResponseBody.text()` 依次为 ① 去 UTF-8 BOM →
  ② 响应头 Content-Type charset → ③ `EncodingDetect.getHtmlEncode(bytes)` 内容嗅探。
  HTML meta 标签探测属于 EncodingDetect，属另一模块，本文不展开。

GET query 特例（encodeParams(isQuery=true)，仅当 charset 已指定且非 escape）：

```kotlin
if (NetworkUtils.encodedQuery(params)) return params           // 已编码则原样
return encodeQueryParams(params, charset)                      // 自定义保守编码器
```

自定义编码器安全字符表：字母数字 + `_.-~!$%&()*+,/:;=?@[\]^`{|}` —— 即结构字符（`=&+` 等）保持原样，
仅对非 ASCII/控制字符按目标 charset 逐字节输出大写 `%XX`。这与表单路径（整体 URLEncoder、`+` 表空格）
不同，复刻时两条路径都要实现。

---

## 6. 分页占位符与 CustomUrl

### 6.1 page 的取值约定

- 搜索/发现：`WebBook.searchBookAwait/exploreBookAwait` 传 `page`，**首页为 1**，UI 翻页递增。
- 目录翻页、正文、ajax 等：通常不传 page（null）→ `<…>` 页码块逻辑整体跳过。
- page=1 与 >1 没有任何内置特判；“第一页偏移 -1”完全靠书源自己写 `{{page-1}}`（或旧源迁移产物）。
  同理不存在 `,+1` 之类的隐式语法。

### 6.2 各占位符汇总

| 写法 | 生效条件 | 行为 |
|---|---|---|
| `{{key}}` | 总是（JS 绑定） | 插入关键字原文，无转义 |
| `{{page}}` / `{{page±n}}` | 总是（JS 绑定） | 页码及任意 JS 算术；整数 Double 去小数点 |
| `<p1,p2,…>` | page != null | 取 `pages[min(page-1,size-1)]`，全局替换该匹配 |
| `searchKey/searchPage` | 运行时不支持 | 导入期已迁移（§1.5/§3） |

### 6.3 CustomUrl（本代码库实况）

`CustomUrl.kt` 只有 49 行，功能为「URL + 附加属性袋」：

- init：用同一个 `paramPattern` 把输入切成裸 URL 和 `,{JSON}` 属性 map（解析失败则属性为空）。
- `putAttribute(k,v)`：v=null 删除该键。
- `getUrl()` 返回裸 URL；`getAttr()` 返回属性 map。
- `toString()`：属性为空返回裸 URL，否则 `mUrl + "," + GSON.toJson(attribute)` 重新拼回。

调用方：WebDAV 远程书籍路径（`book.origin = BookType.webDavTag + CustomUrl(path)`）、`WebDav` 取裸 URL、
`UrlUtil.getSuffix` 取裸 URL 推导文件扩展名。**它不做任何模板展开、不分页。**

> ⚠️ 差异声明：任务大纲提到的 “CustomUrl types 0/1/2、page chunking、pageBar、%p”
> 在本源码树中**不存在**（全库 grep 无命中）。本仓库的分页机制就是 §1.4 的 `<…>` 页码块 +
> `{{page}}` 算术；CustomUrl 没有 type 字段。若 Python 目标行为参考了其他阅读类 App 的概念，
> 请勿映射到本实现的 CustomUrl。（旁证：`SearchBooksUseCase` 用 `Regex("""<[^<>]+>""`) 检测搜索 URL
> 是否含分页组，依据同样是 `<…>` 语法。）

---

## 7. Header 组装

### 7.1 来源与优先级

1. **源级**（构造期）：`source.getHeaderMap(userAgent, hasLoginHeader)`（或外部直接传 `headerMapF`）：
   - 书源的 `header` 规则字段：可为 `@js:` / `<js>…</js>` 动态生成 JSON，或直接是 JSON 对象文本；
     解析同样 strict→lenient 两段式。
   - **默认 UA 注入**：仅当上述结果不含 `User-Agent`（`AppConst.UA_NAME`）时放入全局默认 UA
     （`AppConfig.userAgent`）。
   - `hasLoginHeader=true`（默认）时合并登录头 `loginHeader_{key}` 缓存的 JSON。
   - 键 `proxy` 被抽出（§2）。
2. **规则级/URL 选项级**：`option.headers` 在 analyzeUrl 中合并，同名覆盖源级；值统一 `toString()`
   （JSON 数字头会变字符串）。Gson 解析保序，headerMap 为 LinkedHashMap。

**没有自动 Referer、也没有自动 Content-Type 注入。** Content-Type 是发送期按 §4.3 分流隐式决定的；
需要 Referer 的书源必须自己在 headers 里写。

### 7.2 发送前的 cookie 步骤（setCookie）

每次真正发起请求前：

- `domain = NetworkUtils.getSubDomain(source?.getKey() ?: 最终url)`（PublicSuffix 归一到 eTLD+1，IP 原样）。
- `CookieStore.getCookie(domain)` 非空则与现有 `headerMap["Cookie"]` merge 后回写（URL 选项临时 Cookie
  优先于数据库 Cookie 的合并语义由 mergeCookies 决定）。
- 书源开启 `enabledCookieJar` 时写入内部标记头 `CookieManager.cookieJarHeader`，否则移除。

### 7.3 对外输出

- `headerMap: LinkedHashMap<String,String>` — 最终头集合（含 UA/Cookie/proxy 抽除后的结果）。
- `getUserAgent()` — headerMap 中的 User-Agent，缺省回落 AppConfig.userAgent。
- `getUrlAndHeaders(): Pair<url, headerMap>`（setCookie 之后）。
- `getGlideUrl()` — 图片加载用的 url+headers 包装。

---

## 8. 最终请求对象（Python 复刻必须产出的接口）

一个解析完成的 AnalyzeUrl 等价于以下不可变描述（建议 dataclass）：

```text
RequestSpec:
  method        : "GET" | "POST" | "HEAD"        # 仅三种；未知 method 一律 GET
  url           : str                            # 展开并绝对化后的完整 URL（GET 时含原始 query）
  url_no_query  : str                            # 去掉 '?' 之后的部分
  encoded_query : str | None                     # GET query 编码产物；None 表示无 '?'
  body          : str | None                     # 选项 body 规范化文本（§4.1）
  encoded_form  : str | None                     # 表单模式编码产物；非 None ⇒ 表单发送
  headers       : OrderedDict[str, str]          # §7 组装结果（有序、可含 Cookie）
  charset       : str | None                     # "escape" 为哨兵值
  type          : str | None                     # 二进制/文件类型标记
  retry         : int                            # >=0，总尝试 = retry+1（非 2xx 即重试）
  proxy         : str | None                     # http|socks4|socks5://host:port[@user@pass]
  use_web_view  : bool
  web_js        : str | None
  body_js       : str | None                     # 响应后处理 JS
  js            : str | None                     # 解析期已执行完毕（此处仅存档）
  dns_ip        : str | None
  server_id     : int | None
  web_view_delay_ms : int                        # max(0, v)
```

组装实际 HTTP 请求的规则（与 executeStrRequest/getResponseAwait 一致）：

- **GET**：目标 = `url_no_query`，query = `encoded_query`（整串替换式拼接，不再二次编码）。
- **HEAD**：同 GET 但 method=HEAD。
- **POST**：按 §4.3 三分支选 form/json/raw-body；URL 用 `url_no_query`。
- **type != None 时**：文本接口退化为「字节流 → hex 字符串」响应（`StrResponse(url, bytes.toHexString())`）。
- 重试循环：`for i in 0..retry`，拿到 2xx 立即返回，否则继续，最后一次的结果/异常兜底返回。
- 响应后处理：Content-Type 为 xml 但 body 不以 `<?xml` 开头 → 补 XML 声明；然后若有 `bodyJs`，
  以 `result=body` 执行并用其返回值替换 body。
- webView 分支、并发限速（`ConcurrentRateLimiter`，读取书源 concurrentRate 规则）、Cronet/DNS 定制、
  data URI 快捷解码（`urlNoQuery` 以 `data:` 开头时直接 Base64 解码返回）属于执行层，
  可作为独立模块边界，不属于 RequestSpec 本身。

### 复刻清单（最小验收面）

1. 三段管线顺序（@js → {{}} → 选项切分）与各正则逐字一致。
2. `<a,b,c>` 钳制语义、`{{}}` 整数 Double 格式化、`result`/`@result` 管线。
3. UrlOption 全键表 + method 白名单（PUT→GET 怪癖）+ webView 真值判定 + proxy 仅源级生效。
4. 表单/query 双编码路径（escape、checkEncoded、保守查询编码器安全表）。
5. POST 三分支与固定 Content-Type 值。
6. header 组装顺序、UA 缺省注入、登录头合并、setCookie 合并。
7. （可选兼容）ImportOldData 的旧语法迁移函数。
