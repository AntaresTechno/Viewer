# Legado 书源规则引擎实现规格（供 Python 重实现）

> 本文档从以下 Kotlin 源码中逐行提取，是**唯一权威依据**（以本仓库 `legado-with-MD3-main` 为准，与上游 legado 可能有差异）：
>
> - `app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeRule.kt`（顶层调度、模式判定、JS 集成）
> - `app/src/main/java/io/legado/app/model/analyzeRule/RuleAnalyzer.kt`（通用词法切分器）
> - `app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeByJSoup.kt`（jsoup 迷你选择器）
> - `app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeByJSonPath.kt`（Jayway JSONPath）
> - `app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeByXPath.kt`（JsoupXpath / SeimiCrawler JXDocument）
> - `app/src/main/java/io/legado/app/model/analyzeRule/AnalyzeByRegex.kt`（纯正则链）
>
> 辅助常量：`app/src/main/java/io/legado/app/constant/AppPattern.kt`；URL 侧：`AnalyzeUrl.kt`。
> 本文不写 Python 代码，只给精确语法与行为规格。

---

## 1. 总体架构

```
AnalyzeRule（门面 + 模式判定 + JS 引擎绑定）
 ├── AnalyzeByJSoup     Mode.Default   （默认，HTML/XML → jsoup Element）
 ├── AnalyzeByJSonPath  Mode.Json      （Jayway JsonPath）
 ├── AnalyzeByXPath     Mode.XPath     （SeimiCrawler JsoupXpath）
 ├── AnalyzeByRegex     Mode.Regex     （纯正则链，仅 getElement(s) 与模板规则）
 └── Rhino JS           Mode.Js / Mode.WebJs
```

- `enum class Mode { XPath, Json, Default, Js, Regex, WebJs }`（AnalyzeRule.kt:783）。
  **不存在** `MODE_JSOUPLIST` 之类的外部常量——那是旧版本的命名，本版本只有这个枚举。
- 内容通过 `setContent(content, baseUrl)` 注入。**content 不允许为 null**（直接 `AssertionError`）。

### 1.1 isJSON 判定（决定 JSON 自动模式）

```kotlin
isJSON = when (content) {
    is Node -> false                      // jsoup Node 一律不是 JSON
    else -> content.toString().isJson()
}
// StringExtensions.isJson():
str.startsWith("{") && str.endsWith("}") || str.startsWith("[") && str.endsWith("]")
// （先 trim()；只看首尾字符，不校验合法性）
```

即：字符串 trim 后首尾为 `{}` 或 `[]` 即认为内容是 JSON。`setContent` 会清空三个分析器缓存实例
（同一 content 的分析器惰性创建并复用；传入非当前 content 的对象时每次新建）。

### 1.2 分析器输入适配

| 分析器 | 输入适配 |
|---|---|
| AnalyzeByJSoup | `Element` 直通；`JXNode`（元素则 asElement，否则 toString 再 parse）；字符串以 `<?xml` 开头（忽略大小写）→ `Parser.xmlParser()`，否则 `Jsoup.parse(str)` |
| AnalyzeByXPath | `JXNode` 元素直通；jsoup `Document/Element/Elements` 包成 `JXDocument`；字符串若以 `</td>` 结尾 → 补 `<tr>…</tr>`；以 `</tr>` 或 `</tbody>` 结尾 → 补 `<table>…</table>`；`<?xml` 前缀 → xmlParser；否则 `JXDocument.create(html)` |
| AnalyzeByJSonPath | `ReadContext` 直通，否则 `JsonPath.parse(obj)`（Jayway 默认配置） |

---

## 2. 规则模式自动检测与显式前缀

模式判定发生在 `AnalyzeRule.SourceRule` 的 `init` 中（AnalyzeRule.kt:598-632），**按以下顺序做首个命中的分支**：

```kotlin
rule = when {
    mode == Mode.Js || mode == Mode.Regex -> ruleStr          // 已是 Js/Regex 则原样保留
    ruleStr.startsWith("@CSS:", true) -> { mode = Mode.Default; ruleStr }   // 忽略大小写；前缀不剥离！
    ruleStr.startsWith("@@")           -> { mode = Mode.Default; ruleStr.substring(2) } // 大小写敏感
    ruleStr.startsWith("@XPath:", true)-> { mode = Mode.XPath;  ruleStr.substring(7) } // 忽略大小写
    ruleStr.startsWith("@Json:", true) -> { mode = Mode.Json;   ruleStr.substring(6) } // 忽略大小写
    isJSON || ruleStr.startsWith("$.") || ruleStr.startsWith("$[") ->
                                          { mode = Mode.Json;   ruleStr }        // 大小写敏感
    ruleStr.startsWith("/")            -> { mode = Mode.XPath;  ruleStr }        // "//x" "/html/x"
    else -> ruleStr                                                    // Default(jsoup)
}
```

精确要点：

