"""legado 备份同步 — 复用本站已配置的外部 WebDAV 服务器，与阅读(legado)互同步阅读进度。

与内建的 ``/dav`` 服务端（面向 legado 主动连入本站）不同，这里连接的是
**外部的 WebDAV 服务器**（坚果云 / Alist / Nextcloud…）。只要 legado 与本站
都指向同一个服务器、同一个目录，就能通过 legado 的 WebDAV 布局双端互同步进度。

legado 的 WebDAV 布局（其 WebDavDir 之下）：

- ``bookProgress/{书名}_{作者}.json`` —— 每本书的阅读进度（双向同步本体）
- ``backup<日期>.zip`` / ``backup.zip``   —— 全量备份（内含 ``bookshelf.json`` 书架）

本模块提供：

- ``sync_progress`` —— 双向/拉取/推送进度（基本功能）；文件命名与正文格式与
  内建 ``/dav`` 服务端保持一致，同名书匹配、新者胜合并，legado 端可读回。
- ``import_shelf``  ——（可选）从最新 legado 全量备份读出书架并入库。

凭据复用 ``WebDavConfig`` 的 url/username/password，仅另存 ``legado_directory``。
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import select

if TYPE_CHECKING:
    from ...models import WebDavConfig

# 一次同步最多收发多少个进度文件，防止超大书架拖垮请求
_MAX_FILES = 600

_BOOKSHELF_ZIP_ENTRY = "bookshelf.json"


def _norm(s: str | None) -> str:
    return (s or "").strip().casefold()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _naive_ms(dt) -> int:
    """datetime → epoch 毫秒（SQLite 常返回 naive，按本地时区处理）。"""
    if dt is None:
        return 0
    dt = dt if dt.tzinfo else dt.astimezone()
    return int(dt.timestamp() * 1000)


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _mod_to_ms(s: str) -> int:
    """getlastmodified（RFC1123）→ epoch 毫秒；解析失败返回 0。"""
    try:
        return int(parsedate_to_datetime(s).timestamp() * 1000)
    except (TypeError, ValueError, OverflowError):
        return 0


# ------------------------------------------------------------------ DAV 封装
async def _dav(cfg, method: str, url: str, **kw):
    """带凭据的 WebDAV 请求（惰性 import，避免插件间循环依赖）。"""
    from .plugin import _dec_pwd, dav_request

    return await dav_request(
        method, url,
        username=cfg.username, password=_dec_pwd(cfg.password_enc), **kw,
    )


def _dir_part(cfg) -> str:
    return (cfg.legado_directory or "legado").strip().strip("/")


def _progress_url(cfg) -> str:
    from .plugin import _join_url

    return _join_url(cfg.url or "", f"{_dir_part(cfg)}/bookProgress")


def _base_url(cfg) -> str:
    from .plugin import _join_url

    return _join_url(cfg.url or "", _dir_part(cfg))


async def _ensure_dir(cfg, url: str) -> None:
    """目录不存在则 MKCOL（409/404 → 建；已存在 405 忽略）。"""
    status, body, _h = await _dav(cfg, "PROPFIND", url, headers={"Depth": "0"})
    if status in (200, 207):
        return
    mk, mbody, _h = await _dav(cfg, "MKCOL", url)
    if mk not in (200, 201, 405):
        from .plugin import _http_error

        raise _http_error(mk, mbody, "创建远程目录")


# --------------------------------------------------------------- 云端目录列表
async def list_progress(cfg) -> list[dict]:
    """列取远端 bookProgress/ 下的 .json 进度文件。

    返回 ``[{name, modified_ms, size}]``；目录不存在或为空返回空列表。
    """
    from .plugin import _parse_propfind

    url = _progress_url(cfg)
    status, body, _h = await _dav(cfg, "PROPFIND", url,
                                  headers={"Depth": "1"})
    if status in (404, 405):
        return []
    if status != 207:
        from .plugin import _http_error

        raise _http_error(status, body, "列取 legado 进度")
    return [
        {"name": it["name"], "modified_ms": _mod_to_ms(it["modified"]),
         "size": it["size"]}
        for it in _parse_propfind(body)
        if not it["isDir"] and it["name"].lower().endswith(".json")
    ][: _MAX_FILES]


async def _fetch_cloud(cfg) -> dict[str, dict]:
    """下载全部远端 bookProgress JSON → ``{文件名: 进度字段 dict}``。"""
    out: dict[str, dict] = {}
    for it in await list_progress(cfg):
        url = _progress_url(cfg) + "/" + it["name"]
        status, raw, _h = await _dav(cfg, "GET", url)
        if status != 200:
            continue
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and data.get("name"):
            out[it["name"]] = data
    return out


def _canonical(*, name: str, author: str) -> str:
    """进度文件名生成与内建 dav_server.progress_filename 保持一致。"""
    from .dav_server import progress_filename

    return progress_filename((name or "").strip(), (author or "").strip())


def _parse_cloud(data: dict) -> dict:
    try:
        idx = int(data.get("durChapterIndex") or 0)
        pos = int(data.get("durChapterPos") or 0)
        tms = int(data.get("durChapterTime") or 0)
    except (TypeError, ValueError):
        idx = pos = tms = 0
    return {
        "name": str(data.get("name") or ""),
        "author": str(data.get("author") or ""),
        "idx": idx, "pos": pos, "tms": tms,
        "title": str(data.get("durChapterTitle") or ""),
    }


def _build_payload(*, name: str, author: str, idx: int, pos: int,
                   tms: int, title: str) -> str:
    return json.dumps({
        "name": name or "", "author": author or "",
        "durChapterIndex": int(idx), "durChapterPos": int(pos),
        "durChapterTime": int(tms), "durChapterTitle": title or "",
    }, ensure_ascii=False)


# ------------------------------------------------------------- 本地数据装配
async def _load_shelf_with_progress(db, user_id: int):
    """书架 + 阅读进度 → (shelf, progress_by_url, by_name, by_url)。"""
    from ...models import ReadProgress, ShelfItem

    shelf = (await db.execute(
        select(ShelfItem).where(ShelfItem.user_id == user_id)
    )).scalars().all()
    prog = (await db.execute(
        select(ReadProgress).where(ReadProgress.user_id == user_id)
    )).scalars().all()
    by_url = {p.book_url: p for p in prog}
    by_name: dict[str, list] = {}
    shelf_by_url: dict[str, object] = {}
    for s in shelf:
        by_name.setdefault(_norm(s.name), []).append(s)
        shelf_by_url[s.book_url] = s
    return shelf, by_url, by_name, shelf_by_url


async def _select_progress(db, user_id: int, book_url: str):
    from ...models import ReadProgress

    return (await db.execute(
        select(ReadProgress).where(
            ReadProgress.user_id == user_id, ReadProgress.book_url == book_url)
    )).scalars().first()


async def _record_match(db, item, *, idx: int, pos: int, tms: int,
                        title: str, counts: dict) -> None:
    """把云端进度合并进某本书；仅当云端较新时写入。"""
    from ...models import ReadProgress

    cur = await _select_progress(db, item.user_id, item.book_url)
    if cur is None:
        db.add(ReadProgress(
            user_id=item.user_id, book_url=item.book_url,
            chapter_index=idx, chapter_title=title, offset=pos,
            updated_at=_ms_to_dt(tms or _now_ms()),
        ))
        counts["progressUpdated"] += 1
        return
    cur_ms = _naive_ms(cur.updated_at)
    if tms > cur_ms or (tms == cur_ms and (idx, pos) > (cur.chapter_index,
                                                        cur.offset)):
        cur.chapter_index = idx
        cur.chapter_title = title
        cur.offset = pos
        cur.updated_at = _ms_to_dt(max(tms, cur_ms))
        counts["progressUpdated"] += 1


async def _apply_cloud(db, user_id: int, by_name: dict, cloud: dict,
                       counts: dict) -> None:
    """按 书名+作者 把云端进度并入书架中匹配的书籍；无匹配则投递后台自动入库。"""
    name = cloud["name"]
    pool = by_name.get(_norm(name))
    if pool:
        if cloud["author"]:
            candidates = [s for s in pool
                          if _norm(s.author) == _norm(cloud["author"])] or pool
        else:
            candidates = pool
        for s in candidates:
            await _record_match(db, s, idx=cloud["idx"], pos=cloud["pos"],
                                tms=cloud["tms"], title=cloud["title"],
                                counts=counts)
        return
    # 书架没有这本书：后台按书名搜索书源入库（与内建 /dav 一致）
    if cloud["name"]:
        try:
            from .sync_ingest import spawn_ingest

            spawn_ingest(
                user_id, name=cloud["name"], author=cloud["author"],
                idx=cloud["idx"], pos=cloud["pos"], tms=cloud["tms"],
                title=cloud["title"],
            )
            counts["pendingMatch"] += 1
        except (TypeError, ValueError):  # pragma: no cover
            pass


# ------------------------------------------------------------- 公开入口
async def sync_progress(user_id: int, cfg, direction: str = "both") -> dict:
    """双向 / 拉取 / 推送 legado 阅读进度。direction ∈ {both, pull, push}。"""
    from ...core.db import get_session_factory

    if not (cfg and cfg.url):
        raise HTTPException(400, "尚未配置本插件的 WebDAV 服务器")
    if not (cfg.legado_directory or "").strip():
        raise HTTPException(400, "未填写 legado 同步目录")

    counts = {"pulled": 0, "pushed": 0, "progressUpdated": 0,
              "pendingMatch": 0}
    factory = get_session_factory()
    cloud = await _fetch_cloud(cfg)

    if direction in ("both", "pull"):
        async with factory() as db:
            _shelf, _by_url, by_name, _ = await _load_shelf_with_progress(
                db, user_id)
            for fname, data in cloud.items():
                c = _parse_cloud(data)
                if not c["name"]:
                    continue
                await _apply_cloud(db, user_id, by_name, c, counts)
                counts["pulled"] += 1
            cfg2 = await db.get(type(cfg), user_id)
            if cfg2 is not None:
                cfg2.legado_last_sync_at = datetime.now(timezone.utc)
            await db.commit()

    if direction in ("both", "push"):
        cloud_by_file = {
            _canonical(name=_parse_cloud(d)["name"],
                       author=_parse_cloud(d)["author"]): _parse_cloud(d)
            for d in cloud.values()
        }
        await _ensure_dir(cfg, _progress_url(cfg))
        async with factory() as db:
            _shelf, by_url, _bn, _ = await _load_shelf_with_progress(db, user_id)
            pushed = 0
            for s in _shelf:
                p = by_url.get(s.book_url)
                if p is None or not (s.name or "").strip():
                    continue
                fname = _canonical(name=s.name, author=s.author)
                local_ms = _naive_ms(p.updated_at) or _now_ms()
                cc = cloud_by_file.get(fname)
                if cc is not None and cc["tms"] >= local_ms:
                    continue  # 云端不更旧，避免覆盖更新的 legado 进度/乒乓
                payload = _build_payload(
                    name=s.name, author=s.author,
                    idx=int(p.chapter_index or 0), pos=int(p.offset or 0),
                    tms=local_ms, title=p.chapter_title or "")
                url = _progress_url(cfg) + "/" + fname
                status, body, _h = await _dav(
                    cfg, "PUT", url, data=payload.encode("utf-8"),
                    headers={"Content-Type": "application/json; charset=utf-8"})
                if status not in (200, 201, 204):
                    from .plugin import _http_error
                    raise _http_error(status, body, "上传进度")
                pushed += 1
            counts["pushed"] = pushed
            cfg2 = await db.get(type(cfg), user_id)
            if cfg2 is not None:
                cfg2.legado_last_sync_at = datetime.now(timezone.utc)
                await db.commit()

    return counts


async def import_shelf(user_id: int, cfg) -> dict:
    """（可选）从最新 legado 全量备份（backup*.zip）读出书架并入库。

    已存在的书（书名+作者相同）复用并把进度合入；没有的书按 legado 元数据
    建立。返回 {addedShelf, updatedShelf, progressUpdated, backup}。
    """
    from ...core.db import get_session_factory
    from ...models import ReadProgress, ShelfItem

    if not (cfg and cfg.url):
        raise HTTPException(400, "尚未配置本插件的 WebDAV 服务器")

    base = _base_url(cfg)
    status, body, _h = await _dav(cfg, "PROPFIND", base,
                                  headers={"Depth": "1"})
    if status != 207:
        from .plugin import _http_error

        raise _http_error(status, body, "列取 legado 备份")
    from .plugin import _parse_propfind

    zips = [
        it for it in _parse_propfind(body)
        if not it["isDir"] and it["name"].lower().startswith("backup")
        and it["name"].lower().endswith(".zip")
    ]
    if not zips:
        return {"addedShelf": 0, "updatedShelf": 0, "progressUpdated": 0,
                "backup": ""}
    zips.sort(key=lambda it: _mod_to_ms(it["modified"]), reverse=True)
    latest = zips[0]

    status, raw, _h = await _dav(cfg, "GET", base + "/" + latest["name"])
    if status != 200:
        from .plugin import _http_error

        raise _http_error(status, raw, "下载 legado 备份")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            text = zf.read(_BOOKSHELF_ZIP_ENTRY).decode("utf-8")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise HTTPException(400, f"备份内没有 {_BOOKSHELF_ZIP_ENTRY}") from exc
    try:
        books = json.loads(text)
    except ValueError as exc:
        raise HTTPException(400, "备份书架解析失败") from exc
    if not isinstance(books, list):
        books = []

    counts = {"addedShelf": 0, "updatedShelf": 0, "progressUpdated": 0,
              "backup": latest["name"]}
    factory = get_session_factory()
    async with factory() as db:
        _shelf, prog_by_url, by_name, _ = await _load_shelf_with_progress(
            db, user_id)
        for bk in books:
            if not isinstance(bk, dict):
                continue
            name = str(bk.get("name") or "").strip()
            if not name:
                continue
            author = str(bk.get("author") or "").strip()
            idx = int(bk.get("durChapterIndex") or 0)
            pos = int(bk.get("durChapterPos") or 0)
            tms = int(bk.get("durChapterTime") or _now_ms())
            title = str(bk.get("durChapterTitle") or "")

            pool = by_name.get(_norm(name)) or []
            if author:
                item = next((s for s in pool if _norm(s.author) == _norm(author)),
                            None) or (pool[0] if pool else None)
            else:
                item = pool[0] if pool else None

            if item is not None:
                counts["updatedShelf"] += 1
                cur = prog_by_url.get(item.book_url)
                if cur is None or _naive_ms(cur.updated_at) < tms:
                    await _record_match(db, item, idx=idx, pos=pos, tms=tms,
                                        title=title, counts=counts)
                continue

            # 新的书：直接用 legado 元数据建立
            book_url = str(bk.get("bookUrl") or "").strip()
            if not book_url:
                continue
            item = ShelfItem(
                user_id=user_id, book_url=book_url,
                toc_url=str(bk.get("tocUrl") or ""),
                name=name, author=author,
                cover_url=str(bk.get("coverUrl") or ""),
                intro=str(bk.get("intro") or ""),
                last_chapter=title,
                source_url=str(bk.get("origin") or ""),
            )
            db.add(item)
            await db.flush()
            by_name.setdefault(_norm(name), []).append(item)
            counts["addedShelf"] += 1
            db.add(ReadProgress(
                user_id=user_id, book_url=book_url,
                chapter_index=idx, chapter_title=title, offset=pos,
                updated_at=_ms_to_dt(tms),
            ))
            counts["progressUpdated"] += 1
        await db.commit()
        return counts