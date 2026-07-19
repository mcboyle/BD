"""696 (v3.66.696) -- wire a "Check JD coverage" button on SiteActions.

JD-3 (694) shipped GET /api/sites/<sid>/jd/coverage backend-only, so the parity
scanner read it spa_wired:false (no SPA surface). This cut adds a
"Check JD coverage" entry to SiteActions' INTEGRATION_CHECKS array -- a
read-only GET that mirrors the existing "Test JDownloader" (jd/diagnose) button
-- so an operator can check coverage from the UI. Wired with the FULL
/api/sites/${...}/jd/coverage literal so gui_parity credits it spa_wired.

RED-first on pristine v3.66.695: /jd/coverage is NOT referenced in
SiteActions.tsx -> the source-literal test RED, and gui_parity reads the
endpoint spa_wired:false -> the parity test RED.
"""
from pathlib import Path

import tools.gui_parity_inventory as g

_REPO = Path(__file__).resolve().parent.parent
_COVERAGE = "GET /api/sites/<sid>/jd/coverage"


def _wired(items, ce):
    for it in items:
        if it.get("command_or_endpoint") == ce:
            return it.get("spa_wired")
    raise AssertionError("inventory item not found: " + ce)


def test_jd_coverage_endpoint_is_spa_wired():
    inv = g.build(str(_REPO))
    assert _wired(inv["items"], _COVERAGE) is True, \
        "jd/coverage still spa_wired:false -> add the SiteActions button"


def test_site_actions_references_jd_coverage_literal():
    src = (_REPO / "frontend" / "src" / "routes" / "SiteActions.tsx").read_text()
    assert "/jd/coverage" in src, "SiteActions must call the full /jd/coverage literal"
    # mirrors the sibling jd/diagnose button label style
    assert "Check JD coverage" in src or "JD coverage" in src


def test_jd_coverage_is_read_only_get():
    """The coverage check is a GET (read-only), like the other diagnose
    buttons -- it must not appear as a mutating/confirm-gated action."""
    inv = g.build(str(_REPO))
    for it in inv["items"]:
        if it.get("command_or_endpoint") == _COVERAGE:
            assert it.get("method", "GET").upper() == "GET"
            break
    else:
        raise AssertionError("coverage endpoint missing from inventory")
