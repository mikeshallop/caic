#!/usr/bin/env bash
# jarvischat_refactor.sh
# Refactors /opt/jarvischat/app.py into a proper Python package structure.
# Run as root or sudo on jarvis. Safe: backs up app.py first, does not delete originals.
# Usage: bash jarvischat_refactor.sh

set -euo pipefail

APP_DIR="/opt/jarvischat"
BACKUP="${APP_DIR}/app.py.pre-refactor-$(date +%Y%m%d-%H%M%S)"

echo "=== JarvisChat Modular Refactor ==="
echo "Working in: $APP_DIR"

cd "$APP_DIR"

# --- Backup ---
cp app.py "$BACKUP"
echo "Backed up app.py -> $BACKUP"

# --- Create routers directory ---
mkdir -p routers

# =============================================================================
# config.py
# =============================================================================
cat > config.py << 'PYEOF'
"""
JarvisChat - Central configuration.
All constants, environment variables, limits, and skill registry live here.
"""
import os
import re
import ipaddress
import logging

log = logging.getLogger("jarvischat")

VERSION = "v1.8.0"
OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
LLAMA_SERVER_BASE = os.environ.get("LLAMA_SERVER_BASE", "http://192.168.50.108:8081")
SEARXNG_BASE = "http://localhost:8888"
DEFAULT_MODEL = "llama3.1:latest"

# --- Auth ---
SESSION_TIMEOUT_SECONDS = 90
MAX_PIN_ATTEMPTS = 5
PIN_LOCKOUT_SECONDS = 300
ALLOW_DEFAULT_PIN = os.getenv("JARVISCHAT_ALLOW_DEFAULT_PIN", "false").lower() == "true"
TRUSTED_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.getenv("JARVISCHAT_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
}
DEFAULT_ALLOWED_CIDRS = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
ALLOWED_CIDRS_RAW = os.getenv("JARVISCHAT_ALLOWED_CIDRS", DEFAULT_ALLOWED_CIDRS)
TRUST_X_FORWARDED_FOR = (
    os.getenv("JARVISCHAT_TRUST_X_FORWARDED_FOR", "false").lower() == "true"
)

# --- Rate limits ---
RATE_WINDOW_SECONDS = 60
RL_LOGIN_PER_WINDOW = 10
RL_CHAT_PER_WINDOW = 24
RL_SEARCH_PER_WINDOW = 16
RL_WRITE_PER_WINDOW = 30
RL_DEFAULT_PER_WINDOW = 240
RL_STATS_PER_WINDOW = 600

# --- Payload limits ---
BODY_LIMIT_DEFAULT_BYTES = 64 * 1024
BODY_LIMIT_CHAT_BYTES = 128 * 1024
BODY_LIMIT_PROFILE_BYTES = 256 * 1024

MAX_CHAT_MESSAGE_CHARS = 8000
MAX_SEARCH_QUERY_CHARS = 500
MAX_PROFILE_CHARS = 32000
MAX_MEMORY_FACT_CHARS = 2000
MAX_PRESET_NAME_CHARS = 120
MAX_PRESET_PROMPT_CHARS = 12000
MAX_SETTINGS_KEYS = 16
MAX_SETTINGS_VALUE_CHARS = 8000
MAX_CONVERSATION_TITLE_CHARS = 200
MAX_SKILL_KEY_CHARS = 120
MAX_SKILL_PROMPT_CHARS = 1600

ALLOWED_SETTINGS_KEYS = {
    "profile_enabled",
    "default_model",
    "search_enabled",
    "memory_enabled",
    "skills_enabled",
}

# --- Perplexity ---
PERPLEXITY_THRESHOLD = 15.0

# --- Refusal / hedge patterns ---
REFUSAL_PATTERNS = re.compile(
    r"|".join([
        r"i don'?t have (?:real-?time|current|live)",
        r"i (?:can'?t|cannot) provide (?:current|real-?time|live)",
        r"i don'?t have access to (?:current|real-?time|live)",
        r"(?:current|live|real-?time) (?:data|information|prices?|weather)",
        r"my (?:knowledge|training) (?:cutoff|only goes|ends)",
        r"as of my (?:knowledge|training) cutoff",
        r"i'?m not able to (?:access|provide|browse)",
        r"(?:check|visit|use) a (?:website|financial|news)",
        r"as an ai model",
        r"based on my training data",
        r"i don'?t have the capability",
    ]),
    re.IGNORECASE,
)

HEDGE_PATTERNS = [
    r"^I'?m sorry,?\s*but\s*I\s*(?:can'?t|cannot)\s*assist\s*with\s*that[^.]*\.\s*",
    r"^I'?m sorry,?\s*but[^.]*(?:previous|incorrect)[^.]*\.\s*",
    r"(?:But\s+)?[Pp]lease\s+(?:make\s+sure\s+to\s+)?verify\s+(?:the\s+)?(?:data|information|this)\s+(?:from\s+)?(?:reliable\s+)?sources[^.]*\.\s*",
    r"[Pp]lease\s+verify[^.]*(?:accurate|reliability)[^.]*\.\s*",
    r"[Bb]ut\s+please\s+(?:make\s+sure|verify|check)[^.]*\.\s*",
]

# --- Built-in skills registry ---
BUILTIN_SKILLS = [
    {"key": "memory.search",       "name": "Memory Search",        "category": "memory",       "risk": "low",    "description": "Search stored memory facts relevant to the current prompt."},
    {"key": "memory.add",          "name": "Memory Add",           "category": "memory",       "risk": "medium", "description": "Store a new memory fact with topic tagging."},
    {"key": "memory.forget",       "name": "Memory Forget",        "category": "memory",       "risk": "high",   "description": "Delete matching memories when asked to forget information."},
    {"key": "conversation.list",   "name": "Conversation List",    "category": "conversation", "risk": "low",    "description": "List existing conversations with metadata."},
    {"key": "conversation.get",    "name": "Conversation Get",     "category": "conversation", "risk": "low",    "description": "Read a conversation and its message history."},
    {"key": "conversation.delete", "name": "Conversation Delete",  "category": "conversation", "risk": "high",   "description": "Delete a single conversation thread."},
    {"key": "conversation.delete_all", "name": "Conversation Delete All", "category": "conversation", "risk": "high", "description": "Delete all conversations and messages."},
    {"key": "search.web",          "name": "Web Search",           "category": "search",       "risk": "low",    "description": "Run explicit web search and summarize results."},
    {"key": "settings.get",        "name": "Settings Get",         "category": "settings",     "risk": "low",    "description": "Read current runtime settings."},
    {"key": "settings.update",     "name": "Settings Update",      "category": "settings",     "risk": "high",   "description": "Update allowlisted runtime settings keys."},
]