| 前缀 | 大小写 | 剥离 | 结果模式 |
|---|---|---|---|
| `@CSS:` | **忽略** | **不剥离**（保留整串；由 AnalyzeByJSoup 内部的 `SourceRule` 再剥前 5 字符并置 `isCss=true`） | Default（CSS 全选择器子模式） |
| `@@` | 敏感 | 剥 2 字符 | Default（传统 CSS 标记） |
| `@XPath:` | **忽略**（`@xpath:` 合法） | 剥 7 字符 | XPath |
| `@Json:` | **忽略**（`@json:` 合法；注意不是 `@JSON:` 特判，是 ignoreCase） | 剥 6 字符 | Json |
| `$.` / `$[` | 敏感 | 不剥离 | Json |
| （content 是 JSON） | — | 不剥离 | **所有无前缀规则都进 Json 模式** |
| `/` | 敏感 | 不剥离 | XPath（注释原文："XPath特征很明显,无需配置单独的识别标头"） |
| 其他 | — | — | Default = jsoup 迷你语法 |

- **顺序即优先级**：`@CSS:` 在 `@@` 之前；`@Json:` 在 `$.` 之前（因此 `@json:$.a` 先被前缀分支命中并剥掉 `@json:`，剩下 `$.a` 保持 Json 模式）。
- 判定基于**剥掉 @put 之后？否** —— 前缀判定先于 `splitPutRule`，即 `@put:{...}` 出现在串首会破坏前缀识别（罕见，不必兼容）。
- `startsWith("/")` 意味着任何以 `/` 开头的 jsoup 规则会误入 XPath——这是上游固有行为。

### 2.1 Regex 模式的两种来源

1. **显式 allInOne**：`splitSourceRule(ruleStr, allInOne=true)` 且 `ruleStr.startsWith(":")` → 整条规则置 `Mode.Regex`，`start=1` 跳过冒号，同时置实例级 `isRegex = true`。
   - **粘性陷阱**：`isRegex` 是 `AnalyzeRule` 实例字段，一旦置位，之后该实例上所有 `splitSourceRule` 产生的段都默认 `Mode.Regex`。Python 实现要么复刻要么明确放弃此行为（推荐复刻，某些书源依赖）。
2. **自动触发**（SourceRule.init 内，见 §4）：规则文本中出现 `$1`~`$99` 回引或 `{{…}}`/`@get:{}` 模板时转 Regex 模式。**这里的 "Regex 模式" 并不是"用正则解析内容"，而是"本段是模板/回引规则，在 makeUpRule 阶段就地展开成字符串"**。真正用正则抓内容的只有 `getElement/getElements`（§5.4）。

---

## 3. splitSourceRule：把整条规则切成 SourceRule 序列

`splitSourceRule(ruleStr, allInOne=false)`（AnalyzeRule.kt:528-572）：

1. 若 `allInOne && ruleStr.startsWith(":")` → mMode=Regex、start=1、isRegex=true；
   否则若实例 `isRegex==true` → mMode=Regex。
2. 用 `JS_PATTERN` 扫描 `<js>…</js>` 与 `@js:`：

```kotlin
// AppPattern.kt
val JS_PATTERN = Regex("<js>([\\w\\W]*?)</js>|@js:([\\w\\W]*)", RegexOption.IGNORE_CASE)
val WebJS_PATTERN = Regex("@webjs:([\\w\\W]{5,})", RegexOption.IGNORE_CASE)
```

   - `<js>(…)</js>` 非贪婪，取 group1；`@js:` **贪婪吃到规则末尾**，取 group2。忽略大小写。
   - 两段 JS 之间的普通文本（trim 后非空）作为一个 `SourceRule(text, mMode)`；JS 体本身**不做 trim**，作为 `SourceRule(body, Mode.Js)`。
3. 再对**原始字符串**跑 `WebJS_PATTERN`（共享同一个 start 游标）：`@webjs:` 后至少 5 个字符才成段，段类型 `Mode.WebJs`。由于 `@js:` 贪婪吞到末尾，`@webjs:` 只能出现在 `@js:` 之前才会生效。
4. 尾部剩余文本（trim 后非空）追加为 `SourceRule(text, mMode)`。

结果是一个**有序管道**：各段依次执行，前一段输出（result）作为下一段输入（§5）。跨模式混合正是靠这个分段实现的，
例如 `$.data@css:.title@text` 不是合法混用，但 `$.list` + `<js>…</js>` + jsoup 段的管道是合法的。
另注意：`getString/getStringList` 对完整规则串有缓存（§14），`getElement/getElements` 直接调用 splitSourceRule 不走缓存。

---

## 4. SourceRule 内部解析：@put / @get:{…} / {{…}} / $N / ## 后缀

每个 SourceRule 构造时（即使 mode 已定）继续做四步加工：

### 4.1 分离 `@put`

```kotlin
private val putPattern = Regex("@put:(\\{[^}]+?\\})", RegexOption.IGNORE_CASE)
```
所有匹配从规则文本中删除；花括号内文本先按严格 Gson 解析为 `Map<String,String>`，失败再按宽松 Gson 重试（宽松成功时打一次日志）。键值随后在执行每段前经 `putRule(putMap)` 处理：对每个 `(key,value)` 执行 `put(key, getString(value))` —— 即 put 的值本身可以是嵌套规则，针对当前 result 所在内容求值后存入变量存储（chapter → book → ruleData → source 四级，先命中先用）。

### 4.2 evalPattern 切分（模板参数化）

```kotlin
private val evalPattern =
    Regex("@get:\\{[^}]+?\\}|\\{\\{[\\w\\W]*?\\}\\}", RegexOption.IGNORE_CASE)
```

