"""JarvisChat routers - Settings."""
from fastapi import APIRouter, HTTPException, Request
from db import get_db
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES
from config import MAX_SETTINGS_KEYS, MAX_SETTINGS_VALUE_CHARS, ALLOWED_SETTINGS_KEYS

router = APIRouter()


@router.get("/api/settings")
async def get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    db.close()
    return {row["key"]: row["value"] for row in rows}


@router.put("/api/settings")
async def update_settings(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Settings payload must be an object")
    if len(body) > MAX_SETTINGS_KEYS:
        raise HTTPException(status_code=413, detail="Too many settings in one request")
    unknown_keys = sorted(key for key in body.keys() if str(key) not in ALLOWED_SETTINGS_KEYS)
    if unknown_keys:
        raise HTTPException(status_code=400, detail=f"Unknown setting key(s): {', '.join(unknown_keys)}")
    db = get_db()
    for key, value in body.items():
        if len(str(key)) > 80 or len(str(value)) > MAX_SETTINGS_VALUE_CHARS:
            db.close()
            raise HTTPException(status_code=413, detail="Setting key/value too long")
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    db.commit()
    db.close()
    return {"status": "ok"}
