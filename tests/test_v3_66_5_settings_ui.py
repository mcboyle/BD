"""v3.66.5 — tests for the Settings UI plumbing of
template_auto_detect_mode (the JS modal exposes a Mode dropdown; this
file covers the server-side reads/writes that drive it)."""
import os
import sys

import pytest


# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

def _client():
    from bulk_downloader import app as app_mod
    app_mod._app_cfg["auth_token"] = ""
    return app_mod.app.test_client()


def test_get_global_config_defaults_to_static():
    """First-time read (no value persisted) should default to
    'static' so the UI dropdown always has something selected."""
    client = _client()
    r = client.get("/api/global_config")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("template_auto_detect_mode") == "static"


def test_post_global_config_accepts_static():
    client = _client()
    r = client.post(
        "/api/global_config",
        json={"template_auto_detect_mode": "static"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("template_auto_detect_mode") == "static"


def test_post_global_config_accepts_detect():
    client = _client()
    r = client.post(
        "/api/global_config",
        json={"template_auto_detect_mode": "detect"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200
    assert r.get_json()["template_auto_detect_mode"] == "detect"


def test_post_global_config_accepts_detect_then_static():
    client = _client()
    r = client.post(
        "/api/global_config",
        json={"template_auto_detect_mode": "detect_then_static"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200
    assert (r.get_json()["template_auto_detect_mode"]
            == "detect_then_static")


def test_post_global_config_accepts_deep():
    """The new 'deep' value must round-trip through the endpoint."""
    client = _client()
    r = client.post(
        "/api/global_config",
        json={"template_auto_detect_mode": "deep"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200
    assert r.get_json()["template_auto_detect_mode"] == "deep"


def test_post_global_config_rejects_unknown_mode():
    """Typos / wrong values must NOT silently coerce to 'static' —
    that would mask a UI bug. The server returns 400 with a useful
    error message naming the valid set."""
    client = _client()
    r = client.post(
        "/api/global_config",
        json={"template_auto_detect_mode": "DEEP_DETECT"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 400
    err = r.get_json().get("error", "")
    assert "DEEP_DETECT" in err or "deep_detect" in err.lower()
    assert "deep" in err
    assert "static" in err


def test_post_global_config_normalizes_case():
    """Mixed-case input should normalize to lowercase before
    validation — UIs that accidentally uppercase the value
    shouldn't break."""
    client = _client()
    r = client.post(
        "/api/global_config",
        json={"template_auto_detect_mode": "DEEP"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200
    assert r.get_json()["template_auto_detect_mode"] == "deep"


def test_post_does_not_drop_other_fields():
    """Saving template_auto_detect_mode should NOT clobber unrelated
    config (e.g. global_max_concurrent). Regression guard against
    a refactor that turned the per-key updates into a replace-all."""
    client = _client()
    # Set a value first
    client.post(
        "/api/global_config",
        json={"global_max_concurrent": 8},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    # Now save only the mode
    client.post(
        "/api/global_config",
        json={"template_auto_detect_mode": "deep"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    r = client.get("/api/global_config")
    body = r.get_json()
    assert body["template_auto_detect_mode"] == "deep"
    assert body.get("global_max_concurrent") == 8


