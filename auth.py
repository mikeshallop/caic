"""
JarvisChat - Auth: session management, PIN verification, middleware, auth routes.
"""
import hashlib
import hmac
import logging
import re
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from config import SESSION_TIMEOUT_SECONDS, MAX_PIN_ATTEMPTS, PIN_LOCKOUT_SECONDS, RATE_WINDOW_SECONDS
from db import get_db, get_setting
from security import (
    SESSIONS, PIN_ATTEMPTS, SESSION_LOCK, audit_event, get_client_ip,
    is_ip_allowed, check_rate_limit, rate_policy, origin_allowed,
    is_state_changing, request_body_limit, read_json_body, hash_pin,
    customer_error_envelope, log_incident,
)

log = logging.getLogger("jarvischat")
router = APIRouter()


def verify_admin_pin(pin: str) -> bool:
    if not re.fullmatch(r"\d{4}", pin or ""):
        return False
    db = get_db()
    pin_hash = get_setting(db, "admin_pin_hash", "")
    pin_salt = get_setting(db, "admin_pin_salt", "")
    db.close()
    if not pin_hash or not pin_salt:
        return False
    _, candidate_hash = hash_pin(pin, salt_hex=pin_salt)
    return hmac.compare_digest(candidate_hash, pin_hash)


def is_ip_locked(ip: str) -> tuple:
    now_ts = time.time()
    with SESSION_LOCK:
        state = PIN_ATTEMPTS.get(ip)
        if not state:
            return False, 0
        locked_until = state.get("locked_until", 0)
        if locked_until > now_ts:
            return True, int(locked_until - now_ts)
        if locked_until:
            PIN_ATTEMPTS.pop(ip, None)
    return False, 0


def record_pin_failure(ip: str) -> None:
    now_ts = time.time()
    with SESSION_LOCK:
        state = PIN_ATTEMPTS.get(ip, {"fail_count": 0, "locked_until": 0})
        state["fail_count"] = int(state.get("fail_count", 0)) + 1
        if state["fail_count"] >= MAX_PIN_ATTEMPTS:
            state["locked_until"] = now_ts + PIN_LOCKOUT_SECONDS
            state["fail_count"] = 0
        PIN_ATTEMPTS[ip] = state


def clear_pin_failures(ip: str) -> None:
    with SESSION_LOCK:
        PIN_ATTEMPTS.pop(ip, None)


def cleanup_sessions(now_ts: Optional[float] = None) -> None:
    now_ts = now_ts or time.time()
    with SESSION_LOCK:
        expired = [
            sid for sid, meta in SESSIONS.items()
            if (now_ts - meta.get("last_seen", 0)) > SESSION_TIMEOUT_SECONDS
        ]
        for sid in expired:
            del SESSIONS[sid]


def create_session(ip: str, role: str) -> str:
    now_ts = time.time()
    sid = uuid.uuid4().hex
    with SESSION_LOCK:
        SESSIONS[sid] = {"ip": ip, "role": role, "created_at": now_ts, "last_seen": now_ts}
    return sid


def validate_session(sid: str, ip: str, touch: bool = True) -> bool:
    if not sid:
        return False
    now_ts = time.time()
    cleanup_sessions(now_ts)
    with SESSION_LOCK:
        session = SESSIONS.get(sid)
        if not session or session.get("ip") != ip:
            return False
        if touch:
            session["last_seen"] = now_ts
    return True


def get_session(sid: str, ip: str, touch: bool = True) -> Optional[dict]:
    if not sid:
        return None
    now_ts = time.time()
    cleanup_sessions(now_ts)
    with SESSION_LOCK:
        session = SESSIONS.get(sid)
        if not session or session.get("ip") != ip:
            return None
        if touch:
            session["last_seen"] = now_ts
        return dict(session)


def revoke_session(sid: str) -> None:
    if not sid:
        return
    with SESSION_LOCK:
        SESSIONS.pop(sid, None)


def is_admin_only(path: str, method: str) -> bool:
    if method in {"PUT", "DELETE", "PATCH"}:
        return True
    if method != "POST":
        return False
    guest_allowed_posts = {
        "/api/chat", "/api/search", "/api/show", "/api/auth/login",
        "/api/auth/logout", "/api/auth/session", "/api/auth/heartbeat", "/api/auth/guest",
    }
    return path not in guest_allowed_posts


# --- Auth routes ---

@router.post("/api/auth/guest")
async def auth_guest(request: Request):
    ip = get_client_ip(request)
    sid = create_session(ip, role="guest")
    audit_event("guest_session", "success", ip=ip, role="guest")
    return {"status": "ok", "session_id": sid, "role": "guest", "timeout_seconds": SESSION_TIMEOUT_SECONDS}


@router.post("/api/auth/login")
async def auth_login(request: Request):
    from security import BODY_LIMIT_DEFAULT_BYTES
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    pin = str(body.get("pin", ""))
    ip = get_client_ip(request)
    locked, retry_after = is_ip_locked(ip)
    if locked:
        audit_event("admin_login", "locked", ip=ip, role="none", details=f"retry_after={retry_after}", warning=True)
        raise HTTPException(status_code=429, detail=f"Too many failed PIN attempts. Retry in {retry_after}s.")
    if not verify_admin_pin(pin):
        record_pin_failure(ip)
        audit_event("admin_login", "failed", ip=ip, role="none", warning=True)
        raise HTTPException(status_code=401, detail="Invalid PIN")
    clear_pin_failures(ip)
    sid = create_session(ip, role="admin")
    audit_event("admin_login", "success", ip=ip, role="admin")
    return {"status": "ok", "session_id": sid, "role": "admin", "timeout_seconds": SESSION_TIMEOUT_SECONDS}


@router.get("/api/auth/session")
async def auth_session(request: Request):
    sid = request.headers.get("x-session-id", "").strip()
    ip = get_client_ip(request)
    session = get_session(sid, ip, touch=True)
    return {"authenticated": bool(session), "role": session.get("role") if session else "none"}


@router.post("/api/auth/heartbeat")
async def auth_heartbeat(request: Request):
    sid = request.headers.get("x-session-id", "").strip()
    ip = get_client_ip(request)
    if not sid or not validate_session(sid, ip, touch=True):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"status": "ok"}


@router.post("/api/auth/logout")
async def auth_logout(request: Request):
    from security import BODY_LIMIT_DEFAULT_BYTES
    ip = get_client_ip(request)
    sid = request.headers.get("x-session-id", "").strip()
    role = "none"
    if sid:
        session = get_session(sid, ip, touch=False)
        role = session.get("role", "none") if session else "none"
    if not sid:
        try:
            body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
            sid = str(body.get("session_id", "")).strip()
        except Exception:
            try:
                sid = (await request.body()).decode("utf-8", errors="ignore").strip()
            except Exception:
                sid = ""
    revoke_session(sid)
    audit_event("logout", "success", ip=ip, role=role)
    return {"status": "ok"}
