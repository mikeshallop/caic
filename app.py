#!/usr/bin/env python3
"""
JarvisChat - Lightweight Ollama Coding Companion
A minimal replacement for Open-WebUI that actually runs on Python 3.13
Talks to Ollama API on localhost:11434

Features:
  - Persistent profile/memory injected into every conversation
  - Saved system prompt presets (coding assistant, sysadmin, general, custom)
  - Streaming chat with conversation history
  - Model switching between all installed Ollama models
  - Copy-to-clipboard on code blocks
  - Token count estimates
  - SearXNG integration for web search when model is uncertain
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

import httpx
import psutil
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

# --- Logging Setup ---
import logging.handlers

log = logging.getLogger("jarvischat")
log.setLevel(logging.DEBUG)
syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
syslog_handler.setFormatter(logging.Formatter('jarvischat[%(process)d]: %(levelname)s %(message)s'))
log.addHandler(syslog_handler)

# --- Configuration ---
VERSION = "1.3.1"
OLLAMA_BASE = "http://localhost:11434"
SEARXNG_BASE = "http://localhost:8888"
DB_PATH = Path(__file__).parent / "jarvischat.db"
DEFAULT_MODEL = "deepseek-coder:6.7b"

# --- Perplexity Threshold ---
# Higher perplexity = model is less confident / more uncertain
# Tune this based on your models. Start conservative (higher threshold).
PERPLEXITY_THRESHOLD = 15.0

# --- Refusal Patterns (fallback for confident refusals) ---
REFUSAL_PATTERNS = re.compile(r"|".join([
    r"i don'?t have (?:real-?time|current|live)",
    r"i (?:can'?t|cannot) provide (?:current|real-?time|live)",
    r"i don'?t have access to (?:current|real-?time|live)",
    r"(?:current|live|real-?time) (?:data|information|prices?|weather)",
    r"my (?:knowledge|training) (?:cutoff|only goes|ends)",
    r"as of my (?:knowledge|training) cutoff",
    r"i'?m not able to (?:access|provide|browse)",
    r"(?:check|visit|use) a (?:website|financial|news)",
]), re.IGNORECASE)

# --- Hedging patterns to strip from search-augmented responses ---
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
    
    lines = [f"Here's what I found:\n"]
    for r in results[:3]:  # Top 3 results
        lines.append(f"**{r['title']}**")
        if r['content']:
            lines.append(f"{r['content']}")
        lines.append("")
    
    return "\n".join(lines).strip()

# --- Default Profile ---
DEFAULT_PROFILE = """You are a coding companion running locally on a machine called "jarvis".

## Environment
- jarvis: Debian 13 (trixie) x86_64, AMD Ryzen 5 5600X, 16GB RAM, AMD RX 6600 XT (8GB VRAM), IP varies
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
- Working on "Sysadmin's Wizard's Notebook" app concept in Rust
- Veteran on fixed income — prefers free/open-source solutions
- Home lab enthusiast with Z-Wave and Tapo smart home devices
- Streams Fortnite on a regular schedule

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
        "prompt": "You are a senior software engineer and coding companion. Focus on writing clean, efficient, well-documented code. Provide complete working examples. Explain architectural decisions and trade-offs. Prefer Rust, Python, and bash."
    },
    {
        "name": "Linux Sysadmin",
        "prompt": "You are an experienced Linux systems administrator. Focus on command-line solutions, systemd services, networking, storage, and security. Prefer Debian/Ubuntu conventions. Be concise and direct."
    },
    {
        "name": "General Assistant",
        "prompt": "You are a helpful general-purpose assistant. Be clear and concise."
    }
]

# --- Database Setup ---
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

    # Seed default profile if empty
    existing = conn.execute("SELECT id FROM profile WHERE id = 1").fetchone()
    if not existing:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT INTO profile (id, content, updated_at) VALUES (1, ?, ?)",
                      (DEFAULT_PROFILE, now))

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
    defaults = {
        "profile_enabled": "true",
        "default_model": DEFAULT_MODEL,
        "search_enabled": "true",
    }
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

# --- SearXNG Integration ---
async def query_searxng(query: str, max_results: int = 5) -> list[dict]:
    """Query SearXNG and return search results."""
    log.info(f"Querying SearXNG: '{query}'")
    async with httpx.AsyncClient() as client:
        # For weather queries, hit wttr.in directly
        weather_match = re.search(r"(?:weather|temperature|forecast)\s+(?:in\s+)?(.+?)(?:\s+right now|\s+today|\s+degrees)?$", query, re.IGNORECASE)
        if weather_match or "weather" in query.lower() or "temperature" in query.lower():
            location = weather_match.group(1) if weather_match else re.sub(r"(weather|temperature|forecast|right now|today|degrees)", "", query, flags=re.IGNORECASE).strip()
            if location:
                try:
                    log.info(f"Fetching weather for: {location}")
                    resp = await client.get(
                        f"https://wttr.in/{location}?format=3",
                        timeout=10.0,
                        headers={"User-Agent": "curl/7.68.0"}
                    )
                    if resp.status_code == 200:
                        weather_text = resp.text.strip()
                        log.info(f"wttr.in returned: {weather_text}")
                        return [{
                            "title": "Current Weather",
                            "url": f"https://wttr.in/{location}",
                            "content": weather_text,
                        }]
                except Exception as e:
                    log.warning(f"wttr.in error: {e}, falling back to SearXNG")
        
        try:
            resp = await client.get(
                f"{SEARXNG_BASE}/search",
                params={
                    "q": query,
                    "format": "json",
                    "categories": "general",
                },
                timeout=10.0
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                
                # Check for direct answers/infoboxes first
                if data.get("answers"):
                    for answer in data["answers"]:
                        results.append({
                            "title": "Direct Answer",
                            "url": "",
                            "content": answer,
                        })
                        log.info(f"Got direct answer: {answer[:100]}")
                
                if data.get("infoboxes"):
                    for box in data["infoboxes"]:
                        content = box.get("content", "")
                        if not content and box.get("attributes"):
                            content = " | ".join([f"{a.get('label','')}: {a.get('value','')}" for a in box["attributes"]])
                        results.append({
                            "title": box.get("infobox", "Info"),
                            "url": box.get("urls", [{}])[0].get("url", "") if box.get("urls") else "",
                            "content": content,
                        })
                        log.info(f"Got infobox: {box.get('infobox', '')}")
                
                # Then regular results
                for r in data.get("results", [])[:max_results]:
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": r.get("content", ""),
                    })
                
                log.info(f"SearXNG returned {len(results)} total results")
                for i, r in enumerate(results[:5]):
                    log.debug(f"  Result {i+1}: {r['title'][:60]}")
                return results
            else:
                log.warning(f"SearXNG returned status {resp.status_code}")
        except Exception as e:
            log.error(f"SearXNG error: {e}")
    return []

def calculate_perplexity(logprobs: list) -> float:
    """Calculate perplexity from logprobs. Higher = less confident."""
    if not logprobs:
        return 0.0
    avg_logprob = sum(lp["logprob"] for lp in logprobs) / len(logprobs)
    perplexity = math.exp(-avg_logprob)
    return perplexity

def is_uncertain(logprobs: list, threshold: float = PERPLEXITY_THRESHOLD) -> bool:
    """Check if model output indicates uncertainty based on perplexity."""
    if not logprobs:
        log.debug("No logprobs returned, skipping uncertainty check")
        return False
    perplexity = calculate_perplexity(logprobs)
    log.info(f"Perplexity: {perplexity:.2f} (threshold: {threshold})")
    return perplexity > threshold

def is_refusal(text: str) -> bool:
    """Check if model is refusing/admitting it can't help."""
    match = REFUSAL_PATTERNS.search(text)
    if match:
        log.info(f"Refusal detected: '{match.group()}'")
        return True
    return False

