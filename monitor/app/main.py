"""监控服务入口。

    uvicorn app.main:app --host 0.0.0.0 --port 9090
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router
from .auth import request_token, reset_current_user, set_current_user, token_hash
from .auth_api import router as auth_router
from .config import settings
from .runtime import db, discovery, lx_gateway, platform_auth, scheduler, seed_defaults

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("monitor")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_defaults()
    log.info("发现层: direct-discovery；下载后端: lx-gateway")

    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        await discovery.aclose()
        await lx_gateway.aclose()
        await platform_auth.aclose()


app = FastAPI(title="music-monitor", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def authentication_middleware(request, call_next):
    path = request.url.path
    public = path in {"/", "/favicon.ico", "/api/healthz", "/api/auth/status", "/api/auth/setup", "/api/auth/login", "/api/auth/logout"} \
        or path.startswith("/static/")
    context_token = None
    if not public and path.startswith("/api/"):
        if db.user_count() == 0:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "需要先创建管理员", "code": "SETUP_REQUIRED"}, status_code=428)
        raw = request_token(request)
        user = db.session_user(token_hash(raw)) if raw else None
        if not user:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "未登录或登录已过期", "code": "UNAUTHORIZED"}, status_code=401)
        request.state.user = user
        context_token = set_current_user(user)
    try:
        return await call_next(request)
    finally:
        if context_token is not None:
            reset_current_user(context_token)


app.include_router(auth_router)
app.include_router(router)


@app.get("/api/healthz", include_in_schema=False)
async def public_healthz():
    return {"ok": True, "version": __version__}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # 204 按 HTTP 规范不能带 body。之前返回 JSONResponse({}, 204) 会带 2 字节 body，
    # uvicorn 每次都在 send 阶段抛 RuntimeError: Response content longer than Content-Length
    # （浏览器仍收到合法的 204，但那条 keep-alive 连接会被打断，表现像「点了没反应」）。
    return Response(status_code=204)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
