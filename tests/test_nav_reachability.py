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
