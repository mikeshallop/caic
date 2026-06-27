# JarvisChat — Agents Guide

## Run

```bash
./venv/bin/uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

## Tests

```bash
./venv/bin/python -m pytest tests/ -v
```

All tests use `tmp_path` fixtures + monkeypatched `httpx.AsyncClient.stream`. No external services needed. Test factories reset `SESSIONS`, `PIN_ATTEMPTS`, `RATE_EVENTS` globals — be careful not to let test state leak. After the modular refactor, tests import directly from the correct modules (`db`, `security`, `config`, `search`, `rag`, `memory`, `routers.*`) — not from the old monolithic `app` namespace.

Every router has a dedicated test file:
| File | Covers |
|------|--------|
| `test_auth_capabilities.py` | `auth.py` — guest/admin sessions, origin blocking, logout |
| `test_chat_streaming_and_memory_paths.py` | `routers/chat.py` — streaming, auto-search, remember/forget |
| `test_completions.py` | `routers/completions.py` — API key auth, FIM, streaming, blocking, errors |
| `test_conversations.py` | `routers/conversations.py` — full CRUD, guest admin enforcement |
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

Modules that call `httpx.AsyncClient` (chat, completions, models, search_route)
are mocked via `monkeypatch.setattr` on `AsyncClient.stream`, `.get`, or `.post`.
CPU stats in `models.py` (`api/stats`) use real `psutil`; GPU stats are
monkeypatched via `routers.models.get_gpu_stats`.

## Architecture

Refactored from single-file (`app.py`) into modules under project root:

| File | Role |
|------|------|
| `app.py` | FastAPI app, middleware, router registration |
| `config.py` | Constants, env vars, rate/payload limits, built-in skills registry |
| `db.py` | SQLite schema, connection factory, settings helpers |
| `auth.py` | PIN-based guest/admin sessions, auth routes |
| `security.py` | Rate limiting, origin checks, IP allowlist, audit/incident logging |
| `memory.py` | FTS5 memory CRUD, remember/forget command parsing |
| `search.py` | SearXNG integration, perplexity scoring, refusal detection |
| `rag.py` | Qdrant vector search + system prompt assembly |
| `gpu.py` | AMD GPU stats via `rocm-smi` |
| `routers/` | One module per endpoint group (chat, search, skills, completions, etc.) |

### Entrypoint / API keys

- `app.py` line 148: `uvicorn.run(app, ...)` when called directly
- `config.py` line 14: `LLAMA_SERVER_BASE` defaults to `http://192.168.50.108:8081` — llama-server, **not** standard Ollama port, used by all model endpoints
- `config.py` line 17: `COMPLETIONS_API_KEY` read from `JARVISCHAT_COMPLETIONS_API_KEY` env var or auto-generates a random key — no longer a missing import
- `config.py` line 13: `OLLAMA_BASE` is legacy/unused — all endpoints now use `LLAMA_SERVER_BASE`

### Key flows

1. **`/api/chat`** → `process_remember_command()` intercepts "remember that..." / "forget about..." first → else `build_system_prompt()` (profile + FTS5 memory + Qdrant RAG + preset + skills) → stream from llama-server with `logprobs: true` → if perplexity > 15.0 OR `REFUSAL_PATTERNS` match, re-query with SearXNG results
2. **`/api/search`** → bypasses perplexity/refusal, queries SearXNG directly → summarizes via llama-server (no raw results leaked in SSE)
3. **`/v1/chat/completions`** → OpenAI-compatible for Continue.dev/IDE integration; FIM requests proxied without persistence

### Perplexity / auto-search

The upstream request includes `"logprobs": true`. `parse_llama_stream_chunk()` extracts per-token logprobs from each chunk's `choices[0].logprobs.content[].logprob`. The `all_logprobs` list is populated during streaming, so `calculate_perplexity()` and `is_uncertain()` work correctly — auto-search on high perplexity is no longer dead code.

### Auth / lockdown

- Guest session by default (`POST /api/auth/guest`), admin unlock via 4-digit PIN (`POST /api/auth/login`)
- Admin required for PUT/DELETE/PATCH + all POST except allowlist (`/api/chat`, `/api/search`, `/api/auth/*`)
- IP allowlist, rate limiting, origin checking, payload size limits — all enforced in `app.py` middleware
- Origin check applies to **all** `/api/` requests (not just state-changing methods); `origin_allowed()` returns `False` when both `Origin` and `Referer` headers are absent, closing CSRF read gap
- `JARVISCHAT_ADMIN_PIN` env var required on first boot (or `JARVISCHAT_ALLOW_DEFAULT_PIN=true`)

### Database

- SQLite at `jarvischat.db`, auto-created by `init_db()` on startup via FastAPI `lifespan`
- `get_db()` opens new connection per request (no pool). Close after use.
- FTS5 virtual table `memories` for full-text search with BM25 ranking. FTS5 operator keywords (`AND`, `OR`, `NOT`, `NEAR`) are double-quoted to prevent parse errors.

### External services

| Service | Required | Port |
|---------|----------|------|
| llama-server (OpenAI-compat API) | Yes | 8081 (ultron) or env `LLAMA_SERVER_BASE` |
| SearXNG | No | 8888 |
| wttr.in | No | weather shortcut bypasses SearXNG; curl UA for plain-text output |
| rocm-smi | No | AMD GPU stats |
| Qdrant | No | 6333 (ultron) — RAG vector search |

### Config quirks

- Rate limits and payload caps in `config.py` — tweak for testing by monkeypatching module attributes (note: patch `security.RL_*` not `config.RL_*` since `security` imports bindings separately)
- `ALLOWED_SETTINGS_KEYS` in `config.py` controls which keys the UI can write via `/api/settings`
- Settings table seeded with defaults (`profile_enabled`, `search_enabled`, `memory_enabled`, `skills_enabled`, `default_model`) — never overwritten by `init_db()`
- Profile table uses singleton row `id=1`
- RAG embedding requests go to `LLAMA_SERVER_BASE` at `/api/embeddings`

### SSE Protocol

All streaming endpoints yield `data: {json}\n\n`. Key shapes:
- `{token, conversation_id}` — streaming token
- `{searching: true}` — web search triggered
- `{search_results: N}` — N results (no raw_results payload)
- `{done: true, perplexity, tokens_per_sec, searched?}` — terminal
- `{error: "...", error_key: "..."}` — error with incident key