先看**第一个**匹配：
```kotlin
val firstMatch = evalPattern.find(rule)
if (firstMatch != null) {
    tmp = rule.substring(start, firstMatch.range.first)
    if (mode != Mode.Js && mode != Mode.Regex &&
        (firstMatch.range.first == 0 || !tmp.contains("##"))) {
        mode = Mode.Regex       // 含 {{}}/@get:{} 且(位于开头 或 其前文无"##") ⇒ 转模板模式
    }
}
```
即：只要规则里出现 `{{…}}` 或 `@get:{…}`，且它出现在位置 0 或它前面的文本不含 `##`，本段就变成 Regex（模板）模式。
反过来说 `xxx##re##rep{{$.a}}` 这种 `{{}}` 出现在 `##` 之后的不会改变模式。

然后遍历全部匹配，切出参数序列 `ruleParam`+`ruleType`：

- `@get:{key}`（忽略大小写）→ type=-2，param=`key`（去掉 `@get:{` 与 `}`）。
- `{{…}}` → type=-1，param=花括号内 JS 文本（`[\w\W]*?` 非贪婪，**不支持嵌套 `}}`**）。
- 匹配之间的普通文本 → 交给 `splitRegex` 继续拆 `$N`。

### 4.3 splitRegex：拆 `\$\d{1,2}` 回引

```kotlin
private val regexPattern = Regex("\\$\\d{1,2}")
```
只对**第一段**（`rule.split("##")` 的 `[0]`，即替换后缀之前的部分）扫描：
- 若含 `$N` 且 mode 不是 Js/Regex → mode 转 Regex。
- 每个 `$N` 记为 type=N（整数）、param=原词；两侧普通文本记为 type=0。
- 注意：`$N` 只认 1–2 位数字（最大 $99）；`$$1` 这类不特殊处理。

### 4.4 makeUpRule(result)：反向组装模板

执行每段规则前调用（getString/getStringList/getElement 都调，**getElements 不调**）。逻辑（AnalyzeRule.kt:709-769）：

- 从**最后一个参数往前**（`while (index-- > 0)`），逐个 `infoVal.insert(0, …)`：
  - `type > 0`（`$N` 回引）：若当前 result 是 `List<String?>` 且 `size > N` → 插入 `list[N]`（null 跳过）；否则插入**原词面**（如字面 `$1`）。
  - `type == -1`（`{{js}}`）：先判 `isRule(param)` —— param 以 `'@'`、`$.`、`$[` 或 `//` 开头则**当作规则**递归求值：`getString(getOrCreateSingleSourceRule(param))`（对当前 content 求值）；否则当 JS：`evalJS(param, result)`，返回值 null→跳过；String→原样；Double 且整除 1→`%.0f` 格式化（避免 1.0）；其他→toString。
  - `type == -2`（`@get:{}`）→ 插入 `get(key)`（变量读取，特例 key：`bookName`→book.name，`title`→chapter.title；查找顺序 chapter 变量→book 变量→ruleData 变量→source 变量→""）。
  - `type == 0` → 原文插入。
- 组装完成后 `rule = infoVal.toString()`（仅当原本就有参数时才替换）。
- 最后统一分离正则后缀（见 §4.5）。

**关键理解**：Regex 模式段的“执行”就是把模板展开后的字符串直接当作该段输出（§5 中 when 的 `else -> rule` 分支）。

### 4.5 `##regex#replacement` 正则替换后缀

makeUpRule 末尾：

```kotlin
val ruleStrS = rule.split("##")
rule = ruleStrS[0].trim()               // 主规则（去两端空白！）
if (ruleStrS.size > 1) replaceRegex = ruleStrS[1]
if (ruleStrS.size > 2) replacement  = ruleStrS[2]
if (ruleStrS.size > 3) replaceFirst = true
```

- 语法：`主规则##match##replacement`，第三种形态 `主规则##match##replacement###`（第 4 段存在，无论内容是什么）→ 仅替换**第一处**匹配。
- **没有 flag 字母机制**（没有 i/s/m 位）。需要忽略大小写请在 regex 里内联写 Java 正则开关如 `(?i)`。
- 替换执行 `replaceRegex(result, rule)`（AnalyzeRule.kt:480-503）：
  - 正常路径：`result.replace(regex, replacement)` —— Java/Kotlin 语义，replacement 里 `$1..$9` 为捕获组引用，`\` 转义；空 replacement 即删除匹配。列表结果时**逐项** toString 后各自替换。
  - `replaceFirst` 路径：取 `regex.find(result)` 的**第一个**匹配，对其 value 做 `replaceFirst(regex, replacement)`；**找不到匹配时整体返回 ""**（不是原串！）。
  - regex 编译失败（regexCache 得到 null）时降级：普通路径做**字面量** `result.replace(replaceRegex, replacement)`；replaceFirst 路径直接返回 replacement。
- 纯过滤规则（主规则为空但有 `##…`）在 getString 循环里仍会执行（条件 `if (rule.isNotBlank() || sourceRule.replaceRegex.isEmpty())` 保证），效果是对当前 result 应用替换。
- `##` 切分发生在**模板组装之后**，所以模板生成的文本里若含 `##` 也会被再次切分——这是已知边界行为。

---

## 5. 顶层取值函数语义

### 5.1 getString(ruleStr/ruleList, mContent=null, isUrl=false, unescape=true) → String

