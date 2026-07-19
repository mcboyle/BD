#!/usr/bin/env python3
"""Fake webhook receiver for capturing BulkDL notification traffic.

Listens on 127.0.0.1:8765 by default. Every POST/GET is captured to
/tmp/apprise_capture/<ts>_<method>_<path>.json with headers + body.

Usage:
    python3 fake_webhook_server.py [--port 8765] [--capture-dir /tmp/cap]

Point BulkDL's Apprise config at http://localhost:8765/anywhere and
inspect the capture dir to see what was sent.
"""
import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

class CaptureHandler(BaseHTTPRequestHandler):
    capture_dir = "/tmp/apprise_capture"

    def _record(self, method):
        os.makedirs(self.capture_dir, exist_ok=True)
        body = b""
        ln = int(self.headers.get("Content-Length", "0") or 0)
        if ln:
            body = self.rfile.read(ln)
        ts = time.strftime("%Y%m%d-%H%M%S")
        safe_path = self.path.replace("/", "_") or "_root"
        fn = f"{self.capture_dir}/{ts}_{method}_{safe_path}.json"
        rec = {
            "ts": time.time(),
            "method": method,
            "path": self.path,
            "headers": dict(self.headers),
            "body_str": body.decode("utf-8", "replace"),
        }
        try:
            rec["body_json"] = json.loads(body)
        except Exception:
            pass
        with open(fn, "w") as f:
            json.dump(rec, f, indent=2)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_POST(self): self._record("POST")
    def do_GET(self):  self._record("GET")
    def do_PUT(self):  self._record("PUT")

    def log_message(self, fmt, *args):
        pass  # quiet

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--capture-dir", default="/tmp/apprise_capture")
    p.add_argument("--bind", default="127.0.0.1")
    args = p.parse_args()
    CaptureHandler.capture_dir = args.capture_dir
    os.makedirs(args.capture_dir, exist_ok=True)
    srv = HTTPServer((args.bind, args.port), CaptureHandler)
    print(f"Fake webhook receiver on http://{args.bind}:{args.port}/")
    print(f"Captures to: {args.capture_dir}")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()
