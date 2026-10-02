"""dl95-txxx-7 (O1649; B7-B p1/txxx scan_done, reprobed on test2 .95 2026-10-02T14:07Z): discovery on
https://member.txxx.com/ COMPLETED with discovered 0, zero_reason no_thumbnails, links 351, thumbnail_links 0.

Why: txxx scene cards are <a class="listing-item" href="/videos/<id>/<slug>/"> whose thumbnail is a CSS
background-image on a div (no <img>), so _ANCHOR_JS reported has_img False for every card and the cohort,
which admits only thumbnail rows, was empty.  The fix: a background-image url() inside the link counts as a thumbnail.

Fixture: 19 REAL cards + nav links from the rendered member.txxx.com page (tests/fixtures/dl95_txxx_7/member_listing.html),
run through the crawler's own _ANCHOR_JS in Chromium.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from bulk_downloader import scene_crawler as sc

BD_GATE_SCOPE = "module"
_SCENE_RE = re.compile(r"/videos/\d+/[^/]+/$")
FIXTURE = Path(__file__).parent / "fixtures" / "dl95_txxx_7" / "member_listing.html"


@pytest.fixture(scope="module")
def anchors():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(FIXTURE.read_text(encoding="utf8"))
        rows = page.locator(sc._LINK_SELECTOR).evaluate_all(sc._ANCHOR_JS)
        browser.close()
    return rows


def test_background_thumbnail_cards_report_a_thumbnail(anchors):
    cards = [r for r in anchors if _SCENE_RE.search(r["url"])]
    assert len({r["url"] for r in cards}) == 19
    # a card has several links to its scene; the thumbnail one (a.listing-item) must report the thumbnail
    shown = {r["url"] for r in cards if r["has_img"]}
    assert shown == {r["url"] for r in cards}, sorted({r["url"] for r in cards} - shown)


def test_nav_links_without_a_thumbnail_do_not_report_one(anchors):
    nav = [r for r in anchors if r["url"].endswith(("/latest-updates/", "/most-popular/", "/models/", "/channels/", "/videos/"))]
    assert nav and not any(r["has_img"] for r in nav)


def test_cohort_admits_the_19_member_scenes_and_not_the_nav(anchors):
    scenes, _shapes = sc._scene_cohort(anchors, "https://member.txxx.com/")
    urls = [str(s["url"]) for s in scenes]
    assert len(set(urls)) == 19, urls
    assert all(_SCENE_RE.search(u) for u in urls), urls


# Lens F1 (C4-C, r1 + r2): a nav whose links each draw a sprite icon as a background url(), or as a 16x16
# thumbnail-classed span (class="ico img"), must not become
# thumbnail evidence and take the cohort from the real scene cards. The cards here carry their thumbnail
# only as a card-sized background url() (no <img>, no thumbnail class), so they are the positive control.
_SPRITE_NAV = "".join(
    f'<li><a href="/categories/cat-{c}/"><span style="display:inline-block;width:16px;height:16px;'
    f'background-image:url(/sprite.png)"></span><span style="background-image:url(/sprite.png)"></span>'
    f'<span class="ico img" style="display:inline-block;width:16px;height:16px;background-image:url(/sprite.png)">'
    f'</span>Cat {c}</a></li>'
    for c in "abcdefghijkl")
_BG_CARDS = "".join(
    f'<li><a href="/videos/{1000 + i}/scene-slug-{i}/"><div style="width:288px;height:162px;'
    f'background-image:url(/t/{i}.jpg)"></div></a><h3>Scene {i}</h3></li>'
    for i in range(10))


@pytest.fixture(scope="module")
def sprite_nav_anchors():
    sync_api = pytest.importorskip("playwright.sync_api")
    html = (f"<html><head><base href='https://example.test/'></head><body><ul>{_SPRITE_NAV}</ul>"
            f"<ul>{_BG_CARDS}</ul></body></html>")
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        rows = page.locator(sc._LINK_SELECTOR).evaluate_all(sc._ANCHOR_JS)
        browser.close()
    return rows


def test_sprite_icon_nav_links_are_not_thumbnails_and_card_sized_backgrounds_are(sprite_nav_anchors):
    nav = [r for r in sprite_nav_anchors if "/categories/" in r["url"]]
    cards = [r for r in sprite_nav_anchors if "/videos/" in r["url"]]
    assert len(nav) == 12 and len(cards) == 10
    assert [r["url"] for r in nav if r["has_img"]] == []
    assert all(r["has_img"] for r in cards), [r["url"] for r in cards if not r["has_img"]]
    scenes, _shapes = sc._scene_cohort(sprite_nav_anchors, "https://example.test/")
    urls = sorted({str(s["url"]) for s in scenes})
    assert len(urls) == 10 and all("/videos/" in u for u in urls), urls
