![cAIc banner](static/readme-banner.png)

# cAIc v1.0.0

**Cluster AI coordinator — heterogeneous GPU inference for homelab AI clusters.**

Your RX 6600 XT can't run a 14B model. Your RTX 5070 Ti can. Your old MacBook can run the small stuff. Alone, each box is limited. Together, they're a cluster — if the software lets them cooperate.

cAIc makes them cooperate.

## The Problem

Every distributed inference tool — llama.cpp RPC, vLLM, exo — assumes you have identical GPUs. Same vendor, same VRAM, same drivers. That assumption works for data centers with 64 identical H100s. It doesn't work for your homelab with an AMD card in the server, an NVIDIA card in the gaming PC, and a MacBook on the desk.

You have more aggregate compute than any single consumer machine. The software just can't see it that way.

## How cAIc Solves It

cAIc uses **query-routing** instead of layer-splitting. Each machine runs a complete model on its own GPU. When a query comes in, the coordinator classifies it and routes the *whole request* to the best-suited node — code questions to the coder model, general chat to the instruct model. No layer sharing, no straggler problem, no VRAM negotiation between mismatched GPUs.

```
┌─────────────────────────────────────────────────┐
│              docker compose stack                │
│                                                  │
│  ┌──────────┐  ┌────────┐  ┌──────────────────┐ │
│  │ SearXNG  │  │ Qdrant │  │    RabbitMQ      │ │
│  │  :8888   │  │ :6333  │  │  :5672/:15672    │ │
│  └────┬─────┘  └───┬────┘  └────────┬─────────┘ │
│       │            │                 │           │
│       ▼            ▼                 ▼           │
│  ┌──────────────────────────────────────────┐   │
│  │           cAIc (FastAPI)                 │   │
│  │           :8080 (HTTP)                   │   │
│  └──────┬──────────────┬───────────────────┘   │
│         │              │                        │
│         ▼              ▼                        │
│  ┌──────────────┐  ┌──────────────┐             │
│  │ llama-server │  │   Ollama     │             │
│  │    :8081     │  │   :11434     │             │
│  │  (GPU/RPC)   │  │ (embeddings) │             │
│  └──────────────┘  └──────────────┘             │
└─────────────────────────────────────────────────┘
```

**Coordinator** (CPU-only, no GPU) handles the web UI, RAG embedding, query triage, web search, memory, conversation storage, and the message broker. Every CPU-bound task stays here so it never competes with inference for GPU resources.

**Workers** (discrete GPU) run only llama-server. No database, no browser sessions, no orchestration overhead. They register via AMQP, respond to health checks, and accept model-swap commands when the coordinator needs a different model for the current query.

A worker with a slow GPU still contributes — it handles less latency-sensitive queries or batch work while the fast GPU handles interactive chat.

## What You Get

- **Clustered inference** across mismatched GPUs and machines — AMD, NVIDIA, Apple Silicon, CPU-only
- **Automatic query routing** — triage classifies each query and routes to the best node
- **Dynamic model swapping** — coordinator requests model changes on workers when needed
- **RAG with auto-eviction** — Qdrant-backed vector search with score-based corpus management
- **Persistent memory** — FTS5-backed memory that learns your preferences over time
- **Web search** — SearXNG integration for automatic lookups when the model is uncertain
- **Private Chat mode** — toggle to keep nothing on disk: no memory, no RAG, no search, no persistence
- **At-rest encryption** — AES-256-GCM on all query-derived text in SQLite and Qdrant
- **IDE integration** — OpenAI-compatible `/v1/chat/completions` endpoint for Continue.dev and friends
- **OpenAI-compat FIM** — `/v1/fim/completions` for code completion
- **6 color themes** — IBM Blue, Matrix, Dark, Light, Amber, Trippin
- **Docker-ready** — `docker compose up -d` and you're running

## Quick Start (Docker)

```bash
git clone ssh://gitea@llgit.llamachile.tube:1319/gramps/caic.git && cd caic
scripts/setup.sh           # generates .env, secrets, pulls default model (~4.6GB)
docker compose up -d       # boots cAIc + Qdrant + RabbitMQ + SearXNG + llama-server + Ollama
```

