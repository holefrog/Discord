#!/usr/bin/env bash
# Discord bridge (VPS architecture): ask the VPS queue which @-mentions are
# waiting for a reply. Wake the worker only for ids not woken in the last
# 180 s, so one slow item does not spawn a worker every 5 s; an item still
# pending after 180 s is re-woken (stuck-worker retry).
#
# Health heartbeat (2026-11-16, owner-approved): the same 5 s poll also
# watches the bridge itself. One failed poll is treated as a blip
# (fail_streak=1, silent). Two consecutive failures, with the egress
# control (example.com) still OK, declare the bridge down and wake once
# ("bridge-down") so the worker alerts the owner (chat + email). If it
# stays down 600 s, wake once more ("restart-due") so the worker checks
# for a hold-off from the owner and otherwise restarts the VPS itself
# (services first if SSH still works, else GCP console Reset). After a
# restart, if still down 300 s later, wake once ("restart-failed") and
# never auto-restart again for this incident. When the bridge answers
# again after a down incident, wake once ("bridge-recovered").
# Health state lives in discord-bridge-health.json next to the mention
# state; the restart worker updates restarted/restarted_at there.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

TOKEN_FILE="$HOME/hooks/secrets/discord-bridge-newvps-hook-token"
STATE_FILE="$HOME/hooks/state/discord-bridge-vps-check.json"
HEALTH_FILE="$HOME/hooks/state/discord-bridge-health.json"
URL="http://<VPS_IP>:3000/api/bridge/inbox"
CONTROL_URL="https://example.com"
RESTART_AFTER_SECS=600
RESTART_FAILED_AFTER_SECS=300
mkdir -p "$HOME/hooks/state"
DRY="${HATCH_HOOK_DRY_RUN:-0}"

tok="$(cat "$TOKEN_FILE" 2>/dev/null || true)"
if [ -z "$tok" ]; then
    log "bridge hook token missing" '{}'
    silent "bridge token missing" '{}'
    exit 0
fi

# Health state machine: stdin is the python program; args are
# health_file now up(0/1) restart_after restart_failed_after.
# Prints one JSON line: {"action":"silent|wake","reason":...,
# "payload":{...},"state":{...}|null} where state=null means no change.
health_decide() {
    python3 - "$HEALTH_FILE" "$1" "$2" "$RESTART_AFTER_SECS" "$RESTART_FAILED_AFTER_SECS" <<'PYEOF'
import json, sys
path, now, up, restart_after, restart_failed_after = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1", int(sys.argv[4]), int(sys.argv[5])
try:
    s = json.load(open(path))
except Exception:
    s = {}
s.setdefault("status", "ok")
s.setdefault("fail_streak", 0)
s.setdefault("first_fail_at", None)
s.setdefault("since", None)
s.setdefault("restart_woken", False)
s.setdefault("restarted", False)
s.setdefault("restarted_at", None)
s.setdefault("restart_failed_woken", False)

def out(action, reason, payload=None, state=None):
    print(json.dumps({"action": action, "reason": reason, "payload": payload or {}, "state": state}))
    sys.exit(0)

if up:
    if s["status"] == "down":
        down_since, down_secs, was_restarted = s["since"], now - (s["since"] or now), s["restarted"]
        fresh = {"status": "ok", "fail_streak": 0, "first_fail_at": None, "since": None,
                 "restart_woken": False, "restarted": False, "restarted_at": None,
                 "restart_failed_woken": False}
        out("wake", "bridge-recovered",
            {"down_since": down_since, "down_secs": down_secs, "restarted": was_restarted}, fresh)
    if s["fail_streak"] != 0 or s["first_fail_at"] is not None:
        s["fail_streak"] = 0
        s["first_fail_at"] = None
        out("silent", "bridge ok", {}, s)
    out("silent", "bridge ok", {}, None)

# bridge down
s["fail_streak"] = s["fail_streak"] + 1
if s["first_fail_at"] is None:
    s["first_fail_at"] = now
if s["status"] == "down":
    elapsed = now - (s["since"] or now)
    if not s["restart_woken"] and elapsed >= restart_after:
        s["restart_woken"] = True
        out("wake", "restart-due", {"down_since": s["since"], "down_secs": elapsed}, s)
    if (s["restarted"] and not s["restart_failed_woken"] and s["restarted_at"]
            and (now - s["restarted_at"]) >= restart_failed_after):
        s["restart_failed_woken"] = True
        out("wake", "restart-failed",
            {"down_since": s["since"], "down_secs": elapsed, "restarted_at": s["restarted_at"]}, s)
    out("silent", "bridge still down %ds" % elapsed, {}, s)
if s["fail_streak"] >= 2:
    s["status"] = "down"
    s["since"] = s["first_fail_at"]
    s["restart_woken"] = False
    s["restarted"] = False
    s["restarted_at"] = None
    s["restart_failed_woken"] = False
    out("wake", "bridge-down", {"down_since": s["since"]}, s)
out("silent", "bridge unreachable (1st failure, watching)", {}, s)
PYEOF
}

