"""Cockpit shell redesign — Slice 1 structural invariants.

Custom-runner friendly: zero-arg test functions, repo root derived from
__file__, no pytest builtins. Reads tools/cockpit_console.py as text (the
cockpit is a DEPLOY-EXCLUDED server-rendered HTML/CSS/JS blob, so structural
text assertions are the gate). Pins the resizable + collapsible sidebar,
the compact brand + appearance popover, horizontal tiers, the focus fix,
and the density toggle — without disturbing the appearance-test guardrails.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"


def _src():
    return COCKPIT.read_text(encoding="utf-8")


# ── resizable sidebar ────────────────────────────────────────────────────────
def test_sidebar_width_is_a_css_var():
    src = _src()
    assert "var(--side-w" in src, "sidebar column must read --side-w (resizable)"
    assert 'id="resizer"' in src, "no resize handle (#resizer)"
    assert "bd_cockpit_sidew" in src, "resized width not persisted"


# ── collapsible sidebar (also the focus fix) ─────────────────────────────────
def test_collapse_primitive_present():
    src = _src()
    assert 'id="collapsebtn"' in src, "no collapse control (#collapsebtn)"
    assert 'id="reexpand"' in src, "no re-expand control when collapsed (#reexpand)"
    assert "bd_cockpit_collapsed" in src, "collapse state not persisted"
    assert "function setCollapsed" in src, "no setCollapsed primitive"


def test_collapse_beats_layout_classes():
    """The collapse rule must out-specify every layout class (the focus-bug
    fix): a doubled-class selector (.app.app[data-collapsed]) is 0,3,0 and
    beats .app.miller (0,2,0)."""
    src = _src()
    assert '.app.app[data-collapsed="1"]' in src, \
        "collapse rule not specificity-hardened against layout classes"


def test_collapse_uses_single_column_not_zero_fr():
    """Regression (shipped-broken in 346/347): collapse must use a SINGLE-column
    grid (1fr). With '0 1fr' + a display:none sidebar, main becomes the first
    grid item and lands in the 0-width column -> squished to a sliver with a
    mid-screen scrollbar. Caught only by render validation, pinned here too."""
    src = _src()
    assert '.app.app[data-collapsed="1"]{grid-template-columns:1fr}' in src, \
        "collapse must be a single 1fr column"
    assert '.app.app[data-collapsed="1"]{grid-template-columns:0 1fr}' not in src, \
        "collapse still uses the broken 0 1fr grid (squishes main)"


def test_focus_key_routes_through_collapse():
    """The `f` shortcut must drive the collapse primitive (works in every
    layout), not the old additive .focus class that lost to .miller."""
    src = _src()
    assert "if(e.key==='f'){const a=$('.app');if(a)setCollapsed(" in src, \
        "f-key no longer routes through setCollapsed"


def test_focus_layout_trap_removed():
    """Regression (shipped-broken 346-348): the selectable 'Focus' LAYOUT used
    .app.focus{grid-template-columns:0 1fr} and hid the sidebar -> squished main
    AND no exit (the layout switcher lives in the now-hidden sidebar, and the
    re-expand button only shows for data-collapsed). The Focus layout is removed;
    focus-via-collapse (the `f` key) remains the supported path. A persisted
    'focus' value must fall back to the Sidebar layout on load."""
    src = _src()
    assert "['focus','Focus']" not in src, "Focus is still a selectable layout"
    assert ".app.focus{grid-template-columns:0 1fr}" not in src, "broken .app.focus rule still present"
    assert "if(!LAYOUTS.some(x=>x[0]===L)){L='side'" in src, \
        "missing stale-layout recovery guard (stuck users won't auto-recover)"


# ── compact brand + appearance popover (selects relocated, IDs preserved) ────
def test_appearance_popover_holds_pickers():
    src = _src()
    assert 'id="appmenu"' in src, "no appearance popover (#appmenu)"
    assert 'id="gearbtn"' in src, "no gear control to open appearance (#gearbtn)"
    # the pinned pickers must still exist (relocated, not removed)
    assert 'id="layout_sel"' in src and 'id="theme_sel"' in src, \
        "layout/theme pickers lost in the brand restructure"


# ── horizontal tier segmenter (kills Miller's vertical 104px column) ─────────
def test_miller_tier_is_horizontal():
    src = _src()
    assert ".app.miller .tierseg{display:flex;flex-direction:row" in src, \
        "Miller tier segmenter is not horizontal"


# ── density toggle ───────────────────────────────────────────────────────────
def test_density_toggle_present():
    src = _src()
    assert 'id="density_seg"' in src, "no density toggle (#density_seg)"
    assert "bd_cockpit_density" in src, "density choice not persisted"
    assert ".app.compact" in src, "no compact-density CSS"