SKILLS_BY_KEY = {s["key"]: s for s in BUILTIN_SKILLS}


def parse_allowed_cidrs(raw: str) -> list:
    networks = []
    for entry in (raw or "").split(","):
        value = entry.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            log.warning(f"Invalid CIDR ignored: {value}")
    return networks


ALLOWED_NETWORKS = parse_allowed_cidrs(ALLOWED_CIDRS_RAW)

DEFAULT_PROFILE = """You are a coding companion running locally on a machine called "jarvis".

## Environment
- jarvis: Debian 13 (trixie) x86_64, AMD Ryzen 5 5600X, 16GB RAM, AMD RX 6600 XT (8GB VRAM)
- ultron: Debian 13, Ryzen 7 7840HS, 16GB RAM, primary AI inference node, IP 192.168.50.108
- Corsair: Windows 11, gaming/streaming rig, RTX 5070 Ti
- pivault: RPi 5, 8GB RAM, Debian 13, 11TB RAID5 NAS at /mnt/pivault, IP 192.168.50.158
- Router: ASUS ROG Rapture GT-BE98 Pro "BigBlinkyRouter" at 192.168.50.1
- llama-server on ultron:8081 (OpenAI-compat API), Qdrant on ultron:6333

## About the User
- Experienced developer, BS in Computer Science (Oklahoma State), coding since 1981 (TRS-80)
- Deep Unix/Linux background — wrote device drivers at SCO during Xenix era (1990s)
- Currently learning Rust, transitioning from decades of PHP
- Building a WW2 mobile game in Godot Engine for Android
- Veteran on fixed income — prefers free/open-source solutions
- Home lab enthusiast with Zigbee, Z-Wave and Tapo smart home devices

## How to Respond
- Be direct and concise — no hand-holding, this user knows what they're doing
- When showing code, prefer complete working examples over snippets
- Default to command-line solutions over GUI when possible
- Consider resource constraints (fixed income, specific hardware limits)
- Use Rust, Python, or bash unless another language is specifically needed
- Explain trade-offs when multiple approaches exist"""

DEFAULT_PRESETS = [
    {"name": "Coding Companion", "prompt": "You are a senior software engineer and coding companion. Focus on writing clean, efficient, well-documented code. Provide complete working examples. Explain architectural decisions and trade-offs. Prefer Rust, Python, and bash."},
    {"name": "Linux Sysadmin",   "prompt": "You are an experienced Linux systems administrator. Focus on command-line solutions, systemd services, networking, storage, and security. Prefer Debian/Ubuntu conventions. Be concise and direct."},
    {"name": "General Assistant","prompt": "You are a helpful general-purpose assistant. Be clear and concise."},
]
PYEOF
echo "  [+] config.py"

# =============================================================================
# db.py
# =============================================================================
cat > db.py << 'PYEOF'
"""
JarvisChat - Database layer.
Schema init, connection factory, settings helpers, skill state management.
"""
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import (
    BUILTIN_SKILLS, DEFAULT_MODEL, DEFAULT_PRESETS, DEFAULT_PROFILE,
    MAX_SKILL_PROMPT_CHARS, ALLOWED_NETWORKS,
)

log = logging.getLogger("jarvischat")

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "jarvischat.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_setting(db, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def list_skills_with_state(db) -> list:
    rows = db.execute("SELECT skill_key, enabled, updated_at FROM skills").fetchall()
    state_by_key = {
        row["skill_key"]: {"enabled": bool(row["enabled"]), "updated_at": row["updated_at"]}
        for row in rows
    }
    merged = []
    for skill in BUILTIN_SKILLS:
        state = state_by_key.get(skill["key"], {"enabled": True, "updated_at": ""})
        merged.append({**skill, "enabled": state["enabled"], "updated_at": state["updated_at"]})
    return sorted(merged, key=lambda s: (s["category"], s["name"]))


def set_skill_enabled(db, skill_key: str, enabled: bool) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT OR REPLACE INTO skills (skill_key, enabled, updated_at) VALUES (?, ?, ?)",
        (skill_key, 1 if enabled else 0, now),
    )


def format_active_skills_prompt(skills: list) -> str:
    lines = [
        "## Active Skills",
        "Use these skills only when needed. Prefer concise answers over unnecessary tool usage.",
    ]
    for skill in skills:
        lines.append(f"- {skill['key']} ({skill['risk']} risk): {skill['description']}")
    text = "\n".join(lines)
    if len(text) > MAX_SKILL_PROMPT_CHARS:
        return text[:MAX_SKILL_PROMPT_CHARS - 3] + "..."
    return text


