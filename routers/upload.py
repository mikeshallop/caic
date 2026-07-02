"""JarvisChat routers - /api/upload file/document attachment endpoint."""
import json
import logging
import os
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse

from config import UPLOAD_DIR, MAX_UPLOAD_BYTES, SUPPORTED_UPLOAD_TYPES, UPLOAD_CONTEXT_EXPIRY_HOURS
from db import get_db, insert_upload_context
from rag import chunk_text, QDRANT_URL, EMBED_URL, EMBED_MODEL, RAG_COLLECTION

log = logging.getLogger("jarvischat")
router = APIRouter()


@router.post("/api/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("both"),
    conversation_id: str = Form(""),
):
    if mode not in ("context", "ingest", "both"):
        raise HTTPException(status_code=422, detail="mode must be context, ingest, or both")

    if file.size and file.size > MAX_UPLOAD_BYTES:
        return JSONResponse(status_code=413, content={"detail": f"File exceeds {MAX_UPLOAD_BYTES} byte limit"})

    content_type = file.content_type or "application/octet-stream"
    if content_type not in SUPPORTED_UPLOAD_TYPES:
        return JSONResponse(status_code=415, content={"detail": f"Unsupported file type: {content_type}"})

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=422, detail="Empty file")

    if content_type == "application/pdf":
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(raw_bytes))
            extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            log.warning(f"PDF extraction error: {e}")
            raise HTTPException(status_code=422, detail="Failed to extract text from PDF")
    else:
        extracted = raw_bytes.decode("utf-8", errors="replace")

    result = {"filename": file.filename, "size_bytes": len(raw_bytes), "mode": mode}

    if mode in ("ingest", "both"):
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        chunks = chunk_text(extracted)
        ingested = 0
        async with httpx.AsyncClient() as client:
            for i, chunk in enumerate(chunks):
                embed_resp = await client.post(
                    f"{EMBED_URL}/api/embeddings",
                    json={"model": EMBED_MODEL, "prompt": chunk},
                    timeout=30.0,
                )
                if embed_resp.status_code != 200:
                    log.warning(f"Embedding failed for chunk {i}: {embed_resp.status_code}")
                    continue
                vector = embed_resp.json()["embedding"]
                point_id = f"{file.filename}-{i}"
                upsert_resp = await client.put(
                    f"{QDRANT_URL}/collections/{RAG_COLLECTION}/points?wait=true",
                    json={
                        "points": [{
                            "id": point_id,
                            "vector": vector,
                            "payload": {"text": chunk, "source": file.filename, "upload_date": datetime.now(timezone.utc).isoformat(), "type": "upload"},
                        }]
                    },
                    timeout=30.0,
                )
                if upsert_resp.status_code in (200, 201):
                    ingested += 1
                else:
                    log.warning(f"Qdrant upsert failed for chunk {i}: {upsert_resp.status_code}")
        result["chunks_ingested"] = ingested

    if mode in ("context", "both"):
        expires = (datetime.now(timezone.utc) + timedelta(hours=UPLOAD_CONTEXT_EXPIRY_HOURS)).isoformat()
        db = get_db()
        try:
            cid = insert_upload_context(db, conversation_id or "", file.filename or "unnamed", extracted, expires)
            db.commit()
            result["context_id"] = cid
        finally:
            db.close()

    result["message"] = f"Uploaded {file.filename}"
    return result
