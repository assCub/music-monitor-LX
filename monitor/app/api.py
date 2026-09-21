"""HTTP 接口层。前端是一个零构建的单页应用，直接消费这些 JSON。"""
from __future__ import annotations

import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import __version__, charts, pipeline, quality
from .account_platforms import ACCOUNT_PLATFORMS, QR_LOGIN_SOURCES
from .auth import current_user, current_user_id, require_admin
from .config import apply_runtime_overrides, settings
from .runtime import credential_store, db, discovery, lx_gateway, platform_auth, scheduler

log = logging.getLogger("monitor.api")
router = APIRouter(prefix="/api")

# 浏览器只拿到一次性的代理会话 ID；平台返回的真实扫码 key 保留在服务端，
# 并与发起登录的本地用户绑定，避免不同用户之间串用扫码结果。
_platform_login_sessions: dict[str, dict[str, Any]] = {}
_PLATFORM_LOGIN_TTL = 10 * 60


@asynccontextmanager
async def _catalog_for(user_id: str):
    async with discovery.credentials(credential_store.load(user_id)):
        yield discovery


# --------------------------------------------------------------------------- 模型
class MonitorIn(BaseModel):
    name: str
    kind: str = Field(pattern="^(chart|playlist|favorites|artist)$")
    enabled: bool = True
    sources: list[str] = Field(default_factory=list)
    target: dict[str, Any] = Field(default_factory=dict)
    quality: str = "master"
    fallback: str = "best_effort"
    auto_download: bool = True
    embed: bool = True
    interval_minutes: int = 360
    max_downloads: int = 30
    include_kw: str = ""
    exclude_kw: str = ""


class MonitorPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    sources: list[str] | None = None
    target: dict[str, Any] | None = None
    quality: str | None = None
    fallback: str | None = None
    auto_download: bool | None = None
    embed: bool | None = None
    interval_minutes: int | None = None
    max_downloads: int | None = None
    include_kw: str | None = None
    exclude_kw: str | None = None


class PreviewIn(BaseModel):
    kind: str
    sources: list[str] = Field(default_factory=list)
    target: dict[str, Any] = Field(default_factory=dict)
    include_kw: str = ""
    exclude_kw: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class CookiesIn(BaseModel):
    cookies: dict[str, str]


class RetryIn(BaseModel):
    track_ids: list[int]


class PlatformLoginCheckIn(BaseModel):
    key: str


class PlatformLoginActionIn(BaseModel):
    key: str
    action: str = Field(pattern="^(send_code|validate|up_sms)$")
    code: str = ""


class PlatformCookieIn(BaseModel):
    cookie: str


# --------------------------------------------------------------------------- 总览
@router.get("/health")
async def health() -> dict[str, Any]:
    user = current_user()
    return {
        "version": __version__,
        "lx_gateway": await lx_gateway.healthz(),
        "discovery": {"ok": True, "kind": "direct", "platforms": ["qq", "netease"]},
        "scheduler": {**scheduler.status(), "running_monitors": [
            mid for mid in scheduler.status()["running_monitors"] if db.get_monitor(mid, user["id"]) is not None
        ]},
        "user": user,
        "tracks": db.track_stats(user["id"]),
        "monitors": len(db.list_monitors(user["id"])),
    }


@router.get("/platforms")
async def platforms() -> dict[str, Any]:
    async with _catalog_for(current_user_id()) as catalog:
        items = await catalog.sources()
    return {"items": items, "quality_levels": [
        {"key": k, "label": v["label"]} for k, v in quality.QUALITY_LEVELS.items()
    ]}


# --------------------------------------------------------------------------- 榜单
@router.get("/charts")
async def chart_list() -> dict[str, Any]:
    return {"groups": charts.CHART_GROUPS, "index": charts.chart_index()}


@router.get("/charts/resolve")
async def chart_resolve(key: str = "", platform: str = "", id: str = "", link: str = "", limit: int = 50) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if key:
        found = charts.chart_index().get(key)
        if not found:
            raise HTTPException(404, f"未知榜单：{key}")
        entry = dict(found)
    else:
        entry = {"platform": platform, "id": id, "link": link}
    async with _catalog_for(current_user_id()) as catalog:
        result = await charts.resolve_chart(catalog, entry)
    songs = result["songs"]
    return {
        "ok": result["ok"],
        "message": result["message"],
        "resolved": result["resolved"],
        "count": len(songs),
        "songs": [
            {
                "id": s.get("id"), "source": s.get("source"), "name": s.get("name"), "artist": s.get("artist"),
                "album": s.get("album"), "duration": s.get("duration"), "cover": s.get("cover"),
            }
            for s in songs[:limit]
        ],
    }


