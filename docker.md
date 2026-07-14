# Docker Distribution — Architecture & Planning

> **Part of B3 (v1.0 gate).** This document catalogs every service, volume, port, configuration, and decision needed to ship cAIc as a `docker compose` stack. It also defines extraction (setup) and back-out (uninstall) procedures so nothing is lost when reality disagrees with the plan.

## 1. Stack Overview

```
┌─────────────────────────────────────────────────────────┐
│                    docker compose stack                  │
│                                                         │
│  ┌────────────┐  ┌──────────┐  ┌────────────────────┐  │
│  │  SearXNG    │  │  Qdrant  │  │  RabbitMQ          │  │
│  │  :8888      │  │  :6333   │  │  :5672 / :15672    │  │
│  └──────┬──────┘  └────┬─────┘  └────────┬───────────┘  │
│         │              │                  │              │
│         ▼              ▼                  ▼              │
│  ┌──────────────────────────────────────────────────┐   │
│  │              cAIc (FastAPI)                     │   │
│  │              :8080 (HTTP)                        │   │
│  │                                                  │   │
│  │  SQLite  ◄── caic.db                  (volume)    │   │
│  │  Uploads ◄── /app/uploads            (volume)    │   │
│  └──────────┬──────────────┬───────────────────────┘   │
│             │              │                            │
│             ▼              ▼                            │
│  ┌──────────────┐  ┌──────────────┐                    │
│  │ llama-server │  │   Ollama     │                    │
│  │ :8081        │  │ :11434       │                    │
│  │ (GPU/RPC)    │  │ (embeddings) │                    │
│  └──────────────┘  └──────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

> **This compose stack defines the coordinator.** A coordinator runs cAIc, the broker, and optional infrastructure services. Workers (headless inference nodes) do not use Docker — they install just llama-server + a Python node agent. See §9 for the worker deployment model.

### Service roles

| Service | Image | Role |
|---------|-------|------|
| **cAIc** | Custom `Dockerfile` | FastAPI app serving UI + API |
| **SearXNG** | `searxng/searxng:latest` | Privacy-respecting web search |
| **Qdrant** | `qdrant/qdrant:latest` | Vector database for RAG |
| **RabbitMQ** | `rabbitmq:4-management` | Message broker for AMQP cluster |
| **llama-server** | `ghcr.io/ggml-org/llama.cpp:server` | LLM inference (OpenAI-compat API) |
| **Ollama** | `ollama/ollama:latest` | Embeddings for RAG chunk vectors |

### Non-containerized (host-level)

| Component | Reason |
|-----------|--------|
| AMD GPU driver + ROCm | Kernel access required for GPU compute |
| llama.cpp RPC workers | Runs on *other* hosts — not on the Docker host |
| `rocm-smi` | Hardware stats — not needed for core function |
| `psutil` | Already inside the container via pip |

---

## 2. Service Catalog

### 2.1 cAIc (FastAPI app)

**Image:** `caic:latest` (built from `Dockerfile`)

**Ports:**
| Container | Host | Purpose |
|-----------|------|---------|
| 8080 | 8080 | HTTP API + UI |

**Volumes:**
| Container path | Type | Purpose |
|----------------|------|---------|
| `/app/caic.db` | named volume `caic_data` | SQLite database |
| `/app/uploads` | named volume `caic_uploads` | Uploaded files |
| `/app/hardware_state.json` | (inside volume) | Cached hardware probe |

**Dependencies:** Wait for SearXNG, Qdrant, RabbitMQ, llama-server, Ollama before serving.

**Restart:** `unless-stopped`

**Healthcheck:** `curl -f http://localhost:8080/`

### 2.2 SearXNG

**Image:** `searxng/searxng:latest`

**Ports:**
| Container | Host | Purpose |
|-----------|------|---------|
| 8080 | 8888 | Search API |

**Volumes:**
| Container path | Type | Purpose |
|----------------|------|---------|
| `/etc/searxng` | named volume `searxng_config` | `settings.yml` |

**Environment:**
```env
SEARXNG_BASE_URL=https://localhost:8888
```

**Config override (`/etc/searxng/settings.yml`):**
```yaml
search:
  safe_search: 0
  autocomplete: ""
server:
  secret_key: ${SEARXNG_SECRET_KEY}
  limiter: false
  image_proxy: false
  method: GET
  port: 8080
  bind_address: "0.0.0.0"
```

