"""兼容旧数据库质量字段的标签归一化。实际取链策略在 lx-gateway。"""
from __future__ import annotations

from typing import Any

QUALITY_LEVELS: dict[str, dict[str, Any]] = {
    "standard": {"label": "标准 (≈128 kbps)"},
    "high": {"label": "较高 (≈320 kbps)"},
    "lossless": {"label": "无损 (FLAC)"},
    "flac24bit": {"label": "FLAC 24bit"},
    "hires": {"label": "Hi-Res"},
    "atmos": {"label": "杜比全景声"},
    "atmos_plus": {"label": "杜比全景声 Plus"},
    "master": {"label": "最高音质（失败自动逐档降级）"},
}


def normalize_quality(name: str | None) -> str:
    value = (name or "").strip().lower()
    return value if value in QUALITY_LEVELS else "master"
