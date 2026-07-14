"""
cAIc — Model pull/download helper.

Uses Ollama's pull API to download models that aren't available on the
inference server. Runs synchronously during startup.
"""
import asyncio
import json
import logging

import httpx

from config import DEFAULT_MODEL, LLAMA_SERVER_BASE, OLLAMA_BASE

log = logging.getLogger("caic")


async def _model_available_on_llama(model: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{LLAMA_SERVER_BASE}/v1/models")
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                return any(m.get("id") == model for m in models)
    except Exception:
        pass
    return False


async def _model_available_on_ollama(model: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{OLLAMA_BASE}/api/show", json={"name": model})
            return resp.status_code == 200
    except Exception:
        pass
    return False


async def _pull_via_ollama(model: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", f"{OLLAMA_BASE}/api/pull", json={"name": model}) as resp:
                if resp.status_code != 200:
                    log.warning("ollama pull returned %s for %s", resp.status_code, model)
                    return False
                async for line in resp.aiter_lines():
                    if line.strip():
                        try:
                            data = json.loads(line)
                            status = data.get("status", "")
                            if status:
                                log.info("ollama pull %s: %s", model, status)
                        except json.JSONDecodeError:
                            pass
                return True
    except httpx.ConnectError:
        log.warning("ollama not reachable at %s — cannot pull %s", OLLAMA_BASE, model)
    except Exception as e:
        log.warning("ollama pull error for %s: %s", model, e)
    return False


async def ensure_model(model: str = "") -> bool:
    """Ensure *model* is available for inference. Pull via Ollama if needed."""
    model = model or DEFAULT_MODEL
    if await _model_available_on_llama(model):
        log.info("model %s already available on llama-server", model)
        return True
    log.info("model %s not found on llama-server, checking Ollama", model)
    if await _model_available_on_ollama(model):
        log.info("model %s found on Ollama (available for embeddings)", model)
        return True
    log.info("model %s not found on Ollama either — pulling", model)
    ok = await _pull_via_ollama(model)
    if ok:
        log.info("model %s pulled successfully", model)
    else:
        log.warning("model %s could not be pulled", model)
    return ok