def init_db():
    from security import hash_pin
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT 'New Chat',
            model TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_presets (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY CHECK (id = 1), content TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            skill_key TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(
            fact, topic, source, created_at UNINDEXED
        )
    """)

    if not conn.execute("SELECT id FROM profile WHERE id = 1").fetchone():
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT INTO profile (id, content, updated_at) VALUES (1, ?, ?)", (DEFAULT_PROFILE, now))

    if conn.execute("SELECT COUNT(*) as c FROM system_presets").fetchone()["c"] == 0:
        now = datetime.now(timezone.utc).isoformat()
        for preset in DEFAULT_PRESETS:
            conn.execute(
                "INSERT INTO system_presets (id, name, prompt, is_default, created_at) VALUES (?, ?, ?, 1, ?)",
                (str(uuid.uuid4()), preset["name"], preset["prompt"], now),
            )

    defaults = {
        "profile_enabled": "true", "default_model": DEFAULT_MODEL,
        "search_enabled": "true", "memory_enabled": "true", "skills_enabled": "true",
    }
    for key, value in defaults.items():
        if not conn.execute("SELECT key FROM settings WHERE key = ?", (key,)).fetchone():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    now = datetime.now(timezone.utc).isoformat()
    for skill in BUILTIN_SKILLS:
        if not conn.execute("SELECT skill_key FROM skills WHERE skill_key = ?", (skill["key"],)).fetchone():
            conn.execute("INSERT INTO skills (skill_key, enabled, updated_at) VALUES (?, 1, ?)", (skill["key"], now))

    existing_pin_hash = conn.execute("SELECT value FROM settings WHERE key = 'admin_pin_hash'").fetchone()
    existing_pin_salt = conn.execute("SELECT value FROM settings WHERE key = 'admin_pin_salt'").fetchone()
    if not existing_pin_hash or not existing_pin_salt:
        from config import ALLOW_DEFAULT_PIN
        configured_pin = os.getenv("JARVISCHAT_ADMIN_PIN", "").strip()
        if re.fullmatch(r"\d{4}", configured_pin):
            seed_pin, pin_source = configured_pin, "env"
        elif ALLOW_DEFAULT_PIN:
            seed_pin, pin_source = "1234", "default"
        else:
            raise RuntimeError(
                "Admin PIN bootstrap blocked: set JARVISCHAT_ADMIN_PIN to a 4-digit PIN "
                "or set JARVISCHAT_ALLOW_DEFAULT_PIN=true."
            )
        salt_hex, pin_hash_hex = hash_pin(seed_pin)
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("admin_pin_hash", pin_hash_hex))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("admin_pin_salt", salt_hex))
        if pin_source == "default":
            log.warning("Admin PIN seeded from insecure default 1234 (override enabled).")
        else:
            log.info("Admin PIN hash seeded from configured environment PIN.")

    conn.commit()
    conn.close()
PYEOF
echo "  [+] db.py"

# =============================================================================
# security.py
# =============================================================================
cat > security.py << 'PYEOF'
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
PYEOF
echo "  [+] security.py"

# =============================================================================
# auth.py
# =============================================================================
cat > auth.py << 'PYEOF'
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
PYEOF
echo "  [+] auth.py"

# =============================================================================
# memory.py
# =============================================================================
cat > memory.py << 'PYEOF'
"""
JarvisChat - FTS5 memory system.
CRUD, search, remember/forget command processing, topic detection.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from db import get_db
from config import MAX_MEMORY_FACT_CHARS

log = logging.getLogger("jarvischat")

REMEMBER_PATTERNS = [
    (r"remember that (.+)", "explicit"),
    (r"please remember (.+)", "explicit"),
    (r"don'?t forget (.+)", "explicit"),
    (r"note that (.+)", "explicit"),
    (r"keep in mind (?:that )?(.+)", "explicit"),
]

FORGET_PATTERNS = [
    r"forget (?:that )?(.+)",
    r"don'?t remember (.+)",
    r"remove (?:the )?memory (?:about |that )?(.+)",
]


def detect_topic(fact: str) -> str:
    fact_lower = fact.lower()
    if any(w in fact_lower for w in ["prefer", "like", "hate", "always", "never", "favorite"]):
        return "preference"
    elif any(w in fact_lower for w in ["working on", "building", "project", "developing"]):
        return "project"
    elif any(w in fact_lower for w in ["run", "install", "server", "ip", "port", "service", "docker", "systemd"]):
        return "infrastructure"
    elif any(w in fact_lower for w in ["my name", "i am", "i'm a", "i live", "my wife", "my partner"]):
        return "personal"
    return "general"


def add_memory(fact: str, topic: str = "general", source: str = "explicit") -> Optional[int]:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "INSERT INTO memories (fact, topic, source, created_at) VALUES (?, ?, ?, ?)",
        (fact, topic, source, now),
    )
    db.commit()
    rowid = cur.lastrowid
    db.close()
    log.info(f"Memory added [{topic}]: {fact[:50]}...")
    return rowid


def search_memories(query: str, limit: int = 5) -> list:
    if not query.strip():
        return []
    db = get_db()
    words = re.findall(r"[A-Za-z0-9_]+", query)
    if not words:
        db.close()
        return []
    safe_query = " OR ".join(word + "*" for word in words[:10])
    try:
        rows = db.execute(
            "SELECT rowid, fact, topic, source, created_at, bm25(memories) AS rank "
            "FROM memories WHERE memories MATCH ? ORDER BY rank LIMIT ?",
            (safe_query, limit),
        ).fetchall()
        results = [dict(row) for row in rows]
        log.debug(f"Memory search '{query}' returned {len(results)} results")
    except Exception as e:
        log.warning(f"Memory search error: {e}")
        results = []
    db.close()
    return results


def get_all_memories(topic: Optional[str] = None) -> list:
    db = get_db()
    if topic:
        rows = db.execute(
            "SELECT rowid, * FROM memories WHERE topic = ? ORDER BY created_at DESC", (topic,)
        ).fetchall()
    else:
        rows = db.execute("SELECT rowid, * FROM memories ORDER BY created_at DESC").fetchall()
    db.close()
    return [dict(row) for row in rows]


def delete_memory(rowid: int) -> bool:
    db = get_db()
    cur = db.execute("DELETE FROM memories WHERE rowid = ?", (rowid,))
    db.commit()
    deleted = cur.rowcount > 0
    db.close()
    if deleted:
        log.info(f"Memory deleted: rowid={rowid}")
    return deleted


def update_memory(rowid: int, fact: str) -> bool:
    db = get_db()
    cur = db.execute("UPDATE memories SET fact = ? WHERE rowid = ?", (fact, rowid))
    db.commit()
    updated = cur.rowcount > 0
    db.close()
    return updated


def get_memory_count() -> int:
    db = get_db()
    count = db.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
    db.close()
    return count


def process_remember_command(user_message: str) -> Optional[str]:
    for pattern, source in REMEMBER_PATTERNS:
        match = re.search(pattern, user_message, re.IGNORECASE)
        if match:
            fact = match.group(1).strip().rstrip(".")
            topic = detect_topic(fact)
            add_memory(fact, topic=topic, source=source)
            return f"✓ Remembered [{topic}]: {fact}"
    for pattern in FORGET_PATTERNS:
        match = re.search(pattern, user_message, re.IGNORECASE)
        if match:
            search_term = match.group(1).strip().rstrip(".")
            memories = search_memories(search_term, limit=3)
            if memories:
                for m in memories:
                    delete_memory(m["rowid"])
                return f"✓ Forgot {len(memories)} memory/memories about: {search_term}"
            else:
                return f"✗ No memories found about: {search_term}"
    return None
PYEOF
echo "  [+] memory.py"

# =============================================================================
# search.py
# =============================================================================
cat > search.py << 'PYEOF'
"""
JarvisChat - SearXNG integration, perplexity scoring, refusal/hedge detection.
"""
import logging
import math
import re
from urllib.parse import urlparse

import httpx

from config import SEARXNG_BASE, PERPLEXITY_THRESHOLD, REFUSAL_PATTERNS, HEDGE_PATTERNS

log = logging.getLogger("jarvischat")


def sanitize_outbound_url(url: str) -> str:
    if not url:
        return ""
    candidate = url.strip()
    parsed = urlparse(candidate)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return candidate
    return ""


def calculate_perplexity(logprobs: list) -> float:
    if not logprobs:
        return 0.0
    avg_logprob = sum(lp["logprob"] for lp in logprobs) / len(logprobs)
    return math.exp(-avg_logprob)


def is_uncertain(logprobs: list, threshold: float = PERPLEXITY_THRESHOLD) -> bool:
    if not logprobs:
        return False
    perplexity = calculate_perplexity(logprobs)
    log.info(f"Perplexity: {perplexity:.2f} (threshold: {threshold})")
    return perplexity > threshold


def is_refusal(text: str) -> bool:
    match = REFUSAL_PATTERNS.search(text)
    if match:
        log.info(f"Refusal detected: '{match.group()}'")
        return True
    return False


