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
mkdir -p models
if [ ! "$(ls -A models/*.gguf 2>/dev/null)" ]; then
    warn "No .gguf models found in ./models/"
    warn "  Place your model file(s) there before running docker compose up."
else
    ok "models/ has $(ls models/*.gguf 2>/dev/null | wc -l) model(s)"
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
