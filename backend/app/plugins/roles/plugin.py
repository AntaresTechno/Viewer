"""roles 插件 — 权限组（角色）管理与权限目录查询。"""

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

meta = {
    "name": "roles",
    "mount": "roles",
    "title": "权限组",
    "version": "1.0.0",
    "description": "权限组的增删改查与系统权限目录",
    "order": 21,
    "permissions": [
        ("roles.read", "查看权限组"),
        ("roles.manage", "创建/修改/删除权限组"),
        ("roles.catalog", "查看系统权限目录"),
    ],
}


def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.deps import require_perm
    from ...core.db import get_db
    from ...models import Role, User
    from ...plugins.registry import all_permission_keys

    router = APIRouter(tags=["roles"])

    class RoleBody(BaseModel):
        name: str = Field(min_length=1, max_length=64)
        description: str = ""
        permissions: list[str] = []

    def _pub(r: Role) -> dict:
        return {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "permissions": r.permissions or [],
            "is_system": r.is_system,
        }

    @router.get("/permissions/catalog")
    async def permission_catalog(current=Depends(require_perm("roles.catalog"))):
        catalog = [{"key": k, "title": t} for k, t in all_permission_keys()]
        grouped: dict[str, list[dict]] = {}
        for item in catalog:
            ns = item["key"].split(".")[0]
            grouped.setdefault(ns, []).append(item)
        return {"items": catalog, "grouped": grouped}

    @router.get("")
    async def list_roles(
        current=Depends(require_perm("roles.read")),
        db: AsyncSession = Depends(get_db),
    ):
        roles = (await db.execute(select(Role).order_by(Role.id))).scalars().all()
        items = [_pub(r) for r in roles]
        # count users per role in python (small scale ok)
        users = (await db.execute(select(User.role_ids))).scalars().all()
        counts: dict[int, int] = {}
        for ids in users:
            for rid in ids or []:
                counts[rid] = counts.get(rid, 0) + 1
        for item, r in zip(items, roles):
            item["users_count"] = counts.get(r.id, 0)
        return {"items": items}

    @router.post("", status_code=201)
    async def create_role(
        body: RoleBody,
        current=Depends(require_perm("roles.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        if await db.scalar(select(Role).where(Role.name == body.name)):
            raise HTTPException(400, "权限组名称已存在")
        r = Role(
            name=body.name, description=body.description,
            permissions=body.permissions, is_system=False,
        )
        db.add(r)
        await db.commit()
        await db.refresh(r)
        return _pub(r)

    @router.patch("/{role_id}")
    async def update_role(
        role_id: int,
        body: RoleBody,
        current=Depends(require_perm("roles.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        r = await db.get(Role, role_id)
        if not r:
            raise HTTPException(404, "权限组不存在")
        if r.is_system and body.name != r.name:
            raise HTTPException(400, "系统内置权限组不可改名")
        dup = await db.scalar(
            select(Role).where(Role.name == body.name, Role.id != role_id)
        )
        if dup:
            raise HTTPException(400, "权限组名称已存在")
        r.name = body.name
        r.description = body.description
        r.permissions = body.permissions
        await db.commit()
        return _pub(r)

    @router.delete("/{role_id}")
    async def delete_role(
        role_id: int,
        current=Depends(require_perm("roles.manage")),
        db: AsyncSession = Depends(get_db),
    ):
        r = await db.get(Role, role_id)
        if not r:
            raise HTTPException(404, "权限组不存在")
        if r.is_system:
            raise HTTPException(400, "系统内置权限组不可删除")
        users = (await db.execute(select(User))).scalars().all()
        for u in users:
            if role_id in (u.role_ids or []):
                u.role_ids = [x for x in u.role_ids if x != role_id]
        await db.delete(r)
        await db.commit()
        return {"ok": True}

    return router
