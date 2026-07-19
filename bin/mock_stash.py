#!/usr/bin/env python3
"""Stash GraphQL server mock.

Listens on 127.0.0.1:9999. Responds to:
  POST /graphql  -- accepts any query, returns canned empty scenes/performers
  GET  /         -- returns 200 OK so health probes pass
"""
import argparse, json
from http.server import BaseHTTPRequestHandler, HTTPServer

CANNED_GQL = {
    "data": {
        "findScenes": {"count": 0, "scenes": []},
        "findPerformers": {"count": 0, "performers": []},
        "version": {"version": "v0.27.0", "hash": "mock"},
    }
}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b'{"ok":true}')
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", "0") or 0)
        _ = self.rfile.read(ln)  # discard query
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(CANNED_GQL).encode())
    def log_message(self, fmt, *args): pass

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9999)
    p.add_argument("--bind", default="127.0.0.1")
    a = p.parse_args()
    HTTPServer((a.bind, a.port), Handler).serve_forever()

if __name__ == "__main__":
    main()