def clean_hedging(text: str) -> str:
    cleaned = text
    for pattern in HEDGE_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def format_search_results(results: list) -> str:
    if not results:
        return ""
    lines = ["[LIVE WEB DATA]\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        if r["content"]:
            lines.append(f"   {r['content']}")
        lines.append("")
    lines.append("\nAnswer directly using the data above. No apologies. No disclaimers. Just answer.")
    return "\n".join(lines)


def format_direct_answer(question: str, results: list) -> str:
    if not results:
        return "No search results found."
    lines = ["Here's what I found:\n"]
    for r in results[:3]:
        lines.append(f"**{r['title']}**")
        if r["content"]:
            lines.append(f"{r['content']}")
        lines.append("")
    return "\n".join(lines).strip()


def extract_search_query(user_message: str) -> str:
    query = user_message.strip()
    if re.search(r"temperature|weather", query, re.IGNORECASE):
        query = re.sub(r"^what('?s| is) the ", "", query, flags=re.IGNORECASE) + " right now degrees"
    if re.search(r"price|spot price", query, re.IGNORECASE):
        query = re.sub(r"^(what('?s| is)|can you tell me) the ", "", query, flags=re.IGNORECASE) + " today USD"
    query = re.sub(
        r"^(what|who|where|when|why|how|is|are|can|could|would|should|do|does|did)\s+",
        "", query, flags=re.IGNORECASE,
    )
    query = re.sub(r"[?!.]+$", "", query)
    return query[:100].strip() or user_message[:100]


async def query_searxng(query: str, max_results: int = 5) -> list:
    log.info(f"Querying SearXNG: '{query}'")
    async with httpx.AsyncClient() as client:
        weather_match = re.search(
            r"(?:weather|temperature|forecast)\s+(?:in\s+)?(.+?)(?:\s+right now|\s+today|\s+degrees)?$",
            query, re.IGNORECASE,
        )
        if weather_match or "weather" in query.lower() or "temperature" in query.lower():
            location = (
                weather_match.group(1) if weather_match
                else re.sub(r"(weather|temperature|forecast|right now|today|degrees)", "", query, flags=re.IGNORECASE).strip()
            )
            if location:
                try:
                    resp = await client.get(f"https://wttr.in/{location}?format=3", timeout=10.0,
                                            headers={"User-Agent": "curl/7.68.0"})
                    if resp.status_code == 200:
                        return [{"title": "Current Weather",
                                 "url": sanitize_outbound_url(f"https://wttr.in/{location}"),
                                 "content": resp.text.strip()}]
                except Exception as e:
                    log.warning(f"wttr.in error: {e}")

        try:
            resp = await client.get(
                f"{SEARXNG_BASE}/search",
                params={"q": query, "format": "json", "categories": "general"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for answer in data.get("answers", []):
                    results.append({"title": "Direct Answer", "url": "", "content": answer})
                for box in data.get("infoboxes", []):
                    content = box.get("content", "")
                    if not content and box.get("attributes"):
                        content = " | ".join([f"{a.get('label','')}: {a.get('value','')}" for a in box["attributes"]])
                    results.append({
                        "title": box.get("infobox", "Info"),
                        "url": sanitize_outbound_url(box.get("urls", [{}])[0].get("url", "") if box.get("urls") else ""),
                        "content": content,
                    })
                for r in data.get("results", [])[:max_results]:
                    results.append({"title": r.get("title", ""), "url": sanitize_outbound_url(r.get("url", "")), "content": r.get("content", "")})
                log.info(f"SearXNG returned {len(results)} results")
                return results
        except Exception as e:
            log.error(f"SearXNG error: {e}")
    return []
PYEOF
echo "  [+] search.py"

# =============================================================================
# gpu.py
# =============================================================================
cat > gpu.py << 'PYEOF'
"""
JarvisChat - AMD GPU stats via rocm-smi.
"""
import json
import logging
import subprocess

log = logging.getLogger("jarvischat")


def get_gpu_stats() -> dict:
    try:
        result = subprocess.run(
            ["rocm-smi", "--showuse", "--showmemuse", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            gpu_info = data.get("card0", {})
            gpu_use = gpu_info.get("GPU use (%)", 0)
            vram_use = gpu_info.get("GPU Memory Allocated (VRAM%)", 0)
            if isinstance(gpu_use, str):
                gpu_use = int(gpu_use.replace("%", "").strip() or 0)
            if isinstance(vram_use, str):
                vram_use = int(vram_use.replace("%", "").strip() or 0)
            return {"gpu_percent": gpu_use, "vram_percent": vram_use, "available": True}
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception as e:
        log.warning(f"GPU stats error: {e}")
    return {"gpu_percent": 0, "vram_percent": 0, "available": False}
PYEOF
echo "  [+] gpu.py"

# =============================================================================
# rag.py
# =============================================================================
cat > rag.py << 'PYEOF'
"""
JarvisChat - RAG pipeline: Qdrant vector search + system prompt assembly.
"""
import logging

import httpx

from db import get_db, get_setting, list_skills_with_state, format_active_skills_prompt
from memory import search_memories
from config import MAX_SKILL_PROMPT_CHARS

log = logging.getLogger("jarvischat")

QDRANT_URL = "http://192.168.50.108:6333"
EMBED_URL = "http://192.168.50.108:11434"
EMBED_MODEL = "mxbai-embed-large"
RAG_COLLECTION = "jarvis_rag"
RAG_SCORE_THRESHOLD = 0.25


async def query_rag(query: str, limit: int = 3) -> list:
    try:
        async with httpx.AsyncClient() as client:
            embed_resp = await client.post(
                f"{EMBED_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": query},
                timeout=10.0,
            )
            if embed_resp.status_code != 200:
                return []
            vector = embed_resp.json()["embedding"]
            search_resp = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/search",
                json={"vector": vector, "limit": limit, "with_payload": True},
                timeout=10.0,
            )
            if search_resp.status_code != 200:
                return []
            return search_resp.json().get("result", [])
    except Exception as e:
        log.warning(f"RAG query error: {e}")
        return []


async def build_system_prompt(db, extra_prompt: str = "", user_message: str = "") -> str:
    parts = []
    settings = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings").fetchall()}

    if settings.get("profile_enabled", "true") == "true":
        profile = db.execute("SELECT content FROM profile WHERE id = 1").fetchone()
        if profile and profile["content"].strip():
            parts.append(profile["content"].strip())

    if settings.get("memory_enabled", "true") == "true" and user_message:
        memories = search_memories(user_message, limit=5)
        if memories:
            memory_lines = [f"- {m['fact']}" for m in memories]
            parts.append("## Relevant Context from Memory\n" + "\n".join(memory_lines))
            log.debug(f"Injected {len(memories)} memories into context")

    if user_message:
        try:
            rag_results = await query_rag(user_message)
            if rag_results:
                rag_lines = [r["payload"]["text"] for r in rag_results if r["score"] > RAG_SCORE_THRESHOLD]
                if rag_lines:
                    parts.append("## Retrieved Context\n" + "\n\n---\n\n".join(rag_lines))
                    log.warning(f"RAG injected {len(rag_lines)} chunks into context")
        except Exception as e:
            log.warning(f"RAG injection error: {e}")

    if settings.get("skills_enabled", "true") == "true":
        active_skills = [s for s in list_skills_with_state(db) if s["enabled"]]
        if active_skills:
            parts.append(format_active_skills_prompt(active_skills))

    if extra_prompt and extra_prompt.strip():
        parts.append(extra_prompt.strip())

    return "\n\n---\n\n".join(parts) if parts else ""
PYEOF
echo "  [+] rag.py"

# =============================================================================
# routers/__init__.py
# =============================================================================
cat > routers/__init__.py << 'PYEOF'
PYEOF
echo "  [+] routers/__init__.py"

# =============================================================================
# routers/models.py
# =============================================================================
cat > routers/models.py << 'PYEOF'
"""
JarvisChat routers - Model listing, system stats.
"""
import logging
from typing import Optional

import httpx
import psutil
from fastapi import APIRouter, HTTPException, Request

from config import OLLAMA_BASE
from gpu import get_gpu_stats
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES

log = logging.getLogger("jarvischat")
router = APIRouter()


@router.get("/api/models")
async def list_models():
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{OLLAMA_BASE}/v1/models", timeout=10)
            data = resp.json()
            models = [{"name": m["id"], "model": m["id"]} for m in data.get("data", [])]
            return {"models": models}
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to llama-server.")


@router.get("/api/ps")
async def running_models():
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{OLLAMA_BASE}/api/ps", timeout=10)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to Ollama.")


@router.post("/api/show")
async def show_model(request: Request):
    from security import BODY_LIMIT_DEFAULT_BYTES
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{OLLAMA_BASE}/api/show", json=body, timeout=10)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to Ollama.")


@router.get("/api/stats")
async def system_stats():
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    gpu = get_gpu_stats()
    return {
        "cpu_percent": round(cpu_percent, 1),
        "memory_percent": round(memory.percent, 1),
        "memory_used_gb": round(memory.used / (1024**3), 1),
        "memory_total_gb": round(memory.total / (1024**3), 1),
        "gpu_percent": gpu["gpu_percent"],
        "vram_percent": gpu["vram_percent"],
        "gpu_available": gpu["available"],
    }


@router.get("/api/search/status")
async def search_status():
    from config import SEARXNG_BASE
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{SEARXNG_BASE}/search",
                                    params={"q": "test", "format": "json"}, timeout=5)
            return {"available": resp.status_code == 200}
        except Exception:
            return {"available": False}