def format_search_results(results: list[dict]) -> str:
    """Format search results as context for the model."""
    if not results:
        return ""
    
    lines = ["[LIVE WEB DATA]\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        if r['content']:
            lines.append(f"   {r['content']}")
        lines.append("")
    
    lines.append("\nAnswer directly using the data above. No apologies. No disclaimers. No \"please verify elsewhere.\" Just answer.")
    return "\n".join(lines)

def extract_search_query(user_message: str) -> str:
    """Extract a good search query from the user's message."""
    query = user_message.strip()
    
    # For temperature/weather queries, be more specific
    if re.search(r"temperature|weather", query, re.IGNORECASE):
        query = re.sub(r"^what('?s| is) the ", "", query, flags=re.IGNORECASE)
        query = query + " right now degrees"
    
    # For price queries, be more specific  
    if re.search(r"price|spot price", query, re.IGNORECASE):
        query = re.sub(r"^(what('?s| is)|can you tell me) the ", "", query, flags=re.IGNORECASE)
        query = query + " today USD"
    
    # Remove common question words
    query = re.sub(r"^(what|who|where|when|why|how|is|are|can|could|would|should|do|does|did)\s+", "", query, flags=re.IGNORECASE)
    # Remove trailing punctuation
    query = re.sub(r"[?!.]+$", "", query)
    # Limit length
    if len(query) > 100:
        query = query[:100]
    return query.strip() or user_message[:100]

# --- App Lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"JarvisChat v{VERSION} starting up")
    log.info(f"Ollama: {OLLAMA_BASE}")
    log.info(f"SearXNG: {SEARXNG_BASE}")
    init_db()
    yield
    log.info("JarvisChat shutting down")

app = FastAPI(title="JarvisChat", lifespan=lifespan)

# --- API Routes ---

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE.replace("{{VERSION}}", VERSION)

@app.get("/api/models")
async def list_models():
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags", timeout=10)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to Ollama. Is it running?")

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
    """Get model information including context size."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{OLLAMA_BASE}/api/show", json=body, timeout=10)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to Ollama.")

# --- Search Status ---
@app.get("/api/search/status")
async def search_status():
    """Check if SearXNG is available."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{SEARXNG_BASE}/healthz", timeout=5)
            return {"available": resp.status_code == 200}
        except:
            # Try a simple search as fallback health check
            try:
                resp = await client.get(f"{SEARXNG_BASE}/search", params={"q": "test", "format": "json"}, timeout=5)
                return {"available": resp.status_code == 200}
            except:
                return {"available": False}

# --- System Stats ---

def get_gpu_stats() -> dict:
    """Get AMD GPU stats via rocm-smi."""
    try:
        result = subprocess.run(
            ["rocm-smi", "--showuse", "--showmemuse", "--json"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            # Parse rocm-smi JSON output
            gpu_info = data.get("card0", {})
            gpu_use = gpu_info.get("GPU use (%)", 0)
            vram_use = gpu_info.get("GPU Memory Allocated (VRAM%)", 0)
            # Handle string or int values
            if isinstance(gpu_use, str):
                gpu_use = int(gpu_use.replace("%", "").strip() or 0)
            if isinstance(vram_use, str):
                vram_use = int(vram_use.replace("%", "").strip() or 0)
            return {"gpu_percent": gpu_use, "vram_percent": vram_use, "available": True}
    except subprocess.TimeoutExpired:
        log.warning("rocm-smi timed out")
    except FileNotFoundError:
        log.debug("rocm-smi not found")
    except json.JSONDecodeError:
        # Fallback: parse text output
        try:
            result = subprocess.run(
                ["rocm-smi", "--showuse", "--showmemuse"],
                capture_output=True, text=True, timeout=5
            )
            gpu_use = 0
            vram_use = 0
            for line in result.stdout.split("\n"):
                if "GPU use (%)" in line:
                    match = re.search(r"(\d+)", line.split(":")[-1])
                    if match:
                        gpu_use = int(match.group(1))
                elif "GPU Memory Allocated (VRAM%)" in line:
                    match = re.search(r"(\d+)", line.split(":")[-1])
                    if match:
                        vram_use = int(match.group(1))
            return {"gpu_percent": gpu_use, "vram_percent": vram_use, "available": True}
        except Exception as e:
            log.warning(f"rocm-smi parse error: {e}")
    except Exception as e:
        log.warning(f"GPU stats error: {e}")
    return {"gpu_percent": 0, "vram_percent": 0, "available": False}

@app.get("/api/stats")
async def system_stats():
    """Get system resource usage (CPU, memory, GPU)."""
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

# --- Profile ---

@app.get("/api/profile")
async def get_profile():
    db = get_db()
    row = db.execute("SELECT content, updated_at FROM profile WHERE id = 1").fetchone()
    db.close()
    if row:
        return {"content": row["content"], "updated_at": row["updated_at"]}
    return {"content": "", "updated_at": ""}

@app.put("/api/profile")
async def update_profile(request: Request):
    body = await request.json()
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute("UPDATE profile SET content = ?, updated_at = ? WHERE id = 1",
               (body["content"], now))
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
    db.execute(
        "INSERT INTO system_presets (id, name, prompt, is_default, created_at) VALUES (?, ?, ?, 0, ?)",
        (preset_id, body["name"], body["prompt"], now)
    )
    db.commit()
    db.close()
    return {"id": preset_id, "name": body["name"], "prompt": body["prompt"]}

@app.put("/api/presets/{preset_id}")
async def update_preset(preset_id: str, request: Request):
    body = await request.json()
    db = get_db()
    db.execute("UPDATE system_presets SET name = ?, prompt = ? WHERE id = ?",
               (body["name"], body["prompt"], preset_id))
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

# --- Conversation CRUD ---

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
    db.execute(
        "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (conv_id, title, model, now, now)
    )
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
    messages = db.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", (conv_id,)
    ).fetchall()
    db.close()
    return {"conversation": dict(conv), "messages": [dict(m) for m in messages]}

@app.put("/api/conversations/{conv_id}")
async def update_conversation(conv_id: str, request: Request):
    body = await request.json()
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    if "title" in body:
        db.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                    (body["title"], now, conv_id))
    if "model" in body:
        db.execute("UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
                    (body["model"], now, conv_id))
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

# --- Chat (streaming) ---

