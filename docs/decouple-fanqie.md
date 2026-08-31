# Legado 引擎去耦设计：把「番茄特化」从引擎核心流程里抽出去

> 状态：**设计已确认 + 迁移已完成（S1–S5）**。
> 已确认决策：① SSO 分组「配置为主 + 书源 `extra` 可选覆盖」；② 适配器「自注册」；
> ③ 封面 HEIC 转码已泛化为通用 `services/heic.py`（可插拔预处理器 + 本地解码兜底）。
> 符合「保持番茄可用性不退步」硬约束。

---

## 1. 背景：检测到的两处真实耦合

`legado_rule` 引擎本体（四个 analyzer、`@js:`/`java.*` 桥、rhino 方言、登录 UI/动作、
探索页、`source_state` 登录态/Cookie/缓存）是**通用完备的 legado 移植**，绝大多数代码里的
「番茄」字样只是解释性注释。真正把番茄逻辑**写进通用流程**的只有两处：

| # | 位置 | 体量 | 内容 | 破坏的通用性 |
|---|------|------|------|--------------|
| 1 | `web_book.py`「访客降级」块（~582–740，+ 调用点 525/768/849） | ~160 行 | 硬编码 `_FQ_DOMAINS`、`_FQ_RELAY_HOSTS`、byteimg 原图 `replaceCover`、抓 `fanqienovel.com/page/{bid}` 抠 `thumbUri`、`/api/reader/directory/detail`、中继 content 接口 | `get_book_info`/`get_toc`/`get_content` 里出现「按域名走另一条刮取路径」的分支 |
| 2 | `source_state.py` SSO 镜像（49–56/129–138/192–210）+ `source_login.py` 触发（217–219/271–275） | ~30 行 | 只要登录表单有 `token：`，就把 `sessionid` 镜像到 `snssdk.com`↔`fanqienovel.com` | generic 登录流里写死了「某类账号是跨域单点登录」 |

**结论**：引擎本体不是番茄专用客户端，但**在引擎核心流程里埋了两个按域名硬编码的番茄逃生舱**。
耦合位置比体量更值得担心——想支持第二个带登录门禁的书源，就得再写一套逐个源的 `_xxx_guest_*`。

---

## 2. 设计原则

1. **引擎核心零番茄**：`web_book`/`source_state`/`source_login` 的直通路径里不出现
   `fanqienovel.com`/`snssdk.com`/`byteimg` 等字面值。
2. **番茄行为保留，改由「数据」或「适配器」表达**：
   - 登录态跨域镜像 → 数据驱动（可配置的 sessionid 域名分组）；
   - 访客降级刮取 → 可插拔适配器（adapter），番茄只是**默认注册**的一个实现。
3. **默认匹配行为与现状一致**：不配任何东西时，番茄源的降级/镜像行为完全不变。
4. **可扩展而不改引擎**：新增第二个登录门禁书源时，只需注册一个新适配器，不碰 `web_book` 主干。
5. **每步可独立合入、可回滚**：分步迁移，每步带回归测试。

---

## 3. 目标架构

```
legado_rule/
├── web_book.py            # 主流程：get_book_info / get_toc / get_content / explore…
│                           仅靠一个 hook 询问「本源有访客降级适配器吗？」
├── source_degradation/     # ★ 新增：源能力适配器层
│   ├── registry.py         #   select(source, *capability) 与注册表
│   ├── interfaces.py       #   GuestReadAdapter(abstract): guest_cover/toc/content/book_id
│   └── fanqie.py           #   ← 番茄实现（从 web_book.py 原样搬迁 160 行，改签名）
├── source_state.py        # 登录态/Cookie；SSO 镜像改为读配置分组（无番茄字面量）
└── legacy_engine.py       # （可选）兼容层：把适配器动作桥回引擎结果形状
```

### 3.1 能力适配器接口（`source_degradation/interfaces.py`）

