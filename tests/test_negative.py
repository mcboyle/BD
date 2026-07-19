"""Negative tests — assert that things which SHOULD fail actually do.

Most of the suite is happy-path: it confirms correct input produces
correct output. These tests confirm the opposite half — that bad input,
missing resources, and invalid state are REJECTED rather than silently
accepted. A validator that never says no is worthless.
"""
import json
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _client():
    from bulk_downloader import app as a
    return a.app.test_client()


# ── Config validation must REJECT bad configs ───────────────────────────

def test_negative_validate_rejects_missing_required_field():
    """A config missing a required field must NOT validate as ok."""
    from bulk_downloader.site_editor import validate_config
    result = validate_config({})  # empty — missing everything required
    assert result["ok"] is False
    assert len(result["errors"]) > 0


def test_negative_validate_rejects_non_dict():
    from bulk_downloader.site_editor import validate_config
    result = validate_config("this is not a config")
    assert result["ok"] is False


def test_negative_validate_blank_name_rejected():
    from bulk_downloader.site_editor import validate_config
    result = validate_config({"name": "   "})  # whitespace-only
    assert result["ok"] is False


def test_negative_validate_script_exit_1_on_bad(tmp_path):
    """The pre-commit validator must exit non-zero on a broken config —
    if it exits 0, it would let a broken commit through."""
    import subprocess
    cfg = tmp_path / "sites_config.json"
    cfg.write_text(json.dumps({"broken": {"name": ""}}))
    r = subprocess.run(
        [sys.executable, os.path.join(_REPO, "tools",
                                      "validate_sites_config.py"),
         "--config", str(cfg)],
        capture_output=True, text=True, timeout=20)
    assert r.returncode != 0, "validator passed a broken config"


# ── Filename preview must REJECT bad templates ──────────────────────────

def test_negative_preview_rejects_numeric_template():
    c = _client()
    r = c.post("/api/sites/preview_filename", json={"template": 999})
    assert r.status_code == 400


def test_negative_preview_rejects_dict_template():
    c = _client()
    r = c.post("/api/sites/preview_filename",
               json={"template": {"nested": "object"}})
    assert r.status_code == 400


# ── Unknown routes must 404 ─────────────────────────────────────────────

def test_negative_unknown_api_route_404():
    c = _client()
    r = c.get("/api/this_endpoint_does_not_exist")
    assert r.status_code == 404


def test_negative_unknown_deep_route_404():
    c = _client()
    r = c.get("/api/sites/nonexistent/totally/made/up")
    assert r.status_code == 404


# ── Wrong methods must 405 (not silently succeed) ───────────────────────

def test_negative_delete_on_get_endpoint_rejected():
    c = _client()
    r = c.delete("/api/health")
    assert r.status_code in (404, 405)


def test_negative_put_on_get_endpoint_rejected():
    c = _client()
    r = c.put("/api/sites/schema")
    assert r.status_code in (404, 405)


# ── Nonexistent resources must not 200 with fake data ───────────────────

def test_negative_get_nonexistent_site():
    """Fetching a site id that doesn't exist must not return ok data."""
    c = _client()
    r = c.get("/api/sites/this_site_id_does_not_exist")
    # Either 404, or a JSON body that does NOT claim success
    if r.status_code == 200:
        body = r.get_json()
        assert not (isinstance(body, dict) and body.get("ok") is True
                    and body.get("config")), \
            "nonexistent site returned ok config data"


def test_negative_doctor_diagnose_nonexistent_history():
    """Diagnosing a history id that doesn't exist must report no match
    — not invent a diagnosis."""
    c = _client()
    r = c.post("/api/doctor/diagnose", json={"hid": 88888888})
    body = r.get_json()
    assert body["ok"] is True
    assert body["diagnosis"]["matched"] is False


# ── Diagnoser must NOT match nonsense ───────────────────────────────────

def test_negative_diagnose_unrelated_text_no_match():
    """An error string matching no known signature must report
    matched=False, not a confident wrong guess."""
    from bulk_downloader import doctor
    d = doctor.diagnose_failure(
        "the quick brown fox jumped over the lazy dog")
    assert d["matched"] is False
    assert d["confidence"] == "none"


def test_negative_diagnose_empty_no_false_match():
    from bulk_downloader import doctor
    for junk in ("", "   ", None, "\n\n"):
        d = doctor.diagnose_failure(junk)
        assert d["matched"] is False


# ── Schema must REJECT being treated as strict ──────────────────────────

def test_negative_schema_does_not_require_fields():
    """The schema is intentionally non-strict — if it ever grows a
    top-level `required` array it would reject valid partial configs.
    This test fails if that regression happens."""
    c = _client()
    schema = c.get("/api/sites/schema").get_json()
    site_schema = schema["additionalProperties"]
    assert "required" not in site_schema, \
        "site schema became strict — partial configs would break"


# ── Rotate-token script must REJECT malformed config ────────────────────

def test_negative_rotate_token_rejects_bad_json(tmp_path):
    import subprocess
    cfg = tmp_path / "app_config.json"
    cfg.write_text("{ broken json")
    r = subprocess.run(
        [sys.executable, os.path.join(_REPO, "tools", "rotate_token.py"),
         "--config", str(cfg)],
        capture_output=True, text=True, timeout=15, env=dict(os.environ))
    assert r.returncode != 0, "rotate_token accepted malformed JSON"


def test_negative_rotate_token_rejects_json_array(tmp_path):
    """app_config.json must be an object, not an array."""
    import subprocess
    cfg = tmp_path / "app_config.json"
    cfg.write_text("[1, 2, 3]")
    r = subprocess.run(
        [sys.executable, os.path.join(_REPO, "tools", "rotate_token.py"),
         "--config", str(cfg)],
        capture_output=True, text=True, timeout=15, env=dict(os.environ))
    assert r.returncode != 0


# ── Widget config must REJECT unknown ids ───────────────────────────────

def test_negative_unknown_widget_id_not_in_catalog():
    """A made-up widget id must NOT be considered valid."""
    from bulk_downloader import widgets_config
    assert "totally_fake_widget_xyz" not in widgets_config.VALID_WIDGET_IDS


# ── Integrity endpoint must not invent issues ───────────────────────────

def test_negative_integrity_clean_install_no_issues():
    """A fresh install has no real files — integrity must report zero
    issues, not hallucinate them."""
    c = _client()
    body = c.get("/api/library/integrity").get_json()
    assert body["count"] == 0
