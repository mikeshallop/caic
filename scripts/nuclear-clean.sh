#!/usr/bin/env bash
# cAIc — nuclear clean uninstall
# Removes every trace of cAIc: bare-metal systemd install AND Docker stack.
# Prompts for each step. Use -y to skip ALL prompts (fully automatic).
# Use with extreme caution — will delete data and model files.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKIP=false
[ "${1:-}" = "-y" ] && SKIP=true

prompt_yn() {
    if $SKIP; then return 0; fi
    local msg=$1; local default=${2:-n}
    if [[ "$default" == "y" ]]; then
        read -p "$msg [Y/n] " r; [[ -z "$r" || "$r" =~ ^[Yy] ]]
    else
        read -p "$msg [y/N] " r; [[ "$r" =~ ^[Yy] ]]
    fi
}
warn() { echo -e "${YELLOW}$1${NC}"; }

if ! $SKIP; then
    echo ""
    echo "${RED}================================================${NC}"
    echo "${RED}   cAIc NUCLEAR CLEAN - removes all traces${NC}"
    echo "${RED}   This will delete data, models, and the app.${NC}"
    echo "${RED}================================================${NC}"
    echo ""
    echo "cAIc NUCLEAR CLEAN"
    echo "=================="
    read -p "Type 'yes' to continue: " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Aborted."
        exit 1
    else
        SKIP=true
    fi
fi

if ! $SKIP; then
    prompt_yn "Run nuclear clean?" y || { echo "Aborted."; exit 0; }
fi

echo ""
echo "========== PART 1: Docker stack =========="
if command -v docker &>/dev/null; then
    # Stop all cAIc-related containers
    docker compose down 2>/dev/null || docker-compose down 2>/dev/null || echo "  no compose stack running"
    docker ps --filter name=cai[c] --filter name=searxng --filter name=qdrant --filter name=rabbit --filter name=llama-server --filter name=ollama --filter name=caic -aq 2>/dev/null \
        | xargs -r docker rm -f 2>/dev/null || true
    docker volume rm caic_data caic_uploads searxng_config qdrant_storage rabbitmq_data ollama_models 2>/dev/null || echo "  volumes already gone"
    docker rmi caic:latest 2>/dev/null || echo "  caic image already gone"
    echo "  Docker stack removed"
else
    echo "  Docker not installed — skipping"
fi

echo ""
echo "========== PART 2: Systemd service ========="
SERVICE_FILE="/etc/systemd/system/caic.service"
if [ -f "$SERVICE_FILE" ]; then
    stop caic 2>/dev/null || true
    systemctl disable caic 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    echo "  systemd service removed"
else
    echo "  no systemd service found"
fi

echo ""
echo "========== PART 3: Install directory ========"
for dir in /opt/caic /var/lib/caic; do
    if [ -d "$dir" ]; then
        rm -rf "$dir"
        echo "  Removed: $dir"
    else
        echo "  Skipped: $dir"
    fi
done

echo ""
echo "========== PART 4: Config and state files ========"
for f in \
    /home/gramps/.caic_amqp_secret \
    hardware_state.json \
    setup.log \
    .env; do
    rm -f "$f" 2>/dev/null || true
done
echo "  Config/state files cleaned"

echo ""
echo "========== PART 5: Temp data ==========="
rm -rf /tmp/caic_uploads 2>/dev/null || true
echo "  Temp data cleaned"

echo ""
echo "========== PART 6: User data (prompt each) ========="
if prompt_yn "Remove the repository (current dir)?" n; then
    cd ..
    rm -rf "$SCRIPT_DIR"
    echo "  Repository removed"
else
    echo "  Repository preserved"
fi

echo ""
echo "${GREEN}cAIc nuclear clean complete.${NC}"
echo ""
echo "Manually verify:"
echo "  - ls /opt/caic/"
echo "  - ls /etc/systemd/system/caic*"
echo "  - docker images | grep -E \"caic|searx|qdrant|rabbit|llama|ollama\""
echo "  - docker ps -a"
echo ""
echo "To remove Docker Engine itself:"
echo "  sudo apt remove docker.io containerd runc"
echo "  sudo rm -rf /var/lib/docker"
echo ""
echo "To remove python packages:"
echo "  pip uninstall fastapi uvicorn httpx psutil jinja2 python-multipart pypdf aio-pika"
