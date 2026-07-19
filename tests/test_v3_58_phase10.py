"""v3.58 (Phase 10) — dashboard + notifications backlog.

Covers the two testable Phase 10 items shipped in v3.58.0:
#133 active-alerts widget, #91 notification presets.
"""
import os
import re
import sys

import pytest


# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe


# ── #133 active-alerts widget ───────────────────────────────────────────

def test_active_alerts_in_python_set():
    from bulk_downloader import widgets_config
    assert "active_alerts" in widgets_config.VALID_WIDGET_IDS


def test_active_alerts_collector_value():
    """Clean install → 0 active alerts, no crash."""
    from bulk_downloader import app as a
    from bulk_downloader.app_widgets_api import _collect_data
    d = _collect_data(None)
    assert d.get("active_alerts", 0) == 0


def test_active_alerts_in_widgets_endpoint():
    from bulk_downloader import app as a
    c = a.app.test_client()
    d = c.get("/api/widgets/data").get_json()["data"]
    assert "active_alerts" in d


# ── #91 notification presets ────────────────────────────────────────────

def test_notify_presets_endpoint_shape():
    from bulk_downloader import app as a
    c = a.app.test_client()
    body = c.get("/api/notify/presets").get_json()
    assert body["ok"] is True
    assert "presets" in body
    assert len(body["presets"]) >= 4


def test_notify_presets_cover_common_services():
    from bulk_downloader import app as a
    c = a.app.test_client()
    body = c.get("/api/notify/presets").get_json()
    ids = {p["id"] for p in body["presets"]}
    for expected in ("pushover", "discord", "slack", "telegram"):
        assert expected in ids


def test_notify_preset_template_placeholders_match_fields():
    """Every {placeholder} in a preset template must have a matching
    field — otherwise the UI can't fill it in."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    body = c.get("/api/notify/presets").get_json()
    for preset in body["presets"]:
        placeholders = set(re.findall(r"\{([a-z_]+)\}", preset["template"]))
        field_keys = {f["key"] for f in preset["fields"]}
        assert placeholders == field_keys, (
            f"{preset['id']}: template {placeholders} != "
            f"fields {field_keys}")


def test_notify_preset_fields_have_labels_and_hints():
    from bulk_downloader import app as a
    c = a.app.test_client()
    body = c.get("/api/notify/presets").get_json()
    for preset in body["presets"]:
        assert preset.get("name")
        assert preset.get("docs", "").startswith("http")
        for field in preset["fields"]:
            assert field.get("label")
            assert field.get("hint")
