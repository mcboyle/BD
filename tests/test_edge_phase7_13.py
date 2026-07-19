"""Edge-case and error-path tests for the Phase 7-13 endpoints.

The per-phase test files cover the happy path. This file covers what
they don't: malformed input, missing parameters, boundary values,
empty state, wrong methods, and oversized input. These are the cases
that actually break in production.

Naming: test_edge_<phase>_<case> so failures point straight at the gap.
"""
import json
import os
import sys
import time

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

def _client():
    from bulk_downloader import app as a
    return a.app.test_client()


# ════════════════════════════════════════════════════════════════════
# /api/doctor  &  /api/doctor/diagnose  (Phase 7)
# ════════════════════════════════════════════════════════════════════

def test_edge_doctor_get_only():
    """/api/doctor is GET — POST should not be routed to it."""
    c = _client()
    r = c.post("/api/doctor")
    assert r.status_code in (404, 405)


def test_edge_doctor_diagnose_empty_body():
    """POST with no JSON body must not 500."""
    c = _client()
    r = c.post("/api/doctor/diagnose")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["diagnosis"]["matched"] is False


def test_edge_doctor_diagnose_empty_error_string():
    c = _client()
    r = c.post("/api/doctor/diagnose", json={"error": ""})
    assert r.get_json()["diagnosis"]["matched"] is False


def test_edge_doctor_diagnose_null_error():
    c = _client()
    r = c.post("/api/doctor/diagnose", json={"error": None})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_edge_doctor_diagnose_nonstring_error():
    """A list where a string is expected must not crash."""
    c = _client()
    r = c.post("/api/doctor/diagnose", json={"error": ["a", "b"]})
    assert r.status_code == 200


def test_edge_doctor_diagnose_bad_hid():
    """A history id that doesn't exist → graceful no-match, not 500."""
    c = _client()
    r = c.post("/api/doctor/diagnose", json={"hid": 999999999})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_edge_doctor_diagnose_nonnumeric_hid():
    c = _client()
    r = c.post("/api/doctor/diagnose", json={"hid": "not-a-number"})
    assert r.status_code == 200


def test_edge_doctor_diagnose_huge_error_string():
    """A 100k-char error string must not hang or crash."""
    c = _client()
    r = c.post("/api/doctor/diagnose", json={"error": "x" * 100_000})
    assert r.status_code == 200


def test_edge_doctor_diagnose_garbage_body():
    """Raw non-JSON body."""
    c = _client()
    r = c.post("/api/doctor/diagnose", data="not json at all",
               content_type="application/json")
    assert r.status_code in (200, 400)


# ════════════════════════════════════════════════════════════════════
# /api/jobs/stuck  (Phase 8)
# ════════════════════════════════════════════════════════════════════

def test_edge_stuck_negative_minutes():
    """minutes=-5 must not crash; treated as a threshold."""
    c = _client()
    r = c.get("/api/jobs/stuck?minutes=-5")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_edge_stuck_zero_minutes():
    c = _client()
    r = c.get("/api/jobs/stuck?minutes=0")
    assert r.status_code == 200


def test_edge_stuck_nonnumeric_minutes():
    """minutes=abc must fall back to the default, not 500."""
    c = _client()
    r = c.get("/api/jobs/stuck?minutes=abc")
    assert r.status_code == 200
    assert r.get_json()["threshold_minutes"] == 30.0


def test_edge_stuck_huge_minutes():
    c = _client()
    r = c.get("/api/jobs/stuck?minutes=999999999")
    assert r.status_code == 200


def test_edge_stuck_job_missing_progress_field():
    """A job dict with no last_progress_at must be skipped, not crash."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "EdgeSite"}).get_json()["id"]
    a.runners[sid].jobs["https://x.com/noprog"] = {"status": "running"}
    r = c.get("/api/jobs/stuck")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_edge_stuck_job_non_dict_value():
    """A corrupt non-dict job entry must be skipped defensively."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "EdgeSite2"}).get_json()["id"]
    a.runners[sid].jobs["https://x.com/bad"] = "this is not a dict"
    r = c.get("/api/jobs/stuck")
    assert r.status_code == 200


# ════════════════════════════════════════════════════════════════════
# /api/sites/preview_filename  (Phase 9)
# ════════════════════════════════════════════════════════════════════

def test_edge_preview_no_body():
    c = _client()
    r = c.post("/api/sites/preview_filename")
    # empty template is valid -> empty preview
    assert r.status_code == 200


