"""G7/G9/G4 read-only status surfaces (browser/Cloak + deploy health + DOM recorder).

Verifies the three additive data-layer collectors and their routes: shape,
HTTP 200/ok, and the posture invariant that no secret-ish values leak.
Additive/read-only — no mutation, no new behaviour on existing endpoints.
"""
import json
import re

from flask import Flask

from bulk_downloader import app_data_layer as DL

_LEAK = re.compile(r"eyJ[\w-]{8,}|token=|[\w.+-]+@[\w.-]+\.\w{2,}")


def test_browser_status_shape():
    st = DL.collect_browser_status()
    for k in ("available", "version", "import_error", "resolved_backend",
              "display_set"):
        assert k in st, k
    assert isinstance(st["available"], bool)
    assert st["resolved_backend"] in ("cloakbrowser", "playwright", "unknown")


def test_deploy_health_shape():
    h = DL.collect_deploy_health()
    for k in ("app_version", "deployed_version_marker", "frontend_dist_present",
              "rrweb_vendored", "snapdom_vendored"):
        assert k in h, k
    assert isinstance(h["frontend_dist_present"], bool)
    # running in the source tree, the app version must resolve (not "unknown")
    assert h["app_version"] and h["app_version"] != "unknown"


def test_dom_recorder_status_shape():
    st = DL.collect_dom_recorder_status()
    assert isinstance(st, dict)
    assert "available" in st
    if not st.get("available"):
        assert "error" in st
        return
    for k in ("rrweb_present", "snapdom_present", "rrweb_bytes", "snapdom_bytes",
              "dom_events_dropped", "arm_fail_streak", "vendor_complete", "health"):
        assert k in st, k
    assert isinstance(st["rrweb_present"], bool)
    assert isinstance(st["snapdom_present"], bool)
    assert isinstance(st["vendor_complete"], bool)
    assert st["health"] in ("ok", "degraded", "error")
    assert isinstance(st["dom_events_dropped"], int)
    assert isinstance(st["arm_fail_streak"], int)


def test_dom_recorder_status_health_logic():
    """Health label is derived correctly from the counter fields."""
    import unittest.mock as mock
    base = {
        "rrweb_present": True, "rrweb_bytes": 100,
        "snapdom_present": True, "snapdom_bytes": 100,
        "dom_events_dropped": 0, "arm_fail_streak": 0,
    }
    with mock.patch("bulk_downloader.dom_recorder.get_status", return_value=dict(base)):
        st = DL.collect_dom_recorder_status()
    assert st["health"] == "ok"
    assert st["vendor_complete"] is True

    with mock.patch("bulk_downloader.dom_recorder.get_status",
                    return_value={**base, "dom_events_dropped": 5}):
        st = DL.collect_dom_recorder_status()
    assert st["health"] == "degraded"

    with mock.patch("bulk_downloader.dom_recorder.get_status",
                    return_value={**base, "arm_fail_streak": 5}):
        st = DL.collect_dom_recorder_status()
    assert st["health"] == "degraded"

    with mock.patch("bulk_downloader.dom_recorder.get_status",
                    return_value={**base, "rrweb_present": False}):
        st = DL.collect_dom_recorder_status()
    assert st["health"] == "error"
    assert st["vendor_complete"] is False


def test_status_routes_ok():
    app = Flask(__name__)
    DL.register_routes(app)
    c = app.test_client()
    for ep in ("/api/data/browser_status", "/api/data/deploy_health",
               "/api/data/dom_recorder_status"):
        r = c.get(ep)
        assert r.status_code == 200, ep
        body = r.get_json()
        assert body["ok"] is True, ep
        assert "data" in body


def test_status_posture_no_secret_values():
    blob = json.dumps(DL.collect_browser_status())
    blob += json.dumps(DL.collect_deploy_health())
    blob += json.dumps(DL.collect_dom_recorder_status())
    assert not _LEAK.search(blob), "status output must not carry secret-ish values"


def test_workflow_analytics_shape():
    wa = DL.collect_workflow_analytics()
    assert isinstance(wa, dict)
    for k in ("total_drafts", "with_workflow_data", "with_trigger_candidate", "templates"):
        assert k in wa, k
    assert isinstance(wa["templates"], list)
    assert wa["total_drafts"] == len(wa["templates"])
    for row in wa["templates"]:
        for f in ("host", "status", "bucket", "has_workflow", "derived_steps",
                  "trigger_candidate", "trigger_evidence", "confidence"):
            assert f in row, f
        assert isinstance(row["derived_steps"], list)
        assert isinstance(row["has_workflow"], bool)


def test_workflow_analytics_route_ok():
    app = Flask(__name__)
    DL.register_routes(app)
    c = app.test_client()
    r = c.get("/api/data/workflow_analytics")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert "data" in body


def test_workflow_analytics_posture():
    wa = DL.collect_workflow_analytics()
    blob = json.dumps(wa)
    assert not _LEAK.search(blob), "workflow analytics must not carry secret-ish values"


def test_capture_analytics_g3_fields():
    """G3: capture_analytics returns the full capture-result field set."""
    ca = DL.collect_capture_analytics()
    assert "capture_summary" in ca
    cs = ca["capture_summary"]
    for k in ("json_count", "wacz_count", "with_dom", "with_ws",
              "with_wacz_sibling", "capture_kinds"):
        assert k in cs, k
    # Each artifact item has the G3 fields
    for item in ca["artifacts"].get("items", []):
        assert "type" in item
        assert "capture_kind" in item or item.get("type") == "wacz"


def test_vpn_status_shape():
    v = DL.collect_vpn_status()
    assert isinstance(v, dict)
    assert "available" in v
    if not v.get("available"):
        assert "error" in v
        return
    for k in ("tunnel_count", "provider_count", "providers",
              "system_killswitch_available", "active_killswitches"):
        assert k in v, k
    assert isinstance(v["tunnel_count"], int)
    assert isinstance(v["providers"], list)
    assert isinstance(v["system_killswitch_available"], bool)


def test_secrets_status_shape():
    s = DL.collect_secrets_status()
    assert isinstance(s, dict)
    assert "available" in s
    if not s.get("available"):
        assert "error" in s
        return
    for k in ("backend", "is_unlocked", "credential_count", "credentials_enumerable"):
        assert k in s, k
    assert isinstance(s["backend"], str)
    assert isinstance(s["is_unlocked"], bool)


def test_secrets_status_no_values():
    """Posture: secrets_status must never expose key names or secret material."""
    s = DL.collect_secrets_status()
    blob = json.dumps(s)
    # credential_count is an int; the keys list must never appear
    assert "bulkdl-site-" not in blob, "secret key names must not leak"
    assert not _LEAK.search(blob)


def test_vpn_secrets_routes_ok():
    app = Flask(__name__)
    DL.register_routes(app)
    c = app.test_client()
    for ep in ("/api/data/vpn_status", "/api/data/secrets_status"):
        r = c.get(ep)
        assert r.status_code == 200, ep
        body = r.get_json()
        assert body["ok"] is True, ep
        assert "data" in body