def build_system_prompt(db, extra_prompt=""):
    """Build the full system prompt: profile + preset/custom prompt"""
    parts = []

    # Check if profile is enabled
    settings = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings").fetchall()}
    if settings.get("profile_enabled", "true") == "true":
        profile = db.execute("SELECT content FROM profile WHERE id = 1").fetchone()
        if profile and profile["content"].strip():
            parts.append(profile["content"].strip())

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

    # Check if search is enabled
    settings = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings").fetchall()}
    search_enabled = settings.get("search_enabled", "true") == "true"
    log.debug(f"Chat request: model={model}, search_enabled={search_enabled}")

    # Auto-create conversation if needed
    if not conv_id:
        conv_id = str(uuid.uuid4())
        title = user_message[:80] + ("..." if len(user_message) > 80 else "")
        db.execute(
            "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, title, model, now, now)
        )
    else:
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))

    # Save user message
    db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conv_id, "user", user_message, now)
    )
    db.commit()

    # Build message history
    history_rows = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC",
        (conv_id,)
    ).fetchall()

    # Build system prompt (profile + preset)
    system_prompt = build_system_prompt(db, preset_prompt)
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
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE}/api/chat",
                    json=ollama_payload,
                    timeout=httpx.Timeout(300.0, connect=10.0)
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            try:
                                chunk = json.loads(line)
                                if "message" in chunk and "content" in chunk["message"]:
                                    token = chunk["message"]["content"]
                                    full_response.append(token)
                                    yield f"data: {json.dumps({'token': token, 'conversation_id': conv_id})}\n\n"
                                # Collect logprobs
                                if "logprobs" in chunk and chunk["logprobs"]:
                                    all_logprobs.extend(chunk["logprobs"])
                                if chunk.get("done"):
                                    # Capture timing info from final chunk
                                    eval_count = chunk.get("eval_count", 0)
                                    eval_duration = chunk.get("eval_duration", 0)
                                    tokens_per_sec = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0
                                    break
                            except json.JSONDecodeError:
                                pass

                # Check for uncertainty and search if needed
                assistant_msg = "".join(full_response)
                perplexity = calculate_perplexity(all_logprobs) if all_logprobs else 0.0
                should_search = is_uncertain(all_logprobs) or is_refusal(assistant_msg)
                
                if search_enabled and should_search:
                    # Signal that we're searching
                    yield f"data: {json.dumps({'searching': True, 'conversation_id': conv_id})}\n\n"
                    
                    # Query SearXNG
                    search_query = extract_search_query(user_message)
                    log.info(f"Extracted search query: '{search_query}'")
                    search_results = await query_searxng(search_query)
                    
                    if search_results:
                        # Build augmented messages - inject search context, DON'T include the refusal
                        search_context = format_search_results(search_results)
                        
                        # Rebuild: system prompt + search context + original user question
                        augmented_messages = []
                        if system_prompt:
                            augmented_messages.append({"role": "system", "content": system_prompt + "\n\n" + search_context})
                        else:
                            augmented_messages.append({"role": "system", "content": search_context})
                        
                        # Add conversation history except the last user message (we'll re-add it)
                        for row in history_rows[:-1]:
                            augmented_messages.append({"role": row["role"], "content": row["content"]})
                        
                        # Re-add the user question
                        augmented_messages.append({"role": "user", "content": user_message})
                        
                        augmented_payload = {
                            "model": model,
                            "messages": augmented_messages,
                            "stream": True,
                        }
                        
                        # Signal search results found - include actual results for debug
                        yield f"data: {json.dumps({'search_results': len(search_results), 'results_preview': [r['title'] for r in search_results], 'conversation_id': conv_id})}\n\n"
                        
                        # Stream the augmented response
                        yield f"data: {json.dumps({'debug': 'Starting augmented response...', 'conversation_id': conv_id})}\n\n"
                        augmented_response = []
                        async with client.stream(
                            "POST",
                            f"{OLLAMA_BASE}/api/chat",
                            json=augmented_payload,
                            timeout=httpx.Timeout(300.0, connect=10.0)
                        ) as resp2:
                            async for line in resp2.aiter_lines():
                                if line.strip():
                                    try:
                                        chunk = json.loads(line)
                                        if "message" in chunk and "content" in chunk["message"]:
                                            token = chunk["message"]["content"]
                                            augmented_response.append(token)
                                        if chunk.get("done"):
                                            break
                                    except json.JSONDecodeError:
                                        pass
                        
                        # Clean hedging from the response
                        raw_response = "".join(augmented_response)
                        if not raw_response.strip():
                            log.warning("Augmented response empty, falling back to original")
                            raw_response = assistant_msg
                        cleaned_response = clean_hedging(raw_response)
                        log.debug(f"Cleaned hedging: {len(raw_response)} -> {len(cleaned_response)} chars")
                        
                        # If model STILL refuses after getting search data, format answer ourselves
                        if is_refusal(cleaned_response) or len(cleaned_response) < 20:
                            log.warning("Model refused even with search context, formatting direct answer")
                            cleaned_response = format_direct_answer(user_message, search_results)
                        
                        # Send cleaned response as single chunk
                        yield f"data: {json.dumps({'token': cleaned_response, 'conversation_id': conv_id, 'augmented': True})}\n\n"
                        
                        # Save the cleaned response
                        search_note = "\n\n---\n*🔍 Enhanced with web search results*"
                        saved_msg = cleaned_response + search_note
                        
                        db2 = get_db()
                        db2.execute(
                            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                            (conv_id, "assistant", saved_msg, datetime.now(timezone.utc).isoformat())
                        )
                        db2.commit()
                        db2.close()
                        
                        yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'searched': True, 'perplexity': round(perplexity, 2), 'tokens_per_sec': round(tokens_per_sec, 1)})}\n\n"
                        return
                
                # No search needed - save original response
                db2 = get_db()
                db2.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (conv_id, "assistant", assistant_msg, datetime.now(timezone.utc).isoformat())
                )
                db2.commit()
                db2.close()
                yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'perplexity': round(perplexity, 2), 'tokens_per_sec': round(tokens_per_sec, 1)})}\n\n"
                
            except httpx.ConnectError:
                yield f"data: {json.dumps({'error': 'Cannot connect to Ollama. Is it running?'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")

# =====================================================================
# FRONTEND
# =====================================================================

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JarvisChat</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root {
    --bg-primary: #0a0e14;
    --bg-secondary: #111820;
    --bg-tertiary: #1a2230;
    --bg-hover: #1e2a3a;
    --text-primary: #c8d6e5;
    --text-secondary: #7f8fa6;
    --text-muted: #4a5568;
    --accent: #48b5e0;
    --accent-dim: #2a6f8a;
    --accent-glow: rgba(72, 181, 224, 0.15);
    --danger: #e74c3c;
    --danger-hover: #c0392b;
    --success: #2ecc71;
    --warning: #f39c12;
    --border: #1e2a3a;
    --scrollbar: #2a3a4a;
    --radius: 8px;
    --font-body: 'IBM Plex Sans', -apple-system, sans-serif;
    --font-mono: 'JetBrains Mono', 'Consolas', monospace;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: var(--font-body); background: var(--bg-primary); color: var(--text-primary); height: 100vh; overflow: hidden; display: flex; }
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--scrollbar); border-radius: 3px; }

