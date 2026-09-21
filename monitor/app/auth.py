"""music-monitor 用户认证、Session 与当前请求用户上下文。"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import os
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request

SESSION_COOKIE = "music_monitor_session"
SESSION_TTL = 30 * 24 * 60 * 60

_current_user: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "music_monitor_user", default=None
)


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt64, digest64 = stored.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt64)
        expected = base64.urlsafe_b64decode(digest64)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def new_session(db, user_id: str) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + SESSION_TTL
    db.create_session(token_hash(token), user_id, expires)
    return token, expires


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def request_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(SESSION_COOKIE, "")


def set_current_user(user: dict[str, Any] | None):
    return _current_user.set(user)


def reset_current_user(token) -> None:
    _current_user.reset(token)


def current_user() -> dict[str, Any]:
    user = _current_user.get()
    if not user:
        raise HTTPException(401, "未登录或登录已过期")
    return user

def current_user_id() -> str:
    return str(current_user()["id"])


def require_admin() -> dict[str, Any]:
    user = current_user()
    if user.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user
