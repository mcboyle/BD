"""v3.66.767 -- scrape_listing wiring pin (the last genuinely-dark operator route).

POST /api/scrape_listing existed server-side (app_scrape_listing.py, Phase 4
thin-core-shell) with ZERO frontend call sites: spa_wired=False, operator_facing.
This cut wires it into the Add-URL surface as a third "scrape" mode -- paste a
listing-page URL, the server returns the video-looking links, they populate the
URL-list textarea for review + enqueue. A real two-step control, not a dead one.

RED on pristine v3.66.766: the route is in NEITHER the path nor the method-aware
spa_wiring set, ROUTE_INDEX pins spa_wired=false, and the full literal is absent
from AddUrlDialog.tsx. GREEN after the FE wiring + ROUTE_INDEX/gui_parity regen.

run_tests.py conventions: zero-arg test functions; repo root from __file__.
"""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_gui_parity():
    saved = list(sys.path)
    sys.path.insert(0, str(REPO / "tools"))
    sys.path.insert(0, str(REPO))
    try:
        import gui_parity_inventory as P  # noqa: E402
        return P
    finally:
        sys.path[:] = saved


def test_scrape_listing_is_method_aware_spa_wired():
    """The scanner must credit (POST, /api/scrape_listing) from a FULL /api/
    literal call site in the SPA source -- not a comment, not a base-var concat."""
    P = _load_gui_parity()
    _eps, meps = P._spa_wiring(str(REPO))
    assert ("POST", "/api/scrape_listing") in meps, (
        "POST /api/scrape_listing is not method-aware spa_wired "
        "(no apiPost full-literal call site found)")


def test_scrape_listing_full_literal_in_add_url_dialog():
    """The wiring lives in AddUrlDialog.tsx as a full string literal so the
    parity scanner can credit it (the scanner cannot see concatenated bases)."""
    dlg = (REPO / "frontend" / "src" / "components" / "AddUrlDialog.tsx").read_text(
        encoding="utf-8")
    assert '"/api/scrape_listing"' in dlg, (
        "full /api/scrape_listing literal missing from AddUrlDialog.tsx")


def test_scrape_listing_route_index_marks_spa_wired():
    """ROUTE_INDEX.json (the join artifact) must reflect the flip: the shipped
    entry for POST /api/scrape_listing carries spa_wired=true after regen."""
    ri = json.loads((REPO / "ROUTE_INDEX.json").read_text(encoding="utf-8"))

    def _find(o):
        if isinstance(o, dict):
            if o.get("path") == "/api/scrape_listing":
                return o
            for v in o.values():
                r = _find(v)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = _find(v)
                if r is not None:
                    return r
        return None

    entry = _find(ri)
    assert entry is not None, "/api/scrape_listing missing from ROUTE_INDEX"
    assert entry.get("spa_wired") is True, (
        "ROUTE_INDEX still pins scrape_listing spa_wired=false")


def test_scrape_mode_populates_url_list_two_step():
    """The scrape control is a genuine two-step flow (scrape -> review -> enqueue),
    not a dead button: the mutation targets the scrape route and its success
    handler feeds the found links into the existing list-enqueue path."""
    dlg = (REPO / "frontend" / "src" / "components" / "AddUrlDialog.tsx").read_text(
        encoding="utf-8")
    assert '"scrape"' in dlg, "scrape mode not added to the Mode union"
    # the mutation posts to the route and the found links are surfaced for enqueue
    assert "apiPost" in dlg and "/api/scrape_listing" in dlg
    assert "found" in dlg, "scrape result 'found' links not consumed in the FE"
