"""JarvisChat routers - /api/ingest terminal command RAG hook."""
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from config import COMPLETIONS_API_KEY
from rag import chunk_text, maybe_evict, QDRANT_URL, EMBED_URL, EMBED_MODEL, RAG_COLLECTION

log = logging.getLogger("jarvischat")
router = APIRouter()


def _check_api_key(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[7:].strip()
    if token != COMPLETIONS_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.post("/api/ingest")
async def ingest_content(request: Request):
    _check_api_key(request)
    body = await request.json()
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="content is required")
    source = str(body.get("source", "external")).strip() or "external"
    metadata = body.get("metadata") or {}

    chunks = chunk_text(content)
    if not chunks:
        raise HTTPException(status_code=422, detail="content produced no chunks")

    ingested = 0
    async with httpx.AsyncClient() as client:
        for i, chunk in enumerate(chunks):
            embed_resp = await client.post(
                f"{EMBED_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": chunk},
                timeout=30.0,
            )
            if embed_resp.status_code != 200:
                log.warning(f"Ingest embedding failed for chunk {i}: {embed_resp.status_code}")
                continue
            vector = embed_resp.json()["embedding"]
            point_id = f"ingest-{source}-{datetime.now(timezone.utc).timestamp()}-{i}"
            payload = {"text": chunk, "source": source, "ingest_date": datetime.now(timezone.utc).isoformat(), "type": "ingest"}
            payload.update(metadata)
            upsert_resp = await client.put(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points?wait=true",
                json={"points": [{"id": point_id, "vector": vector, "payload": payload}]},
                timeout=30.0,
            )
            if upsert_resp.status_code in (200, 201):
                ingested += 1
            else:
                log.warning(f"Ingest Qdrant upsert failed for chunk {i}: {upsert_resp.status_code}")

    if ingested > 0:
        evicted = await maybe_evict()
        if evicted:
            log.info(f"Evicted {evicted} vectors after ingest")

    return {"chunks_ingested": ingested, "source": source, "message": f"Ingested {ingested} chunks from {source}"}
