#!/usr/bin/env python3
"""VPS-local Discord poller for Bot-Muse.

Runs entirely on the VPS: scans configured channels + threads, queues new
@-mentions to inbox.jsonl, advances watermarks.json, acks each new @, and
captures the ~25 messages before each @ as context so the reply worker on
the Muse side never has to call Discord itself.

State dir:  $BRIDGE_STATE_DIR (default ~/discord-bridge)
Token file: $BRIDGE_STATE_DIR/token.txt  (one line: "Bot <token>", chmod 600)
Trial mode: TRIAL=1 disables ack posts.

Last printed line is JSON: {"new": N, "secs": S, "calls": C}
"""
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://discord.com/api/v10"
STATE_DIR = os.path.expanduser(os.environ.get("BRIDGE_STATE_DIR", "~/discord-bridge"))
TRIAL = os.environ.get("TRIAL") == "1"
ACK_TEXT = "收到，正在处理"
UA = "DiscordBot (bot-muse-bridge, 1.0)"

calls = 0
_conn = None


def load_token():
    path = os.path.join(STATE_DIR, "token.txt")
    if not os.path.exists(path):
        path = os.path.expanduser("~/discord-bridge/token.txt")
    if not os.path.exists(path):
        print(json.dumps({"error": "token file missing"}))
        sys.exit(2)
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


TOKEN = None  # set in main


def api(method, path, body=None):
    global calls, _conn
    for attempt in range(3):
        calls += 1
        data = json.dumps(body) if body is not None else None
        try:
            if _conn is None:
                _conn = http.client.HTTPSConnection("discord.com", timeout=20)
            _conn.request(method, "/api/v10" + path, body=data, headers={
                "Authorization": TOKEN,
                "User-Agent": UA,
                "Content-Type": "application/json",
            })
            resp = _conn.getresponse()
            raw = resp.read().decode()
            if resp.status == 429:
                try:
                    retry = float(json.loads(raw or "{}").get("retry_after", 5))
                except Exception:
                    retry = 5
                time.sleep(retry + 0.5)
                continue
            if resp.status >= 400:
                raise RuntimeError("Discord %s %s -> %s" % (method, path, resp.status))
            return json.loads(raw) if raw else {}
        except (http.client.HTTPException, ConnectionError, OSError):
            _conn = None
            if attempt == 2:
                raise
            time.sleep(0.5)
    raise RuntimeError("Discord %s %s rate-limited after retries" % (method, path))


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def get_messages(cid, after=None, before=None, limit=50):
    q = "?limit=%d" % max(1, min(int(limit), 100))
    if after:
        q += "&after=%s" % after
    if before:
        q += "&before=%s" % before
    out = api("GET", "/channels/%s/messages%s" % (cid, q))
    return out if isinstance(out, list) else []


def guild_active_threads(any_cid):
    ch = api("GET", "/channels/%s" % any_cid)
    gid = ch.get("guild_id")
    if not gid:
        return []
    out = api("GET", "/guilds/%s/threads/active" % gid)
    return out.get("threads", []) if isinstance(out, dict) else []


def channel_archived_threads(cid):
    out_all = []
    for kind in ("public", "private"):
        try:
            out = api("GET", "/channels/%s/threads/archived/%s" % (cid, kind))
            if isinstance(out, dict):
                out_all.extend(out.get("threads", []))
        except Exception:
            pass
    return out_all


def send_message(cid, content):
    ids = []
    for i in range(0, len(content), 1900):
        m = api("POST", "/channels/%s/messages" % cid, {"content": content[i : i + 1900]})
        if isinstance(m, dict) and m.get("id"):
            ids.append(m["id"])
        time.sleep(0.5)
    return ids


def fmt_context(msgs, before_id):
    """msgs are newest-first from Discord; return oldest-first text lines."""
    lines = []
    for m in reversed(msgs):
        ts = (m.get("timestamp") or "")[11:16]
        author = m.get("author", {})
        name = author.get("global_name") or author.get("username", "?")
        if author.get("bot"):
            name += " [bot]"
        text = (m.get("content") or "").replace("\n", " ")[:500]
        if not text and m.get("attachments"):
            text = "[附件 x%d]" % len(m["attachments"])
        lines.append("[%s] %s: %s" % (ts, name, text))
    return lines


