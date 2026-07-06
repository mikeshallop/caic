"""JarvisChat routers — RAG corpus management admin endpoints."""
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from eviction import get_rag_operational_stats, EVICTION_LOG
from rag import QDRANT_URL, RAG_COLLECTION

log = logging.getLogger("jarvischat")
router = APIRouter()


@router.get("/api/rag/stats")
async def rag_stats(request: Request):
    if getattr(request.state, "session_role", "none") != "admin":
        raise HTTPException(status_code=403, detail="Admin PIN required for this action")
    stats = await get_rag_operational_stats()
    stats["eviction_log_size"] = len(EVICTION_LOG)
    return stats


@router.post("/api/rag/flush")
async def rag_flush(request: Request):
    try:
        async with httpx.AsyncClient() as client:
            scroll_resp = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/scroll",
                json={"limit": 10000, "with_payload": False, "with_vector": False},
                timeout=30.0,
            )
            if scroll_resp.status_code != 200:
                return JSONResponse(status_code=502, content={"detail": f"Qdrant scroll failed: {scroll_resp.status_code}"})

            all_points = scroll_resp.json().get("result", {}).get("points", [])
            point_ids = [p["id"] for p in all_points]

            if not point_ids:
                return {"deleted_count": 0, "collection": RAG_COLLECTION, "status": "flushed"}

            delete_resp = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/delete",
                json={"points": point_ids},
                timeout=30.0,
            )
            if delete_resp.status_code not in (200, 201):
                return JSONResponse(status_code=502, content={"detail": f"Qdrant delete failed: {delete_resp.status_code}"})

            EVICTION_LOG.clear()
            log.warning(f"RAG collection '{RAG_COLLECTION}' flushed ({len(point_ids)} points deleted)")

            return {
                "deleted_count": len(point_ids),
                "collection": RAG_COLLECTION,
                "status": "flushed",
            }
    except Exception as e:
        log.warning(f"RAG flush error: {e}")
        return JSONResponse(status_code=502, content={"detail": f"RAG flush error: {e}"})
