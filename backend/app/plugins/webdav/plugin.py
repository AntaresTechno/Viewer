"""webdav 插件 — 把书架 / 阅读进度 / 阅读统计备份到任意 WebDAV 网盘。

兼容坚果云、InfiniCloud、Alist、Nextcloud 等 WebDAV 服务：

- ``PUT /api/webdav/config``        保存服务器配置（密码混淆存储，接口不回显）
- ``POST /api/webdav/test``         测试连接（PROPFIND Depth:0）
- ``POST /api/webdav/backup``       立即备份：生成 JSON 上传到 远端目录
- ``GET  /api/webdav/backups``      列出远端目录里的备份文件
- ``POST /api/webdav/restore``      按文件名恢复（合并语义，不删除本地已有）
- ``DELETE /api/webdav/backups/{name}`` 删除远端某份备份

同时内置 **WebDAV 服务端**（legado 进度同步专用路径 ``/dav``）：

- ``GET  /api/webdav/server``        查询服务端状态/地址/账号
- ``PUT  /api/webdav/server``        开启或关闭服务端
- ``POST /api/webdav/server/secret`` 生成/重置独立访问密码（仅返回一次）

以及 **legado 备份同步**（复用同一远端服务器，连接外部的 WebDAV，见
``legado_sync.py``）：

- ``GET  /api/webdav/legado``        查询 legado 同步配置/最近同步时间
- ``PUT  /api/webdav/legado``        保存 legado 目录并开关同步
- ``POST /api/webdav/legado/sync``   双向/拉取/推送 legado 阅读进度
- ``POST /api/webdav/legado/import`` 从最新 legado 全量备份导入书架（可选）

每日自动备份由 daily_refresh 服务在目录刷新后触发（配置里 auto_backup 开启）。
"""

import base64
import json as _json
import re
import secrets
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

meta = {
    "name": "webdav",
    "mount": "webdav",
    "title": "WebDAV 备份",
    "version": "1.2.0",
    "description": "书架/进度/统计备份到 WebDAV 网盘；内置 legado 兼容的 WebDAV 服务端（/dav 进度同步）与外部 WebDAV 的 legado 备份同步",
    "order": 40,
    "permissions": [("webdav.use", "使用 WebDAV 备份/同步")],
    # 站点根路径专用路由：legado 同步服务端（见 dav_server.py）
    "mount_root": "dav",
}

_DAV_NS = "{DAV:}"
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,200}\.json$")


def _enc_pwd(pwd: str) -> str:
    return base64.b64encode(pwd.encode("utf-8")).decode("ascii") if pwd else ""


def _dec_pwd(enc: str) -> str:
    try:
        return base64.b64decode(enc.encode("ascii")).decode("utf-8") if enc else ""
    except Exception:  # noqa: BLE001
        return ""


def _join_url(base: str, directory: str, name: str = "") -> str:
    """拼 WebDAV 完整地址：base + 目录 + 文件名（各段做 URL 编码）。"""
    base = (base or "").rstrip("/")
    dir_part = "/".join(
        quote(seg.strip(), safe="") for seg in (directory or "").split("/") if seg.strip()
    )
    url = f"{base}/{dir_part}" if dir_part else base
    if name:
        url = f"{url}/{quote(name, safe='')}"
    return url


# ------------------------------------------------------------------ DAV I/O
async def dav_request(
    method: str,
    url: str,
    *,
    username: str = "",
    password: str = "",
    data: bytes | None = None,
    headers: dict | None = None,
    timeout: float = 25.0,
):
    """发起一个带 Basic Auth 的 WebDAV 请求，返回 (status, body_bytes, headers)。"""
    import httpx

    auth = (username, password) if username or password else None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.request(
            method, url, content=data, headers=headers or {}, auth=auth
        )
        return resp.status_code, resp.content, dict(resp.headers)


