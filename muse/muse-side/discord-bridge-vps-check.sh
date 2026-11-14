#!/usr/bin/env bash
# Discord bridge (VPS architecture): ask the VPS queue which @-mentions are
# waiting for a reply. Wake the worker only for ids not woken in the last
# 180 s, so one slow item does not spawn a worker every 5 s; an item still
# pending after 180 s is re-woken (stuck-worker retry).
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

TOKEN_FILE="$HOME/hooks/secrets/discord-bridge-newvps-hook-token"
STATE_FILE="$HOME/hooks/state/discord-bridge-vps-check.json"
URL="http://<VPS_IP>:3000/api/bridge/inbox"
mkdir -p "$HOME/hooks/state"

tok="$(cat "$TOKEN_FILE" 2>/dev/null || true)"
if [ -z "$tok" ]; then
    log "bridge hook token missing" '{}'
    silent "bridge token missing" '{}'
    exit 0
fi

out="$(curl -s --max-time 8 -H "X-Bridge-Token: $tok" "$URL" 2>/dev/null || true)"
ids="$(printf '%s' "$out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(" ".join(sorted(str(i.get("id")) for i in d.get("items", []))))
except Exception:
    print("")
' 2>/dev/null || true)"

if [ -z "${ids:-}" ]; then
    if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then
        printf '{"ids": [], "woken_at": 0}\n' > "$STATE_FILE"
    fi
    silent "no pending mentions" '{}'
    exit 0
fi

now="$(date +%s)"
last_ids=""; last_woken=0
if [ -f "$STATE_FILE" ]; then
    read -r last_ids last_woken <<< "$(python3 -c '
import json
try:
    s = json.load(open("'"$STATE_FILE"'"))
    print(" ".join(s.get("ids", [])), int(s.get("woken_at", 0)))
except Exception:
    print("", 0)
' 2>/dev/null || echo ' 0')"
fi

if [ "$last_ids" = "$ids" ] && [ $((now - ${last_woken:-0})) -lt 180 ]; then
    silent "same mentions already woken recently" '{}'
    exit 0
fi

n="$(printf '%s' "$ids" | wc -w)"
if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then
    printf '%s' "$ids" | python3 -c '
import json, sys, time
ids = sys.stdin.read().split()
json.dump({"ids": ids, "woken_at": int(time.time())}, open("'"$STATE_FILE"'", "w"))
'
fi
wake "discord-mention" "{\"new\": $n, \"source\": \"vps\"}"
