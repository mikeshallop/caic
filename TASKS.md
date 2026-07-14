# cAIc — OpenCode Prompt Sequence
# Generated: 2026-07-14
# Execute sequentially. Run full test suite after each task before proceeding.
# Test command: ./venv/bin/python -m pytest tests/ -v

---

## Session 2026-07-14 — RAG bugfixes + Topbar redesign

- **RAG bugs fixed**: Collection name mismatch (`jarvis_rag` → `caic_rag`, migrated 219 points), `vectors_count`→`points_count` (Qdrant v1.10+ API change), removed unindexed `order_by` that caused 502 on scroll, made `RAG_COLLECTION` env-configurable (`CAIC_RAG_COLLECTION`).
- **Semantic search fixed**: Set `CAIC_EMBED_URL=http://192.168.50.108:11434` (mxbai-embed-large lives on ultron, not the old embed server).
- **Topbar redesign**: Moved system stats (CPU/MEM/GPU/VRAM/TOK) to a centered bottom strip. Moved toggles (MEM, SEARCH, PROFILE, SORT, PRIVACY) into a ⋮ hamburger menu next to ADMIN badge. Palette icon sits immediately after version number in topbar-left. Removed standalone (i) button — privacy info accessible via ⋮ → About Privacy. Input bar above chat, stats at very bottom. Mobile-responsive padding/sizing.

---

## ~~TASK 1 — README Cleanup [DONE]~~

Review README.md in the current repo. Remove any node references other than `coordinator` (192.168.50.108) and `worker` (192.168.50.210). Ensure all references to the project use the exact casing `cAIc` — not `Jarvischat`, `JarvisChat`, or `jarvischat`. Do not change any functional content, endpoint documentation, or architecture descriptions — this is a text cleanup only. After editing, verify the file renders cleanly as markdown. Commit with message: `docs: clean up node references and branding consistency`.

No new tests required for this task.

---

## ~~TASK 2 — Qwen2.5-Coder llama-server Service on Coordinator (Infrastructure) [DONE]~~

**Status: Systemd unit created, verified, and restored.**

This task originally defined creation of `/etc/systemd/system/llama-server-coder.service` (port 8082, Qwen2.5-Coder-14B Q5_K_M) as a prerequisite for dynamic model swapping. That sysadmin work is done.

**The real Task 2 deliverable — the ability to dynamically swap models based on query classification — is delivered by Roadmap N (Tasks 9–15).** The flow:

1. **Task 13** — Phi-4-mini triage (`triage.py`) classifies the query as `general`, `code`, `search`, or `rag`
2. **Task 13** — `select_node()` picks the best worker node; if the ideal model isn't active, it triggers a swap
3. **Task 14** — `request_model_swap()` publishes `cmd.swap_model` via AMQP `jc.admin` exchange
4. **Task 12** — The node agent on worker receives the command, stops the current llama-server, starts the correct one, waits for health, and publishes `model_ready`
5. **Task 14** — coordinator receives `model_ready`, updates the cluster registry, and routes the query to the node

The swap is async and transparent — the user sees only latency. The UI (Task 15) shows a yellow "swapping" status dot during the transition.

The service unit at `/etc/systemd/system/llama-server-coder.service` is the **target** the node agent starts when swapping to code inference. It is not enabled at boot — the AMQP cluster manages activation.

See Tasks 9–15 for the actual model swap implementation.

No pytest tests required for this infrastructure task.

---

## ~~TASK 3 — Update OpenCode Config to Use Qwen on :8082 [DONE]~~

Update `/home/gramps/.config/opencode/opencode.jsonc` (on this machine, coordinator) to point the configured provider at `http://127.0.0.1:8082/v1` instead of `http://127.0.0.1:8081/v1`. The model name in the config should be updated to reflect `qwen2.5-coder-14b` or whatever model ID the llama-server instance at :8082 reports via `/v1/models`. Verify the endpoint is reachable before writing the config change. Do not restart OpenCode — the config change takes effect on next session start.

No pytest tests required for this task.

---

## ~~TASK 4 — File/Document Attachment: Backend Ingest Endpoint [DONE]~~

**Status: `POST /api/upload` with mode=(context|ingest|both), PDF/text extraction, Qdrant upsert, SQLite context (1hr expiry). Committed `4a891c8` (v1.9.0).**

