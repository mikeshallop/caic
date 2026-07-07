"""JarvisChat routers - Conversation CRUD."""
import logging
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from db import get_db
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES
from config import DEFAULT_MODEL, MAX_CONVERSATION_TITLE_CHARS

log = logging.getLogger("caic")
router = APIRouter()


@router.get("/api/conversations")
async def list_conversations():
    db = get_db()
    rows = db.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
    result = []
    for r in rows:
        c = dict(r)
        attach_count = db.execute(
            "SELECT COUNT(*) FROM upload_context WHERE conversation_id = ?", (c["id"],)
        ).fetchone()[0]
        c["attachment_count"] = attach_count
        result.append(c)
    db.close()
    return result


@router.post("/api/conversations")
async def create_conversation(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    conv_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    model = body.get("model", DEFAULT_MODEL)
    title = str(body.get("title", "New Chat"))[:MAX_CONVERSATION_TITLE_CHARS]
    db = get_db()
    db.execute("INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
               (conv_id, title, model, now, now))
    db.commit()
    db.close()
    return {"id": conv_id, "title": title, "model": model, "created_at": now, "updated_at": now}


@router.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    db = get_db()
    conv = db.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if not conv:
        db.close()
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = db.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", (conv_id,)).fetchall()
    db.close()
    return {"conversation": dict(conv), "messages": [dict(m) for m in messages]}


@router.put("/api/conversations/{conv_id}")
async def update_conversation(conv_id: str, request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    if "title" in body:
        db.execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                   (str(body["title"])[:MAX_CONVERSATION_TITLE_CHARS], now, conv_id))
    if "model" in body:
        db.execute("UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
                   (body["model"], now, conv_id))
    db.commit()
    db.close()
    return {"status": "ok"}


@router.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    db = get_db()
    db.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    db.commit()
    db.close()
    return {"status": "ok"}


@router.delete("/api/conversations")
async def delete_all_conversations():
    db = get_db()
    db.execute("DELETE FROM messages")
    db.execute("DELETE FROM conversations")
    db.commit()
    db.close()
    log.info("Deleted all conversations")
    return {"status": "ok"}
