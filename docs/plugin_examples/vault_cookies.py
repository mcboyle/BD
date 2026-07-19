"""vault_cookies.py -- inject YOUR OWN cookies from a vault file into the live
browser context. GATED full-access example.

A LIFECYCLE plugin (Surface B). It runs after the persistent context opens and
adds cookies you control -- e.g. a session you exported yourself -- into the
live Playwright context. This is the operator using their own authenticated
session, which is exactly the in-charter "authenticated profile reuse" posture.

  *** FULL-ACCESS REQUIRED ***
This plugin declares capabilities ["lifecycle", "page_access"]. It will NOT
load unless you enable the gate:
    plugins.json:  {"allow_full_access": true}
    or env:        BD_PLUGINS_ALLOW_FULL_ACCESS=1
With the gate off, BulkDownloader skips this plugin at load and shows the reason
on the status page. Read the full-access disclaimer (status page / docs) first:
plugins run with NO sandbox and full live-browser access; what you do with that
is your responsibility, and must stay within each site's ToS, the law, and the
capture charter (no access-control bypass, no DRM, no challenge-solving).

Configure via environment:
    COOKIE_VAULT=/home/you/.bd_cookies.json
The vault file is a JSON list of Playwright cookie dicts:
    [{"name":"sess","value":"...","domain":".example.com","path":"/"}, ...]

Copy into INSTALL_DIR/plugins/, enable full-access, and Reload.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PLUGIN = {
    "name": "vault-cookies",
    "version": "1.0.0",
    "api_version": 2,
    "author": "BulkDownloader (example)",
    "capabilities": ["lifecycle", "page_access"],
    "description": "Inject operator-supplied cookies into the live context (gated)",
}

from bulk_downloader import plugins as P


def _load_cookies():
    fp = os.environ.get("COOKIE_VAULT", "").strip()
    if not fp:
        return []
    p = Path(fp)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


@P.lifecycle("after_context", name="vault-cookies", timeout=8.0)
def inject(context, page, site_id):
    """Add cookies from the vault into the live context. No-op if the vault is
    unset/empty. Raises on a real failure so the quarantine guard can act."""
    cookies = _load_cookies()
    if not cookies:
        return
    try:
        context.add_cookies(cookies)
    except Exception as e:
        raise RuntimeError(f"cookie injection failed for {site_id}: {e}")
