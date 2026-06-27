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
    escaped = []
    for word in words[:10]:
        if word.upper() in {"AND", "OR", "NOT", "NEAR"}:
            escaped.append(f'"{word}"*')
        else:
            escaped.append(word + "*")
    safe_query = " OR ".join(escaped)
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