- 空规则返回 ""。
- result 初值 = content；三个快速路径：
  1. **Rhino NativeObject**（上一段 JS 返回了 JS 对象）：只用**第一条**子规则。putRule + makeUpRule(content)；若 `getParamSize()>1`（含模板参数）→ 取组装好的 rule 字符串；否则把 rule 当作 **JS 属性名**直接 `obj[rule]`。之后应用 replaceRegex。
  2. **LinkedTreeMap**（GSON 解析出的 Map 作为内容）：`result = map[firstRule.rule]`，无其他处理。
  3. 一般情况：逐段子执行：
     ```kotlin
     result = when (sourceRule.mode) {
         Mode.WebJs -> getWebJsResult(rule, result)
         Mode.Js    -> evalJS(rule, result)
         Mode.Json  -> getAnalyzeByJSonPath(result).getString(rule)
         Mode.XPath -> getAnalyzeByXPath(result).getString(rule)
         Mode.Default -> if (isUrl) getAnalyzeByJSoup(result).getString0(rule)
                         else       getAnalyzeByJSoup(result).getString(rule)
         else -> rule            // Mode.Regex：模板已在 makeUpRule 展开
     }
     ```
     每段之后若有 replaceRegex 则 `result = replaceRegex(result.toString(), rule)`。
     注意循环里的守卫：`result ?: continue`（result 变 null 就跳过后续段但仍执行 makeUpRule/putRule）。
- 收尾：null→""；`unescape && 结果含 '&'` → `StringEscapeUtils.unescapeHtml4`（HTML 实体解码）。
- `isUrl=true` 时：空白 → `baseUrl ?: ""`；否则 `NetworkUtils.getAbsoluteURL(redirectUrl, str)`（相对地址基于 redirectUrl 解析为绝对 URL；已是 http(s) 或 data: 的原样保留；`javascript` 开头变 ""）。

### 5.2 getStringList(...) → List<String>?

与 getString 同构，差异：
- Default 模式一律 `AnalyzeByJSoup.getStringList`（不分 isUrl）。
- WebJs：响应先尝试按 JSON 数组解析为字符串列表，失败则整个响应当一个字符串。
- 收尾：result 是 String → `split("\n")` 成列表。
- `isUrl=true`：逐项绝对化（`NetworkUtils.getAbsoluteURL`），去空、**去重保序**。
- 最终 unchecked cast `as? List<String>`；失败即抛 ClassCastException（实现时应等价地假定 List[String]）。
- NativeObject 快速路径里列表结果的 replaceRegex 也是逐项应用。

### 5.3 getElement(ruleStr) → Any?

- 用 `splitSourceRule(ruleStr, true)`（allInOne，支持前导 `:` 正则模式）。
- 每段：putRule → **makeUpRule(result)** → 按 mode 派发：
  `Regex → AnalyzeByRegex.getElement(str, rule.splitNotBlank("&&"))`；
  `WebJs → 响应按 JSON object 解析成 Map`；
  `Js → evalJS`；`Json → jsonPath.getObject(rule)`；
  `XPath → xpath.getElements(rule)`（List<JXNode>）；`Default → jsoup.getElements(rule)`（Elements）。
- 每段后有 replaceRegex 则对 `result.toString()` 应用。
- 返回原始对象（Element/Elements/JXNode 列表/Map/…），供下游作为新 content 使用。

### 5.4 getElements(ruleStr) → List<Any>

- 同样 `splitSourceRule(ruleStr, true)`。
- **不调用 makeUpRule** ⇒ 列表规则中 `{{…}}`、`@get:{}`、`$N`、`@put` 之外的模板机制无效（putMap 仍然执行）；**也不应用 replaceRegex**。
- 派发：`Regex → AnalyzeByRegex.getElements(str, splitNotBlank("&&"))`；`WebJs → JSON 数组→List<Map>`；`Js → evalJS`（须返回列表）；`Json → getList`；`XPath/Default → 各自 getElements`。
- result 为 null → 空 ArrayList；否则 unchecked cast `as List<Any>`。

### 5.5 明确**不存在**的特性（勿臆造）

- **`<...>` 单元素模式不存在**于 AnalyzeRule。尖括号只出现在 `<js></js>` 标签和 AnalyzeUrl 的翻页参数 `<page,page2,…>`（§13）。
- **joinString 参数不存在**：所有多值合并硬编码为 `"\n"`（getString/getStringList/各分析器一致）。
- **`lastUrlResult` 不存在**于本仓库（已全文检索确认）。URL 侧上下文变量见 §13。
- **flag 字母后缀不存在**（见 §4.5）。

---

## 6. `&&` / `||` / `%%` 组合语义

切分由 `RuleAnalyzer.splitRule("&&","||"[,"%%"])` 完成（§7 是算法细节）。语义在各分析器中一致：

- **`&&`**：每个子规则的**全部结果依次拼接**（列表 addAll；字符串场景 joinToString("\n")）。
- **`||`**：按序求值，**遇到第一个非空结果即停止**（短路）。
- **`%%`**：按索引交错合并（zip-longest）：以第一个子结果列表的长度为准，`for i in 0 until results[0].size: for each list: 取第 i 个`。
  jsoup 的 Elements 版本同构（按下标配对交错元素）。

