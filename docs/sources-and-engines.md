# 书源、订阅源与规则引擎

Viewer 将“来源”分成三个平级数据域。书源管理、订阅源管理和媒体源管理都可以从管理仪表盘直接进入，并在各自页面互相切换。

## 来源类型

| 类型 | 主要标识 | 用途 | 管理位置 |
| --- | --- | --- | --- |
| 书源 | `bookSourceUrl` | 小说搜索、发现、详情、目录和正文 | 管理 → 书源管理 |
| 订阅源 | `sourceUrl` | RSS、Atom 或 Legado 订阅文章 | 管理 → 订阅源管理 |
| 媒体源 | `sourceUrl` 或 `bookSourceUrl` | 漫画、音频、视频目录与播放/阅读 | 管理 → 媒体源管理 |

普通管理入口只写入当前数据域；媒体源导入支持 Legado RssSource 或 BookSource，并记录 `sourceFormat` 与 `mediaKind`。通过 Legado 协议导入混合集合时则会先分类，再分别写入三个数据域。

## 导入方式

三类管理接口都支持粘贴 JSON；书源、订阅源和媒体源也支持传入 URL，由后端下载 JSON 后导入。需要导入可能混合多种来源的 Legado 文件时，可以打开 `legado://import/bookSource?src=...`，由协议确认页统一分类导入。

书源 JSON 可以是单个对象或数组。系统以 `bookSourceUrl` 去重：

- 新地址创建来源。
- 已存在地址更新名称、分组、原始 JSON 和引擎。
- 缺少地址、引擎未知或成员不是对象时计入 `skipped`。

`bookSourceGroup` 导入时取逗号分隔的第一个分组。`viewEngine` 可指定引擎 key；没有指定时使用请求的默认引擎，当前默认是 `legado`。

订阅源以 `sourceUrl` 去重，并保留完整原始 JSON，便于导出时保留当前尚未消费的字段。订阅源在管理仪表盘拥有独立数量统计、管理卡片和导入导出入口，与书源管理平级。

媒体源可以显式选择 `comic`、`audio`、`video`。协议自动分类主要依据 `bookSourceType`、RssSource 的 `type` 和显式 `mediaType`；缺少类型时只使用保守的规则特征判断。具体映射见[协议桥与 Legado 协议](protocols.md)。

## Legado 规则引擎

`engine_legado` 是独立的 `engine` 组件，负责把来源 JSON 适配为统一接口。当前实现包含：

- 搜索与发现。
- 书籍详情。
- 目录及多页目录。
- 正文及多页正文。
- jsoup/CSS、JSONPath、XPath、正则等规则路径。
- QuickJS 执行的 `@js`、模板脚本和 jsLib。
- 书源登录表单、Cookie 和登录头。

Android 专属对象不等于浏览器或服务端 API；依赖 Android UI、WebView 或设备能力的规则可能需要服务端兼容实现或无法运行。

## 引擎接口

新引擎实例应实现：

```python
def matches(raw: dict) -> bool: ...
async def search_book(src: dict, key: str, page: int = 1) -> list[dict]: ...
async def explore_kinds(src: dict) -> list[dict]: ...
async def explore_kind_values(src: dict) -> dict: ...
def explore_kind_action(src: dict, kind: dict, value=None, long_click=False) -> dict: ...
async def explore_book(src: dict, url: str, page: int = 1) -> list[dict]: ...
async def book_info(src: dict, book: dict) -> dict: ...
async def get_toc(src: dict, book: dict, toc_url=None) -> list[dict]: ...
async def get_content(src: dict, book: dict, chapter: dict,
                      next_chapter_url=None, base_url=None) -> str: ...
```

引擎对象会被缓存并跨请求复用，不应把单次请求的可变数据保存在实例字段中。网络和解析失败应抛出明确异常，不能用空列表伪装成功。

## 发现交互

发现页先加载来源提供的分类标签，用户点击标签后才请求相应分类内容。分类动作可以返回刷新、打开登录或携带搜索词等指令；前端根据统一动作结果更新页面。

当所有分类为空时，按以下顺序排查：

1. 确认来源已启用且被正确导入到书源或媒体源域。
2. 检查分类 URL 与来源站点是否仍可访问。
3. 检查是否需要登录、Cookie、请求头或脚本预热。
4. 查看后端日志中的实际网络或规则错误。
5. 使用同一来源的搜索接口区分“发现规则错误”和“整个来源不可用”。

`JS 执行出错: TypeError: not a function` 通常表示规则调用了服务端桥中不存在或类型不匹配的方法。应记录具体来源、规则字段与调用表达式，再补充兼容层；不要把异常转换成“分类为空”。

## 正文与本地缓存

目录可以进入队列刷新。正文获取后可经过净化插件，再保存章节内容。整书下载使用独立并发上限；图片会通过缓存和格式转换服务提供给浏览器。

停用来源只阻止新的在线解析，不会自动删除书架、阅读进度或已下载章节。
