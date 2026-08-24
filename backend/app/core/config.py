"""Application settings."""
from __future__ import annotations

import secrets
from pathlib import Path

from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    app_name: str = "Viewer"
    secret_key: str = secrets.token_hex(32)
    token_algorithm: str = "HS256"
    token_expire_minutes: int = 60 * 24 * 14  # 14 days
    database_url: str = f"sqlite+aiosqlite:///{BACKEND_DIR / 'data' / 'viewer.db'}"
    first_admin_username: str = "admin"
    first_admin_password: str = "view123456"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3080",
        "http://127.0.0.1:3080",
    ]
    request_timeout: float = 15.0
    default_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    toc_page_limit: int = 40          # nextTocUrl 最大翻页次数
    content_page_limit: int = 30      # nextContentUrl 最大翻页次数
    search_per_source_limit: int = 20
    image_cache_mb: int = 300         # 图片磁盘缓存上限（LRU 逐出）
    replace_regex_timeout: float = 5.0  # 单条净化规则正则执行超时

    # ---- 并发控制（"可调线程"） ----
    parser_concurrency: int = 4        # 解析器目录/正文多页并行的最大并发请求数
    search_concurrency: int = 6        # 跨源搜索同时请求的书源数
    prefetch_concurrency: int = 3      # 阅读器内容预取的最大并发请求数
    library_download_concurrency: int = 4  # 整本预下载的最大并发章节数

    model_config = {"env_prefix": "VIEWER_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
