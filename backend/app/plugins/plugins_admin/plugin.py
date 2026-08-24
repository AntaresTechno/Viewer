"""plugins 插件 — 插件开关管理 + ZIP 安装（仅超级管理员）。"""

import io
import re
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

meta = {
    "name": "plugins",
    "mount": "plugins",
    "title": "插件管理",
    "version": "1.1.0",
    "description": "查看已加载的 API 插件并启用/停用（重启生效）；支持上传 ZIP 安装新插件",
    "order": 22,
    "permissions": [("plugins.manage", "查看与启停插件")],
}

# ZIP 安装的安全上限
_MAX_ZIP_BYTES = 50 * 1024 * 1024          # 压缩包体积
_MAX_TOTAL_UNCOMPRESSED = 128 * 1024 * 1024  # 解压后总体积
_MAX_MEMBERS = 2000                        # 条目数量


def _sanitize_dirname(name: str) -> str:
    """把任意字符串收敛成合法的插件目录名。

    registry 会跳过以 '_' 开头的包，这里同样剥掉前导下划线。
    """
    cleaned = re.sub(r"[^A-Za-z0-9\-]", "_", name).lstrip("_-")
    return cleaned or "uploaded_plugin"


def _plan_install(
    zf: zipfile.ZipFile, upload_name: str
) -> tuple[list[zipfile.ZipInfo], str, int]:
    """校验 ZIP 内容并推断安装布局 → (待解压成员, 目录名, 剥离的前置段数)。

    - ``plugin.py`` 在压缩包根目录：原样解压（strip=0），目录名取上传文件名；
    - ``plugin.py`` 在唯一顶层目录内：剥掉该目录层解压（strip=1）。
    """
    members = [
        i for i in zf.infolist()
        if not i.is_dir()
        and not i.filename.startswith("__MACOSX/")
        and PurePosixPath(i.filename).name != ".DS_Store"
    ]
    if not members:
        raise HTTPException(400, "ZIP 内没有文件")
    if len(members) > _MAX_MEMBERS:
        raise HTTPException(400, f"ZIP 内文件过多（>{_MAX_MEMBERS}）")
    if sum(i.file_size for i in members) > _MAX_TOTAL_UNCOMPRESSED:
        raise HTTPException(400, "解压后的总体积超过限制")

    # zip-slip 防护：拒绝绝对路径与 ..
    for m in members:
        p = PurePosixPath(m.filename)
        if p.is_absolute() or ".." in p.parts:
            raise HTTPException(400, f"ZIP 内包含非法路径: {m.filename}")

    def is_junk(parts: tuple[str, ...]) -> bool:
        return bool(parts) and (
            parts[0] == "__MACOSX" or ".DS_Store" in parts
        )

    candidates = []
    for m in members:
        parts = PurePosixPath(m.filename).parts
        if not parts or is_junk(parts):
            continue
        if parts[-1] == "plugin.py":
            candidates.append(parts)

    if not candidates:
        raise HTTPException(400, "ZIP 中找不到 plugin.py：包根目录或唯一子目录下需有 plugin.py")

    top = min(candidates, key=len)  # 取最浅的 plugin.py
    if len(top) == 1:
        # plugin.py 在压缩包根目录 → 用上传文件名做插件目录名
        stem = Path(upload_name or "plugin.zip").stem
        dirname = _sanitize_dirname(stem)
        return members, dirname, 0

    root = top[0]
    mixed = {
        PurePosixPath(m.filename).parts[0]
        for m in members
        if PurePosixPath(m.filename).parts
        and not is_junk(PurePosixPath(m.filename).parts)
    } - {root}
    if mixed:
        raise HTTPException(
            400,
            f"ZIP 结构不支持：除 {root}/ 外还有顶层条目 {sorted(mixed)[:3]}",
        )
    dirname = _sanitize_dirname(root)
    # 剥掉顶层目录层，把该目录的“内容”作为插件包体
    stripped = [
        i for i in members
        if len(PurePosixPath(i.filename).parts) > 1
    ]
    return stripped, dirname, 1


