#!/usr/bin/env bash
# Run ON the VPS by 所有者 himself. The token is typed at a hidden prompt,
# written straight to a 600-perm file on the VPS, and never shown, logged,
# or passed through Muse / chat.
set -euo pipefail
DIR="$HOME/discord-bridge"
mkdir -p "$DIR"
read -r -s -p "Paste the Bot token (input hidden), then Enter: " tok
echo
tok="$(printf '%s' "$tok" | tr -d ' \t\r\n')"
case "$tok" in
  Bot\ *) line="$tok" ;;
  *)      line="Bot $tok" ;;
esac
umask 077
printf '%s' "$line" > "$DIR/token.txt"
chmod 600 "$DIR/token.txt"
code="$(curl -s -o /tmp/botme.json -w '%{http_code}' --max-time 15 \
  -H "Authorization: $line" -H 'User-Agent: DiscordBot (bot-muse-bridge, 1.0)' \
  https://discord.com/api/v10/users/@me)"
echo "Discord answered: HTTP $code"
python3 -c "import json;d=json.load(open('/tmp/botme.json'));print('Bot account:', d.get('username'), d.get('id'))" 2>/dev/null || cat /tmp/botme.json
rm -f /tmp/botme.json
echo "Saved to $DIR/token.txt (600). Done - you can close this session."