@router.post("/charts/verify")
async def chart_verify(payload: dict[str, Any]) -> dict[str, Any]:
    """批量校验榜单可用性。请求体：{"keys": ["netease_hot", ...]} 或 {"entries": [{...}]}"""
    index = charts.chart_index()
    entries: list[dict[str, Any]] = []
    for key in payload.get("keys") or []:
        if key in index:
            entries.append(dict(index[key]))
    entries.extend(payload.get("entries") or [])
    if not entries:
        entries = [dict(c) for c in index.values()]

    out = []
    for entry in entries[:40]:
        async with _catalog_for(current_user_id()) as catalog:
            result = await charts.resolve_chart(catalog, entry)
        out.append({
            "key": entry.get("key", ""),
            "name": entry.get("name") or entry.get("link") or entry.get("id"),
            "platform": entry.get("platform", ""),
            "ok": result["ok"],
            "count": len(result["songs"]),
            "message": result["message"],
            "resolved": result["resolved"],
        })
    return {"items": out}


# --------------------------------------------------------------------------- 歌单 / 收藏夹
@router.get("/platform-login")
async def platform_login_status() -> dict[str, Any]:
    health = await platform_auth.healthz()
    return {
        "ok": bool(health.get("ok")),
        "sources": health.get("sources") or [],
        "configured": credential_store.configured(current_user_id()),
    }


@router.get("/platform-accounts")
async def platform_accounts() -> dict[str, Any]:
    user_id = current_user_id()
    mapping = credential_store.load(user_id)
    methods = db.get_user_setting(user_id, "platform_login_methods", {}) or {}
    items = []
    for source, meta in ACCOUNT_PLATFORMS.items():
        label = meta["label"]
        cookie = mapping.get(source, "")
        profile = {"valid": False, "verified": False, "user_id": "", "nickname": "", "avatar": "", "vip_type": 0}
        error = ""
        if cookie:
            try:
                profile = await discovery.account_info(source, cookie)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        items.append({
            "source": source, "label": label, "configured": bool(cookie),
            "valid": bool(profile.get("valid")), "profile": profile,
            "login_method": methods.get(source, "cookie" if cookie else ""), "error": error,
            "fixed": bool(meta.get("fixed")), "qr_sources": list(meta.get("qr_sources") or []),
            "cookie": bool(meta.get("cookie")), "verified_profile": bool(meta.get("verified_profile")),
            "cookie_hint": meta.get("cookie_hint", ""),
        })
    return {"items": items}


@router.post("/platform-accounts/other-cookies")
async def platform_other_cookies(payload: CookiesIn) -> dict[str, Any]:
    """批量配置没有扫码协议的平台；成功项会在账号中心追加独立标签。"""
    user_id = current_user_id()
    methods = db.get_user_setting(user_id, "platform_login_methods", {}) or {}
    saved: list[str] = []
    errors: dict[str, str] = {}
    for raw_source, raw_cookie in payload.cookies.items():
        source = str(raw_source or "").strip().lower()
        cookie = str(raw_cookie or "").strip()
        meta = ACCOUNT_PLATFORMS.get(source)
        if not meta:
            errors[source or "未知平台"] = "不支持的平台标识"
            continue
        if meta.get("qr_sources"):
            errors[source] = "该平台支持扫码，请使用上方独立标签登录"
            continue
        if not cookie:
            errors[source] = "Cookie 不能为空"
            continue
        try:
            profile = await discovery.account_info(source, cookie)
        except Exception as exc:  # noqa: BLE001
            errors[source] = f"Cookie 校验失败：{exc}"
            continue
        if not profile.get("valid"):
            errors[source] = "Cookie 格式无效或已经过期"
            continue
        credential_store.set_platform(user_id, source, cookie)
        methods[source] = "cookie"
        saved.append(source)
    db.set_user_setting(user_id, "platform_login_methods", methods)
    return {"ok": bool(saved), "saved": saved, "errors": errors}


