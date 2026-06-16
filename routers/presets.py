"""JarvisChat routers - System prompt presets."""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from db import get_db
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES
from config import MAX_PRESET_NAME_CHARS, MAX_PRESET_PROMPT_CHARS

router = APIRouter()


@router.get("/api/presets")
async def list_presets():
    db = get_db()
    rows = db.execute("SELECT * FROM system_presets ORDER BY is_default DESC, name ASC").fetchall()
    db.close()
    return [dict(r) for r in rows]


@router.post("/api/presets")
async def create_preset(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    name = str(body.get("name", "")).strip()
    prompt = str(body.get("prompt", "")).strip()
    if not name or not prompt:
        raise HTTPException(status_code=400, detail="Preset name and prompt are required")
    if len(name) > MAX_PRESET_NAME_CHARS or len(prompt) > MAX_PRESET_PROMPT_CHARS:
        raise HTTPException(status_code=413, detail="Preset fields are too long")
    preset_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute("INSERT INTO system_presets (id, name, prompt, is_default, created_at) VALUES (?, ?, ?, 0, ?)",
               (preset_id, name, prompt, now))
    db.commit()
    db.close()
    return {"id": preset_id, "name": name, "prompt": prompt}


@router.put("/api/presets/{preset_id}")
async def update_preset(preset_id: str, request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    name = str(body.get("name", "")).strip()
    prompt = str(body.get("prompt", "")).strip()
    if not name or not prompt:
        raise HTTPException(status_code=400, detail="Preset name and prompt are required")
    if len(name) > MAX_PRESET_NAME_CHARS or len(prompt) > MAX_PRESET_PROMPT_CHARS:
        raise HTTPException(status_code=413, detail="Preset fields are too long")
    db = get_db()
    db.execute("UPDATE system_presets SET name = ?, prompt = ? WHERE id = ?", (name, prompt, preset_id))
    db.commit()
    db.close()
    return {"status": "ok"}


@router.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str):
    db = get_db()
    db.execute("DELETE FROM system_presets WHERE id = ? AND is_default = 0", (preset_id,))
    db.commit()
    db.close()
    return {"status": "ok"}