**重大怪点——分隔符全局单一**：`splitRule` 以规则中**最先出现**的那个分隔符为本次切分的 `elementsType`，整条规则只按它切开；其余分隔符留在子段里。例如 `a||b&&c` 会切成 `["a", "b&&c"]`（elementsType="||"）。后果因分析器而异：

- **JSONPath / XPath 的 getString/getStringList/getList**：子段**递归**调用自身解析，所以混合分隔符能级联正确处理（先 `||` 切开，`b&&c` 再被递归按 `&&` 切）。语义近似"按出现位置优先级递归"，而非固定的 && > || > %%。
- **AnalyzeByJSoup.getStringList/getElements**：子段交给 `getResultList`/递归 getElements，**不再按 && || %% 切**，混合分隔符不可靠。写书源时约定单条规则只用一种分隔符即可与 Python 实现对齐；实现建议照抄"首分隔符 + 递归"模型（json/xpath），jsoup 则按"首分隔符一次性"处理。

各入口使用的分隔符集合（精确）：

| 入口 | 分隔符 |
|---|---|
| AnalyzeByJSonPath.getString | `&&`,`||` |
| AnalyzeByJSonPath.getStringList / getList | `&&`,`||`,`%%` |
| AnalyzeByXPath.getString | `&&`,`||` |
| AnalyzeByXPath.getStringList / getElements | `&&`,`||`,`%%` |
| AnalyzeByJSoup.getStringList / getElements | `&&`,`||`,`%%` |
| AnalyzeByRegex（经 splitNotBlank("&&")） | 仅 `&&`，且剔除空白段 |

字符串结果合并字符一律 `"\n"`；JSON 单规则读出 List 时也 joinToString("\n")。

---

## 7. RuleAnalyzer 词法器（切分算法精确规格）

`RuleAnalyzer(data, code=false)`：`code=true`（仅 JSONPath 用）时平衡组用"代码平衡"，否则"规则平衡"。

状态：`queue`(原文)、`pos`(当前位置)、`start`(当前字段起点)、`startX`(当前规则起点)、`step`(命中分隔符长度)、`elementsType`(命中的分隔符)。

