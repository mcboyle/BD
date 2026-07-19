"""v3.62.3 — AI model dropdown.

The AI-settings model fields used to be free-text inputs, which let the
operator type a model the endpoint did not have (this is how the dead
qwen2-vl:7b-q4 tag got saved). v3.62.3 replaces them with dropdowns
populated from the models the provider actually exposes:

  - aiassist.list_available_models()  — provider-agnostic model lister
  - POST /api/ai/models               — HTTP surface for the UI
  - app.js                            — <select>s + "Other…" escape hatch

These tests pin that behaviour. They run without network: the Ollama
endpoint is unreachable in the sandbox, so the lister must FAIL OPEN
(empty list, ok=False) rather than raise.
"""
import json
import os
import sys

import pytest

# tests/ is not a package; add this file's dir to sys.path so the
# sibling _env helper imports cleanly under both pytest and the
# custom run_tests.py runner.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402


# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

def _app_js():
    import bulk_downloader
    base = os.path.dirname(bulk_downloader.__file__)
    return open(os.path.join(base, "static", "app.js"),
                encoding="utf-8").read()


# ── aiassist.list_available_models ──────────────────────────────────

def test_list_models_shape_and_fail_open():
    """The result shape contract holds in both branches: a dict with
    ok/models/provider/error, where models is a list. The sandbox has
    nothing listening on 11434 -> ok=False, models=[]. A real
    deployment with the ollama systemd unit running -> ok=True with
    a non-empty models list. Both are valid; the SHAPE is what the
    UI depends on, and that must hold either way."""
    from bulk_downloader import aiassist
    res = aiassist.list_available_models(
        provider="ollama", endpoint="http://localhost:11434")
    assert isinstance(res, dict)
    for key in ("ok", "models", "provider", "error"):
        assert key in res, f"result missing {key!r}"
    assert isinstance(res["models"], list)
    assert res["provider"] == "ollama"
    if _env.HAS_OLLAMA_LOCAL:
        # real host: ollama is up; the contract is reachable->ok=True
        # with a populated list, OR a real error from the endpoint
        # (which still fails-open as ok=False but with an error).
        # Either way the shape must hold; do not assert ok=True
        # unconditionally because the model list can legitimately be
        # empty on a fresh install.
        if res["ok"]:
            assert isinstance(res["models"], list)
        else:
            assert isinstance(res["error"], str)
    else:
        # sandbox: nothing listening
        assert res["ok"] is False
        assert res["models"] == []


def test_list_models_rejects_public_ollama_endpoint():
    """The Ollama privacy guard must apply to model listing too — a
    public endpoint is refused before any probe."""
    from bulk_downloader import aiassist
    res = aiassist.list_available_models(
        provider="ollama", endpoint="http://8.8.8.8:11434")
    assert res["ok"] is False
    assert res["models"] == []
    assert "public" in res["error"].lower()


def test_list_models_unknown_provider():
    """An unknown provider name fails cleanly, not with a crash."""
    from bulk_downloader import aiassist
    res = aiassist.list_available_models(
        provider="not_a_provider", endpoint="http://localhost:11434")
    assert res["ok"] is False
    assert "unknown provider" in res["error"].lower()


def test_list_models_claude_has_fixed_list():
    """Claude exposes a fixed model list even with no key — the
    dropdown must still be populated for cloud providers."""
    from bulk_downloader import aiassist
    res = aiassist.list_available_models(
        provider="claude", endpoint="https://api.anthropic.com")
    # ClaudeProvider.list_models() returns its fixed_model_list
    assert res["models"], "Claude should expose its fixed model list"
    assert all(":" not in m or m.startswith("claude")
               for m in res["models"])


# ── POST /api/ai/models route ───────────────────────────────────────

def test_api_ai_models_route_is_post_json():
    """The route the UI calls must accept POST and return JSON with a
    'models' list."""
    from bulk_downloader.app import app
    app.config["TESTING"] = True
    client = app.test_client()
    r = client.post("/api/ai/models",
                     json={"provider": "ollama",
                           "endpoint": "http://localhost:11434"})
    assert r.status_code == 200
    body = json.loads(r.data.decode("utf-8"))
    assert "models" in body and isinstance(body["models"], list)


def test_api_ai_models_handles_masked_key_sentinel():
    """The UI sends '<configured>' for an already-saved key; the route
    must treat it as 'use the stored key', not pass it through."""
    from bulk_downloader.app import app
    app.config["TESTING"] = True
    client = app.test_client()
    r = client.post("/api/ai/models",
                     json={"provider": "claude",
                           "endpoint": "https://api.anthropic.com",
                           "api_key": "<configured>"})
    assert r.status_code == 200
    json.loads(r.data.decode("utf-8"))  # parseable, no crash


# ── app.js: dropdown UI ─────────────────────────────────────────────


