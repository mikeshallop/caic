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
import sqlite3
import subprocess
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

import httpx
import psutil
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# --- Logging Setup ---
import logging.handlers

log = logging.getLogger("jarvischat")
log.setLevel(logging.DEBUG)
syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
syslog_handler.setFormatter(logging.Formatter('jarvischat[%(process)d]: %(levelname)s %(message)s'))
log.addHandler(syslog_handler)

# --- Configuration ---
VERSION = "1.5.0"
OLLAMA_BASE = "http://localhost:11434"
SEARXNG_BASE = "http://localhost:8888"
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "jarvischat.db"
DEFAULT_MODEL = "llama3.1:latest"

# --- Templates and Static Files ---
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# --- Perplexity Threshold ---
PERPLEXITY_THRESHOLD = 15.0

# --- Refusal Patterns ---
REFUSAL_PATTERNS = re.compile(r"|".join([
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
]), re.IGNORECASE)

# --- Hedging patterns ---
HEDGE_PATTERNS = [
    r"^I'?m sorry,?\s*but\s*I\s*(?:can'?t|cannot)\s*assist\s*with\s*that[^.]*\.\s*",
    r"^I'?m sorry,?\s*but[^.]*(?:previous|incorrect)[^.]*\.\s*",
    r"(?:But\s+)?[Pp]lease\s+(?:make\s+sure\s+to\s+)?verify\s+(?:the\s+)?(?:data|information|this)\s+(?:from\s+)?(?:reliable\s+)?sources[^.]*\.\s*",
    r"[Pp]lease\s+verify[^.]*(?:accurate|reliability)[^.]*\.\s*",
    r"[Bb]ut\s+please\s+(?:make\s+sure|verify|check)[^.]*\.\s*",
]

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
        if r['content']:
            lines.append(f"{r['content']}")
        lines.append("")
    return "\n".join(lines).strip()

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
    {"name": "Coding Companion", "prompt": "You are a senior software engineer and coding companion. Focus on writing clean, efficient, well-documented code. Provide complete working examples. Explain architectural decisions and trade-offs. Prefer Rust, Python, and bash."},
    {"name": "Linux Sysadmin", "prompt": "You are an experienced Linux systems administrator. Focus on command-line solutions, systemd services, networking, storage, and security. Prefer Debian/Ubuntu conventions. Be concise and direct."},
    {"name": "General Assistant", "prompt": "You are a helpful general-purpose assistant. Be clear and concise."}
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
        conn.execute("INSERT INTO profile (id, content, updated_at) VALUES (1, ?, ?)", (DEFAULT_PROFILE, now))

    # Seed default presets if empty
    existing_presets = conn.execute("SELECT COUNT(*) as c FROM system_presets").fetchone()
    if existing_presets["c"] == 0:
        now = datetime.now(timezone.utc).isoformat()
        for preset in DEFAULT_PRESETS:
            conn.execute(
                "INSERT INTO system_presets (id, name, prompt, is_default, created_at) VALUES (?, ?, ?, 1, ?)",
                (str(uuid.uuid4()), preset["name"], preset["prompt"], now)
            )

    # Default settings
    defaults = {"profile_enabled": "true", "default_model": DEFAULT_MODEL, "search_enabled": "true", "memory_enabled": "true"}
    for key, value in defaults.items():
        existing = conn.execute("SELECT key FROM settings WHERE key = ?", (key,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))

    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# =============================================================================
# MEMORY SYSTEM (FTS5)
# =============================================================================

