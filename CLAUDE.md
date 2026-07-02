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

Single-file FastAPI backend (`app.py`) + single-template frontend (`templates/index.html`). No build step. SQLite database auto-created at `jarvischat.db` on first run.

### Request Flow: `/api/chat`

1. User message saved to DB → conversation created if new
2. `build_system_prompt()` assembles: profile + FTS5 memory search results + preset prompt
3. Streamed to Ollama (`/api/chat`, `stream: true`, `logprobs: true`) via SSE
4. **Auto web search trigger**: if perplexity > 15.0 OR response matches `REFUSAL_PATTERNS`, re-queries Ollama with SearXNG results prepended to system prompt
5. Final response saved to DB; SSE `done` event sent with perplexity + tokens/sec

### Request Flow: `/api/search` (explicit search)

Bypasses perplexity/refusal detection entirely — queries SearXNG directly then asks Ollama to summarize with results as system context.

### Memory System

FTS5 virtual table (`memories`) in SQLite. `search_memories()` uses BM25 ranking. `process_remember_command()` intercepts "remember that..." / "forget about..." before the message reaches Ollama and returns a confirmation string. Topic auto-detection via keyword matching in `detect_topic()`.

### Key Constants (top of `app.py`)

- `OLLAMA_BASE` — `http://localhost:11434`
- `SEARXNG_BASE` — `http://localhost:8888`
- `PERPLEXITY_THRESHOLD` — `15.0` (controls auto-search sensitivity)
- `DEFAULT_MODEL` — `llama3.1:latest`

### External Services

- **Ollama** — required, must be running on port 11434
- **SearXNG** — optional, port 8888; `GET /api/search/status` probes availability
- **wttr.in** — weather shortcut in `query_searxng()`, bypasses SearXNG for weather queries
- **rocm-smi** — AMD GPU stats via subprocess; gracefully degrades if not available

### Database

`get_db()` opens a new connection per request (no connection pool). `init_db()` runs at startup via the FastAPI `lifespan` handler. The `profile` table uses a singleton row (`id = 1`). Default settings are seeded but never overwritten by `init_db()`.

### SSE Protocol

All streaming endpoints yield `data: {json}\n\n`. Key event shapes:
- `{token, conversation_id}` — streaming token
- `{searching: true}` — web search triggered
- `{search_results: N}` — N results retrieved
- `{done: true, perplexity, tokens_per_sec, searched?}` — terminal event
- `{error: "..."}` — error event

### Deployment

Runs as systemd service under user `jarvischat`, working directory `/opt/jarvischat`. Logs via syslog (`journalctl -u jarvischat`).
