#!/usr/bin/env python3
"""Bridge queue API for the VPS-local Discord poller.

Stdlib-only HTTP server. Auth: header X-Bridge-Token must match
~/discord-bridge/hook-token (constant-time compare).

Endpoints:
  GET  /api/bridge/pending  -> {"pending": N}
  GET  /api/bridge/inbox    -> {"items": [unreplied queue items]}
  POST /api/bridge/replied  -> {"id": ..., "reply_id": ...} marks an item done
  POST /api/bridge/reply    -> {"channel_id": ..., "content": ...} posts to
                               Discord with the bot token from token.txt

Bind/port: BRIDGE_HOST (default 0.0.0.0), BRIDGE_PORT (default 8080).
State dir: BRIDGE_STATE_DIR (default ~/discord-bridge).
"""
import hmac
import json
import os
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE_DIR = os.path.expanduser(os.environ.get("BRIDGE_STATE_DIR", "~/discord-bridge"))
HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("BRIDGE_PORT", "8080"))
DISCORD = "https://discord.com/api/v10"
UA = "DiscordBot (bot-muse-bridge, 1.0)"


def read_file(name, default=""):
    try:
        with open(os.path.join(STATE_DIR, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


def bridge_token_ok(given):
    want = read_file("hook-token").strip()
    return bool(want) and bool(given) and hmac.compare_digest(given, want)


def read_inbox():
    items = []
    for line in read_file("inbox.jsonl").splitlines():
        line = line.strip()
        if line:
            try:
                items.append(json.loads(line))
            except ValueError:
                pass
    return items


def read_replied():
    try:
        return json.loads(read_file("replied.json", "{}") or "{}")
    except ValueError:
        return {}


def discord_post(channel_id, content):
    token = read_file("token.txt").strip()
    if not token:
        return None, "bot token not on VPS yet"
    ids = []
    for i in range(0, len(content), 1900):
        body = json.dumps({"content": content[i : i + 1900]}).encode()
        req = urllib.request.Request(
            "%s/channels/%s/messages" % (DISCORD, channel_id),
            data=body,
            method="POST",
            headers={
                "Authorization": token,
                "User-Agent": UA,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                m = json.loads(resp.read().decode() or "{}")
                if m.get("id"):
                    ids.append(m["id"])
        except urllib.error.HTTPError as e:
            return None, "discord send failed: HTTP %s" % e.code
        time.sleep(0.5)
    return ids, None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep journal clean
        pass

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        return bridge_token_ok(self.headers.get("X-Bridge-Token", ""))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw.decode() or "{}")
        except ValueError:
            return None

    def do_GET(self):
        if not self.path.startswith("/api/bridge/"):
            return self._json(404, {"error": "not found"})
        if not self._authed():
            return self._json(401, {"error": "bad bridge token"})
        if self.path == "/api/bridge/pending":
            replied = read_replied()
            pending = [i for i in read_inbox() if i["id"] not in replied]
            return self._json(200, {"pending": len(pending)})
        if self.path == "/api/bridge/inbox":
            replied = read_replied()
            items = [i for i in read_inbox() if i["id"] not in replied]
            return self._json(200, {"items": items})
        return self._json(404, {"error": "unknown bridge endpoint"})

    def do_POST(self):
        if not self.path.startswith("/api/bridge/"):
            return self._json(404, {"error": "not found"})
        if not self._authed():
            return self._json(401, {"error": "bad bridge token"})
        body = self._body()
        if body is None:
            return self._json(400, {"error": "invalid JSON body"})
        if self.path == "/api/bridge/replied":
            if not body.get("id"):
                return self._json(400, {"error": "id required"})
            replied = read_replied()
            replied[str(body["id"])] = str(body.get("reply_id") or "")
            with open(os.path.join(STATE_DIR, "replied.json"), "w", encoding="utf-8") as f:
                json.dump(replied, f, indent=2)
            return self._json(200, {"ok": True})
        if self.path == "/api/bridge/reply":
            channel_id = str(body.get("channel_id") or "")
            content = str(body.get("content") or "")
            if not channel_id or not content:
                return self._json(400, {"error": "channel_id and content required"})
            ids, err = discord_post(channel_id, content)
            if err:
                code = 503 if "token" in err else 502
                return self._json(code, {"error": err})
            return self._json(200, {"ok": True, "ids": ids})
        return self._json(404, {"error": "unknown bridge endpoint"})


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
