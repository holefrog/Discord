#!/usr/bin/env bash
# Resident loop for the VPS Discord poller: one round, short pause, repeat.
# Round duration is the discovery latency; keep the pause small.
set -u
cd "$HOME/discord-bridge"
export BRIDGE_STATE_DIR="$HOME/discord-bridge"
while true; do
  /usr/bin/python3 "$HOME/discord-bridge/poll_vps.py" >> "$HOME/discord-bridge/poll.log" 2>&1 || true
  sleep 2
done
