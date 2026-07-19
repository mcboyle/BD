"""Cockpit bar-layout dropdowns — portal contract (v3.66.353, supersedes 352).

History: v3.66.352 made the bar `.side` `overflow:visible` so its
position:absolute nav dropdowns and the gear popover could escape the thin bar.
That stopgap had a cost (the deferred 352 item): with overflow:visible the bar
could not scroll horizontally, so below ~900px the nav overflowed instead of
scrolling. v3.66.353 is the proper fix: the bar dropdowns and the gear popover
are now position:FIXED and JS-positioned from the trigger's rect, so the bar can
be `overflow-x:auto` (horizontal scroll at narrow widths) while the menus still
escape the bar's clip box. They also open on tap (the navhead/drawerhead click
branches to the dropdown in a bar layout), not just CSS :hover.

Custom-runner friendly: zero-arg test functions, repo root from __file__, reads
tools/cockpit_console.py as text (deploy-excluded server-rendered blob). The live
geometry gate is render_check.py section [6] + the narrow-width probe (chromium).
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"


def _src():
    return COCKPIT.read_text(encoding="utf-8")


def _rule(src, selector_prefix):
    m = re.search(re.escape(selector_prefix) + r"\{[^}]*\}", src)
    return m.group(0) if m else None


def test_topnav_side_scrolls_horizontally():
    """Top bar .side must allow horizontal scroll (overflow-x:auto) — the
    portal dropdowns escape via position:fixed, so the bar no longer needs
    overflow:visible (which disabled narrow-width scroll)."""
    rule = _rule(_src(), ".app.topnav .side")
    assert rule, "could not find .app.topnav .side rule"
    assert "overflow-x:auto" in rule, "top bar .side must use overflow-x:auto; found: " + rule
    assert "overflow:visible" not in rule, "top bar .side must NOT use overflow:visible (kills scroll): " + rule


def test_bottombar_side_scrolls_horizontally():
    rule = _rule(_src(), ".app.bottombar .side")
    assert rule, "could not find .app.bottombar .side rule"
    assert "overflow-x:auto" in rule, "bottombar .side must use overflow-x:auto; found: " + rule
    assert "overflow:visible" not in rule, "bottombar .side must NOT use overflow:visible: " + rule


def test_bar_dropdowns_are_fixed_portals():
    """The bar dropdowns must be position:fixed (escape the bar's overflow clip)
    and driven by the JS .baropen class (not a pure CSS :hover, which never fires
    on a real touch tap)."""
    src = _src()
    for prefix in (".app.topnav .navsec .navitems,.app.topnav .navdrawer .drawerbody",
                   ".app.bottombar .navsec .navitems,.app.bottombar .navdrawer .drawerbody"):
        rule = _rule(src, prefix)
        assert rule, "missing bar dropdown rule: " + prefix
        assert "position:fixed" in rule, "bar dropdown must be position:fixed; found: " + rule
    # .baropen drives visibility (JS-controlled, tap-capable)
    assert ".navsec.baropen .navitems" in src and ".navdrawer.baropen .drawerbody" in src, \
        "bar dropdowns must be shown via the .baropen class"
    # the old pure-CSS :hover reveal must be gone for the bars
    assert ".app.topnav .navsec:hover .navitems" not in src, "stale topnav :hover reveal still present"
    assert ".app.bottombar .navsec:hover .navitems" not in src, "stale bottombar :hover reveal still present"


def test_bar_dropdown_controller_present():
    """The JS controller that positions the fixed dropdowns and adds tap support."""
    src = _src()
    for token in ("function openBarDropdown", "function wireBarDropdowns",
                  "function _isBarLayout", "function closeBarDropdowns",
                  "toggleBarDropdown"):
        assert token in src, "missing bar-dropdown controller token: " + token
    # the navhead click branches to the dropdown in a bar layout (tap-to-open)
    assert "if(_isBarLayout()){toggleBarDropdown(sec)" in src, \
        "navhead click must open the dropdown in a bar layout (tap support)"


def test_gear_popover_fixed_in_bar_layouts():
    """The gear #appmenu is also position:absolute and would be clipped by the
    now-scrolling bar; it must be repositioned fixed in bar layouts (placeMenu)."""
    src = _src()
    assert "placeMenu" in src, "missing gear-popover placeMenu helper"
    assert "am.style.position='fixed'" in src, "gear popover must go position:fixed in bar layouts"


def test_other_layouts_untouched():
    """Guard against scope creep: side/rail/mode/miller get no new overflow override."""
    src = _src()
    for bad in (".app.rail .side{overflow:visible",
                ".app.mode .side{overflow:visible",
                ".app.miller .side{overflow:visible"):
        assert bad not in src, "unexpected overflow override on " + bad


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} passed")
