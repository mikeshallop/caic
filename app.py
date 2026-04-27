#!/usr/bin/env python3
"""
JarvisChat - Lightweight Ollama Coding Companion
A minimal replacement for Open-WebUI that actually runs on Python 3.13
Talks to Ollama API on localhost:11434

Features:
  - Persistent profile/memory injected into every conversation
  - FTS5-based memory system for context retrieval
  - Saved system prompt presets (coding assistant, sysadmin, general, custom)
  - Streaming chat with conversation history
  - Model switching between all installed Ollama models
  - Copy-to-clipboard on code blocks
  - Token count estimates
  - SearXNG integration for web search when model is uncertain
  - Explicit web search via search button
"""

import json
import logging
import math
import os
import platform
import sqlite3
import subprocess
import hashlib
import hmac
import time
import uuid
import re
import ipaddress
from collections import defaultdict, deque
from threading import Lock
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

import httpx
import psutil
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# --- Logging Setup ---
import logging.handlers

log = logging.getLogger("jarvischat")
log.setLevel(logging.DEBUG)
syslog_handler = logging.handlers.SysLogHandler(address="/dev/log")
syslog_handler.setFormatter(
    logging.Formatter("jarvischat[%(process)d]: %(levelname)s %(message)s")
)
log.addHandler(syslog_handler)

# --- Configuration ---
VERSION = "1.7.2"
OLLAMA_BASE = "http://localhost:11434"
SEARXNG_BASE = "http://localhost:8888"
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "jarvischat.db"
DEFAULT_MODEL = "llama3.1:latest"

# --- Auth / Session Configuration ---
# Session timeout is intentionally short so tab close/crash leaves a brief exposure window.
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

# --- Rate / Payload Guardrails (home-lab friendly defaults) ---
RATE_WINDOW_SECONDS = 60
RL_LOGIN_PER_WINDOW = 10
RL_CHAT_PER_WINDOW = 24
RL_SEARCH_PER_WINDOW = 16
RL_WRITE_PER_WINDOW = 30
RL_DEFAULT_PER_WINDOW = 240
RL_STATS_PER_WINDOW = 600

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
ALLOWED_SETTINGS_KEYS = {
    "profile_enabled",
    "default_model",
    "search_enabled",
    "memory_enabled",
}

# --- Templates and Static Files ---
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# --- Perplexity Threshold ---
PERPLEXITY_THRESHOLD = 15.0

# --- Refusal Patterns ---
REFUSAL_PATTERNS = re.compile(
    r"|".join(
        [
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
        ]
    ),
    re.IGNORECASE,
)

# --- Hedging patterns ---
HEDGE_PATTERNS = [
    r"^I'?m sorry,?\s*but\s*I\s*(?:can'?t|cannot)\s*assist\s*with\s*that[^.]*\.\s*",
    r"^I'?m sorry,?\s*but[^.]*(?:previous|incorrect)[^.]*\.\s*",
    r"(?:But\s+)?[Pp]lease\s+(?:make\s+sure\s+to\s+)?verify\s+(?:the\s+)?(?:data|information|this)\s+(?:from\s+)?(?:reliable\s+)?sources[^.]*\.\s*",
    r"[Pp]lease\s+verify[^.]*(?:accurate|reliability)[^.]*\.\s*",
    r"[Bb]ut\s+please\s+(?:make\s+sure|verify|check)[^.]*\.\s*",
]

SESSIONS: dict[str, dict] = {}
PIN_ATTEMPTS: dict[str, dict] = {}
RATE_EVENTS: dict[str, deque[float]] = defaultdict(deque)
SESSION_LOCK = Lock()
RATE_LOCK = Lock()


def hash_pin(pin: str, salt_hex: Optional[str] = None) -> tuple[str, str]:
    """Hash a 4-digit PIN with PBKDF2-HMAC-SHA256."""
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 200_000)
    return salt.hex(), digest.hex()


def audit_event(
    event: str,
    outcome: str,
    *,
    ip: str = "unknown",
    role: str = "none",
    details: str = "",
    warning: bool = False,
) -> None:
    # Structured audit entries make destructive/auth events searchable in journal/syslog.
    payload = {
        "event": event,
        "outcome": outcome,
        "ip": ip,
        "role": role,
        "details": details[:300],
    }
    msg = "AUDIT " + json.dumps(payload, separators=(",", ":"))
    if warning:
        log.warning(msg)
    else:
        log.info(msg)


