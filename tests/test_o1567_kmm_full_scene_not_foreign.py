"""fx-kmm-trailer-pick (O1567, live bd2 10.0.70.52 23:18Z/23:21Z, operator-imported session): on the members episode
page https://members.kellymadisonmedia.com/episodes/440521210 the "Episode" block lists the full scene
(/download/video/6023/4k_h264, 17.04 GB) and the "Trailer" block the 820 MB trailer .mp4. Both teenfidelity and
kellymadisonmedia saved the trailer (648_jade_jaxxx_trailer_4k.mp4, 165.6 s; 655_kimora_quin_trailer_4k.mp4, 139.0 s):
the full-scene row was stamped FOREIGN and excluded ("reason": "foreign").

Cause: the page names its work by the numeric id, so page_work_tokens reads only the route word ('episodes',). The
slug rule then treated the download link's last segment '4k_h264' as ANOTHER work's slug. An id-routed page has no
slug identity to compare against; only the numeric-id rules may call a link foreign there.

Page served by page.route in a local headless chromium. No live site, no credentials.
"""

from __future__ import annotations

import pytest

BD_GATE_SCOPE = "module"

M = "https://members.kellymadisonmedia.com"
T = "https://tour-content-cdn.kellymadisonmedia.com"
PAGE_URL = M + "/episodes/440521210"
FULL_4K = M + "/download/video/6023/4k_h264?expires=1&sig=x"


def _li(href, label):
    return f'<li><a href="{href}" class="is-underlined">\n MP4 {label}\n</a></li>'


HTML = (
    "<html><head><title>Members Area - Porn Fidelity : Kelly Madison : Teen Fidelity</title></head><body>"
    '<div class="card-content"><div class="content"><h4>Episode</h4>'
    '<div class="notification is-info">15 of 15 downloads remaining</div><ul class="is-size-7">'
    + _li(FULL_4K, "4k (17.04 GB)")
    + _li(M + "/download/video/6023/1080p_h264?expires=1&sig=x", "1080p (8.17 GB)")
    + _li(M + "/download/video/6023/720p_h264?expires=1&sig=x", "720p (3.94 GB)")
    + '</ul><h4>Trailer</h4><ul class="is-size-7">'
    + _li(T + "/episode/mp4_4k_video_file/440521210/648_jade_jaxxx_trailer_4k.mp4?t=1", "4k (820.44 MB)")
    + _li(T + "/episode/mp4_1080p_video_file/440521210/648_jade_jaxxx_trailer_1080p.mp4?t=1", "1080p (393.39 MB)")
    + "</ul></div></div></body></html>"
)

# The applied template on both sites (user_b4b_teenfidelity_o1517), verbatim from the VM's sites_config.json.
LEARNED = {
    "row_selectors": ["a:has-text('MP4') >> nth=0", "a[href*='.mp4']:has-text('MP4')", "a:has-text('MP4')"],
    "url_attribute": "href",
    "tier_labels_seen": ["4k", "1080p", "720p", "480p"],
}


def _launch(p):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(headless=True, timeout=20000, args=["--no-sandbox", "--disable-dev-shm-usage"])
    except PlaywrightError as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}")


def test_members_episode_picks_the_full_scene_not_the_trailer():
    from bulk_downloader import detect
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=HTML))
            page.goto(PAGE_URL)
            best = detect.find_best_download(page, learned=LEARNED)
        finally:
            browser.close()
    if isinstance(best, tuple):
        best = best[0]
    assert best, "no download candidate at all"
    assert "trailer" not in best["text"], f"picked the trailer: {best['text']!r}; excluded={best.get('_excluded_candidates')}"
    assert "/download/video/6023/4k_h264" in best["text"], best["text"]
    assert not [c for c in best.get("_excluded_candidates") or [] if c.get("reason") == "foreign"]


def test_id_routed_page_has_no_slug_standing():
    from bulk_downloader import detect

    assert detect._candidate_names_another_work(PAGE_URL, FULL_4K) is False
    # The numeric-id rule still speaks for an id-routed page: another episode of the same route is foreign.
    assert detect._candidate_names_another_numeric_work(PAGE_URL, M + "/episodes/927208495") is True


def test_slug_routed_page_still_condemns_another_slug():
    """Negative control: a page whose work IS a slug keeps the slug rule."""
    from bulk_downloader import detect

    page = "https://example-studio.test/scenes/jade-jaxxx-poolside-afternoon"
    assert detect.page_work_tokens(page)
    assert detect._candidate_names_another_work(page, "https://example-studio.test/scenes/kimora-quin-late-night-drive")


# Lens bd-worker-B16-B REFUTE F1 (gen 1 exempted EVERY id-routed page from the slug rule): the misread is the
# rendition leaf, not the page. A slug link on an id-routed page still names another work.
IDROUTE_PAGE = "https://www.tubeexample.test/watch/17134715/"
IDROUTE_HTML = ("<html><body><h1>My scene</h1><div class=\"related\">"
                '<a href="/porn/another-great-4k-scene/">MP4 4K FUCK A FAN</a></div></body></html>')


def test_a_rendition_leaf_names_no_work_on_any_page():
    from bulk_downloader import detect

    assert detect._candidate_route_identity("/download/video/6023/4k_h264") == ()
    assert detect._candidate_route_identity("/download/video/6023/1080p_h264") == ()
    # a slug page's own rendition link is not foreign either
    assert not detect._candidate_names_another_work(
        "https://example-studio.test/scenes/jade-jaxxx-poolside-afternoon", "/download/video/6023/4k_h264")
    # a rendition leaf under ANOTHER work's slug still names that work
    assert detect._candidate_names_another_work(
        "https://example-studio.test/scenes/jade-jaxxx-poolside-afternoon", "/scenes/kimora-quin-late-night/1080p")


def test_id_routed_page_still_condemns_another_slug():
    from bulk_downloader import detect

    assert detect._candidate_names_another_work(IDROUTE_PAGE, "/porn/another-great-4k-scene/"), (
        "KMM_IDROUTE_SLUG_TILE_NOT_FOREIGN: a slug link on an id-routed page lost its FOREIGN stamp")


def test_id_routed_page_never_picks_another_scenes_tile():
    """Real Chromium: an id-routed page with no own control and a learned row that reaches a related tile."""
    from bulk_downloader import detect
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=IDROUTE_HTML))
            page.goto(IDROUTE_PAGE)
            best = detect.find_best_download(
                page, learned={"row_selectors": ["a.download", "div.related a"], "url_attribute": "href"})
        finally:
            browser.close()
    if isinstance(best, tuple):
        best = best[0]
    assert not best or "another-great-4k-scene" not in best.get("text", ""), (
        f"KMM_IDROUTE_WRONG_SCENE_PICKED: {best.get('text')!r}")
