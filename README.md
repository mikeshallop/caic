# jarvisChat v0.11.0

You have a garage full of retired office PCs, a GPU that was mid-range when Obama was president, and a burning desire to chat with a language model without renting some billionaire's server farm. Congratulations — you've found your people.

jarvisChat is a chat UI that grew limbs. It started as a single-file Python script because OpenWebUI wouldn't install on Debian 13, and somewhere along the way it learned to file paperwork (file attachments), write things down (RAG ingest), boss around other computers (AMQP clustering), and check its own pulse (hardware self-assessment). It now does all the things you didn't ask for, plus a few you might actually use.

Under the hood: FastAPI + SQLite + Jinja2 on Python 3.13. Stitches together mismatched hardware via llama.cpp RPC — your gaming PC's dusty RX 580, the NUC in the closet, that old workstation from 2017 — and spreads inference across them like peanut butter on stale bread. It shouldn't work, but somehow it does.

When we hit v1.0, this will ship as a Docker-based distribution with a setup wizard that detects CPU vs GPU, probes your hardware, and stands up SearXNG, Qdrant, RabbitMQ, and everything else with a single `docker compose up`. Manual install docs will also be maintained for the bare-metal crowd.

Developer wiki: [docs/wiki/Home.md](docs/wiki/Home.md)

## What's New in v0.11.0

### File & Document Attachments (v1.9.0–v1.10.0)
- **`POST /api/upload`** — multipart file upload with PDF/text extraction; modes: `context` (chat injection), `ingest` (RAG corpus), `both`
- **`DELETE /api/upload/{id}`** — removes upload from SQLite + Qdrant
- **`PATCH /api/upload/{id}/link`** — associates upload with a conversation
- **`GET /api/upload/by-conversation/{id}`** — list attachments per conversation
- **Paperclip UI** — file picker, preview pill, image thumbnails, gallery overlay
- **Attachment indicators** — 📎 badge on conversations with attachments
- **Chat context injection** — `upload_context_id` prepends document text to system prompt

### Terminal RAG Hook — `POST /api/ingest` (v0.11.0)
- Bearer token auth (same key as `/v1/chat/completions`)
- Chunking via shared `chunk_text()` helper, embed via Ollama, upsert to Qdrant
- `jc-ingest.sh` — PROMPT_COMMAND shell script for autonomous terminal history ingestion

### v1.8.0 Foundation (refactor & fixes)
- **Modular refactor** — single-file `app.py` split into `config.py`, `db.py`, `auth.py`, `security.py`, `memory.py`, `search.py`, `rag.py`, `gpu.py`, and `routers/` package
- **Perplexity auto-search fixed** — `logprobs: true` now properly extracted from stream chunks
- **All `/api/models` endpoints** target `LLAMA_SERVER_BASE` (llama-server) not Ollama
- **RAG embedding** via Ollama at `http://192.168.50.210:11434`
- **Origin check** applies to all API methods, rejects absent Origin/Referer

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
│   ├── skills.py       # Skills management
│   ├── upload.py       # File attachment endpoints
│   └── ingest.py       # Terminal RAG ingest
├── static/
│   └── logo.png        # Logo image (optional)
├── templates/
│   └── index.html      # Frontend
└── tests/              # 110 pytest tests
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
./venv/bin/pip install fastapi uvicorn httpx psutil jinja2 python-multipart pypdf

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
Description=jarvisChat - Local Inference Web Interface
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

### File Upload & Ingest

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/upload` | Upload file (multipart, admin) |
| DELETE | `/api/upload/{id}` | Delete upload (admin) |
| PATCH | `/api/upload/{id}/link` | Link upload to conversation (admin) |
| GET | `/api/upload/by-conversation/{id}` | List uploads for conversation |
| POST | `/api/ingest` | Ingest text into RAG (Bearer token auth) |

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

All 110 tests use `tmp_path` fixtures + monkeypatched `httpx.AsyncClient`. No external services needed.

## License

MIT

## Repository

Gitea: `ssh://gitea@llgit.llamachile.tube:1319/gramps/jarvisChat.git`
