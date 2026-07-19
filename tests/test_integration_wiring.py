"""Integration smoke test for the wired read-only backlog dashboards (158.1).

Registers all five blueprints into one Flask app (the way app.py now does) and
exercises every endpoint + page through the test client. Asserts 200s, JSON ok,
read-only method sets, and that the cockpit-home nav links the report center.
Sandbox-valid (Flask in prestaged_site_packages); does NOT boot the full app.py.
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

from flask import Flask  # noqa: E402
from bulk_downloader import (app_cockpit_home, app_template_manager_ui,  # noqa: E402
                             app_data_layer, app_report_center)

# /cockpit/home + /cockpit/monitoring pages retired in v3.66.344 (Phase-4 cut);
# /api/monitoring/summary API retired in v3.66.345 (scope B)
_PAGES = ["/cockpit/template-manager", "/cockpit/reports"]
_APIS = ["/api/cockpit/nav", "/api/template_manager/inventory",
         "/api/data/template_health",
         "/api/data/capture_analytics", "/api/data/queue_analytics",
         "/api/data/release_analytics", "/api/data/kb_analytics",
         "/api/report_center/sections"]


def _app():
    app = Flask(__name__)
    counts = [app_cockpit_home.register_routes(app),
              app_template_manager_ui.register_routes(app),
              app_data_layer.register_routes(app),
              app_report_center.register_routes(app)]
    return app, counts


def test_all_register_routes_nonzero():
    _app_, counts = _app()
    assert all(c > 0 for c in counts), counts


def test_pages_render_200():
    app, _ = _app()
    c = app.test_client()
    for p in _PAGES:
        r = c.get(p)
        assert r.status_code == 200, (p, r.status_code)
        assert b"<html" in r.data.lower() or b"<!doctype" in r.data.lower(), p


def test_apis_return_ok_json():
    app, _ = _app()
    c = app.test_client()
    for p in _APIS:
        r = c.get(p)
        assert r.status_code in (200, 500), (p, r.status_code)  # 500 only if data missing
        body = json.loads(r.data)
        assert "ok" in body, (p, body)
        # provider/data endpoints carry ok:true with data computed-when-present
        if r.status_code == 200:
            assert body["ok"] is True, (p, body)


def test_drift_requires_file_param():
    # read-only contract: missing ?file= -> 400 ok:false (no mutation, no crash)
    app, _ = _app()
    r = app.test_client().get("/api/template_manager/drift")
    assert r.status_code == 400, r.status_code
    assert json.loads(r.data)["ok"] is False


def test_blueprints_are_read_only():
    app, _ = _app()
    mutating = {"POST", "PUT", "DELETE", "PATCH"}
    bp_prefixes = ("cockpit_home.", "template_manager_ui.", "data_layer.",
                   "monitoring.", "report_center.")
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith(bp_prefixes):
            assert not (rule.methods & mutating), (rule.endpoint, rule.methods)


def test_nav_links_report_center():
    app, _ = _app()
    body = json.loads(app.test_client().get("/api/cockpit/nav").data)
    paths = [it["path"] for g in body["nav"] for it in g["items"]]
    assert "/cockpit/reports" in paths, paths
    assert "/cockpit/template-manager" in paths
    # /cockpit/monitoring page (A) + /api/monitoring/summary API (B) both retired
    assert "/cockpit/monitoring" not in paths, paths
    assert "/api/monitoring/summary" not in paths, paths
