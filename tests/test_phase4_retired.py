#!/usr/bin/env python3
"""test_phase4_retired.py -- end-state assertions for the Phase-4 retirement,
now extended through the v3.66.353 cleanup cut.

Phase-4 (scope A @344 / scope B @345) retired the redundant server-rendered
Center PAGES and the dead /api/monitoring/summary API while keeping the live
APIs the SPA/console depend on. v3.66.353 finished the job by physically
removing the two now-empty modules (app_actions_center, app_monitoring) from the
source tree; this file asserts both the retired routing AND the removed modules.

run_tests.py conventions: zero-arg test_* functions, plain asserts, no pytest
builtins.
"""
import importlib.util
import json
from pathlib import Path

from flask import Flask

_REPO = Path(__file__).resolve().parent.parent


def _app():
    # Only app_cockpit_home survives among the retired-page blueprints; it
    # serves /api/cockpit/nav (the nav source of truth). The retired pages and
    # APIs are simply unregistered -> any request to them 404s.
    from bulk_downloader.app_cockpit_home import register_routes as _ch
    app = Flask(__name__)
    _ch(app)
    return app


_RETIRED_PAGES = (
    "/cockpit/home",
    "/cockpit/monitoring",
    "/cockpit/actions",
    "/cockpit/actions/library",
    "/cockpit/actions/backup",
    "/cockpit/actions/import_views",
    "/cockpit/actions/tail",
    "/cockpit/actions/site",
    "/cockpit/actions/more",
    "/cockpit/actions/rebalance",
    "/cockpit/actions/imports",
    "/cockpit/actions/site_payload",
)


def test_retired_pages_404():
    c = _app().test_client()
    for p in _RETIRED_PAGES:
        assert c.get(p).status_code == 404, (p, c.get(p).status_code)


def test_retired_modules_removed():
    # v3.66.353: the empty blueprints were physically removed from the tree.
    assert importlib.util.find_spec("bulk_downloader.app_actions_center") is None
    assert importlib.util.find_spec("bulk_downloader.app_monitoring") is None


def test_nav_drops_retired_pages():
    c = _app().test_client()
    body = json.loads(c.get("/api/cockpit/nav").data)
    paths = [it["path"] for g in body["nav"] for it in g["items"]]
    assert "/cockpit/monitoring" not in paths, paths
    assert "/cockpit/actions" not in paths, paths
    assert "/cockpit/home" not in paths, paths


# ---- regression guards: the live surface must survive ----------------------

def test_kept_apis_still_registered():
    c = _app().test_client()
    assert c.get("/api/cockpit/nav").status_code == 200


def test_monitoring_summary_retired():
    assert _app().test_client().get("/api/monitoring/summary").status_code == 404


def test_nav_keeps_live_entries():
    c = _app().test_client()
    body = json.loads(c.get("/api/cockpit/nav").data)
    paths = [it["path"] for g in body["nav"] for it in g["items"]]
    assert "/cockpit/template-manager" in paths, paths
    assert "/cockpit/reports" in paths, paths
    assert "/cockpit/settings" in paths, paths
    assert "/api/monitoring/summary" not in paths, paths


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} passed")
