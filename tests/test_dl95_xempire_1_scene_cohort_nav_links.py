"""dl95-xempire-1: scene discovery must queue only scene-shaped links and dedupe within one crawl.

O1508 rerun on test2 (harness-work/UIUX-20260928/download-95/A1-A/FINDINGS-A1-A.md, A5; shots xempire/09_end__0.png,
xempire/xempire__scan_state.png): discovery of https://members.xempire.com/en reported "2 discovered / 2 queued" and
both were https://members.xempire.com/livecam/autologin -- one nav banner linked twice -- which then failed
"auth-required, retries exhausted".  _scene_cohort let two spellings of one destination form a "cohort" (every path
segment repeated, so nothing varies) and deduped on the raw href only.  A real scene cohort
(/en/video/<studio>/<slug>/<id>) always has a varying segment or distinct query ids.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

LISTING = "https://members.xempire.com/en"
BANNER = [
    {"url": "https://members.xempire.com/livecam/autologin", "has_img": True, "text": ""},
    {"url": "https://members.xempire.com/livecam/autologin#top", "has_img": True, "text": ""},
]
NAV = [
    {"url": "https://members.xempire.com/en/models", "has_img": False, "text": "Models"},
    {"url": "https://members.xempire.com/en/account/settings", "has_img": False, "text": "Account"},
]
SCENES = [
    {"url": "https://members.xempire.com/en/video/hardx/first-scene/111111", "has_img": True, "text": "First"},
    {"url": "https://members.xempire.com/en/video/darkx/second-scene/222222", "has_img": True, "text": "Second"},
]


def _cohort(anchors):
    from bulk_downloader import scene_crawler
    scenes, shapes = scene_crawler._scene_cohort([dict(a) for a in anchors], LISTING)
    return [s["url"] for s in scenes], shapes


def test_a_nav_banner_linked_twice_is_not_a_scene_cohort():
    # The A5 page: nothing scene-shaped, one banner destination under two hrefs.
    urls, _ = _cohort(BANNER + NAV)
    assert urls == [], f"dl95-xempire-1: nav banner queued as scenes: {urls}"


def test_the_banner_never_rides_along_with_real_scenes():
    # Two scenes (2 images) tie the banner's 2 images and 2 rows; the tie used to admit both cohorts.
    urls, shapes = _cohort(BANNER + NAV + SCENES)
    assert urls == [s["url"] for s in SCENES], f"dl95-xempire-1: {urls}"
    assert shapes == ["/en/video/<slug>/<slug>/<n>"], shapes


def test_one_scene_under_two_spellings_is_queued_once():
    dup = {"url": SCENES[0]["url"] + "/#comments", "has_img": False, "text": "First"}
    urls, _ = _cohort(SCENES + [dup])
    assert urls == [s["url"] for s in SCENES], f"dl95-xempire-1: a scene was queued twice within one crawl: {urls}"


def test_query_id_scene_cohort_is_still_a_cohort():
    # Negative control against over-filtering: every path segment repeats, but the ids in the query differ.
    rows = [{"url": f"https://members.xempire.com/en/view.php?id={n}", "has_img": True, "text": f"S{n}"} for n in (1, 2, 3)]
    urls, _ = _cohort(rows)
    assert urls == [r["url"] for r in rows], urls


def test_a_single_scene_card_is_still_found():
    urls, _ = _cohort(NAV + SCENES[:1])
    assert urls == [SCENES[0]["url"]], urls


def test_thumbnail_evidence_survives_the_merge_when_the_title_link_comes_first():
    title_link = {"url": SCENES[1]["url"] + "/", "has_img": False, "text": "Second"}
    urls, _ = _cohort(NAV + [title_link, SCENES[1]])
    assert urls == [title_link["url"]], f"dl95-xempire-1: merged card lost its thumbnail: {urls}"


def test_a_multi_thumbnail_cohort_needs_no_scene_hint():
    # Sibling thumbnails prove the shape (wowgirls /updates/<slug>); the product hint rule gates only a lone thumbnail.
    rows = [{"url": f"https://members.xempire.com/updates/scene-{n}", "has_img": True, "text": f"S{n}"} for n in "ab"]
    urls, _ = _cohort(NAV + rows)
    assert urls == [r["url"] for r in rows], f"dl95-xempire-1: over-filtered a real 2-card cohort: {urls}"
