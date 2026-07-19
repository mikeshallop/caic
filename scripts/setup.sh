#!/usr/bin/env bash
# cAIc — First-run scaffolding
# Creates secrets, config, and directories needed by docker compose.
# Idempotent: safe to re-run.
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }

echo "cAIc — scaffolding"
echo "==================="

# ── .env ────────────────────────────────────────────────────
if [ ! -f .env ]; then
    cp .env.example .env

    # Auto-generate secrets
    ADMIN_PIN=$(shuf -i 1000-9999 -n 1)
    API_KEY="caic-sk-$(openssl rand -hex 24)"
    RMQ_PASS=$(openssl rand -hex 20)
    SEARX_KEY=$(openssl rand -hex 32)

    sed -i "s/^CAIC_ADMIN_PIN=$/CAIC_ADMIN_PIN=$ADMIN_PIN/" .env
    sed -i "s/^CAIC_COMPLETIONS_API_KEY=$/CAIC_COMPLETIONS_API_KEY=$API_KEY/" .env
    sed -i "s/^RABBITMQ_PASSWORD=$/RABBITMQ_PASSWORD=$RMQ_PASS/" .env
    sed -i "s/^SEARXNG_SECRET_KEY=$/SEARXNG_SECRET_KEY=$SEARX_KEY/" .env

    ok ".env created"
    warn "  Admin PIN:    $ADMIN_PIN"
    warn "  API key:      $API_KEY"
else
    warn ".env already exists — skipped"
fi

# ── secrets/ ────────────────────────────────────────────────
mkdir -p secrets
if [ ! -f secrets/rabbitmq_password.txt ]; then
    RMQ_PASS=$(grep '^RABBITMQ_PASSWORD=' .env | cut -d= -f2)
    echo -n "$RMQ_PASS" > secrets/rabbitmq_password.txt
    ok "secrets/rabbitmq_password.txt created"
else
    warn "secrets/rabbitmq_password.txt exists — skipped"
fi

# ── searxng/settings.yml ────────────────────────────────────
mkdir -p searxng
if [ ! -f searxng/settings.yml ]; then
    SEARX_KEY=$(grep '^SEARXNG_SECRET_KEY=' .env | cut -d= -f2)
    # Substitute the secret key into the template
    sed "s/\${SEARXNG_SECRET_KEY}/$SEARX_KEY/" searxng-settings.yml.dist > searxng/settings.yml
    ok "searxng/settings.yml created"
else
    warn "searxng/settings.yml exists — skipped"
fi

# ── models/ ─────────────────────────────────────────────────
DEFAULT_MODEL_REPO="unsloth/Qwen2.5-7B-Instruct-GGUF"
DEFAULT_MODEL_FILE="Qwen2.5-7B-Instruct-Q4_K_M.gguf"
DEFAULT_MODEL_SIZE_MB=4600  # approximate download size
DEFAULT_MODEL_NAME="qwen2.5-7b-instruct"

mkdir -p models

# Set LLAMA_MODEL and CAIC_DEFAULT_MODEL in .env if not already set
LLAMA_MODEL_LINE=$(grep '^LLAMA_MODEL=' .env || true)
if [ -z "$LLAMA_MODEL_LINE" ] || [ "$LLAMA_MODEL_LINE" = "LLAMA_MODEL=" ]; then
    sed -i "s/^LLAMA_MODEL=$/LLAMA_MODEL=$DEFAULT_MODEL_FILE/" .env
    sed -i "s/^CAIC_DEFAULT_MODEL=.*/CAIC_DEFAULT_MODEL=$DEFAULT_MODEL_NAME/" .env
    ok "Set LLAMA_MODEL=$DEFAULT_MODEL_FILE"
    ok "Set CAIC_DEFAULT_MODEL=$DEFAULT_MODEL_NAME"
fi

if ls models/*.gguf 1>/dev/null 2>&1; then
    ok "models/ has $(ls models/*.gguf | wc -l) model(s)"
else
    echo ""
    echo "  No .gguf models found in ./models/"
    echo ""

    # Check disk space
    AVAIL_KB=$(df -k models/ | tail -1 | awk '{print $4}')
    AVAIL_MB=$((AVAIL_KB / 1024))
    REQUIRED_MB=$((DEFAULT_MODEL_SIZE_MB + 500))  # 500MB safety margin

    if [ "$AVAIL_MB" -lt "$REQUIRED_MB" ]; then
        warn "Insufficient disk space: ${AVAIL_MB}MB available, ~${REQUIRED_MB}MB needed"
        warn "Free space or change LLAMA_MODEL in .env to use a smaller model."
        echo ""
    else
        echo "  Download default model (~${DEFAULT_MODEL_SIZE_MB}MB):"
        echo "    ${DEFAULT_MODEL_REPO}/${DEFAULT_MODEL_FILE}"
        echo ""
        read -p "  Download now? [Y/n] " r
        if [[ -z "$r" || "$r" =~ ^[Yy] ]]; then
            echo ""
            echo "  Downloading ${DEFAULT_MODEL_FILE}..."
            if command -v hf &>/dev/null; then
                # huggingface-cli (hf_transfer) if available
                hf download "$DEFAULT_MODEL_REPO" "$DEFAULT_MODEL_FILE" \
                    --local-dir models/ --local-dir-use-symlinks False
            elif command -v wget &>/dev/null; then
                wget -q --show-progress -O "models/$DEFAULT_MODEL_FILE" \
                    "https://huggingface.co/${DEFAULT_MODEL_REPO}/resolve/main/${DEFAULT_MODEL_FILE}"
            elif command -v curl &>/dev/null; then
                curl -L --progress-bar -o "models/$DEFAULT_MODEL_FILE" \
                    "https://huggingface.co/${DEFAULT_MODEL_REPO}/resolve/main/${DEFAULT_MODEL_FILE}"
            else
                warn "Neither wget nor curl found — cannot download."
                warn "  Manual: wget -O models/$DEFAULT_MODEL_FILE \\"
                warn "    https://huggingface.co/${DEFAULT_MODEL_REPO}/resolve/main/${DEFAULT_MODEL_FILE}"
            fi

            if [ -f "models/$DEFAULT_MODEL_FILE" ]; then
                DOWNLOADED_MB=$(du -m "models/$DEFAULT_MODEL_FILE" | cut -f1)
                ok "Downloaded ${DEFAULT_MODEL_FILE} (${DOWNLOADED_MB}MB)"
            else
                warn "Download failed — place model manually in ./models/"
            fi
        else
            warn "Skipped. Place .gguf model(s) in ./models/ before docker compose up."
        fi
    fi
fi

# ── data/ ───────────────────────────────────────────────────
mkdir -p data
ok "data/ directory ready"

# ── Verify Docker ───────────────────────────────────────────
echo ""
if command -v docker &>/dev/null && docker compose version &>/dev/null; then
    ok "Docker Compose available: $(docker compose version --short)"
    echo ""
    echo -e "${GREEN}Ready!${NC} Run:  docker compose up -d"
else
    warn "Docker Compose not found — install Docker Engine + Compose plugin first."
    echo "  https://docs.docker.com/engine/install/"
fi
