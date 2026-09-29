"""dl95-beeg-1: related-scene cards on an id-routed scene page are another work.

Measured on test2 (harness-work/UIUX-20260928/download-95/B6-B/p1/beeg/, build b73666ec):
https://beeg.com/-0920833012505915 went 17 min in "Finding download button", then
"Clicking [8K] -- no identity proof", tried 8K, 8K, 5K and filed needs_review. The
"tiers" were related-video cards: their "1.8K"/"2.5K" view/like counts and channel
names ("Tiny 4K") read as resolutions. Each card links to ANOTHER scene on the same
route shape (/-0<id>), but beeg's routes carry no title slug, so the token-based
identity check could call none of them foreign and all were admitted as UNKNOWN.

Contract pinned here: on a page whose route is one opaque numeric id, a same-host link
of the same route shape with a DIFFERENT id names another work (FOREIGN). With every
candidate foreign, find_best_download reports nothing in scope (a fast, named
needs_review) instead of admitting another scene's card.
"""
from __future__ import annotations

# An ordinary module test: its subject is the module under test, not the tree.
BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader import detect

SCENE = "https://beeg.com/-0920833012505915"


def _card(scene_id, channel, title, views, likes):
    return (f'<a class="card" href="/-0{scene_id}"><img alt="">'
            f'<div>{channel}</div><div>{title}</div><div>{views}</div>'
            f'<div>2d</div><div>{likes}</div></a>')


# The measured shape, trimmed: a player that is not a link, then related cards
# whose counts / channel names carry resolution-looking tokens.
_BEEG_SCENE = f"""<!DOCTYPE html><html><head><title>Animax AI | A scene</title></head>
<body><main><video id="player" poster="p.jpg"></video><h1>A scene</h1>
<section class="related">
{_card("274086217869699", "Tiny 4K", "Sexy Teen Gets Her", "310K", "1.2K")}
{_card("513167729466248", "My Night Collection", "I Can Never Finish", "467K", "1.8K")}
{_card("872651879449618", "Diana Rider", "Stepsibling incident", "90K", "2.5K")}
{_card("743858292186308", "Absentia", "Passionate Sex", "51K", "1.4K")}
</section></main></body></html>"""


@contextmanager
def _page(html, url=SCENE):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body=html))
            page.goto(url, wait_until="load")
            yield page
        finally:
            browser.close()


def test_related_cards_on_an_id_routed_scene_are_not_admitted():
    with _page(_BEEG_SCENE) as page:
        best = detect.find_best_download(page)
    assert not (best and best.get("locator") is not None), (
        "DL95-BEEG1: a related-scene card was admitted as this scene's download: "
        f"{(best or {}).get('text', '')[:60]!r}")
    assert best is not None and best.get("_no_in_scope_candidates"), (
        "DL95-BEEG1: related cards were not refused as another work (expected the "
        "nothing-in-scope outcome)")
    reasons = {c.get("reason") for c in best.get("_excluded_candidates") or []}
    assert reasons, "the refusal must name why each card was refused"


@pytest.mark.parametrize("href,expected", [
    ("/-0274086217869699", detect._WORK_FOREIGN),         # another scene, same route
    ("https://beeg.com/-0274086217869699", detect._WORK_FOREIGN),
    ("/-0920833012505915", detect._WORK_UNKNOWN),         # the page itself: not a claim
    ("/4K", detect._WORK_UNKNOWN),                        # a category route, not an id
    ("https://other.example/-0274086217869699", detect._WORK_UNKNOWN),  # other host
    ("/tag/-0274086217869699", detect._WORK_UNKNOWN),     # different route shape
])
def test_opaque_id_route_affinity(href, expected):
    class _El:
        def get_attribute(self, name):
            return href if name == "href" else None

    assert detect._candidate_work_affinity(_El(), SCENE) == expected


def test_slug_routed_pages_are_unchanged():
    """Control: a slug route keeps the token rule; a numeric-only sibling id on a
    slug page is not condemned by the new rule."""
    class _El:
        def __init__(self, href):
            self.href = href

        def get_attribute(self, name):
            return self.href if name == "href" else None

    page = "https://site.example/videos/12345678/the-scene-title"
    assert detect._candidate_work_affinity(
        _El("/videos/87654321/the-scene-title"), page) == detect._WORK_IN_SCOPE
    assert detect._candidate_work_affinity(
        _El("/videos/87654321"), page) == detect._WORK_UNKNOWN