@router.post("/platform-accounts/{source}/cookie")
async def platform_account_cookie(source: str, payload: PlatformCookieIn) -> dict[str, Any]:
    if source not in ACCOUNT_PLATFORMS or not ACCOUNT_PLATFORMS[source].get("cookie"):
        raise HTTPException(404, "暂不支持该平台")
    cookie = payload.cookie.strip()
    if not cookie:
        raise HTTPException(400, "Cookie 不能为空")
    try:
        profile = await discovery.account_info(source, cookie)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Cookie 校验失败：{exc}") from exc
    if not profile.get("valid"):
        raise HTTPException(400, "Cookie 无效或已经过期")
    user_id = current_user_id()
    credential_store.set_platform(user_id, source, cookie)
    methods = db.get_user_setting(user_id, "platform_login_methods", {}) or {}
    methods[source] = "cookie"
    db.set_user_setting(user_id, "platform_login_methods", methods)
    return {"ok": True, "source": source, "profile": profile}


@router.delete("/platform-accounts/{source}")
async def platform_account_logout(source: str) -> dict[str, Any]:
    if source not in ACCOUNT_PLATFORMS:
        raise HTTPException(404, "暂不支持该平台")
    user_id = current_user_id()
    credential_store.remove_platform(user_id, source)
    methods = db.get_user_setting(user_id, "platform_login_methods", {}) or {}
    methods.pop(source, None)
    db.set_user_setting(user_id, "platform_login_methods", methods)
    return {"ok": True, "source": source}


@router.post("/platform-login/{source}/start")
async def platform_login_start(source: str) -> dict[str, Any]:
    if source not in QR_LOGIN_SOURCES:
        raise HTTPException(404, "暂不支持该平台扫码")
    try:
        result = await platform_auth.start(source)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"生成二维码失败：{exc}") from exc
    platform_key = str(result.get("key") or "").strip()
    if not platform_key:
        raise HTTPException(502, "扫码服务未返回有效会话")
    now = time.monotonic()
    for session_id, session in list(_platform_login_sessions.items()):
        if float(session.get("expires_at") or 0) <= now:
            _platform_login_sessions.pop(session_id, None)
    session_id = secrets.token_urlsafe(24)
    _platform_login_sessions[session_id] = {
        "user_id": current_user_id(), "source": source, "key": platform_key,
        "expires_at": now + _PLATFORM_LOGIN_TTL,
    }
    return {
        "source": source, "key": session_id, "image_url": result.get("image_url", ""),
        "expires_at": result.get("expires_at"),
    }


@router.post("/platform-login/{source}/check")
async def platform_login_check(source: str, payload: PlatformLoginCheckIn) -> dict[str, Any]:
    if source not in QR_LOGIN_SOURCES:
        raise HTTPException(404, "暂不支持该平台扫码")
    session = _platform_login_sessions.get(payload.key)
    if not session or session.get("user_id") != current_user_id() or session.get("source") != source:
        raise HTTPException(404, "扫码会话不存在，请重新生成二维码")
    if float(session.get("expires_at") or 0) <= time.monotonic():
        _platform_login_sessions.pop(payload.key, None)
        return {"status": "expired", "message": "二维码已过期"}
    try:
        result = await platform_auth.check(source, str(session["key"]))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"检查扫码状态失败：{exc}") from exc
    session["extra"] = dict(result.get("extra") or {})
    if result.get("status") in {"success", "expired", "failed"}:
        _platform_login_sessions.pop(payload.key, None)
    return _public_platform_login_result(_save_platform_login_result(source, result))