def create_incident_key() -> str:
    """Create a readable unique key for customer-visible error lookup."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"INC-{ts}-{uuid.uuid4().hex[:8].upper()}"


def customer_error_envelope(message: str, incident_key: str) -> dict:
    """Stable client-safe error contract with support lookup key."""
    return {
        "detail": message,
        "error_key": incident_key,
        "error": {
            "message": message,
            "incident_key": incident_key,
            "support_hint": "Share this incident key for exact diagnostics.",
        },
    }


def log_incident(
    event: str,
    *,
    message: str,
    request: Optional[Request] = None,
    exc: Optional[Exception] = None,
) -> str:
    """Log internal failure details with traceback and system snapshot."""
    incident_key = create_incident_key()
    payload = {
        "event": event,
        "incident_key": incident_key,
        "message": message,
        "app_version": VERSION,
        "pid": os.getpid(),
        "python": platform.python_version(),
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


def parse_allowed_cidrs(raw: str) -> list[ipaddress._BaseNetwork]:
    """Parse comma-separated CIDR list into validated network objects."""
    networks: list[ipaddress._BaseNetwork] = []
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


def clean_hedging(text: str) -> str:
    """Remove hedging sentences from model response."""
    cleaned = text
    for pattern in HEDGE_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def format_direct_answer(question: str, results: list[dict]) -> str:
    """Format search results directly when model refuses to help."""
    if not results:
        return "No search results found."
    lines = ["Here's what I found:\n"]
    for r in results[:3]:
        lines.append(f"**{r['title']}**")
        if r["content"]:
            lines.append(f"{r['content']}")
        lines.append("")
    return "\n".join(lines).strip()


def sanitize_outbound_url(url: str) -> str:
    """Allow only absolute http/https URLs for outbound links shown in UI."""
    if not url:
        return ""
    candidate = url.strip()
    parsed = urlparse(candidate)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return candidate
    return ""


async def read_json_body(request: Request, max_bytes: int) -> dict:
    """Read and parse JSON body with a hard size cap for non-streaming requests."""
    raw = await request.body()
    if len(raw) > max_bytes:
        raise HTTPException(status_code=413, detail="Request payload too large")
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")


def request_body_limit(path: str) -> int:
    if path in {"/api/chat", "/api/search"}:
        return BODY_LIMIT_CHAT_BYTES
    if path == "/api/profile":
        return BODY_LIMIT_PROFILE_BYTES
    return BODY_LIMIT_DEFAULT_BYTES


def rate_policy(path: str, method: str, ip: str, sid: str) -> tuple[str, int]:
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


def check_rate_limit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
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


# --- Default Profile ---
DEFAULT_PROFILE = """You are a coding companion running locally on a machine called "jarvis".

## Environment
- jarvis: Debian 13 (trixie) x86_64, AMD Ryzen 5 5600X, 16GB RAM, AMD RX 6600 XT (8GB VRAM)
- llamadev: Windows 11, primary development machine, IP 192.168.50.108, user "alphaalpaca"
- Corsair: Windows 11, gaming/streaming rig
- pivault: RPi 5, 8GB RAM, Debian 13, 11TB RAID5 NAS at /mnt/pivault, IP 192.168.50.159
- Router: ASUS ROG Rapture GT-BE98 Pro "BigBlinkyRouter" at 192.168.50.1
- Ollama runs on jarvis with GPU acceleration (ROCm), serving models on port 11434

## About the User
- Experienced developer, BS in Computer Science (Oklahoma State), coding since 1981 (TRS-80)
- Deep Unix/Linux background — wrote device drivers at SCO during Xenix era (1990s)
- Currently learning Rust, transitioning from decades of PHP
- Building a WW2 mobile game in Godot Engine for Android
- Runs a YouTube series: "Building a Professional Dev Environment with Local AI"
- Veteran on fixed income — prefers free/open-source solutions
- Home lab enthusiast with Z-Wave and Tapo smart home devices

## How to Respond
- Be direct and concise — no hand-holding, this user knows what they're doing
- When showing code, prefer complete working examples over snippets
- Default to command-line solutions over GUI when possible
- Consider resource constraints (fixed income, specific hardware limits)
- Use Rust, Python, or bash unless another language is specifically needed
- Explain trade-offs when multiple approaches exist
- Don't repeat information the user clearly already knows"""

# --- Default System Prompt Presets ---
DEFAULT_PRESETS = [
    {
        "name": "Coding Companion",
        "prompt": "You are a senior software engineer and coding companion. Focus on writing clean, efficient, well-documented code. Provide complete working examples. Explain architectural decisions and trade-offs. Prefer Rust, Python, and bash.",
    },
    {
        "name": "Linux Sysadmin",
        "prompt": "You are an experienced Linux systems administrator. Focus on command-line solutions, systemd services, networking, storage, and security. Prefer Debian/Ubuntu conventions. Be concise and direct.",
    },
    {
        "name": "General Assistant",
        "prompt": "You are a helpful general-purpose assistant. Be clear and concise.",
    },
]


