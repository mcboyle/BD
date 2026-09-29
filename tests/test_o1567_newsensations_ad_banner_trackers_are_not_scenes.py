"""fx-newsensations-discovery-banners (O1568d, test7 10.0.70.84, 2026-09-29).

Scene discovery of https://www.newsensations.com/members/ queued ad banners
(/members/bannerload.php?track=2311|2313|2447, then 2341) as scenes although
tpl95-newsensations-2 cleared the offers.php cross-sell live. A temporary
census of the page the crawler read after the gate (results/test7/
newsensations-discovery-diag-2229Z.txt) showed why: 32 thumbnailed
bannerload.php?track=<n> ad click-trackers against 23 gallery.php scene cards of
which only one carries an <img>. The tracker cohort out-imaged every other
cohort. An ad click-tracker is never a scene, whatever its thumbnail count.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

LISTING = "https://www.newsensations.com/members/"
_M = "https://www.newsensations.com/members/"

# The measured census, shape for shape (counts and has_img as read live).
BANNERS = (
    [{"url": f"{_M}bannerload.php?track={2300 + i}", "has_img": True, "text": ""}
     for i in range(32)]
    + [{"url": f"{_M}bannerload.php?track=2447", "has_img": False, "text": "Live Cams"}]
)
GALLERY = (
    [{"url": f"{_M}gallery.php?id=11399&type=vids", "has_img": True, "text": ""}]
    + [{"url": f"{_M}gallery.php?id={11380 + i}&type=vids", "has_img": False,
        "text": f"Scene {i}"} for i in range(22)]
)
OTHER = (
    [{"url": f"{_M}sets.php?id={i}", "has_img": False, "text": f"Set {i}"}
     for i in range(55)]
    + [{"url": f"{_M}category.php?id={i}", "has_img": False, "text": f"Cat {i}"}
       for i in range(23)]
    + [{"url": f"{_M}index.php", "has_img": True, "text": ""},
       {"url": f"{_M}favorites.php", "has_img": False, "text": "Favorites"},
       {"url": f"{_M}dvds.php", "has_img": False, "text": "DVDs"},
       {"url": f"{_M}logout.php", "has_img": False, "text": "Logout"}]
)


def _cohort(anchors, listing=LISTING):
    from bulk_downloader import scene_crawler
    scenes, shapes = scene_crawler._scene_cohort([dict(a) for a in anchors], listing)
    return [s["url"] for s in scenes], shapes


def test_newsensations_members_home_queues_no_ad_banner():
    urls, shapes = _cohort(BANNERS + GALLERY + OTHER)
    banners = [u for u in urls if "bannerload.php" in u]
    assert banners == [], (
        f"fx-newsensations-discovery-banners: {len(banners)} ad click-trackers "
        f"queued as scenes, shapes={shapes}")
    # With the trackers out, one thumbnail on a non-scene-shaped cohort stays
    # weak evidence (dl95-xempire-1): nothing is queued rather than a guess.
    assert (urls, shapes) == ([], [])


def test_ad_script_and_banner_directory_shapes_are_trackers():
    from bulk_downloader.scene_crawler import _is_ad_tracker
    for url in (f"{_M}bannerload.php?track=2311",
                "https://example.test/banner.php?id=4",
                "https://example.test/adclick.php?b=9",
                "https://example.test/ads.php?zone=1",
                "https://example.test/banners/728x90/2311"):
        assert _is_ad_tracker(url), url


def test_negative_control_a_banner_word_in_a_scene_slug_is_a_scene():
    from bulk_downloader.scene_crawler import _is_ad_tracker
    scenes = [
        {"url": "https://members.example.test/en/video/studio/banner-girl/111",
         "has_img": True, "text": "Banner Girl"},
        {"url": "https://members.example.test/en/video/studio/ad-astra/222",
         "has_img": True, "text": "Ad Astra"},
    ]
    assert not any(_is_ad_tracker(s["url"]) for s in scenes)
    assert not _is_ad_tracker(f"{_M}gallery.php?id=11399&type=vids")
    assert not _is_ad_tracker("https://example.test/admin.php")
    urls, _ = _cohort(scenes, "https://members.example.test/en")
    assert urls == [s["url"] for s in scenes]
