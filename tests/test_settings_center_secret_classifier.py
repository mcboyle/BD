"""Focused tests for the Settings Center secret classifier (GUI Phase 3 secret-classifier slice).

Proves the corrected rule in bulk_downloader/app_settings_center.py:
  * secret iff name matches password|token|api_key|secret (case-insensitive) OR == cookie_file
  * bare "cookie" is NOT a secret  ->  cookie_max_age_hours is non-secret AND editable
  * cookie_file stays display-never (masked, never editable)
  * username / login_url are not secrets (they stay sticky/preserve-on-blank in app.py)
  * no broad "cookie" token remains in the secret regex

Sandbox-valid: stdlib + the blueprint module (Flask present in prestaged_site_packages); does
not boot app.py. Zero-arg test functions per the custom run_tests.py harness.
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from flask import Flask  # noqa: E402
from bulk_downloader import app_settings_center as SC  # noqa: E402


def _app():
    app = Flask(__name__)
    SC.register_routes(app)
    return app


# ── 1. cookie_file is secret / display-never ──────────────────────────────
def test_cookie_file_is_secret_display_never():
    assert SC._is_secret("cookie_file") is True
    sch = SC._schema()
    assert "cookie_file" in sch["secret_fields"]
    # gui_class for a secret is display-never; never in the editable set
    assert "display-never" in SC._gui_class("cookie_file", True)
    assert "cookie_file" not in SC._editable_field_set()
    # masked in any effective view (presence only, never the value)
    masked = SC._mask_secrets({"cookie_file": "/secret/path/cookies.txt"})
    assert masked["cookie_file"] != "/secret/path/cookies.txt"


# ── 2. cookie_max_age_hours is NOT secret and validates as editable ───────
def test_cookie_max_age_hours_not_secret_and_editable():
    assert SC._is_secret("cookie_max_age_hours") is False
    sch = SC._schema()
    assert "cookie_max_age_hours" not in sch["secret_fields"]
    # it must be an editable (gui-safe) field, not gated
    assert SC._gui_class("cookie_max_age_hours", False) == "gui-safe"
    assert "cookie_max_age_hours" in SC._editable_field_set()
    # validate accepts it (not rejected as secret or gated)
    v = SC._validate_updates({"cookie_max_age_hours": 48})
    assert "cookie_max_age_hours" in v["accepted"], v
    assert "cookie_max_age_hours" not in v["rejected"], v
    # and its value is shown (never masked) in effective views
    masked = SC._mask_secrets({"cookie_max_age_hours": 48})
    assert masked["cookie_max_age_hours"] == 48


# ── 3. username / login_url are not secrets ───────────────────────────────
def test_username_and_login_url_not_secret():
    assert SC._is_secret("username") is False
    assert SC._is_secret("login_url") is False
    sch = SC._schema()
    assert "username" not in sch["secret_fields"]
    assert "login_url" not in sch["secret_fields"]


# ── 4. password / token / api_key / secret fields remain secret ───────────
def test_credential_shaped_fields_remain_secret():
    for k in ("password", "qb_password", "auth_token", "plex_token",
              "captcha_api_key", "xyz_api_key", "client_secret", "stash_api_key",
              "jellyfin_api_key", "ha_token", "tpdb_api_key"):
        assert SC._is_secret(k) is True, k
    sch = SC._schema()
    # the canonical per-site secret set is exactly the 9 corrected names
    assert set(sch["secret_fields"]) == {
        "password", "cookie_file", "captcha_api_key", "stash_api_key",
        "plex_token", "jellyfin_api_key", "ha_token", "qb_password", "tpdb_api_key"
    }, sch["secret_fields"]


# ── 5. no broad "cookie" token remains in the secret floor ────────────────
def test_no_broad_cookie_regex():
    # REDACT-SOT: app_settings_center._is_secret now routes through the shared
    # config-domain SoT floor (site_editor._CONFIG_SECRET_FLOOR). That floor must
    # not contain a bare 'cookie' alternative that would over-match
    # cookie_max_age_hours; it uses 'cookies' (plural) only.
    from bulk_downloader import site_editor as SE
    assert "cookie" not in SE._CONFIG_SECRET_FLOOR, (
        "bare 'cookie' in the config-secret floor would over-match "
        "cookie_max_age_hours; only 'cookies' (plural) is allowed"
    )
    # behavioral proof: cookie-prefixed non-file durations are not secret
    assert SC._is_secret("cookie_max_age_hours") is False
    assert SC._is_secret("cookie_ttl") is False
    # while cookie_file (handled explicitly, not by the floor) still is
    assert SC._is_secret("cookie_file") is True


# ── 6. end-to-end via the live blueprint (schema + validate over HTTP) ────
def test_classifier_through_blueprint():
    app = _app()
    c = app.test_client()
    sch = json.loads(c.get("/api/settings/schema").data)
    assert "cookie_max_age_hours" not in sch["secret_fields"]
    assert "cookie_file" in sch["secret_fields"]
    ed = json.loads(c.get("/api/settings/site/demo/editable").data)
    assert "cookie_max_age_hours" in ed["fields"]
    assert "cookie_file" not in ed["fields"]
    v = json.loads(c.post("/api/settings/site/demo/validate",
                          json={"updates": {"cookie_max_age_hours": 24}}).data)
    assert v["accepted"].get("cookie_max_age_hours") == 24, v
