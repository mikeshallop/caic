# Developer Architecture Guide

This document explains how JarvisChat is structured, the external services it integrates with, and the key architectural changes made during development.

## 1. System Overview

JarvisChat is a single-process FastAPI service with a Jinja2 frontend and SQLite persistence. It connects to an external llama-server for inference and optionally to SearXNG (web search), Qdrant (vector search), and RabbitMQ (AMQP cluster messaging).

### 1.1 Module Layout

Refactored from single-file (`app.py`) into modules under project root:

| File | Role |
|------|------|
| `app.py` | FastAPI app, middleware, router registration, lifespan |
| `config.py` | Constants, env vars, rate/payload limits, built-in skills registry, upload limits, RAG eviction config |
| `db.py` | SQLite schema, connection factory, settings helpers, upload_context CRUD |
| `auth.py` | PIN-based guest/admin sessions, auth routes |
| `security.py` | Rate limiting, origin checks, IP allowlist, audit/incident logging |
| `memory.py` | FTS5 memory CRUD, remember/forget command parsing |
| `search.py` | SearXNG integration, perplexity scoring, refusal detection |
| `rag.py` | Qdrant vector search, system prompt assembly, chunk_text() helper, collection stats |
| `eviction.py` | Score-based RAG eviction engine (extracted from rag.py) |
| `gpu.py` | AMD GPU stats via rocm-smi |
| `amqp.py` | (WIP) aio-pika connection manager for RabbitMQ |
| `routers/` | One module per endpoint group |

### 1.2 External Services

| Service | Required | Port | Purpose |
|---------|----------|------|---------|
| llama-server (ultron) | Yes | 8081 | LLM inference (OpenAI-compat), RPC offload to jarvis:50052 |
| SearXNG | No | 8888 | Privacy-respecting web search |
| Qdrant (ultron) | No | 6333 | Vector database for RAG |
| Ollama (jarvis) | No | 11434 | Embeddings for RAG chunk vectors |
| RabbitMQ (ultron) | No | 5672 | AMQP broker for cluster messaging |
| rocm-smi | No | — | AMD GPU stats (host-level) |

### 1.3 Config Discovery

Key base URLs are configured via environment variables with sensible defaults:

| Variable | Default | Service |
|----------|---------|---------|
| `LLAMA_SERVER_BASE` | `http://192.168.50.108:8081` | llama-server on ultron |
| `OLLAMA_BASE` | `http://localhost:11434` | Legacy — all inference goes through LLAMA_SERVER_BASE |
| `SEARXNG_BASE` | `http://localhost:8888` | SearXNG |
| `QDRANT_URL` | `http://192.168.50.108:6333` | Qdrant on ultron |
| `JARVISCHAT_AMQP_URL` | `amqp://jarvischat:password@localhost:5672/jarvischat` | RabbitMQ |

## 2. Request/Response Architecture

### 2.1 Chat Pipeline (`/api/chat`)

1. Validate session, role, origin, rate, and payload limits in middleware
2. Intercept "remember that..." / "forget about..." commands → process_remember_command()
3. Persist user message and conversation metadata
4. Build system prompt: profile + FTS5 memory + Qdrant RAG results + preset + active skills + uploaded document (if upload_context_id)
5. Stream from llama-server with `logprobs: true` for perplexity scoring
6. If perplexity > 15.0 OR refusal patterns match → re-query with SearXNG results
7. Persist final assistant message and emit terminal SSE event

### 2.2 Explicit Search Pipeline (`/api/search`)

1. Persist search-as-message into conversation
2. Emit `searching` SSE event
3. Pull web results from SearXNG
4. Summarize via llama-server SSE stream
5. Persist summary and emit `done` event

### 2.3 RAG Ingest Pipeline (`/api/ingest`)

1. Bearer token auth (same key as completions API)
2. Chunk text via shared `chunk_text()` helper (512-token chunks, 128-token overlap)
3. Embed via Ollama `/api/embeddings`
4. Upsert to Qdrant collection `jarvis_rag`
5. Trigger `maybe_evict()` if collection exceeds high-water mark

### 2.4 Upload Pipeline (`/api/upload`)

1. Admin required, multipart file upload
2. Validate MIME type + size against config limits
3. PDF text extraction via pypdf; plain text for all other types
4. Three modes: `context` (SQLite with 1hr expiry), `ingest` (RAG/Qdrant), `both`
5. Trigger `maybe_evict()` if ingest mode

## 3. Data Model (SQLite)

Key tables:

- `conversations` — headers, timestamps, attachment_count
- `messages` — ordered chat history per conversation
- `profile` — singleton row for injected profile prompt
- `settings` — runtime toggles and selected defaults
- `system_presets` — named reusable system prompts
- `skills` — per-skill enabled state and timestamp
- `memories` (FTS5 virtual table) — full-text searchable user memory facts
- `upload_context` — auto-expiring document storage for context injection