@router.post("/platform-login/{source}/action")
async def platform_login_action(source: str, payload: PlatformLoginActionIn) -> dict[str, Any]:
    """处理汽水音乐扫码后的短信二次验证。真实平台 key 与验证参数不下发到浏览器。"""
    if source != "soda":
        raise HTTPException(404, "该平台没有额外验证步骤")
    session = _platform_login_sessions.get(payload.key)
    if not session or session.get("user_id") != current_user_id() or session.get("source") != source:
        raise HTTPException(404, "扫码会话不存在，请重新生成二维码")
    if float(session.get("expires_at") or 0) <= time.monotonic():
        _platform_login_sessions.pop(payload.key, None)
        return {"status": "expired", "message": "二维码已过期，请重新扫码"}
    extra = dict(session.get("extra") or {})
    encrypt_uid = str(extra.get("encrypt_uid") or "")
    verify_params = str(extra.get("verify_params") or "")
    if not encrypt_uid:
        raise HTTPException(409, "汽水尚未返回短信验证参数，请稍后重试或重新扫码")
    real_key = str(session["key"])
    if payload.action == "send_code":
        action_key = f"{real_key}|send_code|{encrypt_uid}|{verify_params}"
    elif payload.action == "up_sms":
        action_key = f"{real_key}|up_sms|{encrypt_uid}|{verify_params}"
    else:
        code = payload.code.strip()
        if not code.isdigit() or not 4 <= len(code) <= 8:
            raise HTTPException(400, "请输入 4–8 位数字验证码")
        action_key = f"{real_key}|validate|{encrypt_uid}|{verify_params}|{code}"
    try:
        result = await platform_auth.check(source, action_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"汽水二次验证失败：{exc}") from exc
    new_extra = {**extra, **dict(result.get("extra") or {})}
    if result.get("status") == "failed":
        # 验证码输错后仍保留原会话，允许用户重新发送或输入，不必重新扫码。
        new_extra["need_sms"] = "true"
    result["extra"] = new_extra
    session["extra"] = new_extra
    if result.get("status") in {"success", "expired"}:
        _platform_login_sessions.pop(payload.key, None)
    return _public_platform_login_result(_save_platform_login_result(source, result))


def _save_platform_login_result(source: str, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") == "success":
        cookie = str(result.pop("cookie", "") or "").strip()
        cookies = result.pop("cookies", {}) or {}
        if not cookie and cookies:
            cookie = "; ".join(f"{key}={value}" for key, value in sorted(cookies.items()) if value)
        if not cookie:
            raise HTTPException(502, "扫码成功但平台未返回 Cookie")
        credential_source = "qq" if source in {"qq", "qq_wx"} else source
        user_id = current_user_id()
        credential_store.set_platform(user_id, credential_source, cookie)
        methods = db.get_user_setting(user_id, "platform_login_methods", {}) or {}
        methods[credential_source] = "qr" if source != "qq_wx" else "wechat_qr"
        db.set_user_setting(user_id, "platform_login_methods", methods)
        result["configured"] = credential_store.configured(user_id)
        result["cookie_saved"] = True
    return result


def _public_platform_login_result(result: dict[str, Any]) -> dict[str, Any]:
    """只向浏览器暴露展示所需字段，隐藏真实 token 和平台协议验证参数。"""
    raw_extra = dict(result.get("extra") or {})
    allowed_extra = {
        key: raw_extra[key] for key in (
            "need_sms", "need_sms_code", "mobile", "sms_mode", "can_up_sms",
            "need_user_sms", "up_sms_mobile", "up_sms_content", "retry_time",
        ) if raw_extra.get(key) not in {None, ""}
    }
    status = str(result.get("status") or "waiting")
    if allowed_extra.get("need_sms_code") == "true":
        mobile = str(allowed_extra.get("mobile") or "")
        message = f"验证码已发送{('至 ' + mobile) if mobile else ''}"
    elif allowed_extra.get("need_sms") == "true":
        message = "扫码确认成功，需要完成短信安全验证"
    elif status == "waiting":
        message = "等待扫码"
    elif status == "scanned":
        message = "已扫码，请在手机上确认"
    elif status == "success":
        message = "登录成功，账号凭据已加密保存"
    elif status == "expired":
        message = "二维码已过期，请重新生成"
    else:
        message = str(result.get("message") or "登录失败")
    public = {"source": result.get("source", ""), "status": status, "message": message}
    if allowed_extra:
        public["extra"] = allowed_extra
    for key in ("configured", "cookie_saved"):
        if key in result:
            public[key] = result[key]
    return public


@router.get("/playlists/resolve")
async def playlist_resolve(link: str, limit: int = 60) -> dict[str, Any]:
    if not link.strip():
        raise HTTPException(400, "缺少歌单链接")
    async with _catalog_for(current_user_id()) as catalog:
        playlists = await catalog.search_playlists(link.strip(), None)
        if not playlists:
            return {"ok": False, "message": "未能识别该链接，或该平台不支持歌单解析", "playlists": [], "songs": []}
        first = playlists[0]
        songs = await catalog.playlist_songs(first["id"], first["source"], link=first.get("link") or "")
    return {
        "ok": bool(songs),
        "message": "" if songs else "歌单识别成功但取不到曲目（可能需要登录 Cookie）",
        "playlists": playlists,
        "picked": first,
        "count": len(songs),
        "songs": [
            {
                "id": s.get("id"), "source": s.get("source"), "name": s.get("name"), "artist": s.get("artist"),
                "album": s.get("album"), "duration": s.get("duration"),
            }
            for s in songs[:limit]
        ],
    }


@router.post("/favorites/list")
async def favorites_list(payload: dict[str, Any]) -> dict[str, Any]:
    """列出已登录平台的个人歌单 / 收藏夹，供用户勾选要监控哪些。"""
    sources = payload.get("sources") or []
    user_id = current_user_id()
    try:
        async with _catalog_for(user_id) as catalog:
            playlists = await catalog.user_playlists(sources)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc), "items": []}
    return {
        "ok": bool(playlists),
        "message": "" if playlists else "没有读到个人歌单，请在当前用户设置中配置对应平台 Cookie",
        "items": [
            {**pl, "key": f"{pl['source']}:{pl['id']}"} for pl in playlists
        ],
    }