PYEOF
echo "  [+] routers/models.py"

# =============================================================================
# routers/memories.py
# =============================================================================
cat > routers/memories.py << 'PYEOF'
"""JarvisChat routers - Memory CRUD API."""
from fastapi import APIRouter, HTTPException, Request
from typing import Optional

from db import get_db
from memory import add_memory, delete_memory, update_memory, get_all_memories, search_memories
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES
from config import MAX_MEMORY_FACT_CHARS

router = APIRouter()


@router.get("/api/memories")
async def list_memories(topic: Optional[str] = None):
    memories = get_all_memories(topic)
    return {"memories": memories, "count": len(memories)}


@router.post("/api/memories")
async def create_memory(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    fact = str(body.get("fact", "")).strip()
    if not fact:
        raise HTTPException(status_code=400, detail="Memory fact is required")
    if len(fact) > MAX_MEMORY_FACT_CHARS:
        raise HTTPException(status_code=413, detail="Memory fact is too long")
    rowid = add_memory(fact=fact, topic=body.get("topic", "general"), source=body.get("source", "manual"))
    return {"rowid": rowid, "status": "ok"}


@router.delete("/api/memories/{rowid}")
async def remove_memory(rowid: int):
    if not delete_memory(rowid):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "ok"}


@router.put("/api/memories/{rowid}")
async def edit_memory(rowid: int, request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    fact = str(body.get("fact", "")).strip()
    if not fact:
        raise HTTPException(status_code=400, detail="Memory fact is required")
    if len(fact) > MAX_MEMORY_FACT_CHARS:
        raise HTTPException(status_code=413, detail="Memory fact is too long")
    if not update_memory(rowid, fact):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "ok"}


@router.get("/api/memories/search")
async def search_memories_api(q: str, limit: int = 10):
    results = search_memories(q, limit=limit)
    return {"results": results, "count": len(results)}


@router.get("/api/memories/stats")
async def memory_stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
    topics = db.execute("SELECT topic, COUNT(*) as c FROM memories GROUP BY topic ORDER BY c DESC").fetchall()
    db.close()
    return {"total": total, "by_topic": {row["topic"]: row["c"] for row in topics}}
PYEOF
echo "  [+] routers/memories.py"

# =============================================================================
# routers/profile.py
# =============================================================================
cat > routers/profile.py << 'PYEOF'
"""JarvisChat routers - Profile."""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from db import get_db
from security import read_json_body, BODY_LIMIT_PROFILE_BYTES
from config import MAX_PROFILE_CHARS, DEFAULT_PROFILE

router = APIRouter()


@router.get("/api/profile")
async def get_profile():
    db = get_db()
    row = db.execute("SELECT content, updated_at FROM profile WHERE id = 1").fetchone()
    db.close()
    return ({"content": row["content"], "updated_at": row["updated_at"]} if row
            else {"content": "", "updated_at": ""})


@router.put("/api/profile")
async def update_profile(request: Request):
    body = await read_json_body(request, BODY_LIMIT_PROFILE_BYTES)
    content = str(body.get("content", ""))
    if len(content) > MAX_PROFILE_CHARS:
        raise HTTPException(status_code=413, detail="Profile content is too long")
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute("UPDATE profile SET content = ?, updated_at = ? WHERE id = 1", (content, now))
    db.commit()
    db.close()
    return {"status": "ok", "updated_at": now}


@router.get("/api/profile/default")
async def get_default_profile():
    return {"content": DEFAULT_PROFILE}
PYEOF
echo "  [+] routers/profile.py"

# =============================================================================
# routers/settings.py
# =============================================================================
cat > routers/settings.py << 'PYEOF'
"""JarvisChat routers - Settings."""
from fastapi import APIRouter, HTTPException, Request
from db import get_db
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES
from config import MAX_SETTINGS_KEYS, MAX_SETTINGS_VALUE_CHARS, ALLOWED_SETTINGS_KEYS

