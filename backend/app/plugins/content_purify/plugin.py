"""正文净化插件 — 获取内容 → 净化 → 存入数据库缓存 → 调用。

三个规则来源（对应净化页三张卡）：

1. **内置净化 · MD3 版**：legado MD3 版引擎内置的那一套
   （HtmlFormatter.formatKeepImg），以代码实现为管线第一步，默认生效；
2. **乌云净化**：社区成包（3f067eb2.json 随插件分发），一键安装为本地
   规则包，安装后默认停用，由用户选择性开启；
3. **自定义规则**：上传 JSON 文件 / URL 拉取 / 粘贴导入。

阅读时由 books 插件在 ``/books/content`` 内委托本插件（启用即生效）；
本插件的 ``GET /content`` 也提供独立的等价入口。
"""

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

meta = {
    "name": "content_purify",
    "mount": "purify",
    "title": "正文净化",
    "version": "1.1.0",
    "description": "章节正文净化：MD3 内置净化层 + 规则包（乌云净化/自定义导入）+ 数据库缓存，获取内容后先净化再入库供阅读调用",
    "order": 36,
    "permissions": [
        ("purify.read", "查看净化规则包与缓存"),
        ("purify.manage", "导入/编辑/删除净化规则包"),
        ("purify.process", "使用正文净化阅读"),
        ("purify.cache.manage", "清理净化缓存"),
    ],
}