/* Sidebar */
.sidebar { width: 280px; min-width: 280px; background: var(--bg-secondary); border-right: 1px solid var(--border); display: flex; flex-direction: column; height: 100vh; }
.sidebar-header { padding: 20px 16px 12px; border-bottom: 1px solid var(--border); text-align: center; }
.sidebar-header .logo { width: 100%; max-width: 180px; height: auto; margin-bottom: 12px; border-radius: 8px; }
.sidebar-header h1 { font-family: var(--font-mono); font-size: 18px; font-weight: 600; color: var(--accent); letter-spacing: 1px; margin-bottom: 4px; }
.sidebar-header .subtitle { font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); margin-bottom: 12px; }
.btn-row { display: flex; gap: 6px; }
.new-chat-btn, .settings-btn { padding: 10px 14px; background: var(--accent-glow); border: 1px solid var(--accent-dim); border-radius: var(--radius); color: var(--accent); font-family: var(--font-body); font-size: 13px; font-weight: 500; cursor: pointer; transition: all 0.2s; }
.new-chat-btn { flex: 1; }
.settings-btn { padding: 10px 12px; }
.new-chat-btn:hover, .settings-btn:hover { background: var(--accent-dim); color: #fff; }
.delete-all-btn { padding: 10px 12px; background: transparent; border: 1px solid var(--danger); border-radius: var(--radius); color: var(--danger); font-size: 14px; cursor: pointer; transition: all 0.2s; }
.delete-all-btn:hover { background: var(--danger); color: #fff; }
.conversation-list { flex: 1; overflow-y: auto; padding: 8px; }
.conv-item { padding: 10px 12px; border-radius: var(--radius); cursor: pointer; margin-bottom: 2px; display: flex; justify-content: space-between; align-items: center; transition: background 0.15s; font-size: 13px; color: var(--text-secondary); }
.conv-item:hover { background: var(--bg-hover); color: var(--text-primary); }
.conv-item.active { background: var(--bg-tertiary); color: var(--text-primary); }
.conv-item .conv-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.conv-item .conv-delete { opacity: 0; color: var(--danger); cursor: pointer; padding: 2px 6px; font-size: 16px; }
.conv-item:hover .conv-delete { opacity: 0.7; }
.conv-item .conv-delete:hover { opacity: 1; }
.sidebar-footer { padding: 12px 16px; border-top: 1px solid var(--border); font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); }
.sidebar-footer .status-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.stats-panel { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); }
.stat-row { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.stat-label { width: 36px; font-size: 10px; color: var(--text-muted); text-transform: uppercase; }
.stat-bar { flex: 1; height: 8px; background: var(--bg-tertiary); border-radius: 4px; overflow: hidden; }
.stat-fill { height: 100%; background: var(--accent); border-radius: 4px; transition: width 0.3s ease, background 0.3s ease; width: 0%; }
.stat-fill.gpu { background: var(--success); }
.stat-fill.warn { background: var(--warning); }
.stat-fill.danger { background: var(--danger); }
.stat-value { width: 32px; text-align: right; font-size: 10px; }

/* Main */
.main { flex: 1; display: flex; flex-direction: column; height: 100vh; min-width: 0; }
.topbar { display: flex; align-items: center; justify-content: space-between; padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--bg-secondary); gap: 12px; }
.topbar-left { display: flex; align-items: center; gap: 12px; }
.topbar-right { display: flex; align-items: center; gap: 8px; }
.topbar select { background: var(--bg-tertiary); border: 1px solid var(--border); color: var(--text-primary); font-family: var(--font-mono); font-size: 13px; padding: 6px 10px; border-radius: var(--radius); cursor: pointer; }
.topbar-label { font-size: 12px; color: var(--text-muted); font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 1px; }
.profile-badge, .search-badge { font-size: 11px; padding: 4px 10px; border-radius: 12px; font-family: var(--font-mono); cursor: pointer; border: none; transition: all 0.2s; }
.profile-badge.on, .search-badge.on { background: rgba(46,204,113,0.15); color: var(--success); border: 1px solid rgba(46,204,113,0.3); }
.profile-badge.off, .search-badge.off { background: rgba(231,76,60,0.15); color: var(--danger); border: 1px solid rgba(231,76,60,0.3); }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success); display: inline-block; animation: pulse 2s infinite; }
.status-dot.offline { background: var(--danger); animation: none; }
.status-dot.warning { background: var(--warning); animation: none; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

/* Modal */
.modal-overlay { display:none; position:fixed; top:0;left:0;right:0;bottom:0; background:rgba(0,0,0,0.7); z-index:1000; align-items:center; justify-content:center; }
.modal-overlay.visible { display:flex; }
.modal { background:var(--bg-secondary); border:1px solid var(--border); border-radius:12px; width:90%; max-width:700px; max-height:85vh; overflow-y:auto; }
.modal-header { display:flex; justify-content:space-between; align-items:center; padding:20px 24px 16px; border-bottom:1px solid var(--border); position:sticky; top:0; background:var(--bg-secondary); z-index:1; }
.modal-header h2 { font-family:var(--font-mono); font-size:16px; color:var(--accent); }
.modal-close { background:none; border:none; color:var(--text-muted); font-size:24px; cursor:pointer; }
.modal-close:hover { color:var(--text-primary); }
.modal-body { padding: 20px 24px; }
.modal-section { margin-bottom: 24px; }
.modal-section h3 { font-family:var(--font-mono); font-size:13px; color:var(--text-secondary); text-transform:uppercase; letter-spacing:1px; margin-bottom:8px; }
.modal-section p.desc { font-size:12px; color:var(--text-muted); margin-bottom:10px; line-height:1.5; }
.modal-section textarea { width:100%; background:var(--bg-tertiary); border:1px solid var(--border); color:var(--text-primary); font-family:var(--font-mono); font-size:12px; padding:12px; border-radius:var(--radius); resize:vertical; line-height:1.6; }
.modal-section textarea:focus { outline:none; border-color:var(--accent-dim); }
.token-count { font-size:11px; color:var(--text-muted); font-family:var(--font-mono); margin-top:4px; text-align:right; }
.toggle-row { display:flex; align-items:center; justify-content:space-between; padding:8px 0; }
.toggle-label { font-size:13px; }
.toggle-switch { position:relative; width:44px; height:24px; background:var(--bg-tertiary); border:1px solid var(--border); border-radius:12px; cursor:pointer; transition:background 0.2s; }
.toggle-switch.on { background:var(--accent-dim); border-color:var(--accent-dim); }
.toggle-switch::after { content:''; position:absolute; top:2px; left:2px; width:18px; height:18px; background:var(--text-primary); border-radius:50%; transition:transform 0.2s; }
.toggle-switch.on::after { transform:translateX(20px); }
.btn-small { padding:6px 14px; border-radius:var(--radius); font-family:var(--font-mono); font-size:12px; cursor:pointer; border:1px solid var(--border); transition:all 0.2s; }
.btn-save { background:var(--accent-dim); color:#fff; border-color:var(--accent-dim); }
.btn-save:hover { background:var(--accent); }
.btn-reset { background:transparent; color:var(--text-muted); }
.btn-reset:hover { color:var(--danger); border-color:var(--danger); }
.btn-bar { display:flex; gap:8px; margin-top:10px; }
.preset-item { display:flex; align-items:center; gap:8px; padding:8px 10px; background:var(--bg-tertiary); border-radius:var(--radius); margin-bottom:6px; font-size:13px; }
.preset-item .preset-name { flex:1; color:var(--text-primary); }
.preset-item button { background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:13px; padding:2px 4px; }
.preset-item button:hover { color:var(--text-primary); }

/* Chat */
.chat-container { flex:1; overflow-y:auto; padding:20px; display:flex; flex-direction:column; gap:16px; }
.welcome-screen { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; color:var(--text-muted); text-align:center; gap:12px; }
.welcome-screen .logo { font-family:var(--font-mono); font-size:48px; color:var(--accent-dim); opacity:0.5; }
.welcome-screen p { font-size:14px; max-width:420px; line-height:1.6; }
.message { display:flex; gap:12px; max-width:900px; width:100%; margin:0 auto; animation:fadeIn 0.2s ease; }
@keyframes fadeIn { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
.message .avatar { width:32px; height:32px; min-width:32px; border-radius:6px; display:flex; align-items:center; justify-content:center; font-family:var(--font-mono); font-size:13px; font-weight:600; margin-top:2px; }
.message.user .avatar { background:#1a3a5c; color:var(--accent); }
.message.assistant .avatar { background:var(--accent-dim); color:#fff; }
.message .content { flex:1; min-width:0; }
.message .content .role-label { font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px; color:var(--text-muted); font-family:var(--font-mono); }
.message .content .text { font-size:14px; line-height:1.65; word-wrap:break-word; overflow-wrap:break-word; }
.message .content .text pre { background:var(--bg-primary); border:1px solid var(--border); border-radius:var(--radius); padding:12px; margin:8px 0; overflow-x:auto; font-family:var(--font-mono); font-size:13px; line-height:1.5; position:relative; }
.message .content .text code { font-family:var(--font-mono); background:var(--bg-primary); padding:2px 5px; border-radius:3px; font-size:13px; }
.message .content .text pre code { background:none; padding:0; }
.copy-btn { position:absolute; top:6px; right:6px; background:var(--bg-tertiary); border:1px solid var(--border); color:var(--text-muted); font-family:var(--font-mono); font-size:11px; padding:3px 8px; border-radius:4px; cursor:pointer; }
.copy-btn:hover { color:var(--text-primary); }
.typing-indicator { display:inline-flex; gap:4px; padding:4px 0; }
.typing-indicator span { width:6px; height:6px; background:var(--accent-dim); border-radius:50%; animation:blink 1.4s infinite; }
.typing-indicator span:nth-child(2) { animation-delay:0.2s; }
.typing-indicator span:nth-child(3) { animation-delay:0.4s; }
@keyframes blink { 0%,80%,100%{opacity:0.3} 40%{opacity:1} }
.search-indicator { display:inline-flex; align-items:center; gap:8px; padding:8px 12px; background:rgba(243,156,18,0.15); border:1px solid rgba(243,156,18,0.3); border-radius:var(--radius); color:var(--warning); font-family:var(--font-mono); font-size:12px; margin:8px 0; }
.search-indicator .spinner { width:14px; height:14px; border:2px solid rgba(243,156,18,0.3); border-top-color:var(--warning); border-radius:50%; animation:spin 1s linear infinite; }
@keyframes spin { to{transform:rotate(360deg)} }
.search-badge-inline { display:inline-block; padding:2px 8px; background:rgba(46,204,113,0.15); border:1px solid rgba(46,204,113,0.3); border-radius:10px; color:var(--success); font-family:var(--font-mono); font-size:10px; margin-left:8px; }
.perplexity-badge { display:inline-block; padding:2px 8px; border-radius:10px; font-family:var(--font-mono); font-size:10px; margin-left:8px; }
.perplexity-badge.low { background:rgba(46,204,113,0.15); border:1px solid rgba(46,204,113,0.3); color:var(--success); }
.perplexity-badge.medium { background:rgba(243,156,18,0.15); border:1px solid rgba(243,156,18,0.3); color:var(--warning); }
.perplexity-badge.high { background:rgba(231,76,60,0.15); border:1px solid rgba(231,76,60,0.3); color:var(--danger); }
.tps-badge { display:inline-block; padding:2px 8px; border-radius:10px; font-family:var(--font-mono); font-size:10px; margin-left:8px; background:rgba(72,181,224,0.15); border:1px solid rgba(72,181,224,0.3); color:var(--accent); }

/* Input */
.input-area { padding:16px 20px; border-top:1px solid var(--border); background:var(--bg-secondary); }
.input-row-top { max-width:900px; margin:0 auto 8px; display:flex; gap:8px; align-items:center; }
.input-row-top select { background:var(--bg-tertiary); border:1px solid var(--border); color:var(--text-secondary); font-family:var(--font-mono); font-size:11px; padding:4px 8px; border-radius:var(--radius); cursor:pointer; }
.input-row-top .preset-label { font-size:11px; color:var(--text-muted); font-family:var(--font-mono); }
.input-wrapper { max-width:900px; margin:0 auto; display:flex; gap:10px; align-items:flex-end; }
.input-wrapper textarea { flex:1; background:var(--bg-tertiary); border:1px solid var(--border); color:var(--text-primary); font-family:var(--font-body); font-size:14px; padding:12px 14px; border-radius:var(--radius); resize:none; min-height:44px; max-height:200px; line-height:1.5; }
.input-wrapper textarea:focus { outline:none; border-color:var(--accent-dim); }
.input-wrapper textarea::placeholder { color:var(--text-muted); }
.send-btn { padding:12px 20px; background:var(--accent-dim); border:none; border-radius:var(--radius); color:#fff; font-family:var(--font-mono); font-size:13px; font-weight:600; cursor:pointer; white-space:nowrap; }
.send-btn:hover { background:var(--accent); }
.stop-btn { padding:12px 20px; background:var(--danger); border:none; border-radius:var(--radius); color:#fff; font-family:var(--font-mono); font-size:13px; font-weight:600; cursor:pointer; }
.stop-btn:hover { background:var(--danger-hover); }

/* Token Thermometer */
.token-thermometer { display:flex; flex-direction:column; align-items:center; gap:4px; }
.thermometer-bar { width:12px; height:80px; background:var(--bg-tertiary); border:1px solid var(--border); border-radius:6px; position:relative; overflow:hidden; }
.thermometer-fill { position:absolute; bottom:0; left:0; right:0; background:linear-gradient(to top, var(--success), var(--warning), var(--danger)); transition:height 0.3s ease; border-radius:0 0 5px 5px; }
.thermometer-label { font-family:var(--font-mono); font-size:9px; color:var(--text-muted); writing-mode:vertical-rl; text-orientation:mixed; transform:rotate(180deg); white-space:nowrap; }
.token-info { font-family:var(--font-mono); font-size:10px; color:var(--text-muted); text-align:center; cursor:help; }
.token-info.warning { color:var(--warning); }
.token-info.danger { color:var(--danger); }

@media (max-width:768px) {
    .sidebar { display:none; }
    .topbar { padding:10px 14px; }
    .chat-container { padding:12px; }
    .input-area { padding:10px 12px; }
}
</style>
</head>
<body>

<aside class="sidebar" id="sidebar">
    <div class="sidebar-header">
        <img class="logo" src="data:image/jpeg;base64,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" alt="JarvisChat Logo" />
        <h1>&#9889; JarvisChat {{VERSION}}</h1>
        <div class="subtitle">&#129433; local coding companion</div>
        <div class="btn-row">
            <button class="new-chat-btn" onclick="newChat()">+ New Chat</button>
            <button class="settings-btn" onclick="openSettings()">&#9881;</button>
            <button class="delete-all-btn" onclick="deleteAllConversations()" title="Delete all conversations">&#128465;</button>
        </div>
    </div>
    <div class="conversation-list" id="convList"></div>
    <div class="sidebar-footer">
        <div class="status-row" id="ollamaStatus"><span class="status-dot offline"></span> checking...</div>
        <div class="status-row" id="searchStatus"><span class="status-dot offline"></span> search: checking...</div>
        <div class="stats-panel" id="statsPanel">
            <div class="stat-row">
                <span class="stat-label">CPU</span>
                <div class="stat-bar"><div class="stat-fill" id="cpuFill"></div></div>
                <span class="stat-value" id="cpuValue">--%</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">MEM</span>
                <div class="stat-bar"><div class="stat-fill" id="memFill"></div></div>
                <span class="stat-value" id="memValue">--%</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">GPU</span>
                <div class="stat-bar"><div class="stat-fill gpu" id="gpuFill"></div></div>
                <span class="stat-value" id="gpuValue">--%</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">VRAM</span>
                <div class="stat-bar"><div class="stat-fill gpu" id="vramFill"></div></div>
                <span class="stat-value" id="vramValue">--%</span>
            </div>
        </div>
    </div>
</aside>

<!-- Settings Modal -->
<div class="modal-overlay" id="settingsModal">
    <div class="modal">
        <div class="modal-header">
            <h2>Settings</h2>
            <button class="modal-close" onclick="closeSettings()">&times;</button>
        </div>
        <div class="modal-body">
            <div class="modal-section">
                <h3>Profile / Memory</h3>
                <p class="desc">This context is injected as a system prompt into every conversation. It tells the model who you are, your environment, and how you want responses. Edit freely.</p>
                <div class="toggle-row">
                    <span class="toggle-label">Inject profile into all chats</span>
                    <div class="toggle-switch on" id="profileToggle" onclick="toggleProfile()"></div>
                </div>
                <textarea id="profileEditor" rows="18" spellcheck="false"></textarea>
                <div class="token-count" id="profileTokenCount"></div>
                <div class="btn-bar">
                    <button class="btn-small btn-save" id="saveProfileBtn" onclick="saveProfile()">Save Profile</button>
                    <button class="btn-small btn-reset" onclick="resetProfile()">Reset to Default</button>
                </div>
            </div>

            <div class="modal-section">
                <h3>Web Search (SearXNG)</h3>
                <p class="desc">When enabled, JarvisChat will automatically search the web if the model indicates it doesn't know the answer. Results are injected as context for a better response.</p>
                <div class="toggle-row">
                    <span class="toggle-label">Enable automatic web search</span>
                    <div class="toggle-switch on" id="searchToggle" onclick="toggleSearch()"></div>
                </div>
            </div>

            <div class="modal-section">
                <h3>System Prompt Presets</h3>
                <p class="desc">Presets add extra instructions on top of your profile. Select one in the chat to specialize behavior.</p>
                <div id="presetList"></div>
                <div class="btn-bar" style="margin-top:12px;">
                    <button class="btn-small btn-save" onclick="addPreset()">+ Add Preset</button>
                </div>
            </div>

            <div class="modal-section">
                <h3>General</h3>
                <div class="toggle-row">
                    <span class="toggle-label">Default model</span>
                    <select id="defaultModelSetting" onchange="saveDefaultModel()"></select>
                </div>
            </div>
        </div>
    </div>
</div>

<main class="main">
    <div class="topbar">
        <div class="topbar-left">
            <span class="topbar-label">Model</span>
            <select id="modelSelect"></select>
        </div>
        <div class="topbar-right">
            <button class="search-badge on" id="searchBadge" onclick="toggleSearch()" title="Toggle auto web search">🔍 SEARCH ON</button>
            <button class="profile-badge on" id="profileBadge" onclick="toggleProfile()" title="Toggle profile injection">PROFILE ON</button>
        </div>
    </div>

    <div class="chat-container" id="chatContainer">
        <div class="welcome-screen" id="welcomeScreen">
            <div class="logo">&#9889;</div>
            <p>JarvisChat &mdash; your local coding companion.<br>Profile context is injected automatically.<br>Web search kicks in when the model is uncertain.<br>Pick a model and start building.</p>
        </div>
    </div>

    <div class="input-area">
        <div class="input-row-top">
            <span class="preset-label">PRESET</span>
            <select id="presetSelect">
                <option value="">None (profile only)</option>
            </select>
        </div>
        <div class="input-wrapper">
            <textarea id="userInput" placeholder="Type a message... (Shift+Enter for new line)" rows="1" autofocus></textarea>
            <div class="token-thermometer" title="Context usage">
                <div class="thermometer-bar"><div class="thermometer-fill" id="thermometerFill" style="height:0%"></div></div>
                <div class="token-info" id="tokenInfo">-- / --</div>
            </div>
            <button class="send-btn" id="sendBtn" onclick="sendMessage()">SEND</button>
        </div>
    </div>
</main>

<script>
let currentConvId = null;
let isStreaming = false;
let abortController = null;
let profileEnabled = true;
let searchEnabled = true;
let presets = [];
let modelContextSize = 8192; // default, updated on model change
let cachedProfile = '';
let conversationHistory = []; // track messages for token counting

document.addEventListener('DOMContentLoaded', async () => {
    await loadModels();
    await loadSettings();
    await loadProfile();
    await loadPresets();
    await loadConversations();
    checkOllamaStatus();
    checkSearchStatus();
    updateSystemStats();
    setInterval(checkOllamaStatus, 30000);
    setInterval(checkSearchStatus, 60000);
    setInterval(updateSystemStats, 2000);
    document.getElementById('userInput').addEventListener('input', updateTokenThermometer);
    updateTokenThermometer();
});

async function updateSystemStats() {
    try {
        const resp = await fetch('/api/stats');
        const data = await resp.json();
        
        // Update CPU
        const cpuFill = document.getElementById('cpuFill');
        const cpuValue = document.getElementById('cpuValue');
        cpuFill.style.width = data.cpu_percent + '%';
        cpuFill.className = 'stat-fill' + (data.cpu_percent >= 90 ? ' danger' : data.cpu_percent >= 70 ? ' warn' : '');
        cpuValue.textContent = data.cpu_percent + '%';
        
        // Update Memory
        const memFill = document.getElementById('memFill');
        const memValue = document.getElementById('memValue');
        memFill.style.width = data.memory_percent + '%';
        memFill.className = 'stat-fill' + (data.memory_percent >= 90 ? ' danger' : data.memory_percent >= 70 ? ' warn' : '');
        memValue.textContent = data.memory_percent + '%';
        
        // Update GPU
        const gpuFill = document.getElementById('gpuFill');
        const gpuValue = document.getElementById('gpuValue');
        if (data.gpu_available) {
            gpuFill.style.width = data.gpu_percent + '%';
            gpuFill.className = 'stat-fill gpu' + (data.gpu_percent >= 90 ? ' danger' : data.gpu_percent >= 70 ? ' warn' : '');
            gpuValue.textContent = data.gpu_percent + '%';
        } else {
            gpuFill.style.width = '0%';
            gpuValue.textContent = 'N/A';
        }
        
        // Update VRAM
        const vramFill = document.getElementById('vramFill');
        const vramValue = document.getElementById('vramValue');
        if (data.gpu_available) {
            vramFill.style.width = data.vram_percent + '%';
            vramFill.className = 'stat-fill gpu' + (data.vram_percent >= 90 ? ' danger' : data.vram_percent >= 70 ? ' warn' : '');
            vramValue.textContent = data.vram_percent + '%';
        } else {
            vramFill.style.width = '0%';
            vramValue.textContent = 'N/A';
        }
    } catch(e) {
        console.log('Stats fetch error:', e);
    }
}

async function checkOllamaStatus() {
    try {
        const resp = await fetch('/api/ps');
        const data = await resp.json();
        const el = document.getElementById('ollamaStatus');
        const models = data.models || [];
        el.innerHTML = models.length > 0
            ? '<span class="status-dot"></span> ' + models.map(m => m.name).join(', ')
            : '<span class="status-dot"></span> Ollama ready';
    } catch(e) {
        document.getElementById('ollamaStatus').innerHTML = '<span class="status-dot offline"></span> Ollama offline';
    }
}

async function checkSearchStatus() {
    try {
        const resp = await fetch('/api/search/status');
        const data = await resp.json();
        const el = document.getElementById('searchStatus');
        if (data.available) {
            el.innerHTML = '<span class="status-dot"></span> search: ready';
        } else {
            el.innerHTML = '<span class="status-dot warning"></span> search: unavailable';
        }
    } catch(e) {
        document.getElementById('searchStatus').innerHTML = '<span class="status-dot offline"></span> search: error';
    }
}

async function loadModels() {
    try {
        const resp = await fetch('/api/models');
        const data = await resp.json();
        const select = document.getElementById('modelSelect');
        const settingSelect = document.getElementById('defaultModelSetting');
        select.innerHTML = '';
        settingSelect.innerHTML = '';
        (data.models || []).forEach(m => {
            const gb = (m.size / (1024*1024*1024)).toFixed(1);
            select.add(new Option(m.name + ' (' + gb + 'GB)', m.name));
            settingSelect.add(new Option(m.name, m.name));
        });
        select.addEventListener('change', fetchModelContextSize);
    } catch(e) {}
}

async function fetchModelContextSize() {
    const model = document.getElementById('modelSelect').value;
    if (!model) return;
    try {
        const resp = await fetch('/api/show', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name: model })
        });
        const data = await resp.json();
        // num_ctx is in model_info or parameters
        if (data.model_info && data.model_info['context_length']) {
            modelContextSize = data.model_info['context_length'];
        } else if (data.parameters) {
            const match = data.parameters.match(/num_ctx\s+(\d+)/);
            if (match) modelContextSize = parseInt(match[1]);
        }
        updateTokenThermometer();
    } catch(e) {
        console.log('Could not fetch model context size:', e);
    }
}

async function loadSettings() {
    try {
        const resp = await fetch('/api/settings');
        const s = await resp.json();
        profileEnabled = s.profile_enabled !== 'false';
        searchEnabled = s.search_enabled !== 'false';
        updateProfileUI();
        updateSearchUI();
        if (s.default_model) {
            document.getElementById('modelSelect').value = s.default_model;
            document.getElementById('defaultModelSetting').value = s.default_model;
        }
    } catch(e) {}
}

async function saveSettings() {
    await fetch('/api/settings', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ 
            profile_enabled: profileEnabled ? 'true' : 'false',
            search_enabled: searchEnabled ? 'true' : 'false'
        })
    });
}

async function saveDefaultModel() {
    const model = document.getElementById('defaultModelSetting').value;
    await fetch('/api/settings', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ default_model: model })
    });
}

