"""Cockpit redesign 351 — Slice D: keep favourite stars (clear a11y) AND add a
separate BETA/NEW/PINNED text-badge affordance. The glyph (star) is the toggle;
the badge is a status indicator — never overload one for both.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"


def _src():
    return COCKPIT.read_text(encoding="utf-8")


# ── badge styling exists for all three states ───────────────────────────────
def test_badge_classes_present():
    src = _src()
    assert ".badge{" in src, "no base .badge CSS"
    assert ".badge.pinned" in src, "no .badge.pinned CSS"
    assert ".badge.beta" in src, "no .badge.beta CSS"
    assert ".badge.new" in src, "no .badge.new CSS"


# ── the favourite star is the toggle, with an accessible label ──────────────
def test_pinstar_has_accessible_label():
    src = _src()
    assert "pinstar" in src, "pinstar gone"
    assert "aria-label" in src, "pinstar carries no aria-label"
    # label reflects state, not a generic glyph
    assert "Pin to Everyday" in src, "no Pin label"
    assert "Unpin" in src, "no Unpin label for the pinned state"


# ── badges are a curated, non-invented affordance ───────────────────────────
def test_curated_badge_map_no_invention():
    src = _src()
    assert "NAV_BADGES" in src, "no curated NAV_BADGES map"
    # the one defensible in-code beta surface is Shell (opt-in)
    assert "shell:'beta'" in src or 'shell:"beta"' in src, "Shell (opt-in) not flagged beta"


def test_pinned_badge_is_data_driven_in_more():
    """A PINNED badge appears in the More dropdown for items that are actually
    pinned — driven by pin state, separate from the star toggle."""
    src = _src()
    assert "badge pinned" in src, "no data-driven PINNED badge rendered"
