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
  POST /api/bridge/reply-image -> {"channel_id": ..., "content": ..., 
                               "filename": ..., "image_base64": ...} posts one
                               image (png/jpg/gif/webp, <=8 MB) to Discord via
                               multipart upload, with optional caption text
  GET  /api/bridge/messages?channel_id=..&limit=.. -> {"items": [...]} reads
                               recent messages of a channel with the bot token

Bind/port: BRIDGE_HOST (default 0.0.0.0), BRIDGE_PORT (default 8080).
Production (2026-11-13 split): BRIDGE_HOST=127.0.0.1, BRIDGE_PORT=3001,
behind the nginx reverse proxy on :3000 (path /api/bridge/).
State dir: BRIDGE_STATE_DIR (default ~/discord-bridge).
"""
import base64
import hmac
import json
import os
import time
import urllib.parse
import uuid
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


_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def discord_post_image(channel_id, content, image_bytes, filename):
    """Post one image file to a Discord channel (multipart upload)."""
    token = read_file("token.txt").strip()
    if not token:
        return None, "bot token not on VPS yet"
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        return None, "image missing or over 8 MB"
    ext = os.path.splitext(filename or "")[1].lower()
    mime = _IMAGE_TYPES.get(ext)
    if not mime:
        return None, "unsupported image type (png/jpg/gif/webp only)"
    boundary = uuid.uuid4().hex
    payload = json.dumps(
        {"content": (content or "")[:1900]}, ensure_ascii=False
    ).encode()
    body = b"".join(
        [
            b"--" + boundary.encode() + b"\r\n",
            b'Content-Disposition: form-data; name="payload_json"\r\n',
            b"Content-Type: application/json\r\n\r\n",
            payload + b"\r\n",
            b"--" + boundary.encode() + b"\r\n",
            (
                'Content-Disposition: form-data; name="files[0]"; '
                'filename="%s"\r\n' % os.path.basename(filename)
            ).encode(),
            ("Content-Type: %s\r\n\r\n" % mime).encode(),
            image_bytes + b"\r\n",
            b"--" + boundary.encode() + b"--\r\n",
        ]
    )
    req = urllib.request.Request(
        "%s/channels/%s/messages" % (DISCORD, channel_id),
        data=body,
        method="POST",
        headers={
            "Authorization": token,
            "User-Agent": UA,
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            m = json.loads(resp.read().decode() or "{}")
            if m.get("id"):
                return [m["id"]], None
            return None, "discord send failed: no message id"
    except urllib.error.HTTPError as e:
        return None, "discord send failed: HTTP %s" % e.code


def discord_get_messages(channel_id, limit):
    token = read_file("token.txt").strip()
    if not token:
        return None, "bot token not on VPS yet"
    req = urllib.request.Request(
        "%s/channels/%s/messages?limit=%d" % (DISCORD, channel_id, limit),
        headers={"Authorization": token, "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            msgs = json.loads(resp.read().decode() or "[]")
    except urllib.error.HTTPError as e:
        return None, "discord read failed: HTTP %s" % e.code
    items = [
        {
            "id": m.get("id"),
            "author": (m.get("author") or {}).get("username", ""),
            "author_id": (m.get("author") or {}).get("id", ""),
            "content": m.get("content") or "",
            "timestamp": m.get("timestamp") or "",
            "attachments": [
                {
                    "filename": a.get("filename", ""),
                    "size": a.get("size", 0),
                    "content_type": a.get("content_type", ""),
                }
                for a in (m.get("attachments") or [])
            ],
        }
        for m in (msgs if isinstance(msgs, list) else [])
    ]
    return items, None


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
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/bridge/"):
            return self._json(404, {"error": "not found"})
        if not self._authed():
            return self._json(401, {"error": "bad bridge token"})
        if path == "/api/bridge/pending":
            replied = read_replied()
            pending = [i for i in read_inbox() if i["id"] not in replied]
            return self._json(200, {"pending": len(pending)})
        if path == "/api/bridge/inbox":
            replied = read_replied()
            items = [i for i in read_inbox() if i["id"] not in replied]
            return self._json(200, {"items": items})
        if path == "/api/bridge/messages":
            qs = urllib.parse.parse_qs(parsed.query)
            channel_id = (qs.get("channel_id") or [""])[0]
            try:
                limit = int((qs.get("limit") or ["25"])[0])
            except ValueError:
                limit = 25
            if not channel_id:
                return self._json(400, {"error": "channel_id required"})
            limit = max(1, min(100, limit))
            items, err = discord_get_messages(channel_id, limit)
            if err:
                code = 503 if "token" in err else 502
                return self._json(code, {"error": err})
            return self._json(200, {"items": items})
        return self._json(404, {"error": "unknown bridge endpoint"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/bridge/"):
            return self._json(404, {"error": "not found"})
        if not self._authed():
            return self._json(401, {"error": "bad bridge token"})
        body = self._body()
        if body is None:
            return self._json(400, {"error": "invalid JSON body"})
        if path == "/api/bridge/replied":
            if not body.get("id"):
                return self._json(400, {"error": "id required"})
            replied = read_replied()
            replied[str(body["id"])] = str(body.get("reply_id") or "")
            with open(os.path.join(STATE_DIR, "replied.json"), "w", encoding="utf-8") as f:
                json.dump(replied, f, indent=2)
            return self._json(200, {"ok": True})
        if path == "/api/bridge/reply":
            channel_id = str(body.get("channel_id") or "")
            content = str(body.get("content") or "")
            if not channel_id or not content:
                return self._json(400, {"error": "channel_id and content required"})
            ids, err = discord_post(channel_id, content)
            if err:
                code = 503 if "token" in err else 502
                return self._json(code, {"error": err})
            return self._json(200, {"ok": True, "ids": ids})
        if path == "/api/bridge/reply-image":
            channel_id = str(body.get("channel_id") or "")
            content = str(body.get("content") or "")
            filename = os.path.basename(str(body.get("filename") or "image.png"))
            b64 = str(body.get("image_base64") or "")
            if not channel_id or not b64:
                return self._json(
                    400, {"error": "channel_id and image_base64 required"}
                )
            try:
                image_bytes = base64.b64decode(b64, validate=True)
            except Exception:
                return self._json(400, {"error": "invalid base64 image"})
            ids, err = discord_post_image(
                channel_id, content, image_bytes, filename
            )
            if err:
                code = 503 if "token" in err else 502
                return self._json(code, {"error": err})
            return self._json(200, {"ok": True, "ids": ids})
        return self._json(404, {"error": "unknown bridge endpoint"})


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
