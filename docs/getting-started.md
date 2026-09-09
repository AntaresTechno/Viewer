# 安装与启动

## 环境要求

- Python 3.12。
- Node.js 与 npm，用于安装和构建前端。
- Windows 一键脚本还需要 `uv` 位于 `PATH`。
- 可写的 `backend/data` 目录；默认 SQLite 数据库保存在这里。

## Windows 一键启动

在仓库根目录执行：

```powershell
.\start.bat
```

脚本会完成以下工作：

1. 使用 uv 创建 `backend/.venv`。
2. 安装 `backend/requirements.txt`。
3. 在缺少 `frontend/dist` 时调用 `build.bat` 构建前端。
4. 在 `0.0.0.0:8000` 启动 Uvicorn。
5. 用默认浏览器打开 `http://127.0.0.1:8000`。

开发模式会同时启动 Vite 和带自动重载的后端：

```powershell
.\start.bat dev
```

Vite 监听 `0.0.0.0:5173`，并把 `/api` 代理到 `127.0.0.1:8000`。

## macOS 与 Linux

在仓库根目录执行：

```bash
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt

cd frontend
npm install
npm run build

cd ../backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

前端构建完成后由 FastAPI 从 `frontend/dist` 提供静态文件和单页应用回退。

## 手动开发

终端一：

```bash
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

终端二：

```bash
cd frontend
npm install
npm run dev
```

访问 `http://127.0.0.1:5173`。

## 首次启动

数据库初始化会自动创建表、系统权限组和第一个管理员。代码中的默认值为：

```text
用户名：admin
密码：view123456
```

首次登录后应立即修改密码。若要在空数据库创建不同的管理员，请在第一次启动前设置：

```dotenv
VIEWER_FIRST_ADMIN_USERNAME=owner
VIEWER_FIRST_ADMIN_PASSWORD=使用一个足够长的随机密码
```

这些变量只影响“数据库中尚无任何用户”的初始化过程，不会覆盖现有账号。

## 基本验收

启动后检查：

```bash
curl http://127.0.0.1:8000/api/health
```

响应应包含 `status: "ok"` 和组件启用状态。随后打开：

- `/`：Viewer 前端。
- `/docs`：FastAPI 交互式接口文档。
- `/api/health`：无需登录的健康检查。

如果生产入口只显示接口错误或旧页面，先重新运行前端构建并重启后端。