# --------------------------------------------------------------------------- 监控 CRUD
@router.get("/monitors")
async def monitor_list() -> dict[str, Any]:
    user_id = current_user_id()
    running = [mid for mid in scheduler.status()["running_monitors"] if db.get_monitor(mid, user_id) is not None]
    return {"items": db.list_monitors(user_id), "running": running}


@router.post("/monitors")
async def monitor_create(payload: MonitorIn) -> dict[str, Any]:
    data = payload.model_dump()
    if not data["name"].strip():
        raise HTTPException(400, "监控名称不能为空")
    user_id = current_user_id()
    mid = db.create_monitor(data, user_id)
    return {"id": mid, "monitor": db.get_monitor(mid, user_id)}


@router.get("/monitors/{mid}")
async def monitor_get(mid: int) -> dict[str, Any]:
    user_id = current_user_id()
    mon = db.get_monitor(mid, user_id)
    if mon is None:
        raise HTTPException(404, "监控不存在")
    return {
        "monitor": mon,
        "runs": db.recent_runs(mid, limit=20, user_id=user_id),
        "stats": _monitor_stats(mid),
    }


@router.patch("/monitors/{mid}")
async def monitor_patch(mid: int, payload: MonitorPatch) -> dict[str, Any]:
    user_id = current_user_id()
    if db.get_monitor(mid, user_id) is None:
        raise HTTPException(404, "监控不存在")
    changes = payload.model_dump(exclude_none=True)
    db.update_monitor(mid, changes, user_id)
    if "interval_minutes" in changes:
        db.set_next_run(mid, interval_minutes=int(changes["interval_minutes"]))
    return {"monitor": db.get_monitor(mid, user_id)}


@router.delete("/monitors/{mid}")
async def monitor_delete(mid: int) -> dict[str, Any]:
    db.delete_monitor(mid, current_user_id())
    return {"ok": True}


@router.post("/monitors/{mid}/run")
async def monitor_run(mid: int) -> dict[str, Any]:
    if db.get_monitor(mid, current_user_id()) is None:
        raise HTTPException(404, "监控不存在")
    started = scheduler.spawn(mid)
    return {"ok": True, "started": started, "message": "已开始执行" if started else "该监控正在执行中"}


@router.post("/monitors/{mid}/preview")
async def monitor_preview(mid: int, limit: int = 60) -> dict[str, Any]:
    mon = db.get_monitor(mid, current_user_id())
    if mon is None:
        raise HTTPException(404, "监控不存在")
    async with _catalog_for(current_user_id()) as catalog:
        return await pipeline.preview_monitor(
            catalog, mon, limit=limit, lx_gateway=lx_gateway,
        )


@router.post("/preview")
async def draft_preview(payload: PreviewIn) -> dict[str, Any]:
    """新建监控前先干跑一次，确认榜单/歌单/收藏夹能解析出曲目。"""
    draft = payload.model_dump()
    draft["name"] = "preview"
    draft["fallback"] = "best_effort"
    draft["embed"] = True
    draft["user_id"] = current_user_id()
    async with _catalog_for(current_user_id()) as catalog:
        return await pipeline.preview_monitor(
            catalog, draft, limit=60, lx_gateway=lx_gateway,
        )


