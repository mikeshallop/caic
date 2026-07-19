"""
cAIc - RAG pipeline: Qdrant vector search + system prompt assembly.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx

from crypto import encrypt_text, decrypt_text
from eviction import _update_retrieval_count
from db import get_db, get_setting, list_skills_with_state, format_active_skills_prompt
from memory import search_memories
from config import MAX_SKILL_PROMPT_CHARS, QDRANT_URL, RAG_COLLECTION

log = logging.getLogger("caic")

EMBED_URL = os.environ.get("CAIC_EMBED_URL", "http://192.168.50.210:11434")
EMBED_MODEL = os.environ.get("CAIC_EMBED_MODEL", "mxbai-embed-large")
RAG_SCORE_THRESHOLD = 0.25

# Re-export eviction symbols for backward compatibility
from eviction import (  # noqa: E402
    maybe_evict, get_rag_operational_stats, EVICTION_LOG,
    get_collection_count, get_collection_stats, evict_batch,
)


async def _upsert_fact(fact: str, text: str, topic: str,
                       client: httpx.AsyncClient) -> bool:
    """Embed text and upsert a fact to Qdrant."""
    chunks = chunk_text(text)
    if not chunks:
        return False
    ts = datetime.now(timezone.utc).timestamp()
    ok = False
    for i, chunk in enumerate(chunks):
        try:
            er = await client.post(
                f"{EMBED_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": chunk},
                timeout=10.0,
            )
            if er.status_code != 200:
                continue
            vector = er.json()["embedding"]
            pid = f"auto-{ts}-{i}"
            payload = {
                "text": encrypt_text(chunk), "source": "auto_fact", "fact": fact,
                "ingest_date": datetime.now(timezone.utc).isoformat(),
                "type": "auto_fact", "topic": topic,
            }
            r = await client.put(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points?wait=true",
                json={"points": [{"id": pid, "vector": vector, "payload": payload}]},
                timeout=10.0,
            )
            if r.status_code in (200, 201):
                ok = True
        except Exception as e:
            log.warning(f"Qdrant upsert error: {e}")
    return ok


async def ingest_auto_fact(facts: list[str], user_message: str,
                           assistant_message: str) -> int:
    """Persist pre-detected facts to memories + Qdrant.

    Call this when no conflicts exist — silent ingest.
    Returns the number of facts stored.
    """
    from memory import add_memory, detect_topic

    ingested = 0
    async with httpx.AsyncClient() as client:
        for fact in facts:
            topic = detect_topic(fact)
            add_memory(fact, topic=topic, source="auto")
            ingested += 1
            text = f"Q: {user_message}\nA: {assistant_message}"
            await _upsert_fact(fact, text, topic, client)
    if ingested:
        log.info(f"Auto-ingested {ingested} fact(s) from conversation")
    return ingested


async def confirm_fact_update(memory_id: int, old_fact: str, new_fact: str,
                              user_message: str, assistant_message: str) -> bool:
    """Confirm a user-accepted fact update: replace memory + Qdrant entry."""
    from memory import update_memory, detect_topic

    if not update_memory(memory_id, new_fact):
        return False

    topic = detect_topic(new_fact)
    try:
        async with httpx.AsyncClient() as client:
            # scroll old points with matching fact and delete them
            scroll_r = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/scroll",
                json={
                    "filter": {"must": [{"key": "fact", "match": {"value": old_fact}}]},
                    "limit": 100,
                    "with_payload": False,
                },
                timeout=10.0,
            )
            if scroll_r.status_code == 200:
                ids = [p["id"] for p in scroll_r.json().get("result", [])]
                if ids:
                    await client.post(
                        f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/delete",
                        json={"points": ids},
                        timeout=10.0,
                    )

            text = f"Q: {user_message}\nA: {assistant_message}"
            await _upsert_fact(new_fact, text, topic, client)
    except Exception as e:
        log.warning(f"Fact update RAG error: {e}")

    log.info(f"Fact updated [memory_id={memory_id}]: {new_fact}")
    return True


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 128) -> list:
    words = text.split()
    target_words = int(chunk_size / 1.3)
    overlap_words = int(overlap / 1.3)
    if not words:
        return []
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + target_words, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += target_words - overlap_words
    return chunks


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
            results = search_resp.json().get("result", [])
            for r in results:
                pid = r.get("id")
                if pid:
                    current = r.get("payload", {}).get("retrieval_count", 0) or 0
                    # Fire-and-forget: update retrieval count without blocking the response
                    asyncio.create_task(_update_retrieval_count(pid, current))
            return results
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
                rag_lines = [decrypt_text(r["payload"]["text"]) for r in rag_results if r["score"] > RAG_SCORE_THRESHOLD]
                if rag_lines:
                    parts.append("## Retrieved Context\n" + "\n\n---\n\n".join(rag_lines))
                    log.info(f"RAG injected {len(rag_lines)} chunks into context")
        except Exception as e:
            log.warning(f"RAG injection error: {e}")

    if settings.get("skills_enabled", "true") == "true":
        active_skills = [s for s in list_skills_with_state(db) if s["enabled"]]
        if active_skills:
            parts.append(format_active_skills_prompt(active_skills))

    if extra_prompt and extra_prompt.strip():
        parts.append(extra_prompt.strip())

    return "\n\n---\n\n".join(parts) if parts else ""
