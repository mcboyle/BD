"""v3.52 (Phase 5) — regression tests for site-editor support.

Covers site_editor.py: validate_config, export_config, import_config,
diff_config — and the four API endpoints that wrap them.
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── validate_config ─────────────────────────────────────────────────────

def test_validate_accepts_minimal_valid_config():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": "MySite"})
    assert r["ok"] is True
    assert r["errors"] == []


def test_validate_rejects_missing_name():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": ""})
    assert r["ok"] is False
    assert any("name" in e for e in r["errors"])


def test_validate_rejects_bad_url():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": "S", "login_url": "example.com"})
    assert r["ok"] is False
    assert any("URL" in e for e in r["errors"])


def test_validate_accepts_good_url():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": "S",
                            "login_url": "https://example.com/login"})
    assert r["ok"] is True


def test_validate_numeric_range_enforced():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": "S", "wait": 999})
    assert r["ok"] is False
    assert any("wait" in e and "between" in e for e in r["errors"])


def test_validate_numeric_non_number_rejected():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": "S", "max_concurrent": "lots"})
    assert r["ok"] is False
    assert any("max_concurrent" in e for e in r["errors"])


def test_validate_rejects_collection_in_string_field():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": ["not", "a", "string"]})
    # name as a list — caught either by required check or type check
    assert r["ok"] is False


def test_validate_warns_login_url_without_creds():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": "S",
                            "login_url": "https://x.com/login"})
    assert r["ok"] is True  # warning, not error
    assert any("login" in w.lower() for w in r["warnings"])


def test_validate_warns_creds_without_selectors():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": "S", "username": "bob",
                            "password": "pw"})
    assert r["ok"] is True
    assert any("selector" in w for w in r["warnings"])


def test_validate_unbalanced_template_braces():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": "S",
                            "filename_template": "{title}{ext"})
    assert r["ok"] is False
    assert any("brace" in e for e in r["errors"])


def test_validate_bad_proxy_scheme():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": "S", "proxy": "1.2.3.4:8080"})
    assert r["ok"] is False
    assert any("Proxy" in e for e in r["errors"])


def test_validate_scheduling_without_time():
    from bulk_downloader import site_editor as se
    r = se.validate_config({"name": "S", "sched_enabled": True,
                            "sched_time": ""})
    assert r["ok"] is False
    assert any("schedule" in e.lower() for e in r["errors"])


# ── export_config ───────────────────────────────────────────────────────

def test_export_strips_secrets_by_default():
    from bulk_downloader import site_editor as se
    env = se.export_config({"name": "S", "password": "sekrit",
                            "captcha_api_key": "key123"})
    assert "password" not in env["config"]
    assert "captcha_api_key" not in env["config"]
    assert set(env["_had_secrets"]) == {"password", "captcha_api_key"}


def test_export_includes_secrets_when_asked():
    from bulk_downloader import site_editor as se
    env = se.export_config({"name": "S", "password": "sekrit"},
                           include_secrets=True)
    assert env["config"]["password"] == "sekrit"


def test_export_has_envelope():
    from bulk_downloader import site_editor as se
    env = se.export_config({"name": "S"})
    assert env["schema"] == se.EXPORT_SCHEMA
    assert "app_version" in env
    assert "exported_at" in env
    assert "config" in env


def test_export_drops_derived_keys():
    from bulk_downloader import site_editor as se
    env = se.export_config({"name": "S", "has_password": True,
                            "fingerprint": "abc"})
    assert "has_password" not in env["config"]
    assert "fingerprint" not in env["config"]


# ── import_config ───────────────────────────────────────────────────────

def test_import_round_trips_export():
    from bulk_downloader import site_editor as se
    env = se.export_config({"name": "RoundTrip",
                            "login_url": "https://x.com"})
    r = se.import_config(env)
    assert r["ok"] is True
    assert r["config"]["name"] == "RoundTrip"


def test_import_drops_unknown_fields():
    from bulk_downloader import site_editor as se
    payload = {"schema": se.EXPORT_SCHEMA,
               "config": {"name": "S", "bogus_field": "x"}}
    r = se.import_config(payload, known_fields={"name"})
    assert "bogus_field" not in r["config"]
    assert any("bogus_field" in w for w in r["warnings"])


def test_import_bare_config_warns():
    from bulk_downloader import site_editor as se
    # No envelope — just a config dict
    r = se.import_config({"name": "Bare"})
    assert r["ok"] is True
    assert any("envelope" in w.lower() for w in r["warnings"])


def test_import_rejects_invalid_config():
    from bulk_downloader import site_editor as se
    env = {"schema": se.EXPORT_SCHEMA, "config": {"name": ""}}
    r = se.import_config(env)
    assert r["ok"] is False


def test_import_surfaces_stripped_secrets():
    from bulk_downloader import site_editor as se
    env = se.export_config({"name": "S", "password": "x"})
    r = se.import_config(env)
    assert any("secret" in w.lower() for w in r["warnings"])


# ── diff_config ─────────────────────────────────────────────────────────

def test_diff_only_reports_changed_fields():
    from bulk_downloader import site_editor as se
    old = {"name": "A", "wait": 4, "delay": 3}
    new = {"name": "B", "wait": 4, "delay": 3}  # only name changed
    d = se.diff_config(old, new)
    assert len(d) == 1
    assert d[0]["field"] == "name"
    assert d[0]["kind"] == "changed"


def test_diff_partial_config_ignores_absent_keys():
    """The editor sends a partial config — absent keys must NOT show
    as removed. This is the bug found during Phase 5 smoke-testing."""
    from bulk_downloader import site_editor as se
    old = {"name": "A", "wait": 4, "delay": 3, "max_retries": 2,
           "headless": True, "min_resolution": 1080}
    new = {"name": "A", "wait": 10}  # partial: only 2 fields
    d = se.diff_config(old, new)
    # Only `wait` changed; everything absent from `new` is untouched
    assert len(d) == 1
    assert d[0]["field"] == "wait"


def test_diff_detects_field_clear_as_removed():
    from bulk_downloader import site_editor as se
    old = {"name": "A", "username": "bob"}
    new = {"username": ""}  # explicitly cleared
    d = se.diff_config(old, new)
    assert len(d) == 1
    assert d[0]["field"] == "username"
    assert d[0]["kind"] == "removed"


def test_diff_detects_added_field():
    from bulk_downloader import site_editor as se
    old = {"name": "A"}
    new = {"login_url": "https://x.com"}
    d = se.diff_config(old, new)
    assert len(d) == 1
    assert d[0]["kind"] == "added"


def test_diff_secrets_never_expose_values():
    from bulk_downloader import site_editor as se
    old = {"password": "old-secret"}
    new = {"password": "new-secret"}
    d = se.diff_config(old, new)
    # password changed value but both are non-blank → bool unchanged →
    # no diff entry (we only diff secret presence, not value)
    assert d == []
    # But setting a previously-empty secret IS a diff
    d2 = se.diff_config({}, {"password": "now-set"})
    assert len(d2) == 1
    assert d2[0]["secret"] is True
    assert "old-secret" not in str(d2)
    assert "now-set" not in str(d2)


# ── API endpoints ───────────────────────────────────────────────────────

def test_api_validate_endpoint():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites/validate", json={"name": "", "wait": 999})
    body = r.get_json()
    assert body["ok"] is False
    assert len(body["errors"]) >= 2


def test_api_export_endpoint():
    from bulk_downloader import app as a
    c = a.app.test_client()
    sid = c.post("/api/sites",
                 json={"name": "ExportEP",
                       "password": "x"}).get_json()["id"]
    r = c.get(f"/api/sites/{sid}/export")
    env = r.get_json()
    assert env["schema"]
    assert "password" not in env["config"]
    assert "Content-Disposition" in r.headers


def test_api_export_include_secrets():
    # v3.66.326: login passwords route through the encrypted vault as @cred:
    # refs, so an include_secrets backup must RESOLVE the ref back to plaintext.
    # The sandbox default backend is locked, so stub an unlocked encrypted
    # backend that stores + resolves to drive the real create -> @cred ->
    # export-resolve round-trip.
    from bulk_downloader import app as a
    from bulk_downloader import secrets_store as ss
    c = a.app.test_client()
    store = {}

    class _Unlocked:
        name = "master_password"

        def is_unlocked(self):
            return True

        def set(self, k, v):
            store[k] = v

        def get(self, k):
            return store.get(k)

    orig = ss.get_backend
    try:
        ss.get_backend = lambda: _Unlocked()
        sid = c.post("/api/sites",
                     json={"name": "S", "password": "x"}).get_json()["id"]
        # stored as a @cred ref, never plaintext
        assert store.get(ss.site_password_key(sid)) == "x"
        r = c.get(f"/api/sites/{sid}/export?include_secrets=1")
        assert r.get_json()["config"]["password"] == "x"
    finally:
        ss.get_backend = orig


def test_api_import_endpoint_creates_site():
    from bulk_downloader import app as a
    c = a.app.test_client()
    sid = c.post("/api/sites",
                 json={"name": "Original"}).get_json()["id"]
    env = c.get(f"/api/sites/{sid}/export").get_json()
    r = c.post("/api/sites/import", json=env)
    body = r.get_json()
    assert body["ok"] is True
    assert body["id"] != sid  # a NEW site


def test_api_import_rejects_invalid():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites/import",
               json={"schema": "bd-site-config/1",
                     "config": {"name": ""}})
    assert r.status_code == 400


def test_api_diff_endpoint():
    from bulk_downloader import app as a
    c = a.app.test_client()
    sid = c.post("/api/sites",
                 json={"name": "DiffEP",
                       "wait": 4}).get_json()["id"]
    r = c.post(f"/api/sites/{sid}/diff",
               json={"name": "DiffEP-Renamed", "wait": 4})
    body = r.get_json()
    assert body["ok"] is True
    assert body["change_count"] == 1
    assert body["diff"][0]["field"] == "name"


def test_api_diff_404_for_unknown_site():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites/nonexistent/diff", json={"name": "X"})
    assert r.status_code == 404
