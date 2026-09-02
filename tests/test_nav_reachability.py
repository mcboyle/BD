"""v3.66.200 — nav reachability gate.

Every page must be reachable by clicks from `/`. Born from the v3.66.199
orphaned-pages MAX audit: from `/`, exactly one page was reachable (itself),
and 13/26 D3 SPA routes had no inbound link. See docs/NAV_CONSOLIDATION.md.

The gate has two independent halves so a failure pinpoints the surface:
  * SPA static audit — cheap, no app boot.
  * Server crawl — boots the real app once and BFS-crawls rendered <a href>.
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="bd_navreach_test_"))

import importlib.util  # noqa: E402

from flask import Flask

_spec = importlib.util.spec_from_file_location(
    "nav_reachability", _ROOT / "tools" / "nav_reachability.py")
nav_reachability = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nav_reachability)


def test_spa_routes_all_have_inbound_links():
    orphans = nav_reachability.check_spa(verbose=False)
    assert orphans == [], "SPA nav orphans:\n  " + "\n  ".join(orphans)


def test_server_pages_all_reachable_from_root():
    orphans = nav_reachability.check_server(verbose=False)
    assert orphans == [], "Server nav orphans:\n  " + "\n  ".join(orphans)


def test_external_nav_entries_present():
    """v3.66.506 — /framework, /fleet, /cockpit are server-rendered (not React
    routes); they must be surfaced as external:true nav entries in navGroups.ts so
    a click path to each server console exists. RED before the navGroups entries
    land, GREEN after."""
    missing = nav_reachability.check_external_nav(verbose=False)
    assert missing == [], "External nav orphans:\n  " + "\n  ".join(missing)


def test_rendered_server_links_resolve_in_flask_or_the_spa():
    """Every currently served internal anchor needs a real routing owner."""
    findings = nav_reachability.check_outbound_internal_links(verbose=False)
    assert findings == [], "Unresolved rendered internal links:\n  " + "\n  ".join(findings)


def test_outbound_links_accept_explicit_server_and_spa_routes():
    """The outbound checker must accept both routing domains.

    Flask's root catch-all serves the SPA document but cannot prove that React
    has a route for a target.  This fixture gives the checker one real Flask
    target and one declared SPA target so a replacement that accepts only one
    domain is caught.
    """
    app = Flask(__name__)

    @app.route("/source")
    def source():
        return ('<a href="/server">server</a><a href="/spa">spa</a>'
                '<a href="/slash">slash redirect</a>')

    @app.route("/server")
    def server():
        return "server"

    @app.route("/slash/")
    def slash():
        return "slash"

    assert nav_reachability.check_outbound_internal_links(
        app=app, spa_routes={"/spa"}, verbose=False) == []


def test_outbound_links_catch_a_rendered_relative_dead_link():
    """A browser-resolved relative breadcrumb is the row-113 failure shape."""
    app = Flask(__name__)

    @app.route("/cockpit/settings")
    def settings():
        return '<a href="home">Cockpit Home</a>'

    checker = getattr(nav_reachability, "check_outbound_internal_links", None)
    assert callable(checker), (
        "nav reachability has no rendered outbound-link checker"
    )
    findings = checker(
        app=app, spa_routes=set(), verbose=False)

    retired = "/cockpit/" + "home"
    assert findings == [
        f"OUTBOUND unresolved: /cockpit/settings href='home' resolves to {retired}"
    ]


def test_outbound_links_refuse_an_empty_rendered_page_denominator():
    """No source pages means the route claim is unmeasured, not clean."""
    findings = nav_reachability.check_outbound_internal_links(
        app=Flask(__name__), spa_routes=set(), verbose=False)

    assert findings == ["OUTBOUND uncheckable: no static server GET pages"]
