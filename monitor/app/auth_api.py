"""公开认证接口与管理员用户管理接口。"""
from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    current_user,
    hash_password,
    new_session,
    request_token,
    require_admin,
    token_hash,
    verify_password,
)
from .runtime import credential_store, db

router = APIRouter(prefix="/api/auth")


class CredentialsIn(BaseModel):
    username: str
    password: str
    display_name: str = ""


class UserCreateIn(CredentialsIn):
    role: str = Field(default="user", pattern="^(admin|user)$")


class UserPatchIn(BaseModel):
    display_name: str | None = None
    role: str | None = Field(default=None, pattern="^(admin|user)$")
    enabled: bool | None = None
    password: str | None = None


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


@router.get("/status")
async def status(request: Request) -> dict:
    needs_setup = db.user_count() == 0
    token = request_token(request)
    user = db.session_user(token_hash(token)) if token else None
    return {"needs_setup": needs_setup, "authenticated": bool(user), "user": user}


@router.post("/setup")
async def setup(payload: CredentialsIn, response: Response) -> dict:
    if db.user_count() != 0:
        raise HTTPException(409, "系统已经完成初始化")
    username = payload.username.strip()
    if len(username) < 3:
        raise HTTPException(400, "用户名至少 3 个字符")
    try:
        user = db.create_user(
            username,
            hash_password(payload.password),
            display_name=payload.display_name or username,
            role="admin",
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(400, str(exc)) from exc
    db.assign_orphan_data(user["id"])
    credential_store.user_dir(user["id"])
    token, _ = new_session(db, user["id"])
    _set_session_cookie(response, token)
    return {"ok": True, "user": user}


@router.post("/login")
async def login(payload: CredentialsIn, response: Response) -> dict:
    user = db.get_user_by_username(payload.username, include_secret=True)
    if not user or not user.get("enabled") or not verify_password(payload.password, user.get("password_hash", "")):
        # 固定少量延迟，降低用户名枚举与暴力尝试收益。
        time.sleep(0.15)
        raise HTTPException(401, "用户名或密码不正确")
    public = db.get_user(user["id"])
    token, _ = new_session(db, user["id"])
    _set_session_cookie(response, token)
    return {"ok": True, "user": public}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    token = request_token(request)
    if token:
        db.delete_session(token_hash(token))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me() -> dict:
    return {"user": current_user()}


@router.get("/users")
async def users() -> dict:
    require_admin()
    return {"items": db.list_users()}


@router.post("/users")
async def create_user(payload: UserCreateIn) -> dict:
    require_admin()
    try:
        user = db.create_user(
            payload.username,
            hash_password(payload.password),
            display_name=payload.display_name or payload.username,
            role=payload.role,
        )
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(400, str(exc)) from exc
    credential_store.user_dir(user["id"])
    return {"ok": True, "user": user}


@router.patch("/users/{user_id}")
async def patch_user(user_id: str, payload: UserPatchIn) -> dict:
    admin = require_admin()
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "用户不存在")
    changes = payload.model_dump(exclude_none=True)
    if user_id == admin["id"] and changes.get("enabled") is False:
        raise HTTPException(400, "不能停用当前管理员")
    if "password" in changes:
        changes["password_hash"] = hash_password(changes.pop("password"))
        db.delete_user_sessions(user_id)
    db.update_user(user_id, **changes)
    return {"ok": True, "user": db.get_user(user_id)}
