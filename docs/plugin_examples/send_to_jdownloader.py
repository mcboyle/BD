"""send_to_jdownloader.py -- hand each finished download's source URL to
JDownloader 2 via the existing JD bridge.

A post-download PROCESSOR that submits the download's URL (with the captured
session cookies, when present) to a running JDownloader 2 instance through its
Remote API -- the same bridge BD already uses for its `backend: jd` download
mode (bulk_downloader/jd_bridge.py). Use this when you want BD to teach/capture
in its own browser but offload the actual fetch to JD's link-grabber.

Self-gating: with JD_SEND unset it does nothing. Dormant by default.

Configure via environment:
    JD_SEND=1                       # enable (required)
    JD_HOST=127.0.0.1               # JD Remote API host (default 127.0.0.1)
    JD_PORT=3128                    # JD Remote API port (default 3128)
    JD_DEST=/downloads/bd           # optional destination dir hint for JD

Copy into INSTALL_DIR/plugins/ and Reload. Needs a reachable JD2 with the
"Deprecated/Remote API" enabled -- the same prerequisite as `backend: jd`.
"""
from __future__ import annotations

import os

PLUGIN = {
    "name": "send-to-jdownloader",
    "version": "1.0.0",
    "api_version": 2,
    "author": "BulkDownloader (example)",
    "capabilities": ["processor"],
    "description": "Submit finished-download URLs to JDownloader 2 via the JD bridge",
}

from bulk_downloader import plugins as P


@P.processor(priority=150, name="send-to-jdownloader", timeout=60.0)
def send_to_jd(payload):
    if not os.environ.get("JD_SEND", "").strip():
        return None
    url = (payload.get("url") or "").strip()
    if not url:
        return {"skipped": "no url in payload"}

    # Reuse the shipped JD bridge so behavior matches `backend: jd` exactly.
    from bulk_downloader import jd_bridge

    cfg = {
        "jd_host": os.environ.get("JD_HOST", "").strip() or "127.0.0.1",
        "jd_port": os.environ.get("JD_PORT", "").strip() or 3128,
    }
    client = jd_bridge.get_client_for_site(cfg)

    cookies_jd = ""
    cookies = payload.get("cookies")
    if cookies:
        try:
            cookies_jd = jd_bridge.cookies_playwright_to_jd(cookies)
        except Exception:  # cookies are best-effort
            cookies_jd = ""

    dest = os.environ.get("JD_DEST", "").strip()
    try:
        link_id = client.submit(url, cookies=cookies_jd, dest_dir=dest)
        return {"submitted": True, "link_id": link_id, "url": url}
    except Exception as e:  # surfaced via processor result + quarantine
        kind = ""
        try:
            kind = jd_bridge.classify_jd_error(str(e))
        except Exception:
            pass
        raise RuntimeError(f"JD submit failed ({kind or 'error'}): {e}")
