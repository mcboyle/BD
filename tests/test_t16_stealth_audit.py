"""T16 (Tier 3) — D-50 UA / stealth audit. Single-item, closes
Group E. Read-only inspection of per-site UA + stealth-related
flags, playwright-stealth library availability, and built-in
STEALTH_JS presence; flags configuration that would silently
degrade anti-detection (missing library when requested, headless
disabled, use_stealth disabled).
"""
import contextlib
import os

from bulk_downloader import dev_suite as ds


@contextlib.contextmanager
def _force_lib_available(value):
    """Monkeypatch stealth.get_library_status to return a fixed
    available flag — both branches must be testable from the sandbox
    where the library may or may not actually be importable."""
    from bulk_downloader import stealth as _s
    saved = _s.get_library_status
    _s.get_library_status = lambda: {
        "available": bool(value),
        "import_error": "" if value else "playwright_stealth: simulated",
        "version": "1.2.3" if value else "",
    }
    try:
        yield
    finally:
        _s.get_library_status = saved


# ── D-50 stealth_audit ─────────────────────────────────────────────

def test_stealth_audit_empty_sites(clean_workdir):
    r = ds.stealth_audit(site_configs={})
    assert r["ok"] is True
    assert r["tool"] == "stealth_audit"
    assert r["site_count"] == 0
    assert r["sites"] == []
    assert r["misconfigurations"] == []
    assert "no misconfig" in r["verdict"]


def test_stealth_audit_reports_library_status(clean_workdir):
    with _force_lib_available(True):
        r = ds.stealth_audit(site_configs={})
    assert r["stealth_library"]["available"] is True
    assert r["stealth_library"]["version"] == "1.2.3"
    assert "available" in r["verdict"]


def test_stealth_audit_reports_builtin_js_presence(clean_workdir):
    r = ds.stealth_audit(site_configs={})
    # constants.STEALTH_JS is always shipped — must be present
    assert r["builtin_stealth_js"]["present"] is True
    assert r["builtin_stealth_js"]["length_bytes"] > 100


def test_stealth_audit_flags_missing_library(clean_workdir):
    site_configs = {
        "siteA": {"use_stealth_library": True,
                  "headless": True, "use_stealth": True},
    }
    with _force_lib_available(False):
        r = ds.stealth_audit(site_configs=site_configs)
    issues = [m for m in r["misconfigurations"]
              if m["site_id"] == "siteA"]
    assert any("playwright-stealth not importable" in m["issue"]
               for m in issues)


def test_stealth_audit_no_warning_when_library_present(clean_workdir):
    site_configs = {
        "siteA": {"use_stealth_library": True,
                  "headless": True, "use_stealth": True},
    }
    with _force_lib_available(True):
        r = ds.stealth_audit(site_configs=site_configs)
    issues_for_a = [m for m in r["misconfigurations"]
                     if m["site_id"] == "siteA"]
    # use_stealth_library=True is FINE when library is available
    assert not any("playwright-stealth not importable" in m["issue"]
                    for m in issues_for_a)


def test_stealth_audit_flags_headless_disabled(clean_workdir):
    site_configs = {
        "siteA": {"headless": False, "use_stealth": True,
                  "use_stealth_library": False},
    }
    with _force_lib_available(True):
        r = ds.stealth_audit(site_configs=site_configs)
    assert any(m["site_id"] == "siteA" and "headless=False" in m["issue"]
               for m in r["misconfigurations"])


def test_stealth_audit_flags_use_stealth_disabled(clean_workdir):
    site_configs = {
        "siteA": {"headless": True, "use_stealth": False,
                  "use_stealth_library": False},
    }
    with _force_lib_available(True):
        r = ds.stealth_audit(site_configs=site_configs)
    assert any(m["site_id"] == "siteA" and "use_stealth=False" in m["issue"]
               for m in r["misconfigurations"])


def test_stealth_audit_clean_site_no_misconfig(clean_workdir):
    site_configs = {
        "siteA": {"headless": True, "use_stealth": True,
                  "use_stealth_library": False,
                  "user_agent": "Mozilla/5.0 ..."},
    }
    with _force_lib_available(True):
        r = ds.stealth_audit(site_configs=site_configs)
    misconfig_for_a = [m for m in r["misconfigurations"]
                       if m["site_id"] == "siteA"]
    assert misconfig_for_a == []


def test_stealth_audit_groups_distinct_user_agents(clean_workdir):
    ua1 = "Mozilla/5.0 (X11; Linux x86_64) Chrome/120"
    ua2 = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121"
    site_configs = {
        "siteA": {"headless": True, "use_stealth": True,
                  "user_agent": ua1},
        "siteB": {"headless": True, "use_stealth": True,
                  "user_agent": ua1},  # same UA as A
        "siteC": {"headless": True, "use_stealth": True,
                  "user_agent": ua2},
        "siteD": {"headless": True, "use_stealth": True},  # blank UA
    }
    with _force_lib_available(True):
        r = ds.stealth_audit(site_configs=site_configs)
    assert r["distinct_ua_count"] == 2
    assert ua1 in r["distinct_user_agents"]
    assert ua2 in r["distinct_user_agents"]
    assert r["sites_using_runner_default_ua"] == 1  # siteD


# ── dev route ─────────────────────────────────────────────────────

def test_stealth_audit_route(fresh_app):
    j = fresh_app.get("/api/dev/stealth_audit").get_json()
    assert j["ok"] is True
    assert j["tool"] == "stealth_audit"


def test_t16_route_is_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        assert c.get("/api/dev/stealth_audit").status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
