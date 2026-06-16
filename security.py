"""
JarvisChat - Security utilities.
PIN hashing, audit logging, incident tracking, CSRF/origin checks,
rate limiting, request helpers.
"""
import hashlib
import hmac
import json
import logging
import math
import os
import platform
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import Lock
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from config import (
    ALLOWED_NETWORKS, TRUST_X_FORWARDED_FOR, TRUSTED_ORIGINS,
    BODY_LIMIT_DEFAULT_BYTES, BODY_LIMIT_CHAT_BYTES, BODY_LIMIT_PROFILE_BYTES,
    RATE_WINDOW_SECONDS, RL_LOGIN_PER_WINDOW, RL_CHAT_PER_WINDOW,
    RL_SEARCH_PER_WINDOW, RL_STATS_PER_WINDOW, RL_WRITE_PER_WINDOW,
    RL_DEFAULT_PER_WINDOW, VERSION,
)

import ipaddress

log = logging.getLogger("jarvischat")

SESSIONS: dict = {}
PIN_ATTEMPTS: dict = {}
RATE_EVENTS: dict = defaultdict(deque)
SESSION_LOCK = Lock()
RATE_LOCK = Lock()


def hash_pin(pin: str, salt_hex: Optional[str] = None) -> tuple:
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 200_000)
    return salt.hex(), digest.hex()


def audit_event(event: str, outcome: str, *, ip: str = "unknown", role: str = "none",
                details: str = "", warning: bool = False) -> None:
    payload = {"event": event, "outcome": outcome, "ip": ip, "role": role, "details": details[:300]}
    msg = "AUDIT " + json.dumps(payload, separators=(",", ":"))
    if warning:
        log.warning(msg)
    else:
        log.info(msg)


def create_incident_key() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"INC-{ts}-{uuid.uuid4().hex[:8].upper()}"


def customer_error_envelope(message: str, incident_key: str) -> dict:
    return {
        "detail": message, "error_key": incident_key,
        "error": {"message": message, "incident_key": incident_key,
                  "support_hint": "Share this incident key for exact diagnostics."},
    }


def log_incident(event: str, *, message: str, request: Optional[Request] = None,
                 exc: Optional[Exception] = None) -> str:
    incident_key = create_incident_key()
    payload = {
        "event": event, "incident_key": incident_key, "message": message,
        "app_version": VERSION, "pid": os.getpid(), "python": platform.python_version(),
        "platform": platform.platform(),
        "method": request.method if request else "",
        "path": request.url.path if request else "",
        "client_ip": get_client_ip(request) if request else "",
    }
    if exc:
        log.exception("INCIDENT " + json.dumps(payload, separators=(",", ":")))
    else:
        log.error("INCIDENT " + json.dumps(payload, separators=(",", ":")))
    return incident_key


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if TRUST_X_FORWARDED_FOR and forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def is_ip_allowed(ip: str) -> bool:
    normalized = ip.strip().lower()
    if normalized in {"localhost", "testclient"}:
        normalized = "127.0.0.1"
    try:
        ip_obj = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    for network in ALLOWED_NETWORKS:
        if ip_obj in network:
            return True
    return False


def request_body_limit(path: str) -> int:
    if path in {"/api/chat", "/api/search"}:
        return BODY_LIMIT_CHAT_BYTES
    if path == "/api/profile":
        return BODY_LIMIT_PROFILE_BYTES
    return BODY_LIMIT_DEFAULT_BYTES


def rate_policy(path: str, method: str, ip: str, sid: str) -> tuple:
    identity = sid or ip
    if path == "/api/auth/login":
        return f"login:{ip}", RL_LOGIN_PER_WINDOW
    if path == "/api/chat":
        return f"chat:{identity}", RL_CHAT_PER_WINDOW
    if path == "/api/search":
        return f"search:{identity}", RL_SEARCH_PER_WINDOW
    if path == "/api/stats":
        return f"stats:{ip}", RL_STATS_PER_WINDOW
    if method in {"POST", "PUT", "DELETE", "PATCH"}:
        return f"write:{identity}", RL_WRITE_PER_WINDOW
    return f"api:{identity}", RL_DEFAULT_PER_WINDOW


def check_rate_limit(key: str, limit: int, window_seconds: int) -> tuple:
    now_ts = time.time()
    with RATE_LOCK:
        bucket = RATE_EVENTS[key]
        while bucket and bucket[0] <= (now_ts - window_seconds):
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(math.ceil(window_seconds - (now_ts - bucket[0]))))
            return False, retry_after
        bucket.append(now_ts)
    return True, 0


def origin_allowed(request: Request) -> bool:
    host = request.headers.get("host", "").strip()
    expected_origin = f"{request.url.scheme}://{host}".rstrip("/") if host else ""
    origin = request.headers.get("origin", "").strip().rstrip("/")
    referer = request.headers.get("referer", "").strip()
    if origin:
        return origin == expected_origin or origin in TRUSTED_ORIGINS
    if referer:
        parsed = urlparse(referer)
        ref_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        return ref_origin == expected_origin or ref_origin in TRUSTED_ORIGINS
    return True


def is_state_changing(method: str) -> bool:
    return method in {"POST", "PUT", "DELETE", "PATCH"}


async def read_json_body(request: Request, max_bytes: int) -> dict:
    raw = await request.body()
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail="Request payload too large")
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