**Restart:** `unless-stopped`

### 2.3 Qdrant

**Image:** `qdrant/qdrant:latest`

**Ports:**
| Container | Host | Purpose |
|-----------|------|---------|
| 6333 | 6333 | HTTP API |
| 6334 | — | gRPC (internal only) |

**Volumes:**
| Container path | Type | Purpose |
|----------------|------|---------|
| `/qdrant/storage` | named volume `qdrant_storage` | Vector index data |

**Environment:**
```env
QDRANT__SERVICE__GRPC_PORT=6334
```

**Restart:** `unless-stopped`

### 2.4 RabbitMQ

**Image:** `rabbitmq:4-management`

**Ports:**
| Container | Host | Purpose |
|-----------|------|---------|
| 5672 | 5672 | AMQP messaging |
| 15672 | — | Management UI (internal only) |

**Volumes:**
| Container path | Type | Purpose |
|----------------|------|---------|
| `/var/lib/rabbitmq` | named volume `rabbitmq_data` | Message store |

**Environment:**
```env
RABBITMQ_DEFAULT_USER=caic
RABBITMQ_DEFAULT_PASS_FILE=/run/secrets/rabbitmq_password
RABBITMQ_DEFAULT_VHOST=/
```

**Restart:** `unless-stopped`

### 2.5 llama-server

**Image:** `ghcr.io/ggml-org/llama.cpp:server`

**Ports:**
| Container | Host | Purpose |
|-----------|------|---------|
| 8081 | 8081 | OpenAI-compat API |

**Volumes:**
| Container path | Type | Purpose |
|----------------|------|---------|
| `/models` | bind mount `./models` | Model GGUF files |

**Environment:**
```env
LLAMA_ARG_MODEL=/models/<model-file>
LLAMA_ARG_N_GPU_LAYERS=0              # set >0 for GPU offload
LLAMA_ARG_MAIN_GPU=0
LLAMA_ARG_CTX_SIZE=4096
LLAMA_ARG_HOST=0.0.0.0
LLAMA_ARG_PORT=8081
LLAMA_ARG_EMBEDDINGS=1
LLAMA_ARG_LOGPROBS=1
LLAMA_ARG_RPC=                         # optional: comma-separated RPC endpoints
```

**Restart:** `unless-stopped`

**Healthcheck:** `curl -f http://localhost:8081/health`

**Notes:**
- Models directory bind mount — user places `.gguf` files in `./models/` on the host
- RPC offload to other machines (e.g., `10.0.0.50:50052,10.0.0.51:50052`)
- If no GPU, set `LLAMA_ARG_N_GPU_LAYERS=0` for CPU-only
- `LLAMA_ARG_EMBEDDINGS=1` required for perplexity scoring
- `LLAMA_ARG_LOGPROBS=1` required for auto-search trigger

### 2.6 Ollama

**Image:** `ollama/ollama:latest`

**Ports:**
| Container | Host | Purpose |
|-----------|------|---------|
| 11434 | 11434 | Embeddings API |

**Volumes:**
| Container path | Type | Purpose |
|----------------|------|---------|
| `/root/.ollama` | named volume `ollama_models` | Pulled model blobs |

**Restart:** `unless-stopped`

**Notes:**
- Used exclusively for embeddings (`/api/embeddings`), not inference
- Typically needs a small model like `all-minilm:latest` or `nomic-embed-text:latest`
- Consider replacing Ollama with llama-server's built-in embedding if it supports the same model — would remove one container

---

## 3. Configuration Management

### 3.1 `.env` file (generated by setup wizard)

