"""每日自动刷新：定时拉取书架全部书籍的最新目录，检测更新。

- 每天在配置的小时（默认 4 点）运行一次：把书架里所有不重复的
  (source_url, book_url) 排入 toc 队列（队列串行，不会压垮书源）；
- 目录变化时 toc_queue 会给书架条目打 ``updated_at``/``has_update``，
  书架「按更新排序」与首页「有更新」提醒由此驱动；
- 刷新完成后为开启了 auto_backup 的用户执行一次 WebDAV 备份；
- 上次运行日期记录在 ``app_kv``，重启后当天不会重复跑。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

log = logging.getLogger("viewer.daily_refresh")

_KV_LAST_RUN = "daily_refresh_last_run"

_task: asyncio.Task | None = None


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _seconds_until_next_run(hour: int) -> float:
    """距下一次「今天(若未过)或明天的 hour:00」的秒数。"""
    now = datetime.now().astimezone()
    target = now.replace(hour=max(0, min(hour, 23)), minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(5.0, (target - now).total_seconds())


async def refresh_all_shelves() -> dict:
    """把书架上所有不重复的书排队抓一次最新目录。"""
    from sqlalchemy import select

    from ..models import ShelfItem
    from ..core.db import get_session_factory
    from .toc_queue import toc_queue

    factory = get_session_factory()
    async with factory() as db:
        pairs = (await db.execute(
            select(ShelfItem.source_url, ShelfItem.book_url).distinct()
        )).all()
        queued = 0
        for source_url, book_url in pairs:
            job = await toc_queue.create_job(db, source_url, book_url)
            if job is not None:
                toc_queue.enqueue(job.id)
                queued += 1
    return {"books": len(pairs), "queued": queued}


async def auto_backup_enabled_users() -> int:
    """为开启 auto_backup 的用户各做一次 WebDAV 备份。"""
    from sqlalchemy import select

    from ..core.db import get_session_factory
    from ..models import WebDavConfig
    from ..plugins.registry import plugin_enabled

    if not plugin_enabled("webdav"):
        return 0
    from ..plugins.webdav.plugin import run_backup

    factory = get_session_factory()
    async with factory() as db:
        user_ids = [r[0] for r in (await db.execute(
            select(WebDavConfig.user_id).where(WebDavConfig.auto_backup.is_(True))
        )).all()]

    done = 0
    for uid in user_ids:
        try:
            await run_backup(uid)
            done += 1
        except Exception as exc:  # noqa: BLE001 — 单个用户失败不影响其他人
            log.warning("webdav auto backup failed for user %s: %r", uid, exc)
    return done


async def mark_ran() -> None:
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from ..core.db import get_session_factory
    from ..models import AppKV

    factory = get_session_factory()
    async with factory() as db:
        stmt = sqlite_insert(AppKV).values(key=_KV_LAST_RUN, value=_today())
        stmt = stmt.on_conflict_do_update(
            index_elements=[AppKV.key], set_={"value": _today()}
        )
        await db.execute(stmt)
        await db.commit()


async def already_ran_today() -> bool:
    from ..core.db import get_session_factory
    from ..models import AppKV

    factory = get_session_factory()
    async with factory() as db:
        row = await db.get(AppKV, _KV_LAST_RUN)
        return bool(row and row.value == _today())


async def run_once(reason: str) -> dict | None:
    """执行一轮「目录刷新 → 自动备份」，返回统计（异常时记日志返回 None）。"""
    try:
        stats = await refresh_all_shelves()
        backups = await auto_backup_enabled_users()
        await mark_ran()
        log.info("daily refresh (%s): %s, webdav backups=%s", reason, stats, backups)
        return {**stats, "backups": backups}
    except Exception as exc:  # noqa: BLE001 — 调度循环绝不因此退出
        log.error("daily refresh failed: %r", exc)
        return None


async def _loop(hour: int, run_immediately: bool) -> None:
    if run_immediately:
        # 启动后稍等片刻（等插件/网络就绪），错开高峰也避免与请求抢锁
        await asyncio.sleep(45)
        await run_once("startup")
    while True:
        wait = _seconds_until_next_run(hour)
        log.info("next daily refresh in %.0f s", wait)
        await asyncio.sleep(wait)
        await run_once("scheduled")


def start() -> None:
    """在事件循环里启动调度协程（幂等）。"""
    global _task
    if _task is not None and not _task.done():
        return

    from ..core.config import settings

    if not settings.daily_refresh_enabled:
        log.info("daily refresh disabled by config")
        return

    async def _bootstrap() -> None:
        immediate = settings.daily_refresh_catch_up and not await already_ran_today()
        await _loop(settings.daily_refresh_hour, immediate)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning("daily_refresh.start() called without a running loop")
        return
    _task = loop.create_task(_bootstrap())
