"""v3.62.2 — guard tests for issues surfaced during the v3.62.2 work.

Each test here pins a fix made this cycle so the same class of bug
cannot silently return:

  - widget data view must poll on a timer (not fetch once and freeze)
  - the AI provider Ollama defaults must be real Ollama registry tags
    (Python backend AND the app.js AI-settings modal)
  - login templates must all have collision-free user/pass selectors
  - the live-status endpoints must stay GET and return JSON

These complement the per-feature test files; they are deliberately
cross-cutting and cheap.
"""
import json
import os
import re
import sys

import pytest


# Zero-entropy fixture material. It is confined to a tmp_path vault and cannot
# unlock any operator credential store.
_TEST_MASTER_PASSWORD = "row645-zero-entropy-master-password"
_TEST_CREDENTIAL_KEY = "bulkdl-site-row645-test"
_TEST_CREDENTIAL_VALUE = "row645-zero-entropy-credential"


# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe


@pytest.fixture
def initialized_unlocked_vault(monkeypatch, tmp_path):
    """Real first-use vault initialization through the operator's API seam."""
    # Dynamic lookup avoids broadening this legacy guard's frozen application
    # import graph merely to configure the dependency its existing app import
    # already owns.
    ss = __import__(
        "bulk_downloader.secrets_store", fromlist=["secrets_store"])

    monkeypatch.delenv("BD_SECRETS_AUDIT", raising=False)
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.json")
    monkeypatch.setattr(ss, "SECRETS_META_FILE", tmp_path / "secrets_meta.json")
    monkeypatch.setattr(ss, "_backend", None)
    monkeypatch.setattr(ss, "_backend_pref", None)
    monkeypatch.setattr(ss, "_audited_cache", None)

    assert ss.configure_backend("master_password") is True
    backend = ss.get_backend()
    assert isinstance(backend, ss.MasterPasswordBackend)
    assert backend.is_initialized() is False
    assert backend.is_unlocked() is False
    # Keep the real PBKDF2/AES-GCM path while making this synthetic fixture
    # cheap. The iteration count is persisted by the same first-use API call.
    backend._data["iterations"] = 1_000

    from bulk_downloader import app as app_module

    app = app_module.app
    app.config["TESTING"] = True
    client = app.test_client()
    unlock = client.post(
        "/api/secrets/unlock", json={"password": _TEST_MASTER_PASSWORD})
    unlock_body = unlock.get_json()
    assert unlock.status_code == 200, unlock_body
    assert unlock_body == {
        "initialized_now": True,
        "is_initialized": True,
        "is_unlocked": True,
        "ok": True,
        "state": "initialized",
    }
    assert ss.SECRETS_FILE.is_file(), "first-use unlock persisted no vault"
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is True

    backend.set(_TEST_CREDENTIAL_KEY, _TEST_CREDENTIAL_VALUE)
    saved_sites = dict(app_module.s_cfg)
    app_module.s_cfg.clear()
    app_module.s_cfg["row645-test"] = {
        "name": "Row 645 synthetic site",
        "password": f"@cred:{_TEST_CREDENTIAL_KEY}",
    }
    refs = ss.password_reference_keys(app_module.s_cfg)
    assert refs == [_TEST_CREDENTIAL_KEY], refs
    assert backend.list_keys() == [_TEST_CREDENTIAL_KEY]
    assert ss.resolve_password(f"@cred:{_TEST_CREDENTIAL_KEY}") == (
        _TEST_CREDENTIAL_VALUE)
    try:
        yield client, backend
    finally:
        app_module.s_cfg.clear()
        app_module.s_cfg.update(saved_sites)

def _static_path(name):
    import bulk_downloader
    base = os.path.dirname(bulk_downloader.__file__)
    return os.path.join(base, "static", name)


# ── widget live-refresh ─────────────────────────────────────────────


# ── AI provider model defaults ──────────────────────────────────────

def test_ollama_default_models_are_real_registry_tags():
    """The Ollama provider's default model names must be tags that
    actually exist in the Ollama registry. The old defaults
    (qwen2-vl:7b-q4 / qwen2.5:7b-q4) did not exist and every pull /
    health check failed. Guard against the bad shapes returning."""
    from bulk_downloader.ai_provider import OllamaProvider
    dm = OllamaProvider.default_models
    for role in ("vision", "text"):
        tag = dm[role]
        # the registry has no "-q4" suffixed tags for these families
        assert "-q4" not in tag, \
            f"{role} model {tag!r} uses a non-existent -q4 tag"
        assert ":" in tag, f"{role} model {tag!r} missing a :tag"
    # vision model is the qwen2.5vl family, not "qwen2-vl"
    assert "vl" in dm["vision"], \
        f"vision model {dm['vision']!r} does not look like a VL model"