This task implements the backend half of file/document attachment (TODO #21). The goal is dual-aspect upload: a file can be used as immediate chat context, ingested into the RAG corpus (Qdrant), or both.

**Add to `config.py`:**
- `UPLOAD_DIR` — path for temporary upload storage, default `/tmp/caic_uploads`
- `MAX_UPLOAD_BYTES` — max file size, default 20MB
- `SUPPORTED_UPLOAD_TYPES` — set of MIME types: `text/plain`, `text/markdown`, `application/pdf`, `application/json`, `text/x-python`, `text/html`

**Create `routers/upload.py`:**

Implement `POST /api/upload` (admin required). Accept `multipart/form-data` with:
- `file` — the uploaded file (required)
- `mode` — string enum: `context` (inject into next chat only), `ingest` (add to RAG corpus), `both` (default: `both`)
- `conversation_id` — optional, associates context-mode content with a specific conversation

Behavior:
- Validate file size against `MAX_UPLOAD_BYTES` — return 413 if exceeded
- Validate MIME type against `SUPPORTED_UPLOAD_TYPES` — return 415 if unsupported
- For PDF files, extract text using `pypdf` (add to requirements.txt)
- For all other types, read as UTF-8 text
- If mode includes `ingest`: chunk the extracted text into 512-token overlapping chunks (128-token overlap), generate embeddings via `EMBED_URL` (http://192.168.50.108:11434/api/embeddings, model mxbai-embed-large), upsert into Qdrant collection `caic` with metadata `{source: filename, upload_date: iso_timestamp, type: "upload"}`
- If mode includes `context`: store the full extracted text in a new SQLite table `upload_context` with columns `(id INTEGER PRIMARY KEY, conversation_id TEXT, filename TEXT, content TEXT, created_at TEXT, expires_at TEXT)`. Context entries expire after 1 hour.
- Return JSON: `{filename, size_bytes, mode, chunks_ingested (if ingest), context_id (if context), message}`

**Add `upload_context` table to `db.py`** `init_db()`.

**Wire `upload.router` into `app.py`** in the router registration block.

**Write `tests/test_upload.py`** covering:
- Valid text file upload, mode=ingest — assert chunks_ingested > 0, Qdrant upsert called
- Valid text file upload, mode=context — assert context_id returned, row exists in upload_context
- Valid text file upload, mode=both — assert both behaviors
- File exceeds MAX_UPLOAD_BYTES — assert 413
- Unsupported MIME type — assert 415
- Guest session attempt — assert 403
- PDF extraction path — mock pypdf, assert text extracted and processed

Mock Qdrant and EMBED_URL calls via monkeypatch. Do not require live external services in tests.

Run full test suite after implementation. All 26 existing tests must continue to pass.

---

## ~~TASK 5 — File/Document Attachment: UI Integration [DONE]~~

**Status: Paperclip icon, file preview pill, gallery overlay, attachment indicators, DELETE/PATCH link/by-conversation endpoints, chat context injection. Committed `81238c0` (v1.10.0).**

This task implements the frontend half of TODO #21. The UI is a single file at `templates/index.html`.

Add a file attachment button to the chat input area. Requirements:
- Paperclip icon button adjacent to the send button
- Clicking opens a file picker filtered to supported types (`.txt`, `.md`, `.pdf`, `.json`, `.py`, `.html`)
- On file selection, show a pill/badge above the input showing the filename with an X to remove it
- On send, if a file is attached: POST to `/api/upload` with `mode=both` and the current `conversation_id`, then include the returned `context_id` in the subsequent `/api/chat` POST body as `upload_context_id`
- If the upload fails, show an inline error and do not send the chat message
- File attachment state clears after send

**Update `/api/chat` in `routers/chat.py`:**
- Accept optional `upload_context_id` in the request body
- If present, look up the content in `upload_context` table and prepend it to the system prompt as: `\n\n[ATTACHED DOCUMENT: {filename}]\n{content}\n[END DOCUMENT]`
- If the context_id is expired or missing, log a warning and continue without it (do not error)

**Add to `tests/test_chat_streaming_and_memory_paths.py`:**
- Test that a valid `upload_context_id` results in document content being prepended to the system prompt
- Test that an expired/missing `upload_context_id` is silently ignored

Run full test suite. All existing tests must continue to pass.

---

## ~~TASK 6 — Roadmap I: Terminal Command RAG Hook [DONE]~~

**Status: `POST /api/ingest` with Bearer token auth, `chunk_text()` shared helper, `caic-ingest.sh` script. Committed `1ac21ad` (v0.11.0).**

This task implements autonomous RAG ingestion of significant terminal activity (TODO #23).

**Create `routers/ingest.py`:**

Implement `POST /api/ingest` (requires Bearer token auth — use same `COMPLETIONS_API_KEY` mechanism as `routers/completions.py`). Accept JSON body:
- `content` — string, the text to ingest (required)
- `source` — string, origin label e.g. `terminal`, `file`, `external` (default: `external`)
- `metadata` — optional dict of additional key/value pairs

Behavior:
- Chunk `content` into 512-token overlapping chunks (128-token overlap) — extract this logic into a shared helper `chunk_text(text, chunk_size=512, overlap=128)` in `rag.py` if not already present
- Generate embeddings via `EMBED_URL`
- Upsert into Qdrant collection `caic` with metadata `{source, ingest_date: iso_timestamp, ...metadata}`
- Return JSON: `{chunks_ingested, source, message}`

**Wire `ingest.router` into `app.py`.**

**Create `/usr/local/bin/caic-ingest.sh` on worker (192.168.50.210)** — this is a shell script, not a Python file, and lives outside the repo. Write it to stdout/document it clearly so gramps can deploy it manually:

```bash
#!/bin/bash
# caic-ingest.sh — pipe terminal commands into cAIc RAG
# Add to ~/.bashrc: export PROMPT_COMMAND="jc_capture"
# Function to call after significant commands

JC_URL="http://192.168.50.210:8080/api/ingest"
JC_TOKEN="${CAIC_COMPLETIONS_API_KEY}"

jc_capture() {
    local cmd
    cmd=$(history 1 | sed 's/^[ ]*[0-9]*[ ]*//')
    # Only ingest significant commands
    if echo "$cmd" | grep -qE '^(git|pip|systemctl|sudo|vi|vim|curl|wget|apt|python|pytest)'; then
        curl -s -X POST "$JC_URL" \
            -H "Authorization: Bearer $JC_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"content\": $(echo "$cmd" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'), \"source\": \"terminal\"}" \
            > /dev/null 2>&1 &
    fi
}
```

**Write `tests/test_ingest.py`** covering:
- Valid ingest with content — assert chunks_ingested > 0
- Missing Bearer token — assert 401
- Wrong Bearer token — assert 403
- Empty content — assert 422
- Qdrant and embed calls mocked via monkeypatch

Run full test suite. All existing tests must continue to pass.

---

## ~~TASK 7 — Roadmap J: Startup Hardware Self-Assessment [DONE]~~

**Status: `hardware.py` + `routers/hardware.py` + 4 tests. Committed `7291b8f` (v0.12.0).**

On jC startup, probe available hardware and write a living config snapshot. This replaces hardcoded assumptions about VRAM and RAM.

**Create `hardware.py`** in the project root:

```
async def assess_hardware() -> dict
```

Probes:
- System RAM: `psutil.virtual_memory().total` and `.available`
- CPU count: `psutil.cpu_count()`
- GPU VRAM total and free: call `rocm-smi --showmeminfo vram --json` via subprocess, parse output. If rocm-smi absent or fails, set VRAM values to 0 and log a warning.
- llama-server reachable: GET `LLAMA_SERVER_BASE/v1/models`, timeout 3s. Record True/False and list of available model IDs.
- Qdrant reachable: GET `http://192.168.50.108:6333/collections`, timeout 3s. Record True/False and collection list.
- SearXNG reachable: GET `http://localhost:8888`, timeout 3s. Record True/False.

Returns a dict with all of the above. Writes result as JSON to `hardware_state.json` in the working directory.

**Call `assess_hardware()` from the FastAPI `lifespan` context** in `app.py` on startup, after `init_db()`. Log a summary line: `HW: {ram_gb}GB RAM, {vram_mb}MB VRAM, llama={reachable}, qdrant={reachable}, searxng={reachable}`.

**Expose `GET /api/hardware`** in a new `routers/hardware.py` — returns the current `hardware_state.json` content as JSON. No auth required (read-only, non-sensitive aggregate stats).

**Wire `hardware.router` into `app.py`.**

**Write `tests/test_hardware.py`** covering:
- `assess_hardware()` with all services reachable (mock subprocess and httpx calls) — assert all fields present
- `assess_hardware()` with rocm-smi absent — assert VRAM=0, no exception raised
- `assess_hardware()` with llama-server unreachable — assert `llama_reachable=False`, no exception
- `GET /api/hardware` — assert returns JSON with expected keys

Run full test suite. All existing tests must continue to pass.

---

## ~~TASK 8 — Roadmap K: RAG Corpus Management [DONE]~~

Qdrant collection `caic` currently grows without bound. Implement score-based eviction with hysteresis, pinned sources, operational stats, and a flush command.

### Config — add to `config.py`:

```python
RAG_MAX_VECTORS = 50000               # absolute ceiling; eviction targets thresholds below it
RAG_EVICTION_HIGH_WATER = 0.80        # fraction of RAG_MAX_VECTORS that triggers eviction
RAG_EVICTION_LOW_WATER = 0.20         # fraction where eviction stops
RAG_EVICTION_BATCH = 1000             # max points to delete per Qdrant scroll/delete cycle
RAG_PINNED_SOURCES = ["upload", "profile"]  # never evicted
RAG_GRACE_HOURS = 1                   # new vectors ineligible for eviction until this old
RAG_ACCESS_WEIGHT = 1.0               # score factor: retrieval_count * ACCESS_WEIGHT
RAG_AGE_WEIGHT = 0.1                  # score factor: ingest_age_hours * AGE_WEIGHT
```

Validations on boot: `high_water > low_water`, `batch > 0`, `max_vectors > 0`.

### Eviction algorithm — add to `rag.py`:

```
score = (retrieval_count * ACCESS_WEIGHT) + (age_hours * AGE_WEIGHT)
```

Lower score = evicted first. Tiebreak: `last_accessed` ASC (older wins).

```python
async def get_collection_count() -> int
    # GET /collections/caic → return vectors_count

async def get_collection_stats() -> dict
    # Return {vector_count, max_vectors, high_water, low_water, percent_full, pinned_sources}

async def evict_batch(batch_size: int) -> int
    # Scroll Qdrant for vectors NOT in RAG_PINNED_SOURCES, WHERE ingest_age > RAG_GRACE_HOURS,
    # ordered by score ASC, last_accessed ASC.
    # Delete up to batch_size. Return count deleted.
    # If 0 evictable vectors found: log warning, return 0 (break loop).

async def maybe_evict() -> int
    # Acquire eviction_lock (asyncio.Lock).
    # count = get_collection_count()
    # threshold_high = RAG_MAX_VECTORS * RAG_EVICTION_HIGH_WATER
    # threshold_low  = RAG_MAX_VECTORS * RAG_EVICTION_LOW_WATER
    # total_evicted = 0
    # while count >= threshold_low:
    #     if total_evicted > 0 and count < threshold_low: break
    #     deleted = evict_batch(RAG_EVICTION_BATCH)
    #     if deleted == 0: break  # no more unpinned targets
    #     total_evicted += deleted
    #     count -= deleted
    #     if count < threshold_high and total_evicted > 0: break
    #     # only one pass if batch spans the full gap
    #     if count < threshold_low: break
    # Record total_evicted + timestamp in EVICTION_LOG (list of dicts, kept in memory, max 1000 entries)
    # Release lock. Return total_evicted.

async def get_rag_operational_stats() -> dict
    # Returns: vector_count, max_vectors, high_water_pct, low_water_pct,
    # percent_full, pinned_sources, grace_hours,
    # eviction_counts_last_1m, eviction_counts_last_5m, eviction_counts_last_30m,
    # at_risk_count (vectors in bottom 10% by score),
    # pinned_count, avg_retrieval_count
```

### Edge cases & guards:

1. **Newborn grace** — vectors < `RAG_GRACE_HOURS` old are excluded from eviction scroll (score=0 otherwise → immediate deletion)
2. **All-pinned freeze** — if scroll returns 0 evictable vectors, log warning and break loop
3. **Race** — `asyncio.Lock()` guards `maybe_evict()`; concurrent callers wait their turn
4. **Zero config** — `RAG_MAX_VECTORS <= 0` → eviction disabled; `RAG_EVICTION_BATCH <= 0` → clamped to 1
5. **Legacy payloads** — vectors without `retrieval_count` or `last_accessed` get defaults (0, `ingest_date`)

### Wire eviction:

Call `maybe_evict()` after each upsert batch completes in:
- `routers/upload.py` — after Qdrant upsert
- `routers/ingest.py` — after Qdrant upsert

### Admin endpoints — new `routers/rag_admin.py`:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/rag/stats` | Operational stats (see `get_rag_operational_stats()`) — admin required |
| POST | `/api/rag/flush` | Delete ALL points from the Qdrant `caic` collection. Returns `{deleted_count, collection: "caic", status: "flushed"}`. Admin required. |

### In-memory eviction log:

```python
EVICTION_LOG: list[dict] = []  # managed by rag.py, max 1000 entries
# Each entry: {timestamp: iso, count: N, remaining: N}
# Tied to RATE_EVENTS pattern from security.py for rolling window calculations
```

### Tests — `tests/test_rag_management.py`:

- `get_collection_count()` — mock Qdrant GET, assert correct count
- `get_collection_stats()` — assert shape matches config
- `evict_batch()` — mock Qdrant scroll + delete, assert pinned sources excluded, grace period enforced, batch size respected
- `maybe_evict()` — below high water: 0 evicted; at high water: eviction fires; stops at low water; all-pinned scroll returns 0 → breaks
- `GET /api/rag/stats` — assert full shape
- `POST /api/rag/flush` — assert points deleted, admin required, guest 403
- `POST /api/rag/flush` by guest — assert 403
- Race lock — concurrent calls to `maybe_evict()` queue up, only one evicts

Mock all Qdrant calls via monkeypatch. Do not require live services.

Run full test suite. All existing tests must continue to pass.

---

## ~~TASK 9 — Roadmap N1: RabbitMQ Install and Service on Coordinator (Infrastructure) [DONE]~~

This task runs on coordinator (this machine). Install RabbitMQ and verify it is operational.

Run the following steps:
1. `apt-get update && apt-get install -y rabbitmq-server`
2. `systemctl enable rabbitmq-server && systemctl start rabbitmq-server`
3. `systemctl status rabbitmq-server` — verify active/running
4. Enable the management plugin: `rabbitmq-plugins enable rabbitmq_management`
5. Create a dedicated jC vhost: `rabbitmqctl add_vhost caic`
6. Create a dedicated user: `rabbitmqctl add_user caic CHANGEME_PASSWORD` — generate a random 24-char alphanumeric password and record it
7. Grant permissions: `rabbitmqctl set_permissions -p caic caic ".*" ".*" ".*"`
8. Verify management UI is reachable: `curl -s -u guest:guest http://localhost:15672/api/overview | python3 -m json.tool`
9. Delete default guest user: `rabbitmqctl delete_user guest`

Declare the two topic exchanges needed by jC:
- Exchange name: `jc.admin`, type: `topic`, durable: true
- Exchange name: `jc.system`, type: `topic`, durable: true

Use `rabbitmqadmin` or `curl` against the management API to declare exchanges. Verify both exchanges appear in: `curl -s -u caic:{password} http://localhost:15672/api/exchanges/caic`

Write the generated RabbitMQ password to `/home/gramps/.caic_amqp_secret` with mode 600. This will be read by cAIc as an env var source in subsequent tasks.

No pytest tests required for this infrastructure task.

---

## ~~TASK 10 — Roadmap N2: AMQP Connection Layer in jC [DONE]~~

This task adds the core AMQP connection manager to jC. It must connect to RabbitMQ on coordinator (localhost from jC's perspective since jC runs on coordinator), handle reconnection, and provide a shared channel for all AMQP operations.

**Add to `requirements.txt`:** `aio-pika>=9.0.0`

**Add to `config.py`:**
- `AMQP_URL` — read from env `CAIC_AMQP_URL`, default `amqp://caic:password@localhost:5672/caic`. The actual password comes from `/home/gramps/.caic_amqp_secret` — read it at startup if the env var is not set.
- `AMQP_RECONNECT_DELAY` — seconds between reconnect attempts, default 5
- `AMQP_EXCHANGE_ADMIN` — `jc.admin`
- `AMQP_EXCHANGE_SYSTEM` — `jc.system`

**Create `amqp.py`** in the project root:

```python
# Manages a single persistent aio-pika connection and channel.
# Provides:
#   connect() -> None          # establish connection, declare exchanges
#   disconnect() -> None       # graceful close
#   get_channel()              # returns current channel, reconnects if needed
#   publish(exchange, routing_key, payload: dict) -> None
#                              # publishes JSON-serialized payload as persistent message
```

Connection must:
- Reconnect automatically on disconnect with `AMQP_RECONNECT_DELAY` backoff
- Log connection events at INFO level
- Not raise on publish if disconnected — log error and return (fire-and-forget, jC must not crash if RabbitMQ is down)

**Start AMQP connection in `app.py` lifespan** after `assess_hardware()`. Disconnect in lifespan cleanup.

**Write `tests/test_amqp.py`** covering:
- `publish()` with mocked aio-pika connection — assert message published with correct exchange and routing key
- `publish()` when disconnected — assert no exception raised, error logged
- `get_channel()` when connection is None — assert reconnect attempted

Mock all aio-pika calls via monkeypatch. Do not require a live RabbitMQ instance in tests.

Run full test suite. All existing tests must continue to pass.

---

## ~~TASK 11 — Roadmap N3: Cluster Protocol & Registration Handler (Coordinator Side) [DONE]~~

**Status: Implemented and pushed (899988c).** `amqp.py` subscribe/rebind, `cluster.py` with CLUSTER_NODES/CLUSTER_EVENTS/CLUSTER_COORDINATOR and 6 handlers, `routers/cluster.py` (`GET /api/cluster`), 13 tests. No passive heartbeats — ping/pong on-demand before work routing. 148 tests pass.

jC on the coordinator must listen for nine message types across `jc.admin` and `jc.system`, maintain the cluster registry, and expose an application-level event log.

### 11.1 AMQP Protocol — Message Catalog

All payloads are JSON, published as persistent messages.

| Direction | Exchange | Routing Key | Message Type | Description |
|-----------|----------|-------------|-------------|-------------|
| Worker → Coordinator | `jc.admin` | `node.{name}.register` | register | Worker requests admission |
| Worker → Coordinator | `jc.admin` | `node.{name}.deregister` | deregister | Worker signals graceful departure |
| Coordinator → Worker | `jc.admin` | `node.{name}.admitted` | admitted | Coordinator grants admission |
| Coordinator → Worker | `jc.admin` | `node.{name}.rejected` | rejected | Coordinator denies admission (with reason) |
| Coordinator → Worker | `jc.admin` | `node.{name}.ping` | ping | Coordinator checks if worker is alive (sent before routing work) |
| Worker → Coordinator | `jc.admin` | `node.{name}.pong` | pong | Worker confirms aliveness |
| Worker → Coordinator | `jc.system` | `node.{name}.event` | event | Application-level syslog event |
| Any → All | `jc.system` | `cluster.coordinator.query` | coord_query | Anyone asks "who is coordinator?" |
| Coordinator → All | `jc.system` | `cluster.coordinator.response` | coord_response | Coordinator announces itself |

Worker presence is assumed from registration onward. No periodic heartbeats — a worker can sit idle for days without chatter. When the coordinator needs to route work to a worker, it pings first; if the worker doesn't pong within timeout, the coordinator deregisters it and moves to the next node.

### 11.2 Payload Schemas

**register** (worker → coordinator):
```json
{
  "node_name": "worker01",
  "node_type": "worker",
  "ip": "192.168.50.210",
  "capabilities": {
    "gpu": true, "gpu_type": "amd", "vram_mb": 8192,
    "cpu_cores": 8, "ram_gb": 16
  },
  "active_model": {
    "name": "llama3.1", "version": "latest", "quant": "Q4_K_M",
    "path": "/var/lib/caic/models/llama3.1-latest-Q4_K_M.gguf",
    "port": 8081
  },
  "inventory": [
    {"name": "llama3.1", "version": "latest", "quant": "Q4_K_M",
     "path": "/var/lib/caic/models/llama3.1-latest-Q4_K_M.gguf", "port": 8081}
  ],
  "status": "active"
}
```

**deregister** (worker → coordinator):
```json
{
  "node_name": "worker01",
  "reason": "shutdown",
  "timestamp": "2026-07-06T12:00:00Z"
}
```

**ping** (coordinator → worker):
```json
{
  "from": "coordinator",
  "node_name": "worker01",
  "type": "ping",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-07-06T12:00:00Z"
}
```
Worker must respond within 5 seconds or the coordinator considers it absent.

**pong** (worker → coordinator):
```json
{
  "node_name": "worker01",
  "type": "pong",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "active",
  "active_model": {"name": "llama3.1", "port": 8081},
  "load": {"cpu_pct": 45, "ram_pct": 62, "vram_pct": 38},
  "timestamp": "2026-07-06T12:00:00Z"
}
```
Correlation ID matches the ping so the coordinator can pair request and response.

**coord_query** (any → `cluster.coordinator.query`):
```json
{"type": "coord_query", "timestamp": "2026-07-06T12:00:00Z"}
```
Coordinator responds on `cluster.coordinator.response`:
```json
{
  "coordinator_node": "coordinator",
  "cluster_nodes": ["worker01"],
  "timestamp": "2026-07-06T12:00:00Z"
}
```

**event** (worker → coordinator):
```json
{
  "node_name": "worker01",
  "severity": "info",
  "message": "llama-server started with model llama3.1:latest",
  "details": {"model": "llama3.1:latest", "port": 8081, "pid": 1234},
  "timestamp": "2026-07-06T12:00:00Z"
}
```
Severity levels: `info`, `warn`, `error`, `critical`. The coordinator assigns `category: "application"` based on the exchange (jc.system). No `event_type` field — the category is determined by the channel, not the payload.

### 11.3 Design — Status Transitions Drive the Event Log

All admin-level events are *derived* from `register()` and `deregister()` as side effects. There are no separate message types for coordinator election, node staleness, quarantine, or release — those are status transitions that `register()`/`deregister()` emit into `CLUSTER_EVENTS` locally.

**Node status lifecycle:**

```
UNKNOWN ──register()──▶ active ──deregister()──▶ (removed)
                           │
               ping timeout│(coordinator publishes
                           │ deregister on its behalf)
                           ▼
                      (removed)
```

**Coordinator status lifecycle:**

```
NONE ──register(node_type=coordinator)──▶ CLUSTER_COORDINATOR set
                                              │
                                   deregister()│or timeout
                                              ▼
                                         CLUSTER_COORDINATOR cleared
```

**Event categories — two buckets, no granular types:**

| Category | When | severity |
|----------|------|----------|
| `cluster` | Node lifecycle, coordinator changes, model swaps, node offline — everything on `jc.admin` | `info` / `warn` / `error` |
| `application` | Worker syslog events (incoming on `jc.system` `node.*.event`) | `info` / `warn` / `error` / `critical` |

Every `_push_event()` call uses one of these two categories. The `message` field carries the human-readable detail — no need for event type strings. The reporting tool filters by category + severity.

**Channel split — security rationale:**

The two exchanges are not an organizational convenience. They enforce a **data isolation boundary**:

| Exchange | Contains | Exposed to |
|----------|----------|------------|
| `jc.admin` | Node lifecycle, heartbeats, model swaps, coordinator changes | Operations / machine-room staff |
| `jc.system` | Application events — inference queries, RAG context, user-facing data | Application-layer audit only |

`jc.system` events can leak information about what users are doing and asking. The split ensures a sysadmin monitoring cluster health never accidentally consumes user-data-bearing events. The channels can be locked down independently — different AMQP credentials, separate queue permissions, different in-transit encryption policies if needed later.

### 11.4 Implementation

**Add to `amqp.py`:**

```python
_SUBSCRIPTIONS: list[tuple[str, str, Callable]]  # (exchange, routing_key, callback)

async def subscribe(exchange, routing_key, callback) -> None
    # Append to _SUBSCRIPTIONS list
    # Declare a unique queue per subscription (name: f"jc.{exchange}.{sanitized_routing_key}")
    # Bind queue to exchange/routing_key, consume with callback
```

Each subscription gets its own queue so multiple subscribers on different routing keys all receive messages. On reconnect: drain old consumers, iterate `_SUBSCRIPTIONS`, re-declare and re-bind each one. The `connect()` function must call `_rebind_subscriptions()` after exchanges are declared.

**Create `cluster.py`** in the project root:

```python
# In-memory cluster registry + event log
# Survives only while jC is running (not persisted)

CLUSTER_NODES: dict[str, NodeRecord]
CLUSTER_EVENTS: deque[EventRecord]   # bounded at 1000 entries
CLUSTER_COORDINATOR: str | None      # node_name of active coordinator

# NodeRecord fields:
#   node_name, node_type, ip, status, active_model, inventory,
#   capabilities: {gpu, gpu_type, vram_mb, cpu_cores, ram_gb}
#   registered_at, last_seen

# EventRecord:
#   category: str     ("cluster" | "application")
#   severity: str     ("info" | "warn" | "error" | "critical")
#   node_name: str
#   message: str
#   details: dict | None
#   timestamp: str

def _push_event(category, severity, node_name, message, details=None) -> None
    # Append EventRecord to CLUSTER_EVENTS, pop left if > 1000

async def handle_registration(message) -> None
    # Parse payload, validate required fields (node_name, node_type, ip, active_model, inventory)
    # Reject if node_name duplicate and CLUSTER_NODES[node_name].status == "active"
    # If CLUSTER_COORDINATOR is None AND node_type == "coordinator":
    #   set CLUSTER_COORDINATOR = node_name
    #   _push_event("cluster", "info", node_name, "elected coordinator")
    #   publish cluster.coordinator.response on jc.system {coordinator_node, cluster_nodes, timestamp}
    # Add node to CLUSTER_NODES with status="active"
    # _push_event("cluster", "info", node_name, f"admitted as {node_type}")
    # publish admitted on jc.admin node.{name}.admitted {node_name, timestamp, amqp_url}

async def handle_deregistration(message) -> None
    # Parse payload (node_name, reason, timestamp)
    # If node_name == CLUSTER_COORDINATOR:
    #   clear CLUSTER_COORDINATOR
    #   _push_event("cluster", "warn", node_name, f"coordinator lost — {reason}")
    # _push_event("cluster", "info", node_name, f"departed — {reason}")
    # Remove node from CLUSTER_NODES, log it

async def handle_pong(message) -> None
    # Parse: node_name, correlation_id, status, active_model, load, timestamp
    # Match correlation_id to outstanding ping
    # If node in CLUSTER_NODES: update last_seen, status, active_model
    # Signal the waiting caller that the node is alive
    # If node unknown: log warning, do NOT auto-admit

async def handle_event(message) -> None
    # Parse: node_name, severity, message, details, timestamp
    # Assigns category="application" (incoming on jc.system)
    # Append EventRecord to CLUSTER_EVENTS (pop left if > 1000)

async def handle_coordinator_query(message) -> None
    # Respond on jc.system cluster.coordinator.response
    # Payload: {coordinator_node, cluster_nodes: list(CLUSTER_NODES.keys()), timestamp}

def get_cluster_state() -> dict
    # Return: {nodes: CLUSTER_NODES, coordinator: CLUSTER_COORDINATOR,
    #          events: last 50 CLUSTER_EVENTS}
```

**Subscribe in `app.py` lifespan** after AMQP connects:

| Exchange | Routing Key | Handler |
|----------|-------------|---------|
| `jc.admin` | `node.*.register` | `handle_registration` |
| `jc.admin` | `node.*.deregister` | `handle_deregistration` |
| `jc.admin` | `node.*.pong` | `handle_pong` |
| `jc.system` | `node.*.event` | `handle_event` |
| `jc.system` | `cluster.coordinator.query` | `handle_coordinator_query` |

### 11.5 API — `GET /api/cluster`

New router `routers/cluster.py`:
- `GET /api/cluster` — returns full cluster state: `{nodes, coordinator, events}` (last 50 events). No auth required.

Wire `cluster.router` into `app.py`.

### 11.6 Tests — `tests/test_cluster.py`

Mock all aio-pika calls. Do not require live RabbitMQ.

| # | Test | What it asserts |
|---|------|-----------------|
| 1 | Valid worker registration | Node admitted, CLUSTER_NODES updated, `cluster` event logged, `admitted` message published |
| 2 | First coordinator auto-promotion | CLUSTER_COORDINATOR set, `cluster` event with "elected" message, `coord_response` published |
| 3 | Duplicate node name rejected | `rejected` message with reason=`duplicate_node_name`, `cluster` event logged |
| 4 | Malformed payload rejected | `rejected` message with reason=`malformed_payload` |
| 5 | Graceful deregistration | Node removed, `cluster` event logged. If coordinator: CLUSTER_COORDINATOR cleared |
| 6 | Pong from known node | last_seen updated, load/status refreshed |
| 7 | Pong from unknown node | Warning logged, node NOT added |
| 8 | Event stored in log | Event appended to CLUSTER_EVENTS; at 1001 entries the oldest is popped |
| 9 | Coordinator query produces response | Response published with coordinator name and node list |
| 10 | GET /api/cluster shape | Response contains `nodes`, `coordinator`, `events` keys |

Run full test suite. All existing tests must continue to pass.

---

## ~~TASK 12 — Roadmap N4: Worker Node Registration Publisher (Worker Side) [DONE]~~

This task creates the worker node AMQP client that runs on worker (192.168.50.210). It is a standalone Python script — not part of the jC FastAPI app — that runs as a systemd service on worker.

**Create `node_agent/agent.py`** in the repo (new directory).

### 12.1 Config & Inventory Discovery

On start, reads `/etc/caic-node-agent.conf` (INI format):
- `node_name` — hostname, default from `socket.gethostname()`
- `node_ip` — LAN IP, default from socket
- `node_type` — `"worker"` (fixed)
- `capabilities` — comma-separated list, e.g. `llm,rag`
- `amqp_url` — RabbitMQ URL on coordinator, e.g. `amqp://caic:password@192.168.50.108:5672/caic`
- `llama_port` — port llama-server/llama-rpc is listening on, default 8081
- `models_dir` — path to GGUF model files, default `/var/lib/caic/models`
- `active_model` — filename of currently active model (without path)

Discovers inventory by globbing `models_dir` for `*.gguf` files and parsing name/version/quant from filename using regex pattern: `{name}-{version}-{quant}.gguf` where quant matches `Q[0-9]+_K_[A-Z]+` or similar standard suffixes.

### 12.2 Registration

Publishes registration to `jc.admin`, routing key `node.{node_name}.register`:
```json
{
  "node_name": "worker01",
  "node_type": "worker",
  "ip": "192.168.50.210",
  "capabilities": ["llm"],
  "active_model": {"name": "...", "version": "...", "quant": "...", "path": "...", "port": 8081}
}
```

### 12.3 Admission Response

Listens on `node.{node_name}.admitted` and `node.{node_name}.rejected` (both `jc.admin`). Logs result. If rejected, exits with error.

### 12.4 Ping Listener

After admission: listens on `jc.admin`, routing key `node.{node_name}.ping`. On receipt, responds immediately (within 1 second) with a pong on `jc.admin`, routing key `node.{node_name}.pong`:

```json
{
  "node_name": "worker01",
  "type": "pong",
  "correlation_id": "<echoed from ping>",
  "status": "active",
  "active_model": {"name": "...", "version": "...", "quant": "...", "path": "...", "port": 8081},
  "load": {"cpu_pct": 45, "ram_pct": 62, "vram_pct": 38},
  "timestamp": "<utc>"
}
```

No periodic heartbeats. Worker sits idle between pings — coordinator only pings when it needs to route work.

### 12.5 Model Swap Command Handler

Listens on `jc.admin`, routing key `node.{node_name}.cmd.swap_model`:
- Payload: `{model_filename: str}`
- Stops current llama-server: `systemctl stop llama-server`
- Updates `/etc/caic-node-agent.conf` active_model field
- Starts llama-server: `systemctl start llama-server` (assumes service reads active_model from conf or ExecStart is updated)
- Waits for llama-server to be healthy: poll `http://localhost:{llama_port}/v1/models` every 2s, timeout 120s
- Publishes to `jc.system`, routing key `node.{node_name}.model_ready`:
  ```json
  {"node_name": "...", "active_model": "...", "port": ..., "timestamp": "..."}
  ```
- If startup fails within timeout: publishes `node.{node_name}.model_failed` with error detail

### 12.6 Files & Tests

**Create `node_agent/requirements.txt`:** `aio-pika>=9.0.0`

**Document `/etc/caic-node-agent.conf` format** in a comment block at the top of `agent.py`.

**Write `tests/test_node_agent.py`** covering:
- Registration payload construction from config + model discovery — assert correct JSON shape
- Model swap command handler: success path — assert systemctl calls made, model_ready published
- Model swap command handler: timeout path — assert model_failed published
- Ping handler: on ping, publishes pong with correct correlation_id
- Agent starts idle after admission, no heartbeat timer

Mock all aio-pika, subprocess, and httpx calls.

**Do not create a systemd service file in this task** — that is a manual deployment step. Document the required service configuration in a comment at the bottom of `agent.py`.

Run full test suite. All existing tests must continue to pass.

---

## ~~TASK 13 — Roadmap N5: Query Routing via AMQP + Phi-4-mini Triage [DONE]~~

This task wires the cluster into jC's chat flow. When a query arrives at `/api/chat`, instead of always routing to the hardcoded `LLAMA_SERVER_BASE`, jC now routes to the best available cluster node based on query context.

**Prerequisites:** Tasks 9–12 complete. At least one worker node admitted to cluster.

**Install Phi-4-mini on coordinator (infrastructure step):**
- Download `Phi-4-mini-Instruct-Q4_K_M.gguf` from HuggingFace using `hf download microsoft/Phi-4-mini-instruct --include "*.Q4_K_M.gguf" --local-dir /var/lib/caic/models`
- Create `/etc/systemd/system/llama-server-triage.service` — same pattern as existing llama-server service but: port 8083, model path points to Phi-4-mini GGUF, no `--rpc` flag (runs entirely on coordinator CPU/iGPU), description `Llama.cpp Server (Phi-4-mini — triage/routing)`
- `systemctl daemon-reload && systemctl enable llama-server-triage && systemctl start llama-server-triage`
- Verify: `curl -s http://localhost:8083/v1/models`

**Add to `config.py`:**
- `TRIAGE_BASE` — `http://127.0.0.1:8083/v1` (Phi-4-mini)
- `TRIAGE_TIMEOUT` — 10 seconds
- `FALLBACK_TO_DEFAULT` — True (if triage fails or no nodes available, fall back to `LLAMA_SERVER_BASE`)

**Create `triage.py`** in the project root:

```python
async def classify_query(query: str) -> str
    # Sends query to Phi-4-mini at TRIAGE_BASE with a classification system prompt.
    # System prompt instructs model to respond with ONLY one of:
    #   "general", "code", "search", "rag"
    # Returns the classification string.
    # Timeout: TRIAGE_TIMEOUT seconds.
    # On any error: returns "general" (fail-safe).

async def select_node(classification: str) -> dict | None
    # Consults CLUSTER_NODES from cluster.py
    # For "code": prefer nodes where active_model name contains "coder" or "qwen"
    # For "general": prefer nodes where active_model name contains "mistral" or "llama"
    # For "search" or "rag": return None (handled locally by jC)
    # If no matching node found: return None (triggers FALLBACK_TO_DEFAULT)
    # Returns NodeRecord dict for selected node, or None

async def get_inference_url(query: str) -> str
    # Combines classify_query + select_node
    # Returns full base URL: f"http://{node.ip}:{node.active_model.port}/v1"
    # Falls back to LLAMA_SERVER_BASE if classification=search/rag, no nodes, or triage error
```

**Update `routers/chat.py`:**
- Replace the hardcoded `LLAMA_SERVER_BASE` reference with a call to `get_inference_url(user_message)`
- The rest of the chat flow (RAG, memory, streaming) is unchanged — only the inference target URL changes

**Write `tests/test_triage.py`** covering:
- `classify_query()` returns valid classification — mock Phi-4-mini response
- `classify_query()` on timeout — assert returns "general", no exception
- `select_node("code")` with coder node in cluster — assert correct node returned
- `select_node("general")` with no matching node — assert None returned
- `get_inference_url()` with code query and coder node available — assert returns node URL
- `get_inference_url()` with no nodes in cluster — assert returns LLAMA_SERVER_BASE fallback

**Update `tests/test_chat_streaming_and_memory_paths.py`:**
- Mock `triage.get_inference_url` to return a fixed URL in all existing tests so they continue to pass without a live cluster

Run full test suite. All existing tests must continue to pass.

---

## ~~TASK 14 — Roadmap N6: Model Swap Command Flow [DONE]~~

**Status: Implemented and pushed (`9d1fd44`).** `request_model_swap()`, `handle_model_ready()`, `handle_model_failed()` in `cluster.py`, async `select_node()` with swap triggering in `triage.py`, `tests/test_model_swap.py` (9 tests). 177 tests pass.

This task implements the coordinator-side logic for requesting a model swap on a worker node when the ideal model is not currently active.

**Add to `cluster.py`:**

```python
async def request_model_swap(node_name: str, model_filename: str) -> bool
    # Publishes to jc.admin exchange, routing key node.{node_name}.cmd.swap_model
    # Payload: {model_filename, requested_at: iso_timestamp}
    # Sets node status to "swapping" in CLUSTER_NODES
    # Returns True if message published successfully

async def handle_model_ready(message) -> None
    # Handles node.{node_name}.model_ready from jc.system
    # Updates CLUSTER_NODES[node_name].active_model to the new model
    # Sets node status back to "active"
    # Logs swap completion with timing

async def handle_model_failed(message) -> None
    # Handles node.{node_name}.model_failed from jc.system
    # Sets node status to "error" in CLUSTER_NODES
    # Logs failure with detail from message payload
```

**Subscribe in `app.py` lifespan:**
- `jc.system` exchange, routing key `node.*.model_ready` → `handle_model_ready`
- `jc.system` exchange, routing key `node.*.model_failed` → `handle_model_failed`

**Update `triage.py` `select_node()`:**
- If the best-matching node exists but its active_model does not match the ideal model for the classification, AND the node status is "active" (not already swapping):
  - Call `request_model_swap(node_name, ideal_model_filename)`
  - Return None (triggers fallback) — the swap happens async, next query will find the right model active
- If node status is "swapping": return None (fallback, swap in progress)

**Update `GET /api/cluster`** to include node status in response.

**Write `tests/test_model_swap.py`** covering:
- `request_model_swap()` — assert swap command published, node status set to "swapping"
- `handle_model_ready()` — assert active_model updated, status set to "active"
- `handle_model_failed()` — assert status set to "error"
- `select_node()` with mismatched active model — assert swap requested, None returned
- `select_node()` with node status "swapping" — assert None returned without publishing another swap

Run full test suite. All existing tests must continue to pass.

---

## ~~TASK 15 — Roadmap N7: Cluster Status UI [DONE]~~

Surface cluster awareness in the jC frontend (`templates/index.html`).

**Add a cluster status panel** to the UI. Requirements:
- Small status bar or collapsible panel, visible but unobtrusive
- Polls `GET /api/cluster` every 15 seconds
- For each admitted node: show node name, active model name, and a colored status dot:
  - Green: active
  - Yellow: swapping
  - Red: error or offline (not seen in last 60 seconds based on last_seen timestamp)
- If no nodes in cluster (empty): show "No worker nodes connected"
- Panel must not interfere with chat input or conversation list

**Update `GET /api/cluster` response** to include `last_seen` per node and a `status` field (`active`, `swapping`, `error`).

**Update heartbeat handling in `cluster.py`:** add a handler for `node.*.heartbeat` on `jc.system` that updates `last_seen` timestamp for the node.

**Subscribe in `app.py` lifespan:**
- `jc.system` exchange, routing key `node.*.heartbeat` → `handle_heartbeat`

**Add `handle_heartbeat()` to `cluster.py`:**
- Updates `CLUSTER_NODES[node_name].last_seen` to current timestamp
- If node was previously marked offline (not in CLUSTER_NODES), log re-registration warning but do not auto-admit — full registration required

**Write `tests/test_cluster_heartbeat.py`** covering:
- `handle_heartbeat()` for known node — assert last_seen updated
- `handle_heartbeat()` for unknown node — assert no crash, warning logged, node not added

Run full test suite. All 26+ existing tests must continue to pass.

~~Commit all changes introduced across Tasks 9–15 with message: `feat: Roadmap N — AMQP cluster nervous system complete`~~

---

## Backlog (Post-Roadmap N) ⏳

### ~~B1 — Context loss in follow-up questions [DONE]~~

**Symptom:** After asking "in {context}, explain {b}", a follow-up "what is {b}'s {x}?" gets a non-sequitur response that ignores the original context.

**Diagnosis:** `build_system_prompt()` is called fresh per-request with new RAG/memory results keyed to the current message text. These can change between turns and may dilute or override the conversation history. The original system prompt used for turn 1 (including its RAG context) is not stored in the DB — only user/assistant messages are. The inference server receives a different system prompt each turn.

**Possible fixes:**
- Store the assembled system prompt with each assistant message in the DB
- When replaying history, re-send the original system prompts from DB rather than rebuilding
- Or: cap RAG/memory injection to only fire on the first message of a conversation, then rely solely on conversation history for follow-ups
- Check that llama-server isn't truncating history due to context window overflow (Mistral-Nemo 12B = 128K context, unlikely)

### ~~B2 — Bang-prefixed search routing [DONE]~~

**Spec:** If a query begins with `!`, route to SearXNG search instead of local inference.

**Where:** In `routers/chat.py` `chat()` handler, after `user_message` is extracted. Strip the `!`, set a flag to always trigger auto-search regardless of perplexity/refusal.

**Change:** Add a `force_search` flag when `user_message.startswith("!")`, strip the prefix from the message saved to DB, and route directly to the search+summarize path.

### B3 — Docker distribution (v1.0 gate)

**Goal:** Ship cAIc as a `docker compose` stack so a single command stands up everything.

**Services to containerize:**
- cAIc (FastAPI app + SQLite)
- SearXNG
- Qdrant
- RabbitMQ
- llama-server (with optional RPC sidecar for GPU offload)
- Ollama (embeddings)

**Also needed:**
- `Dockerfile` for the cAIc app itself
- `docker-compose.yml` with all services, volumes, networks, env vars
- Setup wizard script (run on first boot) that:
  - Probes CPU vs GPU (reuses `hardware.py`)
  - Queries user for admin PIN, node name, IP
  - Generates `.env` file with correct `LLAMA_SERVER_BASE`, `EMBED_URL`, etc.
  - Auto-calculates `RAG_MAX_VECTORS` from available RAM: `max(1000, int(available_ram_gb * 100_000))`
  - Optionally detects and configures RPC GPU offload
- Manual install docs remain alongside for bare-metal deployment

**This task is only actionable after Tasks 8–15 (RAG eviction + AMQP cluster) are complete.**

---

### ~~B4 — RAG Corpus Management UI (Display, Edit, CRUD) [DONE]~~

**Goal:** Provide a management interface in the UI to browse, search, edit, and delete individual entries in the Qdrant-backed RAG corpus.

**Backend — add to `routers/rag_admin.py`:**

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/api/rag/points` | Return paginated list of RAG points with payload (text, source, date). Supports `?offset=0&limit=50&search=` query params | Admin |
| GET | `/api/rag/point/{point_id}` | Return a single point with full payload | Admin |
| DELETE | `/api/rag/point/{point_id}` | Delete a single point from Qdrant | Admin |
| PATCH | `/api/rag/point/{point_id}` | Update a point's text payload (re-embed the new text) | Admin |

Helper functions for Qdrant scroll/delete/update go in `rag.py` or `eviction.py`.

**Frontend — add to `templates/index.html`:**

A "RAG" button in the admin UI (drawer or settings modal) that opens a management panel:
- **Stats bar**: vector count, max vectors, percent full, pinned sources
- **Search bar**: text input to search the RAG corpus by semantic similarity
- **Results table**: paginated list showing each vector's text snippet, source label, ingest date, retrieval count
  - Click to expand full text
  - Delete button per row (with confirmation)
  - Edit button per row (inline text edit → re-embed on save)
- **Bulk actions**: flush all (existing `/api/rag/flush`) with confirmation

**Tests:**

- `tests/test_rag_admin.py` — cover new endpoints: list, get, delete, update, admin-enforcement
- Mock all Qdrant calls via monkeypatch

Run full test suite. All existing tests must continue to pass.**
