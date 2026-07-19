#!/usr/bin/env bash
# cAIc — Docker stack teardown
# Removes all containers, volumes, images, and generated files
# created by setup-wizard / docker compose.
# Run from the docker deployment directory. Use -y to skip prompts.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

prompt_yn() {
    if [ "${1:-}" = "-y" ]; then return 0; fi
    [ "$SKIP" = true ] && return 0
    local msg=$1; local default=${2:-n}
    if [[ "$default" == "y" ]]; then
        read -p "$msg [Y/n] " r; [[ -z "$r" || "$r" =~ ^[Yy] ]]
    else
        read -p "$msg [y/N] " r; [[ "$r" =~ ^[Yy] ]]
    fi
}

SKIP=false
[ "${1:-}" = "-y" ] && SKIP=true

echo "cAIc Docker stack teardown"
echo "=========================="

# ---- Step 1: docker compose down -v ----
if [ -f docker-compose.yml ] || [ -f compose.yml ]; then
    if prompt_yn "Stop Docker stack and remove volumes?" n; then
        docker compose down -v 2>/dev/null || docker-compose down -v 2>/dev/null || true
        echo "  Docker stack stopped and volumes removed"
    else
        warn "  Skipped: Docker stack left intact"
    fi
else
    echo "  Skipped: no compose file found"
fi

# ---- Step 2: Remove Docker images ----
if prompt_yn "Remove cAIc Docker image?" n; then
    docker rmi caic:latest 2>/dev/null || true
    echo "  Removed caic:latest"

    warn "Remove SearXNG / Qdrant / RabbitMQ / llama-server / Ollama images?"
    if prompt_yn "  Remove service images?" n; then
        for tag in searxng/searxng qdrant/qdrant rabbitmq:4-management \
                   ghcr.io/ggml-org/llama.cpp ollama/ollama; do
            docker rmi "$tag" 2>/dev/null || true
        done
        echo "  Service images removed"
    fi
fi

# ---- Step 3: Volumes ----
if prompt_yn "Remove remaining Docker volumes?" n; then
    for vol in caic_data caic_uploads searxng_config qdrant_storage rabbitmq_data ollama_models; do
        docker volume rm "$vol" 2>/dev/null || true
    done
    echo "  Docker volumes removed"
fi

# ---- Step 4: .env ----
if [ -f .env ]; then
    if prompt_yn "Remove .env file?" n; then
        rm -f .env
        echo "  .env removed"
    fi
fi

# ---- Step 5: generated directories ----
for dir in secrets searxng; do
    if [ -d "$dir" ]; then
        if prompt_yn "Remove $dir/ directory?" n; then
            rm -rf "$dir"
            echo "  $dir/ removed"
        fi
    fi
done

# ---- Step 6: generated files ----
for f in setup.log docker-compose.yml compose.yml; do
    [ -f "$f" ] || continue
    if prompt_yn "Remove $f?" n; then
        rm -f "$f"
        echo "  $f removed"
    fi
done

# ---- Step 7: model files (with big warning) ----
if [ -d models ]; then
    echo ""
    echo -e "${RED}WARNING: model files (*.gguf) can be gigabytes each.${NC}"
    if prompt_yn "Remove all model files from ./models/?" n; then
        rm -rf models
        echo "  models/ removed"
    else
        echo "  models/ preserved"
    fi
fi

echo ""
echo -e "${GREEN}cAIc Docker stack teardown complete.${NC}"
echo "Preserved:"
echo "  - ./models/                       (unless you accepted removal)"
echo ""
echo "Note: Docker Engine itself is not removed. To remove it:"
echo "  sudo apt-get remove docker docker-engine docker.io containerd runc"