# =============================================================================
# DATABASE
# =============================================================================


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'New Chat',
            model TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_presets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            prompt TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            content TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # FTS5 Memory table
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5(
            fact,
            topic,
            source,
            created_at UNINDEXED
        )
    """)

    # Seed default profile if empty
    existing = conn.execute("SELECT id FROM profile WHERE id = 1").fetchone()
    if not existing:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO profile (id, content, updated_at) VALUES (1, ?, ?)",
            (DEFAULT_PROFILE, now),
        )

    # Seed default presets if empty
    existing_presets = conn.execute(
        "SELECT COUNT(*) as c FROM system_presets"
    ).fetchone()
    if existing_presets["c"] == 0:
        now = datetime.now(timezone.utc).isoformat()
        for preset in DEFAULT_PRESETS:
            conn.execute(
                "INSERT INTO system_presets (id, name, prompt, is_default, created_at) VALUES (?, ?, ?, 1, ?)",
                (str(uuid.uuid4()), preset["name"], preset["prompt"], now),
            )

    # Default settings
    defaults = {
        "profile_enabled": "true",
        "default_model": DEFAULT_MODEL,
        "search_enabled": "true",
        "memory_enabled": "true",
    }
    for key, value in defaults.items():
        existing = conn.execute(
            "SELECT key FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)", (key, value)
            )

    # Seed admin PIN hash if missing.
    existing_pin_hash = conn.execute(
        "SELECT value FROM settings WHERE key = 'admin_pin_hash'"
    ).fetchone()
    existing_pin_salt = conn.execute(
        "SELECT value FROM settings WHERE key = 'admin_pin_salt'"
    ).fetchone()
    if not existing_pin_hash or not existing_pin_salt:
        # First-boot policy: require explicit PIN unless operator explicitly opts into insecure fallback.
        configured_pin = os.getenv("JARVISCHAT_ADMIN_PIN", "").strip()
        if re.fullmatch(r"\d{4}", configured_pin):
            seed_pin = configured_pin
            pin_source = "env"
        elif ALLOW_DEFAULT_PIN:
            seed_pin = "1234"
            pin_source = "default"
        else:
            raise RuntimeError(
                "Admin PIN bootstrap blocked: set JARVISCHAT_ADMIN_PIN to a 4-digit PIN "
                "or set JARVISCHAT_ALLOW_DEFAULT_PIN=true to allow insecure default PIN 1234."
            )

        salt_hex, pin_hash_hex = hash_pin(seed_pin)
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("admin_pin_hash", pin_hash_hex),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("admin_pin_salt", salt_hex),
        )
        if pin_source == "default":
            log.warning("Admin PIN seeded from insecure default 1234 (override enabled).")
        else:
            log.info("Admin PIN hash seeded from configured environment PIN.")

    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_setting(db, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


# =============================================================================
# MEMORY SYSTEM (FTS5)
# =============================================================================


def add_memory(
    fact: str, topic: str = "general", source: str = "explicit"
) -> int | None:
    """Store a new memory. Returns rowid."""
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


def search_memories(query: str, limit: int = 5) -> list[dict]:
    """Search memories by relevance using FTS5."""
    if not query.strip():
        return []
    db = get_db()
    # Use alphanumeric token extraction so punctuation (e.g. '?') cannot break FTS MATCH syntax.
    words = re.findall(r"[A-Za-z0-9_]+", query)
    if not words:
        db.close()
        return []
    safe_query = " OR ".join(word + "*" for word in words[:10])
    try:
        rows = db.execute(
            """
            SELECT rowid, fact, topic, source, created_at, bm25(memories) AS rank
            FROM memories WHERE memories MATCH ? ORDER BY rank LIMIT ?
        """,
            (safe_query, limit),
        ).fetchall()
        results = [dict(row) for row in rows]
        log.debug(f"Memory search '{query}' returned {len(results)} results")
    except Exception as e:
        log.warning(f"Memory search error: {e}")
        results = []
    db.close()
    return results


def get_all_memories(topic: Optional[str] = None) -> list[dict]:
    """Get all memories, optionally filtered by topic."""
    db = get_db()
    if topic:
        rows = db.execute(
            "SELECT rowid, * FROM memories WHERE topic = ? ORDER BY created_at DESC",
            (topic,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT rowid, * FROM memories ORDER BY created_at DESC"
        ).fetchall()
    db.close()
    return [dict(row) for row in rows]


def delete_memory(rowid: int) -> bool:
    """Delete a memory by rowid."""
    db = get_db()
    cur = db.execute("DELETE FROM memories WHERE rowid = ?", (rowid,))
    db.commit()
    deleted = cur.rowcount > 0
    db.close()
    if deleted:
        log.info(f"Memory deleted: rowid={rowid}")
    return deleted


def update_memory(rowid: int, fact: str) -> bool:
    """Update an existing memory's fact."""
    db = get_db()
    cur = db.execute("UPDATE memories SET fact = ? WHERE rowid = ?", (fact, rowid))
    db.commit()
    updated = cur.rowcount > 0
    db.close()
    return updated


def get_memory_count() -> int:
    """Get total number of memories."""
    db = get_db()
    count = db.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
    db.close()
    return count


# --- Remember/Forget command processing ---
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
    """Auto-detect memory topic from content."""
    fact_lower = fact.lower()
    if any(
        w in fact_lower
        for w in ["prefer", "like", "hate", "always", "never", "favorite"]
    ):
        return "preference"
    elif any(
        w in fact_lower for w in ["working on", "building", "project", "developing"]
    ):
        return "project"
    elif any(
        w in fact_lower
        for w in [
            "run",
            "install",
            "server",
            "ip",
            "port",
            "service",
            "docker",
            "systemd",
        ]
    ):
        return "infrastructure"
    elif any(
        w in fact_lower
        for w in ["my name", "i am", "i'm a", "i live", "my wife", "my partner"]
    ):
        return "personal"
    return "general"


def process_remember_command(user_message: str) -> Optional[str]:
    """Check for 'remember/forget' commands. Returns confirmation or None."""
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


# =============================================================================
# SEARXNG INTEGRATION
# =============================================================================


