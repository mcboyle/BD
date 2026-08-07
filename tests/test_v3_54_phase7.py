"""v3.54 (Phase 7) — regression tests for bd-doctor.

Covers doctor.py: environment_checks, dependency_checks,
cookie_freshness, diagnose_failure, run_diagnostics — plus the two
API endpoints (/api/doctor, /api/doctor/diagnose).
"""
import os
import sys
import time

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

# ── environment_checks ──────────────────────────────────────────────────

def test_environment_checks_includes_python():
    from bulk_downloader import doctor
    checks = doctor.environment_checks()
    py = next(c for c in checks if c["test"] == "python")
    # We're running on 3.9+ in the test env
    assert py["status"] == doctor.OK


def test_environment_checks_includes_ffmpeg_and_playwright():
    from bulk_downloader import doctor
    tests = {c["test"] for c in doctor.environment_checks()}
    assert "ffmpeg" in tests
    assert "ffprobe" in tests
    assert "playwright" in tests


def test_environment_check_results_have_status():
    from bulk_downloader import doctor
    for c in doctor.environment_checks():
        assert c["status"] in (doctor.OK, doctor.WARN, doctor.FAIL)
        assert "message" in c


# ── dependency_checks ───────────────────────────────────────────────────

def test_dependency_checks_covers_optional_deps():
    from bulk_downloader import doctor
    tests = {c["test"] for c in doctor.dependency_checks()}
    # A few representative optional deps
    assert "dep:curl_cffi" in tests
    assert "dep:apprise" in tests


def test_dependency_checks_report_installed_flag():
    from bulk_downloader import doctor
    for c in doctor.dependency_checks():
        det = c.get("detail", {})
        assert "installed" in det
        assert isinstance(det["installed"], bool)


def test_dependency_checks_never_fail_status():
    """Optional deps are optional — a missing one is OK-level, never
    FAIL, so a clean install doesn't show a wall of failures."""
    from bulk_downloader import doctor
    for c in doctor.dependency_checks():
        assert c["status"] != doctor.FAIL


# ── cookie_freshness ────────────────────────────────────────────────────

def test_cookie_freshness_skips_sites_without_cookie_file():
    from bulk_downloader import doctor
    cfg = {"s1": {"name": "NoCookie"}}  # no cookie_file
    checks = doctor.cookie_freshness(cfg)
    assert checks == []


def test_cookie_freshness_flags_missing_file():
    from bulk_downloader import doctor
    cfg = {"s1": {"name": "GhostCookie",
                  "cookie_file": "/nonexistent/path/cookies.json"}}
    checks = doctor.cookie_freshness(cfg)
    assert len(checks) == 1
    assert checks[0]["status"] == doctor.WARN
    assert "missing" in checks[0]["message"]


def test_cookie_freshness_fresh_cookie_is_ok(tmp_path):
    from bulk_downloader import doctor
    cookie = tmp_path / "fresh.json"
    cookie.write_text("[]")
    cfg = {"s1": {"name": "FreshSite", "cookie_file": str(cookie)}}
    checks = doctor.cookie_freshness(cfg)
    assert len(checks) == 1
    assert checks[0]["status"] == doctor.OK


def test_cookie_freshness_stale_cookie_warns(tmp_path):
    from bulk_downloader import doctor
    cookie = tmp_path / "stale.json"
    cookie.write_text("[]")
    # Backdate the mtime 30 days
    old = time.time() - 30 * 86400
    os.utime(str(cookie), (old, old))
    cfg = {"s1": {"name": "StaleSite", "cookie_file": str(cookie)}}
    checks = doctor.cookie_freshness(cfg, warn_age_days=14.0)
    assert len(checks) == 1
    assert checks[0]["status"] == doctor.WARN
    assert "old" in checks[0]["message"]


# ── diagnose_failure ────────────────────────────────────────────────────

