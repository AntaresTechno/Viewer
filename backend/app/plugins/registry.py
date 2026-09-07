"""Component registry — discovers declarations under app/plugins/*.

Every package declares exactly one product-facing kind through ``PLUGIN``:

1. ``core``: bundled core module; always enabled and cannot be toggled.
2. ``plugin``: optional feature plugin; may expose API routes.
3. ``engine``: source-rule engine; exposes ``ENGINE`` + ``create_engine`` and
   may additionally expose management API routes.

The canonical declaration is ``PLUGIN = {"kind": ..., ...}``. Legacy ``meta``
declarations remain readable and are inferred as ``engine`` when an ENGINE
factory exists, otherwise ``plugin``.

An engine package returns an object implementing the source parsing operations
used by the books module::

       ENGINE = {"key": "legado", "title": "Legado 书源", "version": "1.0.0",
                 "description": "阅读(legado)书源规则引擎"}
       def create_engine(ctx) -> LegadoEngine: ...

The engine object must provide async methods ``search_book``, ``book_info``,
``get_toc`` and ``get_content``.
"""
from __future__ import annotations

import dataclasses
import importlib
import pkgutil


@dataclasses.dataclass
class PluginContext:
    """Shared services handed to every plugin."""

    settings: Any

    @property
    def engine(self):
        from ..core.db import get_engine

        return get_engine()

    def session_factory(self):
        from ..core.db import get_session_factory

        return get_session_factory()


@dataclasses.dataclass
class PluginInfo:
    name: str
    mount: str | None
    title: str
    version: str
    description: str
    order: int
    permissions: list[tuple[str, str]]
    create_router: Any | None
    module_name: str
    kind: str
    legacy_manifest: bool = False
    # 可选：挂在站点根路径（/api 之外）的路由工厂，如 WebDAV 服务端 /dav
    mount_root: str | None = None
    create_root_router: Any | None = None


@dataclasses.dataclass
class EngineInfo:
    key: str
    title: str
    version: str
    description: str
    plugin_name: str
    factory: Any  # create_engine(ctx) -> engine instance


_ENGINE_CACHE: dict[str, EngineInfo] | None = None
_INSTANCE_CACHE: dict[str, Any] = {}
_CACHE: dict[str, PluginInfo] | None = None
_DISABLED_PLUGINS: set[str] = set()

PLUGIN_KINDS = ("engine", "plugin", "core")
PLUGIN_KIND_LABELS = {
    "engine": "规则引擎",
    "plugin": "插件",
    "core": "核心模块",
}


def set_disabled_plugins(disabled: set[str]) -> None:
    """Live view of disabled plugins so engine lookups can respect them."""
    global _DISABLED_PLUGINS
    # Core modules are part of the application contract and cannot be disabled,
    # including by a stale plugin_states row from an older release.
    core_names = {
        p.name for p in all_plugins() if p.kind == "core"
    }
    _DISABLED_PLUGINS = set(disabled) - core_names


def plugin_enabled(name: str) -> bool:
    """Whether a discovered component is currently enabled (live view, no DB hit).

    Plugins may call this to delegate behavior to each other without a hard
    dependency: when disabled the caller falls back to its own code path.
    """
    info = discover_plugins().get(name)
    return bool(info and (info.kind == "core" or name not in _DISABLED_PLUGINS))


