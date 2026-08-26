# Viewer 部署指南

> 面向：把 Viewer 从「本机跑起来」推进到「长期对外提供服务」的部署场景。
> 本机环境安装 / 二次开发请先看 [installation-guide.md](installation-guide.md)，
> 架构总览见 [architecture.md](architecture.md)。
>
> 一句话：**前端构建产物 `frontend/dist` + Python 后端**自成一个单端口整站；
> 运行期除了 Python 不需要 Node。

---

## 目录

1. [部署形态与前置准备](#一部署形态与前置准备)
2. [构建（一次性或随版本更新）](#二构建一次性或随版本更新)
3. [运行期只需 Python 的说明](#三运行期只需-python-的说明)
4. [生产运行：Windows](#四生产运行windows)
5. [生产运行：Linux（systemd 常驻）](#五生产运行linuxsystemd-常驻)
6. [单端口模型与进程数](#六单端口模型与进程数)
7. [反向代理与 HTTPS](#七反向代理与-https)
8. [安全加固清单](#八安全加固清单)
9. [数据与备份](#九数据与备份)
10. [升级版本](#十升级版本)
11. [监控与健康检查](#十一监控与健康检查)
12. [常见部署问题](#十二常见部署问题)

---

## 一、部署形态与前置准备

| 形态 | 适合 | 三种做法 |
|---|---|---|
| 本机自用 | 单人单机 | `build.bat` → `start.bat` |
| 局域网共享 | 家庭/办公室多个设备 | 后端绑定 `0.0.0.0`，访问 `http://<主机IP>:8000/` |
| 公网服务器 | 长期对外 | Linux + systemd 常驻 + 反向代理 + HTTPS |

后端是**单文件 SQLite**（`backend/data/viewer.db`）+ 磁盘图片缓存
（`backend/data/cache/`），部署起来非常轻：**无独立数据库服务、无 Redis**。

### 前置准备

- Python ≥ 3.11（推荐 3.12–3.14，3.14 会自动使用 dukpy JS 引擎）；运行期**必须** Python。
- Node.js ≥ 20（**仅首次构建 / 每次升级需要**；构建完 dist 后运行期不再需要）。
- 一台能长期开机的机器（本机/内网主机/云服务器），以及给它配置的域名与证书（公网时才需要）。

---

## 二、构建（一次性或随版本更新）

在**构建机**（或部署机本身上）把前端产物和 Python 依赖都准备好：

```bash
# Windows：一键
build.bat

# 或手动（跨平台）
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
source .venv/bin/activate && pip install -r requirements.txt
cd ../frontend && npm ci && npm run build     # 产物 → frontend/dist
```

产出的可运行结果是：`backend/`（含 `.venv` 与 `app/`）+ `frontend/dist/`。
把**整个项目目录**拷到生产机即可（或直接在服务器上执行上面的构建）。

> 部署机若没装 Node：把 `frontend/dist/` 一起拷过去，跳过 frontend 步骤即可。
> 运行期完全由后端服务这些静态产物。

---

## 三、运行期只需 Python 的说明

后端启动时会：
- 自动创建数据库 `backend/data/viewer.db` 并播种默认管理员与权限组；
- 自动创建 `backend/data/cache/`（封面代理磁盘缓存）；
- 若存在 `frontend/dist/`，把整站 SPA 挂载在 `/` 上（不存在则只有 API）。

因此**只要有 Python + 依赖 + `frontend/dist`** 就能完整运行整站。

---

## 四、生产运行：Windows

```bat
:: 局域网共享：绑定 0.0.0.0，其他设备访问 http://<本机IP>:8000/
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- `start.bat` 默认就是 `--host 0.0.0.0 --port 8000` 并自动打开浏览器，等价于上面这行；
- 想让登录态在**重启后保持**，务必先配 `VIEWER_SECRET_KEY`（见第八节），否则每次重启全体掉登录；
- 需要「关了窗口还在跑」，Windows 建议注册为计划任务（开机自启）或用 **NSSM / WinSW** 把 uvicorn 包装成 Windows 服务。

---

## 五、生产运行：Linux（systemd 常驻）

以 `/opt/viewer` 为例，创建 systemd 服务使其开机自启、崩溃自动拉起：

`/etc/systemd/system/viewer.service`：

```ini
[Unit]
Description=Viewer reading site
After=network.target

[Service]
Type=simple
User=viewer
WorkingDirectory=/opt/viewer/backend
EnvironmentFile=/opt/viewer/backend/.env
# 只允许本机访问；对外由反向代理转发，HTTPS 交给代理层
ExecStart=/opt/viewer/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo useradd -r -s /usr/sbin/nologin viewer
sudo chown -R viewer:viewer /opt/viewer
sudo systemctl daemon-reload
sudo systemctl enable --now viewer
sudo systemctl status viewer
```

> `Requirement`：`EnvironmentFile` 里写 `VIEWER_*` 变量（见第八节）。
> 健康探测用 `GET /api/health`（公开接口，返回 `{"status":"ok",...}`）。

---

## 六、单端口模型与进程数

- 后端把前端 SPA、`/api/*`、`/dav/*`（WebDAV）都挂在同一个端口，一轮 `uvicorn` 即整站。
- **建议保持单进程**（`uvicorn` 默认 1 worker，对一个 SQLite + 内存缓存的应用是最稳妥的组合）：
  - 数据库是单文件 SQLite，多进程并发写会加剧锁竞争；
  - 章节内容 LRU、插件启停等是**进程内**状态，多 worker 会各自维护一份、且登录/状态在不同 worker 间可能不一致。
- 确实需要高并发时，优先考虑：升级到 PostgreSQL（把 `VIEWER_DATABASE_URL` 换成
  `postgresql+asyncpg://...`）+ 每个实例共享同一台 DB，再横向加进程/实例。
  不要在 SQLite 上硬开多 worker。

---

## 七、反向代理与 HTTPS

公网部署强烈建议在 uvicorn 前面放 Nginx / Caddy，由代理层终结 TLS。

Nginx 示例（HTTP 即可；换成 `listen 443 ssl` 并配证书即 HTTPS）：

```nginx
server {
    listen 80;
    server_name viewer.example.com;

    client_max_body_size 64m;   # 书源正文/导入可能较大

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }
    # /api 与 /dav 都走同一个后端，无需单独配置；
    # 生产 SPA 不使用 WebSocket，所以无需 upgrade 头。
}
```

- 证书与自动续期：Caddy 一条 `reverse_proxy` 指令即可自动签发；Nginx 用 certbot。
- 改端口/域名后，若以非 80 端口在**开发**态访问，需按需调整 `VIEWER_CORS_ORIGINS`（生产同域名下一般不需要）。

---

## 八、安全加固清单

上线前逐项核对：

1. **固定 `VIEWER_SECRET_KEY`**（否则 JWT 每次重启随机，全体掉登录）：
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
   写入 `backend/.env`：
   ```dotenv
   VIEWER_SECRET_KEY=<上面生成的字符串>
   VIEWER_FIRST_ADMIN_USERNAME=admin
   VIEWER_FIRST_ADMIN_PASSWORD=<一个强密码>
   ```
2. **首次启动后立即改默认管理员密码**（默认 `admin / view123456`；或用环境变量覆盖后再建库）。
3. 只暴露必要端口：公网服务器上让 **8000 只监听 `127.0.0.1`**（systemd 示例），对外一律走反向代理/HTTPS。
4. 按需收紧 `VIEWER_TOKEN_EXPIRE_MINUTES`（默认 14 天）。
5. `.env` 含密钥，**不要提交 git**（`.gitignore` 已忽略 `.env`）。
6. WebDAV 自动备份会用本站数据库里的 base64 混淆密码请求网盘——保管好账号、勿外泄部署目录。
7. 应用本身不设 TLS，**把 HTTPS 交给反向代理**，别让裸 HTTP 直接对外。

---

## 九、数据与备份

需要备份的只有一处目录：`backend/data/`

```
backend/data/
├── viewer.db            # 全部业务数据：用户/书源/书架/进度/统计/WebDAV 配置
└── cache/               # 封面代理磁盘图片缓存（LRU，可丢弃，丢失只是重新抓）
```

备份建议：

- 库是 SQLite，**不要在服务运行中直接复制 `.db` 文件**（可能拷到写了一半的页）。
  停机备份最简单；不停机可用 SQLite 在线备份工具：
  ```bash
  sqlite3 backend/data/viewer.db ".backup '/backup/viewer-$(date +%F).db'"
  ```
- 本站内置 **WebDAV 备份**：配置一个网盘账号后，可「立即备份」/「每日自动备份」
  书架、阅读进度、阅读统计，并能从云端列表恢复——适合免运维的异地备份。
- 图片缓存不需备份，重启后按 LRU 自动重建。

---

## 十、升级版本

```bash
git pull                       # 取最新代码
# Windows：
build.bat                      # 重装依赖 + 重构建前端口
# 或手动：重建前端 + 升级依赖
cd backend && .venv/bin/pip install -r requirements.txt --upgrade
cd ../frontend && npm ci && npm run build
# 若有 systemd：重启服务
sudo systemctl restart viewer
```

数据库**无需手工迁移**：启动时自动建表/补字段并保持既有数据；
唯一“不可逆”的动作是首次建库播种管理员（默认名/默认密码只在首次建库时生效）。

---

## 十一、监控与健康检查

- 健康接口：`GET /api/health`（公开）→ `200 {"status":"ok","app":"Viewer","plugins":{...}}`。
  可用作负载均衡 / 系统监控的探活目标（systemd 示例里的 Restart 只在本进程崩溃时生效，
  建议原地编排一个对 `/api/health` 的定时探测）。
- 日志：
  - systemd：`journalctl -u viewer -f`
  - 直接 uvicorn：`--log-level info`（默认）；Access 日志默认打印。
- 观察点：书源抓取慢/失败、`backend/data/` 是否持续增长（图片缓存会按 LRU 收敛到
  `VIEWER_IMAGE_CACHE_MB` 附近）、每日自动更新是否在 `VIEWER_DAILY_REFRESH_HOUR` 前后跑完。

---

## 十二、常见部署问题

**Q1：重启后大家掉登录？** 没设 `VIEWER_SECRET_KEY`，见第八节第 1 步。

**Q2：局域网其他设备打不开 8000？** 后端要绑定 `0.0.0.0`（`--host 0.0.0.0`），
且放行防火墙该端口；`start.bat` 与 systemd 示例都已绑定。

**Q3：多 worker 后书架/进度异常？** 见第六节——SQLite + 进程内缓存，建议单 worker。

**Q4：时区 / 自动更新时间**：`VIEWER_DAILY_REFRESH_HOUR`（默认 4）取**服务器本地时间**，
部署在非本地时区的主机上记得换算。

**Q5：SSL/反向代理后图片代理或接口异常？** 应用本身不产生绝对地址，把 `/`、`/api`、
`/dav` 整体反代即可；无需单独配 WebSocket 转发。

---

## 部署检查清单（Actions）

- [ ] `backend/.env`：固定 `VIEWER_SECRET_KEY`，覆盖默认管理员密码
- [ ] 生产机有 Python；`frontend/dist` 就位或被构建
- [ ] 只对局域网/公网需要的接口绑定或由反代包裹 HTTPS
- [ ] 确认 `/api/health` 返回 `ok`
- [ ] 设置 WebDAV 每日自动备份（异地兜底）
- [ ] 记录服务器 **时区**，核对每日自动更新时间
- [ ] 验证：登录（用新密码）→ 导入书源 → 搜索/阅读 全链路

---

*与 [installation-guide.md](installation-guide.md) 互补：前者讲本机安装与开发，后者讲长期对外部署。配置项全表见配置文档 / `backend/app/core/config.py`。*