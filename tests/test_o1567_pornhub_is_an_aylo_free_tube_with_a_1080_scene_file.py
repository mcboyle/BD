"""O1567 fx-pornhub-quality: pornhub scene pages never reached the Aylo page extractor.

MEASURED on test4 (10.0.70.85) 2026-09-29, site pornhub 4845ab5e, journal
``journalctl -u bulkdownloader``: the run logged only ``learned rows missed:
template=aylo_free_tubes`` and ``spa_api_candidate ... chose 0p from page-media; saw:
?p:page-media | ?p:embed-frame``, then ``spa_api_done ... 250p via page-media``.  With no
extractor the DOM scorer answered "Best is 240p (below 1080p) (no identity proof ...)"
(needs_review); after Approve the saved file was a 300x250 6 s AD clip
(3356281_video.mp4, ffprobe h264 300x250) -- the same bytes dedup-matched a youporn
download (dist=0).

The scene page itself carries the real files: a plain ``curl`` of
https://www.pornhub.com/view_video.php?viewkey=ph6273ce250946c returns a
``flashvars_407561051`` object whose mediaDefinitions list hls 1080/720/480/240 direct
``master.m3u8`` URLs (+ one remote mp4 ``get_media`` entry).  ``extractors_aylo`` parses
exactly that, but ``AYLO_FREE_TUBES`` lists only redtube.com, so ``is_aylo_url`` is False
for pornhub.com and ``runner.py`` skips ``_try_aylo_extractor``.

Rule: pornhub.com is an Aylo free tube (page-config extractor + min-resolution floor).
"""

BD_GATE_SCOPE = "module"

import json

import pytest

from bulk_downloader import extractors_aylo as A

SCENE = "https://www.pornhub.com/view_video.php?viewkey=ph6273ce250946c"
CDN = "https://hv-h.phncdn.com/hls/videos/202205/05/407561051"


def _defs():
    q = [(1080, "4000K"), (240, "1000K"), (480, "2000K"), (720, "4000K")]
    d = [{"format": "hls", "quality": str(h),
          "videoUrl": f"{CDN}/{h}P_{r}_407561051.mp4/master.m3u8?hash=x",
          "defaultQuality": h == 720} for h, r in q]
    d.append({"format": "mp4", "quality": [], "remote": True, "defaultQuality": False,
              "videoUrl": "https://www.pornhub.com/video/get_media?s=eyJrIjoiZmQ1NWM5MDNhNzBh"})
    return d


HTML = ("<html><head><title>Full Video | Pornhub</title><script>var flashvars_407561051 = "
        + json.dumps({"video_title": "Scene", "mediaDefinitions": _defs()}).replace("/", "\\/")
        + ";</script></head><body>Warning: adult</body></html>")


@pytest.mark.parametrize("url", [
    SCENE, "https://pornhub.com/view_video.php?viewkey=ph63968156d9d8c",
    "https://de.pornhub.com/view_video.php?viewkey=ph6398f37aabe31"])
def test_pornhub_scene_urls_are_routed_to_the_aylo_page_extractor(url):
    assert A.is_aylo_url(url), f"O1567: {url} is not an Aylo URL -> extractor skipped, DOM scorer picks an ad"
    assert A.is_free_tube_url(url)


def test_the_page_config_yields_the_1080_scene_file_not_a_240_or_an_ad():
    res = A.extract_from_html(HTML, quality_pref=["best"])
    assert res.ok, (res.error, res.error_detail)
    assert "1080P_4000K_407561051" in res.variant.url, res.variant.url


def test_control_a_non_aylo_host_is_still_not_routed():
    assert not A.is_aylo_url("https://www.example.com/view_video.php?viewkey=ph1")
    assert not A.is_free_tube_url("https://www.xhamster.com/videos/x-1")
