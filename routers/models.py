"""
JarvisChat routers - Model listing, system stats.
"""
import logging
from typing import Optional

import httpx
import psutil
from fastapi import APIRouter, HTTPException, Request

from config import LLAMA_SERVER_BASE
from gpu import get_gpu_stats
from security import read_json_body, BODY_LIMIT_DEFAULT_BYTES

log = logging.getLogger("jarvischat")
router = APIRouter()


@router.get("/api/models")
async def list_models():
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{LLAMA_SERVER_BASE}/v1/models", timeout=10)
            data = resp.json()
            models = [{"name": m["id"], "model": m["id"]} for m in data.get("data", [])]
            return {"models": models}
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to inference server.")


@router.get("/api/ps")
async def running_models():
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{LLAMA_SERVER_BASE}/v1/models", timeout=10)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to inference server.")


@router.post("/api/show")
async def show_model(request: Request):
    body = await read_json_body(request, BODY_LIMIT_DEFAULT_BYTES)
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{LLAMA_SERVER_BASE}/api/show", json=body, timeout=10)
            return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Cannot connect to inference server.")


@router.get("/api/stats")
async def system_stats():
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    gpu = get_gpu_stats()
    return {
        "cpu_percent": round(cpu_percent, 1),
        "memory_percent": round(memory.percent, 1),
        "memory_used_gb": round(memory.used / (1024**3), 1),
        "memory_total_gb": round(memory.total / (1024**3), 1),
        "gpu_percent": gpu["gpu_percent"],
        "vram_percent": gpu["vram_percent"],
        "gpu_available": gpu["available"],
    }


@router.get("/api/search/status")
async def search_status():
    from config import SEARXNG_BASE
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{SEARXNG_BASE}/search",
                                    params={"q": "test", "format": "json"}, timeout=5)
            return {"available": resp.status_code == 200}
        except Exception:
            return {"available": False}