def test_diagnose_failure_classifies():
    """The runner only expands @parametrize on class methods, so we
    loop the cases inline."""
    from bulk_downloader import doctor
    cases = [
        ("HTTP 429 Too Many Requests", "Rate limiting"),
        ("rate limit exceeded, slow down", "Rate limiting"),
        ("Turnstile captcha challenge detected", "Captcha challenge"),
        ("hCaptcha verification required", "Captcha challenge"),
        ("session expired, please sign in",
         "Authentication / session expiry"),
        ("HTTP 403 Forbidden", "Authentication / session expiry"),
        ("waiting for selector .btn timed out", "Selector drift"),
        ("no such element: download button", "Selector drift"),
        ("ENOSPC: no space left on device", "Disk pressure"),
        ("connection reset by peer", "Network / connectivity"),
        ("DNS resolution failed", "Network / connectivity"),
        ("HTTP 404 Not Found", "Content removed"),
    ]
    for error, expected_cause in cases:
        d = doctor.diagnose_failure(error)
        assert d["matched"] is True, f"{error!r} did not match"
        assert d["cause"] == expected_cause, (
            f"{error!r}: expected {expected_cause}, got {d['cause']}")
        assert d["suggestion"]
        assert d["confidence"] in ("high", "medium", "low")


def test_diagnose_failure_unrecognized():
    from bulk_downloader import doctor
    d = doctor.diagnose_failure("some totally novel error blah blah")
    assert d["matched"] is False
    assert d["confidence"] == "none"
    # Still gives a generic next step rather than nothing
    assert d["suggestion"]


def test_diagnose_failure_empty_input():
    from bulk_downloader import doctor
    d = doctor.diagnose_failure("")
    assert d["matched"] is False
    d2 = doctor.diagnose_failure(None)
    assert d2["matched"] is False


def test_diagnose_selector_beats_network_on_overlap():
    """'waiting for selector ... timed out' contains 'timed out' (a
    network signature) but should classify as selector drift — the
    more specific signature comes first in the table."""
    from bulk_downloader import doctor
    d = doctor.diagnose_failure("waiting for selector .x timed out")
    assert d["cause"] == "Selector drift"


# ── run_diagnostics ─────────────────────────────────────────────────────

def test_run_diagnostics_shape():
    from bulk_downloader import doctor
    rep = doctor.run_diagnostics(sites_config={})
    assert "ok" in rep
    assert "checks" in rep
    assert "summary" in rep
    assert "elapsed_ms" in rep
    assert isinstance(rep["checks"], list)


def test_run_diagnostics_summary_counts_match():
    from bulk_downloader import doctor
    rep = doctor.run_diagnostics(sites_config={})
    s = rep["summary"]
    assert s["ok"] + s["warn"] + s["fail"] == len(rep["checks"])


def test_run_diagnostics_ok_when_no_fails():
    from bulk_downloader import doctor
    rep = doctor.run_diagnostics(sites_config={})
    assert rep["ok"] == (rep["summary"]["fail"] == 0)


# ── API endpoints ───────────────────────────────────────────────────────

def test_api_doctor_endpoint():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/api/doctor")
    body = r.get_json()
    assert body["ok"] is True
    assert "report" in body
    assert "checks" in body["report"]


def test_api_doctor_diagnose_with_error_text():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/doctor/diagnose",
               json={"error": "HTTP 429 rate limit"})
    body = r.get_json()
    assert body["ok"] is True
    assert body["diagnosis"]["cause"] == "Rate limiting"


def test_api_doctor_diagnose_by_history_id():
    """Passing a history id should look up the message text."""
    from bulk_downloader import app as a
    from bulk_downloader.db import db_log, db_conn
    db_log("siteA", "Site A", "https://x.com/v", "failed",
           "", 0, "captcha challenge detected")
    with db_conn() as cx:
        hid = cx.execute(
            "SELECT id FROM history ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
    c = a.app.test_client()
    r = c.post("/api/doctor/diagnose", json={"hid": hid})
    body = r.get_json()
    assert body["ok"] is True
    assert body["diagnosis"]["cause"] == "Captcha challenge"
