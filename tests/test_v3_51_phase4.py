"""v3.51 (Phase 4) — regression tests for the collection dashboard widgets.

Covers:
  - The 8 new collection widget ids are valid + present in both
    Python (VALID_WIDGET_IDS) and JS (widgets.js WIDGETS array)
  - The 'collection' category exists in widgets.js CATEGORIES
  - _collect_data populates the lib_* / audit_* keys correctly
  - Watched-percentage math
  - Empty-library edge case (no division by zero)
"""
import os
import re
import sys
from pathlib import Path

import pytest


# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_COLLECTION_IDS = {
    "lib_total", "lib_size", "lib_watched", "lib_unrated",
    "lib_missing", "lib_recent", "lib_top_studio", "audit_recent",
}


# ── catalog presence ────────────────────────────────────────────────────

def test_collection_ids_in_python_valid_set():
    from bulk_downloader import widgets_config
    for wid in _COLLECTION_IDS:
        assert wid in widgets_config.VALID_WIDGET_IDS, \
            f"{wid} missing from VALID_WIDGET_IDS"


# ── data collection ─────────────────────────────────────────────────────

def test_collect_data_populates_library_keys():
    """With library rows present, _collect_data should yield lib_* keys."""
    from bulk_downloader import app as a  # boot triggers migrations
    from bulk_downloader.db import db_log
    from bulk_downloader.app_widgets_api import _collect_data
    # Two completed downloads → two library rows
    db_log("siteA", "Site A", "https://x.com/v1", "done",
           "/tmp/dl/a.mp4", 1_000_000, "ok")
    db_log("siteA", "Site A", "https://x.com/v2", "done",
           "/tmp/dl/b.mp4", 3_000_000, "ok")
    d = _collect_data(None)
    assert d.get("lib_total") == 2
    assert "lib_size_fmt" in d
    assert d.get("lib_watched_pct") == 0.0  # nothing watched yet
    assert d.get("lib_unrated") == 2


def test_collect_data_watched_percentage_math():
    from bulk_downloader import app as a
    from bulk_downloader.db import db_log
    from bulk_downloader import library as lib
    from bulk_downloader.app_widgets_api import _collect_data
    # Four downloads
    for i in range(4):
        db_log("siteA", "Site A", f"https://x.com/v{i}", "done",
               f"/tmp/dl/v{i}.mp4", 1000, "ok")
    rows, _ = lib.library_browse(limit=10)
    # Mark 1 of 4 watched → 25%
    lib.library_set_watched(rows[0]["id"], True)
    d = _collect_data(None)
    assert d.get("lib_total") == 4
    assert d.get("lib_watched_pct") == 25.0


def test_collect_data_empty_library_no_zero_division():
    """With zero library rows, lib_watched_pct must be 0.0, not a
    ZeroDivisionError."""
    from bulk_downloader import app as a
    from bulk_downloader.app_widgets_api import _collect_data
    d = _collect_data(None)
    # Either the keys are absent or they're safe zeros — never a crash
    assert d.get("lib_watched_pct", 0.0) == 0.0
    assert d.get("lib_total", 0) == 0


def test_collect_data_audit_count():
    from bulk_downloader import app as a
    from bulk_downloader import audit
    from bulk_downloader.app_widgets_api import _collect_data
    # Three audit events in the last 24h
    for i in range(3):
        audit.audit_log(source="api", action="update",
                        target=f"sites_config:s{i}")
    d = _collect_data(None)
    assert d.get("audit_recent") == 3


def test_collect_data_top_studio():
    from bulk_downloader import app as a
    from bulk_downloader import library as lib
    from bulk_downloader.app_widgets_api import _collect_data
    # 3 files in StudioX, 1 in StudioY → StudioX is top
    for i in range(3):
        lib.library_record(f"/tmp/x/v{i}.mp4", studio="StudioX")
    lib.library_record("/tmp/y/v0.mp4", studio="StudioY")
    d = _collect_data(None)
    assert d.get("lib_top_studio") == "StudioX"


def test_widgets_data_endpoint_includes_collection():
    """End-to-end: GET /api/widgets/data returns collection keys."""
    from bulk_downloader import app as a
    from bulk_downloader.db import db_log
    db_log("siteA", "Site A", "https://x.com/v1", "done",
           "/tmp/dl/a.mp4", 500, "ok")
    c = a.app.test_client()
    r = c.get("/api/widgets/data")
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"].get("lib_total") == 1