def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.deps import require_perm
    from ...core.db import get_db
    from ...legado_rule.exceptions import FetchError
    from ...models import (
        BookRef,
        BookSourceRow,
        PurifiedContent,
        PurifyPack,
        PurifyRule,
    )
    from ...plugins.registry import get_engine
    from ...services import content_purify as pipeline

    router = APIRouter(tags=["purify"])

    # ------------------------------------------------------------ catalog
    def _pack_summary(rows: list[PurifyRule]) -> dict:
        """乌云包的概要统计：分组分布与 JS 规则数量。"""
        groups: dict[str, int] = {}
        js_count = 0
        content_rules = 0
        for r in rows:
            g = str(r.get("group") or "") if isinstance(r, dict) else ""
            groups[g or "未分组"] = groups.get(g or "未分组", 0) + 1
            rep = str((r.get("replacement") or "")) if isinstance(r, dict) else ""
            if rep.strip().lower().startswith("@js:") or \
                    rep.strip().lower().startswith("<js>"):
                js_count += 1
            if isinstance(r, dict) and r.get("scopeContent", True):
                content_rules += 1
        return {"groups": groups, "jsRules": js_count, "contentRules": content_rules}

    @router.get("/catalog")
    async def get_catalog(
        current=Depends(require_perm("purify.read")),
        db: AsyncSession = Depends(get_db),
    ):
        """三个规则来源的状态：内置(MD3) / 乌云净化 / 自定义入口提示。"""
        from . import presets as preset_lib

        origins = set((await db.execute(
            select(PurifyPack.origin)
        )).scalars().all())

        wuyun_rows = preset_lib.load_wuyun_rules()
        items = []
        for src in preset_lib.BUILTIN_SOURCES:
            item = {
                **src,
                "installed": True,  # 内置层视为已就绪
                "jsEngine": pipeline.js_engine_available(),
            }
            if src["key"] == preset_lib.WUYUN_KEY:
                summary = _pack_summary(wuyun_rows)
                origin = f"preset:{preset_lib.WUYUN_KEY}"
                pack = await db.scalar(
                    select(PurifyPack).where(PurifyPack.origin == origin)
                )
                item.update({
                    "installed": pack is not None,
                    "packId": pack.id if pack else None,
                    "packEnabled": bool(pack.enabled) if pack else False,
                    "ruleCount": len(wuyun_rows),
                    **summary,
                })
            items.append(item)
        return {"items": items}

    class PresetInstallBody(BaseModel):
        key: str = Field(min_length=1)

    @router.post("/presets/install", status_code=201)
    async def install_preset(
        body: PresetInstallBody,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        """一键安装内置来源为本地规则包（目前即乌云净化；默认停用）。"""
        from . import presets as preset_lib

        preset = preset_lib.preset_by_key(body.key)
        if preset is None or not preset.get("installable"):
            raise HTTPException(404, f"该来源不可安装: {body.key}")
        origin = f"preset:{body.key}"
        existing = await db.scalar(
            select(PurifyPack).where(PurifyPack.origin == origin)
        )
        if existing is not None:
            return {"ok": True, "installed": False, "packId": existing.id}
        rows = preset_lib.load_wuyun_rules()
        norm = pipeline.parse_rule_payload(rows)
        if not norm:
            raise HTTPException(500, "打包的规则文件为空或格式不正确")
        pack = PurifyPack(
            name=preset["title"],
            description=f"{preset['description']}",
            origin=origin,
            enabled=False,  # 选择性开启：安装后由用户手动启用
            order=100,
        )
        db.add(pack)
        await db.flush()
        # 保持包内导出顺序，重排为 1..N
        norm.sort(key=lambda d: d.get("order") or 0)
        for i, it in enumerate(norm):
            it["order"] = i + 1
            db.add(PurifyRule(pack_id=pack.id, **it))
        await db.commit()
        return {
            "ok": True, "installed": True, "packId": pack.id,
            "rules": len(norm), "note": "已安装并停用，请开启后生效",
        }

    # --------------------------------------------------------------- packs
    @router.get("/packs")
    async def list_packs(
        current=Depends(require_perm("purify.read")),
        db: AsyncSession = Depends(get_db),
    ):
        counts = {
            pid: cnt for pid, cnt in (await db.execute(
                select(PurifyRule.pack_id, func.count(PurifyRule.id))
                .group_by(PurifyRule.pack_id)
            )).all()
        }
        rows = (await db.execute(
            select(PurifyPack).order_by(PurifyPack.order, PurifyPack.id)
        )).scalars().all()
        return {
            "items": [pipeline.pack_dict(p, counts.get(p.id, 0)) for p in rows]
        }

    class PackCreateBody(BaseModel):
        name: str = Field(min_length=1, max_length=128)
        description: str = ""
        order: int = 0

    @router.post("/packs/create", status_code=201)
    async def create_pack(
        body: PackCreateBody,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        pack = PurifyPack(
            name=body.name, description=body.description,
            origin="manual", enabled=True, order=body.order,
        )
        db.add(pack)
        await db.commit()
        await db.refresh(pack)
        return {"ok": True, "id": pack.id}

    async def _import_rows_into_pack(
        db: AsyncSession, obj: object, name: str, origin: str
    ) -> tuple[int, int]:
        """把规范化后的规则落成一个新包，返回 (pack_id, rule_count)。"""
        norm = pipeline.parse_rule_payload(obj)
        if not norm:
            raise HTTPException(400, "没有可导入的规则（缺少 pattern）")
        # 分组信息不落库（PurifyRule 无 group 列），取第一个分组名做包名兜底
        first_group = ""
        raw_items = obj if isinstance(obj, list) else [obj]
        for r in raw_items:
            if isinstance(r, dict) and r.get("group"):
                first_group = str(r["group"])
                break
        pack = PurifyPack(
            name=(name or first_group or "导入的净化规则")[:128],
            description=f"导入规则包，共 {len(norm)} 条",
            origin=origin, enabled=True,
            order=0,
        )
        db.add(pack)
        await db.flush()
        # 保持导出顺序，重排为 1..N（order 字段已在 parse 中规范化）
        norm.sort(key=lambda d: d.get("order") or 0)
        for i, it in enumerate(norm):
            it["order"] = i + 1
            db.add(PurifyRule(pack_id=pack.id, **it))
        await db.commit()
        return pack.id, len(norm)

    class PackImportBody(BaseModel):
        data: str = Field(description="替换规则 JSON：对象或数组（支持新旧两种导出格式）")
        name: str = ""

    @router.post("/packs/import", status_code=201)
    async def import_pack(
        body: PackImportBody,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        """粘贴 JSON 导入为一个净化规则包。"""
        import json as _json

        try:
            obj = _json.loads(body.data)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"JSON 解析失败: {exc}") from exc
        pack_id, count = await _import_rows_into_pack(db, obj, body.name, "import")
        return {"ok": True, "packId": pack_id, "imported": count}

    class PackImportUrlBody(BaseModel):
        url: str = Field(min_length=1)
        name: str = ""

    @router.post("/packs/import-url", status_code=201)
    async def import_pack_url(
        body: PackImportUrlBody,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        """从 URL 拉取规则 JSON 导入为一个净化规则包。"""
        import json as _json

        from ...legado_rule.net import fetch as net_fetch

        resp = await net_fetch(body.url)
        if resp.error:
            raise HTTPException(400, f"拉取失败: {resp.error}")
        try:
            obj = _json.loads(resp.body)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"返回内容不是合法 JSON: {exc}") from exc
        pack_id, count = await _import_rows_into_pack(
            db, obj, body.name, f"import:{body.url[:200]}"
        )
        return {"ok": True, "packId": pack_id, "imported": count}

    @router.post("/packs/import-file", status_code=201)
    async def import_pack_file(
        file: UploadFile = File(...),
        name: str = "",
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        """上传规则 JSON 文件导入为一个净化规则包。"""
        import json as _json

        raw = await file.read()
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(400, "文件过大（>2MB）")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(400, "文件需为 UTF-8 编码的 JSON") from exc
        try:
            obj = _json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"JSON 解析失败: {exc}") from exc
        stem = (file.filename or "").rsplit(".", 1)[0]
        pack_id, count = await _import_rows_into_pack(
            db, obj, name or stem, "import"
        )
        return {"ok": True, "packId": pack_id, "imported": count}

    class PackUpdate(BaseModel):
        name: str | None = None
        description: str | None = None
        enabled: bool | None = None
        order: int | None = None

    async def _load_pack(db: AsyncSession, pack_id: int) -> PurifyPack:
        pack = await db.get(PurifyPack, pack_id)
        if pack is None:
            raise HTTPException(404, "规则包不存在")
        return pack

    @router.put("/packs/{pack_id}")
    async def update_pack(
        pack_id: int,
        body: PackUpdate,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        pack = await _load_pack(db, pack_id)
        for k in ("name", "description", "enabled", "order"):
            v = getattr(body, k)
            if v is not None:
                setattr(pack, k, v)
        await db.commit()
        return {"ok": True}

    @router.post("/packs/{pack_id}/toggle")
    async def toggle_pack(
        pack_id: int,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        pack = await _load_pack(db, pack_id)
        pack.enabled = not pack.enabled
        await db.commit()
        return {"ok": True, "enabled": pack.enabled}

    @router.delete("/packs/{pack_id}")
    async def delete_pack(
        pack_id: int,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        pack = await _load_pack(db, pack_id)
        await db.execute(
            delete(PurifyRule).where(PurifyRule.pack_id == pack_id)
        )
        await db.delete(pack)
        await db.commit()
        return {"ok": True}

    # --------------------------------------------------------------- rules
    @router.get("/packs/{pack_id}/rules")
    async def list_rules(
        pack_id: int,
        current=Depends(require_perm("purify.read")),
        db: AsyncSession = Depends(get_db),
    ):
        await _load_pack(db, pack_id)
        rows = (await db.execute(
            select(PurifyRule).where(PurifyRule.pack_id == pack_id)
            .order_by(PurifyRule.order, PurifyRule.id)
        )).scalars().all()
        return {"items": [pipeline.rule_dict(r) for r in rows]}

    class RuleBody(BaseModel):
        name: str = ""
        pattern: str = Field(min_length=1)
        replacement: str = ""
        scope: str = ""
        regex: bool = True
        caseSensitive: bool = True
        scopeContent: bool = True
        scopeTitle: bool = False
        order: int = 0

    @router.post("/packs/{pack_id}/rules", status_code=201)
    async def add_rule(
        pack_id: int,
        body: RuleBody,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        await _load_pack(db, pack_id)
        rule = PurifyRule(
            pack_id=pack_id, name=body.name[:128], order=body.order,
            pattern=body.pattern, replacement=body.replacement,
            scope=body.scope[:512], regex=body.regex,
            case_sensitive=body.caseSensitive,
            scope_content=body.scopeContent, scope_title=body.scopeTitle,
        )
        db.add(rule)
        await db.commit()
        return {"ok": True, "id": rule.id}

    class RuleUpdate(BaseModel):
        name: str | None = None
        pattern: str | None = None
        replacement: str | None = None
        scope: str | None = None
        regex: bool | None = None
        caseSensitive: bool | None = None
        scopeContent: bool | None = None
        scopeTitle: bool | None = None
        order: int | None = None
        isActive: bool | None = None

    @router.put("/rules/{rule_id}")
    async def update_rule(
        rule_id: int,
        body: RuleUpdate,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        rule = await db.get(PurifyRule, rule_id)
        if rule is None:
            raise HTTPException(404, "规则不存在")
        mapping = {
            "name": ("name", lambda v: str(v)[:128]),
            "pattern": ("pattern", str),
            "replacement": ("replacement", str),
            "scope": ("scope", lambda v: str(v)[:512]),
            "regex": ("regex", bool),
            "caseSensitive": ("case_sensitive", bool),
            "scopeContent": ("scope_content", bool),
            "scopeTitle": ("scope_title", bool),
            "order": ("order", int),
            "isActive": ("is_active", bool),
        }
        for k, (col, cast) in mapping.items():
            v = getattr(body, k)
            if v is not None:
                setattr(rule, col, cast(v))
        await db.commit()
        return {"ok": True}

    @router.post("/rules/{rule_id}/toggle")
    async def toggle_rule(
        rule_id: int,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        rule = await db.get(PurifyRule, rule_id)
        if rule is None:
            raise HTTPException(404, "规则不存在")
        rule.is_active = not rule.is_active
        await db.commit()
        return {"ok": True, "isActive": rule.is_active}

    class IdsBody(BaseModel):
        ids: list[int]

    @router.post("/rules/delete")
    async def delete_rules(
        body: IdsBody,
        current=Depends(require_perm("purify.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        await db.execute(delete(PurifyRule).where(PurifyRule.id.in_(body.ids)))
        await db.commit()
        return {"ok": True}

    # ---------------------------------------------------------------- test
    class TestBody(BaseModel):
        text: str
        bookName: str = ""
        sourceUrl: str = ""
        sourceName: str = ""

    @router.post("/test")
    async def test_purify(
        body: TestBody,
        current=Depends(require_perm("purify.read")),
        db: AsyncSession = Depends(get_db),
    ):
        rules = await pipeline.active_rules(db)
        out, applied = await pipeline.purify_text(
            body.text, rules,
            book_name=body.bookName, source_url=body.sourceUrl,
            source_name=body.sourceName,
        )
        return {"content": out, "applied": applied,
                "fingerprint": pipeline.rules_fingerprint(rules)}

    # ------------------------------------------------------------- content
    @router.get("/content")
    async def purified_content(
        source_url: str,
        url: str,
        book_url: str = "",
        title: str = "",
        base: str = "",
        name: str = "",
        current=Depends(require_perm("purify.process")),
        db: AsyncSession = Depends(get_db),
    ):
        """独立调用入口：缓存优先 → 回源抓取 → 净化 → 入库 → 返回。"""
        row = await db.scalar(
            select(BookSourceRow).where(BookSourceRow.source_url == source_url)
        )
        if row is None:
            raise HTTPException(404, f"书源不存在: {source_url}")
        if not row.enabled:
            raise HTTPException(400, "该书源已停用")
        import json as _json

        src = _json.loads(row.raw_json)
        try:
            eng = get_engine(getattr(row, "engine", None), ctx)
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        chapter = {"url": url, "title": title, "isVolume": False}

        async def _fetch_raw() -> str:
            return await eng.get_content(
                src, {}, chapter, None, base_url=base or None
            )

        try:
            text, cached = await pipeline.process_chapter(
                db,
                source_url=source_url, url=url, book_url=book_url,
                title=title, book_name=name, source_name=row.source_name,
                fetch_raw=_fetch_raw,
            )
        except FetchError as exc:
            raise HTTPException(502, f"书源连接失败：{exc}") from exc
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                502, f"书源解析失败：{type(exc).__name__}: {exc}"
            ) from exc
        return {"content": text, "cached": cached, "purified": True}

    # ---------------------------------------------------------------- cache
    @router.get("/cache/stats")
    async def cache_stats(
        current=Depends(require_perm("purify.read")),
        db: AsyncSession = Depends(get_db),
    ):
        total = await db.scalar(select(func.count(PurifiedContent.id))) or 0
        sizes = (await db.execute(
            select(func.sum(func.length(PurifiedContent.raw)),
                   func.sum(func.length(PurifiedContent.content)))
        )).first()
        raw_bytes, content_bytes = (sizes[0] or 0, sizes[1] or 0)

        per_book = {
            (s, b): c for s, b, c in (await db.execute(
                select(PurifiedContent.source_url, PurifiedContent.book_url,
                       func.count(PurifiedContent.id))
                .where(PurifiedContent.book_url != "")
                .group_by(PurifiedContent.source_url, PurifiedContent.book_url)
            )).all()
        }
        refs = (await db.execute(select(BookRef))).scalars().all()
        ref_by = {(r.source_url, r.book_url): r for r in refs}
        books = []
        for (s, b), cnt in sorted(per_book.items(), key=lambda kv: -kv[1]):
            ref = ref_by.get((s, b))
            books.append({
                "sourceUrl": s,
                "bookUrl": b,
                "name": (ref.name if ref else "") or b,
                "chapters": cnt,
            })
        return {
            "chapters": int(total),
            "rawBytes": int(raw_bytes),
            "contentBytes": int(content_bytes),
            "booksTotal": len(books),
            "books": books,
        }

    @router.delete("/cache")
    async def clear_cache(
        source_url: str = "",
        book_url: str = "",
        current=Depends(require_perm("purify.cache.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        stmt = delete(PurifiedContent)
        if source_url:
            stmt = stmt.where(PurifiedContent.source_url == source_url)
        if book_url:
            stmt = stmt.where(PurifiedContent.book_url == book_url)
        res = await db.execute(stmt)
        await db.commit()
        return {"deleted": res.rowcount}

    @router.post("/cache/invalidate")
    async def invalidate_cache(
        source_url: str = "",
        book_url: str = "",
        current=Depends(require_perm("purify.cache.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        """把缓存标记为过期（清空指纹）：下次读取时用原文重新净化。"""
        stmt = select(PurifiedContent)
        if source_url:
            stmt = stmt.where(PurifiedContent.source_url == source_url)
        if book_url:
            stmt = stmt.where(PurifiedContent.book_url == book_url)
        rows = (await db.execute(stmt)).scalars().all()
        n = 0
        changed = False
        for r in rows:
            if r.fingerprint:
                r.fingerprint = ""
                changed = True
                n += 1
        if changed:
            await db.commit()
        return {"invalidated": n}

    # ------------------------------------------------- local library bridge
    @router.get("/local-content")
    async def local_content(
        source_url: str,
        url: str,
        current=Depends(require_perm("purify.read")),
        db: AsyncSession = Depends(get_db),
    ):
        """查看某章的缓存明细（原文长度/净化文长度/命中规则）。"""
        row = await db.scalar(
            select(PurifiedContent).where(
                PurifiedContent.source_url == source_url,
                PurifiedContent.url == url,
            )
        )
        if row is None:
            raise HTTPException(404, "该章节尚未进入净化缓存")
        return {
            "url": row.url,
            "title": row.title,
            "rawChars": len(row.raw or ""),
            "contentChars": len(row.content or ""),
            "applied": row.applied or [],
            "fingerprint": row.fingerprint,
            "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        }

    return router
