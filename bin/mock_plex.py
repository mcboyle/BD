#!/usr/bin/env python3
"""Plex Media Server mock.

Listens on 127.0.0.1:32400 (Plex default port). Responds to:
  GET /identity        -- server identity XML
  GET /library/sections -- library list XML
  GET /status/sessions -- empty sessions list

Override responses via --responses-dir.
"""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer

IDENTITY = '''<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="0" claimed="1" machineIdentifier="mock-plex-machine-id"
 version="1.40.0.0"/>'''
SECTIONS = '''<?xml version="1.0" encoding="UTF-8"?>
<MediaContainer size="1">
  <Directory key="1" type="movie" title="Movies" agent="tv.plex.agents.movie"
   scanner="Plex Movie" language="en" uuid="mock-section-uuid"/>
</MediaContainer>'''
SESSIONS = '<MediaContainer size="0"/>'

ROUTES = {
    "/identity": ("application/xml", IDENTITY),
    "/library/sections": ("application/xml", SECTIONS),
    "/status/sessions": ("application/xml", SESSIONS),
}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ROUTES:
            ct, body = ROUTES[path]
            self.send_response(200); self.send_header("Content-Type", ct); self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, fmt, *args): pass

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=32400)
    p.add_argument("--bind", default="127.0.0.1")
    a = p.parse_args()
    HTTPServer((a.bind, a.port), Handler).serve_forever()

if __name__ == "__main__":
    main()
