"""Component registry — discovers declarations under app/plugins/*.

Every package declares exactly one product-facing kind through ``PLUGIN``:

1. ``core``: bundled core module; always enabled and cannot be toggled.
2. ``plugin``: optional feature plugin; may expose API routes and a bundled UI.
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
from pathlib import Path, PurePosixPath
from typing import Any


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
class PluginUiInfo:
    """A plugin-owned, self-contained HTML management interface."""

    entry: str
    title: str


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
    requires: tuple[str, ...] = ()
    ui: PluginUiInfo | None = None
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

_MAX_PLUGIN_UI_BYTES = 2 * 1024 * 1024


def parse_plugin_ui(manifest: dict, module: Any) -> PluginUiInfo | None:
    """Validate the optional UI declaration without executing frontend code."""
    raw = manifest.get("ui")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("PLUGIN.ui must be an object")
    entry = str(raw.get("entry") or "").strip()
    title = str(raw.get("title") or manifest.get("title") or manifest["name"]).strip()
    rel = PurePosixPath(entry)
    if (
        not entry
        or "\\" in entry
        or rel.is_absolute()
        or ".." in rel.parts
        or rel.suffix.lower() != ".html"
    ):
        raise ValueError("PLUGIN.ui.entry must be a relative .html path")
    module_file = getattr(module, "__file__", None)
    if not module_file:
        raise ValueError("plugin module has no filesystem location")
    root = Path(module_file).resolve().parent
    target = root.joinpath(*rel.parts).resolve()
    if root != target and root not in target.parents:
        raise ValueError("PLUGIN.ui.entry escapes the plugin directory")
    if not target.is_file():
        raise ValueError(f"PLUGIN.ui.entry does not exist: {entry}")
    if target.stat().st_size > _MAX_PLUGIN_UI_BYTES:
        raise ValueError("PLUGIN.ui.entry exceeds 2 MiB")
    return PluginUiInfo(entry=entry, title=title or manifest["name"])


def load_plugin_ui(info: PluginInfo) -> str:
    """Read a validated plugin UI entry, re-checking containment and size."""
    if info.ui is None:
        raise ValueError(f"plugin '{info.name}' has no UI")
    module = importlib.import_module(info.module_name)
    module_file = getattr(module, "__file__", None)
    if not module_file:
        raise ValueError("plugin module has no filesystem location")
    root = Path(module_file).resolve().parent
    target = root.joinpath(*PurePosixPath(info.ui.entry).parts).resolve()
    if root != target and root not in target.parents:
        raise ValueError("plugin UI path escapes the plugin directory")
    if not target.is_file() or target.stat().st_size > _MAX_PLUGIN_UI_BYTES:
        raise ValueError("plugin UI entry is missing or too large")
    return target.read_text(encoding="utf-8")


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
    discovered = discover_plugins()

    def _enabled(plugin_name: str, visiting: set[str]) -> bool:
        info = discovered.get(plugin_name)
        if info is None or plugin_name in visiting:
            return False
        if info.kind != "core" and plugin_name in _DISABLED_PLUGINS:
            return False
        return all(
            _enabled(required, visiting | {plugin_name})
            for required in info.requires
        )

    return _enabled(name, set())


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
        if not str(manifest.get("name") or "").strip():
            print(f"[plugins] skipped '{mod_info.name}': PLUGIN.name is required")
            continue
        try:
            ui = parse_plugin_ui(manifest, module)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"[plugins] skipped '{mod_info.name}': invalid UI declaration: {exc}")
            continue
        if (
            create_router is None
            and not (isinstance(engine_meta, dict) and create_engine)
            and ui is None
        ):
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
            requires=tuple(dict.fromkeys(
                str(item).strip()
                for item in manifest.get("requires", [])
                if str(item).strip()
            )),
            ui=ui,
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
    # Stable topological order: dependency routers are created before plugins
    # that register handlers or other capabilities with them.
    pending = dict(sorted(found.items(), key=lambda kv: (kv[1].order, kv[1].name)))
    ordered: dict[str, PluginInfo] = {}
    while pending:
        ready = [
            name for name, info in pending.items()
            if all(required not in pending for required in info.requires)
        ]
        # Cycles remain visible in the management UI; plugin_enabled marks
        # them unavailable rather than hiding a configuration error.
        if not ready:
            ready = [next(iter(pending))]
        name = ready[0]
        ordered[name] = pending.pop(name)
    _CACHE = ordered
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
            candidates = ((known - disabled) | (enabled_extra & known)) | core
            changed = True
            while changed:
                changed = False
                for name in tuple(candidates):
                    if any(
                        required not in candidates
                        for required in discovered[name].requires
                    ):
                        candidates.remove(name)
                        changed = True
            return candidates

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
    if enabled:
        unavailable = [dep for dep in info.requires if not plugin_enabled(dep)]
        if unavailable:
            raise ValueError(
                f"plugin '{name}' requires enabled plugin(s): "
                + ", ".join(unavailable)
            )
    else:
        dependents = [
            item.name for item in all_plugins()
            if name in item.requires and plugin_enabled(item.name)
        ]
        if dependents:
            raise ValueError(
                f"plugin '{name}' is required by enabled plugin(s): "
                + ", ".join(dependents)
            )

    factory = get_session_factory()
    async with factory() as session:
        row = await session.scalar(select(PluginState).where(PluginState.name == name))
        if row is None:
            row = PluginState(name=name, enabled=enabled)
            session.add(row)
        else:
            row.enabled = enabled
        await session.commit()
