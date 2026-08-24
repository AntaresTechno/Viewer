"""Async SQLAlchemy engine/session + startup seeding."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings
from ..models import Base  # noqa: F401  (ensures tables registered)


class _Db:
    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None


db = _Db()


def get_engine() -> AsyncEngine:
    if db.engine is None:
        db.engine = create_async_engine(settings.database_url, echo=False)
        db.session_factory = async_sessionmaker(db.engine, expire_on_commit=False)
    return db.engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert db.session_factory is not None
    return db.session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def _migrate_sqlite(conn) -> None:
    """Tiny additive-column migrations for pre-existing dev databases."""
    from sqlalchemy import text

    res = await conn.execute(text("PRAGMA table_info(book_sources)"))
    cols = {row[1] for row in res.fetchall()}
    if "engine" not in cols:
        await conn.execute(
            text("ALTER TABLE book_sources ADD COLUMN engine VARCHAR(32) "
                 "NOT NULL DEFAULT 'legado'")
        )

    # book_refs：阅读器短链缓存的最基本信息（简介/分类/最新章节/目录页）
    res = await conn.execute(text("PRAGMA table_info(book_refs)"))
    cols = {row[1] for row in res.fetchall()}
    additive = {
        "intro": "TEXT NOT NULL DEFAULT ''",
        "kind": "VARCHAR(256) NOT NULL DEFAULT ''",
        "last_chapter": "VARCHAR(256) NOT NULL DEFAULT ''",
        "toc_url": "VARCHAR(1024) NOT NULL DEFAULT ''",
    }
    for col, ddl in additive.items():
        if col not in cols:
            await conn.execute(
                text(f"ALTER TABLE book_refs ADD COLUMN {col} {ddl}")
            )

    # book_chapter_contents：本地书库按“书”分组的书 URL 列（预下载/本地库统计用）
    res = await conn.execute(text("PRAGMA table_info(book_chapter_contents)"))
    cols = {row[1] for row in res.fetchall()}
    if "book_url" not in cols:
        await conn.execute(
            text("ALTER TABLE book_chapter_contents ADD COLUMN "
                 "book_url VARCHAR(1024) NOT NULL DEFAULT ''")
        )


async def init_db() -> None:
    """Create tables and seed defaults (roles, admin user)."""
    from sqlalchemy import select

    from .security import hash_password
    from ..models import Role, User
    from ..plugins.registry import all_permission_keys

    factory = get_session_factory()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_sqlite(conn)

    async with factory() as session:
        # default roles
        roles = {
            "admin": ("管理员", ["*"]),
            "user": ("普通用户", [
                "auth.basic",
                "books.shelf.read", "books.shelf.write",
                "books.sources.read", "books.search", "books.explore",
                "books.toc", "books.content", "books.progress.write",
            ]),
            "guest": ("访客", ["auth.basic"]),
        }
        existing = (await session.execute(select(Role))).scalars().all()
        by_name = {r.name: r for r in existing}
        for name, (desc, perms) in roles.items():
            if name not in by_name:
                session.add(Role(name=name, description=desc, permissions=perms))

        # default admin account
        has_user = (
            await session.execute(select(User).limit(1))
        ).scalars().first()
        if has_user is None:
            admin_role = await session.scalar(select(Role).where(Role.name == "admin"))
            session.add(User(
                username=settings.first_admin_username,
                password_hash=hash_password(settings.first_admin_password),
                display_name="Administrator",
                email="",
                is_superuser=True,
                role_ids=[admin_role.id] if admin_role else [],
            ))
        await session.commit()


async def permission_catalog() -> list[dict]:
    """Aggregate permission declarations from all plugins."""
    return [
        {"key": k, "title": t}
        for k, t in all_permission_keys()
    ]
