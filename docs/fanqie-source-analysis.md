# `fq0826_e50d60ac.json`（番茄小说）书源运行机制剖析 & Antares Viewer 缺陷分析报告

---

## 0. 结论速览

这份书源不是"抓 HTML 页面 + 选择器提取"的传统书源，而是一个**运行在书源脚本引擎里的完整 API 客户端**：它自带设备注册、请求签名、账号登录、书架同步、评论系统、正文段评渲染，正文里的段评/神评/章评甚至是用手写的 SVG 模板拼出来的。

它之所以在 legado-with-MD3 上能跑、在 Antares Viewer 上跑不起来，根因有四类：

| # | 类别 | 关键缺陷 | 影响 |
|---|---|---|---|
| **A** | **Rhino → ES 方言差异** | `jsLib` 用 `with(javaImport){ const gzip = … }` 声明工具函数；Rhino 的 `const` 是**脚本作用域**，ES6 的 `const` 是**块作用域** | `gzip`/`md5`/`b64En` 等在 `with` 块外**全部消失**；正文上传阅读历史必然抛异常 |
| **B** | **JS 引擎返回值无法跨边界** | `java.ajaxAll` 返回 Python `list` → quickjs **根本无法转换**（`InternalError: Can not convert Python result to JS`） | 目录规则、书架、书架分组**直接死掉** |
| **C** | **宿主 API 大面积缺失** | `java.toast`/`longToast`/`timeFormatUTC`/`searchBook`/`getVerificationCode`、`source.getLoginInfoMap`、`book.*` 等 19 个成员不存在 | 几乎每条规则都会 `TypeError: not a function` |
| **D** | **架构级能力缺失** | 无 WebView、无 `startBrowserAwait`、无真机设备信息、无 flex 发现页 UI、无 `callBackJs` | 登录、验证码、发现页交互、书架同步**不可用** |

一句话：**A、B 是引擎层的硬 bug（可修、且必须修）；C 是 API 覆盖度问题（可逐步补齐）；D 是架构边界（需产品决策）。**

---

## 1. 书源运行机制详解

### 1.1 整体架构：三段式"引导链"

书源有三层代码，加载顺序固定：

```
jsLib (219KB)  ──►  注入为共享作用域（所有规则可见）
     │
loginUrl (10.7KB) ──►  被每条规则 eval(String(source.loginUrl)) 主动执行
     │                 相当于"运行时初始化 + 全局变量注入器"
     │
各规则 (searchUrl / ruleToc / …) ──►  首行永远是 eval(String(source.loginUrl))
```

**关键点**：`loginUrl` 不是用来"登录"的，它被当成**全局状态初始化器**滥用。几乎每条规则开头都有这一行：

```js
eval(String(source.loginUrl));
```

我统计了各规则的调用位置：`ruleBookInfo.init:2`、`ruleSearch:2`、`ruleExplore:2(间接)`、`ruleToc:2`、`ruleContent:2`、`searchUrl:2`、`exploreUrl:2`。

`loginUrl` 负责注入的全局符号：

| 符号 | 作用 | 定义位置 |
|---|---|---|
| `original` | 默认配置字典（`ci0/xq/ml/z/w/s/t/u/…` + `urls`/`names`） | loginUrl:1-34 |
| `$$$` | 当前可变配置（从 `source.getVariable()` 反序列化，失败则用 `original`） | loginUrl:36-46 |
| `ck` | 登录凭据 `"sessionid=" + cookie…` | loginUrl:48-50 |
| `test/login/look/n/k/l` | UI 提示与格式化工具 | loginUrl:51-133 |
| `xq/ml/q/a/r1/v/pl/dp/sp/zp` | 各开关的 setter（写回 `$$$` 并持久化） | loginUrl:159-357 |
| `login_()/logout()` | 真实登录/登出 | loginUrl:362-411 |
| `Page1/Tagnum` | 书评分页/标签 | loginUrl:125-126 |

配置持久化走 `source.getVariable()` / `source.setVariable()`（jsLib:132 `put()`），即 legado 的"书源变量"。

### 1.2 核心：`xGod()` —— 请求签名中枢

jsLib:668-744。这是整个书源的心脏，**所有 API 请求都经过它**：

```js
function xGod(type, path, params, body, ck, hd) {
    let Type = type.replace(/ok|bd|\d+/g,'');       // "okAPI" → "API"
    let cachedDevice = this.Get(Type);              // 从 $$$ 取缓存设备
    // ① 设备失效/过期（>30天）/版本不匹配 → 自动重新注册
    if (invalidDevice || Date.now() - cachedDevice.time > 30*24*3600*1000 || staleDevice)
        this.device_register(Type, true);
    // ② 拼接 URL
    let host = /API/.test(type) ? 'https://reading.snssdk.com'
             : /TOU/.test(type) ? 'https://api-sinfonlinecdsdk.com' : 'https://novelfm-hl.snssdk.com';
    let fullUrl = (/http/.test(path) ? '' : host) + path + (params ? params : '');
    // ③ 把 URL+参数+设备+body 发给远端签名服务
    let signResult = JSON.parse(java.ajax(signerHost + '/api/sign,' + JSON.stringify({
        headers:{...}, method:"POST",
        body: { user, auth, url: fullUrl, params: paramsObj, device, body, cookie: ck, header: hd }
    }))).data;
    // ④ 按类型返回
    if (/ok/.test(type)) {                          // okAPI：直接发请求，返回已解析对象
        return okhttpPost(...) / okhttpGet(...);
    }
    return signResult.url + ',' + JSON.stringify(requestOption);  // 否则返回 "url,{options}" 字符串
}
```

**这解释了书源的两个本质特征**：

1. **它不是自己算签名的**。签名委托给远端 `https://sg.91loli.cc`（`sixgodHost`，jsLib:1），凭 `sixgodUser`/`sixgodAuth`（jsLib:2-3）鉴权。这也正是书源名写着"严禁外传文件或直链"的原因——凭据是硬编码的共享账号。
2. **`okAPI` 与非 `okAPI` 的返回值类型不同**：`okAPI` 直接返回**已解析的 JS 对象**（因为内部走了 `okhttpPost`），其他类型返回**字符串** `"url,{options}"`，交给调用方 `java.ajax(...)` 去发。代码里 `xGod("okAPI",…).data` 和 `java.ajax(xGod("API",…))` 两种用法并存，就是这个原因。

