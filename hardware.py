"""
cAIc — Startup hardware self-assessment.
"""
import asyncio
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

import httpx
import psutil

from config import LLAMA_SERVER_BASE, SEARXNG_BASE

log = logging.getLogger("caic")

HARDWARE_STATE_PATH = Path("hardware_state.json")
_TIMEOUT_EXPIRED = subprocess.TimeoutExpired


def _get_vram_darwin() -> tuple[int, int]:
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return 0, 0
        vram_total_mb = 0
        for line in result.stdout.splitlines():
            m = re.match(r"\s+VRAM \(Dynamic, Max\):\s+(\d+)\s+GB", line)
            if m:
                vram_total_mb += int(m.group(1)) * 1024
        return vram_total_mb, vram_total_mb  # free ~ total (unified memory)
    except (FileNotFoundError, _TIMEOUT_EXPIRED):
        log.warning("system_profiler not available — VRAM stats set to 0")
    except Exception as e:
        log.warning(f"Darwin VRAM error: {e}")
    return 0, 0


def _get_vram_linux() -> tuple[int, int]:
    vram_total_mb = 0
    vram_free_mb = 0
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for card, info in data.items():
                tot = info.get("VRAM Total (MB)", 0)
                free = info.get("VRAM Free (MB)", None)
                used = info.get("VRAM Used (MB)", 0)
                if tot:
                    vram_total_mb += int(tot)
                if free is not None:
                    vram_free_mb += int(free)
                else:
                    vram_free_mb += int(tot) - int(used)
    except (FileNotFoundError, _TIMEOUT_EXPIRED, json.JSONDecodeError):
        log.warning("rocm-smi not available or failed — VRAM stats set to 0")
    except Exception as e:
        log.warning(f"rocm-smi error: {e}")
    return vram_total_mb, vram_free_mb


async def assess_hardware() -> dict:
    mem = psutil.virtual_memory()
    ram_total_gb = round(mem.total / (1024 ** 3), 1)
    ram_available_gb = round(mem.available / (1024 ** 3), 1)
    cpu_count = psutil.cpu_count()

    if sys.platform == "darwin":
        vram_total_mb, vram_free_mb = _get_vram_darwin()
    else:
        vram_total_mb, vram_free_mb = _get_vram_linux()

    llama_reachable = False
    llama_models = []
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{LLAMA_SERVER_BASE}/v1/models")
            if resp.status_code == 200:
                llama_reachable = True
                data = resp.json()
                llama_models = [m.get("id", "") for m in data.get("data", [])]
    except Exception:
        log.warning("llama-server not reachable")

    qdrant_reachable = False
    qdrant_collections = []
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get("http://192.168.50.108:6333/collections")
            if resp.status_code == 200:
                qdrant_reachable = True
                data = resp.json()
                raw = data.get("result", {}).get("collections", [])
                qdrant_collections = [c.get("name", "") for c in raw]
    except Exception:
        log.warning("Qdrant not reachable")

    searxng_reachable = False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(SEARXNG_BASE)
            if resp.status_code == 200:
                searxng_reachable = True
    except Exception:
        log.warning("SearXNG not reachable")

    state = {
        "ram_total_gb": ram_total_gb,
        "ram_available_gb": ram_available_gb,
        "cpu_count": cpu_count,
        "vram_total_mb": vram_total_mb,
        "vram_free_mb": vram_free_mb,
        "llama_reachable": llama_reachable,
        "llama_models": llama_models,
        "qdrant_reachable": qdrant_reachable,
        "qdrant_collections": qdrant_collections,
        "searxng_reachable": searxng_reachable,
    }
    HARDWARE_STATE_PATH.write_text(json.dumps(state, indent=2))
    log.info(
        f"HW: {ram_total_gb}GB RAM, {vram_total_mb}MB VRAM, "
        f"llama={llama_reachable}, qdrant={qdrant_reachable}, searxng={searxng_reachable}"
    )
    return state