```env
# --- Secrets (auto-generated, change before production) ---
CAIC_ADMIN_PIN=
CAIC_COMPLETIONS_API_KEY=
CAIC_ALLOW_DEFAULT_PIN=false
RABBITMQ_PASSWORD=
SEARXNG_SECRET_KEY=

# --- Host discovery (auto-detected by setup wizard) ---
LLAMA_SERVER_BASE=http://llama-server:8081
OLLAMA_BASE=http://ollama:11434
SEARXNG_BASE=http://searxng:8888
QDRANT_URL=http://qdrant:6333
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672

# --- Performance tuning (calculated by setup wizard) ---
RAG_MAX_VECTORS=50000
RAG_EVICTION_HIGH_WATER=0.80
RAG_EVICTION_LOW_WATER=0.20
RAG_EVICTION_BATCH=1000

# --- llama-server options ---
LLAMA_MODEL=llama3.1-8b-instruct.Q4_K_M.gguf
LLAMA_N_GPU_LAYERS=0
LLAMA_RPC_ENDPOINTS=
LLAMA_CTX_SIZE=4096

# --- Ollama ---
OLLAMA_EMBED_MODEL=all-minilm:latest

# --- Network ---
CAIC_ALLOWED_CIDRS=127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
CAIC_TRUSTED_ORIGINS=
CAIC_TRUST_X_FORWARDED_FOR=false
```

### 3.2 Mapping of config.py → .env variable

Every config.py default that references an external service must accept a matching env var at runtime:

| config.py constant | .env variable | Service |
|-------------------|---------------|---------|
| `LLAMA_SERVER_BASE` | `LLAMA_SERVER_BASE` | llama-server |
| `OLLAMA_BASE` | `OLLAMA_BASE` | Ollama |
| `SEARXNG_BASE` | `SEARXNG_BASE` | SearXNG |
| `QDRANT_URL` | `QDRANT_URL` | Qdrant |
| `COMPLETIONS_API_KEY` | `CAIC_COMPLETIONS_API_KEY` | — |
| `ALLOWED_CIDRS_RAW` | `CAIC_ALLOWED_CIDRS` | — |
| `TRUST_X_FORWARDED_FOR` | `CAIC_TRUST_X_FORWARDED_FOR` | — |
| `TRUSTED_ORIGINS` | `CAIC_TRUSTED_ORIGINS` | — |
| `RAG_MAX_VECTORS` | `RAG_MAX_VECTORS` | — (calc'd from RAM) |

### 3.3 Secrets management

| Secret | Generated by | Stored in | Mounted to |
|--------|-------------|-----------|------------|
| `CAIC_ADMIN_PIN` | User prompt | `.env` | cAIc container |
| `CAIC_COMPLETIONS_API_KEY` | Auto-generated, shown to user | `.env` | cAIc container |
| `RABBITMQ_PASSWORD` | Auto-generated | `.env` + Docker secret | RabbitMQ container |
| `SEARXNG_SECRET_KEY` | Auto-generated | `.env` | SearXNG container |

**Docker secrets approach:** Use `secrets:` in compose file for RabbitMQ password (mounted as file) rather than passing via env var, since `settings.yml` in SearXNG and RabbitMQ config can reference file-based secrets without env-var leakage.

### 3.4 Dockerfile for cAIc

```dockerfile
FROM python:3.13-slim-bookworm AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.13-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && \
    rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Multi-stage rationale:** First stage compiles/bundles packages (wheels), final stage is minimal. Devs can skip builder with `--target builder` for live-reload with volume mount.

---

## 4. docker-compose.yml structure

```yaml
services:
  caic:
    build: .
    ports: ["8080:8080"]
    volumes:
      - caic_data:/app/caic.db
      - caic_uploads:/app/uploads
    env_file: .env
    depends_on:
      searxng: { condition: service_started }
      qdrant: { condition: service_started }
      rabbitmq: { condition: service_healthy }
      llama-server: { condition: service_healthy }
      ollama: { condition: service_started }
    restart: unless-stopped

  searxng:
    image: searxng/searxng:latest
    ports: ["8888:8080"]
    volumes:
      - ./searxng/settings.yml:/etc/searxng/settings.yml:ro
      - searxng_config:/etc/searxng
    env_file: .env
    restart: unless-stopped

  qdrant:
    image: qdrant/qdrant:latest
    ports: ["6333:6333"]
    volumes:
      - qdrant_storage:/qdrant/storage
    restart: unless-stopped

  rabbitmq:
    image: rabbitmq:4-management
    ports: ["5672:5672"]
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
    env_file: .env
    secrets:
      - rabbitmq_password
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "check_port_connectivity"]
      interval: 15s
      timeout: 5s
      retries: 3
    restart: unless-stopped

  llama-server:
    image: ghcr.io/ggml-org/llama.cpp:server
    ports: ["8081:8081"]
    volumes:
      - ./models:/models:ro
    env_file: .env
    command: >
      --model /models/${LLAMA_MODEL}
      --host 0.0.0.0 --port 8081
      --ctx-size ${LLAMA_CTX_SIZE:-4096}
      --n-gpu-layers ${LLAMA_N_GPU_LAYERS:-0}
      --embeddings
      --logprobs
      ${LLAMA_RPC_ENDPOINTS:+--rpc ${LLAMA_RPC_ENDPOINTS}}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8081/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped

  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes:
      - ollama_models:/root/.ollama
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    restart: unless-stopped