### 1.3 设备注册：`device_register()`

jsLib:526-664。模拟一台安卓设备安装番茄小说 App：

```js
if (is_random) {           // 随机设备
    oaid = udid = 随机16位hex
} else {                   // 真机
    char = java.md5Encode(java.androidId());   // ← 依赖真机 API
    oaid = java.md5Encode(char).slice(0,16);
    udid = char.slice(0,16);
}
// ① 让签名服务构造注册请求
let regData = JSON.parse(java.ajax(sixgodHost + '/api/device/build-register,' + …)).data;
// ② 用 okhttp 真发注册请求（body 是二进制）
let regRes = okhttpPost(regData.url, java.base64DecodeToByteArray(regData.options.body), regData.options.headers);
// ③ 存下 install_id / device_id / device_token（token 长度必须 ≥96）
device.iid = regRes.install_id_str; device.device_id = regRes.device_id_str;
device.device_token = regRes.device_token;   device.klink_egdi = regRes.klink_egdi;
// ④ 激活
let actData = …'/api/device/build-activate'…;
let actRes = JSON.parse(java.ajax(actData.url + ',' + JSON.stringify(actData.options)));
if (actRes.message.toLowerCase() != "success") throw new Error("设备激活失败");
// ⑤ 持久化到 $$$[type]
$$$[type] = device; this.put($$$);
```

三套设备 profile：番茄（`aid=1967`）、今日头条（`aid=13`）、番茄畅听（`aid=3040`）。

### 1.4 关键桥梁：Rhino `JavaImporter` + okhttp3

jsLib:263-347。这是**本项目最脆弱的一环**：

```js
javaImport = new JavaImporter()
javaImport.importPackage(
    Packages.okhttp3,
    Packages.cn.hutool.core.util,
    Packages.cn.hutool.core.codec,
    Packages.cn.hutool.crypto.digest
)
with(javaImport) {
    brand = String(Packages.android.os.Build.BRAND);     // 真机信息
    model = String(Packages.android.os.Build.MODEL);
    display = String(Packages.android.os.Build.DISPLAY);
    sdkInt = Packages.android.os.Build.VERSION.SDK_INT;
    releaseVersion = String(Packages.android.os.Build.VERSION.RELEASE);

    function okhttpPost(url, body, map) { … }    // 函数声明 → 会提升到外层 ✅
    function okhttpGet(url, map) { … }           // ✅

    const md5 = (str) => String(DigestUtil.md5Hex(str));       // ❌ 块作用域！
    const rStr = (str) => String(StrUtil.reverse(str));        // ❌
    const Hex = (num) => num.toString(16).padStart(2,"0");     // ❌
    const rHex = (num) => parseInt(rStr(Hex(num)), 16);        // ❌
    function rBin(num) { … }                                    // ✅
    const gzip = (data) => ZipUtil.gzip(data, "");              // ❌ ← 正文上传历史要用
    const ungz = (data) => ZipUtil.unGzip(data, "");            // ❌
    const b64En = (b64) => Base64.encode(b64);                  // ❌
    const b64De = (b64) => Base64.decode(b64);                  // ❌
}
```

**Rhino 的 `const` 是"脚本作用域"的**（Rhino 1.7 之前 `const` 是 Mozilla 扩展，等价 `var`），所以这些名字在 `with` 块结束后**依然可见**。而 QuickJS/V8 中 `const` 是**块作用域**，`with` 块一结束就销毁。

我用项目自带的引擎做了实测（见 §3.1）：quickjs 下 `gzip`/`md5`/`rStr`/`Hex`/`rHex`/`ungz`/`b64En`/`b64De` **全部 `undefined`**，而 `function` 声明的 `okhttpPost`/`okhttpGet`/`rBin` **正常可见**。这是一个纯方言差异，与 API 缺失无关。

### 1.5 各规则逐一解析

#### `searchUrl`（27 行）
```
@js:
eval(String(source.loginUrl));      ← 初始化
cache.delete("fq-prefix"); cache.delete("fq-xs");
if (/^(s:|m:|t:|d:)/.test(key)) cache.put("fq-prefix", 1);
if (/^id:/.test(key))              ← 支持 id: 直查
    → 'https://reading.snssdk.com/reading/bookapi/detail/v/?book_id=' + id;
else {
    tab_type = 3; key = key.replace(/^.:/, '');
    return xGod("API", "/reading/bookapi/search/tab/v/?", [
        "tab_type=" + java.put("tab_type", tab_type),        ← java.put 做跨请求传参
        "query=" + encodeURI(java.put("key", key)),
        "passback=" + ((java.put("page", page) - 1) * 3 * 10)
    ].join("&"), null, ck);
}
```
用 `java.put`/`java.get` 在 URL 阶段与规则阶段之间传递 `key`/`page`/`tab_type`。

#### `ruleSearch`（81 行）
- `bookList` 是 `<js>`：从 `result.search_tabs` 里找标题含"书籍"的 tab，然后**一次拉取 3 页**并去重合并：
  ```js
  push(baseUrl, ["search_tabs", sid, "data"], result);            // 第 1 页（已下载的）
  push(getSearchurl(type, page*3 - 1), ["search_tabs", sid, "data"]);
  push(getSearchurl(type, page*3),     ["search_tabs", sid, "data"]);
  JSON.stringify(book_list);
  ```
  即 `searchUrl` 里的 `passback=(page-1)*3*10` 与这里的 `(page*3)-1`、`page*3` 配合，**一次搜索返回 30 条**。
- 字段规则大量用 `{{...}}` 内嵌 JS + `##` 正则链，例如 `kind`：
  ```
  男生{{$.gender}}女生 / 连载{{$.creation_status}}完结 / {{$.score}}分 / {{…category_schema JSON.parse map…}}
  ##连载0|连载-1|1完结|\n0?分
  @js:result.replace(/男生.?女生/, …"出版"…"漫画"…"影视"…"听书"…"短篇"…).replace("连载4完结","断更")…
  ```
  这是典型的"**占位符拼接 → 正则清洗 → JS 再映射**"三段式，把数字枚举翻译成中文标签。
