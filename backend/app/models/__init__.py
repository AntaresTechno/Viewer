"""SQLAlchemy ORM models."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    avatar_hue: Mapped[int] = mapped_column(Integer, default=217)  # MD3 tonal hue
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(256), default="")
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class PluginState(Base):
    __tablename__ = "plugin_states"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class BookSourceRow(Base):
    """A book source stored as canonical JSON, parsed by a pluggable engine."""

    __tablename__ = "book_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    source_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    source_group: Mapped[str] = mapped_column(String(256), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    custom_order: Mapped[int] = mapped_column(Integer, default=0)
    raw_json: Mapped[str] = mapped_column(Text)
    # which source-rule engine parses this source ("legado", future: others)
    engine: Mapped[str] = mapped_column(String(32), default="legado")


class ShelfItem(Base):
    __tablename__ = "shelf_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    book_url: Mapped[str] = mapped_column(String(1024))
    toc_url: Mapped[str] = mapped_column(String(1024), default="")
    name: Mapped[str] = mapped_column(String(256), default="")
    author: Mapped[str] = mapped_column(String(128), default="")
    cover_url: Mapped[str] = mapped_column(String(1024), default="")
    intro: Mapped[str] = mapped_column(Text, default="")
    last_chapter: Mapped[str] = mapped_column(String(256), default="")
    source_url: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReadProgress(Base):
    __tablename__ = "read_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    book_url: Mapped[str] = mapped_column(String(1024), index=True)
    chapter_index: Mapped[int] = mapped_column(Integer, default=0)
    chapter_title: Mapped[str] = mapped_column(String(256), default="")
    offset: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BookRef(Base):
    """打开过的书籍注册表：短 id -> 书籍定位信息（阅读器短链接用）。

    除定位信息外还冗余一份"最基本信息"（书名/作者/封面/简介/分类/最新章节/
    目录页），打开阅读器时全部走本地缓存，不再现场请求书源。
    """

    __tablename__ = "book_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String(512), index=True)
    book_url: Mapped[str] = mapped_column(String(1024), index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    author: Mapped[str] = mapped_column(String(128), default="")
    cover_url: Mapped[str] = mapped_column(String(1024), default="")
    intro: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(256), default="")
    last_chapter: Mapped[str] = mapped_column(String(256), default="")
    toc_url: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BookChapter(Base):
    """Cached TOC entry for one (source, book), filled by the toc queue."""

    __tablename__ = "book_chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String(512), index=True)
    book_url: Mapped[str] = mapped_column(String(1024), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(256), default="")
    url: Mapped[str] = mapped_column(String(1024), default="")
    base_url: Mapped[str] = mapped_column(String(1024), default="")
    is_volume: Mapped[bool] = mapped_column(Boolean, default=False)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)


class BookChapterContent(Base):
    """本地书库：已下载的章节正文（离线可读，不再回源）。

    由 /books/content 在首次下载时写入；后续请求先查这里，命中即返回，
    真正把内容"拉进数据库"而非每次代理回源。
    """

    __tablename__ = "book_chapter_contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String(512), index=True)
    book_url: Mapped[str] = mapped_column(String(1024), index=True, default="")
    url: Mapped[str] = mapped_column(String(1024), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BookAsset(Base):
    """本地书库：已下载的二进制资源（封面 / 正文插图）。

    由 /books/cover 在首次下载时写入；后续请求先查这里，命中即从本地
    库返回字节，不再回源。
    """

    __tablename__ = "book_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="cover")
    mime: Mapped[str] = mapped_column(String(128), default="")
    blob: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TocJob(Base):
    """One background TOC fetch for a (source, book); status shown in UI."""

    __tablename__ = "toc_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String(512), index=True)
    book_url: Mapped[str] = mapped_column(String(1024), index=True)
    # queued -> running -> done | error
    status: Mapped[str] = mapped_column(String(16), default="queued")
    error: Mapped[str] = mapped_column(Text, default="")
    chapters: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ReplaceRule(Base):
    """净化/替换规则（legado ReplaceRule 的实用子集）。"""

    __tablename__ = "replace_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    group: Mapped[str] = mapped_column(String(128), default="")
    group_order: Mapped[int] = mapped_column(Integer, default=0)
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    pattern: Mapped[str] = mapped_column(Text, default="")
    replacement: Mapped[str] = mapped_column(Text, default="")
    # 作用范围：空 = 全部；条目按 换行/分号/|| 分隔，子串匹配书名、源名或源 URL，
    # 前缀 "-" 表示排除。
    scope: Mapped[str] = mapped_column(String(512), default="")
    regex: Mapped[bool] = mapped_column(Boolean, default=True)  # False = 纯文本替换
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=True)
