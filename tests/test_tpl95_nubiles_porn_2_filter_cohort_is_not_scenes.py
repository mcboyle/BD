"""tpl95-nubiles-porn-2 (harness-work/UIUX-20260928/download-95/A8-A/tpl-nubiles-porn/events.jsonl#scan_new_rows + s2_find.png):
scene discovery on https://members.nubiles-porn.com/video/gallery queued /video/gallery/website/18, /23, /75 -- the
gallery's site-filter logo cards -- as scenes; each job then downloaded whichever scene that filtered listing showed
first. The scene cards are /video/watch/<id>/<slug>.

The image-bearing cohort with the most thumbnails won; the site-filter logos out-imaged the scene cards. A cohort whose
every URL sits under a listing ROUTE word (the product rule's gallery / sort / page / browse / sites / shorts) now loses
to any other image cohort, and stands only when it is the only one. Pure anchor fixtures; no browser, no network.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

LISTING = "https://members.nubiles-porn.com/video/gallery"
SITE = "https://members.nubiles-porn.com"


def _a(path, img=True, text=""):
    return {"url": SITE + path, "has_img": img, "text": text}


def _nubiles_gallery():
    logos = [
        _a(f"/video/gallery/website/{n}")
        for n in (18, 23, 75, 4, 7, 9, 11, 12, 15, 21, 30, 33, 40, 41)
    ]
    scenes = [_a(f"/video/watch/{100000 + i}/scene-title-{i}") for i in range(12)]
    titles = [
        _a(f"/video/watch/{100000 + i}/scene-title-{i}", img=False, text=f"Scene {i}")
        for i in range(12)
    ]
    nav = [
        _a("/video/gallery", img=False),
        _a("/photo/gallery", img=False),
        _a("/account/profile", img=False),
    ]
    return logos + scenes + titles + nav


def test_the_site_filter_cohort_is_not_the_scene_cohort():
    """THE ROW: the /video/watch/<id>/<slug> cards are the scenes, not /video/gallery/website/<n>."""
    from bulk_downloader.scene_crawler import _scene_cohort

    scenes, shapes = _scene_cohort(_nubiles_gallery(), LISTING)
    urls = [row["url"] for row in scenes]
    assert urls and all("/video/watch/" in u for u in urls), (shapes, urls[:4])
    assert not any("/gallery/website/" in u for u in urls), (shapes, urls[:4])
    assert len(urls) == 12, (shapes, len(urls))


def test_a_cohort_without_route_words_keeps_the_old_ranking():
    """Negative control: with no listing-route cohort, thumbnail count still decides (models > scenes here)."""
    from bulk_downloader.scene_crawler import _scene_cohort

    anchors = [_a(f"/models/model-{i}") for i in range(9)] + [
        _a(f"/scenes/scene-{i}") for i in range(5)
    ]
    scenes, shapes = _scene_cohort(anchors, SITE + "/scenes")
    assert [row["url"] for row in scenes] == [
        SITE + f"/models/model-{i}" for i in range(9)
    ], shapes


def test_a_lone_route_word_cohort_still_stands():
    """The rule only re-ranks: a listing whose only image cohort is route-shaped is not emptied by it."""
    from bulk_downloader.scene_crawler import _scene_cohort

    anchors = [_a(f"/video/gallery/website/{n}") for n in (18, 23, 75, 4)]
    scenes, _shapes = _scene_cohort(anchors, LISTING)
    assert len(scenes) == 4, scenes
