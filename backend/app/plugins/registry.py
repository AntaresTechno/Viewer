"""Plugin registry — discovers plugins under app/plugins/*.

Two plugin kinds are supported (a package may be both):

1. **API 插件** exposes ``meta`` + ``create_router(ctx)`` and gets mounted at
   ``/api/<mount>``.

2. **规则引擎插件** (source-engine plugin) exposes ``ENGINE`` metadata and
   ``create_engine(ctx)`` returning an object implementing the source parsing
   operations used by the books plugin::

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


def set_disabled_plugins(disabled: set[str]) -> None:
    """Live view of disabled plugins so engine lookups can respect them."""
    global _DISABLED_PLUGINS
    _DISABLED_PLUGINS = set(disabled)


def plugin_enabled(name: str) -> bool:
    """Whether an API plugin is currently enabled (live view, no DB hit).

    Plugins may call this to delegate behavior to each other without a hard
    dependency: when disabled the caller falls back to its own code path.
    """
    return name not in _DISABLED_PLUGINS


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
        meta = getattr(module, "meta", None)
        create_router = getattr(module, "create_router", None)
        create_root_router = getattr(module, "create_root_router", None)
        engine_meta = getattr(module, "ENGINE", None)
        create_engine = getattr(module, "create_engine", None)
        if not isinstance(meta, dict):
            continue
        if create_router is None and not (isinstance(engine_meta, dict) and create_engine):
            continue
        info = PluginInfo(
            name=meta["name"],
            mount=meta.get("mount") if create_router is not None else None,
            title=meta.get("title", meta["name"]),
            version=meta.get("version", "0.0.0"),
            description=meta.get("description", ""),
            order=int(meta.get("order", 100)),
            permissions=[tuple(p) for p in meta.get("permissions", [])],
            create_router=create_router,
            module_name=module.__name__,
            mount_root=meta.get("mount_root") if create_root_router else None,
            create_root_router=create_root_router if meta.get("mount_root") else None,
        )
        found[info.name] = info
        if isinstance(engine_meta, dict) and create_engine is not None:
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
            known = set(discover_plugins().keys())
            return (known - disabled) | (enabled_extra & known)

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

    factory = get_session_factory()
    async with factory() as session:
        row = await session.scalar(select(PluginState).where(PluginState.name == name))
        if row is None:
            row = PluginState(name=name, enabled=enabled)
            session.add(row)
        else:
            row.enabled = enabled
        await session.commit()
