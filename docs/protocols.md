# 协议桥与 Legado 协议

## 两个独立插件

协议能力由两个包分工：

| 插件 | 包 | 职责 |
| --- | --- | --- |
| 协议桥 | `protocol_bridge` | 注册处理器、校验自定义 URI、分发动作、管理系统级 URL Scheme |
| Legado 协议 | `protocol_legado` | 定义 `legado://`、`yuedu://` 的路径语义，并自动分类导入来源 |

`protocol_legado` 通过 `requires = ["protocol_bridge", "engine_legado", "rss", "media"]` 声明前置：协议桥负责系统链接，引擎、订阅和媒体组件负责各分类的落库与后续解析。协议桥本身不包含 Legado 的路径和导入规则，因此其他协议插件可以复用它。

```text
操作系统 URL Scheme
        │
        ▼
protocol_bridge 原生启动器
        │ 打开 /protocol?uri=...
        ▼
Web 确认页 ──解析──> protocol_bridge 分发器
                          │
                          ▼
                    protocol_legado
                          │ 用户确认
                          ▼
                  来源识别与分类导入
```

## 支持的 Legado 链接

标准形式：

```text
legado://import/bookSource?src=<百分号编码的 HTTP(S) 地址>
yuedu://import/bookSource?src=<百分号编码的 HTTP(S) 地址>
```

兼容旧形式：

```text
legado://booksource/importonline?src=<百分号编码的 HTTP(S) 地址>
```

示例：

```text
legado://import/bookSource?src=https%3A%2F%2Fqyyuapi.com%2Fsy%2Fhx%2FV3.0.json
```

`src` 必须是没有嵌入用户名和密码的 HTTP 或 HTTPS 地址，最长 4096 字符。远程响应可以是单个来源对象、来源数组，或以 `items`、`data` 包装的来源集合。

链接路径继续使用 Legado 兼容的 `bookSource` 名称，但下载完成后不再假定内容全是普通书源。系统逐项识别并写入对应管理域：

| 来源字段 | 类型值 | 导入位置 |
| --- | --- | --- |
| `bookSourceUrl` | `bookSourceType` 未声明或 `0` | 书源管理 |
| `bookSourceUrl` | `bookSourceType=1` | 媒体源管理 → 音频 |
| `bookSourceUrl` | `bookSourceType=2` | 媒体源管理 → 漫画 |
| `bookSourceUrl` | `bookSourceType=3` | 媒体源管理 → 视频 |
| `sourceUrl` | `type` 未声明或 `0` | 订阅源管理 |
| `sourceUrl` | `type=1` | 媒体源管理 → 漫画 |
| `sourceUrl` | `type=2` | 媒体源管理 → 视频 |
| `sourceUrl` | `type=3` | 媒体源管理 → 音频 |

显式 `mediaType` 为 `comic`、`audio` 或 `video` 时优先采用。没有类型字段的来源仅在规则特征足够明确时识别为媒体，否则保留为普通书源或订阅源。无法识别的成员计入 `skipped`，警告会显示在确认页。

所有协议动作先进入 Web 确认页，不会仅凭链接自动写入数据库。确认后，整批数据在一个事务中导入；任一分类写入失败会回滚整批操作。结果页会展示书源、订阅源、漫画、音频和视频数量，并提供对应管理入口。

执行端点为 `POST /api/protocol-legado/import/sources`。旧端点 `POST /api/protocol-legado/import/book-source` 继续保留，但内部使用相同的自动分类逻辑。

## 系统注册

以超级管理员进入“管理 → 插件管理”，在“协议桥”卡片中打开“协议桥设置”。页面会列出当前协议插件注册到桥中的 Scheme，并提供注册与移除操作。

注册范围始终是运行后端的当前系统用户：

- Windows：写入 `HKCU\Software\Classes\<scheme>`。
- macOS：在用户 Applications 中创建协议桥 App，并通过 LaunchServices 登记。
- Linux：在用户应用目录创建 `.desktop`，通过 `xdg-mime` 登记 `x-scheme-handler`。

注册过程会保存先前处理器信息，移除时尽量恢复。它不会在模块导入或 Viewer 启动时静默执行。

如果 Viewer 地址是本机回环 HTTP 地址，原生启动器发现服务未运行时会尝试后台启动 Uvicorn。远程部署场景不会自动启动服务。

重要边界：点击插件 UI 的“注册”修改的是后端所在电脑，而不是远程浏览器所在电脑。要让桌面浏览器直接打开协议，协议桥必须运行并注册在该桌面系统上。

## HTTP 入口

无法或不希望注册系统 Scheme 时，可以直接打开：

```text
/protocol?uri=<完整协议 URI 的百分号编码>
```

也支持路径式入口：

```text
/protocol/legado/import/bookSource?src=<编码后的来源地址>
```

页面要求用户已经登录，并根据动作执行端点再次检查权限。

## 扩展新协议

新的协议适配插件声明 `protocol_bridge` 为前置，然后注册 Scheme：

```python
from ..protocol_bridge.service import ProtocolAction, register_handler

def resolve_example(parsed):
    if parsed.netloc != "import" or parsed.path != "/item":
        return None
    return ProtocolAction(
        handler="protocol_example",
        scheme=parsed.scheme,
        action="item.import",
        title="导入 Example",
        description="确认后导入。",
        payload={"src": "..."},
        execute_path="/api/protocol-example/import",
    )

def create_router(ctx):
    register_handler("protocol_example", {"example"}, resolve_example)
    # 返回带实际执行端点的 APIRouter
```

同一个 Scheme 不能由不同插件重复注册。分发器还会检查 URI 长度、控制字符、Scheme 语法、处理器所有权和执行路径。