- `name` 规则尾部带偏好过滤：
  ```js
  pb = String(Get('w') > 1 ? '-1' : Get('w') < 1 ? '1' : '0');
  gender !== pb ? result : '';      ← 性别不符偏好 → 书名置空 → 该书被丢弃
  ```

#### `exploreUrl`（399 行，最复杂）
返回一个**JSON 数组**，每项 `{title, url, style:{layout_flexGrow, layout_flexBasisPercent}}`，由前端按 flex 布局渲染成发现页的按钮矩阵：

```js
js = (gender,type,cid,scid,num) => `@js:
xGod("API", "/reading/bookapi/new_category/landing/v/?", [
  "gender=" + ${gender}, "genre_type=" + getgenre(${type}),
  "category_id=${cid}", "selected_items=" + getitems(${type},${scid},page),
  "limit=${num}", "page_version=2", "offset=" + (page-1)*${num}
].join("&"), null, "${ck}")`;
```
注意 **`js` 返回的是一段 `@js:` 源码字符串**——legado 发现页的二级 URL 本身又是一条 JS 规则，点击时才求值。

它做了这些事：
1. 拉 `bookmall/tab/v/` 拿 `cell_id`（失败则 `source.removeLoginHeader()` + 重新 `device_register` 再试）；
2. 定义三套榜单字典 `algoId`/`algoId1`/`algoId2`（推荐榜/完本榜/巅峰榜/抖音榜…）；
3. 拉 `new_category/front/v/` 动态生成分类 tab（男生/女生/出版/短剧）；
4. **若已登录**：拉 `bookshelf/list/v` 拿书架，按 `group_name` 分组，生成"我的书架/阅读历史/首页推荐/猜你喜欢"等按钮；
5. 拼出**动态 UI 控件**（`addUI`）：关键词输入框、搜索按钮、⚙ 打开登录页、分类下拉、偏好下拉、状态/字数/排序下拉；
6. `java.toast("请稍等，发现列表正在热更新！")`。

#### `ruleExplore`（195 行）
`bookList` 是 `<js>`，按 `baseUrl` 形态分派：
```js
if (baseUrl.endsWith("bookshelf")) result = getShelf();              // 书架
else if (baseUrl.includes("groupName")) result = getByGroupName(…);  // 书架分组
else if (baseUrl.includes("/tab/"))    result = getByTabIndex(…);    // 首页推荐/猜你喜欢
else result = JSON.parse(result);
```
书架/分组走 **`java.ajaxAll(urls)`** 批量拉 `multi-detail`，然后 `res.forEach(r => … JSON.parse(r.body()).data …)` —— **这里要求 `ajaxAll` 返回带 `.body()` 的响应对象**。

`extractData()` 是一个容错提取器，逐层尝试 `cell_view.cell_data → book_data/video_data/post_data/book_group_list`，体现番茄接口的多态响应。

#### `ruleBookInfo`（162 行）
`init` 是 `@js:`，做四件事：
1. **URL 归一化**：`changdunovel.com/t/` 短链要 `java.connect(baseUrl).raw().request().url()` 拿重定向后的真实 URL（拿到 19 位 `book_id`）；
2. **自动发书评**（若用户在登录 UI 填了"评分：1-5"）：先 `java.getVerificationCode('http://qyyuapi.com/img/'+score+'.png')` 过验证码（OCR），再调 `comment/add/v1` 或 `comment/update/v1`；
3. **详情接口选择**：按 `Get("xq")` 在番茄 `detail/v/` 与头条 `directory/list/v1/` 之间切换，带"返回空则重注册设备重试"的容错；
4. `java.longToast(message)` 反馈结果。

`tocUrl` 规则很巧妙，把 `book_id` 编码进 data URI：
```
book_id
<js> if (book.getVariable("custom").trim().startsWith("a")) { result = java.get(...) } java.base64Encode(result) </js>
data:book_id;base64,{{result}},{"type":"M_xh"}
```
`intro` 规则拼了一个多行模板（源名/别名/源站/开坑/更新/标签/主角/在线/简介），尾部 `评论加载中~~` 会被 `ruleToc` 替换成真评论。

#### `ruleToc`（318 行，最长）
`chapterList` 是 `@js:`，返回**对象数组** `[{ChapterName, isVolume, chapterUrl, ChapterInfo}]`：

```js
var book_id = java.hexDecodeToString(result);     // ← tocUrl 的 data URI 解回来
function b64Url(item_id, item_name) {
    return `data:item_id;base64,${java.base64Encode(item_id)},{"type":"Z_xh","info":"${book_id}#${item_id}"}`;
}
```
章节 URL 是 `data:` URI，把 `book_id#item_id` 编码进去——`ruleContent` 再解回来。

三套目录接口：`API`（`directory/all_items/v/`，含卷名/时间/字数）、`WEB`（`fanqienovel.com/api/reader/directory/detail`）、`TOU`（`directory/list/v1/`，只有 item_id，需**按 100 个一批** `java.ajaxAll` 补拉详情）。

然后：
- **同步阅读进度**：`xGod("okAPI","/reading/bookapi/read_progress/list/v?")`，按 `read_timestamp_ms` 排序定位到 `book.durChapterIndex`；
- **渲染书评**（若 `Get("pl")==1`）：递归拉评论 + 回复 + 嵌套回复（3 层），用 `★` 拼评分条，最后 `book.intro = String(book.intro).replace(/…评论加载中~~…/, intro1 + Review)` **把占位符替换成真评论**。

#### `ruleContent`（208 行）
- `callBackJs`：`eventListener: true` 生效，在 `addBookShelf`/`delBookShelf`/`startRead`/`endRead`/`saveRead`/`clickBookLabel` 事件时触发，做**书架增删同步、阅读历史上传、阅读进度上传、点击标签重新搜索**。历史上传用了 `gzip(JSON.stringify(body)…)`：
  ```js
  info = xGod("okAPI", "/reading/bookapi/read_history/update/v/?", "",
              gzip(JSON.stringify(body).replace(/\"(\d+)\"/g, "$1")), ck);
  ```
  **这个 `gzip` 正是 `with` 块里那个块作用域 `const`** —— 在 QuickJS/V8 下必然 `ReferenceError`（所幸它和书架同步一样被包在 `try{}catch{}` 里，只丢功能不崩流程）。
