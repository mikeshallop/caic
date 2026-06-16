"""JarvisChat routers - Memory CRUD API."""
from fastapi import APIRouter, HTTPException, Request
from typing import Optional

from db import get_db
from memory import add_memory, delete_memory, update_memory, get_all_memories, search_memories
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES
from config import MAX_MEMORY_FACT_CHARS

router = APIRouter()


@router.get("/api/memories")
async def list_memories(topic: Optional[str] = None):
    memories = get_all_memories(topic)
    return {"memories": memories, "count": len(memories)}


@router.post("/api/memories")
async def create_memory(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    fact = str(body.get("fact", "")).strip()
    if not fact:
        raise HTTPException(status_code=400, detail="Memory fact is required")
    if len(fact) > MAX_MEMORY_FACT_CHARS:
        raise HTTPException(status_code=413, detail="Memory fact is too long")
    rowid = add_memory(fact=fact, topic=body.get("topic", "general"), source=body.get("source", "manual"))
    return {"rowid": rowid, "status": "ok"}


@router.delete("/api/memories/{rowid}")
async def remove_memory(rowid: int):
    if not delete_memory(rowid):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "ok"}


@router.put("/api/memories/{rowid}")
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


@router.get("/api/memories/search")
async def search_memories_api(q: str, limit: int = 10):
    results = search_memories(q, limit=limit)
    return {"results": results, "count": len(results)}


@router.get("/api/memories/stats")
async def memory_stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) as c FROM memories").fetchone()["c"]
    topics = db.execute("SELECT topic, COUNT(*) as c FROM memories GROUP BY topic ORDER BY c DESC").fetchall()
    db.close()
    return {"total": total, "by_topic": {row["topic"]: row["c"] for row in topics}}