async def query_searxng(query: str, max_results: int = 5) -> list[dict]:
    """Query SearXNG and return search results."""
    log.info(f"Querying SearXNG: '{query}'")
    async with httpx.AsyncClient() as client:
        # Weather shortcut
        weather_match = re.search(
            r"(?:weather|temperature|forecast)\s+(?:in\s+)?(.+?)(?:\s+right now|\s+today|\s+degrees)?$",
            query,
            re.IGNORECASE,
        )
        if (
            weather_match
            or "weather" in query.lower()
            or "temperature" in query.lower()
        ):
            location = (
                weather_match.group(1)
                if weather_match
                else re.sub(
                    r"(weather|temperature|forecast|right now|today|degrees)",
                    "",
                    query,
                    flags=re.IGNORECASE,
                ).strip()
            )
            if location:
                try:
                    resp = await client.get(
                        f"https://wttr.in/{location}?format=3",
                        timeout=10.0,
                        headers={"User-Agent": "curl/7.68.0"},
                    )
                    if resp.status_code == 200:
                        return [
                            {
                                "title": "Current Weather",
                                "url": sanitize_outbound_url(f"https://wttr.in/{location}"),
                                "content": resp.text.strip(),
                            }
                        ]
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
                    results.append(
                        {"title": "Direct Answer", "url": "", "content": answer}
                    )
                for box in data.get("infoboxes", []):
                    content = box.get("content", "")
                    if not content and box.get("attributes"):
                        content = " | ".join(
                            [
                                f"{a.get('label', '')}: {a.get('value', '')}"
                                for a in box["attributes"]
                            ]
                        )
                    results.append(
                        {
                            "title": box.get("infobox", "Info"),
                            "url": sanitize_outbound_url(
                                box.get("urls", [{}])[0].get("url", "")
                                if box.get("urls")
                                else ""
                            ),
                            "content": content,
                        }
                    )
                for r in data.get("results", [])[:max_results]:
                    results.append(
                        {
                            "title": r.get("title", ""),
                            "url": sanitize_outbound_url(r.get("url", "")),
                            "content": r.get("content", ""),
                        }
                    )
                log.info(f"SearXNG returned {len(results)} results")
                return results
        except Exception as e:
            log.error(f"SearXNG error: {e}")
    return []


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