volumes:
  caic_data:
  caic_uploads:
  searxng_config:
  qdrant_storage:
  rabbitmq_data:
  ollama_models:

secrets:
  rabbitmq_password:
    file: ./secrets/rabbitmq_password.txt
```

**Notes:**
- GPU reservations use `resources.reservations.devices` — this is compose v3.8+. For AMD GPUs, replace `driver: nvidia` with `driver: amd` (experimental Docker support). For hosts without GPU, omit the `deploy` block entirely.
- The `deploy` block only applies when deployed as a swarm stack. For `docker compose`, GPU access may need `--gpus all` or `device_requests` in config. Verify compatibility.
- SearXNG config file (`settings.yml`) is bind-mounted read-only from the host repo clone — the setup wizard should generate this file.

---

## 5. Networking

### 5.1 Internal communication (compose network)

| From | To | Port | Protocol |
|------|----|------|----------|
| cAIc | llama-server | 8081 | HTTP |
| cAIc | Ollama | 11434 | HTTP |
| cAIc | SearXNG | 8080 | HTTP |
| cAIc | Qdrant | 6333 | HTTP |
| cAIc | RabbitMQ | 5672 | AMQP |
| RabbitMQ | (cluster peers) | 4369 | EPMD |
| RabbitMQ | (cluster peers) | 25672 | Inter-node |

### 5.2 Exposed ports (host-facing)

| Port | Service | Should expose? | Notes |
|------|---------|---------------|-------|
| 8080 | cAIc | ✅ Required | UI + API |
| 8888 | SearXNG | Optional | Only if user wants standalone search |
| 6333 | Qdrant | Optional | Only for external tooling |
| 5672 | RabbitMQ | Optional | Only for remote AMQP clients |
| 15672 | RabbitMQ mgmt | ❌ Internal | Healthcheck only |
| 8081 | llama-server | Optional | Only for external tooling |
| 11434 | Ollama | Optional | Only for external tooling |

**Design decision:** By default, only port 8080 (cAIc) is published. All other services remain on the internal compose network. Advanced users can opt-in by uncommenting `ports:` blocks.

### 5.3 Reverse proxy consideration

For production, a reverse proxy (Caddy, nginx, Traefik) should sit in front:

```yaml
# Optional — compose profile: "proxy"
caddy:
  image: caddy:latest
  ports: ["80:80", "443:443"]
  volumes:
    - ./Caddyfile:/etc/caddy/Caddyfile:ro
    - caddy_data:/data
