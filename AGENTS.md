# JarvisChat — Agents Guide

## Run

```bash
uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

## Tests

```bash
python3 -m pytest tests/ -v
```

All tests use `tmp_path` fixtures + monkeypatched `httpx.AsyncClient.stream/get/post/put`. No external services needed. Test factories reset `SESSIONS`, `PIN_ATTEMPTS`, `RATE_EVENTS` globals — be careful not to let test state leak. Tests import directly from the correct modules (`db`, `security`, `config`, `search`, `rag`, `memory`, `routers.*`).

Every router has a dedicated test file:
| File | Covers |
|------|--------|
| `test_auth_capabilities.py` | `auth.py` — guest/admin sessions, origin blocking, logout |
| `test_chat_streaming_and_memory_paths.py` | `routers/chat.py` — streaming, auto-search, remember/forget, upload context injection |
| `test_completions.py` | `routers/completions.py` — API key auth, FIM, streaming, blocking, errors |
| `test_conversations.py` | `routers/conversations.py` — full CRUD, guest admin enforcement, attachment_count |
| `test_ingest.py` | `routers/ingest.py` — Bearer auth, chunk/embed/upsert, validation |
| `test_memories.py` | `routers/memories.py` — edit, search, stats endpoints |
| `test_models_router.py` | `routers/models.py` — models list, ps, show, stats, search/status |
| `test_presets.py` | `routers/presets.py` — full CRUD, default preset protection |
| `test_profile.py` | `routers/profile.py` — get, update, default, length validation |
| `test_search_route.py` | `routers/search_route.py` — explicit search flow, no results, errors |
| `test_search_url_sanitization.py` | `search.py` URL sanitizer |
| `test_settings_allowlist.py` | `routers/settings.py` — allowlisted key enforcement |
| `test_skills_framework.py` | `routers/skills.py` — list, toggle, unknown skill, prompt injection |
| `test_ip_allowlist.py` | IP allowlist helper + middleware |
| `test_rate_and_payload_guardrails.py` | Rate limits + payload size enforcement |
| `test_error_envelopes.py` | Global exception handler + stream error incidents |
| `test_upload.py` | `routers/upload.py` — upload, delete, link, by-conversation, attachment_count integration |

Modules that call `httpx.AsyncClient` (chat, completions, models, search_route, upload, ingest)
are mocked via `monkeypatch.setattr` on `AsyncClient.stream`, `.get`, or `.post`.
CPU stats in `models.py` (`api/stats`) use real `psutil`; GPU stats are
monkeypatched via `routers.models.get_gpu_stats`.

## Architecture

Refactored from single-file (`app.py`) into modules under project root:

| File | Role |
|------|------|
| `app.py` | FastAPI app, middleware, router registration |
| `config.py` | Constants, env vars, rate/payload limits, built-in skills registry, upload limits |
| `db.py` | SQLite schema, connection factory, settings helpers, upload_context CRUD |
| `auth.py` | PIN-based guest/admin sessions, auth routes |
| `security.py` | Rate limiting, origin checks, IP allowlist, audit/incident logging |
| `memory.py` | FTS5 memory CRUD, remember/forget command parsing |
| `search.py` | SearXNG integration, perplexity scoring, refusal detection |
| `rag.py` | Qdrant vector search + system prompt assembly + chunk_text() helper |
| `gpu.py` | AMD GPU stats via `rocm-smi` |
| `routers/` | One module per endpoint group (chat, search, skills, completions, upload, ingest) |

### Entrypoint / API keys

- `app.py` line 148: `uvicorn.run(app, ...)` when called directly
- `config.py` line 14: `LLAMA_SERVER_BASE` defaults to `http://192.168.50.108:8081` — llama-server on ultron, RPC-offloads GPU layers to jarvis :50052
- `config.py` line 17: `COMPLETIONS_API_KEY` read from `JARVISCHAT_COMPLETIONS_API_KEY` env var or auto-generates
- `config.py` line 13: `OLLAMA_BASE` is legacy/unused — all endpoints use `LLAMA_SERVER_BASE`

### Key flows

1. **`/api/chat`** → `process_remember_command()` intercepts "remember that..." / "forget about..." first → optional `upload_context_id` fetches document text from SQLite → `build_system_prompt()` (profile + FTS5 memory + Qdrant RAG + preset + skills + uploaded doc) → stream from llama-server with `logprobs: true` → if perplexity > 15.0 OR `REFUSAL_PATTERNS` match, re-query with SearXNG results
2. **`/api/search`** → bypasses perplexity/refusal, queries SearXNG directly → summarizes via llama-server
3. **`/v1/chat/completions`** → OpenAI-compatible for Continue.dev/IDE integration; FIM requests proxied without persistence
4. **`/api/upload`** → multipart file upload, PDF/text extraction, `mode=(context|ingest|both)`, stores SQLite context (1hr expiry) + Qdrant upsert
5. **`/api/ingest`** → Bearer token auth, programmatic RAG ingest (terminal hook, external tools)

### Perplexity / auto-search

The upstream request includes `"logprobs": true`. `parse_llama_stream_chunk()` extracts per-token logprobs from each chunk's `choices[0].logprobs.content[].logprob`. The `all_logprobs` list is populated during streaming, so `calculate_perplexity()` and `is_uncertain()` work correctly.

### Auth / lockdown

- Guest session by default (`POST /api/auth/guest`), admin unlock via 4-digit PIN (`POST /api/auth/login`)
- Admin required for PUT/DELETE/PATCH + all POST except allowlist (`/api/chat`, `/api/search`, `/api/auth/*`)
- `/api/ingest` is exempt from session auth — self-authenticates via Bearer token
- IP allowlist, rate limiting, origin checking, payload size limits — all enforced in `app.py` middleware
- Origin check applies to **all** `/api/` requests; returns `False` when both `Origin` and `Referer` are absent
- `JARVISCHAT_ADMIN_PIN` env var required on first boot (or `JARVISCHAT_ALLOW_DEFAULT_PIN=true`)

### Database

- SQLite at `jarvischat.db`, auto-created by `init_db()` on startup via FastAPI `lifespan`
- `get_db()` opens new connection per request (no pool). Close after use.
- FTS5 virtual table `memories` for full-text search with BM25 ranking.
- `upload_context` table: auto-expiring document storage for chat context injection.

### External services

| Service | Required | Port |
|---------|----------|------|
| llama-server (ultron) | Yes | 8081 + RPC :50052 (jarvis GPU) |
| SearXNG | No | 8888 |
| wttr.in | No | weather shortcut |
| rocm-smi | No | AMD GPU stats |
| Qdrant | No | 6333 (ultron) — RAG vector search |

### Config quirks

- `BODY_LIMIT_UPLOAD_BYTES` = 20MB for `/api/upload`; other paths use smaller limits
- `SUPPORTED_UPLOAD_TYPES` includes images (png/jpeg/gif/svg/webp) + text + PDF + JSON
- `UPLOAD_CONTEXT_EXPIRY_HOURS` = 1 hour
- Rate limits and payload caps in `config.py` — patch `security.RL_*` not `config.RL_*` for tests
- RAG embedding requests go to `EMBED_URL` at `/api/embeddings` (Ollama on jarvis :11434)

### SSE Protocol

All streaming endpoints yield `data: {json}\n\n`. Key shapes:
- `{token, conversation_id}` — streaming token
- `{searching: true}` — web search triggered
- `{search_results: N}` — N results (no raw_results payload)
- `{done: true, perplexity, tokens_per_sec, searched?}` — terminal
- `{error: "...", error_key: "..."}` — error with incident key
