# jarvisChat — OpenCode Prompt Sequence
# Generated: 2026-07-01
# Execute sequentially. Run full test suite after each task before proceeding.
# Test command: ./venv/bin/python -m pytest tests/ -v

---

## TASK 1 — README Cleanup [DONE]

Review README.md in the current repo. Remove any node references other than `ultron` (192.168.50.108) and `jarvis` (192.168.50.210). Ensure all references to the project use the exact casing `jarvisChat` — not `Jarvischat`, `JarvisChat`, or `jarvischat`. Do not change any functional content, endpoint documentation, or architecture descriptions — this is a text cleanup only. After editing, verify the file renders cleanly as markdown. Commit with message: `docs: clean up node references and branding consistency`.

No new tests required for this task.

---

## TASK 2 — Qwen2.5-Coder llama-server Service on Ultron (Infrastructure) [DONE]

**Status: Systemd unit created, verified, and restored.**

This task originally defined creation of `/etc/systemd/system/llama-server-coder.service` (port 8082, Qwen2.5-Coder-14B Q5_K_M) as a prerequisite for dynamic model swapping. That sysadmin work is done.

**The real Task 2 deliverable — the ability to dynamically swap models based on query classification — is delivered by Roadmap N (Tasks 9–15).** The flow:

1. **Task 13** — Phi-4-mini triage (`triage.py`) classifies the query as `general`, `code`, `search`, or `rag`
2. **Task 13** — `select_node()` picks the best worker node; if the ideal model isn't active, it triggers a swap
3. **Task 14** — `request_model_swap()` publishes `cmd.swap_model` via AMQP `jc.admin` exchange
4. **Task 12** — The node agent on jarvis receives the command, stops the current llama-server, starts the correct one, waits for health, and publishes `model_ready`
5. **Task 14** — ultron receives `model_ready`, updates the cluster registry, and routes the query to the node

The swap is async and transparent — the user sees only latency. The UI (Task 15) shows a yellow "swapping" status dot during the transition.

The service unit at `/etc/systemd/system/llama-server-coder.service` is the **target** the node agent starts when swapping to code inference. It is not enabled at boot — the AMQP cluster manages activation.

See Tasks 9–15 for the actual model swap implementation.

No pytest tests required for this infrastructure task.

---

## TASK 3 — Update OpenCode Config to Use Qwen on :8082 [DONE]

Update `/home/gramps/.config/opencode/opencode.jsonc` (on this machine, ultron) to point the configured provider at `http://127.0.0.1:8082/v1` instead of `http://127.0.0.1:8081/v1`. The model name in the config should be updated to reflect `qwen2.5-coder-14b` or whatever model ID the llama-server instance at :8082 reports via `/v1/models`. Verify the endpoint is reachable before writing the config change. Do not restart OpenCode — the config change takes effect on next session start.

No pytest tests required for this task.

---

## TASK 4 — File/Document Attachment: Backend Ingest Endpoint [DONE]

**Status: `POST /api/upload` with mode=(context|ingest|both), PDF/text extraction, Qdrant upsert, SQLite context (1hr expiry). Committed `4a891c8` (v1.9.0).**

