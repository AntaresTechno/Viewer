"""SQLAlchemy ORM models."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
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
    # 书源侧最近一次检测到内容变化的时间（目录刷新发现新章/换章时更新），
    # 书架「按更新排序」与首页「有更新」提醒都基于它。
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    has_update: Mapped[bool] = mapped_column(Boolean, default=False)


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


class PurifyPack(Base):
    """正文净化插件：一组净化规则的容器（如「乌云净化」类成包方案）。

    可整体启停/排序；``origin`` 标记来源（preset:<key> / import / manual）。
    """

    __tablename__ = "purify_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(String(512), default="")
    origin: Mapped[str] = mapped_column(String(256), default="", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PurifyRule(Base):
    """正文净化插件：包内单条替换/净化规则（语义与 ReplaceRule 对齐）。"""

    __tablename__ = "purify_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pack_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("purify_packs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), default="")
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    pattern: Mapped[str] = mapped_column(Text, default="")
    replacement: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(512), default="")
    regex: Mapped[bool] = mapped_column(Boolean, default=True)  # False = 纯文本替换
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=True)
    # legado 新版导出格式的作用域：规则应用于章节标题还是正文
    scope_content: Mapped[bool] = mapped_column(Boolean, default=True)
    scope_title: Mapped[bool] = mapped_column(Boolean, default=False)


class PurifiedContent(Base):
    """正文净化插件：净化结果缓存（一章一条）。

    命中指纹直接调用；规则变化时用 raw 本地重新净化（离线可用）；
    抓取失败时兜底返回旧净化结果。
    """

    __tablename__ = "purified_contents"
    __table_args__ = (UniqueConstraint("source_url", "url", name="uq_purify_chapter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String(512), index=True)
    book_url: Mapped[str] = mapped_column(String(1024), default="", index=True)
    url: Mapped[str] = mapped_column(String(1024), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    raw: Mapped[str] = mapped_column(Text, default="")  # 引擎抓取的原始正文
    content: Mapped[str] = mapped_column(Text, default="")  # 净化后的正文
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    applied: Mapped[list] = mapped_column(JSON, default=list)  # 命中的规则名
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


def local_today() -> str:
    """服务器本地时区的 YYYY-MM-DD（首页阅读统计按自然日聚合）。"""
    from datetime import datetime as _dt

    return _dt.now().astimezone().strftime("%Y-%m-%d")


class ReadingStat(Base):
    """首页插件：按 用户/自然日/书 聚合的阅读时长（秒）。

    阅读器前端每 30 秒心跳上报一次在读时长；累计阅读、今日/总时长、
    连续天数与近 14 天柱状图全部由这张表推导。
    """

    __tablename__ = "reading_stats"
    __table_args__ = (
        UniqueConstraint("user_id", "day", "book_url", name="uq_reading_stat_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    day: Mapped[str] = mapped_column(String(10), index=True, default=local_today)
    book_url: Mapped[str] = mapped_column(String(1024))
    source_url: Mapped[str] = mapped_column(String(512), default="")
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WebDavConfig(Base):
    """WebDAV 插件：每用户一份远端备份配置（书架/进度/设置备份）。"""

    __tablename__ = "webdav_configs"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(512), default="")  # 服务器根地址
    username: Mapped[str] = mapped_column(String(256), default="")
    # 简单混淆存储（base64）；WebDAV 密码需要可还原以发起请求。
    password_enc: Mapped[str] = mapped_column(Text, default="")
    directory: Mapped[str] = mapped_column(String(256), default="AntaresViewer")
    auto_backup: Mapped[bool] = mapped_column(Boolean, default=False)
    last_backup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_backup_file: Mapped[str] = mapped_column(String(256), default="")


class AppKV(Base):
    """极简键值表：记录每日任务「上次运行日期」之类的内部状态。"""

    __tablename__ = "app_kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
