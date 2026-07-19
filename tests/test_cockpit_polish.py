"""Cockpit redesign 351 — optional polish: top/bottom-bar active pill on the
current group, and a uniform .main-inner width wrapper for pages that don't
self-wrap. Structural assertions over the cockpit source.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"


def _src():
    return COCKPIT.read_text(encoding="utf-8")


def test_active_pill_css_top_and_bottom():
    src = _src()
    assert ".app.topnav .navsec.active" in src or ".app.topnav .navdrawer.active" in src, \
        "no top-bar active pill CSS"
    assert ".app.bottombar .navsec.active" in src or ".app.bottombar .navdrawer.active" in src, \
        "no bottom-bar active pill CSS"


def test_go_marks_active_group():
    src = _src()
    assert "classList.add('active')" in src, "go() does not mark the active group"
    # and clears stale active marks
    assert ".active" in src and "remove('active')" in src, "go() does not clear stale active marks"


def test_main_inner_wrap_is_uniform():
    """Pages that don't self-wrap get a .main-inner width cap applied in go()."""
    src = _src()
    assert "indexOf('main-inner')" in src, "go() does not apply a uniform main-inner wrap"
