"""Row 113 remainder (BRIEF-ROW113-REMAINDER, bd-pm-C-B): the retired /cockpit/home renderers are gone.

v3.66.344 retired the server-rendered /cockpit/home page, but bulk_downloader/app_cockpit_home.py kept its two HTML
builders, _item_html and _group_html, "as dead code for a later cleanup cut". They were called by nothing (repo
grep: only each other), and _item_html still emitted <a href> anchors from NAV paths -- the page shape row 113 was
about. The nav source of truth, /api/cockpit/nav, stays.
"""

from __future__ import annotations

import ast
from pathlib import Path

from flask import Flask

from bulk_downloader import app_cockpit_home

BD_GATE_SCOPE = "module"

SRC = Path(app_cockpit_home.__file__)


def _defined_functions() -> set[str]:
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def test_retired_cockpit_home_renderers_are_removed() -> None:
    left = {"_item_html", "_group_html"} & _defined_functions()
    assert not left, (
        f"ROW113_DEAD_RENDERERS: retired /cockpit/home HTML builders still defined: {sorted(left)}"
    )
    assert "<a href=" not in SRC.read_text(encoding="utf-8"), (
        "ROW113_DEAD_RENDERERS: app_cockpit_home.py still builds an anchor for the retired page"
    )


def test_control_nav_api_still_served() -> None:
    assert {"api_cockpit_nav", "register_routes"} <= _defined_functions()
    app = Flask(__name__)
    assert app_cockpit_home.register_routes(app) == 1
    with app.test_client() as client:
        body = client.get("/api/cockpit/nav").get_json()
    assert (
        body["ok"] is True
        and body["nav"] == app_cockpit_home.NAV
        and len(body["nav"]) > 0
    )
