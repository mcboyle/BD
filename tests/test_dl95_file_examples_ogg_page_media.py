"""dl95-file-examples-5: a direct .ogg URL must be a spa-api page-media candidate and keep its extension.

Live on test2 (harness-work/DOT95-LANE/live-fe-rerun2/history.json): the four direct file-examples .ogg URLs (real
video/ogg bytes) all failed "[page_shape] No download button found". Chromium plays an .ogg inline, so no download
event fires and the job reaches the spa-api fallback -- which found "2 page media URL(s)" and still no option, because
spa_media_extract.MEDIA_EXT_RE had no ogg/ogv. MP4/WEBM (same inline-play route) landed; AVI/MOV/WMV download
natively.

  1. page_media_candidates admits .ogg and .ogv page media.
  2. spa_file_ext keeps .ogg/.ogv (the saved name used to fall back to ".mp4" for anything outside its list),
     and still falls back to ".mp4" for a name without a known media extension.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bulk_downloader import spa_media_extract as spa  # noqa: E402

BD_GATE_SCOPE = "module"

PAGE = "https://file-examples.com/storage/x/2018/04/file_example_OGG_1920_13_3mg.ogg"


@pytest.mark.parametrize("media", [
    PAGE,
    "https://file-examples.com/storage/x/2018/04/file_example_OGG_640_2_7mg.ogv?token=1",
])
def test_ogg_page_media_is_a_candidate(media):
    cands = spa.page_media_candidates(PAGE, [media])
    assert [c["url"] for c in cands] == [media], f"DL95_FE5_OGG_NOT_MEDIA: {cands!r}"


def test_non_media_page_resource_is_still_refused():
    assert spa.page_media_candidates(PAGE, ["https://file-examples.com/ads/banner.js"]) == []


@pytest.mark.parametrize("fname,ext", [
    ("file_example_OGG_1920_13_3mg.ogg", ".ogg"),
    ("clip.OGV", ".ogv"),
    ("clip.webm", ".webm"),
    ("clip.wmv", ".wmv"),
    ("", ".mp4"),
    ("stream", ".mp4"),
    ("banner.js", ".mp4"),
])
def test_spa_file_ext_keeps_the_media_extension(fname, ext):
    assert spa.spa_file_ext(fname) == ext, f"DL95_FE5_EXT_LOST: {fname!r}"


_AUDIO_AND_VIDEO_HTML = """<!doctype html><html><body>
<audio autoplay><source src="/sfx/chime.ogg" type="audio/ogg"></audio>
<audio src="/sfx/theme.mp4"></audio>
<video><source src="/v/scene123.ogv" type="video/ogg"></video>
</body></html>"""


def test_page_audio_is_never_a_page_media_candidate():
    """Lens R1 (correctness-A2-A): PAGE_MEDIA_JS read every <source>, <audio><source> included, so admitting ogg
    made a page's Ogg sound effect "the video". Only <video> and its <source> children are page media."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    page_url = "https://site.example/video/123/"
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            pg = br.new_page()
            # Every request is served locally; no .example host is ever reached.
            pg.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html", body=_AUDIO_AND_VIDEO_HTML))
            pg.goto(page_url, wait_until="load")
            media = pg.evaluate(spa.PAGE_MEDIA_JS) or []
        finally:
            br.close()
    urls = [c["url"] for c in spa.page_media_candidates(page_url, media)]
    assert urls == ["https://site.example/v/scene123.ogv"], f"DL95_FE5_AUDIO_AS_VIDEO: {urls!r}"
