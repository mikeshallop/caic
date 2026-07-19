#!/usr/bin/env bash
# cAIc — bare-metal/systemd uninstall
# Removes the cAIc install deployed via the README Fresh Install path.
# Run as root or with sudo. Use -y to skip prompts.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
SKIP_PROMPT=false
[ "${1:-}" = "-y" ] && SKIP_PROMPT=true

prompt_yn() {
    if $SKIP_PROMPT; then return 0; fi
    local msg=$1; local default=${2:-n}
    if [[ "$default" == "y" ]]; then
        read -p "$msg [Y/n] " r; [[ -z "$r" || "$r" =~ ^[Yy] ]]
    else
        read -p "$msg [y/N] " r; [[ "$r" =~ ^[Yy] ]]
    fi
}
warn() { echo -e "${YELLOW}$1${NC}"; }

echo "cAIc Bare-metal / systemd uninstall"
echo "===================================="

# ---- Step 1: systemd service ----
SERVICE_FILE="/etc/systemd/system/caic.service"
if [ -f "$SERVICE_FILE" ]; then
    warn "Stopping and disabling caic service..."
    systemctl stop caic 2>/dev/null || true
    systemctl disable caic 2>/dev/null || true
    if prompt_yn "Remove caic systemd service?" n; then
        rm -f "$SERVICE_FILE" /etc/systemd/system/caic.service
        systemctl daemon-reload
        echo "  Removed systemd service"
    fi
else
    echo "  Skipped: no systemd service at $SERVICE_FILE"
fi

# ---- Step 2: /opt/caic directory ----
CAIC_DIR="/opt/caic"
if [ -d "$CAIC_DIR" ]; then
    if prompt_yn "Remove cAIc installation directory ($CAIC_DIR)?" n; then
        rm -rf "$CAIC_DIR"
        echo "  Removed $CAIC_DIR"
    else
        warn "  Skipped: $CAIC_DIR preserved"
    fi
else
    echo "  Skipped: $CAIC_DIR does not exist"
fi

# ---- Step 3: AMQP secret file ----
if [ -f "/home/gramps/.caic_amqp_secret" ]; then
    if prompt_yn "Remove AMQP secret file (/home/gramps/.caic_amqp_secret)?" n; then
        rm -f /home/gramps/.caic_amqp_secret
        echo "  Removed AMQP secret"
    fi
fi

# ---- Step 4: Upload temp directory ----
UPLOAD_DIR="/tmp/caic_uploads"
if [ -d "$UPLOAD_DIR" ]; then
    if prompt_yn "Remove upload temp directory ($UPLOAD_DIR)?" n; then
        rm -rf "$UPLOAD_DIR"
        echo "  Removed upload temp"
    fi
fi

# ---- Step 5: hardware_state.json in cwd ----
if [ -f "hardware_state.json" ]; then
    if prompt_yn "Remove cached hardware state?" n; then
        rm -f hardware_state.json
        echo "  Removed cached hardware state"
    fi
fi

# ---- Step 6: pip packages if user wants ----
if command -v pip &>/dev/null; then
    if prompt_yn "Attempt to uninstall cAIc-related pip packages?" n; then
        pip uninstall -y fastapi uvicorn httpx psutil aio-pika jinja2 python-multipart pypdf 2>/dev/null || true
        echo "  Uninstalled pip packages"
    fi
fi

echo ""
echo -e "${GREEN}cAIc bare-metal/uninstall complete.${NC}"
echo "The following may remain:"
echo "  - /opt/caic/                     (if you chose to preserve)"
echo "  - ~/.caic_amqp_secret            (if preserved)"
echo "  - hardware_state.json            (if preserved)"
echo "  - installed pip packages         (if you chose to skip)"
echo "  - Python venv at /opt/caic/venv  (removed with /opt/caic)"
echo ""
echo "Caic data stored outside install:"
echo "  - caic.db                        (if configured via CAIC_DB_PATH)"
echo "  - claude/opencode .jsonc updates (not tracked by this script)"