router = APIRouter()


@router.get("/api/settings")
async def get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    db.close()
    return {row["key"]: row["value"] for row in rows}


@router.put("/api/settings")
async def update_settings(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Settings payload must be an object")
    if len(body) > MAX_SETTINGS_KEYS:
        raise HTTPException(status_code=413, detail="Too many settings in one request")
    unknown_keys = sorted(key for key in body.keys() if str(key) not in ALLOWED_SETTINGS_KEYS)
    if unknown_keys:
        raise HTTPException(status_code=400, detail=f"Unknown setting key(s): {', '.join(unknown_keys)}")
    db = get_db()
    for key, value in body.items():
        if len(str(key)) > 80 or len(str(value)) > MAX_SETTINGS_VALUE_CHARS:
            db.close()
            raise HTTPException(status_code=413, detail="Setting key/value too long")
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    db.commit()
    db.close()
    return {"status": "ok"}
PYEOF
echo "  [+] routers/settings.py"

# =============================================================================
# routers/skills.py
# =============================================================================
cat > routers/skills.py << 'PYEOF'
"""JarvisChat routers - Skills."""
from fastapi import APIRouter, HTTPException, Request
from db import get_db, get_setting, list_skills_with_state, set_skill_enabled
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES
from config import MAX_SKILL_KEY_CHARS, SKILLS_BY_KEY

router = APIRouter()


@router.get("/api/skills")
async def list_skills():
    db = get_db()
    skills = list_skills_with_state(db)
    db.close()
    return {"skills": skills, "count": len(skills)}


@router.get("/api/skills/active")
async def list_active_skills():
    db = get_db()
    skills_enabled = get_setting(db, "skills_enabled", "true") == "true"
    skills = list_skills_with_state(db)
    db.close()
    active = [s for s in skills if s["enabled"]] if skills_enabled else []
    return {"skills": active, "count": len(active), "skills_enabled": skills_enabled}


@router.put("/api/skills/{skill_key}")
async def update_skill(skill_key: str, request: Request):
    skill_key = skill_key.strip()
    if len(skill_key) > MAX_SKILL_KEY_CHARS or skill_key not in SKILLS_BY_KEY:
        raise HTTPException(status_code=404, detail="Unknown skill")
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    if "enabled" not in body or not isinstance(body.get("enabled"), bool):
        raise HTTPException(status_code=400, detail="Field 'enabled' (boolean) is required")
    db = get_db()
    set_skill_enabled(db, skill_key, bool(body["enabled"]))
    db.commit()
    skills = list_skills_with_state(db)
    db.close()
    updated = next((s for s in skills if s["key"] == skill_key), None)
    return {"status": "ok", "skill": updated}
PYEOF
echo "  [+] routers/skills.py"

# =============================================================================
# routers/presets.py
# =============================================================================
cat > routers/presets.py << 'PYEOF'
"""JarvisChat routers - System prompt presets."""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from db import get_db
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES
from config import MAX_PRESET_NAME_CHARS, MAX_PRESET_PROMPT_CHARS

router = APIRouter()


@router.get("/api/presets")
async def list_presets():
    db = get_db()
    rows = db.execute("SELECT * FROM system_presets ORDER BY is_default DESC, name ASC").fetchall()
    db.close()
    return [dict(r) for r in rows]


@router.post("/api/presets")
async def create_preset(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    name = str(body.get("name", "")).strip()
    prompt = str(body.get("prompt", "")).strip()
    if not name or not prompt:
        raise HTTPException(status_code=400, detail="Preset name and prompt are required")
    if len(name) > MAX_PRESET_NAME_CHARS or len(prompt) > MAX_PRESET_PROMPT_CHARS:
        raise HTTPException(status_code=413, detail="Preset fields are too long")
    preset_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute("INSERT INTO system_presets (id, name, prompt, is_default, created_at) VALUES (?, ?, ?, 0, ?)",
               (preset_id, name, prompt, now))
    db.commit()
    db.close()
    return {"id": preset_id, "name": name, "prompt": prompt}


@router.put("/api/presets/{preset_id}")
async def update_preset(preset_id: str, request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    name = str(body.get("name", "")).strip()
    prompt = str(body.get("prompt", "")).strip()
    if not name or not prompt:
        raise HTTPException(status_code=400, detail="Preset name and prompt are required")
    if len(name) > MAX_PRESET_NAME_CHARS or len(prompt) > MAX_PRESET_PROMPT_CHARS:
        raise HTTPException(status_code=413, detail="Preset fields are too long")
    db = get_db()
    db.execute("UPDATE system_presets SET name = ?, prompt = ? WHERE id = ?", (name, prompt, preset_id))
    db.commit()
    db.close()
    return {"status": "ok"}


@router.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str):
    db = get_db()
    db.execute("DELETE FROM system_presets WHERE id = ? AND is_default = 0", (preset_id,))
    db.commit()
    db.close()
    return {"status": "ok"}
PYEOF
echo "  [+] routers/presets.py"

# =============================================================================
# routers/conversations.py
# =============================================================================
cat > routers/conversations.py << 'PYEOF'
"""JarvisChat routers - Conversation CRUD."""
import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from db import get_db
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES
from config import DEFAULT_MODEL, MAX_CONVERSATION_TITLE_CHARS

log = logging.getLogger("jarvischat")
router = APIRouter()


@router.get("/api/conversations")
async def list_conversations():
    db = get_db()
    rows = db.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
    db.close()
    return [dict(r) for r in rows]


@router.post("/api/conversations")
async def create_conversation(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    model = body.get("model", DEFAULT_MODEL)
    title = str(body.get("title", "New Chat"))[:MAX_CONVERSATION_TITLE_CHARS]
    db = get_db()
    db.execute("INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
               (conv_id, title, model, now, now))
    db.commit()
    db.close()
    return {"id": conv_id, "title": title, "model": model, "created_at": now, "updated_at": now}


@router.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    db = get_db()
    conv = db.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if not conv:
        db.close()
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = db.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", (conv_id,)).fetchall()
    db.close()
    return {"conversation": dict(conv), "messages": [dict(m) for m in messages]}


@router.put("/api/conversations/{conv_id}")
async def update_conversation(conv_id: str, request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    if "title" in body:
        db.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                   (str(body["title"])[:MAX_CONVERSATION_TITLE_CHARS], now, conv_id))
    if "model" in body:
        db.execute("UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
                   (body["model"], now, conv_id))
    db.commit()
    db.close()
    return {"status": "ok"}


@router.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    db = get_db()
    db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    db.commit()
    db.close()
    return {"status": "ok"}


@router.delete("/api/conversations")
async def delete_all_conversations():
    db = get_db()
    db.execute("DELETE FROM messages")
    db.execute("DELETE FROM conversations")
    db.commit()
    db.close()
    log.info("Deleted all conversations")
    return {"status": "ok"}
PYEOF
echo "  [+] routers/conversations.py"

# =============================================================================
# routers/chat.py
# =============================================================================
cat > routers/chat.py << 'PYEOF'
"""JarvisChat routers - /api/chat streaming endpoint."""
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from config import DEFAULT_MODEL, LLAMA_SERVER_BASE
from db import get_db
from memory import process_remember_command
from rag import build_system_prompt
from search import (calculate_perplexity, is_uncertain, is_refusal,
                    clean_hedging, format_search_results, format_direct_answer,
                    extract_search_query, query_searxng)
from security import read_json_body, log_incident, BODY_LIMIT_CHAT_BYTES
from config import MAX_CHAT_MESSAGE_CHARS

log = logging.getLogger("jarvischat")
router = APIRouter()


def parse_llama_stream_chunk(line: str) -> tuple:
    if line.startswith("data: "):
        line = line[6:]
    if line.strip() == "[DONE]":
        return None, True, {}
    try:
        chunk = json.loads(line)
        choices = chunk.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            token = delta.get("content")
            finish = choices[0].get("finish_reason")
            stats = {}
            if finish == "stop":
                usage = chunk.get("usage", {})
                stats["tokens_per_sec"] = usage.get("tokens_per_second", 0.0)
            return token, finish == "stop", stats
        if "message" in chunk and "content" in chunk["message"]:
            token = chunk["message"]["content"]
            done = chunk.get("done", False)
            stats = {}
            if done:
                eval_count = chunk.get("eval_count", 0)
                eval_duration = chunk.get("eval_duration", 0)
                stats["tokens_per_sec"] = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0
            return token, done, stats
    except json.JSONDecodeError:
        pass
    return None, False, {}


@router.post("/api/chat")
async def chat(request: Request):
    body = await read_json_body(request, BODY_LIMIT_CHAT_BYTES)
    conv_id = body.get("conversation_id")
    user_message = body.get("message", "").strip()
    if len(user_message) > MAX_CHAT_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail="Chat message is too long")
    model = body.get("model", DEFAULT_MODEL)
    preset_prompt = body.get("system_prompt", "")

    if not user_message:
        raise HTTPException(status_code=400, detail="Empty message")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    settings = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings").fetchall()}
    search_enabled = settings.get("search_enabled", "true") == "true"

    remember_response = process_remember_command(user_message)

    if not conv_id:
        conv_id = str(uuid.uuid4())
        title = user_message[:80] + ("..." if len(user_message) > 80 else "")
        db.execute("INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                   (conv_id, title, model, now, now))
    else:
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))

    db.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
               (conv_id, "user", user_message, now))
    db.commit()

    history_rows = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC", (conv_id,)
    ).fetchall()
    system_prompt = await build_system_prompt(db, preset_prompt, user_message)
    db.close()

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for row in history_rows:
        messages.append({"role": row["role"], "content": row["content"]})

    ollama_payload = {"model": model, "messages": messages, "stream": True}

    async def stream_response():
        full_response = []
        all_logprobs = []
        tokens_per_sec = 0.0

        if remember_response:
            yield f"data: {json.dumps({'token': remember_response + chr(10) + chr(10), 'conversation_id': conv_id})}\n\n"

        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "POST", f"{LLAMA_SERVER_BASE}/v1/chat/completions",
                    json=ollama_payload,
                    timeout=httpx.Timeout(300.0, connect=10.0),
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            token, done, stats = parse_llama_stream_chunk(line)
                            if token:
                                full_response.append(token)
                                yield f"data: {json.dumps({'token': token, 'conversation_id': conv_id})}\n\n"
                            if done:
                                tokens_per_sec = stats.get("tokens_per_sec", 0.0)

                assistant_msg = "".join(full_response)
                perplexity = calculate_perplexity(all_logprobs) if all_logprobs else 0.0
                should_search = is_uncertain(all_logprobs) or is_refusal(assistant_msg)

                if search_enabled and should_search:
                    yield f"data: {json.dumps({'searching': True, 'conversation_id': conv_id})}\n\n"
                    search_query = extract_search_query(user_message)
                    search_results = await query_searxng(search_query)

                    if search_results:
                        search_context = format_search_results(search_results)
                        augmented_messages = []
                        if system_prompt:
                            augmented_messages.append({"role": "system", "content": system_prompt + "\n\n" + search_context})
                        else:
                            augmented_messages.append({"role": "system", "content": search_context})
                        for row in history_rows[:-1]:
                            augmented_messages.append({"role": row["role"], "content": row["content"]})
                        augmented_messages.append({"role": "user", "content": user_message})

                        yield f"data: {json.dumps({'search_results': len(search_results), 'conversation_id': conv_id})}\n\n"

                        augmented_response = []
                        async with client.stream(
                            "POST", f"{LLAMA_SERVER_BASE}/v1/chat/completions",
                            json={"model": model, "messages": augmented_messages, "stream": True},
                            timeout=httpx.Timeout(300.0, connect=10.0),
                        ) as resp2:
                            async for line in resp2.aiter_lines():
                                if line.strip():
                                    token2, done2, _ = parse_llama_stream_chunk(line)
                                    if token2:
                                        augmented_response.append(token2)
                                    if done2:
                                        break

                        raw_response = "".join(augmented_response) or assistant_msg
                        cleaned_response = clean_hedging(raw_response)
                        if is_refusal(cleaned_response) or len(cleaned_response) < 20:
                            cleaned_response = format_direct_answer(user_message, search_results)

                        yield f"data: {json.dumps({'token': cleaned_response, 'conversation_id': conv_id, 'augmented': True})}\n\n"

                        saved_msg = cleaned_response + "\n\n---\n*🔍 Enhanced with web search results*"
                        if remember_response:
                            saved_msg = remember_response + "\n\n" + saved_msg

                        db2 = get_db()
                        db2.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                                    (conv_id, "assistant", saved_msg, datetime.now(timezone.utc).isoformat()))
                        db2.commit()
                        db2.close()

                        yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'searched': True, 'perplexity': round(perplexity, 2), 'tokens_per_sec': round(tokens_per_sec, 1)})}\n\n"
                        return

                saved_msg = assistant_msg
                if remember_response:
                    saved_msg = remember_response + "\n\n" + saved_msg

                db2 = get_db()
                db2.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                            (conv_id, "assistant", saved_msg, datetime.now(timezone.utc).isoformat()))
                db2.commit()
                db2.close()

                yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'perplexity': round(perplexity, 2), 'tokens_per_sec': round(tokens_per_sec, 1)})}\n\n"

            except httpx.RemoteProtocolError:
                pass
            except httpx.ConnectError:
                yield f"data: {json.dumps({'error': 'Cannot connect to Ollama. Is it running?'})}\n\n"
            except Exception as e:
                incident_key = log_incident("chat_stream", message="Ollama stream failure during chat response",
                                            request=request, exc=e)
                yield f"data: {json.dumps({'error': 'Chat response generation failed before completion. Use the incident key for support lookup.', 'error_key': incident_key})}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")
