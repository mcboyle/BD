"""Cockpit shell redesign — Slice 4 (keyboard/a11y) + 5 (presentation) + 6 (polish).

Custom-runner friendly: zero-arg tests, repo root from __file__, structural
text assertions over the server-rendered cockpit blob.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"


def _src():
    return COCKPIT.read_text(encoding="utf-8")


# ── Slice 4: keyboard navigation + focus ring + help overlay ────────────────
def test_focus_ring_present():
    src = _src()
    assert ":focus-visible{outline:2px solid var(--primary)" in src, "no visible focus ring"


def test_nav_made_keyboard_reachable():
    src = _src()
    assert "setAttribute('tabindex','0')" in src, "nav items not made tabbable"
    assert "if(e.key==='Enter'||e.key===' ')" in src, "no Enter/Space activation"


def test_help_overlay_present():
    src = _src()
    assert 'id="help"' in src, "no help overlay (#help)"
    assert "Keyboard shortcuts" in src, "help overlay has no shortcut list"
    assert "if(e.key==='?')" in src, "? does not open help"


# ── Slice 5: skeletons, zebra, sticky headers, empty state, auto theme ──────
def test_skeleton_loaders_replace_loading_text():
    src = _src()
    assert "function skel(" in src and ".skel{" in src, "no skeleton loader"
    assert "m.innerHTML=skel(" in src, "main loader still bare text"
    assert "host.innerHTML=skel(" in src, "tab loader still bare text"


def test_table_zebra_and_sticky_headers():
    src = _src()
    assert "tbody tr:nth-child(even) td" in src, "no zebra rows"
    assert ".viewer thead th{position:sticky" in src, "no sticky headers in viewer"


def test_empty_state_and_filter_helpers():
    src = _src()
    assert "function empty(" in src and ".emptystate" in src, "no empty-state helper"
    assert "function filterList(" in src, "no list-filter helper"


def test_auto_theme_follows_system():
    src = _src()
    assert "'auto'" in src and "Auto (system)" in src, "no Auto theme option"
    assert "prefers-color-scheme: light" in src, "auto theme does not read system pref"


# ── Slice 6: dismissible posture banner + pin-to-Everyday ───────────────────
def test_posture_banner_dismissible():
    src = _src()
    assert "function dismissPosture" in src, "posture banner not dismissible"
    assert "bd_cockpit_posture_dismissed" in src, "dismissal not persisted"


def test_pin_to_everyday_present():
    src = _src()
    assert 'id="pinnedsec"' in src and 'id="pinned"' in src, "no Pinned nav section"
    assert "function" in src and "togglePin" in src, "no pin toggle"
    assert "bd_cockpit_pins" in src, "pins not persisted"
    assert "pinstar" in src, "no pin affordance on nav items"