def _parse_propfind(body: bytes) -> list[dict]:
    """解析 multistatus XML → [{name, href, size, modified, isDir}]。"""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"WebDAV 响应不是合法 XML: {exc}") from exc
    items = []
    for resp in root.iter(f"{_DAV_NS}response"):
        href_el = resp.find(f"{_DAV_NS}href")
        href = (href_el.text or "") if href_el is not None else ""
        prop = resp.find(f"{_DAV_NS}propstat/{_DAV_NS}prop")
        size, modified, is_dir = 0, "", False
        if prop is not None:
            len_el = prop.find(f"{_DAV_NS}getcontentlength")
            if len_el is not None and len_el.text and len_el.text.isdigit():
                size = int(len_el.text)
            mod_el = prop.find(f"{_DAV_NS}getlastmodified")
            if mod_el is not None and mod_el.text:
                modified = mod_el.text
            if prop.find(f"{_DAV_NS}collection") is not None:
                is_dir = True
        name = href.rstrip("/").rsplit("/", 1)[-1]
        from urllib.parse import unquote

        items.append({
            "name": unquote(name),
            "href": href,
            "size": size,
            "modified": modified,
            "isDir": is_dir,
        })
    return items


def _http_error(status: int, body: bytes, action: str) -> HTTPException:
    hint = {
        401: "用户名或密码错误",
        403: "没有权限访问该目录",
        404: "目录不存在（可先保存配置后再试，或在网盘里创建目录）",
        502: "上游服务错误",
    }.get(status, "")
    detail = body[:200].decode("utf-8", "ignore") if body else ""
    msg = f"WebDAV {action}失败（HTTP {status}）{('：' + hint) if hint else ''}"
    if detail and not hint:
        msg += f"：{detail}"
    return HTTPException(502, msg)


# ------------------------------------------------------------- 备份内容构建
async def build_backup_payload(user_id: int) -> tuple[str, dict]:
    """收集某用户的全部可备份数据 → (payload_json_str, counts)。

    供本插件与 daily_refresh 自动备份共用。
    """
    from sqlalchemy import select

    from ...core.db import get_session_factory
    from ...models import ReadingStat, ReadProgress, ShelfItem, User

    factory = get_session_factory()
    async with factory() as db:
        user = await db.get(User, user_id)
        shelf = (await db.execute(
            select(ShelfItem).where(ShelfItem.user_id == user_id)
        )).scalars().all()
        progress = (await db.execute(
            select(ReadProgress).where(ReadProgress.user_id == user_id)
        )).scalars().all()
        stats = (await db.execute(
            select(ReadingStat).where(ReadingStat.user_id == user_id)
        )).scalars().all()

        def iso(dt):
            return dt.isoformat() if dt else None

        payload = {
            "app": "antares-viewer",
            "version": 1,
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "user": {
                "id": user.id,
                "username": user.username if user else "",
            } if user else {},
            "shelf": [
                {
                    "bookUrl": s.book_url,
                    "tocUrl": s.toc_url,
                    "name": s.name,
                    "author": s.author,
                    "coverUrl": s.cover_url,
                    "intro": s.intro,
                    "lastChapter": s.last_chapter,
                    "sourceUrl": s.source_url,
                    "createdAt": iso(s.created_at),
                }
                for s in shelf
            ],
            "progress": [
                {
                    "bookUrl": p.book_url,
                    "chapterIndex": p.chapter_index,
                    "chapterTitle": p.chapter_title,
                    "offset": p.offset,
                    "updatedAt": iso(p.updated_at),
                }
                for p in progress
            ],
            "readingStats": [
                {"day": r.day, "bookUrl": r.book_url, "seconds": r.seconds}
                for r in stats
            ],
        }
        counts = {
            "shelf": len(shelf),
            "progress": len(progress),
            "readingStats": len(stats),
        }
    return _json.dumps(payload, ensure_ascii=False), counts