def discover_plugins(force: bool = False) -> dict[str, PluginInfo]:
    global _CACHE, _ENGINE_CACHE
    if _CACHE is not None and not force:
        return _CACHE
    import sys

    plugins_pkg = sys.modules[__package__]

    found: dict[str, PluginInfo] = {}
    engines: dict[str, EngineInfo] = {}
    for mod_info in pkgutil.iter_modules(plugins_pkg.__path__):
        if mod_info.name.startswith("_") or not mod_info.ispkg:
            continue
        try:
            module = importlib.import_module(f"{plugins_pkg.__name__}.{mod_info.name}.plugin")
        except Exception as exc:  # noqa: BLE001 - report broken plugin, keep others alive
            try:
                print(f"[plugins] failed to load '{mod_info.name}': {exc!r}")
            except UnicodeEncodeError:
                print(f"[plugins] failed to load '{mod_info.name}': {type(exc).__name__}")
            continue
        create_router = getattr(module, "create_router", None)
        create_root_router = getattr(module, "create_root_router", None)
        engine_meta = getattr(module, "ENGINE", None)
        create_engine = getattr(module, "create_engine", None)
        manifest = getattr(module, "PLUGIN", None)
        legacy_manifest = False
        canonical_manifest = isinstance(manifest, dict)
        if not canonical_manifest:
            manifest = getattr(module, "meta", None)
            legacy_manifest = isinstance(manifest, dict)
        if not isinstance(manifest, dict):
            continue
        if create_router is None and not (isinstance(engine_meta, dict) and create_engine):
            continue
        inferred_kind = "engine" if isinstance(engine_meta, dict) and create_engine else "plugin"
        if canonical_manifest and not str(manifest.get("kind") or "").strip():
            print(f"[plugins] skipped '{mod_info.name}': PLUGIN.kind is required")
            continue
        kind = str(manifest.get("kind") or inferred_kind).strip().lower()
        if kind not in PLUGIN_KINDS:
            print(f"[plugins] skipped '{mod_info.name}': invalid PLUGIN.kind={kind!r}")
            continue
        if kind == "engine" and not (isinstance(engine_meta, dict) and create_engine):
            print(f"[plugins] skipped '{mod_info.name}': engine kind requires ENGINE + create_engine")
            continue
        info = PluginInfo(
            name=manifest["name"],
            mount=manifest.get("mount") if create_router is not None else None,
            title=manifest.get("title", manifest["name"]),
            version=manifest.get("version", "0.0.0"),
            description=manifest.get("description", ""),
            order=int(manifest.get("order", 100)),
            permissions=[tuple(p) for p in manifest.get("permissions", [])],
            create_router=create_router,
            module_name=module.__name__,
            kind=kind,
            legacy_manifest=legacy_manifest,
            mount_root=manifest.get("mount_root") if create_root_router else None,
            create_root_router=create_root_router if manifest.get("mount_root") else None,
        )
        found[info.name] = info
        if kind == "engine" and isinstance(engine_meta, dict) and create_engine is not None:
            ekey = str(engine_meta.get("key") or info.name)
            engines[ekey] = EngineInfo(
                key=ekey,
                title=str(engine_meta.get("title", ekey)),
                version=str(engine_meta.get("version", info.version)),
                description=str(engine_meta.get("description", "")),
                plugin_name=info.name,
                factory=create_engine,
            )
    _CACHE = dict(sorted(found.items(), key=lambda kv: (kv[1].order, kv[1].name)))
    _ENGINE_CACHE = engines
    return _CACHE


def all_plugins() -> list[PluginInfo]:
    return list(discover_plugins().values())


def all_engines() -> list[EngineInfo]:
    discover_plugins()
    assert _ENGINE_CACHE is not None
    return list(_ENGINE_CACHE.values())


def get_engine(key: str | None, ctx: PluginContext | None = None):
    """Return an engine instance by key (default/fallback: 'legado')."""
    discover_plugins()
    assert _ENGINE_CACHE is not None
    ekey = key or "legado"
    info = _ENGINE_CACHE.get(ekey)
    if info is None:
        info = _ENGINE_CACHE.get("legado")
        if info is None:
            raise KeyError(f"no source engine registered for '{ekey}'")
    if info.plugin_name in _DISABLED_PLUGINS:
        raise KeyError(f"engine '{info.key}' is disabled (plugin '{info.plugin_name}')")
    if ctx is None:
        ctx = PluginContext(settings=None)
    inst = _INSTANCE_CACHE.get(info.key)
    if inst is None:
        inst = info.factory(ctx)
        _INSTANCE_CACHE[info.key] = inst
    return inst


def engine_keys() -> list[str]:
    discover_plugins()
    assert _ENGINE_CACHE is not None
    return list(_ENGINE_CACHE.keys())


def all_permission_keys() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for p in all_plugins():
        for key, title in p.permissions:
            if key not in seen:
                seen.add(key)
                out.append((key, title))
    return out


def enabled_plugin_names() -> set[str]:
    """Enabled set = discovered minus DB-disabled. Safe at startup."""
    import asyncio
    import concurrent.futures

    from sqlalchemy import select

    from ..core.db import get_engine, get_session_factory
    from ..models import Base, PluginState

    async def _load() -> set[str]:
        # ensure tables exist even before the lifespan seeder runs
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(select(PluginState))).scalars().all()
            disabled = {r.name for r in rows if not r.enabled}
            enabled_extra = {r.name for r in rows if r.enabled}
            discovered = discover_plugins()
            known = set(discovered)
            core = {name for name, info in discovered.items() if info.kind == "core"}
            return ((known - disabled) | (enabled_extra & known)) | core

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_load())
    else:
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            return pool.submit(asyncio.run, _load()).result()


async def toggle_plugin(name: str, enabled: bool) -> None:
    from sqlalchemy import select

    from ..core.db import get_session_factory
    from ..models import PluginState

    info = discover_plugins().get(name)
    if info is None:
        raise KeyError(f"unknown plugin: {name}")
    if info.kind == "core":
        raise ValueError(f"core module '{name}' cannot be disabled")

    factory = get_session_factory()
    async with factory() as session:
        row = await session.scalar(select(PluginState).where(PluginState.name == name))
        if row is None:
            row = PluginState(name=name, enabled=enabled)
            session.add(row)
        else:
            row.enabled = enabled
        await session.commit()
