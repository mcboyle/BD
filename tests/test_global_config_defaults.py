"""P6-8 — GET /api/global_config/defaults contract.

Backs the SPA "badge non-default settings" feature (Settings item 26): the
Settings page diffs the live /api/global_config values against this frozen
baseline and badges any setting that differs. The baseline is captured from
the _app_cfg seed literal BEFORE _load_app_config() mutates it in place, so it
stays at the shipped defaults regardless of what the operator has saved.

RED-first: the route does not exist yet, so the first two assertions 404 on
pristine source.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_global_config_defaults_route_exists():
    import bulk_downloader.app as a
    c = a.app.test_client()
    r = c.get("/api/global_config/defaults")
    assert r.status_code == 200, f"/api/global_config/defaults -> {r.status_code}"
    body = r.get_json()
    assert isinstance(body, dict), f"expected a dict, got {type(body)}"


def test_global_config_defaults_are_the_shipped_seed_values():
    import bulk_downloader.app as a
    c = a.app.test_client()
    body = c.get("/api/global_config/defaults").get_json()
    # The seed defaults (app.py _app_cfg literal). These must be the SHIPPED
    # values, not whatever the running instance currently has loaded.
    assert body.get("global_max_concurrent") == 0
    assert body.get("ai_enabled") is False
    assert body.get("ai_provider") == "ollama"
    assert body.get("ai_endpoint") == "http://localhost:11434"


def test_global_config_defaults_unaffected_by_runtime_mutation():
    import bulk_downloader.app as a
    c = a.app.test_client()
    # Mutate the live config; the defaults endpoint must keep reporting the
    # frozen baseline (proves it isn't just echoing _app_cfg).
    before = c.get("/api/global_config/defaults").get_json()["ai_enabled"]
    a._app_cfg["ai_enabled"] = not before
    try:
        after = c.get("/api/global_config/defaults").get_json()["ai_enabled"]
        assert after == before, "defaults endpoint must not reflect live mutation"
    finally:
        a._app_cfg["ai_enabled"] = before
