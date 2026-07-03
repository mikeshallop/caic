"""JarvisChat routers — Hardware self-assessment endpoint."""
import json

from fastapi import APIRouter

import hardware

router = APIRouter()


@router.get("/api/hardware")
async def get_hardware_state():
    if hardware.HARDWARE_STATE_PATH.exists():
        return json.loads(hardware.HARDWARE_STATE_PATH.read_text())
    return {"status": "not_ready", "message": "Hardware assessment not yet complete"}
