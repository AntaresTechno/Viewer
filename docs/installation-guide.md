# Viewer 环境安装指南

> 适用对象：首次在本机部署 / 二次开发 **Viewer**（Antares Viewer）的开发者。
> 架构总览见 [docs/architecture.md](architecture.md)。
>
> - **生产模式**：`build.bat` 构建 → `start.bat` 启动，单端口 `http://127.0.0.1:8000/` 即整站
> - **开发模式**：`start.bat dev` 双端热更（Vite 5173 + Uvicorn 8000）

---

## 目录

1. [技术栈与环境要求](#一技术栈与环境要求)
2. [获取代码](#二获取代码)
3. [方式一：Windows 一键脚本](#三方式一windows-一键脚本)
4. [方式二：手动安装（跨平台）](#四方式二手动安装跨平台)
5. [启动与访问](#五启动与访问)
6. [配置说明（.env 与环境变量）](#六配置说明env-与环境变量)
7. [验证安装](#七验证安装)
8. [运行测试](#八运行测试)
9. [常见问题 FAQ](#九常见问题-faq)
10. [卸载与重置](#十卸载与重置)

---

## 一、技术栈与环境要求

| 层 | 技术 | 说明 |
|---|---|---|
| 后端 | Python ≥ 3.11（推荐 3.12–3.14）、FastAPI、SQLAlchemy 2 (async) + aiosqlite、PyJWT、httpx、lxml/jsonpath-ng、dukpy/quickjs | 入口 `app.main:app` |
| 前端 | Vite 6、Vue 3.5、TypeScript 5.6、Pinia、vue-router 4、axios、miuix-vue、motion-v | 包管理 npm 或 pnpm |
| 存储 | 单文件 SQLite `backend/data/viewer.db`（自动创建） | 无需安装数据库服务 |

### 必装软件

| 软件 | 版本要求 | 获取地址 | 备注 |
|---|---|---|---|
| Python | ≥ 3.11（3.14 可用） | <https://www.python.org/downloads/> | Windows 安装时勾选 **Add python.exe to PATH** |
| Node.js | ≥ 20（推荐 22 LTS） | <https://nodejs.org/> | Vite 6 要求；仅跑生产模式也需它构建前端 |
| Git | 任意较新版本 | <https://git-scm.com/> | 仅拉取代码需要，可选 |
| pnpm | ≥ 9（可选） | `corepack enable && corepack prepare pnpm@latest --activate` | 前端仓库带 `pnpm-lock.yaml`；用 npm 也完全可以 |

### Python 版本与 JS 引擎的对应关系

书源规则中的 `@js` / `{{}}` 需要 JS 引擎，`backend/requirements.txt` 已按版本自动二选一：

| Python 版本 | 安装的引擎 |
|---|---|
| 3.14 及以上 | `dukpy`（QuickJS 内核，全平台纯轮子） |
| 3.13 及以下 | `quickjs` |

无需手动干预，`pip install -r requirements.txt` 会自动处理。

---

## 二、获取代码

```bash
git clone <仓库地址> viewer
cd viewer
```

（若拿到的是压缩包，解压后进入根目录即可，目录内含 `backend/`、`frontend/`、`docs/`、`start.bat`、`build.bat`。）

---

## 三、方式一：Windows 一键脚本

仓库根目录提供两支批处理，**首次使用只需双击或命令行执行**：

### 1. `build.bat` —— 安装依赖并构建

自动完成：

1. 不存在 `backend\.venv` 时用系统 `python` 创建虚拟环境；
2. `pip install -r backend\requirements.txt`；
3. `frontend\node_modules` 缺失时执行 `npm install`；
4. `npm run build` 产出 `frontend\dist`。

```bat
build.bat
```

### 2. `start.bat` —— 启动

```bat
:: 生产模式：Uvicorn 直接服务 frontend/dist，自动打开浏览器
start.bat

:: 开发模式：另开窗口跑 Vite(5173) + 本窗口跑 Uvicorn --reload(8000)
start.bat dev
```

> 一键脚本会自动跳过已完成的步骤（venv 已建好则不再创建，dist 已存在且未指定 dev 则不再构建），日常重复执行开销极小。

---

## 四、方式二：手动安装（跨平台）

适用于 macOS / Linux，或在 Windows 上想分步控制的情况。

### 1. 后端（Python）

```bash
cd backend

# 创建虚拟环境（目录名固定为 .venv，一键脚本也认这个名字）
python -m venv .venv

# 激活
# Windows CMD:
.venv\Scripts\activate.bat
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# 安装依赖
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> PowerShell 若报「禁止运行脚本」，先执行一次：
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 2. 前端（Node）

```bash
cd frontend

# 方案 A：npm（与 build.bat/start.bat 一致）
npm install --no-audit --no-fund

# 方案 B：pnpm（锁文件更严格、磁盘占用小）
pnpm install --frozen-lockfile
```

> ⚠️ 不要混用两种包管理器：切换时先删掉 `frontend/node_modules`
> （必要时连同不匹配的 lock 文件），再统一用一种安装。

### 3. 构建前端产物（仅生产部署需要）

```bash
cd frontend
npm run build        # 或 pnpm build，产物输出到 frontend/dist
```

---

## 五、启动与访问

### 生产模式（单端口整站）

```bash
cd backend
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # Windows
# source .venv/bin/activate && python -m uvicorn app.main:app --port 8000   # macOS/Linux
```

后端会自动挂载 `frontend/dist` 作为 SPA，浏览器访问：

```
http://127.0.0.1:8000/
```

API 文档（Swagger）：`http://127.0.0.1:8000/api/docs`

### 开发模式（双端热更）

开两个终端：

```bash
# 终端 1 —— 后端热重载
cd backend
.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload

# 终端 2 —— 前端 Vite
cd frontend
npm run dev          # http://localhost:5173/
```

`vite.config.ts` 已把 `/api` 代理到 `http://127.0.0.1:8000`，
开发时**访问 5173 端口**即可，无需关心跨域。

---

## 六、配置说明（.env 与环境变量）

所有配置集中在 `backend/app/core/config.py`，通过 pydantic-settings 读取，
**环境变量前缀 `VIEWER_`**，也可写在 `backend/.env` 文件里（Uvicorn 需从 `backend` 目录启动才能读到该文件）。

### 必须关注的项（生产环境）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VIEWER_SECRET_KEY` | **每次进程启动随机生成** | JWT 密钥。⚠️ 生产必须显式固定，否则每次重启全体用户掉登录 |
| `VIEWER_FIRST_ADMIN_USERNAME` | `admin` | 初始管理员用户名（仅首次建库生效） |
| `VIEWER_FIRST_ADMIN_PASSWORD` | `view123456` | 初始管理员密码（仅首次建库生效），**上线前务必修改** |

示例 `backend/.env`：

```dotenv
VIEWER_SECRET_KEY=please-generate-a-long-random-string-here
VIEWER_FIRST_ADMIN_USERNAME=admin
VIEWER_FIRST_ADMIN_PASSWORD=<你的强密码>
```

生成随机密钥：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 其他常用可调项（均有默认值，可不配）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VIEWER_DATABASE_URL` | `sqlite+aiosqlite:///backend/data/viewer.db` | SQLAlchemy 异步连接串 |
| `VIEWER_TOKEN_EXPIRE_MINUTES` | `20160`（14 天） | 登录态有效期 |
| `VIEWER_CORS_ORIGINS` | localhost:5173 / :3080 等 | JSON 数组格式 |
| `VIEWER_REQUEST_TIMEOUT` | `15.0` | 抓取书源的超时秒数 |
| `VIEWER_TOC_PAGE_LIMIT` / `VIEWER_CONTENT_PAGE_LIMIT` | 40 / 30 | 目录、正文最大翻页数 |
| `VIEWER_SEARCH_PER_SOURCE_LIMIT` | `20` | 单书源搜索结果上限 |
| `VIEWER_IMAGE_CACHE_MB` | `300` | 封面磁盘缓存 LRU 上限 |
| `VIEWER_REPLACE_REGEX_TIMEOUT` | `5.0` | 单条净化正则超时 |
| `VIEWER_PARSER_CONCURRENCY` / `VIEWER_SEARCH_CONCURRENCY` / `VIEWER_PREFETCH_CONCURRENCY` / `VIEWER_LIBRARY_DOWNLOAD_CONCURRENCY` | 4 / 6 / 3 / 4 | 各类抓取并发度（“可调线程”） |
| `VIEWER_DAILY_REFRESH_ENABLED` / `_HOUR` / `_CATCH_UP` | true / 4 / true | 每日自动更新书架目录（服务器本地时间 4 点；重启当天补跑） |

数据目录 `backend/data/`（数据库、图片缓存等）会在首次启动时自动创建，无需手动建。

---

## 七、验证安装

1. 打开 `http://127.0.0.1:8000/`（生产）或 `http://localhost:5173/`（开发）；
2. 使用初始账号登录：`admin` / `view123456`（若未用环境变量覆盖）；
3. **立即在「用户」页面修改默认密码**；
4. 访问 `/api/docs` 确认 Swagger 正常返回；
5. 在书源页导入一个 Legado 书源 JSON 并搜索任意关键词，能出结果即代表规则引擎（含 JS 引擎）工作正常。

---

## 八、运行测试

```bash
# 后端单元测试
cd backend
.venv\Scripts\python -m pytest -q

# 全链路冒烟测试（PowerShell）
powershell -ExecutionPolicy Bypass -File backend\tests\e2e_smoke.ps1
```

---

## 九、常见问题 FAQ

**Q1：`pip install` 时 `lxml` 编译失败？**
Linux 上先装系统头文件再重试：
```bash
sudo apt install build-essential libxml2-dev libxslt-dev python3-dev   # Debian/Ubuntu
sudo dnf install gcc libxml2-devel libxslt-devel python3-devel         # Fedora
```
macOS：`xcode-select --install`。通常升级 pip 到新版后会直接命中预编译轮子，无需编译。

**Q2：启动后登录状态全部失效 / 重启就掉登录？**
没设 `VIEWER_SECRET_KEY`，密钥每次启动随机。见[第六节](#六配置说明env-与环境变量)固定它。

**Q3：端口被占用（8000 或 5173）？**
换端口启动：`uvicorn app.main:app --port 8001`；开发模式下需同步改 `frontend/vite.config.ts` 中 proxy 的 target。查占用：`netstat -ano | findstr :8000`（Windows）/ `lsof -i :8000`（macOS/Linux）。

**Q4：书源搜索无结果或 `@js` 规则报错？**
确认 JS 引擎装上了：`pip list | findstr -i "dukpy quickjs"`。对照[第一节](#一技术栈与环境要求)的版本对应表检查 Python 版本；3.14 下 quickjs 无轮子属正常，应使用 dukpy。

**Q5：前端装依赖很慢？**
`npm config set registry https://registry.npmmirror.com`（或 pnpm 对应配置）后再安装。

**Q6：`Activate.ps1` 无法加载？**
执行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 后重开终端。

**Q7：能否只跑后端不装 Node？**
不能完成首次构建——`frontend/dist` 由 Vite 产出。但 dist 一旦构建好，运行期只需要 Python。

---

## 十、卸载与重置

| 操作 | 做法 |
|---|---|
| 完全卸载 | 删除整个 `viewer/` 目录即可（所有数据都在其中） |
| 重置数据库 | 停止服务后删除 `backend/data/viewer.db`，重启自动重建并重新播种初始管理员 |
| 清理图片缓存 | 删除 `backend/data/cache/img`，重启后按 LRU 重新积累 |
| 重装后端依赖 | 删除 `backend/.venv` 后重新走第四节第 1 步 |
| 重装前端依赖 | 删除 `frontend/node_modules` 后重新 `npm install` |

---

*文档基于当前代码生成：后端入口 `backend/app/main.py`，配置 `backend/app/core/config.py`，脚本 `build.bat` / `start.bat`。*
