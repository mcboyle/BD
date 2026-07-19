"""Endpoint + render tests for the additive cockpit landing page (#1 / P4).

Additive only; the existing sidebar is untouched. NEEDS OPERATOR CLICK-THROUGH
VALIDATION for the live page. Runs under run_tests.py.
"""
import sys
from pathlib import Path

from flask import Flask

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from bulk_downloader.app_cockpit_home import register_routes, NAV  # noqa: E402


def _client():
    app = Flask(__name__)
    register_routes(app)
    return app.test_client()


def test_register_routes_adds_one():
    # v3.66.344 (Phase-4 retired): /cockpit/home page removed; only the
    # /api/cockpit/nav API remains (the nav source of truth).
    app = Flask(__name__)
    assert register_routes(app) == 1


def test_nav_api_groups():
    data = _client().get("/api/cockpit/nav").get_json()
    assert data["ok"] is True
    groups = {g["group"] for g in data["nav"]}
    assert {"Templates", "Monitoring", "Dev / release"} <= groups, groups


def test_home_page_retired_404():
    # v3.66.344 (Phase-4 retired cut): the server-rendered /cockpit/home landing
    # page was removed. /api/cockpit/nav (covered by test_nav_api_groups) remains
    # the nav source of truth.
    assert _client().get("/cockpit/home").status_code == 404


def test_nav_structure_well_formed():
    for g in NAV:
        assert "group" in g and "items" in g
        for it in g["items"]:
            assert {"label", "path", "kind"} <= set(it)
