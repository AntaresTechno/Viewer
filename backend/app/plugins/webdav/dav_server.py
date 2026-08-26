"""WebDAV 服务端 — 让阅读(legado)客户端把本站当作进度同步服务器。

专用路径挂载在 ``/dav``（meta["mount_root"]），与 ``/api`` 完全独立。
兼容 legado AppWebDav 的同步方式（见仓库内 legado-with-MD3 源码）：

- 认证：HTTP Basic（账号=本站登录名，密码=插件里生成的独立访问密钥）
- 进度目录：``{配置URL}/bookProgress/{书名}_{作者}.json``
- 上传进度：``PUT``，内容为 BookProgress JSON：
  ``{"name","author","durChapterIndex","durChapterPos","durChapterTime","durChapterTitle"}``
- 拉取进度：``GET`` 同名文件；先 ``PROPFIND Depth:1`` 列目录，
  用 ``getlastmodified`` 与本地 syncTime 比较决定是否下载
- 初始化：``PROPFIND Depth:0`` 探测 + 对若干子目录 ``MKCOL``（此处一律成功）

双向合并策略：

- legado 上传的进度原样存入 dav_resources（保证其他 legado 设备可读回），
  同时当书架里存在 书名+作者 匹配的书籍时，把进度按"新者胜"合并进 ReadProgress；
- 网页端的 ReadProgress 反向合成为同名进度文件：GET 时若本地更新则返回本地值，
  PROPFIND 列表的时间戳也取两者较新者，避免 legado 因 syncTime 判断跳过。

仅开放阅读进度资源：``bookProgress/`` 之外的写操作一律拒绝。
"""
from __future__ import annotations

import base64
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_DAV_NS = "{DAV:}"
# legado 文件名清洗：normalizeFileName → replaceReservedChar
_FILE_NAME_BAD = re.compile(r'[\\/:*?"<>|]')
_RESERVED = (
    ("%", "%25"), (" ", "%20"), ('"', "%22"), ("#", "%23"), ("&", "%26"),
    ("(", "%28"), (")", "%29"), ("+", "%2B"), (",", "%2C"), ("/", "%2F"),
    (":", "%3A"), (";", "%3B"), ("<", "%3C"), ("=", "%3D"), (">", "%3E"),
    ("?", "%3F"), ("@", "%40"), ("\\", "%5C"), ("|", "%7C"),
)
_MAX_BODY = 2 * 1024 * 1024


def progress_filename(name: str, author: str) -> str:
    """legado 端 getProgressFileName(name, author) 的等价实现。"""
    raw = _FILE_NAME_BAD.sub("_", f"{name}_{author}")
    for ch, rep in _RESERVED:
        raw = raw.replace(ch, rep)
    return raw + ".json"


def _rfc1123(dt: datetime) -> str:
    dt = dt if dt.tzinfo else dt.astimezone()
    return format_datetime(dt.astimezone(timezone.utc), usegmt=True)


def _iso(dt: datetime) -> str:
    dt = dt if dt.tzinfo else dt.astimezone()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms(dt: datetime | None) -> int:
    """datetime → epoch 毫秒（SQLite 常返回 naive，按本地时区处理）。"""
    if dt is None:
        return 0
    dt = dt if dt.tzinfo else dt.astimezone()
    return int(dt.timestamp() * 1000)


def _err(status: int, msg: str) -> HTTPException:
    return HTTPException(status_code=status, detail=msg)


def _parse_payload(text: str) -> dict | None:
    """解析 legado BookProgress JSON；非法或缺关键字段返回 None。"""
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, dict) and "durChapterIndex" in data:
        return data
    return None


