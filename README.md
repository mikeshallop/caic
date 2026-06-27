# JarvisChat v1.8.0

**A lightweight local inference coding companion with persistent memory, web search, and real-time system monitoring.**

Built with FastAPI + SQLite + Jinja2. Runs on Python 3.13. No Docker required.

Developer wiki: [docs/wiki/Home.md](docs/wiki/Home.md)

## What's New in v1.8.0

- **Modular refactor completed** — single-file `app.py` split into `config.py`, `db.py`, `auth.py`, `security.py`, `memory.py`, `search.py`, `rag.py`, `gpu.py`, and `routers/` package
- **`COMPLETIONS_API_KEY`** — auto-generated secret key for the OpenAI-compatible endpoint, overridable via `JARVISCHAT_COMPLETIONS_API_KEY` env var
- **Perplexity auto-search fixed** — upstream request now sends `"logprobs": true`, `parse_llama_stream_chunk()` extracts per-token logprobs, so `calculate_perplexity()` and `is_uncertain()` work correctly (was dead code)
- **All `/api/models` endpoints** — now correctly target `LLAMA_SERVER_BASE` (llama-server on port 8081) instead of the old Ollama port; `/api/ps` uses `/v1/models` endpoint
- **RAG embedding endpoint fixed** — `EMBED_URL` changed from port `:11434` (Ollama) to `:8081` (llama-server)
- **Error messages corrected** — all user-facing errors say "inference server" instead of "Ollama" or "llama-server"
- **Secure SSE protocol** — raw search results are no longer leaked in the SSE event stream
- **FTS5 query safety** — operator keywords (`AND`, `OR`, `NOT`, `NEAR`) are double-quoted to prevent parse errors
- **All 8 test files fixed** — rewired imports after the modular refactor; all 26 tests pass
- **Origin check extended to all API methods** — GET/HEAD/OPTIONS requests no longer bypass origin checking (was limited to POST/PUT/DELETE/PATCH)
- **Missing headers now rejected** — `origin_allowed()` returns `False` when both `Origin` and `Referer` are absent, closing the CSRF read gap for script-initiated requests

## Features

- **Persistent Memory** — SQLite FTS5 full-text search for fast, relevant memory retrieval
- **Web Search** — SearXNG integration for automatic web lookups when the model is uncertain
- **Explicit Search** — Search button to force web search without waiting for model uncertainty
- **Profile Injection** — Custom system prompt injected into every conversation
- **System Presets** — Save and switch between different system prompts
- **Real-time Stats** — CPU, RAM, GPU, VRAM monitoring in sidebar
- **Token Thermometer** — Visual context window usage indicator
- **Streaming Responses** — Server-sent events for real-time token display
- **Conversation History** — SQLite-backed chat persistence with mass-delete option
- **Model Switching** — Change inference models on the fly
- **Skills Framework** — Built-in skill registry with per-skill enable/disable controls

## File Structure

```
/opt/jarvischat/
├── app.py              # FastAPI app entry point
├── config.py           # Constants, env vars, limits, skill registry
├── db.py               # SQLite schema, connection factory
├── auth.py             # PIN-based guest/admin sessions, auth routes
├── security.py         # Rate limiting, origin checks, IP allowlist, audit
├── memory.py           # FTS5 memory CRUD, remember/forget commands
├── search.py           # SearXNG integration, perplexity, refusal detection
├── rag.py              # Qdrant vector search + system prompt assembly
├── gpu.py              # AMD GPU stats via rocm-smi
├── routers/
│   ├── chat.py         # /api/chat streaming endpoint
│   ├── search_route.py # /api/search explicit search endpoint
│   ├── completions.py  # /v1/chat/completions OpenAI-compat endpoint
│   ├── conversations.py# Conversation CRUD
│   ├── memories.py     # Memory CRUD API
│   ├── models.py       # Model listing, system stats
│   ├── presets.py      # System prompt presets
│   ├── profile.py      # User profile
│   ├── settings.py     # Runtime settings
│   └── skills.py       # Skills management
├── static/
│   └── logo.png        # Logo image (optional)
├── templates/
│   └── index.html      # Frontend
└── tests/              # 26 pytest tests
```

## Requirements

- Python 3.11+ (tested on 3.13)
- llama-server running locally or on network (OpenAI-compatible API on port 8081)
- SearXNG (optional, for web search)

## Installation

### Fresh Install