def add_memory(fact: str, topic: str = "general", source: str = "explicit") -> int | None:
    """Store a new memory. Returns rowid."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "INSERT INTO memories (fact, topic, source, created_at) VALUES (?, ?, ?, ?)",
        (fact, topic, source, now)
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
    words = [w.strip() for w in query.split() if w.strip()]
    if not words:
        db.close()
        return []
    safe_query = " OR ".join(word + "*" for word in words[:10])
    try:
        rows = db.execute("""
            SELECT rowid, fact, topic, source, created_at, bm25(memories) AS rank
            FROM memories WHERE memories MATCH ? ORDER BY rank LIMIT ?
        """, (safe_query, limit)).fetchall()
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
        rows = db.execute("SELECT rowid, * FROM memories WHERE topic = ? ORDER BY created_at DESC", (topic,)).fetchall()
    else:
        rows = db.execute("SELECT rowid, * FROM memories ORDER BY created_at DESC").fetchall()
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
    if any(w in fact_lower for w in ["prefer", "like", "hate", "always", "never", "favorite"]):
        return "preference"
    elif any(w in fact_lower for w in ["working on", "building", "project", "developing"]):
        return "project"
    elif any(w in fact_lower for w in ["run", "install", "server", "ip", "port", "service", "docker", "systemd"]):
        return "infrastructure"
    elif any(w in fact_lower for w in ["my name", "i am", "i'm a", "i live", "my wife", "my partner"]):
        return "personal"
    return "general"

def process_remember_command(user_message: str) -> Optional[str]:
    """Check for 'remember/forget' commands. Returns confirmation or None."""
    for pattern, source in REMEMBER_PATTERNS:
        match = re.search(pattern, user_message, re.IGNORECASE)
        if match:
            fact = match.group(1).strip().rstrip('.')
            topic = detect_topic(fact)
            add_memory(fact, topic=topic, source=source)
            return f"✓ Remembered [{topic}]: {fact}"

    for pattern in FORGET_PATTERNS:
        match = re.search(pattern, user_message, re.IGNORECASE)
        if match:
            search_term = match.group(1).strip().rstrip('.')
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
        weather_match = re.search(r"(?:weather|temperature|forecast)\s+(?:in\s+)?(.+?)(?:\s+right now|\s+today|\s+degrees)?$", query, re.IGNORECASE)
        if weather_match or "weather" in query.lower() or "temperature" in query.lower():
            location = weather_match.group(1) if weather_match else re.sub(r"(weather|temperature|forecast|right now|today|degrees)", "", query, flags=re.IGNORECASE).strip()
            if location:
                try:
                    resp = await client.get(f"https://wttr.in/{location}?format=3", timeout=10.0, headers={"User-Agent": "curl/7.68.0"})
                    if resp.status_code == 200:
                        return [{"title": "Current Weather", "url": f"https://wttr.in/{location}", "content": resp.text.strip()}]
                except Exception as e:
                    log.warning(f"wttr.in error: {e}")

        try:
            resp = await client.get(f"{SEARXNG_BASE}/search", params={"q": query, "format": "json", "categories": "general"}, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for answer in data.get("answers", []):
                    results.append({"title": "Direct Answer", "url": "", "content": answer})
                for box in data.get("infoboxes", []):
                    content = box.get("content", "")
                    if not content and box.get("attributes"):
                        content = " | ".join([f"{a.get('label','')}: {a.get('value','')}" for a in box["attributes"]])
                    results.append({"title": box.get("infobox", "Info"), "url": box.get("urls", [{}])[0].get("url", "") if box.get("urls") else "", "content": content})
                for r in data.get("results", [])[:max_results]:
                    results.append({"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")})
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
        if r['content']:
            lines.append(f"   {r['content']}")
        lines.append("")
    lines.append("\nAnswer directly using the data above. No apologies. No disclaimers. Just answer.")
    return "\n".join(lines)

def extract_search_query(user_message: str) -> str:
    query = user_message.strip()
    if re.search(r"temperature|weather", query, re.IGNORECASE):
        query = re.sub(r"^what('?s| is) the ", "", query, flags=re.IGNORECASE) + " right now degrees"
    if re.search(r"price|spot price", query, re.IGNORECASE):
        query = re.sub(r"^(what('?s| is)|can you tell me) the ", "", query, flags=re.IGNORECASE) + " today USD"
    query = re.sub(r"^(what|who|where|when|why|how|is|are|can|could|would|should|do|does|did)\s+", "", query, flags=re.IGNORECASE)
    query = re.sub(r"[?!.]+$", "", query)
    return query[:100].strip() or user_message[:100]


# =============================================================================
# GPU STATS
# =============================================================================

def get_gpu_stats() -> dict:
    """Get AMD GPU stats via rocm-smi."""
    try:
        result = subprocess.run(["rocm-smi", "--showuse", "--showmemuse", "--json"], capture_output=True, text=True, timeout=5)
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

# Mount static files
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


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
    body = await request.json()
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
            resp = await client.get(f"{SEARXNG_BASE}/search", params={"q": "test", "format": "json"}, timeout=5)
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
    body = await request.json()
    rowid = add_memory(fact=body["fact"], topic=body.get("topic", "general"), source=body.get("source", "manual"))
    return {"rowid": rowid, "status": "ok"}

@app.delete("/api/memories/{rowid}")
async def remove_memory(rowid: int):
    if not delete_memory(rowid):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "ok"}

@app.put("/api/memories/{rowid}")
async def edit_memory(rowid: int, request: Request):
    body = await request.json()
    if not update_memory(rowid, body["fact"]):
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
    topics = db.execute("SELECT topic, COUNT(*) as c FROM memories GROUP BY topic ORDER BY c DESC").fetchall()
    db.close()
    return {"total": total, "by_topic": {row["topic"]: row["c"] for row in topics}}


# --- Profile ---

@app.get("/api/profile")
async def get_profile():
    db = get_db()
    row = db.execute("SELECT content, updated_at FROM profile WHERE id = 1").fetchone()
    db.close()
    return {"content": row["content"], "updated_at": row["updated_at"]} if row else {"content": "", "updated_at": ""}

@app.put("/api/profile")
async def update_profile(request: Request):
    body = await request.json()
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute("UPDATE profile SET content = ?, updated_at = ? WHERE id = 1", (body["content"], now))
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
    body = await request.json()
    db = get_db()
    for key, value in body.items():
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    db.commit()
    db.close()
    return {"status": "ok"}


# --- System Presets ---

@app.get("/api/presets")
async def list_presets():
    db = get_db()
    rows = db.execute("SELECT * FROM system_presets ORDER BY is_default DESC, name ASC").fetchall()
    db.close()
    return [dict(r) for r in rows]

@app.post("/api/presets")
async def create_preset(request: Request):
    body = await request.json()
    preset_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute("INSERT INTO system_presets (id, name, prompt, is_default, created_at) VALUES (?, ?, ?, 0, ?)",
               (preset_id, body["name"], body["prompt"], now))
    db.commit()
    db.close()
    return {"id": preset_id, "name": body["name"], "prompt": body["prompt"]}

@app.put("/api/presets/{preset_id}")
async def update_preset(preset_id: str, request: Request):
    body = await request.json()
    db = get_db()
    db.execute("UPDATE system_presets SET name = ?, prompt = ? WHERE id = ?", (body["name"], body["prompt"], preset_id))
    db.commit()
    db.close()
    return {"status": "ok"}

@app.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str):
    db = get_db()
    db.execute("DELETE FROM system_presets WHERE id = ? AND is_default = 0", (preset_id,))
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
    body = await request.json()
    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    model = body.get("model", DEFAULT_MODEL)
    title = body.get("title", "New Chat")
    db = get_db()
    db.execute("INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
               (conv_id, title, model, now, now))
    db.commit()
    db.close()
    return {"id": conv_id, "title": title, "model": model, "created_at": now, "updated_at": now}

@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    db = get_db()
    conv = db.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if not conv:
        db.close()
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = db.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", (conv_id,)).fetchall()
    db.close()
    return {"conversation": dict(conv), "messages": [dict(m) for m in messages]}

@app.put("/api/conversations/{conv_id}")
async def update_conversation(conv_id: str, request: Request):
    body = await request.json()
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    if "title" in body:
        db.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (body["title"], now, conv_id))
    if "model" in body:
        db.execute("UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?", (body["model"], now, conv_id))
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
    body = await request.json()
    query = body.get("query", "").strip()
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
            
            # Save to DB
            db2 = get_db()
            db2.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                        (conv_id, "assistant", error_msg, datetime.now(timezone.utc).isoformat()))
            db2.commit()
            db2.close()
            
            yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id})}\n\n"
            return

        yield f"data: {json.dumps({'search_results': len(results), 'conversation_id': conv_id})}\n\n"

        # Ask Ollama to summarize
        search_context = format_search_results(results)
        messages = [
            {"role": "system", "content": f"You have access to current web data. Answer directly using ONLY the data below. Be concise. No apologies. No disclaimers.\n\n{search_context}"},
            {"role": "user", "content": query}
        ]

        full_response = []
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream("POST", f"{OLLAMA_BASE}/api/chat",
                                          json={"model": model, "messages": messages, "stream": True},
                                          timeout=httpx.Timeout(300.0, connect=10.0)) as resp:
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
                log.error(f"Ollama error during search summarization: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                return

        summary = "".join(full_response)
        
        saved_msg = f"{summary}\n\n---\n*🔍 Web search results*"

        db2 = get_db()
        db2.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (conv_id, "assistant", saved_msg, datetime.now(timezone.utc).isoformat()))
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

    if extra_prompt and extra_prompt.strip():
        parts.append(extra_prompt.strip())

    return "\n\n---\n\n".join(parts) if parts else ""


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    conv_id = body.get("conversation_id")
    user_message = body.get("message", "").strip()
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

    history_rows = db.execute("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC", (conv_id,)).fetchall()
    system_prompt = build_system_prompt(db, preset_prompt, user_message)
    db.close()

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for row in history_rows:
        messages.append({"role": row["role"], "content": row["content"]})

    ollama_payload = {"model": model, "messages": messages, "stream": True, "logprobs": True}

    async def stream_response():
        full_response = []
        all_logprobs = []
        tokens_per_sec = 0.0

        if remember_response:
            yield f"data: {json.dumps({'token': remember_response + chr(10) + chr(10), 'conversation_id': conv_id})}\n\n"

        async with httpx.AsyncClient() as client:
            try:
                async with client.stream("POST", f"{OLLAMA_BASE}/api/chat", json=ollama_payload,
                                          timeout=httpx.Timeout(300.0, connect=10.0)) as resp:
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
                                    tokens_per_sec = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0
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
                            augmented_messages.append({"role": "system", "content": system_prompt + "\n\n" + search_context})
                        else:
                            augmented_messages.append({"role": "system", "content": search_context})
                        for row in history_rows[:-1]:
                            augmented_messages.append({"role": row["role"], "content": row["content"]})
                        augmented_messages.append({"role": "user", "content": user_message})

                        yield f"data: {json.dumps({'search_results': len(search_results), 'conversation_id': conv_id})}\n\n"

                        augmented_response = []
                        async with client.stream("POST", f"{OLLAMA_BASE}/api/chat",
                                                  json={"model": model, "messages": augmented_messages, "stream": True},
                                                  timeout=httpx.Timeout(300.0, connect=10.0)) as resp2:
                            async for line in resp2.aiter_lines():
                                if line.strip():
                                    try:
                                        chunk = json.loads(line)
                                        if "message" in chunk and "content" in chunk["message"]:
                                            augmented_response.append(chunk["message"]["content"])
                                        if chunk.get("done"):
                                            break
                                    except json.JSONDecodeError:
                                        pass

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

            except httpx.ConnectError:
                yield f"data: {json.dumps({'error': 'Cannot connect to Ollama. Is it running?'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
