"""users 插件 — 管理员用户管理。"""

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

meta = {
    "name": "users",
    "mount": "users",
    "title": "用户管理",
    "version": "1.0.0",
    "description": "管理员对用户账号进行增删改查、分配权限组",
    "order": 20,
    "permissions": [
        ("users.read", "查看用户列表"),
        ("users.create", "创建用户"),
        ("users.update", "修改用户"),
        ("users.delete", "删除用户"),
        ("users.reset_password", "重置用户密码"),
    ],
}


def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.deps import require_perm
    from ...core.db import get_db
    from ...core.security import hash_password
    from ...models import User

    router = APIRouter(tags=["users"])

    class CreateBody(BaseModel):
        username: str = Field(min_length=2, max_length=64)
        password: str = Field(min_length=6, max_length=128)
        display_name: str = ""
        email: str = ""
        is_superuser: bool = False
        role_ids: list[int] = []

    class UpdateBody(BaseModel):
        display_name: str | None = None
        email: str | None = None
        bio: str | None = None
        avatar_hue: int | None = None
        is_active: bool | None = None
        is_superuser: bool | None = None
        role_ids: list[int] | None = None

    class ResetPasswordBody(BaseModel):
        new_password: str = Field(min_length=6, max_length=128)

    def _pub(u: User) -> dict:
        return {
            "id": u.id,
            "username": u.username,
            "display_name": u.display_name or u.username,
            "email": u.email,
            "bio": u.bio,
            "avatar_hue": u.avatar_hue,
            "is_superuser": u.is_superuser,
            "is_active": u.is_active,
            "role_ids": u.role_ids,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": (
                u.last_login_at.isoformat() if u.last_login_at else None
            ),
        }

    @router.get("")
    async def list_users(
        keyword: str = "",
        page: int = 1,
        size: int = 20,
        current=Depends(require_perm("users.read")),
        db: AsyncSession = Depends(get_db),
    ):
        stmt = select(User).order_by(User.id)
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(or_(User.username.like(like), User.display_name.like(like)))
        total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = (
            (await db.execute(stmt.limit(size).offset((page - 1) * size))).scalars().all()
        )
        return {"total": total or 0, "items": [_pub(r) for r in rows]}

    @router.post("", status_code=201)
    async def create_user(
        body: CreateBody,
        current=Depends(require_perm("users.create")),
        db: AsyncSession = Depends(get_db),
    ):
        if await db.scalar(select(User).where(User.username == body.username)):
            raise HTTPException(400, "用户名已存在")
        u = User(
            username=body.username,
            password_hash=hash_password(body.password),
            display_name=body.display_name,
            email=body.email,
            is_superuser=body.is_superuser,
            role_ids=body.role_ids,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return _pub(u)

    @router.patch("/{user_id}")
    async def update_user(
        user_id: int,
        body: UpdateBody,
        current=Depends(require_perm("users.update")),
        db: AsyncSession = Depends(get_db),
    ):
        actor, _ = current
        u = await db.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        data = body.model_dump(exclude_unset=True)
        if "is_active" in data and u.id == actor.id and not data["is_active"]:
            raise HTTPException(400, "不能禁用自己")
        for k, v in data.items():
            setattr(u, k, v)
        await db.commit()
        return _pub(u)

    @router.delete("/{user_id}")
    async def delete_user(
        user_id: int,
        current=Depends(require_perm("users.delete")),
        db: AsyncSession = Depends(get_db),
    ):
        actor, _ = current
        if user_id == actor.id:
            raise HTTPException(400, "不能删除自己")
        u = await db.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        if u.is_superuser:
            supers = await db.scalar(
                select(func.count()).select_from(User).where(User.is_superuser)
            )
            if (supers or 0) <= 1:
                raise HTTPException(400, "至少保留一个超级管理员")
        await db.delete(u)
        await db.commit()
        return {"ok": True}

    @router.post("/{user_id}/reset-password")
    async def reset_password(
        user_id: int,
        body: ResetPasswordBody,
        current=Depends(require_perm("users.reset_password")),
        db: AsyncSession = Depends(get_db),
    ):
        u = await db.get(User, user_id)
        if not u:
            raise HTTPException(404, "用户不存在")
        u.password_hash = hash_password(body.new_password)
        await db.commit()
        return {"ok": True}

    return router
