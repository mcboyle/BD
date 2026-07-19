"""move_to_nas.py -- copy or move each finished download to a NAS / archive dir.

A post-download PROCESSOR that relocates the completed file. Runs EARLY
(priority 50) so a media-server refresh (priority 200) sees the file at its
final home.

Self-gating: with NAS_DEST unset it does nothing.

Configure via environment:
    NAS_DEST=/mnt/nas/incoming          # destination directory (required)
    NAS_MODE=copy                       # copy (default) or move
    NAS_PER_SITE=1                      # if set, nest under NAS_DEST/<site_id>/

Copy into INSTALL_DIR/plugins/ and Reload.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

PLUGIN = {
    "name": "move-to-nas",
    "version": "1.0.0",
    "api_version": 2,
    "author": "BulkDownloader (example)",
    "capabilities": ["processor"],
    "description": "Copy/move finished downloads to a NAS or archive directory",
}

from bulk_downloader import plugins as P


@P.processor(priority=50, name="move-to-nas", timeout=120.0)
def relocate(payload):
    dest = os.environ.get("NAS_DEST", "").strip()
    src = payload.get("path", "")
    if not dest or not src:
        return None
    src_p = Path(src)
    if not src_p.is_file():
        return {"skipped": "source missing", "src": src}
    dest_dir = Path(dest)
    if os.environ.get("NAS_PER_SITE", "").strip():
        dest_dir = dest_dir / (payload.get("site_id") or "unknown")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src_p.name
    mode = os.environ.get("NAS_MODE", "copy").strip().lower()
    try:
        if mode == "move":
            shutil.move(str(src_p), str(target))
        else:
            shutil.copy2(str(src_p), str(target))
        return {"mode": mode, "dest": str(target)}
    except Exception as e:  # surfaced via the processor result + quarantine
        raise RuntimeError(f"relocate failed: {e}")
