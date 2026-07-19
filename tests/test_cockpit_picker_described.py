"""Cockpit redesign 351 — Slice A: described-list layout picker + gear a11y.

Custom-runner friendly: zero-arg tests, repo root from __file__, no pytest
builtins. Structural text assertions over the server-rendered cockpit blob.

JS attributes are built via ${...}, so we assert the *builder* (LAYDESC map,
the .opt template carrying role="menuitemradio") and the static popover markup,
never a fully-rendered attribute string like data-l="side".
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"


def _src():
    return COCKPIT.read_text(encoding="utf-8")


# ── the described-list container ────────────────────────────────────────────
def test_laypick_container_present():
    src = _src()
    assert 'class="laypick"' in src, "no .laypick described-list container"
    assert 'id="laypick"' in src, "no #laypick id on the picker host"
    assert ".laypick{" in src, "no .laypick CSS"
    assert ".laypick .opt{" in src, "no .laypick .opt CSS"


def test_laydesc_map_with_six_descriptions():
    src = _src()
    assert "LAYDESC=" in src or "LAYDESC =" in src, "no LAYDESC description map"
    for d in (
        "Best for desktop",
        "Wide-screen compact nav",
        "Icon-first power-user nav",
        "Mobile / tablet friendly",
        "Everyday / Advanced / System",
        "Deep system navigation",
    ):
        assert d in src, f"missing layout description: {d!r}"


def test_opt_rows_are_menuitemradio():
    """Each picker row is a radio menuitem built from LAYOUTS + LAYDESC."""
    src = _src()
    assert 'role="menuitemradio"' in src, "picker rows not role=menuitemradio"
    assert "aria-checked=" in src, "picker rows carry no aria-checked state"
    assert "on-name" in src and "on-desc" in src, "no name/desc row spans"


# ── gear (View settings) a11y ───────────────────────────────────────────────
def test_gear_is_view_settings_with_expanded_state():
    src = _src()
    assert 'aria-label="View settings"' in src, "gear/popover missing View settings label"
    assert 'aria-expanded=' in src, "gear has no aria-expanded state"


def test_appmenu_is_a_menu_role():
    src = _src()
    assert 'id="appmenu"' in src, "appmenu missing"
    # the popover container is a menu with the View settings label
    assert 'role="menu"' in src, "appmenu not role=menu"


def test_escape_and_refocus_close_path():
    """Escape closes the popover and focus returns to the gear button."""
    src = _src()
    assert "Escape" in src, "no Escape-to-close handler"
    assert "gb.focus()" in src or "gear.focus()" in src, "gear is not re-focused on close"


# ── option B: hidden #layout_sel mirror preserved (keeps appearance pin green) ─
def test_layout_sel_mirror_preserved():
    src = _src()
    assert 'id="layout_sel"' in src, "#layout_sel mirror removed (breaks appearance pin)"