@router.get("/monitors/{mid}/runs")
async def monitor_runs(mid: int, limit: int = 30) -> dict[str, Any]:
    if db.get_monitor(mid, current_user_id()) is None:
        raise HTTPException(404, "监控不存在")
    return {"items": db.recent_runs(mid, limit=limit, user_id=current_user_id())}


@router.get("/runs")
async def run_list(limit: int = 50) -> dict[str, Any]:
    return {"items": db.recent_runs(None, limit=limit, user_id=current_user_id())}


# --------------------------------------------------------------------------- 曲目记录
@router.get("/tracks")
async def track_list(monitor_id: int | None = None, status: str | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    return {
        "items": db.list_tracks(
            monitor_id=monitor_id, status=status, limit=limit, offset=offset, user_id=current_user_id()
        ),
        "stats": db.track_stats(current_user_id()),
    }


@router.post("/tracks/retry")
async def track_retry(payload: RetryIn) -> dict[str, Any]:
    results = []
    for tid in payload.track_ids[:20]:
        row = db.one(
            "SELECT t.* FROM tracks t JOIN monitors m ON m.id=t.monitor_id WHERE t.id=? AND m.user_id=?",
            (tid, current_user_id()),
        )
        if row is None:
            results.append({"id": tid, "ok": False, "message": "记录不存在"})
            continue
        track = dict(row)
        mon = db.get_monitor(track["monitor_id"])
        if mon is None:
            results.append({"id": tid, "ok": False, "message": "监控已删除"})
            continue
        song = {
            "id": track["song_id"], "source": track["source"], "name": track["name"],
            "artist": track["artist"], "album": track["album"], "duration": track["duration"],
            "cover": track["cover"], "extra": _loads(track["extra"]),
        }
        try:
            res = await pipeline.redownload(db, mon, song, lx_gateway=lx_gateway)
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "message": str(exc)}
        results.append({"id": tid, **res})
    return {"items": results}


@router.get("/platform-credentials")
async def platform_credentials() -> dict[str, Any]:
    configured = credential_store.configured(current_user_id())
    return {
        "ok": bool(configured),
        "configured": configured,
        "message": "" if configured else "当前用户尚未配置任何平台 Cookie",
    }


@router.post("/platform-credentials")
async def platform_credentials_save(payload: CookiesIn) -> dict[str, Any]:
    user_id = current_user_id()
    credential_store.save(user_id, payload.cookies)
    return {"ok": True, "configured": credential_store.configured(user_id)}


# --------------------------------------------------------------------------- 本服务设置
@router.get("/settings")
async def settings_get() -> dict[str, Any]:
    user = current_user()
    data = db.all_settings()
    # 引擎管理员密码只用于服务端自动重登，不回传给浏览器
    data.pop("engine_password", None)
    data.setdefault("download_concurrency", settings.download_concurrency)
    data.setdefault("download_retries", settings.download_retries)
    data.setdefault("download_batch", settings.download_batch)
    data.setdefault("download_batch_gap", settings.download_batch_gap)
    data.setdefault("download_timeout", settings.download_timeout)
    data.setdefault("lx_platform_priority", list(settings.lx_platform_priority))
    data.setdefault("lx_quality_floor", settings.lx_quality_floor)
    data.setdefault("lx_search_limit", settings.lx_search_limit)
    data.setdefault("lx_match_threshold", settings.lx_match_threshold)
    data.setdefault("lx_download_subdir", db.get_setting("lx_download_subdir", ""))
    data.setdefault("lx_filename_template", settings.lx_filename_template)
    data.setdefault("lx_artist_dir", settings.lx_artist_dir)
    data.setdefault("tick_seconds", settings.tick_seconds)
    data.setdefault("max_downloads_per_run", settings.max_downloads_per_run)
    data.update(db.all_user_settings(user["id"]))
    if user.get("role") != "admin":
        for key in (
            "download_concurrency", "download_retries",
            "download_batch", "download_batch_gap", "download_timeout", "tick_seconds",
            "max_downloads_per_run", "lx_platform_priority", "lx_quality_floor", "lx_search_limit",
            "lx_match_threshold",
        ):
            data.pop(key, None)
    return {"settings": data, "limits": {
        "tick_seconds": settings.tick_seconds,
        "download_concurrency": settings.download_concurrency,
        "download_batch": settings.download_batch,
        "download_batch_gap": settings.download_batch_gap,
        "download_timeout": settings.download_timeout,
        "max_downloads_per_run": settings.max_downloads_per_run,
    }}


