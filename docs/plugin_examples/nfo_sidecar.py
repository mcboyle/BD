"""nfo_sidecar.py -- write a Kodi/Jellyfin .nfo sidecar next to each download.

A post-download PROCESSOR that emits a small XML sidecar describing the video,
which Kodi/Jellyfin/Emby read for title + source. Complements BD's embedded
MP4 tagging for containers/servers that prefer sidecars.

Self-gating: with NFO_SIDECAR unset it does nothing (off by default so it does
not litter your library unless you want it).

Configure via environment:
    NFO_SIDECAR=1     # enable

Copy into INSTALL_DIR/plugins/ and Reload.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from xml.sax.saxutils import escape

PLUGIN = {
    "name": "nfo-sidecar",
    "version": "1.0.0",
    "api_version": 2,
    "author": "BulkDownloader (example)",
    "capabilities": ["processor"],
    "description": "Write a .nfo metadata sidecar beside each finished file",
}

from bulk_downloader import plugins as P


@P.processor(priority=150, name="nfo-sidecar", timeout=10.0)
def write_nfo(payload):
    if not os.environ.get("NFO_SIDECAR", "").strip():
        return None
    src = payload.get("path", "")
    if not src:
        return None
    src_p = Path(src)
    if not src_p.is_file():
        return {"skipped": "source missing"}
    title = src_p.stem
    nfo = src_p.with_suffix(".nfo")
    body = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<movie>\n"
        f"  <title>{escape(title)}</title>\n"
        f"  <studio>{escape(str(payload.get('site_id') or ''))}</studio>\n"
        f"  <source>{escape(str(payload.get('url') or ''))}</source>\n"
        f"  <dateadded>{time.strftime('%Y-%m-%d %H:%M:%S')}</dateadded>\n"
        "</movie>\n"
    )
    try:
        nfo.write_text(body, "utf-8")
        return {"nfo": str(nfo)}
    except Exception as e:
        raise RuntimeError(f"nfo write failed: {e}")
