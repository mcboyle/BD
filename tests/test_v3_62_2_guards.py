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


# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

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

def test_status_endpoints_are_get_and_json():
    """The live-monitoring endpoints (/api/health and /api/ai/status)
    must stay GET and must return JSON. If one starts 404ing or
    returns HTML, live monitoring (live_tests/, dashboards, the dev
    surface) breaks silently."""
    from bulk_downloader.app import app
    app.config["TESTING"] = True
    client = app.test_client()
    for path in ("/api/health", "/api/ai/status"):
        r = client.get(path)
        assert r.status_code == 200, \
            f"GET {path} returned {r.status_code}"
        # must be parseable JSON
        json.loads(r.data.decode("utf-8"))
