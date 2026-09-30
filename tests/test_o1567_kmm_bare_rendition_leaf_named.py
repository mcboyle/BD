"""fx-kmm-generic-filename (O1567, live bd2 10.0.70.52 2026-09-29 23:44Z): the members full-scene link
/download/video/6023/4k_h264 saved as "4k_h264.mp4" on teenfidelity AND kellymadisonmedia -- the route's rendition
leaf, the same for every scene (the next scene becomes 4k_h264_1.mp4 via safe_dest). Row 722 (G29) replaces a bare
leaf with the scene's name, but its pattern knew "4k.h264" and not "4k_h264", and the name it falls back to here is the
site-chrome <title> "Members Area - Porn Fidelity : Kelly Madison : Teen Fidelity" (identical on every scene) while
the scene's name is the page <h1> "Jackin' to Jaxxx - Teenfidelity #648".

Pages are page.set_content fixtures in a local headless chromium. No live site, no credentials.
"""

from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

SCENE_URL = "https://members.kellymadisonmedia.com/episodes/440521210"
CHROME_TITLE = "Members Area - Porn Fidelity : Kelly Madison : Teen Fidelity"
H1 = "Jackin' to Jaxxx - Teenfidelity #648"


def _launch(p):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(headless=True, timeout=20000, args=["--no-sandbox", "--disable-dev-shm-usage"])
    except PlaywrightError as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}")


@pytest.mark.parametrize("leaf", ["4k_h264.mp4", "1080p_h264.mp4", "720p_h264.mp4", "2160p-h265.mp4", "4k.h264.mp4"])
def test_rendition_leaf_with_codec_is_bare(leaf):
    from bulk_downloader.runner_transport import _is_bare_media_leaf

    assert _is_bare_media_leaf(leaf), leaf


@pytest.mark.parametrize("leaf", ["648_jade_jaxxx_4k.mp4", "family-strokes_elise_london_lucy_foxx_full_2160.mp4"])
def test_real_names_stay_names(leaf):
    """Negative control: a stem that names the scene is never replaced."""
    from bulk_downloader.runner_transport import _is_bare_media_leaf, resolve_media_leaf_name

    assert not _is_bare_media_leaf(leaf)
    assert resolve_media_leaf_name(leaf, website_title=H1, scene_url=SCENE_URL) == leaf


def test_bare_rendition_leaf_takes_the_scene_name():
    from bulk_downloader.runner_transport import resolve_media_leaf_name

    assert resolve_media_leaf_name("4k_h264.mp4", website_title=H1, scene_url=SCENE_URL) == (
        "Jackin' to Jaxxx - Teenfidelity #648 [4K].mp4")
    # With no title at all the scene id still names it -- never the rendition leaf.
    assert resolve_media_leaf_name("4k_h264.mp4", website_title="", scene_url=SCENE_URL) == "440521210 [4K].mp4"


def _page_title_choice(html, title, source):
    from bulk_downloader.website_title import scene_heading_over_chrome_title
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.set_content(html)
            return scene_heading_over_chrome_title(page, title, source)
        finally:
            browser.close()


def test_chrome_document_title_yields_to_the_scene_heading():
    html = f"<html><head><title>{CHROME_TITLE}</title></head><body><h1>{H1}</h1></body></html>"
    assert _page_title_choice(html, CHROME_TITLE, "document.title") == H1


@pytest.mark.parametrize(
    "title,h1,source",
    [
        # the usual "<scene> | <brand>" title already carries the heading
        ("Poolside Afternoon | Example Studio", "Poolside Afternoon", "document.title"),
        # a one-word logo heading is not a scene name
        ("Poolside Afternoon", "EXAMPLESTUDIO", "document.title"),
        # og:title is the site's own declared name: never second-guessed
        (CHROME_TITLE, H1, "og:title"),
    ],
)
def test_title_kept_when_it_names_the_scene(title, h1, source):
    html = f"<html><head><title>{title}</title></head><body><h1>{h1}</h1></body></html>"
    assert _page_title_choice(html, title, source) == title