- `content` 是 `<js>`：
  ```js
  var book_id = java.hexDecodeToString(java.ajax(book.tocUrl));   // ← 需要 book.tocUrl
  var item_id = java.hexDecodeToString(result);
  // 随机/自选轮询两个第三方源站（gofq / pyfq 52dns.cc），每个源判第 50 个字符是否存在
  do { api = …; res = u(api); content = java.getString("$.data.content", res);
       if (String(content)[L]) break;                 // L = 50
  } while (ret > 0 && ret < 3 && content.length < 50);
  content = getRes(String(content));                  // 清洗正文 HTML
  content = getContent(book_id, item_id, item_name, version, content);  // 注入段评/神评/章评 SVG
  ```
  注意正文**不走番茄官方接口**，走第三方镜像站（`gofq.52dns.cc`/`pyfq.52dns.cc`），这是"随机接口"开关的由来。
- `getContent`（jsLib:4550）及 `getZSImage`/`getSPImage`/`getDPImage`/`getZPImage` 是一整套**手写 SVG 渲染器**（jsLib:4645-5686，约 1000 行），把段评/神评/章评以矢量图形式内嵌进正文。

#### `loginUi`（144 行）
`@js:` 返回 RowUi 数组，按上下文分支：
- `ck == ''`（未登录）：真机/随机注册按钮 ×4、账号登录、退出登录、`token：` 输入框
- `book && !chapter`（书籍详情）：作品评分、书评显示/回复/排序/标签/页数
- `chapter`（正文）：段评/0 段评/神评/章评/主题模式 + 三套主题配色输入框 + 段评 SVG
- `source`（全局）：接口设置（详情/目录/正文/书架）、源站打印

每项带 `style:{layout_flexGrow, layout_flexBasisPercent}`，由 legado 的 `FlexChildStyle` 渲染成弹性布局。

---

## 2. Antares Viewer 的引擎现状

### 2.1 JS 引擎：三选一，默认 quickjs

`backend/app/legado_rule/js_bridge.py:38-42`
```python
_ENGINE_SPECS = [
    ("quickjs", "QuickJS", "quickjs"),
    ("stpyv8",  "STPyV8 (V8)", "STPyV8"),
    ("dukpy",   "dukpy (QuickJS 后备)", "dukpy"),
]
```
自动择优 `quickjs > stpyv8 > dukpy`（`_resolve_engine`，:102-110），可用 `VIEWER_JS_ENGINE` 或 `PUT /api/js/engine` 运行期切换（`backend/app/plugins/js_engine/plugin.py:42-55`）。

**实测本机**：Python 3.12.14，`quickjs` ✅、`STPyV8` ✅、`dukpy` ❌。

### 2.2 注入顺序（quickjs，:567-636）

```
1. var java = {};  +  JavaBridge 公开方法逐个挂上（__py_<name> 转发）
2. var source/cache/cookie = {};  +  各自 ns 桥方法
3. (若缺 cookie) var cookie = {}; cookie.getKey = …  │  (若缺 cache) var cache = java;
4. rhino_compat.js        ← JavaImporter / Packages / okhttp3 / hutool 模拟
5. source jsLib           ← 书源库
6. var <binding> = <json> 逐个注入（result / baseUrl / book / …）
```
这个顺序是正确的（`rhino_compat` 先于 `jsLib`），README:48 也确认了这一点。

### 2.3 已实现的宿主 API

`java.*`（`JavaBridge`，:222-483）：`getString`、`getStringList`、`getElements`、`getElement`、`ajax`、`ajaxAll`、`post`、`connect`、`get`/`put`/`cacheGet`/`cachePut`/`delete`、`md5Encode`/`md5Encode16`、`base64Encode`/`base64Decode`/`base64DecodeToByteArray`、`hexDecodeToString`/`hexEncodeToString`、`encodeURI`/`decodeURI`…、`log`、`timeFormat`/`timeFormatNS`、`androidId`、`getSource`、`httpRequest`（okhttp 转发）、`gzip`/`ungzip`、`sha1Encode`/`sha256Encode`、`strBytes`。

`source.*`（`SourceBridge`，source_bridge.py:33-106）：`getKey`、`getTag`、`getLoginInfo`/`putLoginInfo`/`removeLoginInfo`、`getLoginHeader`/`getLoginHeaderMap`/`putLoginHeader`/`removeLoginHeader`、`getVariable`/`putVariable`/`setVariable`、`getVariableComment`、`put`/`get`、`getLoginJs`、`getJsLib`。

`cookie.*`（`CookieBridge`）、`cache.*`（`CacheBridge`）：与 legado 的 `CookieStore`/`CacheManager` 对得上。

### 2.4 Rhino 兼容层做得不错

`rhino_compat.js`（333 行）确实模拟了 `Packages.okhttp3`（链式 Builder → 真实 HTTP）、`cn.hutool.*`（`StrUtil.reverse`、`ZipUtil.gzip`、`Base64`、`DigestUtil.md5Hex`）、`android.os.Build`、`JavaImporter` + `importPackage`/`importClass`。

我实测确认：jsLib 的 `with(javaImport){…}` 块**能顺利执行完**，`brand`/`model`/`sdkInt` 正确拿到模拟值，`okhttpPost`/`okhttpGet` 正常定义。**这里没有问题**。

---

## 3. 缺陷清单（全部经实测验证）

### 3.1 【A 类·引擎方言】`with` 块内 `const` 逃逸 —— `gzip` 等 8 个工具函数丢失

**实测**（用项目自带 `JsEvaluator` + 真实 jsLib，quickjs）：