def format_search_results(results: list[dict]) -> str:
    if not results:
        return ""
    lines = ["[LIVE WEB DATA]\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        if r["content"]:
            lines.append(f"   {r['content']}")
        lines.append("")
    lines.append(
        "\nAnswer directly using the data above. No apologies. No disclaimers. Just answer."
    )
    return "\n".join(lines)


def extract_search_query(user_message: str) -> str:
    query = user_message.strip()
    if re.search(r"temperature|weather", query, re.IGNORECASE):
        query = (
            re.sub(r"^what('?s| is) the ", "", query, flags=re.IGNORECASE)
            + " right now degrees"
        )
    if re.search(r"price|spot price", query, re.IGNORECASE):
        query = (
            re.sub(
                r"^(what('?s| is)|can you tell me) the ", "", query, flags=re.IGNORECASE
            )
            + " today USD"
        )
    query = re.sub(
        r"^(what|who|where|when|why|how|is|are|can|could|would|should|do|does|did)\s+",
        "",
        query,
        flags=re.IGNORECASE,
    )
    query = re.sub(r"[?!.]+$", "", query)
    return query[:100].strip() or user_message[:100]


# =============================================================================
# GPU STATS
# =============================================================================


def get_gpu_stats() -> dict:
    """Get AMD GPU stats via rocm-smi."""
    try:
        result = subprocess.run(
            ["rocm-smi", "--showuse", "--showmemuse", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
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


# =============================================================================
# APP LIFECYCLE
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"JarvisChat v{VERSION} starting up")
    log.info(f"Ollama: {OLLAMA_BASE}, SearXNG: {SEARXNG_BASE}")
    init_db()
    log.info(f"Memory system: {get_memory_count()} memories loaded")
    yield
    log.info("JarvisChat shutting down")


app = FastAPI(title="JarvisChat", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    incident_key = log_incident(
        "unhandled_exception",
        message="Unhandled server error",
        request=request,
        exc=exc,
    )
    message = (
        "We could not complete that request right now. "
        "Use the incident key for support lookup."
    )
    return JSONResponse(
        status_code=500,
        content=customer_error_envelope(message, incident_key),
    )

# Mount static files
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# =============================================================================
# AUTH + SESSION
# =============================================================================


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if TRUST_X_FORWARDED_FOR and forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def is_ip_allowed(ip: str) -> bool:
    """Allow only loopback/private CIDRs by default; env can override CIDR list."""
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


def cleanup_sessions(now_ts: Optional[float] = None) -> None:
    now_ts = now_ts or time.time()
    with SESSION_LOCK:
        expired = [
            sid
            for sid, meta in SESSIONS.items()
            if (now_ts - meta.get("last_seen", 0)) > SESSION_TIMEOUT_SECONDS
        ]
        for sid in expired:
            del SESSIONS[sid]


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


def is_ip_locked(ip: str) -> tuple[bool, int]:
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


def create_session(ip: str, role: str) -> str:
    now_ts = time.time()
    sid = uuid.uuid4().hex
    with SESSION_LOCK:
        SESSIONS[sid] = {
            "ip": ip,
            "role": role,
            "created_at": now_ts,
            "last_seen": now_ts,
        }
    return sid


def validate_session(sid: str, ip: str, touch: bool = True) -> bool:
    if not sid:
        return False
    now_ts = time.time()
    cleanup_sessions(now_ts)
    with SESSION_LOCK:
        session = SESSIONS.get(sid)
        if not session:
            return False
        if session.get("ip") != ip:
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
        if not session:
            return None
        if session.get("ip") != ip:
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
    # Capability split: guest may chat/search; write/destructive/admin config paths require PIN-unlocked admin.
    if method in {"PUT", "DELETE", "PATCH"}:
        return True
    if method != "POST":
        return False
    guest_allowed_posts = {
        "/api/chat",
        "/api/search",
        "/api/show",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/session",
        "/api/auth/heartbeat",
        "/api/auth/guest",
    }
    return path not in guest_allowed_posts


def is_state_changing(method: str) -> bool:
    return method in {"POST", "PUT", "DELETE", "PATCH"}


def origin_allowed(request: Request) -> bool:
    """Allow same-origin browser writes and optional configured trusted origins.

    If Origin/Referer is absent, treat as non-browser/API client and allow
    (token/session header remains the primary auth factor).
    """
    host = request.headers.get("host", "").strip()
    expected_origin = f"{request.url.scheme}://{host}".rstrip("/") if host else ""
    origin = request.headers.get("origin", "").strip().rstrip("/")
    referer = request.headers.get("referer", "").strip()

    if origin:
        if origin == expected_origin or origin in TRUSTED_ORIGINS:
            return True
        return False

    if referer:
        parsed = urlparse(referer)
        ref_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if ref_origin == expected_origin or ref_origin in TRUSTED_ORIGINS:
            return True
        return False

    return True


@app.middleware("http")
async def session_auth_middleware(request: Request, call_next):
    path = request.url.path
    ip = get_client_ip(request)
    sid = request.headers.get("x-session-id", "").strip()
    request.state.session_role = "none"
    request.state.client_ip = ip

    if path.startswith("/api/"):
        if not is_ip_allowed(ip):
            audit_event(
                "ip_allowlist",
                "denied",
                ip=ip,
                role="none",
                details=f"{request.method} {path}",
                warning=True,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Client IP not allowed"},
            )

    if path.startswith("/api/"):
        rate_key, rate_limit = rate_policy(path, request.method, ip, sid)
        allowed, retry_after = check_rate_limit(
            rate_key, rate_limit, RATE_WINDOW_SECONDS
        )
        if not allowed:
            audit_event(
                "rate_limit",
                "denied",
                ip=ip,
                role="none",
                details=f"{request.method} {path} retry_after={retry_after}",
                warning=True,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Retry in {retry_after}s."},
            )

        if request.method in {"POST", "PUT", "PATCH"}:
            max_bytes = request_body_limit(path)
            content_length = request.headers.get("content-length", "").strip()
            if content_length.isdigit() and int(content_length) > max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request payload too large"},
                )

    unauth_paths = {
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/session",
        "/api/auth/heartbeat",
        "/api/auth/guest",
    }

    # CSRF hardening for browser writes: same-origin or explicitly allowlisted origins only.
    if path.startswith("/api/") and is_state_changing(request.method):
        if not origin_allowed(request):
            audit_event(
                "origin_check",
                "denied",
                ip=ip,
                role="none",
                details=f"{request.method} {path}",
                warning=True,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Origin check failed"},
            )

    if path.startswith("/api/") and path not in unauth_paths:
        session = get_session(sid, ip, touch=True)
        if not session:
            audit_event(
                "auth_required",
                "denied",
                ip=ip,
                role="none",
                details=f"{request.method} {path}",
                warning=True,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )
        request.state.session_role = session.get("role", "none")
        # Guest sessions stay usable for chat, but advanced/destructive actions require admin capability.
        if session.get("role") != "admin" and is_admin_only(path, request.method):
            audit_event(
                "admin_capability",
                "denied",
                ip=ip,
                role=session.get("role", "none"),
                details=f"{request.method} {path}",
                warning=True,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Admin PIN required for this action"},
            )

    response = await call_next(request)
    # Emit success audit only after route executes, so logs reflect completed admin actions.
    if path.startswith("/api/") and is_admin_only(path, request.method):
        role = getattr(request.state, "session_role", "none")
        if response.status_code < 400 and role == "admin":
            audit_event(
                "admin_action",
                "success",
                ip=ip,
                role=role,
                details=f"{request.method} {path}",
            )
    return response


@app.post("/api/auth/guest")
async def auth_guest(request: Request):
    ip = get_client_ip(request)
    sid = create_session(ip, role="guest")
    audit_event("guest_session", "success", ip=ip, role="guest")
    return {
        "status": "ok",
        "session_id": sid,
        "role": "guest",
        "timeout_seconds": SESSION_TIMEOUT_SECONDS,
    }


@app.post("/api/auth/login")
async def auth_login(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    pin = str(body.get("pin", ""))
    ip = get_client_ip(request)

    locked, retry_after = is_ip_locked(ip)
    if locked:
        audit_event(
            "admin_login",
            "locked",
            ip=ip,
            role="none",
            details=f"retry_after={retry_after}",
            warning=True,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed PIN attempts. Retry in {retry_after}s.",
        )

    if not verify_admin_pin(pin):
        record_pin_failure(ip)
        audit_event("admin_login", "failed", ip=ip, role="none", warning=True)
        raise HTTPException(status_code=401, detail="Invalid PIN")

    clear_pin_failures(ip)
    sid = create_session(ip, role="admin")
    audit_event("admin_login", "success", ip=ip, role="admin")
    return {
        "status": "ok",
        "session_id": sid,
        "role": "admin",
        "timeout_seconds": SESSION_TIMEOUT_SECONDS,
    }


@app.get("/api/auth/session")
async def auth_session(request: Request):
    sid = request.headers.get("x-session-id", "").strip()
    ip = get_client_ip(request)
    session = get_session(sid, ip, touch=True)
    return {
        "authenticated": bool(session),
        "role": session.get("role") if session else "none",
    }


@app.post("/api/auth/heartbeat")
async def auth_heartbeat(request: Request):
    sid = request.headers.get("x-session-id", "").strip()
    ip = get_client_ip(request)
    if not sid or not validate_session(sid, ip, touch=True):
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"status": "ok"}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
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


# =============================================================================
# API ROUTES
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"version": VERSION})


