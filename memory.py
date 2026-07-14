"""
cAIc - FTS5 memory system.
CRUD, search, remember/forget command processing, topic detection.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from crypto import encrypt_text, decrypt_text
from db import get_db
from config import MAX_MEMORY_FACT_CHARS

log = logging.getLogger("caic")

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


AUTO_FACT_PATTERNS = [
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b(?:systemd|nginx|docker|ssh|ufw|iptables|postgres(?:ql)?|redis|mosquitto|node_exporter|prometheus|grafana|qdrant|rabbitmq|searxng|llama-server)\b", re.IGNORECASE),
    re.compile(r"/(?:etc|home|usr|var|opt|tmp|mnt)/\S+"),
    re.compile(r"\b(?:Ryzen|RX\s*\d{4}|RTX\s*\d{4}|Radeon|AMD|NVIDIA|Core\s*i[579]|Threadripper)\b", re.IGNORECASE),
    re.compile(r"\b(?:Qwen|Llama|Gemma|Phi|Mistral|DeepSeek)\S*\b", re.IGNORECASE),
    re.compile(r"\b(?:systemd\.service|docker\s+(?:compose|container|service)|systemctl|journalctl)\b", re.IGNORECASE),
]
SOCIAL_TRIGGERS = {"hi", "hello", "hey", "yo", "sup", "howdy", "good morning", "good evening"}


def _is_social(text: str) -> bool:
    t = text.strip().lower()
    if t in SOCIAL_TRIGGERS or any(t.startswith(w) for w in ("thanks", "thank you", "ty")):
        return True
    return False


def auto_detect_facts(user_message: str, assistant_message: str) -> list[str]:
    """Extract environmental/factual content from a chat turn.

    Returns a list of fact strings ready for storage. Empty list means
    nothing worth persisting.
    """
    if _is_social(user_message):
        return []
    if len(assistant_message) < 40:
        return []
    if process_remember_command(user_message) is not None:
        return []

    found = []
    for pat in AUTO_FACT_PATTERNS:
        if pat.search(user_message):
            found.append(user_message)
            break

    # Also capture when the user is reporting a change they made
    change_match = re.search(
        r"(?:I\s+)?(?:set|changed?|updated|installed|configured|enabled|disabled|added|removed|created|deleted|restarted|reloaded|switched|moved|copied|renamed|symlinked|mounted|unmounted)\s+(?:the\s+)?(.+)",
        user_message, re.IGNORECASE,
    )
    if change_match and user_message not in found:
        found.append(user_message)

    seen = set()
    deduped = []
    for f in found:
        key = f.strip().lower()
        if key not in seen:
            seen.add(key)
            deduped.append(f.strip()[:MAX_MEMORY_FACT_CHARS])
    return deduped


def check_fact_conflicts(facts: list[str]) -> list[dict]:
    """Search for existing memories that conflict with detected facts.

    Returns list of {memory_id, old_fact, new_fact} for each conflict.
    """
    conflicts = []
    for new_fact in facts:
        related = search_memories(new_fact, limit=1)
        if related:
            old = related[0]["fact"]
            if old.rstrip(".") != new_fact.rstrip("."):
                conflicts.append({
                    "memory_id": related[0]["rowid"],
                    "old_fact": old,
                    "new_fact": new_fact,
                })
    return conflicts


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
        (encrypt_text(fact), topic, source, now),
    )
    db.commit()
    rowid = cur.lastrowid
    db.close()
    log.info(f"Memory added [{topic}]: {fact[:50]}...")
    return rowid


def search_memories(query: str, limit: int = 5) -> list:
    if not query.strip():
        return []
    all_mems = get_all_memories()
    words = set(re.findall(r"[A-Za-z0-9_]+", query.lower()))
    scored = []
    for m in all_mems:
        fact_lower = m["fact"].lower()
        score = sum(1 for w in words if w in fact_lower)
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:limit]]


def get_all_memories(topic: Optional[str] = None) -> list:
    db = get_db()
    if topic:
        rows = db.execute(
            "SELECT rowid, * FROM memories WHERE topic = ? ORDER BY created_at DESC", (topic,)
        ).fetchall()
    else:
        rows = db.execute("SELECT rowid, * FROM memories ORDER BY created_at DESC").fetchall()
    db.close()
    result = []
    for row in rows:
        d = dict(row)
        d["fact"] = decrypt_text(d["fact"])
        result.append(d)
    return result


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
    cur = db.execute("UPDATE memories SET fact = ? WHERE rowid = ?", (encrypt_text(fact), rowid))
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
