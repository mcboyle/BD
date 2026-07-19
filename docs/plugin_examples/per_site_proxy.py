"""per_site_proxy.py -- route specific sites through specific proxies.

A CONFIG PROVIDER (Surface A): layers a per-site `proxy` launch knob onto the
browser launch for sites you list. Useful to send one site through your own
VPN/proxy while the rest go direct.

This does NOT bypass anything -- it just selects which of YOUR own egress paths
a given site uses. Self-gating: sites not in the map are returned unchanged.

Edit SITE_PROXIES below (or set SITE_PROXIES_JSON env to a JSON object
{"site_id": "http://host:port", ...}).

Copy into INSTALL_DIR/plugins/ and Reload.
"""
from __future__ import annotations

import json
import os

PLUGIN = {
    "name": "per-site-proxy",
    "version": "1.0.0",
    "api_version": 2,
    "author": "BulkDownloader (example)",
    "capabilities": ["config"],
    "description": "Apply a per-site proxy at browser launch",
}

from bulk_downloader import plugins as P

# site_id -> proxy URL. Playwright expects {"server": "http://host:port"}.
SITE_PROXIES = {
    # "example-site": "http://127.0.0.1:8888",
}


def _proxies():
    env = os.environ.get("SITE_PROXIES_JSON", "").strip()
    if env:
        try:
            return {**SITE_PROXIES, **json.loads(env)}
        except Exception:
            pass
    return SITE_PROXIES


@P.config_provider(priority=100, name="per-site-proxy")
def proxy_for(site_id, cfg):
    proxies = _proxies()
    server = proxies.get(site_id)
    if not server:
        return {}
    return {"proxy": {"server": server}}
