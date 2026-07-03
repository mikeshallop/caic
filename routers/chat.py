"""JarvisChat routers - /api/chat streaming endpoint."""
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from config import DEFAULT_MODEL, LLAMA_SERVER_BASE
from db import get_db, get_upload_context
from memory import process_remember_command
from rag import build_system_prompt
from search import (calculate_perplexity, is_uncertain, is_refusal,
                    clean_hedging, format_search_results, format_direct_answer,
                    extract_search_query, query_searxng)
from security import read_json_body, log_incident, BODY_LIMIT_CHAT_BYTES
from config import MAX_CHAT_MESSAGE_CHARS

log = logging.getLogger("jarvischat")
router = APIRouter()


def parse_llama_stream_chunk(line: str) -> tuple:
    if line.startswith("data: "):
        line = line[6:]
    if line.strip() == "[DONE]":
        return None, True, {}, []
    try:
        chunk = json.loads(line)
        choices = chunk.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            token = delta.get("content")
            finish = choices[0].get("finish_reason")
            stats = {}
            logprobs_list = []
            logprobs_info = choices[0].get("logprobs")
            if logprobs_info:
                content_logprobs = logprobs_info.get("content", [])
                for entry in content_logprobs:
                    if "logprob" in entry:
                        logprobs_list.append({"logprob": entry["logprob"]})
            if finish == "stop":
                usage = chunk.get("usage", {})
                stats["tokens_per_sec"] = usage.get("tokens_per_second", 0.0)
            return token, finish == "stop", stats, logprobs_list
        if "message" in chunk and "content" in chunk["message"]:
            token = chunk["message"]["content"]
            done = chunk.get("done", False)
            stats = {}
            if done:
                eval_count = chunk.get("eval_count", 0)
                eval_duration = chunk.get("eval_duration", 0)
                stats["tokens_per_sec"] = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0
            return token, done, stats, []
    except json.JSONDecodeError:
        pass
    return None, False, {}, []