@app.get("/api/models")
async def list_models():
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags", timeout=10)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to Ollama.")


@app.get("/api/ps")
async def running_models():
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{OLLAMA_BASE}/api/ps", timeout=10)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to Ollama.")


@app.post("/api/show")
async def show_model(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{OLLAMA_BASE}/api/show", json=body, timeout=10)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to Ollama.")


@app.get("/api/search/status")
async def search_status():
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{SEARXNG_BASE}/search",
                params={"q": "test", "format": "json"},
                timeout=5,
            )
            return {"available": resp.status_code == 200}
        except Exception:
            return {"available": False}


@app.get("/api/stats")
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


# --- Memory API ---


@app.get("/api/memories")
async def list_memories(topic: Optional[str] = None):
    memories = get_all_memories(topic)
    return {"memories": memories, "count": len(memories)}


@app.post("/api/memories")
async def create_memory(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    fact = str(body.get("fact", "")).strip()
    if not fact:
        raise HTTPException(status_code=400, detail="Memory fact is required")
    if len(fact) > MAX_MEMORY_FACT_CHARS:
        raise HTTPException(status_code=413, detail="Memory fact is too long")
    rowid = add_memory(
        fact=fact,
        topic=body.get("topic", "general"),
        source=body.get("source", "manual"),
    )
    return {"rowid": rowid, "status": "ok"}


@app.delete("/api/memories/{rowid}")
async def remove_memory(rowid: int):
    if not delete_memory(rowid):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "ok"}


@app.put("/api/memories/{rowid}")
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


@app.get("/api/memories/search")
async def search_memories_api(q: str, limit: int = 10):
    results = search_memories(q, limit=limit)
    return {"results": results, "count": len(results)}


@app.get("/api/memories/stats")
async def memory_stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
    topics = db.execute(
        "SELECT topic, COUNT(*) as c FROM memories GROUP BY topic ORDER BY c DESC"
    ).fetchall()
    db.close()
    return {"total": total, "by_topic": {row["topic"]: row["c"] for row in topics}}


# --- Profile ---


@app.get("/api/profile")
async def get_profile():
    db = get_db()
    row = db.execute("SELECT content, updated_at FROM profile WHERE id = 1").fetchone()
    db.close()
    return (
        {"content": row["content"], "updated_at": row["updated_at"]}
        if row
        else {"content": "", "updated_at": ""}
    )


@app.put("/api/profile")
async def update_profile(request: Request):
    body = await read_json_body(request, BODY_LIMIT_PROFILE_BYTES)
    content = str(body.get("content", ""))
    if len(content) > MAX_PROFILE_CHARS:
        raise HTTPException(status_code=413, detail="Profile content is too long")
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "UPDATE profile SET content = ?, updated_at = ? WHERE id = 1",
        (content, now),
    )
    db.commit()
    db.close()
    return {"status": "ok", "updated_at": now}


@app.get("/api/profile/default")
async def get_default_profile():
    return {"content": DEFAULT_PROFILE}


# --- Settings ---


@app.get("/api/settings")
async def get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    db.close()
    return {row["key"]: row["value"] for row in rows}


@app.put("/api/settings")
async def update_settings(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Settings payload must be an object")
    if len(body) > MAX_SETTINGS_KEYS:
        raise HTTPException(status_code=413, detail="Too many settings in one request")
    unknown_keys = sorted(
        key for key in body.keys() if str(key) not in ALLOWED_SETTINGS_KEYS
    )
    if unknown_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown setting key(s): {', '.join(unknown_keys)}",
        )
    db = get_db()
    for key, value in body.items():
        if len(str(key)) > 80 or len(str(value)) > MAX_SETTINGS_VALUE_CHARS:
            db.close()
            raise HTTPException(status_code=413, detail="Setting key/value too long")
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
    db.commit()
    db.close()
    return {"status": "ok"}


# --- System Presets ---


@app.get("/api/presets")
async def list_presets():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM system_presets ORDER BY is_default DESC, name ASC"
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


@app.post("/api/presets")
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
    db.execute(
        "INSERT INTO system_presets (id, name, prompt, is_default, created_at) VALUES (?, ?, ?, 0, ?)",
        (preset_id, name, prompt, now),
    )
    db.commit()
    db.close()
    return {"id": preset_id, "name": name, "prompt": prompt}


