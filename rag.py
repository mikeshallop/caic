"""
JarvisChat - RAG pipeline: Qdrant vector search + system prompt assembly.
"""
import logging

import httpx

from db import get_db, get_setting, list_skills_with_state, format_active_skills_prompt
from memory import search_memories
from config import LLAMA_SERVER_BASE, MAX_SKILL_PROMPT_CHARS

log = logging.getLogger("jarvischat")

QDRANT_URL = "http://192.168.50.108:6333"
EMBED_MODEL = "mxbai-embed-large"
RAG_COLLECTION = "jarvis_rag"
RAG_SCORE_THRESHOLD = 0.25


async def query_rag(query: str, limit: int = 3) -> list:
    try:
        async with httpx.AsyncClient() as client:
            embed_resp = await client.post(
                f"{LLAMA_SERVER_BASE}/api/embeddings",
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
