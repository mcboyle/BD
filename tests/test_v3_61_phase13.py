"""v3.61 (Phase 13) — regression tests for the companion-tools work.

Covers the two testable Phase 13 items shipped in v3.61.0:
#27 the /metrics route dedupe, and the tools registry endpoint.
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


# ── #27 /metrics route dedupe ───────────────────────────────────────────

def test_metrics_route_registered_once():
    """There must be exactly one /metrics rule — the duplicate
    api_metrics route was removed in v3.61.0."""
    from bulk_downloader import app as a
    rules = [r for r in a.app.url_map.iter_rules() if str(r) == "/metrics"]
    assert len(rules) == 1
    assert rules[0].endpoint == "metrics_endpoint"


def test_metrics_endpoint_serves_prometheus():
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/metrics")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # Prometheus exposition format markers
    assert "# HELP" in body
    assert "# TYPE" in body


def test_no_dead_api_metrics_function():
    """The dead api_metrics handler should be gone from the source."""
    src = open(os.path.join(_REPO, "bulk_downloader", "app.py"),
               encoding="utf-8").read()
    assert "def api_metrics(" not in src


# ── tools registry ──────────────────────────────────────────────────────

def test_registry_json_is_valid():
    path = os.path.join(_REPO, "tools", "registry.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert "tools" in data
    assert isinstance(data["tools"], list)


def test_tools_endpoint_lists_registered_tools():
    from bulk_downloader import app as a
    c = a.app.test_client()
    body = c.get("/api/tools").get_json()
    assert body["ok"] is True
    assert body["registry"] is True
    ids = {t["id"] for t in body["tools"]}
    # The tools that ship in the repo
    assert "rotate_token" in ids
    assert "validate_sites_config" in ids


def test_tools_endpoint_marks_present_scripts():
    """Tools whose script exists on disk are present=True."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    body = c.get("/api/tools").get_json()
    for t in body["tools"]:
        # All registry entries ship in the repo, so all present
        assert t["present"] is True


def test_tools_endpoint_entries_have_required_fields():
    from bulk_downloader import app as a
    c = a.app.test_client()
    body = c.get("/api/tools").get_json()
    for t in body["tools"]:
        assert t.get("id")
        assert t.get("name")
        assert t.get("description")
        assert t.get("kind")


def test_registry_scripts_actually_exist():
    """Every registry entry with a `script` must point at a real file —
    guards against a registry entry drifting from the tools/ dir."""
    path = os.path.join(_REPO, "tools", "registry.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    tools_dir = os.path.join(_REPO, "tools")
    for entry in data["tools"]:
        script = entry.get("script")
        if script:
            assert os.path.exists(os.path.join(tools_dir, script)), (
                f"registry references missing script: {script}")
