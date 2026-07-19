#!/usr/bin/env python3
"""Jellyfin server mock.

Listens on 127.0.0.1:8096. Responds to:
  GET /System/Info    -- minimal system info
  GET /Library/MediaFolders -- empty library list
"""
import argparse, json
from http.server import BaseHTTPRequestHandler, HTTPServer

SYSTEM_INFO = {
    "ServerName": "mock-jellyfin", "Version": "10.9.0",
    "Id": "mock-jellyfin-id", "OperatingSystem": "Linux",
}
MEDIA_FOLDERS = {"Items": [], "TotalRecordCount": 0}

ROUTES = {
    "/System/Info": SYSTEM_INFO,
    "/Library/MediaFolders": MEDIA_FOLDERS,
}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ROUTES:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(ROUTES[path]).encode())
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, fmt, *args): pass

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8096)
    p.add_argument("--bind", default="127.0.0.1")
    a = p.parse_args()
    HTTPServer((a.bind, a.port), Handler).serve_forever()

if __name__ == "__main__":
    main()
