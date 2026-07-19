"""Mobile tap-to-open off-canvas drawer — affordance contract (v3.66.354).

Background (the deferred 353 sub-item): below 820px a global responsive rule
turns `.side` into a `position:fixed` off-canvas drawer (`transform:translateX(-100%)`,
`.app.navopen .side{translateX(0)}`) and shows a fixed `#mobilebar`. But the
mechanism was HALF-BUILT: the CSS existed while (a) no `#mobilebar` element was
ever rendered and (b) nothing ever toggled the `navopen` class — so on a phone
the sidebar slid off-screen with no way to bring it back. v3.66.354 wires the
affordance: a rendered `#mobilebar` with a hamburger toggle, a `navopen` toggle
in JS, a tap-outside scrim, Escape-to-close, close-on-nav-tap, and a resize guard
that clears `navopen` when the viewport widens back past the breakpoint.

Custom-runner friendly: zero-arg test functions, repo root from __file__, reads
tools/cockpit_console.py as text (deploy-excluded server-rendered blob). The live
geometry/clickability gate is render_check.py + the chromium runtime probe.
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


def test_mobilebar_element_is_rendered():
    """A #mobilebar element must actually exist in the served HTML (not just CSS)."""
    src = _src()
    assert re.search(r'id=["\']mobilebar["\']', src), \
        "#mobilebar element is not rendered in the cockpit HTML"


def test_mobilebar_has_hamburger_toggle():
    """The mobilebar must carry an accessible menu/hamburger toggle button."""
    src = _src()
    # the toggle is identifiable by id and must be labelled for screen readers
    assert re.search(r'id=["\']navtoggle["\']', src), \
        "mobile hamburger toggle (#navtoggle) is missing"
    m = re.search(r'<button[^>]*id=["\']navtoggle["\'][^>]*>', src)
    assert m and "aria-label" in m.group(0), \
        "the #navtoggle button must have an aria-label"


def test_js_toggles_navopen():
    """JS must add/remove the `navopen` class on `.app` (open the drawer)."""
    src = _src()
    assert re.search(r"classList\.(toggle|add)\(\s*['\"]navopen['\"]", src), \
        "no JS toggles the 'navopen' class — the drawer can never open"


def test_scrim_closes_drawer_on_tap_outside():
    """A backdrop/scrim must exist and close the drawer when tapped."""
    src = _src()
    assert re.search(r'id=["\']navscrim["\']', src), \
        "tap-outside scrim (#navscrim) is missing"
    # the scrim must be wired to remove navopen (close)
    assert re.search(r"classList\.remove\(\s*['\"]navopen['\"]", src), \
        "nothing removes 'navopen' — the drawer cannot be closed"


def test_scrim_has_css_rule():
    """The scrim needs a CSS rule (hidden by default, shown when navopen)."""
    src = _src()
    assert _rule(src, "#navscrim"), "#navscrim has no CSS rule"
    assert _rule(src, ".app.navopen #navscrim"), \
        ".app.navopen #navscrim (visible-when-open) rule is missing"


def test_escape_closes_drawer():
    """Escape must close the mobile drawer (keyboard parity with the scrim)."""
    src = _src()
    # an Escape handler that clears navopen
    assert re.search(r"Escape[\s\S]{0,200}navopen", src) or \
        re.search(r"navopen[\s\S]{0,200}Escape", src), \
        "no Escape handler clears 'navopen'"


def test_resize_guard_clears_navopen():
    """Widening past the breakpoint must drop `navopen` so it can't stick open
    as a fixed overlay on a desktop-width viewport."""
    src = _src()
    # a resize/matchMedia handler must clear navopen within close proximity
    assert re.search(r"(matchMedia|innerWidth|addEventListener\(\s*['\"]resize)[\s\S]{0,260}navopen", src), \
        "no resize/matchMedia guard clears 'navopen' when the viewport widens"


def test_main_clears_fixed_mobilebar():
    """At <=820px the fixed 46px #mobilebar overlays the top of content; the
    layout must reserve space so content is not hidden underneath it. Accept an
    explicit `padding-top` or a `padding` shorthand whose top value clears 46px."""
    src = _src()
    block = re.search(r"@media\s*\(max-width:820px\)\s*\{[\s\S]*?\n\}", src)
    assert block, "could not find the max-width:820px media block"
    body = block.group(0)
    top = None
    m = re.search(r"padding-top:\s*(\d+)px", body)
    if m:
        top = int(m.group(1))
    else:
        # `.main{padding:<top> <r> <b>[ <l>]}` shorthand — first value is top
        m = re.search(r"\.main\{[^}]*padding:\s*(\d+)px\s+\d", body)
        if m:
            top = int(m.group(1))
    assert top is not None and top >= 46, \
        "the <=820px block must reserve >=46px top space for the fixed #mobilebar; got top=%r" % top
