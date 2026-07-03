# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
# Development
./venv/bin/uvicorn app:app --host 0.0.0.0 --port 8080 --reload

# Production (via systemd)
sudo systemctl restart jarvischat

# Direct run
./venv/bin/python app.py
```

## Dependencies

```bash
./venv/bin/pip install -r requirements.txt
# Also requires: psutil jinja2 python-multipart pypdf (not in requirements.txt)
```

## Architecture

Modular FastAPI app — `app.py` wires routers, middleware, and lifespan. SQLite database auto-created at `jarvischat.db` on first run. No build step, single `templates/index.html`.

### Request Flow: `/api/chat`

1. User message saved to DB → conversation created if new
2. `process_remember_command()` intercepts "remember that..." / "forget about..." first
3. Optional `upload_context_id` → fetches document text from `upload_context` table, injects `[ATTACHED DOCUMENT]` into system prompt
4. `build_system_prompt()` assembles: profile + FTS5 memory search + Qdrant RAG + preset + skills + uploaded doc
5. Streamed to llama-server (`/v1/chat/completions`, `stream: true`, `logprobs: true`) via SSE
6. **Auto web search trigger**: if perplexity > 15.0 OR response matches `REFUSAL_PATTERNS`, re-queries with SearXNG results
7. Final response saved to DB; SSE `done` event sent with perplexity + tokens/sec

### Request Flow: `/api/search` (explicit search)

Bypasses perplexity/refusal — queries SearXNG directly then asks llama-server to summarize results.

### Request Flow: `/api/upload`

Multipart file upload → PDF/text extraction + chunking → optional Qdrant upsert + SQLite context storage (1hr expiry). Supports `mode=(context|ingest|both)`. Images upload as storage only — model cannot process image content.

### Request Flow: `/api/ingest`

Bearer-token-authenticated terminal RAG hook. Accepts raw text, chunks via `chunk_text()`, embeds via Ollama `/api/embeddings`, upserts to Qdrant.

### Memory System

FTS5 virtual table (`memories`) in SQLite. `search_memories()` uses BM25 ranking. `process_remember_command()` intercepts "remember that..." / "forget about..." before the message reaches the model and returns a confirmation string.

### Key Constants (`config.py`)

- `LLAMA_SERVER_BASE` — `http://192.168.50.108:8081` (ultron llama-server, RPC offloads to jarvis GPU)
- `SEARXNG_BASE` — `http://localhost:8888`
- `PERPLEXITY_THRESHOLD` — `15.0`
- `EMBED_URL` — `http://192.168.50.210:11434/api/embeddings` (Ollama on jarvis)
- `VERSION` — current version string

### External Services

| Service | Required | Port |
|---------|----------|------|
| **llama-server** (ultron) | Yes | 8081 + RPC :50052 (jarvis GPU) |
| **SearXNG** | No | 8888 |
| **wttr.in** | No | weather shortcut |
| **rocm-smi** | No | AMD GPU stats |
| **Qdrant** (ultron) | No | 6333 — RAG vector search |
| **Ollama** (jarvis) | No | 11434 — embeddings only |

### Database

`get_db()` opens a new connection per request (no pool). `init_db()` runs at startup via FastAPI `lifespan`. Tables: `conversations`, `messages`, `settings`, `profile` (singleton id=1), `memories` (FTS5), `upload_context`. Default settings seeded but never overwritten.

### SSE Protocol

All streaming endpoints yield `data: {json}\n\n`. Key event shapes:
- `{token, conversation_id}` — streaming token
- `{searching: true}` — web search triggered
- `{search_results: N}` — N results retrieved
- `{done: true, perplexity, tokens_per_sec, searched?}` — terminal event
- `{error: "...", error_key: "..."}` — error with incident key

### Deployment

Runs as systemd service under user `jarvischat`, working directory `/opt/jarvischat`. Logs via syslog (`journalctl -u jarvischat`). Version bumps via git tag + commit, deployed via `git pull && systemctl restart jarvischat`.