@app.put("/api/presets/{preset_id}")
async def update_preset(preset_id: str, request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    name = str(body.get("name", "")).strip()
    prompt = str(body.get("prompt", "")).strip()
    if not name or not prompt:
        raise HTTPException(status_code=400, detail="Preset name and prompt are required")
    if len(name) > MAX_PRESET_NAME_CHARS or len(prompt) > MAX_PRESET_PROMPT_CHARS:
        raise HTTPException(status_code=413, detail="Preset fields are too long")
    db = get_db()
    db.execute(
        "UPDATE system_presets SET name = ?, prompt = ? WHERE id = ?",
        (name, prompt, preset_id),
    )
    db.commit()
    db.close()
    return {"status": "ok"}


@app.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str):
    db = get_db()
    db.execute(
        "DELETE FROM system_presets WHERE id = ? AND is_default = 0", (preset_id,)
    )
    db.commit()
    db.close()
    return {"status": "ok"}


# --- Conversations ---


@app.get("/api/conversations")
async def list_conversations():
    db = get_db()
    rows = db.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
    db.close()
    return [dict(r) for r in rows]


@app.post("/api/conversations")
async def create_conversation(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    model = body.get("model", DEFAULT_MODEL)
    title = str(body.get("title", "New Chat"))[:MAX_CONVERSATION_TITLE_CHARS]
    db = get_db()
    db.execute(
        "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (conv_id, title, model, now, now),
    )
    db.commit()
    db.close()
    return {
        "id": conv_id,
        "title": title,
        "model": model,
        "created_at": now,
        "updated_at": now,
    }


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    db = get_db()
    conv = db.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if not conv:
        db.close()
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = db.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", (conv_id,)
    ).fetchall()
    db.close()
    return {"conversation": dict(conv), "messages": [dict(m) for m in messages]}


@app.put("/api/conversations/{conv_id}")
async def update_conversation(conv_id: str, request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    if "title" in body:
        db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (str(body["title"])[:MAX_CONVERSATION_TITLE_CHARS], now, conv_id),
        )
    if "model" in body:
        db.execute(
            "UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
            (body["model"], now, conv_id),
        )
    db.commit()
    db.close()
    return {"status": "ok"}


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    db = get_db()
    db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    db.commit()
    db.close()
    return {"status": "ok"}


@app.delete("/api/conversations")
async def delete_all_conversations():
    db = get_db()
    db.execute("DELETE FROM messages")
    db.execute("DELETE FROM conversations")
    db.commit()
    db.close()
    log.info("Deleted all conversations")
    return {"status": "ok"}


# =============================================================================
# EXPLICIT WEB SEARCH
# =============================================================================


@app.post("/api/search")
async def explicit_search(request: Request):
    """Explicit web search - bypasses model uncertainty, queries SearXNG directly."""
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
        db.execute(
            "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, title, model, now, now),
        )
    else:
        db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id)
        )

    db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conv_id, "user", f"🔍 {query}", now),
    )
    db.commit()
    db.close()

    async def stream_search():
        yield f"data: {json.dumps({'conversation_id': conv_id, 'searching': True})}\n\n"

        results = await query_searxng(query, max_results=5)

        if not results:
            error_msg = "No search results found."
            yield f"data: {json.dumps({'token': error_msg, 'conversation_id': conv_id})}\n\n"

            # Save to DB
            db2 = get_db()
            db2.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (
                    conv_id,
                    "assistant",
                    error_msg,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            db2.commit()
            db2.close()

            yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id})}\n\n"
            return

        yield f"data: {json.dumps({'search_results': len(results), 'conversation_id': conv_id})}\n\n"

        # Ask Ollama to summarize
        search_context = format_search_results(results)
        messages = [
            {
                "role": "system",
                "content": f"You have access to current web data. Answer directly using ONLY the data below. Be concise. No apologies. No disclaimers.\n\n{search_context}",
            },
            {"role": "user", "content": query},
        ]

        full_response = []
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE}/api/chat",
                    json={"model": model, "messages": messages, "stream": True},
                    timeout=httpx.Timeout(300.0, connect=10.0),
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            try:
                                chunk = json.loads(line)
                                if "message" in chunk and "content" in chunk["message"]:
                                    token = chunk["message"]["content"]
                                    full_response.append(token)
                                    yield f"data: {json.dumps({'token': token, 'conversation_id': conv_id})}\n\n"
                                if chunk.get("done"):
                                    break
                            except json.JSONDecodeError:
                                pass
            except Exception as e:
                incident_key = log_incident(
                    "search_summarization_stream",
                    message="Ollama stream failure during explicit search summarization",
                    request=request,
                    exc=e,
                )
                client_msg = (
                    "Search summarization could not complete right now. "
                    "Use the incident key for support lookup."
                )
                yield f"data: {json.dumps({'error': client_msg, 'error_key': incident_key})}\n\n"
                return

        summary = "".join(full_response)

        saved_msg = f"{summary}\n\n---\n*🔍 Web search results*"

        db2 = get_db()
        db2.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conv_id, "assistant", saved_msg, datetime.now(timezone.utc).isoformat()),
        )
        db2.commit()
        db2.close()

        # Send raw results for frontend expandable div
        yield f"data: {json.dumps({'raw_results': results, 'conversation_id': conv_id})}\n\n"
        yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'searched': True})}\n\n"

    return StreamingResponse(stream_search(), media_type="text/event-stream")


# =============================================================================
# CHAT (STREAMING)
# =============================================================================


def build_system_prompt(db, extra_prompt="", user_message=""):
    """Build the full system prompt: profile + memories + preset."""
    parts = []
    settings = {
        row["key"]: row["value"]
        for row in db.execute("SELECT key, value FROM settings").fetchall()
    }

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

    if extra_prompt and extra_prompt.strip():
        parts.append(extra_prompt.strip())

    return "\n\n---\n\n".join(parts) if parts else ""