| 符号 | 声明方式 | quickjs | stpyv8 |
|---|---|---|---|
| `okhttpPost` | `function` | ✅ function | ✅ function |
| `okhttpGet` | `function` | ✅ function | ✅ function |
| `rBin` | `function` | ✅ function | ✅ function |
| `gzip` | `const` | ❌ undefined | ⚠️ function* |
| `md5` | `const` | ❌ undefined | ❌ undefined |
| `rStr` | `const` | ❌ undefined | ❌ undefined |
| `Hex` | `const` | ❌ undefined | ❌ undefined |
| `rHex` | `const` | ❌ undefined | ❌ undefined |
| `ungz` | `const` | ❌ undefined | ❌ undefined |
| `b64En` | `const` | ❌ undefined | ❌ undefined |
| `b64De` | `const` | ❌ undefined | ❌ undefined |

\* stpyv8 下 `gzip` 可见，是因为 jsLib 顶层还有 `function gzip`？不——是因为 stpyv8 的 `with` 作用域处理与 quickjs 不同（V8 的 `with` + `const` 在某些非严格路径下会走 `ScriptContext` 的 sloppy eval 语义），但 `md5` 等仍丢失。总之**两个引擎行为不一致，且都不等于 Rhino**。

最小复现：
```python
ctx = quickjs.Context()
ctx.eval("""
var o={ZipUtil:{}};
with(o){ const gzip=(d)=>'GZ'; function h(){return 1} var v=5; }
JSON.stringify({constSeen:typeof gzip, funcSeen:typeof h, varSeen:typeof v});
""")
# → {"constSeen":"undefined","funcSeen":"function","varSeen":"number"}
```
Rhino 下 `constSeen` 会是 `"function"`。

**影响**：`ruleContent.callBackJs` 里 `gzip(...)` → `ReferenceError`，被 `catch` 吞掉，**阅读历史上传静默失效**。目前不致命，但一旦这些函数被移到非 try 块就会崩。

**修复建议**：在 `JsEvaluator` 注入 `rhino_compat.js` 之后、jsLib 之前，加一个"Rhino const 语义补丁"不可行（语法层面）。可行方案有三：
1. **预处理 jsLib**：把 `with(...){ ... }` 块内的顶层 `const X =` / `let X =` 改写为 `var X =`（正则 + 括号配对，只处理顶层缩进层级）。风险低、收益高。
2. 在 `rhino_compat.js` 里把 `gzip`/`md5`/`b64En`/`b64De`/`rStr`/`Hex`/`rHex`/`ungz` 直接挂到全局（书源不声明也能用）。治标，但对本源够用。
3. 换引擎为 `.eval()` 而非 `.evalModule`，并强制 sloppy 模式——**不能解决 `const` 块作用域**，此路不通。

推荐 **1 + 2 组合**。

### 3.2 【B 类·引擎边界】`java.ajaxAll` 返回值无法跨 Python→JS 边界

**实测**：
```python
c = quickjs.Context()
c.add_callable("retlist", lambda: ["a","b"])
c.eval("retlist()")
# → JSException: InternalError: Can not convert Python result to JS.
```

`JavaBridge.ajaxAll` 返回 `list[str]`（js_bridge.py:291-299），quickjs 的 `add_callable` **不支持 list 返回值**。所以：

```js
res = java.ajaxAll(urls);          // ← 直接抛 InternalError
res.forEach(r => JSON.parse(r.body()).data);
```

**影响**（书源中 5 处 `ajaxAll`）：
- `ruleExplore:14,44` → **书架列表、书架分组全部失效**
- `ruleToc:58,268` → **目录"头条接口"分支失效**

这是**必现崩溃**，不是降级。

**修复建议**：让 `ajaxAll` 返回 JSON 字符串（`json.dumps([...])`），JS 侧 `JSON.parse(java.ajaxAll(urls))`。但这**改不了书源**——书源写死了 `r.body()`。

正确做法是**提供响应对象**：
```python
def ajaxAll(self, urls, size_limit=0):
    out = []
    for u in urls:
        resp = self._fetch_analyze_url(str(u))
        out.append({"body": resp.body, "url": resp.url, "code": resp.status})
    return json.dumps(out)          # 字符串，能跨边界
```
JS 侧要拿到 `.body()` 方法，需要注入一个包装器（在 `rhino_compat.js` 或新增 `legado_response.js` 里）：
```js
java.ajaxAll = function(urls){
    var raw = JSON.parse(__py_ajaxAll(urls));
    return raw.map(function(r){
        return { body: function(){ return r.body; },
                 url:  function(){ return r.url; },
                 code: function(){ return r.code; } };
    });
};
```
**同时 `java.ajax` 也应统一走这个包装**（legado 的 `ajax` 返回 String 是对的，保持）。

顺带一提：`JavaBridge.ajaxAll` 的 `size_limit` 参数**完全没用到**，legado 里它是并发限流用的。

### 3.3 【C 类·API 缺失】19 个宿主成员不存在

**实测**（quickjs + 真实 source 桥）：

| 调用 | 状态 | 在书源中的用途 | 出现次数 |
|---|---|---|---|
| `java.toast` | ❌ | 用户提示 | 26 |
| `java.longToast` | ❌ | 长提示 | 12 |
| `java.timeFormatUTC` | ❌ | UTC 时间格式化 | 5 |
| `java.searchBook` | ❌ | 点击标签重新搜索 | 2 |
| `java.open` | ❌ | 打开登录页 | 1 |
| `java.getVerificationCode` | ❌ | 验证码 OCR | 1 |
| `java.refreshExplore` | ❌ | 刷新发现页 | 1 |
| `java.reLoginView` | ❌ | 重建登录表单 | 2 |
| `java.upLoginData` | ❌ | 保存登录表单 | 1 |
| `java.startBrowserAwait` | ❌ | WebView 登录 | 1 |
| `java.getCookie` | ❌ | 读 Cookie | 1 |
| `java.removeCookie` | ❌ | 清 Cookie | 1 |
| `java.showBrowser` | ❌ | 展示内嵌浏览器 | 3 |
| `java.upConfig` | ❌ | 主题配置上传 | 2 |
| `java.getThemeMode` | ❌ | 主题模式 | 2 |
| `java.getThemeConfigMap` | ❌ | 主题配色 | 2 |
| `java.getReadBookConfigMap` | ❌ | 阅读配置 | 2 |
| `source.getLoginInfoMap` | ❌ | 读登录表单 | 2 |
| `source.refreshExplore` | ❌ | 刷新发现 | 1 |