Design notes:
- Startup is idempotent: tables created if missing, defaults seeded only when absent
- No connection pool: each request opens and closes a short-lived SQLite connection
- `init_db()` called in FastAPI lifespan

## 4. Security Implementations

### 4.1 Auth Model

- Guest session by default (POST /api/auth/guest)
- Admin unlock via 4-digit PIN (POST /api/auth/login)
- Admin required for PUT/DELETE/PATCH + all POST except allowlist (/api/chat, /api/search, /api/auth/*)
- /api/ingest is exempt from session auth — self-authenticates via Bearer token
- Session heartbeat/timeout (90s default) and explicit logout

### 4.2 PIN Hardening

- Admin PIN hashed with PBKDF2-HMAC-SHA256 + salt
- Failed PIN attempts tracked per client IP (max 5, 300s lockout)
- Default PIN allowed only if JARVISCHAT_ALLOW_DEFAULT_PIN=true

### 4.3 Browser and API Abuse Controls

- Origin checks on all /api/ requests (rejects absent Origin AND Referer)
- Rate limiting per endpoint category and identity (IP/session)
- Payload size limits per route class (64KB default, 128KB chat, 20MB upload)
- Settings key allowlist (5 keys: profile_enabled, default_model, etc.)
- IP allowlist/CIDR gate with trusted proxy forwarding mode

### 4.4 Output and Error Safety

- Search result URLs sanitized to http/https only
- Client-safe error envelopes with incident key correlation
- Full stack traces logged server-side only

### 4.5 Operational Auditability

- Structured audit events for auth actions, admin ops, guardrail denials
- Incident logs with event type, key, path/method, and runtime metadata

## 5. RAG Architecture

### 5.1 Vector Search

- Qdrant collection `jarvis_rag` on ultron:6333
- Embeddings via Ollama on jarvis:11434 (`/api/embeddings`)
- Shared `chunk_text(text, chunk_size=512, overlap=128)` helper in rag.py
- Upload and ingest endpoints share the same chunk+embed+upsert pipeline

### 5.2 Score-Based Eviction

When `RAG_MAX_VECTORS` is exceeded, eviction fires with hysteresis:

- High-water mark: 80% of max → trigger eviction
- Low-water mark: 20% of max → stop eviction
- Batch size: 1000 vectors per cycle
- Score formula: `score = (access_weight * retrieval_count) + (age_weight * hours_since_ingested)`
- Lower score evicted first (least useful)
- Tiebreaker: oldest last_accessed ASC
- Excluded sources: `upload`, `profile` (pinned)
- Grace period: 1 hour before any vector is eligible
- Thread-safe via `asyncio.Lock`

Eviction module at `eviction.py` (re-exported through `rag.py` for backward compat).

### 5.3 Operational Stats

`GET /api/rag/stats` (admin required) returns:
- vector_count, max_vectors, high_water_pct, low_water_pct, percent_full
- pinned_sources list, grace_hours
- at_risk_count, pinned_count, avg_retrieval_count
- eviction_counts_last_{1,5,30}m

### 5.4 Flush

`POST /api/rag/flush` (admin required) — deletes all non-pinned vectors. Returns `{deleted_count, collection, status}`.

## 6. Cluster Architecture

### 6.1 Design Model: Broker-Mediated

JarvisChat uses a **broker-mediated** cluster design. This is the preferred architecture and is reflected in all implementation decisions below.

**How it works:**
- A single RabbitMQ broker (or clustered set of brokers) acts as the central nervous system
- **Coordinator nodes** run the FastAPI app, host the HTTP API/UI, and publish commands to the broker
- **Worker nodes** connect as AMQP *clients only* — they consume commands and publish status events, but run no broker software themselves
- Communication is asynchronous and persistent: each node opens a TCP connection on startup and keeps it alive. The AMQP-0-9-1 heartbeat detects silent failures within ~60s.

**Why broker-mediated:**
- Workers are heterogeneous (different GPUs, different models, ARM vs x86) — no assumption of uniform software
- Workers are lightweight — a Raspberry Pi with a USB AI accelerator can participate without running a broker
- The coordinator delegates work via messages, not by SSH'ing into workers or requiring shared filesystems
- Failure is isolated: a crashed worker drops off the heartbeat list; the coordinator reassigns its work

**What it is NOT:**
- Not a service mesh — workers do not run identical software stacks
- Not autonomous failover — if the coordinator dies, a replacement must be manually promoted (or pre-configured as a secondary coordinator). Workers cannot self-promote to coordinator because they lack the required services (FastAPI, SQLite, DB schema, SearXNG, Qdrant, etc.)
- Not a peer-to-peer cluster — all orchestration flows through the coordinator

### 6.2 Node Types

Every physical machine in the cluster is classified by which services it runs. Two node types are defined:

| Aspect | Coordinator | Worker |
|--------|------------|--------|
| **Role** | Serves HTTP API/UI, orchestrates inference, owns cluster state | Runs inference models on behalf of the coordinator |
| **Python** | Required — runs FastAPI app | Required — runs node agent (aio-pika consumer) |
| **RabbitMQ server** | Required — hosts the broker | Not required — connects as AMQP client only |
| **RabbitMQ client (aio-pika)** | Required — publishes commands, consumes events | Required — consumes commands, publishes events |
| **FastAPI / uvicorn** | Required | Not needed |
| **SQLite** | Required — owns jarvischat.db | Not needed |
| **Qdrant** | Optional (recommended) — vector DB for RAG | Not needed |
| **SearXNG** | Optional — web search | Not needed |
| **llama-server** | Optional — can share its own GPU for inference | Required — this is why the worker exists |
| **Ollama** | Optional — embeddings for RAG | Not needed |
| **rocm-smi / nvidia-smi** | Optional — hardware stats | Optional — node agent reports this at registration |

### 6.3 Service Distribution Summary

```
Coordinator                          Worker(s)
┌────────────────────┐               ┌─────────────────────────┐
│  jarvisChat        │               │  llama-server           │
│  (FastAPI + SQLite)│               │  (inference)            │
│  RabbitMQ server   │◄──AMQP───────│  aio-pika (agent)       │
│  SearXNG (opt)     │    persistent │  ROCm / CUDA (if GPU)   │
│  Qdrant (opt)      │    TCP        │                         │
│  Ollama (opt)      │    conn       │  No broker              │
│  llama-server(opt) │               │  No jC                  │
└────────────────────┘               │  No DB                  │
                                     │  No search/vector       │
                                     └─────────────────────────┘
```

### 6.4 RabbitMQ Topology

Every RabbitMQ server belongs to a cluster. Currently only the coordinator runs one; if high availability is needed, additional nodes can join the RMQ cluster without changing the architecture.

| Exchange | Type | Purpose |
|----------|------|---------|
| `jc.admin` | topic | Commands: swap model, shutdown, heartbeat request |
| `jc.system` | topic | Events: model_ready, model_failed, heartbeat, registration |

Pending implementation (Tasks 10–15):
- `amqp.py` — aio-pika connection manager with reconnect
- Node agent on jarvis — registration, heartbeat, command consumer
- `triage.py` — Phi-4-mini query classification (general/code/search/rag)
- Dynamic model swap via llama-server RPC

## 7. SSE Protocol

All streaming endpoints yield `data: {json}\n\n`:

- `{token, conversation_id}` — streaming token
- `{searching: true}` — web search triggered
- `{search_results: N}` — N results found (no raw payload)
- `{done: true, perplexity, tokens_per_sec, searched?}` — terminal
- `{error: "...", error_key: "..."}` — error with incident key

## 8. Testing Strategy

### 8.1 Test Framework

- pytest with `tmp_path` + monkeypatched httpx.AsyncClient
- No live external services required
- Test factories reset `SESSIONS`, `PIN_ATTEMPTS`, `RATE_EVENTS` globals per test

### 8.2 Test Coverage Areas (132 tests)

| Test file | Coverage |
|-----------|----------|
| test_auth_capabilities.py | Guest/admin sessions, origin blocking, logout |
| test_chat_streaming_and_memory_paths.py | Streaming, auto-search, remember/forget, upload context injection |
| test_completions.py | API key auth, FIM, streaming, blocking, errors |
| test_conversations.py | Full CRUD, guest admin, attachment_count |
| test_ingest.py | Bearer auth, chunk/embed/upsert, validation |
| test_memories.py | Edit, search, stats |
| test_models_router.py | Models list, ps, show, stats, search/status |
| test_presets.py | Full CRUD, default preset protection |
| test_profile.py | Get, update, default, length validation |
| test_rag_management.py | Collection stats, eviction algorithm (pinned/grace/scoring/batch), maybe_evict hysteresis, operational stats, flush, concurrency lock |
| test_search_route.py | Explicit search flow, no results, errors |
| test_search_url_sanitization.py | URL sanitizer |
| test_settings_allowlist.py | Allowlisted key enforcement |
| test_skills_framework.py | List, toggle, unknown skill, prompt injection |
| test_ip_allowlist.py | IP allowlist helper + middleware |
| test_rate_and_payload_guardrails.py | Rate limits + payload size |
| test_error_envelopes.py | Global exception handler + stream errors |
| test_upload.py | Upload, delete, link, by-conversation, attachment_count |

### 8.3 DoD Process

For substantive changes:
1. Implement code change
2. Add/adjust tests proving behavior and guardrail intent
3. Update this wiki and README in the same change set
4. Validate with full test run before commit

## 9. Hardware Self-Assessment

On startup, `assess_hardware()` probes:
- RAM total/available (psutil)
- VRAM total/free (rocm-smi, best-effort)
- llama-server reachability + model list
- Qdrant reachability + collection list
- SearXNG reachability

Writes `hardware_state.json` to working directory.