@router.post("/settings")
async def settings_post(payload: dict[str, Any]) -> dict[str, Any]:
    user = current_user()
    if "lx_download_subdir" in (payload or {}):
        value = str(payload.get("lx_download_subdir") or "").strip()
        if value.startswith("/") or ".." in value.replace("\\", "/").split("/"):
            raise HTTPException(400, "LX 下载子目录必须是挂载根目录下的相对路径")
    user_keys = {"default_quality", "default_interval_minutes", "default_max_downloads", "default_embed", "default_fallback", "lx_download_subdir", "lx_filename_template", "lx_artist_dir"}
    system_payload: dict[str, Any] = {}
    for key, value in (payload or {}).items():
        if key in user_keys:
            db.set_user_setting(user["id"], key, value)
        elif user.get("role") == "admin":
            db.set_setting(key, value)
            system_payload[key] = value
        else:
            raise HTTPException(403, f"普通用户不能修改系统设置：{key}")
    apply_runtime_overrides(system_payload)
    data = db.all_user_settings(user["id"])
    if user.get("role") == "admin":
        data = {**db.all_settings(), **data}
    data.pop("engine_password", None)
    return {"settings": data}


@router.get("/lx")
async def lx_status() -> dict[str, Any]:
    try:
        health = await lx_gateway.healthz()
        config = await lx_gateway.config()
        sources = await lx_gateway.sources()
        return {
            "enabled": True,
            "backend": "lx",
            "health": health,
            "config": config,
            "sources": sources,
            "priority": list(settings.lx_platform_priority),
            "quality_floor": settings.lx_quality_floor,
            "download_subdir": db.get_setting("lx_download_subdir", ""),
        }
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "backend": "lx", "health": {"ok": False, "detail": str(exc)}, "sources": []}


@router.post("/lx/reload")
async def lx_reload() -> dict[str, Any]:
    require_admin()
    try:
        return await lx_gateway.reload_sources()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"LX 音源重载失败：{exc}") from exc


class LxSourceImportIn(BaseModel):
    url: str = ""
    script: str = ""
    name: str = ""


@router.post("/lx/sources")
async def lx_source_import(payload: LxSourceImportIn) -> dict[str, Any]:
    require_admin()
    try:
        return await lx_gateway.import_source(url=payload.url, script=payload.script, name=payload.name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"LX 音源导入失败：{exc}") from exc


@router.delete("/lx/sources/{source_id}")
async def lx_source_delete(source_id: str) -> dict[str, Any]:
    require_admin()
    try:
        return await lx_gateway.delete_source(source_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"LX 音源删除失败：{exc}") from exc


@router.post("/lx/sources/{source_id}/enable")
async def lx_source_enable(source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    require_admin()
    try:
        return await lx_gateway.set_source_enabled(source_id, bool(payload.get("enabled", True)))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"LX 音源状态修改失败：{exc}") from exc


@router.get("/lx/downloads")
async def lx_downloads(limit: int = 100) -> dict[str, Any]:
    try:
        return {"items": await lx_gateway.downloads(limit=max(1, min(limit, 500)), user_id=current_user_id())}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"读取 LX 下载列表失败：{exc}") from exc


@router.delete("/lx/downloads/completed")
async def lx_downloads_clear() -> dict[str, Any]:
    try:
        return await lx_gateway.clear_completed_downloads(user_id=current_user_id())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"清理 LX 下载列表失败：{exc}") from exc


# --------------------------------------------------------------------------- 工具
def _monitor_stats(mid: int) -> dict[str, int]:
    rows = db.query("SELECT status, COUNT(*) AS c FROM tracks WHERE monitor_id = ? GROUP BY status", (mid,))
    stats = {r["status"]: r["c"] for r in rows}
    stats["total"] = sum(stats.values())
    return stats


def _loads(raw: str) -> dict[str, Any]:
    import json

    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}