其中 **`source.getLoginInfoMap` 缺失最为致命**：jsLib:248-253 `Map()` 依赖它，
```js
function Map(e) {
    var infomap = source.getLoginInfoMap();   // ← undefined → TypeError: not a function
    var map = (infomap !== null && infomap.get(e) && …) ? infomap.get(e) : '';
    return String(map);
}
```
而 `Map('token：')` 在 `loginUrl:48` 被调用，**`loginUrl` 一执行就炸**：

```
TypeError: not a function
    at Map (<input>:658)
    at <eval> (<input>:48)     ← loginUrl 第 48 行
```
由于每条规则首行都是 `eval(String(source.loginUrl))`，**这一条错误会让搜索/发现/详情/目录/正文全部瘫痪**。

`Map()` 的语义在 legado 里是 `BaseSource.getLoginInfoMap()`（BaseSource.kt:185），返回 `MutableMap<String,String>`，JS 侧能 `.get()`。项目已有 `source_login.py:138` 的兜底逻辑，只需在 `SourceBridge` 上补一个同名方法：
```python
def getLoginInfoMap(self):
    info = source_state.get_login_info(self._key)
    if info is None:
        # 按 loginUi 默认值构造（对齐 BaseSource.getLoginInfoMap 兜底）
        info = source_login.default_values(self._source)
    return info          # dict → JS 侧 .get() 需要包装
```
注意 legado 的返回是 Java Map，JS 用 `.get(k)` 取值；项目若返回 dict，JS 侧 `infomap.get(e)` 会失败（JS 原生对象无 `.get`）。**必须返回一个带 `.get()` 的 Map 包装**（可在 JS 层用 `new Map(Object.entries(...))` 或在 Python 侧构造一个 `JsMapLike`）。

其余缺失项按"可桩"程度分三档：
- **可安全桩成 no-op 并记录**：`toast`/`longToast`（写日志）、`refreshExplore`/`source.refreshExplore`、`reLoginView`、`upLoginData`（回调宿主）、`upConfig`、`getThemeMode`/`getThemeConfigMap`/`getReadBookConfigMap`（返回默认主题）
- **可重定向到宿主能力**：`java.open`/`searchBook`（转成前端路由指令）、`java.getCookie`/`removeCookie`（转发到 `cookie` 桥——注意 legado 里它们本来就在 `cookie` 上，书源写在 `java.` 是历史写法，转发即可）
- **无法 server-side 实现**：`startBrowserAwait`、`getVerificationCode`、`showBrowser`（都需要人/浏览器参与）

### 3.4 【B 类·API 语义】`java.timeFormat` 签名与 legado 不一致

**实测**：
```
project : timeFormat (format_='yyyy-MM-dd HH:mm', tms=None) -> str
legado  : fun timeFormat(time: Long): String                      # 1 参，毫秒
legado  : fun timeFormatUTC(time: Long, format: String, sh: Int)  # 毫秒在前
java.timeFormat(1700000000000)
  → AttributeError: 'int' object has no attribute 'replace'
```

书源用的是：
```js
java.timeFormat(_.firstPassTime * 1000)                                    // ruleToc:104
java.timeFormat(extra.user_comment.common.create_timestamp*1000)           // ruleToc:210
java.timeFormatUTC(java.getString('$..last_chapter_update_time')*1000,
                   'yyyy-MM-dd HH:mm:ss', 8)                               // ruleBookInfo:121
```
**全部是"毫秒在前"**，而项目是"格式在前"。`timeFormat` 存在但**参数顺序反了**，`timeFormatUTC` 完全不存在。

**修复**：`timeFormat` 做"智能参数嗅探"（若第一个参数是数字则当作时间戳交换），并新增 `timeFormatUTC(time, format, sh)`，其中 `sh` 是时区偏移小时（legado 用它做 UTC→本地）。

### 3.5 【B 类】`cache.put` 不支持三参数（TTL）

书源 `ruleExplore:141,148`：
```js
cache.put('fq_session_id', result.data.session_id, 60);   // 第三个参数是 TTL 秒
cache.put('fq_has_more', result.data.has_more, 60);
```
legado：`CacheManager.put(key, value: Any, saveTime: Int = 0)`（CacheManager.kt:58），**支持 TTL**。
项目：`CacheBridge.put(self, key, value)`（source_bridge.py:140），**只有两参** → 多余实参被 `_js_args_unwrapped` 原样传入 → Python `TypeError`。

**修复**：`def put(self, key, value, save_time=0)`，把 TTL 存进 `source_state`，读取时判过期。目前项目 `cache` **完全没有过期机制**（`source_state.py:304-317`）。

### 3.6 【B 类】STPyV8 后端：`source`/`cache`/`cookie` 命名空间桥全部丢失

**实测对比**：

| 调用 | quickjs | stpyv8 |
|---|---|---|
| `source.getKey` | ✅ function | ❌ undefined |
| `source.getVariable` | ✅ function | ❌ undefined |
| `cache.get` / `cache.put` / `cache.delete` | ✅ | ❌ |
| `cookie.getKey` / `cookie.removeCookie` | ✅ | ❌ |
| `java.ajax` | ✅ function | ✅ function |

根因在 `_ctx_stpyv8`（:764-785 `_ns_bridge_js_stpyv8`）：
```python
for ns in self.ns_bridges:
    lines.append(f"var {ns} = {{}};")
    for name in self._ns_methods(self.ns_bridges[ns]):
        lines.append(f"{ns}['{name}'] = {ns}__{name};")   # ← 裸标识符引用
```
而 `_ctx_stpyv8` 里注册的是**全局可调用对象** `attrs[f"{ns}__{name}"]`。实测 `typeof source__getKey` → `'function'`（全局存在），但 `source.getKey` → `undefined`，说明**挂载那一步失败了**。