async def _load_locals(db: AsyncSession, user_id: int) -> dict[str, dict]:
    """书架+网页端进度 → {progress文件名: 本地进度条目}。

    同一文件名可能对应多本书（同书不同源/同名书）：有作者信息的优先、
    进度更新的优先。
    """
    from ...models import ReadProgress, ShelfItem

    shelf = (await db.execute(
        select(ShelfItem).where(ShelfItem.user_id == user_id)
    )).scalars().all()
    prog = (await db.execute(
        select(ReadProgress).where(ReadProgress.user_id == user_id)
    )).scalars().all()
    by_url = {p.book_url: p for p in prog}

    locals_: dict[str, dict] = {}
    for s in shelf:
        p = by_url.get(s.book_url)
        if p is None:
            continue
        fname = progress_filename((s.name or "").strip(),
                                  (s.author or "").strip())
        entry = {
            "name": s.name or "", "author": s.author or "",
            "idx": int(p.chapter_index or 0), "pos": int(p.offset or 0),
            "title": p.chapter_title or "", "ms": _ms(p.updated_at),
            "dt": p.updated_at, "book_url": s.book_url,
        }
        cur = locals_.get(fname)
        score = (1 if entry["author"].strip() else 0,
                 entry["ms"], entry["idx"], entry["pos"])
        cur_score = None if cur is None else (
            1 if cur["author"].strip() else 0,
            cur["ms"], cur["idx"], cur["pos"])
        if cur is None or score > cur_score:
            locals_[fname] = entry
    return locals_


