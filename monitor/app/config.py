"""运行期配置。全部可通过环境变量覆盖。"""
from __future__ import annotations

import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        # 调度
        self.tick_seconds: int = max(10, _int("TICK_SECONDS", 60))
        # 下载默认串行，避免上游同时提交整批任务；需要更快时可用环境变量调大。
        self.download_concurrency: int = max(1, _int("DOWNLOAD_CONCURRENCY", 1))
        self.download_retries: int = max(0, _int("DOWNLOAD_RETRIES", 2))
        # ── 分批推送 ──────────────────────────────────────────────────────
        # 一轮里不把整份清单一次性灌给引擎，而是「推一批 → 等这批全部落盘 → 再推下一批」。
        # 好处：上游不会积压、日志两边数得清、中途失败也不会连带影响后面的批次。
        self.download_batch: int = max(1, _int("DOWNLOAD_BATCH", 8))
        self.download_batch_gap: float = max(0.0, _float("DOWNLOAD_BATCH_GAP", 3.0))
        # 单个下载请求的超时（无损单曲 30~80MB，上游要写完盘才回包，30s 不够）
        self.download_timeout: float = max(30.0, _float("DOWNLOAD_TIMEOUT", 300.0))
        self.default_quality: str = os.getenv("DEFAULT_QUALITY", "master").strip().lower()

        self.lx_gateway_url: str = os.getenv("LX_GATEWAY_URL", "http://lx-gateway:8090").rstrip("/")
        self.lx_gateway_token: str = os.getenv("LX_GATEWAY_TOKEN", "").strip()
        priority = [item.strip() for item in os.getenv("LX_PLATFORM_PRIORITY", "tx,kg,kw,mg,wy").split(",")]
        self.lx_platform_priority: tuple[str, ...] = tuple(
            item for item in priority if item in {"tx", "kg", "kw", "mg", "wy"}
        ) or ("tx", "kg", "kw", "mg", "wy")
        self.lx_quality_floor: str = os.getenv("LX_QUALITY_FLOOR", "128k").strip().lower()
        self.lx_download_subdir: str = ""
        self.lx_filename_template: str = os.getenv("DOWNLOAD_FILENAME", "{artist} - {name}").strip() or "{artist} - {name}"
        self.lx_artist_dir: bool = os.getenv("DOWNLOAD_ARTIST_DIR", "true").strip().lower() not in {"0", "false", "no", "off"}
        self.lx_search_limit: int = max(5, min(100, _int("LX_SEARCH_LIMIT", 30)))
        self.lx_match_threshold: float = max(0.5, min(1.0, _float("LX_MATCH_THRESHOLD", 0.76)))

        # 配置库所在目录（**容器内路径**）。
        # 注意变量名不要叫 MONITOR_DATA_DIR —— .env 里那个是「宿主机路径」，
        # 两者同名很容易被误传进容器，导致把宿主路径当成容器路径用。
        self.data_dir: Path = Path(os.getenv("MONITOR_DB_DIR", "/app/data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path: Path = self.data_dir / "monitor.db"
        self.users_dir: Path = Path(os.getenv("USERS_CONFIG_DIR", "/app/users"))
        self.users_dir.mkdir(parents=True, exist_ok=True)

        # HTTP
        self.http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "30"))
        self.user_agent: str = os.getenv(
            "HTTP_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        )

        # 单次运行的安全上限，避免误配置把磁盘写爆。
        # MAX_DOWNLOADS_PER_RUN=0 表示不限 —— 由每个监控自己的「单次最多下载」说了算，
        # 免得两个上限叠在一起、日志里还看不出被哪个卡住。
        self.max_songs_per_playlist: int = max(10, _int("MAX_SONGS_PER_PLAYLIST", 500))
        self.max_downloads_per_run: int = max(0, _int("MAX_DOWNLOADS_PER_RUN", 0))


settings = Settings()


def apply_runtime_overrides(values: dict[str, object]) -> None:
    """应用网页设置，覆盖环境变量默认值；重启后由 SQLite 恢复。"""
    if "download_concurrency" in values:
        settings.download_concurrency = max(1, min(16, int(values["download_concurrency"])))
    if "download_retries" in values:
        settings.download_retries = max(0, min(10, int(values["download_retries"])))
    if "download_batch" in values:
        settings.download_batch = max(1, min(100, int(values["download_batch"])))
    if "download_batch_gap" in values:
        settings.download_batch_gap = max(0.0, min(3600.0, float(values["download_batch_gap"])))
    if "download_timeout" in values:
        settings.download_timeout = max(30.0, min(3600.0, float(values["download_timeout"])))
    if "tick_seconds" in values:
        settings.tick_seconds = max(10, min(3600, int(values["tick_seconds"])))
    if "max_downloads_per_run" in values:
        settings.max_downloads_per_run = max(0, min(10000, int(values["max_downloads_per_run"])))
    if "lx_platform_priority" in values:
        raw = values["lx_platform_priority"]
        items = raw if isinstance(raw, list) else str(raw).split(",")
        settings.lx_platform_priority = tuple(
            item.strip() for item in items if item.strip() in {"tx", "kg", "kw", "mg", "wy"}
        ) or settings.lx_platform_priority
    if "lx_quality_floor" in values:
        settings.lx_quality_floor = str(values["lx_quality_floor"] or "128k").strip().lower()
    if "lx_download_subdir" in values:
        settings.lx_download_subdir = str(values["lx_download_subdir"] or "").strip().strip("/")
    if "lx_filename_template" in values:
        value = str(values["lx_filename_template"] or "").strip()
        settings.lx_filename_template = value[:200] or "{artist} - {name}"
    if "lx_artist_dir" in values:
        value = values["lx_artist_dir"]
        settings.lx_artist_dir = value if isinstance(value, bool) else str(value).strip().lower() not in {"0", "false", "no", "off"}
    if "lx_search_limit" in values:
        settings.lx_search_limit = max(5, min(100, int(values["lx_search_limit"])))
    if "lx_match_threshold" in values:
        settings.lx_match_threshold = max(0.5, min(1.0, float(values["lx_match_threshold"])))