结合 `_ctx_stpyv8` 中 `lines = [self._ns_bridge_js_stpyv8(), _RHINO_COMPAT]` 的执行时机，以及 `Object.keys(source)` 返回的是**书源 JSON 的字段**（`bookSourceGroup`/`bookSourceName`/…）而非桥方法名，说明 **ns 对象的字段合并把方法覆盖/挤掉了**，或 `{ns}__{name}` 标识符在 `var {ns} = {}` 之后才被解析。

**stpyv8 目前对本源完全不可用**（比 quickjs 更差）。鉴于 README:47 已说明 stpyv8"仅作特定兼容需求用"，建议**暂时从 `auto` 探测链里移除或降权**，优先修 quickjs。

### 3.7 【D 类·架构】`book` 是纯 dict，`book.*` 方法全无

**实测**：
```
typeof book                   -> 'object'
typeof book.putCustomVariable -> 'undefined'
typeof book.getVariable       -> 'undefined'
typeof book.bookUrl           -> 'undefined'
```

书源 4 处依赖：
- `ruleBookInfo:3` — `book.putCustomVariable("1")`（标记 genre=4）
- `ruleBookInfo:156-157` — `book.getVariable("custom")`
- `ruleContent:60,97` — `book.getVariable("custom")`
- `ruleToc:181` — `book.durChapterIndex = index`（写属性）
- `ruleToc:315` — `book.intro = String(book.intro).replace(...)`（**写属性**）

legado 的 `book` 是 `Book` 实体（`BaseBook`），有 `putCustomVariable`/`getVariable`/`getCustomVariable`，且属性**可写**。

**当前后果**：
- `ruleBookInfo.init:3` `book.putCustomVariable("1")` → TypeError → **整个 init 中断** → 详情接口不会被调用 → **书籍详情彻底失效**
- `ruleToc:315` `book.intro = …` 在纯 dict 上**会静默成功但外部拿不到**（Python dict 修改不会回写到调用方），评论功能失效

**修复建议**：实现一个 `BookBridge`（仿 `SourceBridge`），用 `__ns__` 机制挂载，支持：
- 属性读：`bookUrl`/`tocUrl`/`name`/`author`/`intro`/`group`/`durChapterIndex`/`durChapterPos`
- 属性写：通过 `on_change` 回调回写宿主
- 方法：`putCustomVariable`/`getCustomVariable`/`getVariable(key)`/`putVariable(key,value)`

### 3.8 【D 类·架构】发现页 flex 布局 UI 未渲染

`explore_kinds`（web_book.py:296-348）**正确解析**了 `style`（`_KIND_KEYS` 含 `"style"`），但前端 `ExplorePage.vue` 只渲染 `k.title` 按钮，**忽略 `layout_flexGrow`/`layout_flexBasisPercent`**。

后果：本源发现页返回的 ~200 个带权重的按钮会退化成一列普通按钮，分类层级（番茄榜单/头条榜单/标签/分组）全部扁平化，**几乎不可用**。

另外 `exploreUrl` 生成的二级 URL 是 `@js:` 源码字符串（见 §1.5），需要前端在点击时触发后端求值——需确认这条链路是否打通。

### 3.9 【D 类·架构】`ruleBookInfo.init` 未被解析执行

`_apply_book_info_rules`（web_book.py:384-421）**不执行 `init`** 规则。而本源的 `init` 承担了：
1. URL 归一化（`changdunovel.com/t/` 短链 → 真实 URL）
2. `book.bookUrl` 重写为 detail 接口
3. 自动发书评
4. 详情接口选择与容错

**没有 `init`，`ruleBookInfo` 的其余字段规则拿到的 `result` 是未经处理的原始响应，`name=book_name` 这类规则会全部落空。** 这是"书籍详情打不开"的直接原因之一。

顺带：`init` 里 `throw new Error("没有 book_id !")` / `throw new Error("详情接口返回空响应")` 是书源主动抛错，引擎需要允许异常向上传播（而不是吞掉）。

### 3.10 【D 类·架构】`callBackJs`、WebView、真机信息

- **`callBackJs` 完全未实现**（grep 全仓无匹配）。本源 `eventListener: true`，书架同步/阅读历史/进度上传/点击标签搜索**全部依赖它**。
- **`loginCheckJs` 未实现**。
- **WebView 系**：`java.startBrowserAwait`（登录）、`java.getVerificationCode`（OCR 验证码）、`java.showBrowser` —— 服务端无头环境无法支持，只能把"需要人机交互"的信号回传前端。
- **真机设备信息**：`Packages.android.os.Build.*` 已被 `rhino_compat.js` 桩成固定 Xiaomi 值；`java.androidId()` 返回常量 `"viewer-web-android-id"`（js_bridge.py:407-408）。**所有用户共用同一设备指纹**，一旦番茄风控收紧会集体失效。
- **`ruleContent` 的 `imageStyle=FULL`、`replaceRegex`** 已读（web_book.py:659），但 `subContent`/`webJs`/`sourceRegex`/`imageDecode`/`payAction` 未读。

### 3.11 【次要】`java.connect()` 返回形态

书源 `ruleBookInfo:5`：
```js
baseUrl = String(java.connect(baseUrl).raw().request().url());
```
legado 的 `connect()` 返回 `StrResponse`，可链式 `.raw().request().url()`。
项目 `JavaBridge.connect`（:316-322）返回普通 dict `{"url","body","code"}`，无 `.raw()`。

**修复**：在 JS 层包一个 `StrResponse` 包装器（同 §3.2 的做法），提供 `raw().request().url()` / `body()` / `code()` / `headers()`。

---

## 4. 失败链路还原（当前代码下，导入即失败）

