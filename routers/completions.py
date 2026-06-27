"""
JarvisChat - /v1/chat/completions router.
OpenAI-compatible endpoint for IDE integration (Continue.dev, etc.).
Runs all requests through the full jC pipeline: profile + RAG + memory injection.
FIM (fill-in-the-middle) requests are proxied directly — not persisted.
Chat-style requests are persisted to conversation history.
Auth: static Bearer token via COMPLETIONS_API_KEY in config.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

from config import DEFAULT_MODEL, LLAMA_SERVER_BASE, COMPLETIONS_API_KEY
from db import get_db
from rag import build_system_prompt
from routers.chat import parse_llama_stream_chunk

log = logging.getLogger("jarvischat")
router = APIRouter()


def _check_api_key(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[7:].strip()
    if token != COMPLETIONS_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _is_fim_request(body: dict) -> bool:
    """
    FIM (fill-in-the-middle) requests use a 'prompt' + optional 'suffix' structure
    rather than a 'messages' array. Continue.dev sends these for inline autocomplete.
    We proxy them directly without pipeline injection or persistence.
    """
    return "prompt" in body and "messages" not in body


def _build_openai_chunk(token: str, model: str, conv_id: str) -> str:
    chunk = {
        "id": f"chatcmpl-{conv_id}",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"content": token},
            "finish_reason": None,
        }],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _build_openai_stop_chunk(model: str, conv_id: str) -> str:
    chunk = {
        "id": f"chatcmpl-{conv_id}",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop",
        }],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _build_openai_response(content: str, model: str, conv_id: str) -> dict:
    """Non-streaming response envelope."""
    return {
        "id": f"chatcmpl-{conv_id}",
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    _check_api_key(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # --- FIM passthrough ---
    if _is_fim_request(body):
        return await _fim_passthrough(body)

    # --- Chat completion ---
    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    model = body.get("model", DEFAULT_MODEL)
    stream = body.get("stream", True)

    # Extract the latest user message for RAG + conversation title
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "").strip()
            break

    if not user_message:
        raise HTTPException(status_code=400, detail="No user message found")

    # --- Persist conversation ---
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conv_id = str(uuid.uuid4())
    title = f"[IDE] {user_message[:72]}{'...' if len(user_message) > 72 else ''}"
    db.execute(
        "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (conv_id, title, model, now, now),
    )
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role in ("user", "assistant"):
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conv_id, role, content, now),
            )
    db.commit()

    # --- Build system prompt through full jC pipeline ---
    system_prompt = await build_system_prompt(db, "", user_message)
    db.close()

    # Assemble messages for upstream: inject jC system prompt, preserve history
    upstream_messages = []
    if system_prompt:
        upstream_messages.append({"role": "system", "content": system_prompt})

    # Strip any system messages from the incoming payload — jC owns the system prompt
    for msg in messages:
        if msg.get("role") != "system":
            upstream_messages.append(msg)

    upstream_payload = {
        "model": model,
        "messages": upstream_messages,
        "stream": True,  # always stream from upstream; we buffer if client wants non-stream
    }

    if stream:
        return StreamingResponse(
            _stream_chat(upstream_payload, model, conv_id, request),
            media_type="text/event-stream",
        )
    else:
        return await _blocking_chat(upstream_payload, model, conv_id, request)


async def _stream_chat(payload: dict, model: str, conv_id: str, request: Request):
    """Stream tokens to client in OpenAI SSE format, persist assistant response."""
    full_response = []

    async with httpx.AsyncClient() as client:
        try:
            async with client.stream(
                "POST", f"{LLAMA_SERVER_BASE}/v1/chat/completions",
                json=payload,
                timeout=httpx.Timeout(300.0, connect=10.0),
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    token, done, _, _ = parse_llama_stream_chunk(line)
                    if token:
                        full_response.append(token)
                        yield _build_openai_chunk(token, model, conv_id)
                    if done:
                        break

            yield _build_openai_stop_chunk(model, conv_id)
            yield "data: [DONE]\n\n"

            # Persist assistant response
            assistant_msg = "".join(full_response)
            if assistant_msg:
                db = get_db()
                db.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (conv_id, "assistant", assistant_msg, datetime.now(timezone.utc).isoformat()),
                )
                db.commit()
                db.close()

        except httpx.ConnectError:
            err = {"error": {"message": "Cannot connect to inference server", "type": "connection_error"}}
            yield f"data: {json.dumps(err)}\n\n"
        except Exception as e:
            log.error(f"completions stream error: {e}")
            err = {"error": {"message": "Stream failed", "type": "server_error"}}
            yield f"data: {json.dumps(err)}\n\n"


async def _blocking_chat(payload: dict, model: str, conv_id: str, request: Request) -> JSONResponse:
    """Accumulate full response, return as standard OpenAI JSON object."""
    full_response = []

    async with httpx.AsyncClient() as client:
        try:
            async with client.stream(
                "POST", f"{LLAMA_SERVER_BASE}/v1/chat/completions",
                json=payload,
                timeout=httpx.Timeout(300.0, connect=10.0),
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    token, done, _, _ = parse_llama_stream_chunk(line)
                    if token:
                        full_response.append(token)
                    if done:
                        break
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="Cannot connect to inference server")
        except Exception as e:
            log.error(f"completions blocking error: {e}")
            raise HTTPException(status_code=500, detail="Inference request failed")

    assistant_msg = "".join(full_response)

    if assistant_msg:
        db = get_db()
        db.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conv_id, "assistant", assistant_msg, datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
        db.close()

    return JSONResponse(content=_build_openai_response(assistant_msg, model, conv_id))


async def _fim_passthrough(body: dict) -> JSONResponse:
    """
    Proxy FIM requests directly to llama-server without pipeline injection.
    Not persisted — autocomplete noise has no RAG value.
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{LLAMA_SERVER_BASE}/v1/completions",
                json=body,
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="Cannot connect to inference server")
        except Exception as e:
            log.error(f"FIM passthrough error: {e}")
            raise HTTPException(status_code=500, detail="FIM request failed")