async def run_backup(user_id: int) -> dict:
    """执行一次完整备份（读取配置 → 构建 → PUT）。返回文件名与条目数。"""
    from sqlalchemy import select

    from ...core.config import settings
    from ...core.db import get_session_factory
    from ...models import WebDavConfig

    factory = get_session_factory()
    async with factory() as db:
        cfg = await db.scalar(
            select(WebDavConfig).where(WebDavConfig.user_id == user_id)
        )
    if cfg is None or not cfg.url:
        raise HTTPException(400, "尚未配置 WebDAV 服务器")

    payload, counts = await build_backup_payload(user_id)
    filename = (
        "antares-backup-"
        + datetime.now().strftime("%Y%m%d-%H%M%S")
        + ".json"
    )
    url = _join_url(cfg.url, cfg.directory, filename)

    async def _put() -> int:
        status, body_bytes, _h = await dav_request(
            "PUT", url,
            username=cfg.username, password=_dec_pwd(cfg.password_enc),
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        _last_error_body[0] = body_bytes
        return status

    # 首次备份时目录可能不存在：先直接 PUT，409/404 则 MKCOL 建目录后重试
    _last_error_body = [b""]
    status = await _put()
    if status in (409, 404):
        col = _join_url(cfg.url, cfg.directory)
        mk, mbody, _h = await dav_request(
            "MKCOL", col,
            username=cfg.username, password=_dec_pwd(cfg.password_enc),
            timeout=settings.request_timeout * 4,
        )
        # 目录已存在(405)或创建成功都继续尝试上传
        if mk not in (200, 201, 405):
            raise _http_error(mk, mbody, "创建远程目录")
        status = await _put()
    if status not in (200, 201, 204):
        raise _http_error(status, _last_error_body[0], "上传备份")

    async with factory() as db:
        cfg2 = await db.scalar(
            select(WebDavConfig).where(WebDavConfig.user_id == user_id)
        )
        if cfg2 is not None:
            cfg2.last_backup_at = datetime.now(timezone.utc).astimezone()
            cfg2.last_backup_file = filename
            db.add(cfg2)
            await db.commit()
    return {"file": filename, **counts}


async def run_auto_backup_if_enabled(user_id: int) -> bool:
    """daily_refresh 调用：auto_backup 开启且插件启用时备份一次。"""
    from sqlalchemy import select

    from ...core.db import get_session_factory
    from ...models import WebDavConfig
    from ...plugins.registry import plugin_enabled

    if not plugin_enabled("webdav"):
        return False
    factory = get_session_factory()
    async with factory() as db:
        cfg = await db.scalar(
            select(WebDavConfig).where(WebDavConfig.user_id == user_id)
        )
    if cfg is None or not cfg.auto_backup:
        return False
    await run_backup(user_id)
    return True


def create_root_router(ctx: "PluginContext") -> APIRouter:
    """站点根路径专用路由工厂：legado 进度同步服务端（挂载于 /dav）。"""
    from .dav_server import create_root_router as _create

    return _create(ctx)


def create_router(ctx: "PluginContext") -> APIRouter:
    from sqlalchemy import select

    from ...core.deps import get_current_user, require_perm
    from ...core.db import get_db
    from ...models import (
        DavResource,
        ReadingStat,
        ReadProgress,
        ShelfItem,
        WebDavConfig,
    )

    def _aware(dt):
        """SQLite 常返回 naive datetime：补上本地时区便于与远端时间比较。"""
        if dt is None:
            return None
        return dt.astimezone() if dt.tzinfo else dt.astimezone()

    router = APIRouter(tags=["webdav"])

    def _cfg_dict(c: WebDavConfig | None) -> dict:
        if c is None:
            return {
                "url": "", "username": "", "directory": "AntaresViewer",
                "hasPassword": False, "autoBackup": False,
                "lastBackupAt": None, "lastBackupFile": "",
                "legadoEnabled": False, "legadoDirectory": "legado",
                "legadoLastSyncAt": None,
            }
        return {
            "url": c.url,
            "username": c.username,
            "directory": c.directory or "AntaresViewer",
            "hasPassword": bool(c.password_enc),
            "autoBackup": bool(c.auto_backup),
            "lastBackupAt": c.last_backup_at.isoformat() if c.last_backup_at else None,
            "lastBackupFile": c.last_backup_file or "",
            "legadoEnabled": bool(c.legado_enabled),
            "legadoDirectory": c.legado_directory or "legado",
            "legadoLastSyncAt": (c.legado_last_sync_at.isoformat()
                                 if c.legado_last_sync_at else None),
        }

    @router.get("/config")
    async def get_config(
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        c = await db.get(WebDavConfig, user.id)
        return _cfg_dict(c)

    class ConfigBody(BaseModel):
        url: str = Field(default="", max_length=512)
        username: str = Field(default="", max_length=256)
        # 为空表示保留原密码；"-clear" 显式清空
        password: str = Field(default="", max_length=256)
        directory: str = Field(default="AntaresViewer", max_length=256)
        autoBackup: bool = False

    @router.put("/config")
    async def save_config(
        body: ConfigBody,
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        url = body.url.strip()
        if url and not url.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "WebDAV 地址必须以 http(s):// 开头")
        c = await db.get(WebDavConfig, user.id)
        if c is None:
            c = WebDavConfig(user_id=user.id)
            db.add(c)
        c.url = url
        c.username = body.username.strip()
        if body.password == "-clear":
            c.password_enc = ""
        elif body.password:
            c.password_enc = _enc_pwd(body.password)
        c.directory = body.directory.strip().strip("/")
        c.auto_backup = body.autoBackup
        await db.commit()
        return {"ok": True, **_cfg_dict(c)}

    @router.post("/test")
    async def test_conn(
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        """对配置的服务器发 PROPFIND(Depth:0) 验证连通性与凭据。"""
        user, _ = current
        c = await db.get(WebDavConfig, user.id)
        if c is None or not c.url:
            raise HTTPException(400, "请先填写 WebDAV 服务器地址")
        url = _join_url(c.url, c.directory)
        status, body, _h = await dav_request(
            "PROPFIND", url,
            username=c.username, password=_dec_pwd(c.password_enc),
            headers={"Depth": "0"},
        )
        if status in (207, 200):
            return {"ok": True}
        raise _http_error(status, body, "连接测试")

    @router.post("/backup")
    async def backup_now(
        current=Depends(require_perm("webdav.use")),
    ):
        user, _ = current
        return await run_backup(user.id)

    @router.get("/backups")
    async def list_backups(
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        c = await db.get(WebDavConfig, user.id)
        if c is None or not c.url:
            raise HTTPException(400, "尚未配置 WebDAV 服务器")
        url = _join_url(c.url, c.directory)
        status, body, _h = await dav_request(
            "PROPFIND", url,
            username=c.username, password=_dec_pwd(c.password_enc),
            headers={"Depth": "1"},
        )
        if status != 207:
            raise _http_error(status, body, "列取备份")
        items = [
            it for it in _parse_propfind(body)
            if not it["isDir"] and it["name"].lower().endswith(".json")
        ]
        items.sort(key=lambda x: x["modified"], reverse=True)
        return {"items": items[:100]}

    class RestoreBody(BaseModel):
        file: str = Field(min_length=1, max_length=220)

    @router.post("/restore")
    async def restore(
        body: RestoreBody,
        current=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        """从远端备份恢复（合并）：书架补齐、进度按“更新者胜”合并。"""
        user, perms = current
        allowed = (
            user.is_superuser or "*" in perms or "webdav.*" in perms
            or "webdav.use" in perms
        )
        if not allowed:
            raise HTTPException(403, "权限不足：webdav.use")
        fname = body.file.strip()
        if not _FILENAME_RE.fullmatch(fname):
            raise HTTPException(400, "非法文件名")
        c = await db.get(WebDavConfig, user.id)
        if c is None or not c.url:
            raise HTTPException(400, "尚未配置 WebDAV 服务器")

        url = _join_url(c.url, c.directory, fname)
        status, raw, _h = await dav_request(
            "GET", url,
            username=c.username, password=_dec_pwd(c.password_enc),
        )
        if status != 200:
            raise _http_error(status, raw, "下载备份")
        try:
            data = _json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"备份文件解析失败: {exc}") from exc
        if not isinstance(data, dict) or data.get("app") != "antares-viewer":
            raise HTTPException(400, "不是 Antares Viewer 的备份文件")

        from datetime import datetime as _dt

        def parse_dt(v):
            if not v:
                return None
            try:
                dt = _dt.fromisoformat(str(v))
            except ValueError:
                return None
            return dt.astimezone() if dt.tzinfo else dt.astimezone()

        shelf_added = shelf_updated = prog_updated = stat_merged = 0
        existing_shelf = {
            s.book_url: s for s in (await db.execute(
                select(ShelfItem).where(ShelfItem.user_id == user.id)
            )).scalars().all()
        }
        for item in data.get("shelf", []) or []:
            book_url = str(item.get("bookUrl") or "")
            if not book_url:
                continue
            row = existing_shelf.get(book_url)
            if row is None:
                row = ShelfItem(user_id=user.id, book_url=book_url)
                db.add(row)
                existing_shelf[book_url] = row
                shelf_added += 1
            else:
                shelf_updated += 1
            for attr, col in (
                ("tocUrl", "toc_url"), ("name", "name"), ("author", "author"),
                ("coverUrl", "cover_url"), ("intro", "intro"),
                ("lastChapter", "last_chapter"), ("sourceUrl", "source_url"),
            ):
                v = str(item.get(attr) or "")
                if v:
                    setattr(row, col, v)

        existing_prog = {
            p.book_url: p for p in (await db.execute(
                select(ReadProgress).where(ReadProgress.user_id == user.id)
            )).scalars().all()
        }
        for item in data.get("progress", []) or []:
            book_url = str(item.get("bookUrl") or "")
            remote_at = parse_dt(item.get("updatedAt"))
            row = existing_prog.get(book_url)
            if row is None:
                row = ReadProgress(
                    user_id=user.id, book_url=book_url,
                    chapter_index=int(item.get("chapterIndex") or 0),
                    chapter_title=str(item.get("chapterTitle") or ""),
                    offset=int(item.get("offset") or 0),
                )
                if remote_at:
                    row.updated_at = remote_at
                db.add(row)
                existing_prog[book_url] = row
                prog_updated += 1
            else:
                local_at = _aware(row.updated_at)
                if local_at is None or (remote_at and remote_at > local_at):
                    row.chapter_index = int(item.get("chapterIndex") or 0)
                    row.chapter_title = str(item.get("chapterTitle") or "")
                    row.offset = int(item.get("offset") or 0)
                    if remote_at:
                        row.updated_at = remote_at
                    prog_updated += 1

        existing_stat = {
            (r.day, r.book_url): r for r in (await db.execute(
                select(ReadingStat).where(ReadingStat.user_id == user.id)
            )).scalars().all()
        }
        for item in data.get("readingStats", []) or []:
            day = str(item.get("day") or "")
            book_url = str(item.get("bookUrl") or "")
            secs = max(0, int(item.get("seconds") or 0))
            if not day or not book_url or not secs:
                continue
            key = (day, book_url)
            row = existing_stat.get(key)
            if row is None:
                db.add(ReadingStat(
                    user_id=user.id, day=day, book_url=book_url, seconds=secs,
                ))
                existing_stat[key] = ReadingStat(
                    user_id=user.id, day=day, book_url=book_url, seconds=secs,
                )
                stat_merged += 1
            elif secs > (row.seconds or 0):
                row.seconds = secs
                stat_merged += 1

        await db.commit()
        return {
            "ok": True,
            "shelfAdded": shelf_added,
            "shelfUpdated": shelf_updated,
            "progressUpdated": prog_updated,
            "statsMerged": stat_merged,
        }

    @router.delete("/backups/{name}")
    async def delete_backup(
        name: str,
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        if not _FILENAME_RE.fullmatch(name):
            raise HTTPException(400, "非法文件名")
        c = await db.get(WebDavConfig, user.id)
        if c is None or not c.url:
            raise HTTPException(400, "尚未配置 WebDAV 服务器")
        url = _join_url(c.url, c.directory, name)
        status, body, _h = await dav_request(
            "DELETE", url,
            username=c.username, password=_dec_pwd(c.password_enc),
        )
        if status not in (200, 202, 204, 404):
            raise _http_error(status, body, "删除备份")
        return {"ok": True}

    # ------------------------------------------------- WebDAV 服务端（legado 同步）
    @router.get("/server")
    async def get_server(
        request: Request,
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        """服务端状态：开关、专用路径地址、账号、最近同步时间。"""
        user, _ = current
        c = await db.get(WebDavConfig, user.id)
        base = str(request.base_url).rstrip("/")
        return {
            "enabled": bool(c is not None and c.dav_enabled),
            "hasSecret": bool(c is not None and c.dav_secret_hash),
            # legado 里配置的 WebDAV 地址（专用路径）
            "url": f"{base}/dav/legado/",
            "account": user.username,
            "lastSyncAt": c.last_sync_at.isoformat() if c and c.last_sync_at else None,
        }

    class ServerBody(BaseModel):
        enabled: bool

    @router.put("/server")
    async def save_server(
        body: ServerBody,
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        """开启 / 关闭 WebDAV 服务端。"""
        user, _ = current
        c = await db.get(WebDavConfig, user.id)
        if c is None:
            c = WebDavConfig(user_id=user.id)
            db.add(c)
        c.dav_enabled = body.enabled
        await db.commit()
        return {"ok": True, "enabled": bool(c.dav_enabled)}

    @router.post("/server/secret")
    async def reset_server_secret(
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        """生成（或重置）服务端访问密码；明文仅本次返回，之后只存哈希。"""
        from ...core.security import hash_password

        user, _ = current
        secret = secrets.token_urlsafe(18)
        c = await db.get(WebDavConfig, user.id)
        if c is None:
            c = WebDavConfig(user_id=user.id)
            db.add(c)
        c.dav_secret_hash = hash_password(secret)
        c.dav_enabled = True  # 生成密码即视为启用
        await db.commit()
        return {"ok": True, "secret": secret}

    @router.get("/server/pending")
    async def server_pending(
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        """待匹配列表：legado 同步过来、但书架里还没有对应书籍的进度文件。

        这些书会在后台用书源自动搜索入库；搜不到的停留在此处，
        手动在本站搜索加入同名书籍后进度会自动关联。
        """
        from .dav_server import _load_locals, _parse_payload

        user, _ = current
        rows = (await db.execute(
            select(DavResource).where(
                DavResource.user_id == user.id,
                DavResource.path.like("bookProgress/%"),
            ).order_by(DavResource.updated_at.desc())
        )).scalars().all()
        matched = await _load_locals(db, user.id)
        items = []
        for r in rows:
            fname = r.path.split("/", 1)[1]
            if fname in matched:
                continue
            data = _parse_payload(r.content) or {}
            items.append({
                "file": fname,
                "name": str(data.get("name") or ""),
                "author": str(data.get("author") or ""),
                "chapterIndex": int(data.get("durChapterIndex") or 0),
                "updatedAt": r.updated_at.isoformat() if r.updated_at else None,
            })
        return {"items": items[:50], "total": len(items)}

    # ------------------------------------------------- legado 备份同步（外部服务器）
    @router.get("/legado")
    async def get_legado_sync(
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        """legado 备份同步配置：复用本插件服务器，另存 legado 目录。"""
        user, _ = current
        c = await db.get(WebDavConfig, user.id)
        return _cfg_dict(c)

    class LegadoBody(BaseModel):
        enabled: bool = True
        directory: str = Field(default="legado", max_length=256)

    @router.put("/legado")
    async def save_legado_sync(
        body: LegadoBody,
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        """保存 legado 同步目录并设置开关。"""
        user, _ = current
        c = await db.get(WebDavConfig, user.id)
        if c is None:
            c = WebDavConfig(user_id=user.id)
            db.add(c)
        c.legado_enabled = body.enabled
        c.legado_directory = body.directory.strip().strip("/")
        await db.commit()
        return {"ok": True, **_cfg_dict(c)}

    @router.post("/legado/sync")
    async def legado_sync_now(
        body: dict | None = None,
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        """与 legado 做双向/拉取/推送进度同步。body 可选 {"direction":"both|pull|push"}。"""
        from .legado_sync import sync_progress

        user, _ = current
        c = await db.get(WebDavConfig, user.id)
        if c is None or not c.url:
            raise HTTPException(400, "尚未配置本插件的 WebDAV 服务器")
        if not c.legado_enabled:
            raise HTTPException(400, "legado 同步未开启：请先点击开关开启")
        direction = "both"
        if isinstance(body, dict) and body.get("direction") in (
                "both", "pull", "push"):
            direction = body["direction"]
        result = await sync_progress(user.id, c, direction)
        await db.refresh(c)
        return {"ok": True, "direction": direction,
                "legadoLastSyncAt": (c.legado_last_sync_at.isoformat()
                                     if c.legado_last_sync_at else None),
                **result}

    @router.post("/legado/import")
    async def legado_import_shelf(
        current=Depends(require_perm("webdav.use")),
        db: AsyncSession = Depends(get_db),
    ):
        """（可选）从最新 legado 全量备份 backup*.zip 导入书架与进度。"""
        from .legado_sync import import_shelf

        user, _ = current
        c = await db.get(WebDavConfig, user.id)
        if c is None or not c.url:
            raise HTTPException(400, "尚未配置本插件的 WebDAV 服务器")
        if not c.legado_enabled:
            raise HTTPException(400, "legado 同步未开启：请先点击开关开启")
        return {"ok": True, **await import_shelf(user.id, c)}

    return router