async function loadProfile() {
    try {
        const resp = await fetch('/api/profile');
        const data = await resp.json();
        cachedProfile = data.content || '';
        document.getElementById('profileEditor').value = cachedProfile;
        updateTokenCount();
        updateTokenThermometer();
    } catch(e) {}
}

async function saveProfile() {
    const content = document.getElementById('profileEditor').value;
    await fetch('/api/profile', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ content: content })
    });
    updateTokenCount();
    var btn = document.getElementById('saveProfileBtn');
    btn.textContent = 'Saved!';
    setTimeout(function() { btn.textContent = 'Save Profile'; }, 1500);
}

async function resetProfile() {
    if (!confirm('Reset profile to default? This overwrites your current profile.')) return;
    try {
        const resp = await fetch('/api/profile/default');
        const data = await resp.json();
        document.getElementById('profileEditor').value = data.content;
        await saveProfile();
    } catch(e) {}
}

function toggleProfile() {
    profileEnabled = !profileEnabled;
    updateProfileUI();
    saveSettings();
}

function toggleSearch() {
    searchEnabled = !searchEnabled;
    updateSearchUI();
    saveSettings();
}

function updateProfileUI() {
    const badge = document.getElementById('profileBadge');
    const toggle = document.getElementById('profileToggle');
    badge.className = 'profile-badge ' + (profileEnabled ? 'on' : 'off');
    badge.textContent = profileEnabled ? 'PROFILE ON' : 'PROFILE OFF';
    if (toggle) toggle.className = 'toggle-switch' + (profileEnabled ? ' on' : '');
}

