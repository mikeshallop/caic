"""JarvisChat routers - Skills."""
from fastapi import APIRouter, HTTPException, Request
from db import get_db, get_setting, list_skills_with_state, set_skill_enabled
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES
from config import MAX_SKILL_KEY_CHARS, SKILLS_BY_KEY

router = APIRouter()


@router.get("/api/skills")
async def list_skills():
    db = get_db()
    skills = list_skills_with_state(db)
    db.close()
    return {"skills": skills, "count": len(skills)}


@router.get("/api/skills/active")
async def list_active_skills():
    db = get_db()
    skills_enabled = get_setting(db, "skills_enabled", "true") == "true"
    skills = list_skills_with_state(db)
    db.close()
    active = [s for s in skills if s["enabled"]] if skills_enabled else []
    return {"skills": active, "count": len(active), "skills_enabled": skills_enabled}


@router.put("/api/skills/{skill_key}")
async def update_skill(skill_key: str, request: Request):
    skill_key = skill_key.strip()
    if len(skill_key) > MAX_SKILL_KEY_CHARS or skill_key not in SKILLS_BY_KEY:
        raise HTTPException(status_code=404, detail="Unknown skill")
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    if "enabled" not in body or not isinstance(body.get("enabled"), bool):
        raise HTTPException(status_code=400, detail="Field 'enabled' (boolean) is required")
    db = get_db()
    set_skill_enabled(db, skill_key, bool(body["enabled"]))
    db.commit()
    skills = list_skills_with_state(db)
    db.close()
    updated = next((s for s in skills if s["key"] == skill_key), None)
    return {"status": "ok", "skill": updated}