now="$(date +%s)"
if ! out="$(curl -s --max-time 8 -H "X-Bridge-Token: $tok" "$URL" 2>/dev/null)"; then
    # Bridge unreachable (timeout/DNS/proxy): must NOT be reported as
    # "no pending mentions" — that hides real @-mentions during an outage.
    # Control check first: if our own egress is broken too, the VPS may
    # be fine and this is not its incident.
    if ! code="$(curl -s -o /dev/null --max-time 8 -w "%{http_code}" "$CONTROL_URL" 2>/dev/null)" \
        || { [ "${code:-000}" -lt 200 ] || [ "${code:-000}" -ge 400 ]; }; then
        log "egress check failed, bridge status unknown" '{}'
        silent "egress check failed, bridge status unknown" '{}'
        exit 0
    fi
    decision="$(health_decide "$now" 0)"
    reason="$(printf '%s' "$decision" | python3 -c 'import json,sys; print(json.load(sys.stdin)["reason"])')"
    new_state="$(printf '%s' "$decision" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["state"]) if d["state"] is not None else "")')"
    action="$(printf '%s' "$decision" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')"
    if [ -n "$new_state" ] && [ "$DRY" != "1" ]; then
        printf '%s' "$new_state" > "$HEALTH_FILE"
    fi
    if [ "$action" = "wake" ]; then
        payload="$(printf '%s' "$decision" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["payload"]))')"
        log "$reason" "$payload"
        wake "$reason" "$payload"
    else
        log "$reason" '{}'
        silent "$reason" '{}'
    fi
    exit 0
fi

# Bridge is up: run the health machine first (may wake on recovery).
decision="$(health_decide "$now" 1)"
reason="$(printf '%s' "$decision" | python3 -c 'import json,sys; print(json.load(sys.stdin)["reason"])')"
new_state="$(printf '%s' "$decision" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["state"]) if d["state"] is not None else "")')"
action="$(printf '%s' "$decision" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')"
if [ -n "$new_state" ] && [ "$DRY" != "1" ]; then
    printf '%s' "$new_state" > "$HEALTH_FILE"
fi
if [ "$action" = "wake" ]; then
    payload="$(printf '%s' "$decision" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["payload"]))')"
    log "$reason" "$payload"
    wake "$reason" "$payload"
    exit 0
fi

ids="$(printf '%s' "$out" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(" ".join(sorted(str(i.get("id")) for i in d.get("items", []))))
except Exception:
    print("")
' 2>/dev/null || true)"

if [ -z "${ids:-}" ]; then
    if [ "$DRY" != "1" ]; then
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
if [ "$DRY" != "1" ]; then
    printf '%s' "$ids" | python3 -c '
import json, sys, time
ids = sys.stdin.read().split()
json.dump({"ids": ids, "woken_at": int(time.time())}, open("'"$STATE_FILE"'", "w"))
'
fi
wake "discord-mention" "{\"new\": $n, \"source\": \"vps\"}"
