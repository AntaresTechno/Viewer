"""FastAPI dependencies: current user & permission enforcement."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .security import decode_token
from ..models import Role, User


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    token = request.query_params.get("token")
    return token or None


def _extract_token_ws(websocket: WebSocket) -> str | None:
    auth = websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return websocket.query_params.get("token")


async def resolve_user(
    db: AsyncSession, token: str | None
) -> tuple[User, list[str]] | None:
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    user = await db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        return None
    perms: set[str] = set()
    for rid in user.role_ids or []:
        role = await db.scalar(select(Role).where(Role.id == rid))
        if role:
            perms.update(role.permissions or [])
    return user, sorted(perms)


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> tuple[User, list[str]]:
    resolved = await resolve_user(db, _extract_token(request))
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return resolved


CurrentUser = AnnotatedUser = tuple  # alias for readability in routers


def require_perm(perm_key: str):
    """Dependency factory enforcing a permission key.

    Superusers bypass all checks. ``*`` grants everything.
    """

    async def checker(current: tuple[User, list[str]] = Depends(get_current_user)):
        user, perms = current
        if user.is_superuser:
            return current
        if "*" in perms or perm_key in perms:
            return current
        # wildcard prefix e.g. books.*
        prefix = perm_key.split(".")[0] + ".*"
        if prefix in perms:
            return current
        raise HTTPException(status.HTTP_403_FORBIDDEN, "权限不足：" + perm_key)

    return checker


def require_superuser(current: tuple[User, Any] = Depends(get_current_user)) -> tuple:
    user, perms = current
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要超级管理员权限")
    return current
