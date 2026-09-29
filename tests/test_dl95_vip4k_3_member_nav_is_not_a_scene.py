"""dl95-vip4k-3 (O1513 on test2, A5-A vip4k/sd2/SD2__VIP.png + vip4k/vq/VQ__vip4k-queue.png): scene discovery on
members.vip4k.com/en/videos queued /en/videos/favorites and /en/videos/history -- the member sidebar -- as scenes; the
favorites job then failed page_shape. test2's scene_crawl_runs show it twice: 101078be queued history + favorites,
4d41856a queued later + liked ahead of the real scenes 741/36/600/609.

Why: the sidebar links share the /en/videos/<x> path signature with the /en/videos/<id> scene cards, and the cohort
admits every row of a winning signature, image or not. They sit first in DOM order, so "Newest N" took them first.
The fix: a text-only row joins only when its varying segments have the kind (id digits vs word) of a thumbnail row.

Fixture: REAL anchors produced by the crawler's own _ANCHOR_JS in Chromium over the repo's vip4k members capture
(tests/fixtures/vip4k_listing_anchors_20260929.json, provenance inside).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from bulk_downloader.scene_crawler import _scene_cohort

BD_GATE_SCOPE = "module"

FX = json.loads(
    (
        Path(__file__).parent / "fixtures" / "vip4k_listing_anchors_20260929.json"
    ).read_text()
)
NAV = {
    "/en/videos/history",
    "/en/videos/favorites",
    "/en/videos/later",
    "/en/videos/liked",
}


def _paths(scenes):
    return [urlsplit(s["url"]).path for s in scenes]


@pytest.fixture
def anchors():
    rows = copy.deepcopy(FX["anchors"])
    paths = [urlsplit(r["url"]).path for r in rows]
    assert (
        NAV <= set(paths) and sum(p.rsplit("/", 1)[1].isdigit() for p in paths) >= 8
    ), "fixture lost its shape"
    assert all(not r["has_img"] for r in rows if urlsplit(r["url"]).path in NAV)
    return rows


def test_member_sidebar_links_are_not_queued_as_scenes(anchors):
    scenes, shapes = _scene_cohort(anchors, FX["listing_url"])
    got = _paths(scenes)
    assert not NAV & set(got), (
        f"member nav pages admitted as scenes: {sorted(NAV & set(got))}"
    )
    assert len(got) == 8 and all(p.rsplit("/", 1)[1].isdigit() for p in got), got
    assert shapes == ["/en/videos/<n>"]


def test_newest_two_are_scenes(anchors):
    # The operator asked "Newest scenes 2": the first two admitted rows are what gets queued.
    scenes, _ = _scene_cohort(anchors, FX["listing_url"])
    assert _paths(scenes)[:2] == ["/en/videos/1363", "/en/videos/1371"]


def test_text_only_cards_of_the_scene_kind_still_join(anchors):
    # nubilefilms shape: sibling cards whose anchor holds no image are admitted by one thumbnail.
    for r in anchors:
        if urlsplit(r["url"]).path in ("/en/videos/1356", "/en/videos/1300"):
            r["has_img"] = False
    got = _paths(_scene_cohort(anchors, FX["listing_url"])[0])
    assert {"/en/videos/1356", "/en/videos/1300"} <= set(got) and not NAV & set(got)


def test_word_slug_scenes_are_unchanged():
    # A site whose scene ids are word slugs: text-only slug cards still join an image-bearing slug cohort.
    base = "https://members.example.test/videos/"
    rows = [
        {"url": base + "a-scene", "has_img": True, "text": ""},
        {"url": base + "b-scene", "has_img": True, "text": ""},
        {"url": base + "c-scene", "has_img": False, "text": "C Scene"},
    ]
    assert _paths(_scene_cohort(rows, "https://members.example.test/videos")[0]) == [
        "/videos/a-scene",
        "/videos/b-scene",
        "/videos/c-scene",
    ]


def test_an_image_bearing_row_of_another_kind_is_still_admitted(anchors):
    # Only text-only rows are filtered: a thumbnail card is evidence on its own.
    for r in anchors:
        if urlsplit(r["url"]).path == "/en/videos/liked":
            r["has_img"] = True
    assert "/en/videos/liked" in _paths(_scene_cohort(anchors, FX["listing_url"])[0])