```

This is out of scope for v1.0 but documented for future.

### 5.4 WireGuard tunnel (off-site workers)

When a worker node runs on a different network (colo, friend's house, VPS), all cross-site traffic must be encrypted. WireGuard provides this at the network layer with zero application changes.

**Approach:** Install WireGuard on the Docker host (not inside a container). The host creates a tunnel interface (`wg0`) with a virtual IP in the `10.0.2.0/24` range. Containers that need to reach remote workers use the host's WireGuard IP via `network_mode: host` or standard routing.

```
Off-site worker                     Docker host (coordinator)
┌────────────────────┐              ┌───────────────────────────────┐
│  wg0: 10.0.2.2     │◄───UDP──────│  wg0: 10.0.2.1                │
│  llama-server      │   :51820     │                               │
│  node_agent.py     │   encrypts   │  ┌───────────────────────┐   │
│                    │   all        │  │  cAIc container        │   │
│                    │   traffic    │  │  LLAMA_SERVER_BASE     │   │
│                    │              │  │    → 10.0.2.2:8081     │   │
│                    │              │  │  CAIC_AMQP_URL         │   │
│                    │              │  │    → amqp://caic:@...  │   │
│                    │              │  └───────────────────────┘   │
└────────────────────┘              └───────────────────────────────┘
```

**Host setup (coordinator):**
```bash
sudo apt install wireguard
wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
chmod 600 /etc/wireguard/private.key
```
Then create `/etc/wireguard/wg0.conf` — see [WireGuard-Setup.md](WireGuard-Setup.md) for full per-node configs.

**Container networking:** The cAIc container needs to reach the WireGuard IP. Options:
1. **`network_mode: host`** — simplest, container shares host network stack. Done by adding `network_mode: host` to the cAIc service. Trade-off: no port isolation.
2. **Host routing** — the host's kernel routes `10.0.2.0/24` via `wg0`. Containers on the default bridge or compose network can reach those IPs if `ip_forward` is enabled. This works out of the box on Linux.

**cAIc env vars after WireGuard:**
```env
# Point at worker's WireGuard IP instead of LAN IP
LLAMA_SERVER_BASE=http://10.0.2.2:8081   # falls back to coordinator's own llama-server
CAIC_AMQP_URL=amqp://caic:password@10.0.2.1:5672/caic  # coordinator's RMQ on WG IP
```

The node agent on each worker configures its registration IP as the WireGuard tunnel IP (`node_ip = 10.0.2.2`), so `triage.py` constructs inference URLs pointing at the encrypted interface.

---

## 6. Setup Wizard (Extraction)

`setup.sh` — idempotent, interactive, runs on first boot.

### Flow

```
1. CHECK: Is .env present?
   ├── YES → skip to step 7 (or ask to regenerate)
   └── NO  → continue

2. INTRO: Print banner, explain what's about to happen

3. PROBE: Run hardware assessment
   ├── psutil → RAM total, CPU count
   ├── rocm-smi → VRAM (optional, best-effort)
   └── nvidia-smi → VRAM (optional, best-effort)

4. NETWORK: Ask for
   ├── Hostname / LAN IP for this machine
   ├── Admin PIN (4 digits, or accept auto-generated)
   └── (Optional) RPC endpoints for GPU offload

5. CALCULATE:
   ├── RAG_MAX_VECTORS = max(1000, int(available_ram_gb * 100_000))
   ├── LLAMA_N_GPU_LAYERS = 0 (CPU default; offer GPU detection)
   ├── LLAMA_MODEL = default gguf filename
   └── RABBITMQ_PASSWORD = openssl rand -hex 20

6. GENERATE:
   ├── .env file from template
   ├── ./secrets/rabbitmq_password.txt
   ├── ./searxng/settings.yml (with generated secret_key)
   └── ./models/README.txt (instructions for placing .gguf)

7. VERIFY:
   ├── docker and docker compose plugin installed
   ├── docker compose version >= 2.x
   ├── SUCCESS → "Run: docker compose up -d"
   └── FAILURE  → show diagnostics and links

8. EXTRACT model:
   ├── Prompt for download URL or local path
   ├── Offer to pull from HuggingFace if huggingface-cli available
   └── Guides user to place file in ./models/
