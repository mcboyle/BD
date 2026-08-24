"""No served page may link to /cockpit/home -- it was retired 768 releases ago.

BACKLOG ROW 113. Three live operator pages carried
`<a href="/cockpit/home">&larr; Cockpit Home</a>` -- app_settings_center.py:350,
app_report_center.py:75 and app_template_manager_ui.py:319 -- for a route
retired at v3.66.344. `app_cockpit_home.py` records the retirement in a comment
and serves only `/api/cockpit/nav`; the surviving cockpit pages are
`/cockpit/reports` and `/cockpit/settings`, and the landing page's replacement
is the SPA at `/`. An operator clicking that link got a 404.

WHY NO EXISTING GATE CAUGHT IT, which is the part worth keeping. `nav_reachability.py`
was built for exactly this class and crawls INBOUND reachability -- it asks
whether every registered route can be reached from somewhere, so a route nobody
links TO is caught. A link pointing OUT at a route that no longer exists is the
mirror image and is structurally outside that denominator. The gate built for
dead links could not see a dead link, because it was measuring the other
direction. Section 0, in the check written for this.

THIS GATE IS A TOMBSTONE, NOT THE GENERAL FIX, and saying so is the point. It
asserts one retired path is absent -- the same shape as
test_task_tracker_stays_retired and test_deploy_manifest_stays_retired. A
general outbound-link checker (every `href="/..."` in served HTML resolves to a
registered route) is NOT built here: the SPA serves client-side paths that are
not Flask routes, so the obvious predicate would fire on correct code, and
section 0 is explicit that over-sensitivity is a soundness bug rather than a
safe default. That remainder is named in row 113 rather than left implied.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

# Its denominator is the tracked tree, not a module.
BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
RETIRED = "/cockpit/home"

# This file necessarily contains the string it bans -- it has to name its own
# subject to test for it. Same exemption shape as the other tombstone gates, and
# CLAUDE.md section 0 records the trap directly: explaining a removal by naming
# the removed thing recreates it, and a comment is inside the denominator of
# every gate that reads source text.
_SELF = "tests/" + Path(__file__).name
_EXEMPT = {
    _SELF,
    # The retirement note itself, which must keep naming the route it retired
    # or the next reader cannot tell what was removed.
    "bulk_downloader/app_cockpit_home.py",
    "CHANGELOG.md",
    "project-knowledge/IMPROVEMENT_BACKLOG.md",
    # Tests that ASSERT the retirement. Each of these names the route in order
    # to prove it is gone -- the current cockpit route/navigation contracts
    # assert the 404 and live nav schema, and
    # test_cockpit_appearance asserts no nav renders an href to it. Banning the
    # string here would delete the checks that make the ban true.
    "tests/test_cockpit_navigation_contract.py",
    "tests/test_cockpit_route_contract.py",
    "tests/test_cockpit_appearance.py",
    "tests/test_integration_wiring.py",
    # Kept as the HISTORICAL record of the v3.66.3xx nav arrangement, with its
    # stale bullets struck through and a banner saying so. It is also the reason
    # the dead breadcrumb survived: it described the 404 link as intended
    # behaviour, and docs/**.md is outside both freshness gates (row 106).
    "docs/NAV_CONSOLIDATION.md",
}


def _tracked_text_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    names = [n for n in out.split("\0") if n]
    assert names, (
        "BD-GATE-UNRUNNABLE: git ls-files returned nothing, so every assertion "
        "below would pass over an empty denominator")
    keep = (".py", ".html", ".js", ".jsx", ".ts", ".tsx", ".md")
    return [n for n in names if n.endswith(keep)]


def test_the_denominator_is_not_empty():
    """Asserted before the verdict, because a predicate over zero files is a
    gate reporting OK having examined nothing."""
    files = _tracked_text_files()
    assert len(files) > 500, (
        f"only {len(files)} tracked text file(s) found; the extension filter and "
        f"the tree have diverged and this gate is asserting over almost nothing")


def test_no_tracked_source_links_to_the_retired_cockpit_home():
    files = _tracked_text_files()
    hits = []
    for rel in files:
        if rel in _EXEMPT:
            continue
        p = REPO / rel
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, IsADirectoryError):
            continue
        if RETIRED in text:
            for i, line in enumerate(text.splitlines(), 1):
                if RETIRED in line:
                    hits.append(f"{rel}:{i}: {line.strip()[:100]}")
    assert not hits, (
        "reference(s) to a route retired at v3.66.344 -- an operator following "
        "one of these gets a 404. The landing page's replacement is the SPA at "
        "'/'; the surviving cockpit pages are /cockpit/reports and "
        "/cockpit/settings:\n  " + "\n  ".join(hits))


def test_the_exemption_list_does_not_silently_cover_the_whole_tree():
    """The over-sensitivity control, inverted.

    An exemption set is how a tombstone gate quietly stops testing anything.
    Every entry must name a file that EXISTS and that genuinely needs to say
    the retired name -- a stale entry is a hole nobody can see.
    """
    for rel in sorted(_EXEMPT):
        assert (REPO / rel).is_file(), (
            f"exempt path {rel!r} does not exist; a stale exemption is an "
            f"unguarded file that reads as guarded")


# ── the RENDERED half, added @1210 ──────────────────────────────────────
# The scan above is a floor, not a ceiling, and its evasion surface is now
# declared: it matches the literal "/cockpit/home" in SOURCE TEXT. Measured
# 2026-08-24 on merged main -- serving
#     <a href="home">&larr; Cockpit Home</a>
# from a surviving cockpit page reproduces the exact operator-facing 404 that
# backlog row 113 was written for, while the source contains no "/cockpit/home"
# anywhere and the scan stays green. `urljoin("http://h/cockpit/settings",
# "home")` is "http://h/cockpit/home".
#
# A browser resolves hrefs; a grep does not. So this half asks the application
# what it actually SERVES, resolves every anchor the way a browser would, and
# compares normalised paths rather than raw text.

def _cockpit_pages():
    """Server-rendered cockpit pages, named explicitly so the denominator is
    reviewable rather than discovered -- an empty crawl would make every
    assertion below vacuously true."""
    return [
        "/cockpit/settings",
        "/cockpit/settings/secrets",
        "/cockpit/template-manager",
    ]


def test_no_rendered_cockpit_anchor_resolves_to_the_retired_home():
    """THE PROPERTY, exercised. A relative href that RESOLVES to the retired
    route is the failure; whether the string appears in source is irrelevant."""
    from urllib.parse import urljoin, urlsplit
    import re as _re

    from bulk_downloader.app import app  # noqa: PLC0415

    app.config["TESTING"] = True
    checked = 0
    anchors_seen = 0
    offenders = []
    with app.test_client() as client:
        for page in _cockpit_pages():
            resp = client.get(page, follow_redirects=True)
            if resp.status_code != 200:
                continue
            checked += 1
            html = resp.get_data(as_text=True)
            for href in _re.findall(r'<a\b[^>]*?href=["\']([^"\']+)["\']',
                                    html, _re.IGNORECASE):
                anchors_seen += 1
                resolved = urljoin("http://bd.local" + page, href)
                if urlsplit(resolved).path == RETIRED:
                    offenders.append((page, href, resolved))

    # PRECONDITIONS, before any verdict: a page set that 404s everywhere, or a
    # page with no anchors at all, would pass this test while testing nothing.
    assert checked >= 1, (
        "no cockpit page returned 200, so no anchor was resolved and this gate "
        f"judged an empty denominator: {_cockpit_pages()}")
    assert anchors_seen >= 1, (
        f"{checked} cockpit page(s) rendered but contained no anchors at all")
    assert not offenders, (
        "a rendered cockpit anchor RESOLVES to the retired "
        f"{RETIRED} even though the source text does not contain it: {offenders}")


def test_a_relative_href_that_resolves_to_the_retired_route_is_caught():
    """EVASION FIXTURE. Pins the resolution semantics the scan cannot see, so
    that a future rewrite back to raw-text matching goes RED here."""
    from urllib.parse import urljoin, urlsplit
    resolved = urljoin("http://bd.local/cockpit/settings", "home")
    assert urlsplit(resolved).path == RETIRED, resolved
    assert RETIRED not in '<a href="home">&larr; Cockpit Home</a>', (
        "the evasion string unexpectedly contains the retired path, so this "
        "fixture is not reproducing the shape that defeated the scan")