def test_ai_model_config_defaults_match_provider():
    """app.py's config defaults for AI models must not reintroduce the
    bad -q4 tags either."""
    from bulk_downloader import app as a
    cfg = getattr(a, "_app_cfg", None)
    assert cfg is not None, "_app_cfg not found in app module"
    for key in ("ai_model_vision", "ai_model_text"):
        assert key in cfg, f"{key} missing from app config defaults"
        val = cfg[key]
        assert "-q4" not in val, \
            f"{key}={val!r} reintroduces a non-existent Ollama tag"
        assert ":" in val, f"{key}={val!r} missing a :tag"


# ── login templates ─────────────────────────────────────────────────

def test_login_templates_have_distinct_user_pass_selectors():
    """Every login template's user_field and pass_field must have
    distinct PRIMARY selectors. A shared selector means the password
    gets typed into the username box (the Tiny4K bug)."""
    from bulk_downloader.login_templates_data import LOGIN_TEMPLATES
    assert LOGIN_TEMPLATES, "no login templates registered"
    for t in LOGIN_TEMPLATES:
        lg = t.get("login") or {}
        uf = lg.get("user_field") or []
        pf = lg.get("pass_field") or []
        assert uf and pf, \
            f"{t['id']}: missing user or pass selectors"
        assert uf[0] != pf[0], \
            f"{t['id']}: user and pass share primary selector {uf[0]!r}"
        # no selector may appear in BOTH lists
        assert not (set(uf) & set(pf)), \
            f"{t['id']}: selector(s) shared between user and pass"


def test_login_templates_all_have_submit():
    """Every login template needs a submit selector or templated login
    can fill the form but never submit it."""
    from bulk_downloader.login_templates_data import LOGIN_TEMPLATES
    for t in LOGIN_TEMPLATES:
        sb = (t.get("login") or {}).get("submit_btn") or []
        assert sb, f"{t['id']}: no submit_btn selector"


def test_login_templates_lookup_functions_work():
    """list_login_templates / get_login_template must stay consistent."""
    from bulk_downloader.login_templates_data import (
        LOGIN_TEMPLATES, list_login_templates, get_login_template)
    listed = list_login_templates()
    assert len(listed) == len(LOGIN_TEMPLATES)
    for entry in listed:
        full = get_login_template(entry["id"])
        assert full is not None
        assert full["id"] == entry["id"]
    assert get_login_template("login_does_not_exist") is None


# ── live-status endpoints stay GET + JSON ───────────────────────────

def test_status_endpoints_are_get_and_json(initialized_unlocked_vault):
    """The live-monitoring endpoints (/api/health and /api/ai/status)
    must stay GET and must return JSON. If one starts 404ing or
    returns HTML, live monitoring (live_tests/, dashboards, the dev
    surface) breaks silently."""
    client, backend = initialized_unlocked_vault
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is True
    assert backend.list_keys() == [_TEST_CREDENTIAL_KEY]
    health = client.get("/api/health")
    health_body = json.loads(health.data.decode("utf-8"))
    assert health_body["credentials"]["is_initialized"] is True
    assert health_body["credentials"]["is_unlocked"] is True
    assert health.status_code == 200, health_body
    assert health_body["ok"] is True

    ai_status = client.get("/api/ai/status")
    assert ai_status.status_code == 200, \
        f"GET /api/ai/status returned {ai_status.status_code}"
    json.loads(ai_status.data.decode("utf-8"))


def test_locked_master_password_vault_is_a_named_structured_503(
        initialized_unlocked_vault):
    """A restart-locked vault is distinguishable from healthy readiness."""
    client, backend = initialized_unlocked_vault
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is True
    assert backend.list_keys() == [_TEST_CREDENTIAL_KEY]

    locked = client.post("/api/secrets/lock")
    assert locked.status_code == 200, locked.get_json()
    assert locked.get_json() == {"ok": True}
    assert backend.is_initialized() is True
    assert backend.is_unlocked() is False
    assert backend.list_keys() == [_TEST_CREDENTIAL_KEY]

    response = client.get("/api/health")
    body = response.get_json()
    assert body["credentials"]["is_initialized"] is True, body
    assert body["credentials"]["is_unlocked"] is False, body
    assert response.status_code == 503, body
    assert body["ok"] is False, body
    assert body["degraded"] == "credential_vault_locked", body
    assert body["credentials"]["state"] == "locked", body
    assert body["credentials"]["reference_count"] == 1, body
    assert body["credentials"]["stored_count"] == 1, body
    assert body["credentials"]["unavailable_count"] == 1, body
