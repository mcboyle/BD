"""v3.66.732 -- cockpit view reachability: the denominator gets the missing mechanisms.

bd-gui-surface (rebuilt @729) reports SEVEN "dark" cockpit views:

    advlanding  complexity  daily  inbox  maturity  orghealth  syslanding

Every one of them is a FALSE POSITIVE, and finding that out cost a reverted cut.

The tool defines dark as "in the PAGES registry, but named by no static
<a data-p> anchor and no REDIRECT alias". That denominator EXCLUDES three real
reachability mechanisms the cockpit actually uses:

  1. TABBED CONSOLIDATION. v3.66.107 deliberately merged inbox + daily + alerts
     into the `priority` page (data-ptab), and the composite indices (complexity,
     maturity, orghealth) into `scores`. The nav entries were REMOVED ON PURPOSE
     and test_v3_66_107 pins that removal -- while a companion test pins that the
     underlying PAGES.inbox / PAGES.daily renderers are KEPT, precisely so
     deep-links and direct go() still resolve. Wiring anchors back would revert a
     deliberate consolidation; the band caught exactly that.

  2. PROGRAMMATIC TIER LANDING. _tierLanding(t) maps a tier to 'advlanding' /
     'syslanding' and the router dispatches it when the layout is mode/miller.
     No anchor names them; they are reached by CODE. (test_cockpit_landings.py
     already guards them.)

  3. REDIRECT aliases -- which the tool DOES model.

So the product was right and the TOOL was wrong. "No anchor" is not "unreachable";
it is "unreachable BY ANCHOR", and reporting that as DARK invites a future session
to wire a control that was closed on purpose. A check whose denominator structurally
excludes the mechanism in use reports a finding it cannot justify.

This suite pins the CORRECTED model: every renderable view must be reachable by at
least one DERIVED mechanism, and a view we cannot classify is UNKNOWN -- which FAILS,
rather than being quietly labelled dark or quietly labelled fine.

Zero-arg tests; repo root via __file__; stdlib only.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONSOLE = REPO / "tools" / "cockpit_console.py"

PAGES_RE = re.compile(r"\bPAGES\.([a-zA-Z0-9_]+)\s*=")
ANCHOR_RE = re.compile(r'data-p="([a-zA-Z0-9_-]+)"')
REDIRECT_BLOCK_RE = re.compile(r"const\s+REDIRECT\s*=\s*\{(.*?)\n\}", re.S)
REDIRECT_KEY_RE = re.compile(r"(?:^|[,{\s])([a-zA-Z0-9_]+)\s*:\s*\[")
# _tierLanding(t){return t==='advanced'?'advlanding':t==='system'?'syslanding':'home';}
TIER_LANDING_RE = re.compile(r"function\s+_tierLanding\s*\([^)]*\)\s*\{(.*?)\}", re.S)
QUOTED_RE = re.compile(r"'([a-zA-Z0-9_]+)'")

# Views consolidated INTO a tabbed host page at v3.66.107. The renderer is kept
# (deep-links resolve); the nav entry was removed on purpose. Each maps to the
# page that now presents it.
CONSOLIDATED = {
    "inbox": "priority",
    "daily": "priority",
    "complexity": "scores",
    "maturity": "scores",
    "orghealth": "scores",
}


def _src():
    return CONSOLE.read_text(encoding="utf-8", errors="replace")


def _views():
    return set(PAGES_RE.findall(_src()))


def _anchors():
    return set(ANCHOR_RE.findall(_src()))


def _redirects():
    m = REDIRECT_BLOCK_RE.search(_src())
    return set(REDIRECT_KEY_RE.findall(m.group(1))) if m else set()


def _tier_landings():
    """Views the router reaches by CODE, not by anchor."""
    m = TIER_LANDING_RE.search(_src())
    return set(QUOTED_RE.findall(m.group(1))) if m else set()


def test_the_tier_landing_mechanism_is_derivable():
    """If this stops parsing, the reachability model below is silently wrong."""
    landings = _tier_landings()
    assert {"advlanding", "syslanding"} <= landings, (
        f"_tierLanding no longer yields the landing pages: {sorted(landings)}. "
        "The reachability denominator depends on parsing it.")


def test_consolidated_views_keep_their_renderers():
    """v3.66.107 removed their NAV entries and kept the renderers on purpose.

    Deleting a renderer would break deep-links and direct go() -- the very thing
    test_v3_66_107 pins.
    """
    src = _src()
    for view in CONSOLIDATED:
        assert f"PAGES.{view}=" in src, (
            f"PAGES.{view} renderer is gone; v3.66.107 requires it kept so "
            "deep-links still resolve")


def test_consolidated_views_have_no_nav_anchor():
    """The negative control for the consolidation.

    If someone (a future session reading a 'dark view' report) wires an anchor
    back, this fails and says why -- instead of the consolidation silently
    un-happening.
    """
    anchors = _anchors()
    regressed = sorted(v for v in CONSOLIDATED if v in anchors)
    assert not regressed, (
        f"{regressed} were consolidated into their host page(s) at v3.66.107 and "
        "must NOT have their own nav anchor. They are reachable via "
        f"{sorted({CONSOLIDATED[v] for v in regressed})} (data-ptab) and by deep-link. "
        "A 'dark view' report that lists them is describing a closed control, not a bug.")


def test_every_renderable_view_is_reachable_by_some_derived_mechanism():
    """THE GATE. Anchor OR redirect OR tab-consolidation OR tier-landing.

    A view reachable by none of these is genuinely unreachable. A view we cannot
    classify at all is UNKNOWN -- and UNKNOWN fails here, because a check that
    cannot verify must say so.
    """
    views = _views()
    reachable = _anchors() | _redirects() | _tier_landings() | set(CONSOLIDATED)
    unreachable = sorted(views - reachable)
    assert not unreachable, (
        f"{len(unreachable)} view(s) in PAGES reachable by NO derived mechanism "
        f"(no anchor, no redirect, no tab-host, no tier-landing): {unreachable}")


def test_no_orphan_anchors():
    """An anchor pointing at a view the router cannot dispatch is a 404 with a
    nice label on it."""
    views = _views()
    known = views | _redirects()
    orphans = sorted(a for a in _anchors() if a not in known)
    assert not orphans, f"anchors pointing at non-existent views: {orphans}"
