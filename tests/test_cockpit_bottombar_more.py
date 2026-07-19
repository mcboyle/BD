"""Cockpit redesign 351 — Slice C: bottom-bar "More" grouping.

In bottombar only, Advanced + System fold under a single "More" menubar item
(upward dropdown). Structural assertions over the cockpit source.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"


def _src():
    return COCKPIT.read_text(encoding="utf-8")


def test_moresec_markup_present():
    src = _src()
    assert "moresec" in src, "no .moresec group"
    assert 'id="morebody"' in src, "no #morebody dropdown host"


def test_moresec_hidden_except_bottombar():
    src = _src()
    assert ".moresec{display:none}" in src, "moresec not hidden by default"
    assert ".app.bottombar .moresec{display:block}" in src, "moresec not shown in bottombar"


def test_bottombar_folds_advanced_system_into_more():
    src = _src()
    # the top-level Advanced/System drawers are suppressed in bottombar
    assert '.app.bottombar .navdrawer[data-tier="advanced"]' in src, "advanced drawer not folded in bottombar"
    assert '.app.bottombar .navdrawer[data-tier="system"]' in src, "system drawer not folded in bottombar"


def test_more_builder_and_subheads():
    src = _src()
    assert "buildMoreSec" in src, "no buildMoreSec() populate function"
    assert ".moresubhead" in src, "no .moresubhead styling for the More dropdown sub-heads"


def test_safe_area_bottom_padding():
    src = _src()
    assert "safe-area-inset-bottom" in src, "no safe-area-inset-bottom padding for the bottom bar"