@router.post("/api/chat")
async def chat(request: Request):
    body = await read_json_body(request, BODY_LIMIT_CHAT_BYTES)
    conv_id = body.get("conversation_id")
    user_message = body.get("message", "").strip()
    if len(user_message) > MAX_CHAT_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail="Chat message is too long")
    model = body.get("model", DEFAULT_MODEL)
    preset_prompt = body.get("system_prompt", "")
    upload_context_id = body.get("upload_context_id")

    if not user_message:
        raise HTTPException(status_code=400, detail="Empty message")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    settings = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM settings").fetchall()}
    search_enabled = settings.get("search_enabled", "true") == "true"

    upload_doc = None
    if upload_context_id:
        ctx = get_upload_context(db, upload_context_id)
        if ctx:
            upload_doc = f"[ATTACHED DOCUMENT: {ctx['filename']}]\n{ctx['content']}\n[END DOCUMENT]"
        else:
            log.warning(f"upload_context_id {upload_context_id} not found or expired, continuing without it")

    remember_response = process_remember_command(user_message)

    if not conv_id:
        conv_id = str(uuid.uuid4())
        title = user_message[:80] + ("..." if len(user_message) > 80 else "")
        db.execute("INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                   (conv_id, title, model, now, now))
    else:
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))

    db.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
               (conv_id, "user", user_message, now))
    db.commit()

    history_rows = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC", (conv_id,)
    ).fetchall()
    extra_prompt = preset_prompt
    if upload_doc:
        extra_prompt = (extra_prompt + "\n\n" + upload_doc) if extra_prompt else upload_doc
    system_prompt = await build_system_prompt(db, extra_prompt, user_message)
    db.close()

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for row in history_rows:
        messages.append({"role": row["role"], "content": row["content"]})

    upstream_payload = {"model": model, "messages": messages, "stream": True, "logprobs": True}

    async def stream_response():
        full_response = []
        all_logprobs = []
        tokens_per_sec = 0.0

        if remember_response:
            yield f"data: {json.dumps({'token': remember_response + chr(10) + chr(10), 'conversation_id': conv_id})}\n\n"

        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "POST", f"{LLAMA_SERVER_BASE}/v1/chat/completions",
                    json=upstream_payload,
                    timeout=httpx.Timeout(300.0, connect=10.0),
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            token, done, stats, chunk_logprobs = parse_llama_stream_chunk(line)
                            if chunk_logprobs:
                                all_logprobs.extend(chunk_logprobs)
                            if token:
                                full_response.append(token)
                                yield f"data: {json.dumps({'token': token, 'conversation_id': conv_id})}\n\n"
                            if done:
                                tokens_per_sec = stats.get("tokens_per_sec", 0.0)

                assistant_msg = "".join(full_response)
                perplexity = calculate_perplexity(all_logprobs) if all_logprobs else 0.0
                should_search = is_uncertain(all_logprobs) or is_refusal(assistant_msg)

                if search_enabled and should_search:
                    yield f"data: {json.dumps({'searching': True, 'conversation_id': conv_id})}\n\n"
                    search_query = extract_search_query(user_message)
                    search_results = await query_searxng(search_query)

                    if search_results:
                        search_context = format_search_results(search_results)
                        augmented_messages = []
                        if system_prompt:
                            augmented_messages.append({"role": "system", "content": system_prompt + "\n\n" + search_context})
                        else:
                            augmented_messages.append({"role": "system", "content": search_context})
                        for row in history_rows[:-1]:
                            augmented_messages.append({"role": row["role"], "content": row["content"]})
                        augmented_messages.append({"role": "user", "content": user_message})

                        yield f"data: {json.dumps({'search_results': len(search_results), 'conversation_id': conv_id})}\n\n"

                        augmented_response = []
                        async with client.stream(
                            "POST", f"{LLAMA_SERVER_BASE}/v1/chat/completions",
                            json={"model": model, "messages": augmented_messages, "stream": True},
                            timeout=httpx.Timeout(300.0, connect=10.0),
                        ) as resp2:
                            async for line in resp2.aiter_lines():
                                if line.strip():
                                    token2, done2, _, _ = parse_llama_stream_chunk(line)
                                    if token2:
                                        augmented_response.append(token2)
                                    if done2:
                                        break

                        raw_response = "".join(augmented_response) or assistant_msg
                        cleaned_response = clean_hedging(raw_response)
                        if is_refusal(cleaned_response) or len(cleaned_response) < 20:
                            cleaned_response = format_direct_answer(user_message, search_results)

                        yield f"data: {json.dumps({'token': cleaned_response, 'conversation_id': conv_id, 'augmented': True})}\n\n"

                        saved_msg = cleaned_response + "\n\n---\n*🔍 Enhanced with web search results*"
                        if remember_response:
                            saved_msg = remember_response + "\n\n" + saved_msg

                        db2 = get_db()
                        db2.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                                    (conv_id, "assistant", saved_msg, datetime.now(timezone.utc).isoformat()))
                        db2.commit()
                        db2.close()

                        yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'searched': True, 'perplexity': round(perplexity, 2), 'tokens_per_sec': round(tokens_per_sec, 1)})}\n\n"
                        return

                saved_msg = assistant_msg
                if remember_response:
                    saved_msg = remember_response + "\n\n" + saved_msg

                db2 = get_db()
                db2.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                            (conv_id, "assistant", saved_msg, datetime.now(timezone.utc).isoformat()))
                db2.commit()
                db2.close()

                yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'perplexity': round(perplexity, 2), 'tokens_per_sec': round(tokens_per_sec, 1)})}\n\n"

            except httpx.RemoteProtocolError:
                pass
            except httpx.ConnectError:
                yield f"data: {json.dumps({'error': 'Cannot connect to inference server. Is it running?'})}\n\n"
            except Exception as e:
                incident_key = log_incident("chat_stream", message="Inference stream failure during chat response",
                                            request=request, exc=e)
                yield f"data: {json.dumps({'error': 'Chat response generation failed before completion. Use the incident key for support lookup.', 'error_key': incident_key})}\n\n"

    return StreamingResponse(stream_response(), media_type="text/event-stream")
