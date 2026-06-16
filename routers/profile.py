"""JarvisChat routers - Profile."""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from db import get_db
from security import read_json_body, BODY_LIMIT_PROFILE_BYTES
from config import MAX_PROFILE_CHARS, DEFAULT_PROFILE

router = APIRouter()


@router.get("/api/profile")
async def get_profile():
    db = get_db()
    row = db.execute("SELECT content, updated_at FROM profile WHERE id = 1").fetchone()
    db.close()
    return ({"content": row["content"], "updated_at": row["updated_at"]} if row
            else {"content": "", "updated_at": ""})


@router.put("/api/profile")
async def update_profile(request: Request):
    body = await read_json_body(request, BODY_LIMIT_PROFILE_BYTES)
    content = str(body.get("content", ""))
    if len(content) > MAX_PROFILE_CHARS:
        raise HTTPException(status_code=413, detail="Profile content is too long")
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute("UPDATE profile SET content = ?, updated_at = ? WHERE id = 1", (content, now))
    db.commit()
    db.close()
    return {"status": "ok", "updated_at": now}


@router.get("/api/profile/default")
async def get_default_profile():
    return {"content": DEFAULT_PROFILE}
