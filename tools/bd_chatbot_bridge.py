#!/usr/bin/env python3
"""bd_chatbot_bridge.py — Discord / Slack bridge for Bulk Downloader.

Phase 79 (v3.40.0). Lightweight HTTP bridge that lets a Discord bot
framework (or Slack, or anything that posts JSON) issue commands to
Bulk Downloader. No persistent connections, no extra dependencies
beyond stdlib.

Run alongside the main Bulk Downloader process:
    python tools/bd_chatbot_bridge.py --listen 0.0.0.0:5557 \\
                                       --bd-url http://127.0.0.1:5555 \\
                                       --bd-token YOUR_TOKEN \\
                                       --bridge-secret SHARED_SECRET

Then point your Discord bot at `http://your-host:5557/cmd`. The bot
includes a `X-Bridge-Secret: ...` header to authenticate. The bridge
translates `command + args` into the right API call against Bulk
Downloader.

Supported chat commands (whatever your bot exposes as slash commands):
  /bd status                  → returns running/queued/needs_review counts
  /bd status SITE             → per-site status
  /bd retry SITE              → re-queue failed URLs for site
  /bd review                  → returns review backlog count + deep link
  /bd pause SITE              → pause site
  /bd start SITE              → start site

The response is always a plain-text string that the bot can post back
to the channel verbatim. Errors are returned with HTTP 400/500 but
also include a `message` field so the bot can render them nicely.
"""
import argparse
import json
import sys
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer


class BridgeHandler(BaseHTTPRequestHandler):
    bd_url = ""
    bd_token = ""
    bridge_secret = ""

    def log_message(self, fmt, *args):
        # Quiet by default; uncomment for debug logging
        # sys.stderr.write(("%s - " + fmt + "\n") % (self.address_string(), *args))
        pass

    def _bd_call(self, method, path, body=None, timeout=15):
        """Make an API call to the Bulk Downloader instance."""
        url = self.bd_url.rstrip("/") + path
        headers = {"Content-Type": "application/json"}
        if self.bd_token:
            headers["X-BD-Token"] = self.bd_token
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                try: return json.loads(raw)
                except Exception: return {"raw": raw}
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode("utf-8", "replace")
            except Exception: pass
            return {"error": f"HTTP {e.code}", "detail": body[:200]}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def _reply(self, status, text, extra=None):
        payload = {"message": text}
        if extra: payload.update(extra)
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Auth via shared secret. AUDIT FIX (v3.42.0): use constant-time
        # comparison so attackers can't probe the secret byte-by-byte
        # via timing. hmac.compare_digest is in stdlib (Python ≥3.3) and
        # doesn't short-circuit on the first mismatching byte.
        if self.bridge_secret:
            import hmac as _hmac
            sent = self.headers.get("X-Bridge-Secret", "") or ""
            if not _hmac.compare_digest(sent, self.bridge_secret):
                self._reply(403, "Forbidden: bad bridge secret")
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            self._reply(400, "Bad JSON")
            return

        command = (req.get("command") or "").strip().lower()
        args = req.get("args") or []

        if self.path == "/cmd":
            handler = COMMANDS.get(command)
            if not handler:
                self._reply(400, f"Unknown command: {command}",
                            extra={"supported": list(COMMANDS.keys())})
                return
            try:
                msg, extra = handler(self, args)
                self._reply(200, msg, extra=extra)
            except Exception as e:
                self._reply(500, f"Handler error: {type(e).__name__}: {e}")
            return

        self._reply(404, "Unknown path")


# ── Command implementations ──────────────────────────────────────────
def _resolve_site(handler, name_or_id):
    """Look up a site by ID or name. Returns sid or None."""
    status = handler._bd_call("GET", "/api/status")
    if not isinstance(status, dict): return None
    if name_or_id in status: return name_or_id
    for sid, data in status.items():
        cfg = (data or {}).get("config") or {}
        if (cfg.get("name") or "").lower() == name_or_id.lower():
            return sid
    return None


