"""
cAIc - Database layer.
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
from crypto import encrypt_text, decrypt_text

log = logging.getLogger("caic")

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "caic.db"


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


def insert_upload_context(db, conversation_id: str, filename: str, content: str, expires_at: str, content_type: str = "text/plain") -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "INSERT INTO upload_context (conversation_id, filename, content, content_type, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (conversation_id, filename, encrypt_text(content), content_type, now, expires_at),
    )
    return cur.lastrowid


def list_upload_context_by_conversation(db, conversation_id: str):
    rows = db.execute(
        "SELECT id, conversation_id, filename, content_type, created_at, expires_at FROM upload_context WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_upload_context_by_id(db, context_id: int) -> bool:
    cur = db.execute("DELETE FROM upload_context WHERE id = ?", (context_id,))
    return cur.rowcount > 0


def get_upload_context(db, context_id: int):
    row = db.execute(
        "SELECT id, conversation_id, filename, content, content_type, expires_at FROM upload_context WHERE id = ?",
        (context_id,),
    ).fetchone()
    if not row:
        return None
    expires = datetime.fromisoformat(row["expires_at"])
    if expires < datetime.now(timezone.utc):
        db.execute("DELETE FROM upload_context WHERE id = ?", (context_id,))
        db.commit()
        return None
    d = dict(row)
    d["content"] = decrypt_text(d["content"])
    return d


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
            perplexity REAL,
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS upload_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            filename TEXT NOT NULL,
            content TEXT NOT NULL,
            content_type TEXT DEFAULT 'text/plain',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    try:
        conn.execute("ALTER TABLE upload_context ADD COLUMN content_type TEXT DEFAULT 'text/plain'")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE messages ADD COLUMN perplexity REAL")
    except Exception:
        pass

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
        configured_pin = os.getenv("CAIC_ADMIN_PIN", "").strip()
        if re.fullmatch(r"\d{4}", configured_pin):
            seed_pin, pin_source = configured_pin, "env"
        elif ALLOW_DEFAULT_PIN:
            seed_pin, pin_source = "1234", "default"
        else:
            raise RuntimeError(
                "Admin PIN bootstrap blocked: set CAIC_ADMIN_PIN to a 4-digit PIN "
                "or set CAIC_ALLOW_DEFAULT_PIN=true."
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
