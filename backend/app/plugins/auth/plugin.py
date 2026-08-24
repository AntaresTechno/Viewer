"""auth 插件 — 登录 / 注册 / 个人资料。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

meta = {
    "name": "auth",
    "mount": "auth",
    "title": "认证",
    "version": "1.0.0",
    "description": "登录、注册、个人资料与密码管理",
    "order": 10,
    "permissions": [
        ("auth.basic", "基础登录权限"),
        ("auth.register", "允许注册新账号"),
        ("auth.admin.view", "查看他人资料（管理）"),
    ],
}


def create_router(ctx) -> APIRouter:
    from ...core.deps import get_current_user
    from ...core.db import get_db
    from ...core.security import create_access_token, hash_password, verify_password
    from ...models import Role, ShelfItem, User

    router = APIRouter(tags=["auth"])

    class LoginBody(BaseModel):
        username: str = Field(min_length=1, max_length=64)
        password: str = Field(min_length=1, max_length=128)

    class RegisterBody(LoginBody):
        display_name: str = Field(default="", max_length=64)

    class PasswordBody(BaseModel):
        old_password: str
        new_password: str = Field(min_length=6, max_length=128)

    class ProfileBody(BaseModel):
        display_name: str | None = None
        email: str | None = None
        bio: str | None = None
        avatar_hue: int | None = None

    def user_public(user: User, perms: list[str]) -> dict:
        return {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name or user.username,
            "email": user.email,
            "bio": user.bio,
            "avatar_hue": user.avatar_hue,
            "is_superuser": user.is_superuser,
            "is_active": user.is_active,
            "role_ids": user.role_ids,
            "permissions": perms,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login_at": (
                user.last_login_at.isoformat() if user.last_login_at else None
            ),
        }

    @router.post("/register")
    async def register(body: RegisterBody, db: AsyncSession = Depends(get_db)):
        exists = await db.scalar(select(User).where(User.username == body.username))
        if exists:
            raise HTTPException(400, "用户名已存在")
        default_role = await db.scalar(select(Role).where(Role.name == "user"))
        user = User(
            username=body.username,
            password_hash=hash_password(body.password),
            display_name=body.display_name or body.username,
            role_ids=[default_role.id] if default_role else [],
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        token = create_access_token(user.id, user.username, user.is_superuser)
        return {
            "token": token,
            "user": user_public(user, ["auth.basic", "books.search"]),
        }

    @router.post("/login")
    async def login(body: LoginBody, db: AsyncSession = Depends(get_db)):
        user = await db.scalar(select(User).where(User.username == body.username))
        if not user or not verify_password(body.password, user.password_hash):
            raise HTTPException(401, "用户名或密码错误")
        if not user.is_active:
            raise HTTPException(403, "账号已被禁用")
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()
        perms: set[str] = set()
        for rid in user.role_ids or []:
            role = await db.get(Role, rid)
            if role:
                perms.update(role.permissions or [])
        token = create_access_token(user.id, user.username, user.is_superuser)
        return {"token": token, "user": user_public(user, sorted(perms))}

    @router.get("/me")
    async def me(current=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
        from sqlalchemy import func

        user, perms = current
        shelf_count = await db.scalar(
            select(func.count()).select_from(ShelfItem).where(
                ShelfItem.user_id == user.id
            )
        )
        data = user_public(user, perms)
        data["shelf_count"] = shelf_count or 0
        return data

    @router.patch("/me/profile")
    async def update_profile(
        body: ProfileBody,
        current=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        if body.display_name is not None:
            user.display_name = body.display_name[:64]
        if body.email is not None:
            user.email = body.email[:128]
        if body.bio is not None:
            user.bio = body.bio[:2000]
        if body.avatar_hue is not None:
            user.avatar_hue = max(0, min(360, int(body.avatar_hue)))
        await db.commit()
        return user_public(user, [])

    @router.post("/me/password")
    async def change_password(
        body: PasswordBody,
        current=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        user, _ = current
        if not verify_password(body.old_password, user.password_hash):
            raise HTTPException(400, "原密码不正确")
        user.password_hash = hash_password(body.new_password)
        await db.commit()
        return {"ok": True}

    return router