def main():
    global TOKEN
    t0 = time.time()
    TOKEN = load_token()
    cfg = load_json(os.path.join(STATE_DIR, "config.json"), {})
    bot_id = cfg.get("bot_user_id")
    if not bot_id:
        bot_id = api("GET", "/users/@me")["id"]
    accepted_user_ids = {bot_id} | set(cfg.get("legacy_user_ids", []))
    mention_role_ids = set(cfg.get("mention_role_ids", []))

    wm_path = os.path.join(STATE_DIR, "watermarks.json")
    wm = load_json(wm_path, {})
    inbox_path = os.path.join(STATE_DIR, "inbox.jsonl")
    seen = set()
    if os.path.exists(inbox_path):
        with open(inbox_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        seen.add(json.loads(line)["id"])
                    except Exception:
                        pass

    eff_channels = dict(cfg.get("channels", {}))
    parent_names = {pid: p.get("name", pid) for pid, p in cfg.get("channels", {}).items()}

    def add_thread(t):
        tid = t.get("id")
        if not tid or tid in eff_channels:
            return
        pid = t.get("parent_id")
        pname = parent_names.get(pid)
        eff_channels[tid] = {
            "mode": "mention",
            "name": ("%s / %s" % (pname, t.get("name", tid))) if pname else t.get("name", tid),
            "thread": True,
            "parent_id": pid,
        }

    thread_parent_ids = set(cfg.get("channels", {}))
    covered_parents = set()
    for cid in cfg.get("channels", {}):
        if cid in covered_parents:
            continue
        covered_parents.add(cid)
        try:
            threads = guild_active_threads(cid)
        except Exception as e:
            print(json.dumps({"warning": "active thread discovery failed: %s" % e}))
            continue
        for t in threads:
            if t.get("parent_id"):
                thread_parent_ids.add(t["parent_id"])
                covered_parents.add(t["parent_id"])
            add_thread(t)
    for pid in sorted(thread_parent_ids):
        for t in channel_archived_threads(pid):
            add_thread(t)

    new_thread_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    queued = []
    for ch_id, ch in eff_channels.items():
        try:
            msgs = get_messages(ch_id, after=wm.get(ch_id), limit=50)
        except Exception as e:
            print(json.dumps({"warning": str(e), "channel": ch_id}))
            continue
        if not msgs:
            continue
        is_new = ch_id not in wm
        if is_new and not ch.get("thread"):
            wm[ch_id] = msgs[0]["id"]
            continue
        mode = ch.get("mode", "mention")
        for m in reversed(msgs):
            mid = m["id"]
            author = m.get("author", {})
            if author.get("bot"):
                wm[ch_id] = mid
                continue
            if mid in seen:
                wm[ch_id] = mid
                continue
            mentioned = any(u.get("id") in accepted_user_ids for u in m.get("mentions", []))
            mentioned = mentioned or any(
                r in mention_role_ids for r in m.get("mention_roles", [])
            )
            if mode == "mention" and not mentioned:
                wm[ch_id] = mid
                continue
            if is_new and ch.get("thread"):
                try:
                    mts = datetime.fromisoformat((m.get("timestamp") or "").replace("Z", "+00:00"))
                except ValueError:
                    mts = None
                if mts is not None and mts < new_thread_cutoff:
                    wm[ch_id] = mid
                    continue
            item = {
                "id": mid,
                "channel_id": ch_id,
                "channel_name": ch.get("name", ch_id),
                "author": author.get("global_name") or author.get("username", "?"),
                "author_id": author.get("id"),
                "content": m.get("content", ""),
                "attachments": [a.get("url") for a in m.get("attachments", [])],
                "ts": m.get("timestamp"),
            }
            try:
                ctx = get_messages(ch_id, before=mid, limit=25)
                item["context"] = fmt_context(ctx, mid)
            except Exception as e:
                item["context"] = []
                item["context_warning"] = str(e)
            queued.append(item)
            seen.add(mid)
            wm[ch_id] = mid

    if queued:
        with open(inbox_path, "a", encoding="utf-8") as f:
            for item in queued:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
    with open(wm_path, "w", encoding="utf-8") as f:
        json.dump(wm, f, indent=2)

    if queued and not TRIAL:
        acked_path = os.path.join(STATE_DIR, "acked.json")
        acked = load_json(acked_path, {})
        dirty = False
        for item in queued:
            if item["id"] in acked:
                continue
            try:
                ids = send_message(item["channel_id"], ACK_TEXT)
                if ids:
                    acked[item["id"]] = ids[0]
                    dirty = True
            except Exception as e:
                print(json.dumps({"warning": "ack failed: %s" % e}))
        if dirty:
            with open(acked_path, "w", encoding="utf-8") as f:
                json.dump(acked, f, indent=2)

    print(json.dumps({
        "new": len(queued),
        "secs": round(time.time() - t0, 2),
        "calls": calls,
        "channels": len(eff_channels),
        "trial": TRIAL,
    }))


if __name__ == "__main__":
    main()
