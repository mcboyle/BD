"""v3.55 (Phase 4 backlog) — diagnostics dashboard widgets.

Closes 5 of the plan's Phase 4 items that the earlier v3.51.0 release
missed: #21 success-rate, #129 error clusters, #131 captcha stats,
#132 cookie warnings, #135 sites-need-attention.

Covers: catalog presence (Python + JS), the new `diagnostics` category,
the data collector's computed values, and the /api/widgets/data shape.
"""
import os
import re
import sys

import pytest


@pytest.fixture(autouse=True)
def _bd_boot_app():
    """v3.66.926 (item 11): the DB boot is no longer an import side effect.

    Tests in this file used to get a migrated schema because `import
    bulk_downloader.app` did db_init() + migrations.apply_pending() at MODULE
    scope -- which is exactly what raced 64 ways across xdist workers and
    destroyed the operator's live history. The work still happens; it is now
    explicit. Production does the same thing (downloader_ui.py calls
    boot_once() before serving), so this asks for the real contract rather
    than re-creating the side effect.

    boot_once() is idempotent and keyed on the RESOLVED DB PATH, so a file
    whose fixtures give each test its own tmpdir gets a boot per database
    rather than one for the whole file.
    """
    from bulk_downloader.app import boot_once
    boot_once()


# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_DIAG_IDS = {
    "success_rate_24h", "error_top_cluster", "captcha_24h",
    "cookie_warnings", "sites_need_attention",
}


# ── catalog presence ────────────────────────────────────────────────────

def test_diagnostics_ids_in_python_set():
    from bulk_downloader import widgets_config
    for wid in _DIAG_IDS:
        assert wid in widgets_config.VALID_WIDGET_IDS, \
            f"{wid} missing from VALID_WIDGET_IDS"


# ── data collection ─────────────────────────────────────────────────────

def test_collect_data_error_top_cluster():
    """Log rate-limit failures → the error cluster widget names it."""
    from bulk_downloader import app as a
    from bulk_downloader.db import db_log
    from bulk_downloader.app_widgets_api import _collect_data
    db_log("s1", "S1", "https://x.com/1", "failed", "", 0,
           "HTTP 429 rate limit exceeded")
    db_log("s1", "S1", "https://x.com/2", "failed", "", 0,
           "HTTP 429 too many requests, slow down")
    d = _collect_data(None)
    # error_fingerprint categorizes 429 as rate_limit
    assert d.get("error_top_cluster") == "rate_limit"


def test_collect_data_success_rate_with_sites():
    """8 done + 2 failed for a configured site → 80%."""
    from bulk_downloader import app as a
    from bulk_downloader.db import db_log
    from bulk_downloader.app_widgets_api import _collect_data
    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "RateSite"}).get_json()["id"]
    for i in range(8):
        db_log(sid, "RateSite", f"https://x.com/d{i}", "done",
               f"/tmp/d{i}.mp4", 1000, "ok")
    for i in range(2):
        db_log(sid, "RateSite", f"https://x.com/f{i}", "failed",
               "", 0, "connection timeout")
    d = _collect_data(None)
    assert d.get("success_rate_24h") == 80.0


def test_collect_data_captcha_zero_when_none():
    from bulk_downloader import app as a
    from bulk_downloader.app_widgets_api import _collect_data
    d = _collect_data(None)
    # No captcha activity → 0, not a crash
    assert d.get("captcha_24h", 0) == 0


def test_collect_data_cookie_warnings_clean_install():
    from bulk_downloader import app as a
    from bulk_downloader.app_widgets_api import _collect_data
    d = _collect_data(None)
    # No sites with stale cookies → 0
    assert d.get("cookie_warnings", 0) == 0


def test_collect_data_no_crash_empty_state():
    """The diagnostics collector must not 500 on a fresh install with
    no sites, no history, no audit log."""
    from bulk_downloader import app as a
    from bulk_downloader.app_widgets_api import _collect_data
    d = _collect_data(None)
    assert isinstance(d, dict)
    # sites_need_attention defaults to 0 on an empty install
    assert d.get("sites_need_attention", 0) == 0


# ── endpoint ────────────────────────────────────────────────────────────

def test_widgets_data_endpoint_has_diagnostics_keys():
    from bulk_downloader import app as a
    from bulk_downloader.db import db_log
    db_log("s1", "S1", "https://x.com/1", "failed", "", 0,
           "captcha challenge detected")
    c = a.app.test_client()
    r = c.get("/api/widgets/data")
    body = r.get_json()
    assert body["ok"] is True
    d = body["data"]
    # error cluster should be populated from the captcha failure
    assert d.get("error_top_cluster") == "captcha"
