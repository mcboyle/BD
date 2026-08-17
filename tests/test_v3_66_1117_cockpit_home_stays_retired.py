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
