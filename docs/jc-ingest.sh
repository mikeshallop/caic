#!/bin/bash
# jc-ingest.sh — pipe terminal commands into jarvisChat RAG
# Deploy to /home/gramps/bin/jc-ingest.sh on jarvis (192.168.50.210)
#
# Usage:
#   1. chmod +x /home/gramps/bin/jc-ingest.sh
#   2. Add to ~/.bashrc:
#        export JARVISCHAT_COMPLETIONS_API_KEY="$(cat /opt/jarvischat/.completions_key)"
#        export PROMPT_COMMAND="jc_capture"
#        source /home/gramps/bin/jc-ingest.sh
#
#   The PROMPT_COMMAND hook runs jc_capture() after each command.
#   Only commands matching the filter pattern are ingested.
#
# Filter: currently captures git, pip, systemctl, sudo, vi/vim, curl,
# wget, apt, python, pytest commands. Edit the grep pattern to adjust.

JC_URL="http://192.168.50.210:8080/api/ingest"
JC_TOKEN="${JARVISCHAT_COMPLETIONS_API_KEY}"

jc_capture() {
    local cmd
    cmd=$(history 1 | sed 's/^[ ]*[0-9]*[ ]*//')
    if echo "$cmd" | grep -qE '^(git|pip|systemctl|sudo|vi|vim|curl|wget|apt|python|pytest)'; then
        curl -s -X POST "$JC_URL" \
            -H "Authorization: Bearer $JC_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"content\": $(echo "$cmd" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'), \"source\": \"terminal\"}" \
            > /dev/null 2>&1 &
    fi
}