def cmd_status(handler, args):
    status = handler._bd_call("GET", "/api/status")
    if "error" in status: return f"API error: {status['error']}", None
    if args:
        # Single-site status
        sid = _resolve_site(handler, args[0])
        if not sid: return f"Site not found: {args[0]}", None
        s = status.get(sid, {})
        c = s.get("counts", {})
        name = s.get("config", {}).get("name", sid)
        return (f"**{name}**: {c.get('running',0)} running · {c.get('pending',0)} pending · "
                f"{c.get('done',0)} done · {c.get('failed',0)} failed · "
                f"{c.get('needs_review',0)} need review"), None
    # Aggregate
    tot = {"running":0, "pending":0, "done":0, "failed":0, "needs_review":0}
    for sid, s in status.items():
        c = (s or {}).get("counts", {})
        for k in tot: tot[k] += c.get(k, 0)
    return (f"**All sites**: {tot['running']} running · {tot['pending']} pending · "
            f"{tot['done']} done · {tot['failed']} failed · "
            f"{tot['needs_review']} need review"), None


def cmd_review(handler, args):
    status = handler._bd_call("GET", "/api/status")
    if "error" in status: return f"API error: {status['error']}", None
    review_total = sum(((s or {}).get("counts") or {}).get("needs_review", 0)
                       for s in status.values())
    if review_total == 0:
        return "No URLs need review right now.", None
    return f"{review_total} URLs need review. Open the Review panel to handle.", None


def cmd_retry(handler, args):
    if not args: return "Usage: /bd retry SITE", None
    sid = _resolve_site(handler, args[0])
    if not sid: return f"Site not found: {args[0]}", None
    r = handler._bd_call("POST", f"/api/sites/{sid}/retry")
    if "error" in r: return f"Retry failed: {r['error']}", None
    return f"Retry queued for {args[0]}.", None


def cmd_pause(handler, args):
    if not args: return "Usage: /bd pause SITE", None
    sid = _resolve_site(handler, args[0])
    if not sid: return f"Site not found: {args[0]}", None
    r = handler._bd_call("POST", f"/api/sites/{sid}/pause")
    if "error" in r: return f"Pause failed: {r['error']}", None
    return f"Paused {args[0]}.", None


def cmd_start(handler, args):
    if not args: return "Usage: /bd start SITE", None
    sid = _resolve_site(handler, args[0])
    if not sid: return f"Site not found: {args[0]}", None
    r = handler._bd_call("POST", f"/api/sites/{sid}/start")
    if "error" in r: return f"Start failed: {r['error']}", None
    return f"Started {args[0]}.", None


def cmd_help(handler, args):
    return ("Commands: status [SITE], review, retry SITE, pause SITE, "
            "start SITE, help"), None


COMMANDS = {
    "status": cmd_status,
    "review": cmd_review,
    "retry":  cmd_retry,
    "pause":  cmd_pause,
    "start":  cmd_start,
    "help":   cmd_help,
}


def main():
    p = argparse.ArgumentParser(description="Discord/Slack bridge for Bulk Downloader")
    p.add_argument("--listen", default="0.0.0.0:5557",
                   help="host:port to bind on (default 0.0.0.0:5557)")
    p.add_argument("--bd-url", default="http://127.0.0.1:5555",
                   help="Bulk Downloader API URL (default http://127.0.0.1:5555)")
    p.add_argument("--bd-token", default="",
                   help="Bulk Downloader auth token (if global auth is enabled)")
    p.add_argument("--bridge-secret", default="",
                   help="Shared secret bots must send via X-Bridge-Secret header. "
                        "Strongly recommended for non-localhost deployments.")
    args = p.parse_args()
    BridgeHandler.bd_url = args.bd_url
    BridgeHandler.bd_token = args.bd_token
    BridgeHandler.bridge_secret = args.bridge_secret
    host, _, port = args.listen.rpartition(":")
    server = HTTPServer((host or "0.0.0.0", int(port)), BridgeHandler)
    sys.stderr.write(f"Bridge listening on {host}:{port} → {args.bd_url}\n")
    if not args.bridge_secret:
        sys.stderr.write("WARNING: no --bridge-secret set; anyone reaching this port can issue commands.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
