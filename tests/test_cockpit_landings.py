"""Cockpit redesign 351 — Slice B: Advanced / System landing pages.

Under mode/miller, selecting the Advanced or System tier with no specific child
shows a landing (NOT Home). Everyday -> Home. Structural assertions over the
cockpit source (zero-arg, repo root from __file__).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"


def _src():
    return COCKPIT.read_text(encoding="utf-8")


# ── the two landing renderers exist ─────────────────────────────────────────
def test_landing_pages_defined():
    src = _src()
    assert "PAGES.advlanding=" in src, "no PAGES.advlanding renderer"
    assert "PAGES.syslanding=" in src, "no PAGES.syslanding renderer"


def test_advanced_landing_heading_and_sub():
    src = _src()
    assert ">Advanced<" in src, "Advanced landing H1 missing"
    assert "Choose an analysis surface." in src, "Advanced sub missing"


def test_system_landing_heading_and_sub():
    src = _src()
    assert "System Overview" in src, "System landing H1 missing"
    assert "evidence timeline" in src.lower(), "System sub missing"


# ── card targets are REAL page keys, Validation first, Risk Board omitted ───
def test_advanced_cards_validation_first():
    src = _src()
    for k in ("validationc", "insightsc", "driftc", "familiesc", "impactc", "trustc"):
        assert f"card('{k}'" in src, f"Advanced landing missing card {k}"
    # Validation precedes Insights (reco alignment)
    assert src.index("card('validationc'") < src.index("card('insightsc'"), \
        "Validation must be the first Advanced card"


def test_system_cards_map_to_real_pages_and_omit_riskboard():
    src = _src()
    for k in ("investigate", "timeline", "graph", "lessons", "govhealth"):
        assert f"card('{k}'" in src, f"System landing missing card {k}"
    # riskboard has no PAGES renderer -> must be omitted, never invented
    assert "card('riskboard'" not in src, "Risk Board referenced but riskboard page does not exist"


# ── tier -> landing routing (setTier + boot) ────────────────────────────────
def test_settier_routes_to_landings():
    src = _src()
    assert "advlanding" in src and "syslanding" in src, "landing keys not referenced in routing"
    # the routing helper maps tier -> landing
    assert "_tierLanding" in src, "no _tierLanding routing helper"


def test_boot_route_honours_tier():
    """Empty-hash boot under mode/miller + advanced/system goes to the landing,
    not always Home."""
    src = _src()
    assert "_bootRoute" in src, "routeFromHash does not consult a tier-aware boot route"
