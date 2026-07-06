"""
JarvisChat - RAG pipeline: Qdrant vector search + system prompt assembly.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import httpx

from db import get_db, get_setting, list_skills_with_state, format_active_skills_prompt
from memory import search_memories
from config import (
    MAX_SKILL_PROMPT_CHARS,
    RAG_MAX_VECTORS, RAG_EVICTION_HIGH_WATER, RAG_EVICTION_LOW_WATER,
    RAG_EVICTION_BATCH, RAG_PINNED_SOURCES, RAG_GRACE_HOURS,
    RAG_ACCESS_WEIGHT, RAG_AGE_WEIGHT,
)

log = logging.getLogger("jarvischat")

QDRANT_URL = "http://192.168.50.108:6333"
EMBED_URL = "http://192.168.50.210:11434"
EMBED_MODEL = "mxbai-embed-large"
RAG_COLLECTION = "jarvis_rag"
RAG_SCORE_THRESHOLD = 0.25

eviction_lock = asyncio.Lock()
EVICTION_LOG: list[dict] = []


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


async def _update_retrieval_count(point_id: str, current_count: int = 0):
    """Fire-and-forget increment of retrieval_count."""
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "retrieval_count": current_count + 1,
                "last_accessed": datetime.now(timezone.utc).isoformat(),
            }
            resp = await client.put(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/payload",
                json={"points": [point_id], "payload": payload},
                timeout=5.0,
            )
            if resp.status_code not in (200, 201):
                log.warning(f"Failed to increment retrieval count for {point_id}: {resp.status_code}")
    except Exception as e:
        log.warning(f"Error incrementing retrieval count for {point_id}: {e}")


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
                    asyncio.ensure_future(_update_retrieval_count(pid, current))
            return results
    except Exception as e:
        log.warning(f"RAG query error: {e}")
        return []


async def get_collection_count() -> int:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}",
                timeout=10.0,
            )
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("vectors_count", 0)
    except Exception as e:
        log.warning(f"get_collection_count error: {e}")
    return 0


async def get_collection_stats() -> dict:
    count = await get_collection_count()
    high_water_pct = int(RAG_EVICTION_HIGH_WATER * 100)
    low_water_pct = int(RAG_EVICTION_LOW_WATER * 100)
    percent_full = round((count / RAG_MAX_VECTORS) * 100, 1) if RAG_MAX_VECTORS > 0 else 0
    return {
        "vector_count": count,
        "max_vectors": RAG_MAX_VECTORS,
        "high_water_mark": int(RAG_MAX_VECTORS * RAG_EVICTION_HIGH_WATER),
        "low_water_mark": int(RAG_MAX_VECTORS * RAG_EVICTION_LOW_WATER),
        "high_water_pct": high_water_pct,
        "low_water_pct": low_water_pct,
        "percent_full": percent_full,
        "pinned_sources": list(RAG_PINNED_SOURCES),
    }


async def evict_batch(batch_size: int) -> int:
    """Scroll non-pinned, out-of-grace-period vectors, compute scores, delete lowest-scoring."""
    filter_conditions = {
        "must_not": [
            {"match": {"key": "source", "value": src}}
            for src in RAG_PINNED_SOURCES
        ]
    }
    try:
        async with httpx.AsyncClient() as client:
            scroll_resp = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/scroll",
                json={
                    "filter": filter_conditions,
                    "limit": min(batch_size * 10, 10000),
                    "with_payload": True,
                    "with_vector": False,
                },
                timeout=30.0,
            )
            if scroll_resp.status_code != 200:
                log.warning(f"Eviction scroll failed: {scroll_resp.status_code}")
                return 0

            points = scroll_resp.json().get("result", {}).get("points", [])
            if not points:
                return 0

            now = datetime.now(timezone.utc)
            scored = []
            for p in points:
                payload = p.get("payload", {})
                date_str = payload.get("ingest_date") or payload.get("upload_date", "")
                if date_str:
                    age_hours = (now - datetime.fromisoformat(date_str)).total_seconds() / 3600
                else:
                    age_hours = 999999

                if age_hours < RAG_GRACE_HOURS:
                    continue

                retrieval_count = payload.get("retrieval_count", 0) or 0
                score = retrieval_count * RAG_ACCESS_WEIGHT + age_hours * RAG_AGE_WEIGHT
                last_accessed = payload.get("last_accessed", date_str)
                scored.append((score, last_accessed, p["id"]))

            if not scored:
                log.warning("No evictable vectors found (all pinned or newborn)")
                return 0

            scored.sort(key=lambda x: (x[0], x[1]))
            to_delete = [p[2] for p in scored[:batch_size]]
            if not to_delete:
                return 0

            delete_resp = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/delete",
                json={"points": to_delete},
                timeout=30.0,
            )
            if delete_resp.status_code not in (200, 201):
                log.warning(f"Eviction delete failed: {delete_resp.status_code}")
                return 0

            return len(to_delete)
    except Exception as e:
        log.warning(f"evict_batch error: {e}")
        return 0


async def maybe_evict() -> int:
    if RAG_MAX_VECTORS <= 0:
        return 0
    effective_batch = max(RAG_EVICTION_BATCH, 1)

    async with eviction_lock:
        count = await get_collection_count()
        threshold_high = int(RAG_MAX_VECTORS * RAG_EVICTION_HIGH_WATER)
        threshold_low = int(RAG_MAX_VECTORS * RAG_EVICTION_LOW_WATER)

        if count < threshold_high:
            return 0

        total_evicted = 0
        while count >= threshold_low:
            if total_evicted > 0 and count < threshold_low:
                break
            deleted = await evict_batch(effective_batch)
            if deleted == 0:
                break
            total_evicted += deleted
            count -= deleted
            if count < threshold_high and total_evicted > 0:
                break
            if count < threshold_low:
                break

        if total_evicted > 0:
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "count": total_evicted,
                "remaining": count,
            }
            EVICTION_LOG.append(entry)
            if len(EVICTION_LOG) > 1000:
                EVICTION_LOG.pop(0)
            log.info(f"Evicted {total_evicted} vectors ({count} remaining)")

        return total_evicted


async def get_rag_operational_stats() -> dict:
    stats = await get_collection_stats()
    now = datetime.now(timezone.utc)
    cutoff_1m = now - timedelta(minutes=1)
    cutoff_5m = now - timedelta(minutes=5)
    cutoff_30m = now - timedelta(minutes=30)

    eviction_1m = sum(
        e["count"] for e in EVICTION_LOG
        if datetime.fromisoformat(e["timestamp"]) > cutoff_1m
    )
    eviction_5m = sum(
        e["count"] for e in EVICTION_LOG
        if datetime.fromisoformat(e["timestamp"]) > cutoff_5m
    )
    eviction_30m = sum(
        e["count"] for e in EVICTION_LOG
        if datetime.fromisoformat(e["timestamp"]) > cutoff_30m
    )

    stats.update({
        "grace_hours": RAG_GRACE_HOURS,
        "eviction_counts_last_1m": eviction_1m,
        "eviction_counts_last_5m": eviction_5m,
        "eviction_counts_last_30m": eviction_30m,
    })
    return stats


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