function updateSearchUI() {
    const badge = document.getElementById('searchBadge');
    const toggle = document.getElementById('searchToggle');
    badge.className = 'search-badge ' + (searchEnabled ? 'on' : 'off');
    badge.innerHTML = searchEnabled ? '🔍 SEARCH ON' : '🔍 SEARCH OFF';
    if (toggle) toggle.className = 'toggle-switch' + (searchEnabled ? ' on' : '');
}

function updateTokenCount() {
    const text = document.getElementById('profileEditor').value;
    cachedProfile = text;
    const tokens = Math.round(text.length / 4);
    document.getElementById('profileTokenCount').textContent = '~' + tokens + ' tokens';
    updateTokenThermometer();
}

function estimateTokens(text) {
    // Rough estimate: ~4 characters per token for English
    return Math.round((text || '').length / 4);
}

function updateTokenThermometer() {
    const userInput = document.getElementById('userInput').value || '';
    const presetId = document.getElementById('presetSelect').value;
    const preset = presets.find(p => p.id === presetId);
    const presetText = preset ? preset.prompt : '';
    
    // Calculate total tokens: profile + preset + history + current input
    let totalTokens = 0;
    if (profileEnabled && cachedProfile) {
        totalTokens += estimateTokens(cachedProfile);
    }
    totalTokens += estimateTokens(presetText);
    conversationHistory.forEach(msg => {
        totalTokens += estimateTokens(msg.content);
    });
    totalTokens += estimateTokens(userInput);
    
    // Update thermometer fill
    const fill = document.getElementById('thermometerFill');
    const info = document.getElementById('tokenInfo');
    const percent = Math.min((totalTokens / modelContextSize) * 100, 100);
    fill.style.height = percent + '%';
    
    // Format numbers: use K for thousands
    const formatNum = n => n >= 1000 ? (n/1000).toFixed(1) + 'K' : n;
    info.textContent = formatNum(totalTokens) + ' / ' + formatNum(modelContextSize);
    info.title = totalTokens + ' / ' + modelContextSize + ' tokens';
    
    // Color coding
    info.className = 'token-info';
    if (percent >= 90) {
        info.classList.add('danger');
    } else if (percent >= 70) {
        info.classList.add('warning');
    }
}

