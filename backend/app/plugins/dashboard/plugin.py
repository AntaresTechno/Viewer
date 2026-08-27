"""dashboard 插件 — 管理端仪表盘统计。"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

meta = {
    "name": "dashboard",
    "mount": "dashboard",
    "title": "仪表盘",
    "version": "1.0.0",
    "description": "站点概览统计",
    "order": 23,
    "permissions": [("dashboard.read", "查看仪表盘统计")],
}


def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.deps import require_perm
    from ...core.db import get_db
    from ...models import BookSourceRow, PluginState, Role, ShelfItem, User
    from ...plugins.registry import all_plugins

    router = APIRouter(tags=["dashboard"])

    @router.get("")
    async def summary(
        current=Depends(require_perm("dashboard.read")),
        db: AsyncSession = Depends(get_db),
    ):
        users_total = await db.scalar(select(func.count()).select_from(User))
        sources_total = await db.scalar(select(func.count()).select_from(BookSourceRow))
        shelf_total = await db.scalar(select(func.count()).select_from(ShelfItem))
        roles_total = await db.scalar(select(func.count()).select_from(Role))
        recent_users = (
            await db.execute(select(User).order_by(User.created_at.desc()).limit(5))
        ).scalars().all()
        plugins = all_plugins()
        states = {
            r.name: r.enabled
            for r in (await db.execute(select(PluginState))).scalars().all()
        }
        return {
            "users_total": users_total or 0,
            "sources_total": sources_total or 0,
            "shelf_total": shelf_total or 0,
            "roles_total": roles_total or 0,
            "plugins_enabled": sum(1 for p in plugins if states.get(p.name, True)),
            "plugins_total": len(plugins),
            "recent_users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "display_name": u.display_name or u.username,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in recent_users
            ],
            "server_time": datetime.now(timezone.utc).isoformat(),
        }

    return router
