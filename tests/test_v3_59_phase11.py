"""v3.59 (Phase 11) — regression tests for the site-editor backlog.

Covers the three testable Phase 11 items shipped in v3.59.0:
#58 JSON Schema endpoint, #103 selector-health endpoint, #57 the
sites_config validation script.
"""
import json
import os
import subprocess
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── #58 JSON Schema ─────────────────────────────────────────────────────

def test_schema_endpoint_is_draft07():
    from bulk_downloader import app as a
    c = a.app.test_client()
    schema = c.get("/api/sites/schema").get_json()
    assert "draft-07" in schema["$schema"]
    assert schema["type"] == "object"


def test_schema_describes_site_config():
    from bulk_downloader import app as a
    c = a.app.test_client()
    schema = c.get("/api/sites/schema").get_json()
    site = schema["additionalProperties"]
    props = site["properties"]
    # Core fields present
    assert "name" in props
    assert "trigger_selector" in props
    assert "download_dir" in props


def test_schema_types_are_correct():
    """Numeric / boolean fields must not be typed as plain strings."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    props = c.get("/api/sites/schema").get_json()[
        "additionalProperties"]["properties"]
    assert props["wait"]["type"] == "number"
    assert props["max_concurrent"]["type"] == "integer"
    assert props["headless"]["type"] == "boolean"
    assert props["verify_integrity"]["type"] == "boolean"


def test_schema_matches_cfg_fields():
    """Every CFG_FIELDS entry must appear in the schema (generated from
    it, so this guards against the generator silently dropping fields)."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    props = c.get("/api/sites/schema").get_json()[
        "additionalProperties"]["properties"]
    for field in a.CFG_FIELDS:
        assert field in props, f"{field} missing from schema"


def test_schema_is_non_strict():
    """Hand-edited file — additionalProperties must stay permissive."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    schema = c.get("/api/sites/schema").get_json()
    assert schema["additionalProperties"]["additionalProperties"] is True
    assert "required" not in schema["additionalProperties"]


# ── #103 selector health ────────────────────────────────────────────────

def test_selector_health_endpoint_shape():
    from bulk_downloader import app as a
    c = a.app.test_client()
    body = c.get("/api/sites/selector_health").get_json()
    assert body["ok"] is True
    assert "sites" in body
    assert "stale_count" in body


def test_selector_health_empty_clean_install():
    from bulk_downloader import app as a
    c = a.app.test_client()
    body = c.get("/api/sites/selector_health").get_json()
    assert body["count"] == 0
    assert body["stale_count"] == 0


def test_selector_health_surfaces_drift():
    """After recording selector failures, the site shows up degraded."""
    from bulk_downloader import app as a
    from bulk_downloader import selector_drift as drift
    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "DriftSite"}).get_json()["id"]
    for _ in range(3):
        drift.record_zero_match(sid, ".missing-btn", "https://x.com/v")
    body = c.get("/api/sites/selector_health").get_json()
    match = [s for s in body["sites"] if s["site_id"] == sid]
    assert match, "drift site not surfaced"
    assert match[0]["consecutive_failures"] >= 3


# ── #57 validation script ───────────────────────────────────────────────

_VALIDATE = os.path.join(_REPO, "tools", "validate_sites_config.py")


def test_validate_script_passes_good_config(tmp_path):
    cfg = tmp_path / "sites_config.json"
    cfg.write_text(json.dumps({
        "s1": {"name": "Good", "login_url": "https://x.com",
               "trigger_selector": ".btn", "dl_selector": ".dl",
               "download_dir": "/tmp/d"}
    }))
    r = subprocess.run([sys.executable, _VALIDATE, "--config", str(cfg)],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr


def test_validate_script_fails_bad_config(tmp_path):
    cfg = tmp_path / "sites_config.json"
    cfg.write_text(json.dumps({"bad": {"name": ""}}))  # missing name
    r = subprocess.run([sys.executable, _VALIDATE, "--config", str(cfg)],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 1
    assert "ERROR" in r.stderr


def test_validate_script_missing_file_is_ok(tmp_path):
    """A fresh checkout has no sites_config.json — that's not a failure."""
    cfg = tmp_path / "nonexistent.json"
    r = subprocess.run([sys.executable, _VALIDATE, "--config", str(cfg)],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0


def test_validate_script_rejects_malformed_json(tmp_path):
    cfg = tmp_path / "sites_config.json"
    cfg.write_text("{ not valid json")
    r = subprocess.run([sys.executable, _VALIDATE, "--config", str(cfg)],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 1
    assert "malformed" in r.stderr.lower()