document.getElementById('profileEditor').addEventListener('input', updateTokenCount);
document.getElementById('presetSelect').addEventListener('change', updateTokenThermometer);

async function loadPresets() {
    try {
        const resp = await fetch('/api/presets');
        presets = await resp.json();
        renderPresetList();
        renderPresetSelect();
    } catch(e) {}
}

function renderPresetList() {
    const container = document.getElementById('presetList');
    container.innerHTML = '';
    presets.forEach(function(p) {
        const div = document.createElement('div');
        div.className = 'preset-item';
        const nameSpan = document.createElement('span');
        nameSpan.className = 'preset-name';
        nameSpan.textContent = p.name;
        div.appendChild(nameSpan);
        const actions = document.createElement('div');
        actions.className = 'preset-actions';
        const editBtn = document.createElement('button');
        editBtn.innerHTML = '&#9998;';
        editBtn.title = 'Edit';
        editBtn.setAttribute('data-id', p.id);
        editBtn.addEventListener('click', function() { editPreset(this.getAttribute('data-id')); });
        actions.appendChild(editBtn);
        if (!p.is_default) {
            const delBtn = document.createElement('button');
            delBtn.innerHTML = '&times;';
            delBtn.title = 'Delete';
            delBtn.setAttribute('data-id', p.id);
            delBtn.addEventListener('click', function() { deletePreset(this.getAttribute('data-id')); });
            actions.appendChild(delBtn);
        }
        div.appendChild(actions);
        container.appendChild(div);
    });
}

function renderPresetSelect() {
    const select = document.getElementById('presetSelect');
    const current = select.value;
    select.innerHTML = '<option value="">None (profile only)</option>';
    presets.forEach(function(p) { select.add(new Option(p.name, p.id)); });
    select.value = current;
}

async function addPreset() {
    const name = prompt('Preset name:');
    if (!name) return;
    const p = prompt('System prompt text:');
    if (!p) return;
    await fetch('/api/presets', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name: name, prompt: p}) });
    await loadPresets();
}

async function editPreset(id) {
    const preset = presets.find(function(p) { return p.id === id; });
    if (!preset) return;
    const name = prompt('Preset name:', preset.name);
    if (!name) return;
    const p = prompt('System prompt:', preset.prompt);
    if (p === null) return;
    await fetch('/api/presets/' + id, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name: name, prompt: p}) });
    await loadPresets();
}

async function deletePreset(id) {
    if (!confirm('Delete this preset?')) return;
    await fetch('/api/presets/' + id, { method:'DELETE' });
    await loadPresets();
}

function getSelectedPresetPrompt() {
    const id = document.getElementById('presetSelect').value;
    if (!id) return '';
    const p = presets.find(function(x) { return x.id === id; });
    return p ? p.prompt : '';
}

function openSettings() { document.getElementById('settingsModal').classList.add('visible'); loadProfile(); }
function closeSettings() { document.getElementById('settingsModal').classList.remove('visible'); }
document.getElementById('settingsModal').addEventListener('click', function(e) { if (e.target.id === 'settingsModal') closeSettings(); });

async function loadConversations() {
    try {
        const resp = await fetch('/api/conversations');
        const convs = await resp.json();
        const list = document.getElementById('convList');
        list.innerHTML = '';
        convs.forEach(function(c) {
            const div = document.createElement('div');
            div.className = 'conv-item' + (c.id === currentConvId ? ' active' : '');
            const titleSpan = document.createElement('span');
            titleSpan.className = 'conv-title';
            titleSpan.textContent = c.title;
            titleSpan.setAttribute('data-id', c.id);
            titleSpan.addEventListener('click', function() { loadConversation(this.getAttribute('data-id')); });
            div.appendChild(titleSpan);
            const delSpan = document.createElement('span');
            delSpan.className = 'conv-delete';
            delSpan.innerHTML = '&times;';
            delSpan.setAttribute('data-id', c.id);
            delSpan.addEventListener('click', function(ev) { ev.stopPropagation(); deleteConversation(this.getAttribute('data-id')); });
            div.appendChild(delSpan);
            list.appendChild(div);
        });
    } catch(e) {}
}

async function loadConversation(convId) {
    try {
        const resp = await fetch('/api/conversations/' + convId);
        const data = await resp.json();
        currentConvId = convId;
        document.getElementById('modelSelect').value = data.conversation.model;
        fetchModelContextSize();
        const container = document.getElementById('chatContainer');
        container.innerHTML = '';
        conversationHistory = [];
        data.messages.forEach(function(msg) { 
            appendMessage(msg.role, msg.content, false);
            conversationHistory.push({ role: msg.role, content: msg.content });
        });
        scrollToBottom();
        updateTokenThermometer();
        await loadConversations();
    } catch(e) {}
}

async function deleteConversation(convId) {
    if (!confirm('Delete this conversation?')) return;
    await fetch('/api/conversations/' + convId, { method:'DELETE' });
    if (currentConvId === convId) { currentConvId = null; showWelcome(); }
    await loadConversations();
}

async function deleteAllConversations() {
    if (!confirm('Delete ALL conversations? This cannot be undone.')) return;
    await fetch('/api/conversations', { method:'DELETE' });
    currentConvId = null;
    conversationHistory = [];
    showWelcome();
    updateTokenThermometer();
    await loadConversations();
}

