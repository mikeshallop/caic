#!/usr/bin/env python3
"""
JarvisChat - Entry point.
Creates the FastAPI app, registers middleware, mounts all routers.
"""
import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import VERSION, RATE_WINDOW_SECONDS
from db import init_db
from memory import get_memory_count
from security import (
    get_client_ip, is_ip_allowed, check_rate_limit, rate_policy,
    origin_allowed, is_state_changing, request_body_limit,
    audit_event, customer_error_envelope, log_incident,
)
from auth import get_session, is_admin_only, router as auth_router
import routers.conversations as conversations
import routers.memories as memories
import routers.models as models
import routers.presets as presets
import routers.profile as profile
import routers.settings as settings
import routers.skills as skills
import routers.chat as chat
import routers.search_route as search_route

# --- Logging ---
log = logging.getLogger("jarvischat")
log.setLevel(logging.DEBUG)
syslog_handler = logging.handlers.SysLogHandler(address="/dev/log")
syslog_handler.setFormatter(logging.Formatter("jarvischat[%(process)d]: %(levelname)s %(message)s"))
log.addHandler(syslog_handler)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"JarvisChat {VERSION} starting up")
    init_db()
    log.info(f"Memory system: {get_memory_count()} memories loaded")
    yield
    log.info("JarvisChat shutting down")


app = FastAPI(title="JarvisChat", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    incident_key = log_incident("unhandled_exception", message="Unhandled server error", request=request, exc=exc)
    message = "We could not complete that request right now. Use the incident key for support lookup."
    return JSONResponse(status_code=500, content=customer_error_envelope(message, incident_key))


# --- Static files ---
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# --- Middleware ---
@app.middleware("http")
async def session_auth_middleware(request: Request, call_next):
    path = request.url.path
    ip = get_client_ip(request)
    sid = request.headers.get("x-session-id", "").strip()
    request.state.session_role = "none"
    request.state.client_ip = ip

    if path.startswith("/api/"):
        if not is_ip_allowed(ip):
            audit_event("ip_allowlist", "denied", ip=ip, role="none", details=f"{request.method} {path}", warning=True)
            return JSONResponse(status_code=403, content={"detail": "Client IP not allowed"})

    if path.startswith("/api/"):
        rate_key, rate_limit = rate_policy(path, request.method, ip, sid)
        allowed, retry_after = check_rate_limit(rate_key, rate_limit, RATE_WINDOW_SECONDS)
        if not allowed:
            audit_event("rate_limit", "denied", ip=ip, role="none",
                        details=f"{request.method} {path} retry_after={retry_after}", warning=True)
            return JSONResponse(status_code=429, content={"detail": f"Rate limit exceeded. Retry in {retry_after}s."})

        if request.method in {"POST", "PUT", "PATCH"}:
            max_bytes = request_body_limit(path)
            content_length = request.headers.get("content-length", "").strip()
            if content_length.isdigit() and int(content_length) > max_bytes:
                return JSONResponse(status_code=413, content={"detail": "Request payload too large"})

    unauth_paths = {
        "/api/auth/login", "/api/auth/logout", "/api/auth/session",
        "/api/auth/heartbeat", "/api/auth/guest",
    }

    if path.startswith("/api/") and is_state_changing(request.method):
        if not origin_allowed(request):
            audit_event("origin_check", "denied", ip=ip, role="none",
                        details=f"{request.method} {path}", warning=True)
            return JSONResponse(status_code=403, content={"detail": "Origin check failed"})

    if path.startswith("/api/") and path not in unauth_paths:
        session = get_session(sid, ip, touch=True)
        if not session:
            audit_event("auth_required", "denied", ip=ip, role="none",
                        details=f"{request.method} {path}", warning=True)
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        request.state.session_role = session.get("role", "none")
        if session.get("role") != "admin" and is_admin_only(path, request.method):
            audit_event("admin_capability", "denied", ip=ip, role=session.get("role", "none"),
                        details=f"{request.method} {path}", warning=True)
            return JSONResponse(status_code=403, content={"detail": "Admin PIN required for this action"})

    response = await call_next(request)
    if path.startswith("/api/") and is_admin_only(path, request.method):
        role = getattr(request.state, "session_role", "none")
        if response.status_code < 400 and role == "admin":
            audit_event("admin_action", "success", ip=ip, role=role, details=f"{request.method} {path}")
    return response


# --- Index ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"version": VERSION})


# --- Register routers ---
for router_module in [
    auth_router, conversations.router, memories.router, models.router,
    presets.router, profile.router, settings.router, skills.router,
    chat.router, search_route.router,
]:
    app.include_router(router_module)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