```

### What setup.sh creates on disk

```
./docker-deploy/
├── .env                        # All env vars (SECRET — add to .gitignore)
├── docker-compose.yml          # Compose stack definition
├── Dockerfile                  # cAIc image build
├── secrets/
│   └── rabbitmq_password.txt   # RabbitMQ password file
├── searxng/
│   └── settings.yml            # SearXNG config with generated secret_key
├── models/
│   ├── README.txt              # Instructions for model placement
│   └── <model>.gguf            # (user-provided)
└── setup.log                   # Wizard run log
```

### Idempotency

Re-running `setup.sh`:
- With `.env` present: ask "Regenerate? This will overwrite existing config."
- Without `.env`: fresh run
- Never overwrites `./models/*.gguf` files
- Never touches running containers — only modifies files on disk

---

## 7. Back-out Procedure (Uninstall)

`teardown.sh` — returns the host system to its pre-install state.

### What gets removed

| Item | Removal method |
|------|---------------|
| Docker containers | `docker compose down -v` |
| Docker images | `docker rmi caic:latest` (ask about other images) |
| Docker volumes | `docker volume rm caic_data ...` (prompt first) |
| Network `caic_default` | Removed with compose |
| `.env` file | `rm .env` |
| `secrets/` directory | `rm -rf secrets/` |
| `searxng/` directory | `rm -rf searxng/` |
| `setup.log` | `rm setup.log` |
| `hardware_state.json` | `rm hardware_state.json` |

### What is preserved (by default)

| Item | Reason |
|------|--------|
| `./models/*.gguf` | User data — prompt for deletion |
| `caic.db` (in volume) | Prompt: "Keep database snapshot?" |
| `./uploads/` (in volume) | Prompt: "Keep uploaded files?" |
| Docker Engine itself | Not installed by this project — leave it |

### Script flow

```
1. CHECK: docker compose file exists?
   ├── NO  → warn, continue
   └── YES → docker compose down -v

2. CHECK: .env exists?
   ├── NO  → skip
   └── YES → ask: "Remove .env?" (default no)

3. ASK: "Remove secrets/ and searxng/ directories?" (default no)

4. ASK: "Remove Docker images? (y/N)" (default no)
   ├── Y → docker rmi caic:latest
   ├── Y → docker image ls | grep searxng/qdrant/rabbitmq → prompt per image
   └── N → skip

5. ASK: "Keep database volume snapshot? (Y/n)" (default yes)
   ├── N → docker volume rm caic_data
   └── Y → leave volume (can be reattached later)

6. ASK: "Remove model files from ./models/? (y/N)" (default no)

7. CLEANUP generated artifacts:
   ├── rm -f setup.log
   ├── rm -f hardware_state.json
   └── rm -f docker-compose.yml

8. SUMMARY:
   ├── "Docker stack removed"
   ├── "Persistent data preserved at: <paths>"
   └── "Models kept at: ./models/"
```

### Partial rollback

If the setup wizard fails mid-way, a partial rollback is better than leaving detritus:

| Failure point | Clean up |
|--------------|----------|
| After .env, before compose | `rm .env; rm -rf secrets/ searxng/` |
| After compose, before first `up` | `rm docker-compose.yml; rm -rf *` |
| After `up` but before healthcheck | `docker compose down -v; rm -rf ./*` |

`setup.sh` should trap EXIT on failure and prompt: "Clean up partial install? [y/N]"

---

## 8. Open Decisions

| Decision | Options | Priority |
|----------|---------|----------|
| **Ollama vs llama-server embeddings** | Both work. Keep both for now — remove Ollama if llama-server handles embeddings. Reduce containers = simpler. | Medium |
| **GPU support in compose** | NVIDIA: well-supported. AMD: requires `--device=/dev/kfd --device=/dev/dri` and ROCm image. Document both. | High |
| **RabbitMQ clustering vs single node** | Single node in v1.0. Clustering docs for multi-host later. | Low |
| **SearXNG config management** | Bind-mount a generated `settings.yml`, or let container create default and post-process. Bind-mount is cleaner. | Medium |
| **Reverse proxy** | Caddy is simplest for auto-HTTPS. Out of scope for v1.0 but design for it. | Low |
| **Healthcheck strategy** | `depends_on` with `condition: service_healthy` is the safest approach but increases startup time. Acceptable. | Medium |
| **Database migration** | SQLite file in volume — no migration needed for v1.0 format. If schema changes post-v1.0, need a migration container. | Low |
| **WireGuard integration** | Documented in docker.md §5.4 + wiki. Host-level install; no container changes needed. WireGuard sidecar container (`linuxserver/wireguard`) is an alternative for users who want everything in compose. | Low |
| **Linux vs macOS vs Windows** | Linux-primary. macOS may work with changes (no rocm-smi). Windows via WSL2 only. | Low |
| **LLM model download** | HuggingFace CLI integration in setup.sh, or manual download. Manual is simpler. | Low |
| **Dockerfile optimization** | Pin pip hashes, use `--no-cache-dir`, consider `slim` vs `alpine`. Alpine has musl compatibility issues with psutil. Stay with slim. | Medium |

## 9. Worker Node Deployment Model

The Docker stack above defines the **coordinator** only. Workers (headless inference nodes) have a radically lighter footprint.

### 9.1 What a worker runs

```
Worker machine (e.g. worker01, worker02)
┌────────────────────────────────────┐
│  llama-server                      │
│  (single binary, no build needed)  │
│                                    │
│  node_agent.py                     │
│  (Python script, aio-pika client)  │
│    ─ connects to coordinator's RMQ │
│    ─ publishes heartbeat + reg     │
│    ─ consumes model_swap commands  │
│                                    │
│  ROCm or CUDA runtime (if GPU)     │
└────────────────────────────────────┘
```

### 9.2 What a worker does NOT run

| Service | Reason |
|---------|--------|
| RabbitMQ server | Connects as AMQP *client* only (aio-pika) |
| FastAPI / uvicorn / jC | No HTTP API, no UI, no database |
| SQLite | No persistent state of its own |
| SearXNG | No web search needs |
| Qdrant | No local vector store |
| Ollama | Uses coordinator's embedding endpoint |
| Docker | Everything runs as bare binaries |
| Python venv with full jC deps | Only needs `aio-pika` + `httpx` |

### 9.3 Worker setup

```bash
# Install WireGuard (required for off-site workers — encrypts all traffic)
sudo apt install wireguard
# See docs/wiki/WireGuard-Setup.md for per-node config

# Install llama-server binary
wget https://github.com/ggml-org/llama.cpp/releases/.../llama-server
chmod +x llama-server

# Install node agent deps
pip install aio-pika httpx

# Create node agent config: /etc/caic-node-agent.conf
# Set node_ip to the WireGuard tunnel IP (e.g., 10.0.2.2)
# Set amqp_url to the coordinator's WireGuard IP (e.g., amqp://caic:password@10.0.2.1:5672/caic)
```

### 9.4 Multiple workers

Each worker registers independently with the coordinator's RabbitMQ. The coordinator tracks all registered workers via `CLUSTER_NODES` and routes inference requests to the best-matching node based on classification and availability.

### 9.5 RabbitMQ and workers — architecture note

Workers connect to RabbitMQ as **standard AMQP TCP clients** — no broker software required. The AMQP-0-9-1 protocol has always been client-server (since 2006), and libraries like `aio-pika`, `pika`, `amqplib`, `php-amqplib`, etc. connect over a single persistent socket. This is distinct from a service-mesh design where every node runs the same software stack and role is determined by config.

```
Broker-mediated model (this project):
  Coordinator runs  RabbitMQ broker  ←── Workers connect as AMQP clients

Service-mesh model (alternative):
  Every node runs    RabbitMQ broker  ←── Nodes cluster together, all autonomous
```

The broker-mediated model is the preferred architecture for this project because workers are intentionally heterogeneous (different GPUs, different models, ARM vs x86) and should not be burdened with infrastructure services.

## 10. Checklist (pre-v1.0 gate)

- [ ] `Dockerfile` written and builds clean
- [ ] `docker-compose.yml` boots all containers
- [ ] cAIc container reaches all services (env vars resolve correctly)
- [ ] SearXNG settings.yml generated correctly by setup.sh
- [ ] RabbitMQ password secret mounted correctly
- [ ] GPU (NVIDIA) passes through to llama-server container
- [ ] GPU (AMD) passes through to llama-server container (or documented limitation)
- [ ] `.env.example` checked in (no real secrets)
- [ ] `setup.sh` written, idempotent, tested on clean Debian
- [ ] `teardown.sh` written, tested, doesn't delete models without confirmation
- [ ] `docker compose up -d` works without any manual steps beyond setup.sh
- [ ] `docker compose down -v` followed by `setup.sh && docker compose up -d` = fresh stack
- [ ] Healthchecks prevent serving before dependencies are ready
- [ ] WireGuard tunnel documented and tested for off-site workers
- [ ] v1.0 release tag created

---

## 11. Files to create for B3

```
docker.md             ← this file (planning doc)
Dockerfile            ← cAIc image
docker-compose.yml    ← full stack
.env.example          ← template without secrets
setup.sh              ← extraction wizard
teardown.sh           ← back-out utility
searxng/
  settings.yml        ← SearXNG config (generated by setup.sh)
secrets/
  rabbitmq_password.txt  ← generated by setup.sh
models/
  README.txt          ← instructions for placing .gguf
```