```python
class GuestReadAdapter(Protocol):
    """某书源的「访客降级」能力：通用规则引擎拿不到数据时的备用读取路径。"""

    # 一个 URL（通常是 bookSourceUrl）是否属于本适配器管辖域
    def matches(self, source: dict) -> bool: ...

    # 若封面/目录/正文在主流程得到空或异常，尝试备用路径；失败返回 None
    async def guest_cover(self, source: dict, book_url: str) -> str | None: ...
    async def guest_toc(self, source: dict, book: dict, toc_url: str, base_url: str) -> list[dict] | None: ...
    async def guest_content(self, source: dict, chapter: dict) -> str | None: ...

    # 主流程能否命中「本适配器产出的章节」并给出 item_id（可选，供 content 判定）
    def is_guest_chapter(self, source: dict, chapter: dict, ch_url: str) -> bool: ...
```

`web_book.py` 主流程只认这个 `Protocol`，不 import 任何番茄模块。

### 3.2 注册与选择（`source_degradation/registry.py`）

```python
_GUEST_READERS: list[GuestReadAdapter] = []

def register(adapter: GuestReadAdapter) -> None: ...
def guest_reader_for(source: dict) -> GuestReadAdapter | None:
    return next((a for a in _GUEST_READERS if a.matches(source)), None)
```

- 番茄适配器在模块导入时 `register()`（**默认注册**，现状行为不丢）。
- **能力开关放进书源 JSON 的 `extra` 字段**（legado 书源允许冗余顶层字段，viewer
  原样存储 `raw_json`，读它无害）：
  ```json
  { "bookSourceUrl": "…",
    "…规则字段…",
    "extra": { "adapters": { "guestRead": false } } }
  ```
  `matches()` 先看 `extra.adapters.guestRead === false` 直接拒绝；缺省时回落域名匹配。
  这样单个书源可显式关闭/强制一份能力，避免误伤其它恰好落在同域的书源。
- `match` 复用现有域名判断逻辑（`fanqienovel.com`/`snssdk.com` 等），但**以适配器私有
  常量科化**，引擎主流程看不到。

### 3.3 主流程改动（`web_book.py`）

把三处 `_fq_*` 分支改为统一 hook，结构完全对称、可一行看懂：

```python
# get_book_info（原 525）
if not info.get("coverUrl"):
    ad = registry.guest_reader_for(source)
    if ad:
        info["coverUrl"] = ad.guest_cover(source, book_url) or info.get("coverUrl")

# get_toc（原 768）
if not chapters:
    ad = registry.guest_reader_for(source)
    if ad:
        guest = await ad.guest_toc(source, book, res.url or toc_url, res.url or toc_url)
        if guest:
            for i, ch in enumerate(guest):
                ch["index"] = i
            return guest

# get_content（原 849–862）
ad = registry.guest_reader_for(source)
if ad and ad.is_guest_chapter(source, chapter, ch_url):
    text = await ad.guest_content(source, chapter)
    if not text:
        raise RuleError("内容为空")
    return text
```

`_fq_book_id`/`_fq_unescape_url`/`_fq_replace_cover`/`_fq_guest_*` 整体搬进
`source_degradation/fanqie.py`，`web_book.py` 不再保留 `_FQ_*` 常量。

### 3.4 SSO 镜像数据驱动化（`source_state.py` / `source_login.py` / `core/config.py`）

把硬编码元组换成**配置驱动的域名分组**：

```python
# core/config.py settings 新增（默认值 = 现状番茄一组）
session_sso_groups: list[list[str]] = [
    ["snssdk.com", "fanqienovel.com"],
]
```

```python
# source_state.py — 不再出现“番茄/头条”字面量
def _sso_group_for(domain: str) -> frozenset[str] | None:
    for group in settings.session_sso_groups:
        g = frozenset(group)
        if domain in g:
            return g
    return None

def _session_targets(domain, cmap):
    targets = {domain}
    if "sessionid" in cmap:
        g = _sso_group_for(domain)
        if g:
            targets.update(g)
    return targets

def ensure_session_global(tok=""):
    # 扫描“全部已落地的 sessionid 所在分组”，把登录态扩散到组内兄弟域名
    ...
```

- `source_login.py` 的两处 `ensure_session_global(_tok)` 调用保留（这是通用动作：
  “把本次登录的会话写全局”），但其内部行为完全由配置驱动，不写死番茄域。
- 想要第二家跨域 SSO 的书源：往 `settings.session_sso_groups` 加一个分组即可，
  引擎与登录流零改动。

### 3.5 封面 HEIC 转码 —— 已泛化（`services/heic.py`）