- `trim()`：跳过规则前的 `'@'` 和所有 `< '!'` 的字符（控制符+空白）。
- `consumeTo(seq)`：区分大小写的字面查找，命中则 pos 移到命中处并置 start=pos。
- `consumeToAny(vararg seq)`：找**最早出现**的任一分隔符（逐位置比对），记 step。
- 平衡组两版：
  - `chompCodeBalanced(open,close)`：引号感知（`'`/`"` 交替，双引号内单引号不算），**反斜杠在任何位置都转义下一字符**；`[`/`]` 计数为主嵌套 depth，`open/close` 仅在 depth==0 时计入 otherDepth；循环至两组深度均归零。
  - `chompRuleBalanced(open,close)`：引号感知；**引号外的 `\` 才转义下一字符**（注释明言 xpath/jsoup 引号内转义无效）；只计 open/close 深度。
- `splitRule(vararg split)` 两阶段：
  1. 阶段一：找到最早分隔符位置 end；然后从 start 起 `findToAny('[','(')` 找筛选器括号——若括号在 end 之前，用 `chompBalanced` 吞掉整个平衡组（不平衡抛 `Error("<前缀>后未平衡")`），重复直到分隔符先于括号出现（`do…while(end>pos)`；若一直没找到分隔符则递归阶段一重找）。
  2. 切分：首段 `queue[startX,end]`，记录 elementsType，pos 跳过分隔符，循环 consumeTo(elementsType) 依次压段，尾段为剩余全文。**段落不做 trim**（空白保留；由各消费方自行 trim）。
  - 单分隔符调用（如 `splitRule("@")`）走同一算法。
- `innerRule(inner="{$.", fr)`：找 `"{$."`，用 **chompCodeBalanced('{','}')**（无论 code 标志）吞平衡体，`fr` 收到 `{` 与 `}` 之间文本（含 `$.`）；fr 返回非空才替换，否则把 `"{$."` 当普通文字跳过继续找。**没有任何成功替换时返回 ""**（调用方据此回退到直接 read）。
- `innerRule(startStr,endStr,fr)`：朴素版（用于 URL 的 `{{ }}`），无平衡检查，未碰到 endStr 则丢弃。

---

## 8. JSoup 迷你选择器语法（AnalyzeByJSoup）

### 8.1 入口与 @CSS:

内部 `SourceRule(ruleStr)`：`@CSS:`（忽略大小写）→ `isCss=true`，取第 5 字符之后的子串（trim）。

- **isCss 子模式**（getStringList）：每个 `&&/||/%%` 子段整体视为 CSS 选择器 + `@属性`：`lastIndexOf('@')` 切开，`select(前半)` 后按 getResultLast 解释后半。**要求段内必须有 `@`**（没有时 lastIndexOf=-1 → substring(0,-1) 直接越界异常）。CSS 模式**不支持**索引/迷你关键字链。
- **isCss 子模式**（getElements）：整段直接 `temp.select(ruleStr)`，无 @attr 拆分。

### 8.2 非 CSS：`@` 链

- `getResultList(ruleStr)`：`RuleAnalyzer.trim()` 后按 `@` 切段；前 n-1 段逐段对当前元素集用 ElementsSingle 过滤（对集合中每个元素分别应用再拼接）；最后一段交给提取器 getResultLast。
  例：`body@class.book@tag.a@text`。
- `getElements(root, rule)`：同样 trim+按 `@` 切；**多于一段时**每段对上一轮结果递归 `getElements(et, rl)`（注意：每段又重新经历 SourceRule/splitRule，因此段内还能写 `&&` 等）；**只有一段**时直接 `ElementsSingle().getElementsSingle(temp, ruleStr)`。

### 8.3 ElementsSingle：前置规则 + 索引语法

`findIndexSet(rule)` 自**右向左**扫描，支持两种索引写法（可无前置规则）：

**(a) 传统阅读写法** `前置.SEP 索引[:索引[:…]]`，SEP ∈ `.`（选取）/ `!`（排除）/ `:`：
- 例：`tag.div.-1:10:2`（取倒数第1、第10、第2个）、`tag.div!0:3`（排除第0、第3个）。
- 右向左累积数字；遇 `-` 置负号；遇 `.` 或 `!` 结束索引收集并定 split、beforeRule=`rus.substring(0,len)`；遇 `:` 继续收下一个索引。遇到其他字符立即放弃 → 整串当选择器（split=' '）。

**(b) 方括号写法** `[索引或区间, …]`，`[!` 开头为排除：
- 要求规则最后一个字符是 `]`；右向左扫到 `[` 截出 beforeRule。
- 项：单个整数，或区间 `start:end[:step]`；start 省略=0、end 省略=-1（代码里 end 缺省 len-1）；负数按 `+len` 转正。
- 区间展开规则（严格照码）：
  ```
  start<0 ⇒ +=len; end<0 ⇒ +=len
  start,end 同时 <0 或同时 >=len ⇒ 本区间作废
  start>=len⇒len-1; start<0⇒0; end>=len⇒len-1; end<0⇒0
  start==end 或 step>=len ⇒ 只取 start 一个
  step = stepX>0 ? stepX : (-stepX<len ? stepX+len : 1)   // 负 step 会被换算成大正步长！
  end>start ? range(start,end,step) : range(start,down_to,end,step)
  ```
  即**让列表反向的正规写法是 `[-1:0]`**（默认 step=1 反向）；显式负 step（如 `-2`）实际得到 step+len 的怪异正步长——这是源码事实行为，需原样复刻。
- 单个索引越界丢弃：正数须 `0 ≤ i < len`；负数须 `len ≥ -i`（取 `i+len`）。
- **去重 + 输出顺序**：索引进 LinkedHashSet（去重）。选取模式（`.`/方括号默认）按"规则书写顺序"重建 Elements（右向左扫描入栈、反向弹出还原）；**排除模式按文档原有顺序**就地删 null。
- 索引前的部分（beforeRule）为空 ⇒ 等价 `children()`（根的直接子元素）。**`{0}`/`{-1}` 花括号索引不存在**，只有上述两种。

**前置规则关键字表**（`beforeRule.split(".")[0]` 精确匹配，小写敏感；`rules[1]` 为参数，因此参数里不能再含 `.`）：

| 写法 | 行为 |
|---|---|
| （空） | `temp.children()` |
| `children` | `temp.children()` |
| `class.x` | `temp.getElementsByClass("x")` |
| `tag.x` | `temp.getElementsByTag("x")` |
| `id.x` | `Collector.collect(Evaluator.Id("x"), temp)`（后代中首个 id=x，可能 0/1 个） |
| `text.x` | `temp.getElementsContainingOwnText("x")`（**ownText** 包含 x 的后代元素） |
| 其他任意 | `temp.select(beforeRule)` —— 完整 jsoup/CSS 选择器（可含组合器、属性选择器等） |

### 8.4 提取器 getResultLast 关键字（最后一段）

| 关键字 | 行为 |
|---|---|
| `text` | `element.text()`（含后代，规范化空白）；空串跳过；每元素一项 |
| `textNodes` | 元素的**直接**文本节点，逐个 trim、跳空，节点间 `"\n"` 连接成**一项/元素** |
| `ownText` | `element.ownText()`；空串跳过 |
| `html` | **先从选中元素里移除 `script`、`style`**，再取 `outerHtml()`（多个元素合并成一个字符串）；空跳过 |
| `all` | `elements.outerHtml()`，不移除 script/style，恒为一项 |
| 其他任意值 | 作为**属性名** `element.attr(name)`：**空白值跳过、重复值去重**（对照已有 textS），每元素至多一项 |

- `value`、`href`、`src`、`content` 等都不是保留字，全走 attr 分支。
- 规则没有任何 `@` 且整串是提取关键字时（如规则就是 `text`）：元素集={root}，直接取 root 的对应值。
- `getStringList` 中若 `@CSS:` 后为空（规则恰为 `@CSS:`）→ 返回 `element.data()`（script/style 等数据节点内容）。
- `getString` = getStringList 后 join("\n")（单项直接返回）；`getString0` = 取列表第 0 项（空列表→""）。

---

## 9. JSONPath 方言（Jayway）

- 引擎：`com.jayway.jsonpath.JsonPath`，默认配置（智能解析关闭与否取决于库默认——按 Jayway 默认即可；路径必须以 `$` 开头）。
- 支持标准 Jayway 语法：`$.data[*]`、`$..key`（深扫）、`$[0].x`、过滤器 `$[?(@.price < 10)]`、多结果返回 List。
- **顶层组合**：`&&`/`||`(/`%%`) 由 RuleAnalyzer 切分（code=true，`{$…}` 内的 `&&` 受代码平衡组保护不被误切，这正是引入它的原因——jsonPath 自身的 `&&` 过滤语法冲突）。
- **内嵌规则 `{$.path}`**：`getString/getStringList` 先 `innerRule("{$.")` 把每个 `{$.x}` 用其求值结果（递归 getString）替换成字符串；全部失败（返回 ""）才尝试把整条规则 `ctx.read(rule)`。替换成功则**不再执行外层 read**。`{$.` 不平衡时按普通文字处理。
- 读值行为：
  - getString：read 结果为 List → joinToString("\n")；否则 toString。异常捕获打印，返回 null。
  - getStringList：List → 每元素 toString 成一项；标量 → 单项。
  - getObject：`ctx.read(rule)` 原样返回（getElement 用）。
  - getList：`ctx.read<ArrayList<Any>>`（getElements 用）；多规则时 %% 按索引交错、&& 拼接、|| 短路。
- **与其他模式的混合**不在本类内完成（无 `@jsoup:`/`@css:` 内嵌切换语法）；跨模式靠 AnalyzeRule 的分段管道与 `{{…}}`/`@get` 模板（§12）。

---

## 10. XPath 方言（JsoupXpath / SeimiCrawler）

- 引擎 `org.seimicrawler.xpath.JXDocument`；语法为其方言：`//div[@class="x"]/text()`、`@text()`、`@html`、`@all`、谓词、`|`（引擎自持，注意与顶层 `||` 的冲突由 RuleAnalyzer 平衡组规避——`(` 内的分隔符会被吞掉）。
- `getString`：单规则 → `selN(xPath)` 所有节点 `asString()` 后 `TextUtils.join("\n", …)`；多规则递归 + `&&` 拼接/`||` 短路。
- `getStringList`：每节点 asString 一项；`&&/||/%%` 同 §6。
- `getElements`：返回 List<JXNode>；`&&/||/%%` 同构。
- 输入修复：片段以 `</td>` 结尾包 `<tr>`，以 `</tr>`/`</tbody>` 结尾包 `<table>`（§1.2）；`<?xml` 前缀走 xmlParser。
- 字符串抽取完全委托库的 asString：`@text()` 取文本、`@html` 取 innerHtml、`@all` 取 outerHtml（JsoupXpath 语义）。

---

## 11. 正则分析器（AnalyzeByRegex）

仅服务于 `getElement/getElements` 的 Regex 模式（规则经 `splitNotBlank("&&")` 切成多条正则，剔除空段、各段 trim）：

- **链式过滤**：从第 0 条开始；非末条正则把**全部匹配**连接成一个字符串作为下一条的输入。
- **末条正则**：
  - `getElement`：只取**第一个**匹配，返回其**所有捕获组**（含 group 0 整体匹配）组成的 List<String>；第一条就无匹配 → null。
  - `getElements`：取**每个**匹配，各生成一组捕获组列表；无匹配 → 空列表。
- 正则为 Kotlin `Regex`（Java 方言）；编译失败会在 Regex() 处抛异常（此处无 try-catch，区别于 ## 后缀的宽容处理）。

---

## 12. JS 执行集成

### 12.1 三种嵌入形式

1. **整段**：`<js>…</js>`（非贪婪）或 `@js:…`（贪婪到规则尾），见 §3，段模式 Js。
2. **模板内嵌**：规则任意位置的 `{{…}}`（evalPattern，非贪婪，不支持嵌套大括号），见 §4.2/4.4。
3. **WebJs**：`@webjs:…`（≥5 字符），在后台 WebView 里执行（url=baseUrl、html=当前内容、result=当前结果 JSON、超时 10s、必须非主线程）。

另有 URL 规则中的 `{{…}}`（§13）与 `@get:{}`/`@put:{}` 变量读写（§4）。

### 12.2 evalJS 绑定变量表（AnalyzeRule.evalJS，Rhino）

| 变量 | 含义 |
|---|---|
| `java` | AnalyzeRule 实例（JsExtensions：ajax、加密、编解码等全套扩展方法） |
| `cookie` | CookieStore |
| `cache` | CacheManager |
| `source` | 书源/RSS 源对象（可为 null） |
| `book` | 当前 BaseBook（可为 null） |
| `result` | **管道当前值**（上一段输出或初始 content；模板 `{{}}` 中为当前 result） |
| `baseUrl` | 当前 baseUrl |
| `chapter` | 当前 BookChapter（可为 null） |
| `title` | `chapter?.title` |
| `src` | **初始注入的 content 原文**（不随管道变化） |
| `nextChapterUrl` | 下一章 URL（可为 null） |
| `rssArticle` | RSS 条目（可为 null） |
| `fromBookInfo` | 是否来自详情页流程 |

脚本经 `RhinoScriptEngine.compile` 编译并缓存（上限 16 条，超出后**不再缓存但照常执行**，getOrPutLimit 无淘汰只是停写）；作用域优先复用 source 的共享 scope，否则运行时 scope（16 次调用后缓存 prototype 弱引用）。

### 12.3 返回值处理（各调用点不同）

- `getString` 管道中：evalJS 返回值直接成为 result（后续 toString/实体解码/URL 解析照常）。
- `getStringList`：返回 List → 保留为列表（最终 cast）；返回 String → 末尾按 `\n` split。
- `makeUpRule` 的 `{{}}`：null→跳过；String→原样嵌入；Double 且整数值→`%.0f`（防 1.0）；其余 toString。
- `getElement(s)`：期望返回对象/List；WebJs 分别按 JSON object/array 解析。
- JS 里返回 Rhino NativeObject 时，下一段规则走 §5.1 的属性名直取快速路径。

---

## 13. URL 规则的处理（AnalyzeUrl，概要）

AnalyzeRule 本身对 URL 只有 `isUrl` 参数的行为差异（§5）；真正的 URL 语法在 `AnalyzeUrl.kt`：

- `initUrl()` 流程：① `analyzeJs()` —— 对 ruleUrl 跑 JS_PATTERN，JS 之间的字面文本中 `@result` 被**替换为累计结果**（这是 URL 规则特有的占位符）；② `replaceKeyPageJs()` —— 含 `{{` 与 `}}` 时用 innerRule 朴素版执行内嵌 JS（String 原样、整值 Double `%.0f`、其余 toString）；随后翻页参数 `<page1,page2,…>`（`pagePattern=<(.*?)>`，非贪婪）按 1-based 页码取第 page 项，超出取**最后一项**；③ `analyzeUrl()`。
- `paramPattern = \s*,\s*(?=\{)`：首个 `,`+`{` 之后是 **URL 选项 JSON**（method(GET/POST/HEAD)、headers、body、type、charset、retry、useWebView、webJs、bodyJs、dnsIp、js……其中 `js` 以 url 为 result 求值后覆盖 url）。选项解析同样严格 Gson 优先、宽松兜底。
- `lastUrlResult` 在本仓库不存在（检索确认）；等价的"上一结果"概念在 URL 场景由 `@result` 占位承担。

---

## 14. 缓存与杂项

| 缓存 | 键 | 上限 | 说明 |
|---|---|---|---|
| stringRuleCache | 完整规则串 | **无限**（hashMap.getOrPut） | 仅 getString/getStringList 的 splitSourceRule 结果 |
| regexCache（## 后缀用） | regex 文本 | 16（超限不缓存） | 编译失败缓存 null → 走字面量替换 |
| scriptCache | JS 文本 | 16（超限不缓存） | Rhino CompiledScript |

- `splitNotBlank("&&")`：按 && 切、每段 trim、丢空白段（Regex 链专用）。
- 变量存储优先级：chapter → book → ruleData → source（put 与 get 同序；get 特例 `bookName`/`title`）。
- `setRedirectUrl(url)`：data: URL 忽略；否则记录为绝对化基准。
- 日志/调试通道（Debug.log、printOnDebug）在 Python 中可映射为 logger，不影响语义。

---

## 15. 陷阱清单（实现时最容易踩的 15 条）

1. `@CSS:` 前缀在 AnalyzeRule 层不剥离，进入 AnalyzeByJSoup 后才剥 5 字符并切换为"整段 CSS + @attr"子模式；该子模式段内**必须**含 `@`。
2. content 是 JSON（首尾 `{}`/`[]`）时**所有**裸规则都被强制为 Json 模式。
3. `/` 开头即 XPath，哪怕本意是 jsoup。
4. Regex 模式的真实含义是"模板/回引段"：makeUpRule 就地展开，输出即展开后的字符串；`$N` 只在 result 为 List 时取元素，否则插回字面量。
5. `{{}}`/`@get:{}` 是否把规则变成模板模式，取决于首次出现位置是否为 0 或前文是否含 `##`（§4.2 的精确条件）。
6. `##` 后缀在模板展开**之后**再切分；第 4 段存在（`###`）即"只替换第一处"；replaceFirst 无匹配时**返回空串**而不是原串；无 flag 字母。
7. getElements（列表）不执行 makeUpRule、不应用 `##` 替换；getElement（单个）两者都做。
8. `&&/||/%%` 的分隔符取"规则中最先出现者"且全局唯一；json/xpath 侧递归级联、jsoup 侧不级联；`%%` 是按下标 zip 交错而非笛卡尔积。
9. 多值合并恒为 `"\n"`；`getString` 含 `&` 时做 HTML4 实体解码（可用 unescape=false 关闭）。
10. 索引语法是 `.n`/`!n`/`:n` 传统式与 `[i,j,k:a:b]` 方括号式两种；负索引 `+len` 越界即弃；选取按规则顺序输出且去重，排除保持文档顺序；负 step 会被换算成 step+len（复刻之）；反向列表用 `[-1:0]`。
11. `html` 提取器会先删除选中元素内的 script/style；`all` 不删；attr 分支跳过空白并去重。
12. JSONPath 内嵌 `{$.x}` 用代码平衡组解析，全部替换失败才回退整串 read；替换成功就不执行外层路径。
13. `@js:` 贪婪吃满剩余规则（其后不能再有别的段）；`<js></js>` 非贪婪；`@webjs:` 至少 5 字符且不能排在 `@js:` 后。
14. JS 绑定的 `result` 是管道当前值，`src` 才是最初页面/JSON 原文；整数值 Double 要格式化为整数。
15. `splitSourceRule(allInOne=true)` 的前导 `:` 会置实例级粘性 `isRegex`，影响后续所有规则解析。