PYEOF
echo "  [+] routers/chat.py"

# =============================================================================
# routers/search_route.py
# =============================================================================
cat > routers/search_route.py << 'PYEOF'
"""JarvisChat routers - /api/search explicit search endpoint."""
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from config import DEFAULT_MODEL, LLAMA_SERVER_BASE, MAX_SEARCH_QUERY_CHARS
from db import get_db
from search import query_searxng, format_search_results
from routers.chat import parse_llama_stream_chunk
from security import read_json_body, log_incident, BODY_LIMIT_CHAT_BYTES

log = logging.getLogger("jarvischat")
router = APIRouter()


@router.post("/api/search")
async def explicit_search(request: Request):
    body = await read_json_body(request, BODY_LIMIT_CHAT_BYTES)
    query = body.get("query", "").strip()
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        raise HTTPException(status_code=413, detail="Search query is too long")
    conv_id = body.get("conversation_id")
    model = body.get("model", DEFAULT_MODEL)

    if not query:
        raise HTTPException(status_code=400, detail="Empty query")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    if not conv_id:
        conv_id = str(uuid.uuid4())
        title = f"🔍 {query[:70]}..." if len(query) > 70 else f"🔍 {query}"
        db.execute("INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                   (conv_id, title, model, now, now))
    else:
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))

    db.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
               (conv_id, "user", f"🔍 {query}", now))
    db.commit()
    db.close()

    async def stream_search():
        yield f"data: {json.dumps({'conversation_id': conv_id, 'searching': True})}\n\n"

        results = await query_searxng(query, max_results=5)

        if not results:
            error_msg = "No search results found."
            yield f"data: {json.dumps({'token': error_msg, 'conversation_id': conv_id})}\n\n"
            db2 = get_db()
            db2.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                        (conv_id, "assistant", error_msg, datetime.now(timezone.utc).isoformat()))
            db2.commit()
            db2.close()
            yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id})}\n\n"
            return

        yield f"data: {json.dumps({'search_results': len(results), 'conversation_id': conv_id})}\n\n"

        search_context = format_search_results(results)
        messages = [
            {"role": "system", "content": f"You have access to current web data. Answer directly using ONLY the data below. Be concise. No apologies. No disclaimers.\n\n{search_context}"},
            {"role": "user", "content": query},
        ]

        full_response = []
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "POST", f"{LLAMA_SERVER_BASE}/v1/chat/completions",
                    json={"model": model, "messages": messages, "stream": True},
                    timeout=httpx.Timeout(300.0, connect=10.0),
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            token, done, _ = parse_llama_stream_chunk(line)
                            if token:
                                full_response.append(token)
                                yield f"data: {json.dumps({'token': token, 'conversation_id': conv_id})}\n\n"
                            if done:
                                break
            except Exception as e:
                incident_key = log_incident("search_summarization_stream",
                                            message="Stream failure during explicit search summarization",
                                            request=request, exc=e)
                yield f"data: {json.dumps({'error': 'Search summarization could not complete right now.', 'error_key': incident_key})}\n\n"
                return

        summary = "".join(full_response)
        saved_msg = f"{summary}\n\n---\n*🔍 Web search results*"

        db2 = get_db()
        db2.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (conv_id, "assistant", saved_msg, datetime.now(timezone.utc).isoformat()))
        db2.commit()
        db2.close()

        yield f"data: {json.dumps({'raw_results': results, 'conversation_id': conv_id})}\n\n"
        yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'searched': True})}\n\n"

    return StreamingResponse(stream_search(), media_type="text/event-stream")
