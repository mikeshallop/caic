"""JarvisChat routers — RAG corpus management admin endpoints."""
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from crypto import decrypt_text, encrypt_text
from eviction import get_rag_operational_stats, EVICTION_LOG
from rag import QDRANT_URL, RAG_COLLECTION, EMBED_URL, EMBED_MODEL

log = logging.getLogger("caic")
router = APIRouter()


def _normalise_date(payload: dict) -> str:
    return payload.get("ingest_date") or payload.get("upload_date") or ""


def _decrypt_payload(payload: dict) -> dict:
    text = payload.get("text", "")
    return {
        "text": decrypt_text(text),
        "source": payload.get("source"),
        "type": payload.get("type"),
        "date": _normalise_date(payload),
        "retrieval_count": payload.get("retrieval_count", 0),
        "topic": payload.get("topic"),
        "fact": payload.get("fact"),
        "filename": payload.get("filename"),
        "last_accessed": payload.get("last_accessed"),
    }


@router.get("/api/rag/stats")
async def rag_stats(request: Request):
    if getattr(request.state, "session_role", "none") != "admin":
        raise HTTPException(status_code=403, detail="Admin PIN required for this action")
    stats = await get_rag_operational_stats()
    stats["eviction_log_size"] = len(EVICTION_LOG)
    return stats


@router.get("/api/rag/points")
async def rag_list_points(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: str = Query(None),
    search: str = Query(None),
    source: str = Query(None),
    sort: str = Query("date"),
    order: str = Query("desc"),
):
    if getattr(request.state, "session_role", "none") != "admin":
        raise HTTPException(status_code=403, detail="Admin PIN required for this action")

    async with httpx.AsyncClient() as client:
        if search:
            embed_resp = await client.post(
                f"{EMBED_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": search},
                timeout=10.0,
            )
            if embed_resp.status_code != 200:
                return JSONResponse(status_code=502, content={"detail": "Embedding failed"})
            vector = embed_resp.json()["embedding"]
            search_body = {"vector": vector, "limit": limit, "with_payload": True}
            if source:
                search_body["filter"] = {"must": [{"match": {"key": "source", "value": source}}]}
            sr = await client.post(
                f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/search",
                json=search_body,
                timeout=10.0,
            )
            if sr.status_code != 200:
                return JSONResponse(status_code=502, content={"detail": "Qdrant search failed"})
            results = sr.json().get("result", [])
            points = []
            for r in results:
                p = _decrypt_payload(r.get("payload", {}))
                p["id"] = r["id"]
                p["score"] = r.get("score")
                points.append(p)
            return {"points": points, "total": len(points), "next_offset": None}

        scroll_body = {
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        if offset:
            scroll_body["offset"] = offset
        if source:
            scroll_body["filter"] = {"must": [{"match": {"key": "source", "value": source}}]}
        if sort:
            direction = "asc" if order == "asc" else "desc"
            scroll_body["order_by"] = {"key": sort, "direction": direction}

        sr = await client.post(
            f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/scroll",
            json=scroll_body,
            timeout=10.0,
        )
        if sr.status_code != 200:
            return JSONResponse(status_code=502, content={"detail": "Qdrant scroll failed"})
        result = sr.json().get("result", {})
        raw_points = result.get("points", [])
        next_offset = result.get("next_page_offset")

        info_resp = await client.get(
            f"{QDRANT_URL}/collections/{RAG_COLLECTION}",
            timeout=5.0,
        )
        total = 0
        if info_resp.status_code == 200:
            total = info_resp.json().get("result", {}).get("vectors_count", 0)

        points = []
        for r in raw_points:
            p = _decrypt_payload(r.get("payload", {}))
            p["id"] = r["id"]
            points.append(p)
        return {"points": points, "total": total, "next_offset": next_offset}


@router.get("/api/rag/point/{point_id}")
async def rag_get_point(point_id: str, request: Request):
    if getattr(request.state, "session_role", "none") != "admin":
        raise HTTPException(status_code=403, detail="Admin PIN required for this action")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/{point_id}",
            timeout=10.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Point not found")
        result = resp.json().get("result", {})
        if not result:
            raise HTTPException(status_code=404, detail="Point not found")
        payload = result.get("payload", {})
        point = _decrypt_payload(payload)
        point["id"] = result["id"]
        return point


@router.delete("/api/rag/point/{point_id}")
async def rag_delete_point(point_id: str, request: Request):
    if getattr(request.state, "session_role", "none") != "admin":
        raise HTTPException(status_code=403, detail="Admin PIN required for this action")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/delete",
            json={"points": [point_id]},
            timeout=10.0,
        )
        if resp.status_code not in (200, 201) and resp.status_code != 404:
            return JSONResponse(status_code=502, content={"detail": "Qdrant delete failed"})
        log.info(f"RAG point {point_id} deleted by admin")
        return {"status": "deleted", "id": point_id}


@router.patch("/api/rag/point/{point_id}")
async def rag_update_point(point_id: str, request: Request):
    if getattr(request.state, "session_role", "none") != "admin":
        raise HTTPException(status_code=403, detail="Admin PIN required for this action")

    body = await request.json()
    new_text = (body.get("text") or "").strip()
    if not new_text:
        raise HTTPException(status_code=400, detail="text required")

    async with httpx.AsyncClient() as client:
        get_resp = await client.get(
            f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points/{point_id}",
            timeout=10.0,
        )
        if get_resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Point not found")
        existing = get_resp.json().get("result", {})
        if not existing:
            raise HTTPException(status_code=404, detail="Point not found")

        old_payload = existing.get("payload", {})
        embed_resp = await client.post(
            f"{EMBED_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": new_text},
            timeout=10.0,
        )
        if embed_resp.status_code != 200:
            return JSONResponse(status_code=502, content={"detail": "Embedding failed"})
        vector = embed_resp.json()["embedding"]

        new_payload = dict(old_payload)
        new_payload["text"] = encrypt_text(new_text)
        date_field = "ingest_date" if "ingest_date" in old_payload else "upload_date"
        new_payload[date_field] = datetime.now(timezone.utc).isoformat()

        upsert_resp = await client.put(
            f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points?wait=true",
            json={"points": [{"id": point_id, "vector": vector, "payload": new_payload}]},
            timeout=10.0,
        )
        if upsert_resp.status_code not in (200, 201):
            return JSONResponse(status_code=502, content={"detail": "Qdrant upsert failed"})

        log.info(f"RAG point {point_id} updated by admin")
        return {"status": "updated", "id": point_id}


@router.post("/api/rag/flush")
async def rag_flush(request: Request):
    if getattr(request.state, "session_role", "none") != "admin":
        raise HTTPException(status_code=403, detail="Admin PIN required for this action")
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
