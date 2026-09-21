"""进程内共享的运行时对象。

放在单独模块，避免 `api` 与 `main` 之间循环导入。
"""
from __future__ import annotations

from .config import apply_runtime_overrides, settings
from .credentials import CredentialStore
from .db import Database
from .discovery import DirectDiscovery
from .lx_gateway import LxGateway
from .platform_auth import PlatformAuthClient
from .scheduler import Scheduler

db = Database(str(settings.db_path))
lx_gateway = LxGateway()
platform_auth = PlatformAuthClient()
discovery = DirectDiscovery(platform_auth=platform_auth)
credential_store = CredentialStore(settings.users_dir, settings.data_dir)
scheduler = Scheduler(db, lx_gateway, discovery=discovery, credential_store=credential_store)
db.recover_running_runs()

DEFAULTS: dict[str, object] = {
    "default_quality": settings.default_quality,
    "default_interval_minutes": 360,
    "default_max_downloads": 30,
    "default_embed": 1,
    "default_fallback": "best_effort",
    "download_concurrency": settings.download_concurrency,
    "download_retries": settings.download_retries,
    "download_batch": settings.download_batch,
    "download_batch_gap": settings.download_batch_gap,
    "download_timeout": settings.download_timeout,
    "lx_platform_priority": list(settings.lx_platform_priority),
    "lx_quality_floor": settings.lx_quality_floor,
    "lx_search_limit": settings.lx_search_limit,
    "lx_match_threshold": settings.lx_match_threshold,
    "lx_download_subdir": "",
    "lx_filename_template": settings.lx_filename_template,
    "lx_artist_dir": settings.lx_artist_dir,
    "tick_seconds": settings.tick_seconds,
    "max_downloads_per_run": settings.max_downloads_per_run,
}


def seed_defaults() -> None:
    for key, value in DEFAULTS.items():
        if db.get_setting(key) is None:
            db.set_setting(key, value)
    apply_runtime_overrides({key: db.get_setting(key) for key in DEFAULTS if key in {
        "download_concurrency", "download_retries", "download_batch", "download_batch_gap",
        "download_timeout", "lx_platform_priority", "lx_quality_floor", "lx_search_limit",
        "lx_match_threshold",
        "lx_download_subdir",
        "lx_filename_template", "lx_artist_dir", "tick_seconds", "max_downloads_per_run",
    }})