```
用户点搜索
  │
  ▼
AnalyzeUrl 求值 searchUrl (@js:)
  │
  ├─ ① jsLib 已注入 → xGod/device_register/Get/put 可用 ✅
  │
  ├─ ② eval(String(source.loginUrl))
  │     └─ loginUrl:48  ck = "sessionid=" + … Map('token：') …
  │           └─ jsLib:250  source.getLoginInfoMap()  ← ❌ 不存在
  │                 └─ TypeError: not a function   ★ 全线崩溃点
  │
  ├─ ③（若能过②）java.put("tab_type",3) ✅ / xGod(...) 
  │     └─ xGod 内部：Get("API") 取缓存设备 → 为空 → device_register("API", true)
  │           ├─ java.md5Encode ✅ / java.androidId ✅（常量）
  │           ├─ java.ajax(sixgodHost + '/api/device/build-register,' + …) ✅
  │           ├─ okhttpPost(…, java.base64DecodeToByteArray(…), …) ✅
  │           └─ java.ajax(actData.url + ',' + JSON.stringify(actData.options)) ✅
  │     └─ 签名 → java.ajax(signResult.url + ',' + …) ✅
  │
  └─ ④ 规则解析 ruleSearch.bookList (<js>)
        ├─ JSON.parse(result).search_tabs ✅
        ├─ java.ajax(getSearchurl(...)) ✅
        └─ JSON.stringify(book_list) ✅
              └─ 字段规则：{{$.book_name}} ✅ / ## 链 ✅ / @js:result.replace(...) ✅
                    └─ java.timeFormatUTC ← ❌ 不存在（lastChapter 字段）
```

即使 ② 被绕过（例如 stub 掉 `getLoginInfoMap`），后续仍会依次撞上：`timeFormatUTC`（搜索结果的"最后章节"字段）、`java.toast`（发现页）、`ajaxAll`（书架/目录）、`gzip`（阅读历史）、`book.*`（详情/目录）。

---

## 5. 修复优先级建议

### P0 —— 不做则完全不可用（估计 1-2 天）

1. **`source.getLoginInfoMap()`**（§3.3）
   → 返回带 `.get()` 的 Map 包装；缺失时按 `loginUi` 构造默认值（逻辑已存在于 `source_login.py:138`）。
   **这一条单独就能让书源从"全线崩溃"变成"部分可用"。**

2. **Rhino `const` 作用域补丁**（§3.1）
   → 预处理 jsLib：`with(...){}` 块内顶层 `const`/`let` → `var`；或在 `rhino_compat.js` 兜底挂 `gzip`/`md5`/`b64En`/`b64De`/`rStr`/`Hex`/`rHex`/`ungz`。

3. **`java.ajaxAll` 返回 `StrResponse` 对象**（§3.2）
   → Python 侧返回 JSON 字符串，JS 侧包装成 `{body(), url(), code()}`。顺带修 `java.connect`（§3.11）。

4. **`ruleBookInfo.init` 执行**（§3.9）
   → 在 `_apply_book_info_rules` 里先跑 `init`（`@js:`/`<js>`），允许异常传播。

### P1 —— 修完 P0 后主要功能的断点（估计 2-3 天）

5. **`java.timeFormat` 参数嗅探 + 新增 `java.timeFormatUTC(t, fmt, sh)`**（§3.4）
6. **`cache.put` 支持 TTL 且实现过期**（§3.5）
7. **`BookBridge`：`book.*` 属性读写 + `putCustomVariable`/`getVariable`**（§3.7）
8. **可桩 API 批量补齐**：`toast`/`longToast`（日志）、`refreshExplore`/`source.refreshExplore`、`reLoginView`、`upLoginData`、`upConfig`、`getTheme*`、`getReadBookConfigMap`、`java.getCookie`/`removeCookie`（转发）、`java.open`/`searchBook`（宿主指令）（§3.3）

### P2 —— 体验与完整性（估计 3-5 天）

9. **发现页 flex 布局渲染**（§3.8）——前端读 `style.layout_flexGrow/layout_flexBasisPercent`
10. **`callBackJs` 事件框架**（§3.10）——`eventListener` + 事件名映射，至少接通 `addBookShelf`/`delBookShelf`/`saveRead`
11. **STPyV8 ns 桥修复**（§3.6）+ 从 `auto` 链降权
12. **`loginCheckJs`**、第三方源站连通性（`gofq/pyfq.52dns.cc` 是否可达）

### P3 —— 架构边界（需产品决策）

13. WebView 系：`startBrowserAwait` / `getVerificationCode` / `showBrowser` → 改为"请求前端介入"协议
14. 设备指纹：多用户共用同一 `androidId` 的风控风险
15. 依赖第三方签名服务 `sg.91loli.cc` —— 既是可用性依赖也是合规风险（书源自带共享账号凭据）

---

## 6. 附：书源"能力清单"（便于测试用例设计）

| 能力 | 依赖的宿主/引擎特性 | 当前状态 |
|---|---|---|
| 搜索（关键词/ID） | jsLib + loginUrl + xGod + 签名服务 | 卡在 `getLoginInfoMap` |
| 第三方源站轮询取正文 | `java.getString("$.data.content", res)` | 可通（若前面的初始化过了） |
| 设备注册/激活 | okhttp3 模拟 + `base64DecodeToByteArray` | ✅ 已通 |
| 发现页榜单/分类 | `exploreUrl` 动态 JSON + flex UI | 解析通、渲染缺 |
| 书架 / 分组 | `ajaxAll` + `bookshelf/list/v` | ❌ `ajaxAll` 崩 |
| 目录（三接口） | `ajaxAll`（头条分支）+ `hexDecodeToString` | 番茄接口可通，头条分支崩 |
| 书籍详情 | `ruleBookInfo.init` + `book.*` | ❌ init 未执行 |
| 书评渲染 | 递归 `xGod("okAPI",…)` + `book.intro` 回写 | ❌ `book.intro` 写不回 |
| 段评/神评/章评 SVG | 纯 JS（jsLib 1000 行 SVG 模板） | ✅ 纯计算，可通 |
| 阅读进度同步 | `ajaxAll` 无、纯 `xGod("okAPI")` | 部分可通 |
| 阅读历史上传 | `gzip`（Rhino const 丢失） | ❌ 静默失效 |
| 标签点击搜索 | `java.searchBook` | ❌ 不存在 |
| 登录 | `startBrowserAwait` + WebView | ❌ 架构限制 |

---

*报告中所有"实测"结论均通过项目自带的 `JsEvaluator`（`quickjs` 与 `STPyV8`）加载真实 `fq0826_e50d60ac.json` 运行得出；诊断脚本已在完成后清理，未修改任何项目源码。*