def create_router(ctx) -> APIRouter:
    from sqlalchemy import select

    from ...core.deps import require_superuser
    from ...core.db import get_db
    from ...models import PluginState
    from ...plugins import registry as registry_mod
    from ...plugins.registry import all_plugins, toggle_plugin

    router = APIRouter(tags=["plugins"])

    class ToggleBody(BaseModel):
        enabled: bool

    @router.get("")
    async def list_plugins(
        current=Depends(require_superuser), db=Depends(get_db)
    ):
        rows = (await db.execute(select(PluginState))).scalars().all()
        states = {r.name: r.enabled for r in rows}
        return {
            "items": [
                {
                    "name": p.name,
                    "title": p.title,
                    "version": p.version,
                    "description": p.description,
                    "mount": p.mount,
                    "enabled": states.get(p.name, True),
                }
                for p in all_plugins()
            ]
        }

    @router.post("/{name}/toggle")
    async def toggle(name: str, body: ToggleBody,
                     current=Depends(require_superuser)):
        known = {p.name for p in all_plugins()}
        if name not in known:
            raise HTTPException(404, "插件不存在")
        await toggle_plugin(name, body.enabled)
        # keep the live disabled-set in sync so engine lookups respect it
        from ...plugins.registry import _DISABLED_PLUGINS, set_disabled_plugins

        disabled = set(_DISABLED_PLUGINS)
        if body.enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        set_disabled_plugins(disabled)
        note = None if body.enabled else "已停用，重启后端后生效"
        return {"ok": True, "note": note}

    @router.post("/install", status_code=201)
    async def install_plugin(
        file: UploadFile = File(...),
        current=Depends(require_superuser),
    ):
        """上传 ZIP 安装插件。

        - 包内根目录（或唯一子目录）需含 ``plugin.py``；
        - 安装即校验导入，失败自动回滚并返回原因；
        - 规则引擎类插件即时生效；API 路由类插件需重启后端挂载。
        """
        import importlib
        import shutil
        import sys

        raw = await file.read()
        if len(raw) > _MAX_ZIP_BYTES:
            raise HTTPException(400, "ZIP 文件过大（>50MB）")
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise HTTPException(400, "不是有效的 ZIP 文件") from exc

        base_pkg = registry_mod.__package__           # e.g. "app.plugins"
        pkg_root = Path(registry_mod.__file__).parent  # .../app/plugins

        def purge_modules(dirname: str) -> None:
            prefix = f"{base_pkg}.{dirname}"
            for mod_name in [
                k for k in list(sys.modules)
                if k == prefix or k.startswith(prefix + ".")
            ]:
                del sys.modules[mod_name]

        def refresh_import_caches() -> None:
            """让导入系统看到刚落盘的新包目录。

            仅 invalidate_caches() 在部分 Python 版本上不足以刷新
            sys.path_importer_cache 里缓存的 FileFinder（目录列表按 mtime
            粒度失效），同一秒内新建的插件目录会被当作不存在；
            这里把整个 path-importer 缓存清掉，强制重新扫描。
            """
            importlib.invalidate_caches()
            sys.path_importer_cache.clear()

        # 单个 with 块内完成「规划 + 解压」，避免 ZipFile 提前 close。
        with zf:
            members, dirname, strip = _plan_install(zf, file.filename or "")

            dest = pkg_root / dirname
            backup = pkg_root / f"_backup_{dirname}_{id(file)}"
            staging = pkg_root / f"_upload_{dirname}_{id(file)}"
            had_old = dest.exists()

            try:
                shutil.rmtree(staging, ignore_errors=True)
                staging.mkdir(parents=True)
                for m in members:
                    parts = PurePosixPath(m.filename).parts[strip:]
                    if not parts:
                        continue
                    target = staging.joinpath(*parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(m))

                # Python 3.14 起 iter_modules 不再把缺 __init__.py 的目录
                # 当作包，registry 会发现不了插件 —— 缺就补一个空的。
                init_file = staging / "__init__.py"
                if not init_file.exists():
                    init_file.write_text("", encoding="utf-8")

                if had_old:
                    shutil.move(str(dest), str(backup))
                shutil.move(str(staging), str(dest))

                # 新建的包目录可能落在 FileFinder 的目录缓存（按 mtime 粒度）
                # 之内，必须先失效导入缓存，否则 import/发现都看不到新目录。
                refresh_import_caches()

                # 校验：真实走一遍 import，meta 不合规视为坏包并回滚
                purge_modules(dirname)
                module = importlib.import_module(f"{base_pkg}.{dirname}.plugin")
                meta_obj = getattr(module, "meta", None)
                if not isinstance(meta_obj, dict) or not meta_obj.get("name"):
                    raise ValueError(
                        "plugin.py 缺少有效的 meta 字典（需至少包含 name）"
                    )
            except HTTPException:
                purge_modules(dirname)
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                if had_old and backup.exists():
                    shutil.move(str(backup), str(dest))
                raise
            except Exception as exc:  # noqa: BLE001 — 任何加载失败都回滚
                purge_modules(dirname)
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                if had_old and backup.exists():
                    shutil.move(str(backup), str(dest))
                msg = repr(exc) if len(repr(exc)) < 300 else type(exc).__name__
                raise HTTPException(400, f"插件加载失败，已回滚：{msg}") from exc
            finally:
                shutil.rmtree(staging, ignore_errors=True)
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)

        # 让新插件进入注册表；引擎缓存清空以便覆盖安装后重建实例
        refresh_import_caches()
        registry_mod.discover_plugins(force=True)
        registry_mod._INSTANCE_CACHE.clear()

        info = next((p for p in all_plugins() if p.name == meta_obj["name"]), None)
        has_router = bool(info and info.create_router)
        note = (
            "安装成功。API 路由将在重启后端后挂载生效。"
            if has_router
            else "规则引擎/纯声明插件已即时生效。"
        )
        return {
            "ok": True,
            "name": meta_obj["name"],
            "title": meta_obj.get("title", meta_obj["name"]),
            "version": meta_obj.get("version", "?"),
            "note": note,
        }

    return router