This task implements the backend half of file/document attachment (TODO #21). The goal is dual-aspect upload: a file can be used as immediate chat context, ingested into the RAG corpus (Qdrant), or both.

**Add to `config.py`:**
- `UPLOAD_DIR` — path for temporary upload storage, default `/tmp/jarvischat_uploads`
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
- If mode includes `ingest`: chunk the extracted text into 512-token overlapping chunks (128-token overlap), generate embeddings via `EMBED_URL` (http://192.168.50.108:11434/api/embeddings, model mxbai-embed-large), upsert into Qdrant collection `jarvischat` with metadata `{source: filename, upload_date: iso_timestamp, type: "upload"}`
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

## TASK 5 — File/Document Attachment: UI Integration [DONE]

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

## TASK 6 — Roadmap I: Terminal Command RAG Hook [DONE]

**Status: `POST /api/ingest` with Bearer token auth, `chunk_text()` shared helper, `jc-ingest.sh` script. Committed `1ac21ad` (v0.11.0).**

This task implements autonomous RAG ingestion of significant terminal activity (TODO #23).

**Create `routers/ingest.py`:**

Implement `POST /api/ingest` (requires Bearer token auth — use same `COMPLETIONS_API_KEY` mechanism as `routers/completions.py`). Accept JSON body:
- `content` — string, the text to ingest (required)
- `source` — string, origin label e.g. `terminal`, `file`, `external` (default: `external`)
- `metadata` — optional dict of additional key/value pairs

Behavior:
- Chunk `content` into 512-token overlapping chunks (128-token overlap) — extract this logic into a shared helper `chunk_text(text, chunk_size=512, overlap=128)` in `rag.py` if not already present
- Generate embeddings via `EMBED_URL`
- Upsert into Qdrant collection `jarvischat` with metadata `{source, ingest_date: iso_timestamp, ...metadata}`
- Return JSON: `{chunks_ingested, source, message}`

**Wire `ingest.router` into `app.py`.**

**Create `/home/gramps/bin/jc-ingest.sh` on jarvis (192.168.50.210)** — this is a shell script, not a Python file, and lives outside the repo. Write it to stdout/document it clearly so gramps can deploy it manually:

```bash
#!/bin/bash
# jc-ingest.sh — pipe terminal commands into jarvisChat RAG
# Add to ~/.bashrc: export PROMPT_COMMAND="jc_capture"
# Function to call after significant commands

JC_URL="http://192.168.50.210:8080/api/ingest"
JC_TOKEN="${JARVISCHAT_COMPLETIONS_API_KEY}"

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

## TASK 7 — Roadmap J: Startup Hardware Self-Assessment [DONE]

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

## TASK 8 — Roadmap K: RAG Corpus Management

Qdrant collection `jarvischat` currently grows without bound. Implement weighted LRU eviction and pinning.

**Add to `config.py`:**
- `RAG_MAX_VECTORS` — max vectors in Qdrant collection before eviction triggers, default 50000
- `RAG_EVICTION_BATCH` — number of vectors to evict per cycle, default 1000
- `RAG_PINNED_SOURCES` — list of source labels that are never evicted, default `["upload", "profile"]`

**Add to `rag.py`:**

```python
async def get_collection_count() -> int
    # GET Qdrant /collections/jarvischat, return vectors_count

async def evict_oldest(batch_size: int) -> int
    # Scroll Qdrant for vectors with source NOT in RAG_PINNED_SOURCES,
    # ordered by ingest_date ascending (oldest first),
    # delete batch_size of them. Return count deleted.

async def maybe_evict() -> int
    # If get_collection_count() >= RAG_MAX_VECTORS: call evict_oldest(RAG_EVICTION_BATCH)
    # Return count evicted (0 if no eviction needed)
```

**Call `maybe_evict()` from the ingest path** — both in `routers/upload.py` and `routers/ingest.py` — after each upsert batch completes.

**Add `GET /api/rag/stats`** to a new `routers/rag_admin.py`:
- Returns `{vector_count, max_vectors, pinned_sources, eviction_batch}`
- Admin required

**Wire `rag_admin.router` into `app.py`.**

**Write `tests/test_rag_management.py`** covering:
- `get_collection_count()` — mock Qdrant GET, assert correct count returned
- `evict_oldest()` — mock Qdrant scroll + delete, assert correct batch size deleted, assert pinned sources excluded
- `maybe_evict()` — below threshold: assert 0 evicted; at/above threshold: assert eviction triggered
- `GET /api/rag/stats` — assert correct JSON shape returned
- Guest attempt on `/api/rag/stats` — assert 403

Run full test suite. All existing tests must continue to pass.

---

## TASK 9 — Roadmap N1: RabbitMQ Install and Service on Ultron (Infrastructure)

This task runs on ultron (this machine). Install RabbitMQ and verify it is operational.

Run the following steps:
1. `apt-get update && apt-get install -y rabbitmq-server`
2. `systemctl enable rabbitmq-server && systemctl start rabbitmq-server`
3. `systemctl status rabbitmq-server` — verify active/running
4. Enable the management plugin: `rabbitmq-plugins enable rabbitmq_management`
5. Create a dedicated jC vhost: `rabbitmqctl add_vhost jarvischat`
6. Create a dedicated user: `rabbitmqctl add_user jarvischat CHANGEME_PASSWORD` — generate a random 24-char alphanumeric password and record it
7. Grant permissions: `rabbitmqctl set_permissions -p jarvischat jarvischat ".*" ".*" ".*"`
8. Verify management UI is reachable: `curl -s -u guest:guest http://localhost:15672/api/overview | python3 -m json.tool`
9. Delete default guest user: `rabbitmqctl delete_user guest`

Declare the two topic exchanges needed by jC:
- Exchange name: `jc.admin`, type: `topic`, durable: true
- Exchange name: `jc.system`, type: `topic`, durable: true

Use `rabbitmqadmin` or `curl` against the management API to declare exchanges. Verify both exchanges appear in: `curl -s -u jarvischat:{password} http://localhost:15672/api/exchanges/jarvischat`

Write the generated RabbitMQ password to `/home/gramps/.jc_amqp_secret` with mode 600. This will be read by jC as an env var source in subsequent tasks.

No pytest tests required for this infrastructure task.

---

## TASK 10 — Roadmap N2: AMQP Connection Layer in jC

This task adds the core AMQP connection manager to jC. It must connect to RabbitMQ on ultron (localhost from jC's perspective since jC runs on ultron), handle reconnection, and provide a shared channel for all AMQP operations.

**Add to `requirements.txt`:** `aio-pika>=9.0.0`

**Add to `config.py`:**
- `AMQP_URL` — read from env `JARVISCHAT_AMQP_URL`, default `amqp://jarvischat:password@localhost:5672/jarvischat`. The actual password comes from `/home/gramps/.jc_amqp_secret` — read it at startup if the env var is not set.
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

## TASK 11 — Roadmap N3: Worker Node Registration Handler (Ultron/jC Side)

jC on ultron must listen on the `jc.admin` exchange for worker node registration requests and respond with admission or rejection.

**Add to `amqp.py`:**

```python
async def subscribe(exchange, routing_key, callback) -> None
    # Declare a queue, bind to exchange/routing_key, consume with callback
```

**Create `cluster.py`** in the project root:

```python
# In-memory cluster registry (survives only while jC is running)
# Structure:
# CLUSTER_NODES: dict[str, NodeRecord]
#
# NodeRecord fields:
#   node_name: str
#   ip: str
#   active_model: ModelRecord
#   inventory: list[ModelRecord]
#   registered_at: str  (ISO timestamp)
#   last_seen: str      (ISO timestamp)
#
# ModelRecord fields:
#   name: str
#   version: str
#   quant: str
#   path: str
#   port: int           (llama-server port this model is served on)

async def handle_registration(message: aio_pika.IncomingMessage) -> None
    # Parse JSON payload from message body
    # Validate required fields: node_name, ip, active_model, inventory
    # Reject if node_name already in CLUSTER_NODES with status="active":
    #   publish to jc.admin routing_key=f"node.{node_name}.rejected"
    #   payload: {node_name, reason: "duplicate_node_name", timestamp}
    # Reject if payload malformed:
    #   publish to jc.admin routing_key=f"node.{node_name}.rejected"
    #   payload: {node_name, reason: "malformed_payload", timestamp}
    # Otherwise admit:
    #   add to CLUSTER_NODES
    #   publish to jc.admin routing_key=f"node.{node_name}.admitted"
    #   payload: {node_name, timestamp, amqp_url: AMQP_URL}

async def handle_deregistration(message) -> None
    # Remove node from CLUSTER_NODES, log it

def get_cluster_state() -> dict
    # Return serializable snapshot of CLUSTER_NODES
```

**Subscribe to registration messages in `app.py` lifespan** after AMQP connects:
- `jc.admin` exchange, routing key `node.*.register` → `handle_registration`
- `jc.admin` exchange, routing key `node.*.deregister` → `handle_deregistration`

**Add `GET /api/cluster`** to a new `routers/cluster.py`:
- Returns `get_cluster_state()` as JSON
- No auth required (read-only status endpoint)

**Wire `cluster.router` into `app.py`.**

**Write `tests/test_cluster.py`** covering:
- Valid registration payload — assert node admitted, added to CLUSTER_NODES, admitted message published
- Duplicate node name — assert rejected, reason=`duplicate_node_name`
- Malformed payload (missing required field) — assert rejected, reason=`malformed_payload`
- Deregistration — assert node removed from CLUSTER_NODES
- `GET /api/cluster` — assert returns current node list

Mock all aio-pika calls. Do not require live RabbitMQ.

Run full test suite. All existing tests must continue to pass.

---

## TASK 12 — Roadmap N4: Worker Node Registration Publisher (Jarvis Side)

This task creates the worker node AMQP client that runs on jarvis (192.168.50.210). It is a standalone Python script — not part of the jC FastAPI app — that runs as a systemd service on jarvis.

**Create `node_agent/agent.py`** in the repo (new directory):

The agent:
1. On start: reads local config from `/etc/jc-node-agent.conf` (INI format):
   - `node_name` — hostname, default from `socket.gethostname()`
   - `node_ip` — LAN IP, default from socket
   - `amqp_url` — RabbitMQ URL on ultron, e.g. `amqp://jarvischat:password@192.168.50.108:5672/jarvischat`
   - `llama_port` — port llama-server/llama-rpc is listening on, default 8081
   - `models_dir` — path to GGUF model files, default `/home/gramps/models`
   - `active_model` — filename of currently active model (without path)

2. Discovers inventory by globbing `models_dir` for `*.gguf` files and parsing name/version/quant from filename using regex pattern: `{name}-{version}-{quant}.gguf` where quant matches `Q[0-9]+_K_[A-Z]+` or similar standard suffixes.

3. Publishes registration request to `jc.admin` exchange, routing key `node.{node_name}.register`:
   ```json
   {
     "node_name": "jarvis",
     "ip": "192.168.50.210",
     "active_model": {"name": "...", "version": "...", "quant": "...", "path": "...", "port": 8081},
     "inventory": [...]
   }
   ```

4. Listens for response on `jc.admin`, routing key `node.{node_name}.admitted` or `node.{node_name}.rejected`. Logs result. If rejected, exits with error.

5. After admission: publishes heartbeat every 30 seconds to `jc.system`, routing key `node.{node_name}.heartbeat`:
   ```json
   {"node_name": "...", "ip": "...", "active_model": "...", "timestamp": "..."}
   ```

6. Listens on `jc.admin`, routing key `node.{node_name}.cmd.swap_model`:
   - Payload: `{model_filename: str}`
   - Stops current llama-server: `systemctl stop llama-server`
   - Updates `/etc/jc-node-agent.conf` active_model field
   - Starts llama-server: `systemctl start llama-server` (assumes service reads active_model from conf or ExecStart is updated)
   - Waits for llama-server to be healthy: poll `http://localhost:{llama_port}/v1/models` every 2s, timeout 120s
   - Publishes to `jc.system`, routing key `node.{node_name}.model_ready`:
     ```json
     {"node_name": "...", "active_model": "...", "port": ..., "timestamp": "..."}
     ```
   - If startup fails within timeout: publishes `node.{node_name}.model_failed` with error detail

**Create `node_agent/requirements.txt`:** `aio-pika>=9.0.0`

**Document `/etc/jc-node-agent.conf` format** in a comment block at the top of `agent.py`.

**Write `tests/test_node_agent.py`** covering:
- Registration payload construction from config + model discovery — assert correct JSON shape
- Model swap command handler: success path — assert systemctl calls made, model_ready published
- Model swap command handler: timeout path — assert model_failed published
- Heartbeat: assert published every interval (mock asyncio.sleep)

Mock all aio-pika, subprocess, and httpx calls.

**Do not create a systemd service file in this task** — that is a manual deployment step. Document the required service configuration in a comment at the bottom of `agent.py`.

Run full test suite. All existing tests must continue to pass.

---

## TASK 13 — Roadmap N5: Query Routing via AMQP + Phi-4-mini Triage

This task wires the cluster into jC's chat flow. When a query arrives at `/api/chat`, instead of always routing to the hardcoded `LLAMA_SERVER_BASE`, jC now routes to the best available cluster node based on query context.

**Prerequisites:** Tasks 9–12 complete. At least one worker node admitted to cluster.

**Install Phi-4-mini on ultron (infrastructure step):**
- Download `Phi-4-mini-Instruct-Q4_K_M.gguf` from HuggingFace using `hf download microsoft/Phi-4-mini-instruct --include "*.Q4_K_M.gguf" --local-dir /home/gramps/models`
- Create `/etc/systemd/system/llama-server-triage.service` — same pattern as existing llama-server service but: port 8083, model path points to Phi-4-mini GGUF, no `--rpc` flag (runs entirely on ultron CPU/iGPU), description `Llama.cpp Server (Phi-4-mini — triage/routing)`
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

## TASK 14 — Roadmap N6: Model Swap Command Flow

This task implements the ultron-side logic for requesting a model swap on a worker node when the ideal model is not currently active.

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

## TASK 15 — Roadmap N7: Cluster Status UI

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

Commit all changes introduced across Tasks 9–15 with message: `feat: Roadmap N — AMQP cluster nervous system complete`

---

## Backlog (Post-Roadmap N)

### B1 — Context loss in follow-up questions

**Symptom:** After asking "in {context}, explain {b}", a follow-up "what is {b}'s {x}?" gets a non-sequitur response that ignores the original context.

**Diagnosis:** `build_system_prompt()` is called fresh per-request with new RAG/memory results keyed to the current message text. These can change between turns and may dilute or override the conversation history. The original system prompt used for turn 1 (including its RAG context) is not stored in the DB — only user/assistant messages are. The inference server receives a different system prompt each turn.

**Possible fixes:**
- Store the assembled system prompt with each assistant message in the DB
- When replaying history, re-send the original system prompts from DB rather than rebuilding
- Or: cap RAG/memory injection to only fire on the first message of a conversation, then rely solely on conversation history for follow-ups
- Check that llama-server isn't truncating history due to context window overflow (Mistral-Nemo 12B = 128K context, unlikely)

### B2 — Bang-prefixed search routing

**Spec:** If a query begins with `!`, route to SearXNG search instead of local inference.

**Where:** In `routers/chat.py` `chat()` handler, after `user_message` is extracted. Strip the `!`, set a flag to always trigger auto-search regardless of perplexity/refusal.

**Change:** Add a `force_search` flag when `user_message.startswith("!")`, strip the prefix from the message saved to DB, and route directly to the search+summarize path.
