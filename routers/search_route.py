"""JarvisChat routers - /api/search explicit search endpoint."""
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from config import DEFAULT_MODEL, LLAMA_SERVER_BASE, MAX_SEARCH_QUERY_CHARS
from crypto import encrypt_text
from db import get_db
from search import query_searxng, format_search_results
from routers.chat import parse_llama_stream_chunk
from security import read_json_body, log_incident, BODY_LIMIT_CHAT_BYTES

log = logging.getLogger("caic")
router = APIRouter()


@router.post("/api/search")
async def explicit_search(request: Request):
    body = await read_json_body(request, BODY_LIMIT_CHAT_BYTES)
    query = body.get("query", "").strip()
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        raise HTTPException(status_code=413, detail="Search query is too long")
    conv_id = body.get("conversation_id")
    model = body.get("model", DEFAULT_MODEL)

    if not query:
        raise HTTPException(status_code=400, detail="Empty query")

    private_chat = body.get("private_chat", False)
    if private_chat:
        raise HTTPException(status_code=403, detail="Web search is disabled in private chat mode")

    db = get_db()
    now = datetime.now(timezone.utc).isoformat()

    if not conv_id:
        conv_id = str(uuid.uuid4())
        title = query[:70] + "..." if len(query) > 70 else query
        db.execute("INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                   (conv_id, encrypt_text(title), model, now, now))
    else:
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))

    db.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
               (conv_id, "user", encrypt_text(query), now))
    db.commit()
    db.close()

    async def stream_search():
        yield f"data: {json.dumps({'conversation_id': conv_id, 'searching': True})}\n\n"

        results = await query_searxng(query, max_results=5)

        if not results:
            error_msg = "No search results found."
            yield f"data: {json.dumps({'token': error_msg, 'conversation_id': conv_id})}\n\n"
            db2 = get_db()
            db2.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                        (conv_id, "assistant", encrypt_text(error_msg), datetime.now(timezone.utc).isoformat()))
            db2.commit()
            db2.close()
            yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id})}\n\n"
            return

        yield f"data: {json.dumps({'search_results': len(results), 'conversation_id': conv_id})}\n\n"

        search_context = format_search_results(results)
        messages = [
            {"role": "system", "content": f"You have access to current web data. Answer directly using ONLY the data below. Be concise. No apologies. No disclaimers.\n\n{search_context}"},
            {"role": "user", "content": query},
        ]

        full_response = []
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "POST", f"{LLAMA_SERVER_BASE}/v1/chat/completions",
                    json={"model": model, "messages": messages, "stream": True},
                    timeout=httpx.Timeout(300.0, connect=10.0),
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            token, done, _, _ = parse_llama_stream_chunk(line)
                            if token:
                                full_response.append(token)
                                yield f"data: {json.dumps({'token': token, 'conversation_id': conv_id})}\n\n"
                            if done:
                                break
            except Exception as e:
                incident_key = log_incident("search_summarization_stream",
                                            message="Stream failure during explicit search summarization",
                                            request=request, exc=e)
                yield f"data: {json.dumps({'error': 'Search summarization could not complete right now.', 'error_key': incident_key})}\n\n"
                return

        summary = "".join(full_response)
        saved_msg = f"{summary}\n\n---\n*🔍 Web search results*"

        db2 = get_db()
        db2.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (conv_id, "assistant", encrypt_text(saved_msg), datetime.now(timezone.utc).isoformat()))
        db2.commit()
        db2.close()

        yield f"data: {json.dumps({'done': True, 'conversation_id': conv_id, 'searched': True})}\n\n"

    return StreamingResponse(stream_search(), media_type="text/event-stream")