@app.post("/api/chat")
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
    settings = {
        row["key"]: row["value"]
        for row in db.execute("SELECT key, value FROM settings").fetchall()
    }
    search_enabled = settings.get("search_enabled", "true") == "true"

    remember_response = process_remember_command(user_message)

    if not conv_id:
        conv_id = str(uuid.uuid4())
        title = user_message[:80] + ("..." if len(user_message) > 80 else "")
        db.execute(
            "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, title, model, now, now),
        )
    else:
        db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id)
        )

    db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conv_id, "user", user_message, now),
    )
    db.commit()

    history_rows = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC",
        (conv_id,),
    ).fetchall()
    system_prompt = build_system_prompt(db, preset_prompt, user_message)
    db.close()

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for row in history_rows:
        messages.append({"role": row["role"], "content": row["content"]})

    ollama_payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "logprobs": True,
    }

    async def stream_response():
        full_response = []
        all_logprobs = []
        tokens_per_sec = 0.0

        if remember_response:
            yield f"data: {json.dumps({'token': remember_response + chr(10) + chr(10), 'conversation_id': conv_id})}\n\n"

        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE}/api/chat",
                    json=ollama_payload,
                    timeout=httpx.Timeout(300.0, connect=10.0),
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            try:
                                chunk = json.loads(line)
                                if "message" in chunk and "content" in chunk["message"]:
                                    token = chunk["message"]["content"]
                                    full_response.append(token)
                                    yield f"data: {json.dumps({'token': token, 'conversation_id': conv_id})}\n\n"
                                if "logprobs" in chunk and chunk["logprobs"]:
                                    all_logprobs.extend(chunk["logprobs"])
                                if chunk.get("done"):
                                    eval_count = chunk.get("eval_count", 0)
                                    eval_duration = chunk.get("eval_duration", 0)
                                    tokens_per_sec = (
                                        (eval_count / (eval_duration / 1e9))
                                        if eval_duration > 0
                                        else 0
                                    )
                                    break
                            except json.JSONDecodeError:
                                pass

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
                            augmented_messages.append(
                                {
                                    "role": "system",
                                    "content": system_prompt + "\n\n" + search_context,
                                }
                            )
                        else:
                            augmented_messages.append(
                                {"role": "system", "content": search_context}
                            )
                        for row in history_rows[:-1]:
                            augmented_messages.append(
                                {"role": row["role"], "content": row["content"]}
                            )
                        augmented_messages.append(
                            {"role": "user", "content": user_message}
                        )

                        yield f"data: {json.dumps({'search_results': len(search_results), 'conversation_id': conv_id})}\n\n"

                        augmented_response = []
                        async with client.stream(
                            "POST",
                            f"{OLLAMA_BASE}/api/chat",
                            json={
                                "model": model,
                                "messages": augmented_messages,
                                "stream": True,
                            },
                            timeout=httpx.Timeout(300.0, connect=10.0),
                        ) as resp2:
                            async for line in resp2.aiter_lines():
                                if line.strip():
                                    try:
                                        chunk = json.loads(line)
                                        if (
                                            "message" in chunk
                                            and "content" in chunk["message"]
                                        ):
                                            augmented_response.append(
                                                chunk["message"]["content"]
                                            )
                                        if chunk.get("done"):
                                            break
                                    except json.JSONDecodeError:
                                        pass

                        raw_response = "".join(augmented_response) or assistant_msg
                        cleaned_response = clean_hedging(raw_response)
                        if is_refusal(cleaned_response) or len(cleaned_response) < 20:
                            cleaned_response = format_direct_answer(
                                user_message, search_results
                            )

                        yield f"data: {json.dumps({'token': cleaned_response, 'conversation_id': conv_id, 'augmented': True})}\n\n"

                        saved_msg = (
                            cleaned_response
                            + "\n\n---\n*🔍 Enhanced with web search results*"
                        )
                        if remember_response:
                            saved_msg = remember_response + "\n\n" + saved_msg

                        db2 = get_db()
                        db2.execute(
                            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                            (
                                conv_id,
                                "assistant",
                                saved_msg,
                                datetime.now(timezone.utc).isoformat(),
                            ),
                        )
                        db2.commit()
                        db2.close()

                        yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'searched': True, 'perplexity': round(perplexity, 2), 'tokens_per_sec': round(tokens_per_sec, 1)})}\n\n"
                        return

                saved_msg = assistant_msg
                if remember_response:
                    saved_msg = remember_response + "\n\n" + saved_msg

                db2 = get_db()
                db2.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (
                        conv_id,
                        "assistant",
                        saved_msg,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                db2.commit()
                db2.close()

                yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'perplexity': round(perplexity, 2), 'tokens_per_sec': round(tokens_per_sec, 1)})}\n\n"

            except httpx.ConnectError:
                yield f"data: {json.dumps({'error': 'Cannot connect to Ollama. Is it running?'})}\n\n"
            except Exception as e:
                incident_key = log_incident(
                    "chat_stream",
                    message="Ollama stream failure during chat response",
                    request=request,
                    exc=e,
                )
                client_msg = (
                    "Chat response generation failed before completion. "
                    "Use the incident key for support lookup."
                )
                yield f"data: {json.dumps({'error': client_msg, 'error_key': incident_key})}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
