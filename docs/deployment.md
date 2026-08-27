# Viewer 部署速查

> 整站 = `backend/` + `frontend/dist/`，单端口 8000。**运行期只需要 Python**；
> Node 只在构建时需要。第一次部署/每轮升级按下面 1→7 走一遍即可。

---

## 1. 构建产物（选一台装了 Node ≥20 的机器执行）

```bat
:: Windows 一键：建 venv + pip install + npm + vite 构建，产出 frontend/dist
build.bat
```

跨平台手动等价版：

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ../frontend && npm ci && npm run build
```

**产出**：`backend/`（含 `.venv`、`app/`）+ `frontend/dist`。把**整个项目目录**拷到生产机即可。

---

## 2. 配置 `backend/.env`（没有就新建；含密钥，勿提交 git）

```bash
# 先生成一个随机串作为签名密钥
python -c "import secrets; print(secrets.token_hex(32))"
```

把输出粘贴进 `backend/.env`：

```dotenv
VIEWER_SECRET_KEY=<上一步生成的串>
VIEWER_FIRST_ADMIN_PASSWORD=<你设置的强密码>
```

- **不设 `VIEWER_SECRET_KEY` → 每次重启全员掉登录**。
- 首次建库时用上面的 `FIRST_ADMIN_*` 播种默认管理员 **admin**。

---

## 3. 启动后端

**场景 A · Windows 本机 / 局域网共享** —— 运行：

```bat
start.bat     :: 等价 uvicorn 绑 0.0.0.0:8000，自动开浏览器
```

其他设备访问 `http://<本机IP>:8000/`。要常驻（关窗口也在跑）：把 uvicorn 注册为
计划任务/开机自启，或用 NSSM/WinSW 包成 Windows 服务。

**场景 B · Linux 常驻（systemd）** —— 把下面的文件存成 `/etc/systemd/system/viewer.service`
（把两处 `/opt/viewer` 换成你的实际路径）：

```ini
[Unit]
Description=Viewer
After=network.target

[Service]
Type=simple
User=viewer
WorkingDirectory=/opt/viewer/backend
EnvironmentFile=/opt/viewer/backend/.env
ExecStart=/opt/viewer/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now viewer
```

> systemd 示例只监听 `127.0.0.1`（对外一律经第 4 步的反代）。

---

## 4. 公网对外（可选）

在反向代理机放一份 nginx 配置（proxypass 到 8000；HTTPS 用 certbot 或 Caddy 自动签发）：

```nginx
server {
    listen 80;
    server_name viewer.example.com;
    client_max_body_size 64m;          # 书源正文/导入较大

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

`/api`、`/dav` 都走同一个后端，无需单独配置；生产 SPA 不用 WebSocket，无需 upgrade 头。

---

## 5. 备份

要备份的只有 `backend/data/`（`viewer.db` + `cache/`）：

- 库是 SQLite，**别在服务运行时直接复制 `.db`**；停机备份最稳，不停机用在线备份：
  ```bash
  sqlite3 backend/data/viewer.db ".backup '/backup/viewer-$(date +%F).db'"
  ```
- 或在应用内**配置一个 WebDAV 网盘**，开启「每日自动备份」（书架/进度/统计异地兜底）。
- `backend/data/cache/` 是图片缓存，**不用备份**，重启按 LRU 自动重建。

---

## 6. 升级

```bash
git pull
build.bat              # 即重装依赖 + 重构建前端（等价：pip install -r requirements.txt --upgrade + npm ci && npm run build）
sudo systemctl restart viewer   # 有 systemd 时；start.bat 则重启该进程
```

数据库**无需手工迁移**（启动自动建表/补字段）；只是首次建库才播种管理员。

---

## 7. 健康检查

```bash
curl http://127.0.0.1:8000/api/health
# → {"status":"ok","app":"Viewer","plugins":{...}} 即正常
```

---

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 重启后全员掉登录 | 没配 `VIEWER_SECRET_KEY`，见第 2 步 |
| 局域网其他设备打不开 8000 | 后端要绑 `0.0.0.0`（`start.bat` 已绑），并放行防火墙该端口 |
| 想用多 worker 扛并发 | SQLite + 进程内内存缓存不适合多进程，**保持单 worker**；要扩展先上 PostgreSQL |
| 自动更新时间不对 | `VIEWER_DAILY_REFRESH_HOUR`（默认 4 点）取**服务器本地时间**，跨时区主机记得换算 |

部署前逐项核对：`backend/.env` 固定了 `VIEWER_SECRET_KEY`、改了管理员密码 → 启动 → 反代/HTTPS
（公网）→ `/api/health` 返回 ok → 配 WebDAV 每日备份。

---

*本机安装 / 开发模式见 [installation-guide.md](installation-guide.md)；配置项全表见 `backend/app/core/config.py`。*