"""账号中心支持的平台清单。

fixed=True 的平台始终显示为主标签；其它平台仅在配置成功后追加标签，
未配置时统一从“其他平台”入口进入。
"""
from __future__ import annotations

from typing import Any


ACCOUNT_PLATFORMS: dict[str, dict[str, Any]] = {
    "netease": {
        "source": "netease", "label": "网易云音乐", "short": "易", "fixed": True,
        "qr_sources": ["netease"], "cookie": True, "verified_profile": True,
        "cookie_hint": "MUSIC_U=...; __csrf=...",
    },
    "qq": {
        "source": "qq", "label": "QQ 音乐", "short": "Q", "fixed": True,
        "qr_sources": ["qq", "qq_wx"], "cookie": True, "verified_profile": True,
        "cookie_hint": "uin=...; qm_keyst=...; qqmusic_key=...",
    },
    "kugou": {
        "source": "kugou", "label": "酷狗音乐", "short": "K", "fixed": True,
        "qr_sources": ["kugou"], "cookie": True, "verified_profile": True,
        "cookie_hint": "userid=...; token=...; KUGOU_API_MID=...",
    },
    "bilibili": {
        "source": "bilibili", "label": "哔哩哔哩", "short": "B", "fixed": True,
        "qr_sources": ["bilibili"], "cookie": True, "verified_profile": True,
        "cookie_hint": "SESSDATA=...; bili_jct=...; DedeUserID=...",
    },
    "soda": {
        "source": "soda", "label": "汽水音乐", "short": "汽", "fixed": True,
        "qr_sources": ["soda"], "cookie": True, "verified_profile": False,
        "cookie_hint": "sessionid=...; uid_tt=...",
    },
    "kuwo": {
        "source": "kuwo", "label": "酷我音乐", "short": "酷", "fixed": False,
        "qr_sources": [], "cookie": True, "verified_profile": False,
        "cookie_hint": "kw_token=...; userid=...",
    },
    "migu": {
        "source": "migu", "label": "咪咕音乐", "short": "咪", "fixed": False,
        "qr_sources": [], "cookie": True, "verified_profile": False,
        "cookie_hint": "完整粘贴咪咕音乐网页 Cookie",
    },
}

QR_LOGIN_SOURCES = {
    qr_source
    for platform in ACCOUNT_PLATFORMS.values()
    for qr_source in platform["qr_sources"]
}


def public_platforms() -> list[dict[str, Any]]:
    return [dict(item) for item in ACCOUNT_PLATFORMS.values()]