The setup wizard auto-generates secrets, detects disk space, downloads a default model, and configures all service hostnames. Point a browser at `http://localhost:8080` and you're chatting.

Requires: Docker Engine + Compose plugin. Place your own `.gguf` models in `./models/` for different sizes/vendors.

→ [Installation Guide](https://llgit.llamachile.tube/gramps/cAIc/wiki/Installation) | [Configuration](https://llgit.llamachile.tube/gramps/cAIc/wiki/Home) | [Bare-Metal Install](https://llgit.llamachile.tube/gramps/cAIc/wiki/Installation)

## Single-Node Mode

cAIc also runs entirely on one machine — coordinator, llama-server, Qdrant, SearXNG, and RabbitMQ all on localhost. Useful for testing, laptops, or WSL2 under Windows 11.

All services degrade gracefully if unreachable. Only llama-server (inference) is strictly required.

## Why Query-Routing?

Most distributed inference splits a *single model* across GPUs — GPU 1 runs layers 0–15, GPU 2 runs 16–31. That works with identical cards. With mixed hardware, the slowest GPU sets the pace for every forward pass.

cAIc routes *whole queries* instead. Each worker runs a complete model. Triage picks the right worker. No layer sharing, no lockstep, no straggler dragging down the cluster.

| | Layer-splitting | cAIc query-routing |
|---|---|---|
| **Hardware** | Identical GPUs required | Any mix — AMD, NVIDIA, Apple, CPU |
| **Bottleneck** | Slowest GPU per forward pass | None — each node runs independently |
| **Model swap** | N/A (one model split) | Async swap per worker |
| **Scale** | Add VRAM to one model | Add machines, each contributes fully |

## Data Safety

| Concern | How cAIc handles it |
|---------|---------------------|
| **Queries on disk?** | AES-256-GCM encrypted at rest. Private Chat mode = nothing touches disk at all. |
| **External services?** | SearXNG is optional and disabled in Private Chat. Everything else runs on your LAN. |
| **Inter-node traffic?** | WireGuard tunnels encrypt all coordinator↔worker traffic. Zero application changes. |
| **Access control?** | Guest sessions for LAN. Admin PIN (PBKDF2-hashed, rate-limited). Optional IP allowlist. |

## Built With

FastAPI + SQLite + Jinja2 on Python 3.13. AMQP-mediated cluster coordination via aio-pika. Qdrant for vector search. OpenAI-compatible inference endpoint via llama.cpp server.

214 tests. All use `tmp_path` fixtures + monkeypatched HTTP clients. No external services needed.

## Documentation

| Page | What's there |
|------|-------------|
| [Home](https://llgit.llamachile.tube/gramps/cAIc/wiki/Home) | Overview, FAQ, links |
| [Installation](https://llgit.llamachile.tube/gramps/cAIc/wiki/Installation) | Docker + bare-metal walkthrough, config reference |
| [Architecture](https://llgit.llamachile.tube/gramps/cAIc/wiki/Developer-Architecture) | Coordinator/worker design, AMQP protocol, module map |
| [Screenshots](https://llgit.llamachile.tube/gramps/cAIc/wiki/Screenshots) | UI gallery |

## Changelog

See [What's New](#whats-new-in-v100) below, or browse the [commit history](https://llgit.llamachile.tube/gramps/cAIc/commits/main).

## License

MIT

## Repository

Gitea: `ssh://gitea@llgit.llamachile.tube:1319/gramps/caic.git`

---

## What's New in v1.0.0

### Docker Containerization (B3)
- `Dockerfile` — multi-stage Python 3.13-slim build with healthcheck
- `docker-compose.yml` — full stack: cAIc, SearXNG, Qdrant, RabbitMQ, llama-server, Ollama
- `scripts/setup.sh` — first-run scaffolding: generates `.env`, secrets, SearXNG config, pulls default model
- All service URLs env-var configurable with Docker service hostnames
- AMQP secret uses Docker secrets pattern (`/run/secrets/`)
- Only port 8080 exposed by default; all other services internal to compose network
- Graceful degradation — SearXNG and Ollama optional

### Bug Fixes & Hardening
- Defaults changed from hardcoded LAN IPs to `localhost` for Docker compatibility
- `DEFAULT_MODEL` configurable via `CAIC_DEFAULT_MODEL` env var
- `HW_STATE_PATH` configurable via `CAIC_HW_STATE_PATH` env var
- Syslog handler wrapped in try/except (container-safe)
- SQLite `PRAGMA journal_mode = WAL` for better concurrency
- `db.close()` in try/finally for proper cleanup
- AMQP subscription append moved before try for reconnect safety
- Missing `psutil` + `jinja2` added to `requirements.txt`
- Test discovery fixed via `tests/conftest.py` sys.path insertion

## What's New in v0.23.0

### Topbar Redesign
- Stats moved to bottom status bar, toggles to hamburger menu, palette next to version
- Mobile-responsive layout, query bar restored above chat

### Uninstall Scripts
- `scripts/uninstall.sh`, `teardown-docker.sh`, `nuclear-clean.sh`

### Code Quality
- Replaced deprecated `asyncio.ensure_future` with `asyncio.create_task`
- `AGENTS.md` → `ai.md` for tool-agnostic project context

## What's New in v0.22.0

### Color Theme System
- 6 themes: IBM Blue, Green Ln (Matrix), Dark, Light, Amber (Fallout), Trippin (neon)
- Palette icon in topbar, CSS variable swap, `localStorage` persistence

### RAG Corpus Management UI (B4)
- Admin modal to browse, search, edit, and delete individual RAG entries
- Stats bar, semantic search, source filter, per-row edit/delete, bulk flush
- 14 new tests, 214 total

## What's New in v0.21.0

### Scrollbar + DOM Fixes
- Scrollbar z-index, `requestAnimationFrame` scroll, direction-aware scroll guard

### Perplexity Persistence
- Perplexity stored per message, confidence badges on loaded conversations

### Config Overhaul
- All service URLs now env-overridable for single-node deployment

## What's New in v0.20.0

### At-Rest Encryption
- AES-256-GCM on all query-derived text: conversations, memories, uploads, RAG, completions
- 256-bit key auto-generated on first boot, never exposed via API

## What's New in v0.19.3

### Private Chat Mode
- Toggle to keep nothing on disk — no persistence, no memory/RAG, no web search

### WireGuard In-Transit Encryption
- All coordinator↔worker traffic encrypted at the network layer

## What's New in v0.19.2

### Waterfall Direction Toggle
- NEW/OLD sort toggle, direction-aware scroll, toast notifications, clipboard fallback

## What's New in v0.19.1

### Default Model Auto-Pull
- Checks llama-server at startup, falls back to Ollama pull if missing

## What's New in v0.19.0

### Apple Silicon Worker Support
- GPU detection via `system_profiler` on macOS, hybrid AMD/Apple/CPU detection

## What's New in v0.18.0

### Wiki + UX Polish
- Full installation guide, screenshots gallery, waterfall layout, barcode stripes, confidence badges, sprocket strips, paper grain background

## What's New in v0.17.26

### Dynamic Model Swap + Cluster Status UI
- `request_model_swap()`, async `select_node()`, heartbeat handler, live status panel

## What's New in v0.14.0

### Cluster Protocol
- 9 AMQP message types, node registry, ping/pong health, coordinator auto-promotion

### RAG Corpus Management
- Score-based eviction with hysteresis, flush endpoint, operational stats

## What's New in v0.13.0

### RAG Eviction Engine
- Score-based eviction with hysteresis (80% high-water, 20% low-water), pinned sources, grace period

## What's New in v0.12.0

### Chat Reply Toolbar
- Copy, print, save, rate actions on assistant messages

### Startup Hardware Assessment
- CPU, RAM, VRAM probe on first boot

## What's New in v0.11.0

### Terminal RAG Hook
- `POST /api/ingest` with Bearer token auth for autonomous terminal history ingestion

## What's New in v0.10.0

### File Upload & Attachments
- PDF/text extraction, chat context injection, RAG ingest, paperclip UI

## What's New in v0.9.0

### Modular Refactor
- Single-file `app.py` split into config/db/auth/security/memory/search/rag/gpu + routers/

## What's New in v0.8.0

### Foundation
- OpenAI-compat endpoint, RAG pipeline, SSE streaming, llama-server integration
