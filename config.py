"""
cAIc - Central configuration.
All constants, environment variables, limits, and skill registry live here.
"""
import os
import re
import ipaddress
import logging

log = logging.getLogger("caic")

VERSION = "v0.20.0"
OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
LLAMA_SERVER_BASE = os.environ.get("LLAMA_SERVER_BASE", "http://192.168.50.108:8081")
SEARXNG_BASE = "http://localhost:8888"
DEFAULT_MODEL = "qwen2.5-7b-instruct"
COMPLETIONS_API_KEY = os.environ.get("CAIC_COMPLETIONS_API_KEY", "caic-sk-" + os.urandom(24).hex())
MODEL_CONTEXT_LENGTH = 4096

# --- AMQP ---
AMQP_RECONNECT_DELAY = 5
AMQP_EXCHANGE_ADMIN = "jc.admin"
AMQP_EXCHANGE_SYSTEM = "jc.system"
AMQP_SECRET_PATH = os.environ.get("CAIC_AMQP_SECRET_PATH", "/home/gramps/.caic_amqp_secret")

def get_amqp_url() -> str:
    url = os.environ.get("CAIC_AMQP_URL")
    if url:
        return url
    try:
        with open(AMQP_SECRET_PATH) as f:
            pw = f.read().strip()
    except (FileNotFoundError, OSError):
        pw = "password"
    return f"amqp://caic:{pw}@localhost:5672/caic"

# --- Auth ---
SESSION_TIMEOUT_SECONDS = 3600
MAX_PIN_ATTEMPTS = 5
PIN_LOCKOUT_SECONDS = 300
ALLOW_DEFAULT_PIN = os.getenv("CAIC_ALLOW_DEFAULT_PIN", "false").lower() == "true"
TRUSTED_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.getenv("CAIC_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
}
DEFAULT_ALLOWED_CIDRS = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
ALLOWED_CIDRS_RAW = os.getenv("CAIC_ALLOWED_CIDRS", DEFAULT_ALLOWED_CIDRS)
TRUST_X_FORWARDED_FOR = (
    os.getenv("CAIC_TRUST_X_FORWARDED_FOR", "false").lower() == "true"
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

# --- Upload ---
UPLOAD_DIR = "/tmp/caic_uploads"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SUPPORTED_UPLOAD_TYPES = {"text/plain", "text/markdown", "application/pdf", "application/json", "text/x-python", "text/html", "image/png", "image/jpeg", "image/gif", "image/svg+xml", "image/webp"}
QDRANT_URL = os.environ.get("CAIC_QDRANT_URL", "http://192.168.50.108:6333")
RAG_COLLECTION = "caic_rag"
UPLOAD_CONTEXT_EXPIRY_HOURS = 1
BODY_LIMIT_UPLOAD_BYTES = MAX_UPLOAD_BYTES

# --- RAG eviction ---
RAG_MAX_VECTORS = 50000
RAG_EVICTION_HIGH_WATER = 0.80
RAG_EVICTION_LOW_WATER = 0.20
RAG_EVICTION_BATCH = 1000
RAG_PINNED_SOURCES = ["upload", "profile"]
RAG_GRACE_HOURS = 1
RAG_ACCESS_WEIGHT = 1.0
RAG_AGE_WEIGHT = 0.1

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

# --- Triage / query routing ---
TRIAGE_BASE = os.environ.get("CAIC_TRIAGE_BASE", "http://127.0.0.1:8083/v1")
TRIAGE_TIMEOUT = 10
FALLBACK_TO_DEFAULT = True

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