```bash
# Create directory and venv
sudo mkdir -p /opt/jarvischat
sudo chown $USER:$USER /opt/jarvischat
cd /opt/jarvischat
python3 -m venv venv

# Install dependencies
./venv/bin/pip install fastapi uvicorn httpx psutil jinja2 python-multipart

# Set admin PIN before first startup (4 digits)
export JARVISCHAT_ADMIN_PIN=4827

# Create subdirectories
mkdir -p templates static

# Copy files
# (copy all .py files to /opt/jarvischat/)
# (copy routers/ directory to /opt/jarvischat/)
# (copy templates/index.html to /opt/jarvischat/templates/)
```

WARNING: Do not use `1234` as your admin PIN unless you accept weak local security.

NOTE: First boot requires `JARVISCHAT_ADMIN_PIN` unless you explicitly opt into insecure fallback with `JARVISCHAT_ALLOW_DEFAULT_PIN=true`.

## Systemd Service

Create `/etc/systemd/system/jarvischat.service`:

```ini
[Unit]
Description=JarvisChat - Local Inference Web Interface
After=network.target

[Service]
Type=simple
User=jarvischat
Group=jarvischat
WorkingDirectory=/opt/jarvischat
ExecStart=/opt/jarvischat/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable jarvischat
sudo systemctl start jarvischat
```

## Memory Commands

In chat, natural language triggers memory operations:

| You say | What happens |
|---------|--------------|
| "remember that I prefer Rust over Go" | Stores as `preference` |
| "remember that JarvisChat runs on port 8080" | Stores as `infrastructure` |
| "note that the deadline is Friday" | Stores as `general` |
| "forget about the deadline" | Removes matching memories |

Memories are automatically searched based on your message content and injected into the system prompt when relevant.

### Memory Topics

Memories are auto-categorized:
- `preference` — likes, dislikes, choices
- `project` — active work, repos, tasks
- `infrastructure` — servers, services, configs
- `personal` — name, location, background
- `general` — everything else

## API Endpoints

### Completions (OpenAI-compatible)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/chat/completions` | OpenAI-compatible chat (requires Bearer API key) |

### Chat & Search

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat` | Send message (streaming SSE) |
| POST | `/api/search` | Explicit web search (streaming SSE) |

### Memory

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/memories` | List all memories |
| POST | `/api/memories` | Add memory |
| PUT | `/api/memories/{rowid}` | Update memory |
| DELETE | `/api/memories/{rowid}` | Delete memory |
| GET | `/api/memories/search?q=term` | Search memories |
| GET | `/api/memories/stats` | Get counts by topic |

### Models & System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/models` | List available models |
| GET | `/api/ps` | List loaded models |
| POST | `/api/show` | Get model info |
| GET | `/api/stats` | CPU, RAM, GPU, VRAM stats |
| GET | `/api/search/status` | SearXNG availability |

### Settings & Profile

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/profile` | Get profile content |
| PUT | `/api/profile` | Update profile (admin) |
| GET | `/api/profile/default` | Get default profile |
| GET | `/api/settings` | Get settings |
| PUT | `/api/settings` | Update settings (admin) |

### Conversations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/conversations` | List conversations |
| POST | `/api/conversations` | Create conversation |
| GET | `/api/conversations/{id}` | Get conversation with messages |
| PUT | `/api/conversations/{id}` | Update conversation title/model |
| DELETE | `/api/conversations/{id}` | Delete conversation |
| DELETE | `/api/conversations` | Delete ALL conversations |

### Presets

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/presets` | List presets |
| POST | `/api/presets` | Create preset |
| PUT | `/api/presets/{id}` | Update preset |
| DELETE | `/api/presets/{id}` | Delete preset |

### Skills

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/skills` | List all skills with state |
| GET | `/api/skills/active` | List active skills |
| PUT | `/api/skills/{key}` | Toggle skill enabled (admin) |

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/guest` | Create guest session |
| POST | `/api/auth/login` | Admin PIN login |
| POST | `/api/auth/logout` | Revoke session |
| GET | `/api/auth/session` | Check session validity |
| POST | `/api/auth/heartbeat` | Extend session TTL |

## Configuration

Settings are stored in the `settings` table and include:

- `profile_enabled` — Inject profile into chats (true/false)
- `search_enabled` — Auto web search (true/false)
- `memory_enabled` — Memory injection (true/false)
- `skills_enabled` — Skills framework (true/false)
- `default_model` — Default inference model

## Testing

```bash
./venv/bin/python -m pytest tests/ -v
```

All 26 tests use `tmp_path` fixtures + monkeypatched `httpx.AsyncClient.stream`. No external services needed.

## License

MIT

## Repository

Gitea: `ssh://gitea@llgit.llamachile.tube:1319/gramps/jarvisChat.git`