- 原 `plugins/books/plugin.py` 的 `_byteimg_web_url`/`_webify_cover`：HEIC 封面转码，
  已落在“封面服务”边界层，且**已泛化为通用可复用服务** `backend/app/services/heic.py`：
  对所有 HEIC 源生效，走「可插拔的远程 web 预处理器注册表（byteimg 为默认注册的一例，
  其余 CDN 可 `register_preprocessor(...)` 追加）+ 始终可用的 pillow-heif 本地解码兜底」。
  插件仅调用 `is_heic(...)` / `webify_heic(...)`，不再持有 byteimg 特化逻辑。

---

## 4. 迁移步骤（每步可独立合入 + 回归测试）

| 步骤 | 改动 | 状态 |
|------|------|------|
| **S1 骨架** | 新增 `source_degradation/`（registry + interfaces + 空 `fanqie.py`） | ✅ |
| **S2 搬迁适配器** | 把 `web_book.py` 的 `_fq_*`/`_FQ_*` 整块搬进 `fanqie.py`，改适配器方法签名 | ✅ |
| **S3 主流程接 hook** | `web_book.py` 三处 `_fq_domain` 分支换成 `registry.guest_reader_for(source)` | ✅ |
| **S4 SSO 数据驱动化** | `config.py` 加 `session_sso_groups`；`source_state.py`/`source_login.py` 去字面量 | ✅ |
| **S5 文档 + 清理** | 更新架构说明、清理临时文件、确认核心模块无番茄域字面量 | ✅ |

> 迁移回归：内联脚本对「适配器选择 / `extra.guestRead=false` 关闭 / SSO 默认分组 /
> `extra.sessionSsoGroup` 覆盖与关闭 / sessionid 兄弟域镜像 / 关闭镜像不扩散」逐项验证，**全部通过**。
> 编译 + `import` 检查通过。番茄真实网络回归需在你本地跑（沙箱网络受限）。

## 实现后的文件结构

- `backend/app/legado_rule/source_degradation/`
  - `interfaces.py` — `GuestReadAdapter` 协议
  - `registry.py` — `register` / `guest_reader_for` / `load_builtin`
  - `fanqie.py` — 番茄访客降级适配器（默认自注册）
- `backend/app/legado_rule/web_book.py` — 主流程仅通过 `guest_reader_for(source)` hook
- `backend/app/core/config.py` — 新增 `session_sso_groups` 配置
- `backend/app/legado_rule/source_state.py` —— `groups_for_source`、`_config_groups`；
  `set_cookie`/`replace_cookie`/`ensure_session_global` 接受可选 `groups`
- `backend/app/legado_rule/source_login.py` —— 登录成功后按本源分组镜像

> 边做边跑：`backend/.venv/Scripts/python.exe -m pytest backend/tests -q`。
> 之前沙箱会拦 `D:\Project\antares\.cache`（测试 fixture 写工作区外），本地跑不受此限。

---

## 5. 兼容性与风险

- **兼容性**：S2/S3/S4 均以「默认配置/默认注册 = 现状」为前提；不配置任何东西时行为等价。
- **风险**：番茄书源真实回归 → 用 S2/S3 保留的 fixture/用例 + 手动点一次番茄搜索/目录/正文验证。
- **回滚**：每步独立 commit、独立 revert；S3 恢复三处 `_fq_domain` 即回到现状。

---

## 6. 已确认决策

1. **SSO 分组**：`settings.session_sso_groups` 配置为**基座**（默认一对一保持番茄现状），
   书源 `extra.sessionSsoGroup` 可对该源覆盖分组（`null`/`[]` 表示该源禁用镜像）。
2. **适配器启用**：**自注册**——`source_degradation/fanqie.py` 在模块导入时
   `register()`，番茄源默认零配置即可用。
3. **封面 byteimg HEIC 转码**：已泛化为通用 `services/heic.py`（可插拔预处理器 + 本地
   解码兜底），**本次随迁完成**（见 §3.5）。

> 本 repo 原有 pytest 的 fixture 会写 `D:\Project\antares\.cache`（工作区外），沙箱
> 环境跑不全；每步改动用内联脚本/定向用例在本工作区内验证。设计和迁移说明以本文件为准。