def create_root_router(ctx) -> APIRouter:
    from ...core.db import get_db
    from ...core.security import verify_password
    from ...models import DavResource, ReadProgress, Role, ShelfItem, User, WebDavConfig
    from ..registry import plugin_enabled

    router = APIRouter(tags=["webdav-server"])

    # ------------------------------------------------------------ 基础认证
    async def dav_auth(
        request: Request, db: AsyncSession = Depends(get_db)
    ) -> tuple[int, WebDavConfig]:
        """HTTP Basic 认证。账号=本站用户名，密码=插件生成的独立访问密码。"""
        def unauthorized() -> HTTPException:
            return HTTPException(
                401, "Unauthorized",
                headers={"WWW-Authenticate": 'Basic realm="Antares Viewer"'})

        if not plugin_enabled("webdav"):
            raise _err(503, "WebDAV 插件未启用")

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("basic "):
            raise unauthorized()
        try:
            decoded = base64.b64decode(auth[6:].strip()).decode("utf-8")
        except Exception:  # noqa: BLE001
            raise unauthorized() from None
        username, _, password = decoded.partition(":")
        # OkHttp 默认按 ISO-8859-1 编码 Basic 凭据，尽量还原 UTF-8
        try:
            password = password.encode("latin-1").decode("utf-8")
        except Exception:  # noqa: BLE001
            pass

        user = (await db.execute(
            select(User).where(User.username == username)
        )).scalars().first()
        if user is None or not user.is_active:
            raise unauthorized()

        cfg = await db.get(WebDavConfig, user.id)
        if cfg is None or not cfg.dav_enabled or not cfg.dav_secret_hash:
            raise _err(403, "WebDAV 服务端未开启：请在本站 WebDAV 页面开启并生成访问密码")
        if not verify_password(password, cfg.dav_secret_hash):
            raise unauthorized()

        # 权限：superuser 或持有 webdav.use
        if not user.is_superuser:
            perms: set[str] = set()
            for rid in user.role_ids or []:
                role = await db.get(Role, rid)
                if role:
                    perms.update(role.permissions or [])
            if not ({"*", "webdav.use"} & perms):
                raise _err(403, "权限不足：webdav.use")
        return user.id, cfg

    # -------------------------------------------------------- 路径与命名空间
    def _norm_parts(raw: str) -> list[str]:
        parts = [p for p in raw.split("/") if p not in ("", ".")]
        if ".." in parts:
            raise _err(400, "非法路径")
        return parts

    def _rebase(parts: list[str]) -> tuple[str, list[str]]:
        """以最后一个 bookProgress 段为界确定命名空间。

        兼容 legado 里自定义 WebDAV 子目录（AppConfig.webDavDir）的情况：
        无论用户配了 /dav/、/dav/legado/ 还是 /dav/xxx/，进度都落到同一份存储。
        返回 (显示前缀, 命名空间内相对路径段)；无 bookProgress 段时相对段为空
        （即请求落在命名空间根集合上）。
        """
        if "bookProgress" in parts:
            i = len(parts) - 1 - parts[::-1].index("bookProgress")
            return "/".join(parts[:i]), parts[i:]
        return "/".join(parts), []

    def _is_progress_file(ns: list[str]) -> bool:
        return (
            len(ns) == 2 and ns[0] == "bookProgress" and ns[1].endswith(".json")
        )

    # ------------------------------------------------------------- 数据读取
    def _cloud_entry(row: DavResource) -> dict:
        data = _parse_payload(row.content) or {}
        return {
            "name": str(data.get("name") or ""),
            "author": str(data.get("author") or ""),
            "idx": int(data.get("durChapterIndex") or 0),
            "pos": int(data.get("durChapterPos") or 0),
            "title": str(data.get("durChapterTitle") or ""),
            "ms": int(data.get("durChapterTime") or 0),
            "dt": row.updated_at,
        }

    def _build_payload(entry: dict) -> str:
        return json.dumps({
            "name": entry["name"],
            "author": entry["author"],
            "durChapterIndex": int(entry["idx"]),
            "durChapterPos": int(entry["pos"]),
            "durChapterTime": int(entry["ms"]),
            "durChapterTitle": entry["title"],
        }, ensure_ascii=False)

    def _winner(cloud: dict | None,
                local: dict | None) -> tuple[dict | None, str]:
        """新者胜；时间相同比 (章节索引, 章节内位置)。返回 (entry, 来源)。"""
        if cloud is None:
            return local, "local"
        if local is None:
            return cloud, "cloud"
        if local["ms"] > cloud["ms"]:
            return local, "local"
        if cloud["ms"] > local["ms"]:
            return cloud, "cloud"
        if (local["idx"], local["pos"]) > (cloud["idx"], cloud["pos"]):
            return local, "local"
        return cloud, "cloud"

    # ------------------------------------------------------------------ XML
    def _coll_entry(href: str, name: str) -> dict:
        return {"href": href, "name": name, "isDir": True, "size": 0,
                "mtime": datetime.now(timezone.utc)}

    def _multistatus(entries: list[dict]) -> bytes:
        root = ET.Element(f"{_DAV_NS}multistatus")
        for e in entries:
            resp = ET.SubElement(root, f"{_DAV_NS}response")
            ET.SubElement(resp, f"{_DAV_NS}href").text = quote(
                e["href"], safe="/%:@&=+$,;~'()!*[]")
            propstat = ET.SubElement(resp, f"{_DAV_NS}propstat")
            prop = ET.SubElement(propstat, f"{_DAV_NS}prop")
            ET.SubElement(prop, f"{_DAV_NS}displayname").text = e["name"]
            rt = ET.SubElement(prop, f"{_DAV_NS}resourcetype")
            if e["isDir"]:
                ET.SubElement(rt, f"{_DAV_NS}collection")
                ET.SubElement(prop, f"{_DAV_NS}getcontenttype").text = \
                    "httpd/unix-directory"
            else:
                ET.SubElement(prop, f"{_DAV_NS}getcontentlength").text = \
                    str(e.get("size", 0))
                ET.SubElement(prop, f"{_DAV_NS}getcontenttype").text = \
                    "application/json; charset=utf-8"
            ET.SubElement(prop, f"{_DAV_NS}creationdate").text = _iso(e["mtime"])
            ET.SubElement(prop, f"{_DAV_NS}getlastmodified").text = \
                _rfc1123(e["mtime"])
            ET.SubElement(propstat, f"{_DAV_NS}status").text = "HTTP/1.1 200 OK"
        head = b'<?xml version="1.0" encoding="utf-8"?>\n'
        return head + ET.tostring(root, encoding="utf-8")

    def _xml_response(body: bytes) -> Response:
        return Response(content=body, status_code=207,
                        media_type="application/xml; charset=utf-8")

    # -------------------------------------------------------------- 存储访问
    async def _get_resource(db, user_id: int,
                            ns_path: str) -> DavResource | None:
        if not ns_path:
            return None
        return (await db.execute(
            select(DavResource).where(
                DavResource.user_id == user_id, DavResource.path == ns_path)
        )).scalars().first()

    async def _list_progress(db, user_id: int,
                             dir_href: str) -> list[dict]:
        """存储记录 ∪ 网页端本地进度，时间戳取较新者。dir_href 以 / 结尾。"""
        rows = (await db.execute(
            select(DavResource).where(
                DavResource.user_id == user_id,
                DavResource.path.like("bookProgress/%"),
            )
        )).scalars().all()
        merged: dict[str, tuple[datetime, int]] = {}
        for r in rows:
            merged[r.path.split("/", 1)[1]] = (r.updated_at, r.size)
        for fname, ent in (await _load_locals(db, user_id)).items():
            cur = merged.get(fname)
            payload_len = len(_build_payload(ent).encode("utf-8"))
            if cur is None or ent["ms"] > _ms(cur[0]):
                merged[fname] = (ent["dt"], payload_len)
        return [
            {"href": f"{dir_href}{quote(fname, safe='')}", "name": fname,
             "isDir": False, "size": size, "mtime": mtime}
            for fname, (mtime, size) in sorted(merged.items())
        ]

    # ----------------------------------------------------------- 各方法实现
    async def _propfind(request: Request, prefix: str, ns: list[str],
                        user_id: int, db: AsyncSession) -> Response:
        depth = (request.headers.get("depth") or "1").strip().lower()
        req_path = request.url.path
        self_href = req_path if req_path.endswith("/") else req_path + "/"
        parent_href = req_path.rsplit("/", 1)[0] + "/"
        ns_path = "/".join(ns)

        if not ns:
            entries = [_coll_entry(self_href,
                                   prefix.rsplit("/", 1)[-1] or "dav")]
            if depth != "0":
                entries.append(_coll_entry(parent_href + "bookProgress/",
                                           "bookProgress"))
        elif len(ns) == 1:
            entries = [_coll_entry(self_href, ns[-1])]
            if depth != "0" and ns[0] == "bookProgress":
                entries.extend(await _list_progress(db, user_id, self_href))
        elif _is_progress_file(ns):
            row = await _get_resource(db, user_id, ns_path)
            if row is None:
                raise _err(404, "Not Found")
            entries = [{"href": self_href, "name": ns[-1], "isDir": False,
                        "size": row.size, "mtime": row.updated_at}]
        else:
            entries = [_coll_entry(self_href, ns[-1])]
        return _xml_response(_multistatus(entries))

    async def _get_file(ns: list[str], user_id: int, db: AsyncSession) -> Response:
        ns_path = "/".join(ns)
        if not ns_path or "/" not in ns_path:
            # 集合 GET（浏览器直接打开）：返回简短说明
            info = {"service": "antares-viewer webdav",
                    "resources": ["bookProgress"]}
            return Response(content=json.dumps(info, ensure_ascii=False),
                            media_type="application/json; charset=utf-8")
        if not _is_progress_file(ns):
            raise _err(404, "Not Found")
        fname = ns[1]

        row = await _get_resource(db, user_id, ns_path)
        cloud = _cloud_entry(row) if row is not None else None
        local = (await _load_locals(db, user_id)).get(fname)
        if cloud is None and local is None:
            raise _err(404, "Not Found")

        entry, source = _winner(cloud, local)
        assert entry is not None
        payload = (
            row.content
            if source == "cloud" and row is not None
               and _parse_payload(row.content)
            else _build_payload(entry)
        )
        mtime = row.updated_at if row is not None else datetime.now(timezone.utc)
        if source == "cloud" and local is not None and (
            cloud["ms"] > local["ms"]
            or (cloud["ms"] == local["ms"]
                and (cloud["idx"], cloud["pos"]) > (local["idx"], local["pos"]))
        ):
            # 双端同步收敛：云端比网页端新 → 把云端进度写回网页端
            prog_row = (await db.execute(
                select(ReadProgress).where(
                    ReadProgress.user_id == user_id,
                    ReadProgress.book_url == local["book_url"],
                )
            )).scalars().first()
            if prog_row is not None:
                prog_row.chapter_index = int(entry["idx"])
                prog_row.chapter_title = str(entry["title"] or "")
                prog_row.offset = int(entry["pos"])
                prog_row.updated_at = datetime.now(timezone.utc)
                await db.commit()
        if source == "local":
            # 本地更新则回写存储，让目录列表时间与内容保持一致
            data = row if row is not None else DavResource(user_id=user_id,
                                                           path=ns_path)
            data.content = payload
            data.size = len(payload.encode("utf-8"))
            data.updated_at = datetime.now(timezone.utc)
            if row is None:
                db.add(data)
            await db.commit()
            mtime = data.updated_at
        return Response(
            content=payload.encode("utf-8"),
            media_type="application/json; charset=utf-8",
            headers={"Last-Modified": _rfc1123(mtime)},
        )

    async def _mirror_to_progress(db: AsyncSession, user_id: int,
                                  text: str) -> str:
        """把上传的进度按 书名+作者 合并到书架对应书籍（新者胜）。"""
        data = _parse_payload(text)
        if data is None:
            return "skipped"
        name = str(data.get("name") or "").strip()
        author = str(data.get("author") or "").strip()
        try:
            idx = int(data.get("durChapterIndex") or 0)
            pos = int(data.get("durChapterPos") or 0)
            tms = int(data.get("durChapterTime") or 0)
        except (TypeError, ValueError):
            return "skipped"
        title = str(data.get("durChapterTitle") or "")
        if not name:
            return "skipped"

        shelf = (await db.execute(
            select(ShelfItem).where(ShelfItem.user_id == user_id)
        )).scalars().all()
        norm = lambda s: (s or "").strip().casefold()  # noqa: E731
        candidates = [s for s in shelf if norm(s.name) == norm(name)]
        if not candidates:
            return "no-book"
        if author:
            pool = [s for s in candidates if norm(s.author) == norm(author)] \
                or [s for s in candidates if not (s.author or "").strip()] \
                or candidates
        else:
            pool = [s for s in candidates if not (s.author or "").strip()] \
                or candidates

        prog = (await db.execute(
            select(ReadProgress).where(
                ReadProgress.user_id == user_id,
                ReadProgress.book_url.in_([s.book_url for s in pool]),
            )
        )).scalars().all()
        by_url = {p.book_url: p for p in prog}

        changed = 0
        for s in pool:
            cur = by_url.get(s.book_url)
            incoming_newer = (
                cur is None
                or tms > _ms(cur.updated_at)
                or (tms == _ms(cur.updated_at)
                    and (idx, pos) > (cur.chapter_index, cur.offset))
            )
            if not incoming_newer:
                continue
            if cur is None:
                db.add(ReadProgress(
                    user_id=user_id, book_url=s.book_url,
                    chapter_index=idx, chapter_title=title, offset=pos,
                ))
            else:
                cur.chapter_index = idx
                cur.chapter_title = title
                cur.offset = pos
            changed += 1
        return f"ok:{changed}" if changed else "kept"

    async def _put_file(request: Request, ns: list[str], user_id: int,
                        cfg: WebDavConfig, db: AsyncSession) -> Response:
        if not _is_progress_file(ns):
            raise _err(403, "本服务端仅开放 bookProgress/ 下的阅读进度同步")
        ns_path = "/".join(ns)
        body = (await request.body())[:_MAX_BODY]
        text = body.decode("utf-8", "replace")

        now = datetime.now(timezone.utc)
        row = await _get_resource(db, user_id, ns_path)
        if row is None:
            row = DavResource(user_id=user_id, path=ns_path, content=text,
                              size=len(body), updated_at=now)
            db.add(row)
        else:
            row.content = text
            row.size = len(body)
            row.updated_at = now

        mirrored = await _mirror_to_progress(db, user_id, text)
        if mirrored == "no-book":
            # 双端同步：书架没有这本书 → 后台自动按书名搜索书源并入库
            d = _parse_payload(text)
            if d is not None and str(d.get("name") or "").strip():
                try:
                    from .sync_ingest import spawn_ingest

                    spawn_ingest(
                        user_id,
                        name=str(d.get("name") or "").strip(),
                        author=str(d.get("author") or "").strip(),
                        idx=int(d.get("durChapterIndex") or 0),
                        pos=int(d.get("durChapterPos") or 0),
                        tms=int(d.get("durChapterTime") or 0),
                        title=str(d.get("durChapterTitle") or ""),
                    )
                except (TypeError, ValueError):
                    pass
        # 节流更新最近同步时间（30 秒一次，避免高频小写入）
        last = cfg.last_sync_at
        last = last if last is None or last.tzinfo else last.astimezone()
        if last is None or (now - last).total_seconds() > 30:
            cfg.last_sync_at = now
        await db.commit()
        return Response(status_code=201, headers={
            "Content-Length": "0", "X-Antares-Mirror": mirrored})

    async def _delete_file(ns: list[str], user_id: int,
                           db: AsyncSession) -> Response:
        if not _is_progress_file(ns):
            raise _err(403, "本服务端仅开放 bookProgress/ 下的阅读进度同步")
        row = await _get_resource(db, user_id, "/".join(ns))
        if row is None:
            raise _err(404, "Not Found")
        await db.delete(row)
        await db.commit()
        return Response(status_code=204)

    # ------------------------------------------------------------- 路由注册
    @router.options("")
    @router.options("/{path:path}")
    async def dav_options() -> Response:
        return Response(status_code=200, headers={
            "DAV": "1",
            "Allow": "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, MKCOL",
            "MS-Author-Via": "DAV",
            "Content-Length": "0",
        })

    methods = ["PROPFIND", "MKCOL", "GET", "HEAD", "PUT", "DELETE"]

    @router.api_route("", methods=methods, include_in_schema=False,
                      response_model=None)
    @router.api_route("/{path:path}", methods=methods, include_in_schema=False,
                      response_model=None)
    async def dav_handler(
        request: Request,
        path: str = "",
        auth: tuple[int, WebDavConfig] = Depends(dav_auth),
        db: AsyncSession = Depends(get_db),
    ) -> Response:
        user_id, cfg = auth
        parts = _norm_parts(path)
        # 命名空间：以 bookProgress 段为界，兼容任意自定义子目录前缀
        prefix, ns = _rebase(parts)

        if request.method == "PROPFIND":
            return await _propfind(request, prefix, ns, user_id, db)
        if request.method == "MKCOL":
            if await _get_resource(db, user_id, "/".join(ns)) is not None:
                raise _err(405, "已存在同名资源")
            return Response(status_code=201, headers={"Content-Length": "0"})
        if request.method in ("GET", "HEAD"):
            return await _get_file(ns, user_id, db)
        if request.method == "PUT":
            return await _put_file(request, ns, user_id, cfg, db)
        if request.method == "DELETE":
            return await _delete_file(ns, user_id, db)
        raise _err(405, "不支持的方法")

    return router
