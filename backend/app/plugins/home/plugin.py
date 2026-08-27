"""home 插件 — 首页：最近阅读 / 累计阅读 / 阅读时长。

数据来源：
- 最近阅读：``read_progress``（按更新时间倒序），书名/封面优先取本人书架，
  其次任意 ``book_refs`` 档案；
- 累计/时长：``reading_stats``（阅读器每 30s 心跳上报的按日聚合秒数），
  推导出今日时长、总时长、累计天数、在读本书数与连续阅读天数；
- 有更新：书架上 ``has_update`` 的条目（目录刷新检测到新章时打标）。
"""

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ...plugins.registry import PluginContext

meta = {
    "name": "home",
    "mount": "home",
    "title": "首页",
    "version": "1.0.0",
    "description": "最近阅读、累计阅读与阅读时长统计",
    "order": 20,
    "permissions": [
        ("home.read", "查看首页（最近阅读/统计）"),
        ("home.stats.write", "上报阅读时长心跳"),
    ],
}


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def create_router(ctx: "PluginContext") -> APIRouter:
    from ...core.deps import require_perm
    from ...core.db import get_db
    from ...models import BookRef, ReadingStat, ReadProgress, ShelfItem

    router = APIRouter(tags=["home"])

    def _recent_item(p, shelf, ref) -> dict:
        name = (shelf.name if shelf else "") or (ref.name if ref else "") or p.book_url
        author = (shelf.author if shelf else "") or (ref.author if ref else "")
        cover = (shelf.cover_url if shelf else "") or (ref.cover_url if ref else "")
        intro = (shelf.intro if shelf else "") or (ref.intro if ref else "")
        src = (shelf.source_url if shelf else "") or (ref.source_url if ref else "")
        last_chapter = (shelf.last_chapter if shelf else "") \
            or (ref.last_chapter if ref else "")
        return {
            "bookUrl": p.book_url,
            "sourceUrl": src,
            "name": name,
            "author": author,
            "coverUrl": cover,
            "intro": intro[:160],
            "lastChapter": last_chapter,
            "chapterIndex": p.chapter_index,
            "chapterTitle": p.chapter_title,
            "readAt": _iso(p.updated_at),
        }

    @router.get("/summary")
    async def summary(
        current=Depends(require_perm("home.read")),
        db: AsyncSession = Depends(get_db),
    ):
        """首页聚合：最近阅读 + 累计统计 + 今日时长 + 书架有更新。"""
        user, _ = current
        today = datetime.now().astimezone().strftime("%Y-%m-%d")

        recent_rows = (await db.execute(
            select(ReadProgress).where(ReadProgress.user_id == user.id)
            .order_by(ReadProgress.updated_at.desc()).limit(12)
        )).scalars().all()

        # ---- 阅读时长聚合（reading_stats）----
        total_seconds = await db.scalar(
            select(func.coalesce(func.sum(ReadingStat.seconds), 0)).where(
                ReadingStat.user_id == user.id)
        )
        today_seconds = await db.scalar(
            select(func.coalesce(func.sum(ReadingStat.seconds), 0)).where(
                ReadingStat.user_id == user.id, ReadingStat.day == today)
        )
        total_days = await db.scalar(
            select(func.count(func.distinct(ReadingStat.day))).where(
                ReadingStat.user_id == user.id)
        )
        total_books = await db.scalar(
            select(func.count(func.distinct(ReadingStat.book_url))).where(
                ReadingStat.user_id == user.id)
        )
        day_rows = (await db.execute(
            select(ReadingStat.day).where(ReadingStat.user_id == user.id)
            .group_by(ReadingStat.day)
        )).scalars().all()

        # 连续阅读天数：从今天（或昨天）往前数连续有记录的天数
        days = {d for d in day_rows}
        streak = 0
        cursor = date.today()
        if today not in days:
            cursor -= timedelta(days=1)
        while cursor.strftime("%Y-%m-%d") in days:
            streak += 1
            cursor -= timedelta(days=1)

        # ---- 书架有更新 ----
        shelf_items = (await db.execute(
            select(ShelfItem).where(
                ShelfItem.user_id == user.id, ShelfItem.has_update.is_(True))
            .order_by(ShelfItem.updated_at.desc()).limit(10)
        )).scalars().all()
        prog_by_book = {p.book_url: p for p in (
            await db.execute(
                select(ReadProgress).where(ReadProgress.user_id == user.id)
            )).scalars().all()}
        updates = []
        for it in shelf_items:
            p = prog_by_book.get(it.book_url)
            updates.append({
                "id": it.id,
                "bookUrl": it.book_url,
                "sourceUrl": it.source_url,
                "name": it.name,
                "author": it.author,
                "coverUrl": it.cover_url,
                "lastChapter": it.last_chapter,
                "updatedAt": _iso(it.updated_at),
                "readAt": _iso(p.updated_at) if p else None,
            })

        # ---- 最近阅读明细 ----
        shelf_rows = (await db.execute(
            select(ShelfItem).where(ShelfItem.user_id == user.id)
        )).scalars().all()
        shelf_by = {r.book_url: r for r in shelf_rows}
        need_refs = [p.book_url for p in recent_rows if p.book_url not in shelf_by]
        ref_by: dict[str, BookRef] = {}
        if need_refs:
            refs = (await db.execute(
                select(BookRef).where(BookRef.book_url.in_(need_refs))
            )).scalars().all()
            for r in refs:
                ref_by.setdefault(r.book_url, r)
        recents = [
            _recent_item(p, shelf_by.get(p.book_url), ref_by.get(p.book_url))
            for p in recent_rows
        ]

        return {
            "todaySeconds": int(today_seconds or 0),
            "totalSeconds": int(total_seconds or 0),
            "totalDays": int(total_days or 0),
            "totalBooks": int(total_books or 0),
            "streakDays": streak,
            "recents": recents,
            "updates": updates,
            "date": today,
        }

    class HeartbeatBody(BaseModel):
        bookUrl: str = Field(min_length=1)
        sourceUrl: str = ""
        seconds: int = Field(default=30, ge=1, le=300)

    @router.post("/heartbeat")
    async def heartbeat(
        body: HeartbeatBody,
        current=Depends(require_perm("home.stats.write")),
        db: AsyncSession = Depends(get_db),
    ):
        """阅读器心跳：把一段在读秒数累加到（用户, 今天, 这本书）。"""
        user, _ = current
        day = datetime.now().astimezone().strftime("%Y-%m-%d")
        row = await db.scalar(
            select(ReadingStat).where(
                ReadingStat.user_id == user.id,
                ReadingStat.day == day,
                ReadingStat.book_url == body.bookUrl,
            )
        )
        if row is None:
            row = ReadingStat(
                user_id=user.id, day=day,
                book_url=body.bookUrl, source_url=body.sourceUrl,
            )
            db.add(row)
        row.seconds = int(row.seconds or 0) + body.seconds
        await db.commit()
        return {"ok": True, "secondsToday": row.seconds}

    @router.get("/daily")
    async def daily(
        days: int = 14,
        current=Depends(require_perm("home.read")),
        db: AsyncSession = Depends(get_db),
    ):
        """近 N 天每日阅读秒数（首页迷你柱状图）。"""
        user, _ = current
        days = max(1, min(days, 60))
        since = (date.today() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        rows = (await db.execute(
            select(ReadingStat.day, func.sum(ReadingStat.seconds))
            .where(ReadingStat.user_id == user.id, ReadingStat.day >= since)
            .group_by(ReadingStat.day)
        )).all()
        by_day = {d: int(s or 0) for d, s in rows}
        out = []
        for i in range(days):
            d = (date.today() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
            out.append({"day": d, "seconds": by_day.get(d, 0)})
        return {"items": out}

    return router
