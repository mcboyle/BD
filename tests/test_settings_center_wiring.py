"""Smoke test for the read-only Settings Center (GUI Phase 3, Slice 1).

Registers the blueprint into a fresh Flask app (the way app.py now does) and exercises
every endpoint + page through the test client. Asserts: routes register, page renders
200 HTML, APIs return ok JSON, routes are GET-only (read-only), the schema is the
authoritative 225-unique CFG_FIELDS, and secret fields are returned by presence only
(never by value). Sandbox-valid (Flask in prestaged_site_packages); does NOT boot app.py.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

from flask import Flask  # noqa: E402
from bulk_downloader import app_settings_center  # noqa: E402

_APIS = ["/api/settings/schema", "/api/settings/global/effective",
         "/api/settings/vpn/summary", "/api/settings/env/effective",
         "/api/settings/site/demo/effective"]


def _app():
    app = Flask(__name__)
    n = app_settings_center.register_routes(app)
    return app, n


def test_register_routes_nonzero():
    _app_, n = _app()
    assert n >= 6, n  # 5 APIs + 1 page


def test_settings_page_renders_200_html():
    app, _ = _app()
    r = app.test_client().get("/cockpit/settings")
    assert r.status_code == 200, r.status_code
    low = r.data.lower()
    assert b"<html" in low or b"<!doctype" in low
    assert b"read-only" in low


def test_apis_return_ok_json():
    app, _ = _app()
    c = app.test_client()
    for p in _APIS:
        r = c.get(p)
        assert r.status_code in (200, 500), (p, r.status_code)
        body = json.loads(r.data)
        assert "ok" in body, (p, body)
        if r.status_code == 200:
            assert body["ok"] is True, (p, body)


def test_schema_is_authoritative_225():
    app, _ = _app()
    body = json.loads(app.test_client().get("/api/settings/schema").data)
    assert body["unique_fields"] == 238, body["unique_fields"]  # v3.66.702: +1 jd_supported_hosts_path (JD-3); v3.66.810: +2 predictive_relogin_{enabled,fraction} (MOD-1 F1.4); v3.66.1016: +1 dismiss_selectors_login (item E)
    assert len(body["secret_fields"]) >= 8, body["secret_fields"]
    assert body["read_only"] is True


def test_only_validate_is_post_and_rest_get():
    # Slice 2 adds exactly one POST (the dry-run validate); everything else is GET.
    app, _ = _app()
    mutating = {"PUT", "DELETE", "PATCH"}
    posts = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("settings_center."):
            assert not (rule.methods & mutating), (rule.rule, rule.methods)
            if "POST" in rule.methods:
                posts.append(rule.rule)
    assert posts == ["/api/settings/site/<sid>/validate"], posts


def test_editable_excludes_secrets_and_gated():
    app, _ = _app()
    body = json.loads(app.test_client().get("/api/settings/site/demo/editable").data)
    assert body["ok"] is True
    keys = set(body["fields"].keys())
    # secrets / auth / selector must NOT be editable here
    for forbidden in ("captcha_api_key", "password", "login_url", "user_field",
                      "dl_selector", "trigger_selector", "dismiss_selectors"):
        assert forbidden not in keys, forbidden
    # a clearly gui-safe field should be present
    assert "max_concurrent" in keys


def test_validate_gates_fields():
    app, _ = _app()
    payload = {"updates": {
        "max_concurrent": "5",        # gui-safe int -> accepted (coerced)
        "use_stealth": "true",        # gui-safe bool -> accepted
        "captcha_api_key": "x",       # secret -> rejected
        "login_url": "http://x",      # auth/login -> rejected
        "dl_selector": ".btn",        # selector -> rejected
        "totally_unknown": 1,         # unknown -> rejected
    }}
    body = json.loads(app.test_client().post(
        "/api/settings/site/demo/validate", json=payload).data)
    assert body["ok"] is True
    assert body["accepted"]["max_concurrent"] == 5
    assert body["accepted"]["use_stealth"] is True
    for r in ("captcha_api_key", "login_url", "dl_selector", "totally_unknown"):
        assert r in body["rejected"], (r, body["rejected"])


def test_validate_is_nonmutating():
    # validate must never touch the config file.
    d = tempfile.mkdtemp()
    cfgp = Path(d) / "sites_config.json"
    cfgp.write_text(json.dumps({"sites": {"acme": {"name": "acme", "max_concurrent": 3}}}))
    old = os.environ.get("BD_SITES_CONFIG_PATH")
    try:
        os.environ["BD_SITES_CONFIG_PATH"] = str(cfgp)
        before = cfgp.read_text()
        app, _ = _app()
        app.test_client().post("/api/settings/site/acme/validate",
                               json={"updates": {"max_concurrent": 9}})
        assert cfgp.read_text() == before, "validate mutated the config file!"
    finally:
        if old is None:
            os.environ.pop("BD_SITES_CONFIG_PATH", None)
        else:
            os.environ["BD_SITES_CONFIG_PATH"] = old


def test_site_editor_page_renders_200():
    app, _ = _app()
    r = app.test_client().get("/cockpit/settings/site/demo")
    assert r.status_code == 200, r.status_code
    low = r.data.lower()
    assert b"gui-safe" in low and (b"<html" in low or b"<!doctype" in low)


def test_secrets_health_presence_only_never_values():
    d = tempfile.mkdtemp()
    cfgp = Path(d) / "sites_config.json"
    secret_val = "TOP_SECRET_KEY_xyz789"
    cfgp.write_text(json.dumps({"sites": {
        "acme": {"name": "acme", "captcha_api_key": secret_val, "stash_api_key": ""},
        "beta": {"name": "beta", "plex_token": "another_secret_value"}}}))
    old = os.environ.get("BD_SITES_CONFIG_PATH")
    try:
        os.environ["BD_SITES_CONFIG_PATH"] = str(cfgp)
        app, _ = _app()
        r = app.test_client().get("/api/settings/secrets/health")
        body = json.loads(r.data)
        assert body["ok"] is True and body["read_only"] is True
        flat = json.dumps(body)
        assert secret_val not in flat and "another_secret_value" not in flat, "secret value leaked!"
        acme = body["per_site"]["acme"]
        assert acme["present"]["captcha_api_key"] is True       # set -> present
        assert acme["present"]["stash_api_key"] is False        # blank -> not present
        assert acme["count_set"] == 1
    finally:
        if old is None:
            os.environ.pop("BD_SITES_CONFIG_PATH", None)
        else:
            os.environ["BD_SITES_CONFIG_PATH"] = old


def test_secrets_page_renders_200():
    app, _ = _app()
    r = app.test_client().get("/cockpit/settings/secrets")
    assert r.status_code == 200, r.status_code
    low = r.data.lower()
    assert b"read-only" in low and b"secrets health" in low


def test_secrets_masked_never_value():
    # Point BD_SITES_CONFIG_PATH at a temp config whose site sets a real secret value;
    # the effective endpoint must return presence only, never the value.
    d = tempfile.mkdtemp()
    cfgp = Path(d) / "sites_config.json"
    secret_val = "SUPER_SECRET_VALUE_123"
    cfgp.write_text(json.dumps({"sites": {"acme": {
        "name": "acme", "captcha_api_key": secret_val, "max_concurrent": 3}}}))
    old = os.environ.get("BD_SITES_CONFIG_PATH")
    try:
        os.environ["BD_SITES_CONFIG_PATH"] = str(cfgp)
        app, _ = _app()
        r = app.test_client().get("/api/settings/site/acme/effective")
        body = json.loads(r.data)
        assert body["ok"] is True, body
        flat = json.dumps(body)
        assert secret_val not in flat, "secret value leaked!"
        assert body["fields"]["captcha_api_key"] == {"present": True}, body["fields"]
        # non-secret value passes through
        assert body["fields"]["max_concurrent"] == 3, body["fields"]
    finally:
        if old is None:
            os.environ.pop("BD_SITES_CONFIG_PATH", None)
        else:
            os.environ["BD_SITES_CONFIG_PATH"] = old
