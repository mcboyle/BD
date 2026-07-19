"""media_server_refresh.py -- refresh Plex / Jellyfin / Stash after a download.

A post-download PROCESSOR: when a download finishes, tell your media server to
scan its library so the new file shows up without waiting for a periodic scan.

This is the highest-value plugin for a self-hosted media workflow. It is
self-gating: with no server URL/token configured (env vars below), it does
nothing, so it is safe to enable unconfigured.

Configure via environment (set whichever servers you run):
    PLEX_URL=http://10.0.70.20:32400   PLEX_TOKEN=xxxx   PLEX_SECTION=2
    JELLYFIN_URL=http://10.0.70.20:8096 JELLYFIN_TOKEN=xxxx
    STASH_URL=http://10.0.70.20:9999   STASH_APIKEY=xxxx   (optional)

Copy this file into INSTALL_DIR/plugins/ (and enable it in plugins.json if you
use an enable-list), then hit Reload on the Maintenance page.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

PLUGIN = {
    "name": "media-server-refresh",
    "version": "1.0.0",
    "api_version": 2,
    "author": "BulkDownloader (example)",
    "capabilities": ["processor"],
    "description": "Trigger a Plex/Jellyfin/Stash library scan on download.done",
}

from bulk_downloader import plugins as P


def _post(url, data=None, headers=None, timeout=8.0):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def _refresh_plex():
    base = os.environ.get("PLEX_URL", "").rstrip("/")
    token = os.environ.get("PLEX_TOKEN", "")
    section = os.environ.get("PLEX_SECTION", "")
    if not (base and token and section):
        return None
    # Plex: GET /library/sections/{id}/refresh?X-Plex-Token=...
    url = f"{base}/library/sections/{section}/refresh?X-Plex-Token={token}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=8.0) as r:
            return ("plex", r.status)
    except Exception:
        return ("plex", None)


def _refresh_jellyfin():
    base = os.environ.get("JELLYFIN_URL", "").rstrip("/")
    token = os.environ.get("JELLYFIN_TOKEN", "")
    if not (base and token):
        return None
    # Jellyfin: POST /Library/Refresh with token header
    code = _post(f"{base}/Library/Refresh",
                 headers={"X-Emby-Token": token, "Content-Length": "0"})
    return ("jellyfin", code)


def _refresh_stash():
    base = os.environ.get("STASH_URL", "").rstrip("/")
    if not base:
        return None
    apikey = os.environ.get("STASH_APIKEY", "")
    headers = {"Content-Type": "application/json"}
    if apikey:
        headers["ApiKey"] = apikey
    # Stash GraphQL: trigger a metadata scan
    body = json.dumps({"query": "mutation { metadataScan(input: {}) }"}).encode()
    code = _post(f"{base}/graphql", data=body, headers=headers)
    return ("stash", code)


@P.processor(priority=200, name="media-server-refresh", timeout=10.0)
def refresh(payload):
    """Fire library scans on whichever servers are configured. Runs late
    (priority 200) so file-moving processors finish first."""
    results = {}
    for fn in (_refresh_plex, _refresh_jellyfin, _refresh_stash):
        out = fn()
        if out:
            results[out[0]] = out[1]
    return {"refreshed": results} if results else None