PYEOF
echo "  [+] routers/search_route.py"

# =============================================================================
# app.py  (slim entry point - replaces monolith)
# =============================================================================
cat > app.py << 'PYEOF'
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
PYEOF
echo "  [+] app.py (slim entry point)"

# =============================================================================
# Verify syntax on all new Python files
# =============================================================================
echo ""
echo "=== Syntax check ==="
ERRORS=0
for f in config.py db.py security.py auth.py memory.py search.py gpu.py rag.py app.py \
          routers/__init__.py routers/conversations.py routers/memories.py routers/models.py \
          routers/presets.py routers/profile.py routers/settings.py routers/skills.py \
          routers/chat.py routers/search_route.py; do
    if python3 -m py_compile "$APP_DIR/$f" 2>&1; then
        echo "  OK  $f"
    else
        echo "  FAIL $f"
        ERRORS=$((ERRORS + 1))
    fi
done

if [ $ERRORS -gt 0 ]; then
    echo ""
    echo "ERROR: $ERRORS file(s) failed syntax check. Restoring original app.py."
    cp "$BACKUP" app.py
    exit 1
fi

# =============================================================================
# Restart service
# =============================================================================
echo ""
echo "=== Restarting jarvischat service ==="
if systemctl restart jarvischat 2>&1; then
    sleep 2
    if systemctl is-active --quiet jarvischat; then
        echo "  Service is UP"
    else
        echo "  WARNING: Service did not come up cleanly. Check: journalctl -u jarvischat -n 50"
    fi
else
    echo "  WARNING: systemctl restart failed. Try: sudo systemctl restart jarvischat"
fi

# =============================================================================
# Git commit
# =============================================================================
echo ""
echo "=== Git commit ==="
cd "$APP_DIR"
git add config.py db.py security.py auth.py memory.py search.py gpu.py rag.py app.py routers/
git commit -m "refactor(arch): modular package structure — split monolithic app.py into config/db/auth/memory/search/rag/gpu + routers/

- config.py: all constants, env vars, limits, skill registry, profiles
- db.py: schema init, connection factory, skill state helpers
- security.py: PIN hashing, audit logging, rate limiting, CSRF, request helpers
- auth.py: session management, PIN verify, auth routes
- memory.py: FTS5 CRUD + remember/forget command processing
- search.py: SearXNG integration, perplexity scoring, refusal/hedge detection
- gpu.py: rocm-smi stats
- rag.py: Qdrant vector search + system prompt assembly
- routers/: conversations, memories, models, presets, profile, settings, skills, chat, search
- app.py: slim entry point, middleware, router registration only

Bumps to v1.9.0"
git push origin main

echo ""
echo "=== Done ==="
echo "Backup at: $BACKUP"
echo "New structure:"
find "$APP_DIR" -name "*.py" ! -path "*/venv/*" ! -path "*/__pycache__/*" | sort