function newChat() {
    currentConvId = null;
    conversationHistory = [];
    showWelcome();
    document.querySelectorAll('.conv-item').forEach(function(el) { el.classList.remove('active'); });
    updateTokenThermometer();
}

function showWelcome() {
    document.getElementById('chatContainer').innerHTML =
        '<div class="welcome-screen" id="welcomeScreen">' +
        '<div class="logo">&#9889;</div>' +
        '<p>JarvisChat &mdash; your local coding companion.<br>Profile context is injected automatically.<br>Web search kicks in when the model is uncertain.<br>Pick a model and start building.</p>' +
        '</div>';
}

async function sendMessage() {
    const input = document.getElementById('userInput');
    const message = input.value.trim();
    if (!message || isStreaming) return;

    const model = document.getElementById('modelSelect').value;
    const presetPrompt = getSelectedPresetPrompt();

    const welcome = document.getElementById('welcomeScreen');
    if (welcome) welcome.remove();

    appendMessage('user', message, true);
    conversationHistory.push({ role: 'user', content: message });
    input.value = '';
    input.style.height = 'auto';
    updateTokenThermometer();

    const assistantDiv = appendMessage('assistant', '', true);
    const textEl = assistantDiv.querySelector('.text');
    textEl.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';
    setStreamingState(true);

    let searchTriggered = false;
    let originalResponse = '';

    try {
        abortController = new AbortController();
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ conversation_id: currentConvId, message: message, model: model, system_prompt: presetPrompt }),
            signal: abortController.signal
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';
        let firstToken = true;
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (let i = 0; i < lines.length; i++) {
                const line = lines[i];
                if (!line.startsWith('data: ')) continue;
                try {
                    const data = JSON.parse(line.slice(6));
                    if (data.error) { textEl.textContent = 'Error: ' + data.error; setStreamingState(false); return; }
                    if (data.conversation_id && !currentConvId) { currentConvId = data.conversation_id; await loadConversations(); }
                    
                    if (data.searching) {
                        // Model expressed uncertainty, searching...
                        originalResponse = fullText;
                        textEl.innerHTML = renderMarkdown(fullText) + '<div class="search-indicator"><div class="spinner"></div>Searching the web...</div>';
                        searchTriggered = true;
                    }
                    
                    if (data.debug) {
                        console.log('[DEBUG]', data.debug);
                        // Show debug in UI temporarily
                        textEl.innerHTML += '<div class="search-indicator" style="font-size:10px">' + data.debug + '</div>';
                    }
                    
                    if (data.search_results) {
                        // Got search results, about to stream augmented response
                        let preview = data.results_preview ? data.results_preview.slice(0,3).join(', ') : '';
                        textEl.innerHTML = '<div class="search-indicator">🔍 Found ' + data.search_results + ' results: ' + preview + '...</div>';
                        fullText = ''; // Reset for augmented response
                        firstToken = true;
                    }
                    
                    if (data.token) {
                        if (firstToken) { 
                            if (searchTriggered) {
                                // Clear the search indicator and start fresh with augmented response
                                textEl.innerHTML = '';
                            } else {
                                textEl.innerHTML = ''; 
                            }
                            firstToken = false; 
                        }
                        fullText += data.token;
                        textEl.innerHTML = renderMarkdown(fullText);
                        scrollToBottom();
                    }
                    
                    if (data.done) { 
                        const roleLabel = assistantDiv.querySelector('.role-label');
                        if (data.searched) {
                            // Add search badge to the message
                            if (roleLabel && !roleLabel.querySelector('.search-badge-inline')) {
                                roleLabel.innerHTML += '<span class="search-badge-inline">🔍 web search</span>';
                            }
                        }
                        // Add perplexity badge
                        if (typeof data.perplexity === 'number' && roleLabel) {
                            const ppl = data.perplexity;
                            let pplClass = 'low';
                            if (ppl >= 15) pplClass = 'high';
                            else if (ppl >= 8) pplClass = 'medium';
                            roleLabel.innerHTML += '<span class="perplexity-badge ' + pplClass + '" title="Perplexity (lower=confident, higher=uncertain)">ppl: ' + ppl.toFixed(1) + '</span>';
                        }
                        // Add tokens per second badge
                        if (typeof data.tokens_per_sec === 'number' && data.tokens_per_sec > 0 && roleLabel) {
                            roleLabel.innerHTML += '<span class="tps-badge" title="Tokens per second">' + data.tokens_per_sec.toFixed(1) + ' t/s</span>';
                        }
                        // Track assistant response for token counting
                        conversationHistory.push({ role: 'assistant', content: fullText });
                        updateTokenThermometer();
                        addCopyButtons(assistantDiv); 
                        setStreamingState(false); 
                        await loadConversations(); 
                        checkOllamaStatus(); 
                    }
                } catch(e) {}
            }
        }
    } catch (e) {
        if (e.name === 'AbortError') textEl.innerHTML += '<br><em style="color:var(--text-muted)">[stopped]</em>';
        else textEl.textContent = 'Error: ' + e.message;
        setStreamingState(false);
    }
}

function setStreamingState(streaming) {
    isStreaming = streaming;
    const btn = document.getElementById('sendBtn');
    if (streaming) {
        btn.textContent = 'STOP'; btn.className = 'stop-btn';
        btn.onclick = function() { if (abortController) abortController.abort(); setStreamingState(false); };
    } else {
        btn.textContent = 'SEND'; btn.className = 'send-btn'; btn.onclick = sendMessage;
    }
}

function appendMessage(role, content, animate) {
    const container = document.getElementById('chatContainer');
    const div = document.createElement('div');
    div.className = 'message ' + role;
    if (!animate) div.style.animation = 'none';
    div.innerHTML = '<div class="avatar">' + (role==='user'?'YOU':'AI') + '</div>' +
        '<div class="content"><div class="role-label">' + role + '</div>' +
        '<div class="text">' + (content ? renderMarkdown(content) : '') + '</div></div>';
    container.appendChild(div);
    if (content && role === 'assistant') addCopyButtons(div);
    scrollToBottom();
    return div;
}

function renderMarkdown(text) {
    var blocks = [];
    text = text.replace(/```(\w*)\n([\s\S]*?)```/g, function(match, lang, code) {
        blocks.push('<pre data-lang="' + lang + '"><code>' + escapeHtml(code) + '</code></pre>');
        return '\x00BLOCK' + (blocks.length - 1) + '\x00';
    });
    text = text.replace(/```([\s\S]*?)```/g, function(match, code) {
        blocks.push('<pre><code>' + escapeHtml(code) + '</code></pre>');
        return '\x00BLOCK' + (blocks.length - 1) + '\x00';
    });
    var h = escapeHtml(text);
    h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
    h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    h = h.replace(/\*(.+?)\*/g, '<em>$1</em>');
    h = h.replace(/\n/g, '<br>');
    h = h.replace(/\x00BLOCK(\d+)\x00/g, function(match, idx) { return blocks[parseInt(idx)]; });
    return h;
}

function addCopyButtons(msgDiv) {
    msgDiv.querySelectorAll('pre').forEach(function(pre) {
        if (pre.querySelector('.copy-btn')) return;
        const btn = document.createElement('button');
        btn.className = 'copy-btn';
        btn.textContent = 'copy';
        btn.onclick = function() {
            navigator.clipboard.writeText(pre.querySelector('code') ? pre.querySelector('code').textContent : pre.textContent)
                .then(function() { btn.textContent = 'copied!'; setTimeout(function() { btn.textContent = 'copy'; }, 1500); });
        };
        pre.style.position = 'relative';
        pre.appendChild(btn);
    });
}

function escapeHtml(t) { var d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
function scrollToBottom() { var c = document.getElementById('chatContainer'); c.scrollTop = c.scrollHeight; }

var userInput = document.getElementById('userInput');
userInput.addEventListener('input', function() { this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 200) + 'px'; });
userInput.addEventListener('keydown', function(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