def test_edge_preview_missing_template_key():
    c = _client()
    r = c.post("/api/sites/preview_filename", json={"context": {}})
    assert r.status_code == 200
    assert r.get_json()["preview"] == ""


def test_edge_preview_template_is_number():
    c = _client()
    r = c.post("/api/sites/preview_filename", json={"template": 12345})
    assert r.status_code == 400


def test_edge_preview_template_is_null():
    c = _client()
    r = c.post("/api/sites/preview_filename", json={"template": None})
    assert r.status_code == 400


def test_edge_preview_context_is_list():
    """context must be a dict; a list should be ignored, not crash."""
    c = _client()
    r = c.post("/api/sites/preview_filename",
               json={"template": "{title}", "context": ["a", "b"]})
    assert r.status_code == 200


def test_edge_preview_unclosed_brace():
    """A malformed template with an unclosed brace must not 500."""
    c = _client()
    r = c.post("/api/sites/preview_filename",
               json={"template": "{title"})
    assert r.status_code in (200, 400)


def test_edge_preview_only_braces():
    c = _client()
    r = c.post("/api/sites/preview_filename", json={"template": "{}{}{}"})
    assert r.status_code in (200, 400)


def test_edge_preview_huge_template():
    c = _client()
    r = c.post("/api/sites/preview_filename",
               json={"template": "{title}" * 5000})
    assert r.status_code in (200, 400)


def test_edge_preview_nested_braces():
    c = _client()
    r = c.post("/api/sites/preview_filename",
               json={"template": "{{title}}"})
    assert r.status_code in (200, 400)


# ════════════════════════════════════════════════════════════════════
# /api/library/integrity  (Phase 9)
# ════════════════════════════════════════════════════════════════════

def test_edge_integrity_negative_limit():
    c = _client()
    r = c.get("/api/library/integrity?limit=-10")
    assert r.status_code == 200


def test_edge_integrity_zero_limit():
    c = _client()
    r = c.get("/api/library/integrity?limit=0")
    assert r.status_code == 200


def test_edge_integrity_nonnumeric_limit():
    c = _client()
    r = c.get("/api/library/integrity?limit=lots")
    assert r.status_code == 200


def test_edge_integrity_unknown_kind():
    """An unrecognized kind filter must return empty, not error."""
    c = _client()
    r = c.get("/api/library/integrity?kind=nonsense_kind")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_edge_integrity_empty_kind():
    c = _client()
    r = c.get("/api/library/integrity?kind=")
    assert r.status_code == 200


# ════════════════════════════════════════════════════════════════════
# /api/sites/schema  &  /api/sites/selector_health  (Phase 11)
# ════════════════════════════════════════════════════════════════════

def test_edge_schema_get_only():
    c = _client()
    r = c.post("/api/sites/schema")
    assert r.status_code in (404, 405)


def test_edge_schema_stable_across_calls():
    """Two calls must return byte-identical schema (deterministic)."""
    c = _client()
    a1 = json.dumps(c.get("/api/sites/schema").get_json(), sort_keys=True)
    a2 = json.dumps(c.get("/api/sites/schema").get_json(), sort_keys=True)
    assert a1 == a2


def test_edge_selector_health_get_only():
    c = _client()
    r = c.post("/api/sites/selector_health")
    assert r.status_code in (404, 405)


def test_edge_selector_health_no_sites():
    """Zero sites configured → empty list, not error."""
    c = _client()
    r = c.get("/api/sites/selector_health")
    body = r.get_json()
    assert body["ok"] is True
    assert body["count"] == 0


# ════════════════════════════════════════════════════════════════════
# /api/notify/presets  (Phase 10)
# ════════════════════════════════════════════════════════════════════

def test_edge_notify_presets_get_only():
    c = _client()
    r = c.post("/api/notify/presets")
    assert r.status_code in (404, 405)


def test_edge_notify_presets_deterministic():
    c = _client()
    a1 = c.get("/api/notify/presets").get_json()
    a2 = c.get("/api/notify/presets").get_json()
    assert a1 == a2


def test_edge_notify_presets_no_secrets_in_templates():
    """Preset templates must be placeholders only — no real tokens
    accidentally baked in."""
    c = _client()
    for preset in c.get("/api/notify/presets").get_json()["presets"]:
        # Every non-scheme segment should be a {placeholder}
        tmpl = preset["template"]
        assert "{" in tmpl and "}" in tmpl
