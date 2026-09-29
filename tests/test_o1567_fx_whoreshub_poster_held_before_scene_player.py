"""O1567 fx-whoreshub-poster-held (bd4, results/bd4/whoreshub.md; bd4 app log 2026-09-29 21:31-21:33Z).

whoreshub is a login site on KVS. On /videos/118048/after-work/ the member page has no download button, so
find_best_download's winner is the lazyload <img> inside a screenshot link
(<a class="item" rel="screenshots" ...><img data-src=".../videos_screenshots/.../1.jpg"></a>, hidden tab)
admitted WITHOUT identity proof. The dl95-pegasproductions-2b member-rendition gate then saw 1.jpg linked on the
logged-out page and HELD the job ("Only a public-tier file found: 1.jpg ... Approve to force") before the scene's
own KVS player was asked -- although its window.flashvars list 480p / 720p / 1080p mp4 files (sibling scenes on
the same run: "chose 1080p from kvs-flashvars"). Approve would have downloaded the JPG.

Rule: a public-tier winner with no identity proof is a guess, not the scene; before the member-rendition hold,
the scene's OWN player media (scene_own_only) answers. It finishes the job (or writes its own min_resolution
hold); only a miss keeps the public-tier hold.

Hermetic: headless Chromium; the job URL is served from a reduced copy of the real page's player + screenshot
markup, the screenshot JPGs answer as images (so the logged-out check calls them public), everything else is
aborted. Transfers are recorded by stubs, never performed.
"""
from __future__ import annotations

from unittest import mock

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

ORIGIN = "https://www.whoreshub.fixture.test"
URL = f"{ORIGIN}/videos/118048/after-work/"
GET = ORIGIN + "/get_file/7/{h}/118000/118048/118048{s}.mp4/?v-acctoken=fixture"
F480 = GET.format(h="3aa81832adf39aab0cf175f76ffb4fe0", s="")
F720 = GET.format(h="4aad02083c517ab3669ae7d2944f0900", s="_720p")
F1080 = GET.format(h="4d4c8462afedf64ccd69a256bea3a060", s="_1080p")
SHOT = ORIGIN + "/get_file/0/{h}/118000/118048/screenshots/{n}.jpg/"
# Real: //wh.cdntrex.com/contents/videos_screenshots/... -- a CDN that answers a cookie-less cross-origin HEAD.
THUMB = "//cdn.fixture.test/contents/videos_screenshots/118000/118048/320x180/{n}.jpg"
GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
# The real page's screenshot tab (id="tab5", hidden until clicked): a fancybox link per screenshot around a
# lazyload <img>. The scorer's winner on the real page is that <img> (score 0, hidden cell, no identity proof).
SHOTS = ('<div id="tab5" class="tab-box" style="display:none"><div class="thumbs block-screenshots">' + "".join(
    f'<div class="thumb"><div class="box"><a href="{SHOT.format(h=f"{n:032x}", n=n)}" class="item" '
    f'rel="screenshots" data-fancybox-type="image"><span class="thumb-img"><img class="img lazyload" '
    f'src="{GIF}" data-src="{THUMB.format(n=n)}" width="320" height="180" alt="Brooklyn Gray - After Work in 4K">'
    f'</span></a></div></div>' for n in (1, 2, 3)) + "</div></div>")
FLASHVARS = f"""<script>var flashvars = {{video_id: '118048', license_code: '$000000000000000',
 video_url: '{F480}', video_url_text: '480p',
 video_alt_url: '{F720}', video_alt_url_text: '720p HD', video_alt_url_hd: '1',
 video_alt_url2: '{F1080}', video_alt_url2_text: '1080p FHD', video_alt_url2_hd: '1',
 preview_url: '//cdn.fixture.test/contents/videos_screenshots/118000/118048/preview.jpg'}};</script>"""


# A related-scenes rail playing ANOTHER scene's 1080p file (hover preview / autoplay) -- never this scene.
FOREIGN = ORIGIN + "/get_file/7/0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f/119000/119777/119777_1080p.mp4/"
RAIL = (f'<div class="related-videos"><a href="{ORIGIN}/videos/119777/other-scene/">'
        f'<video src="{FOREIGN}" muted autoplay playsinline preload="auto"></video></a></div>')


def _html(player=True, rail=False):
    return (f"<!doctype html><html><body><h1>After Work</h1><div id=\"kt_player\"></div>"
            f"{FLASHVARS if player else ''}{SHOTS}{RAIL if rail else ''}</body></html>")


def _route(body):
    def handle(rt):
        u = rt.request.url
        if u == URL:
            return rt.fulfill(status=200, body=body, headers={"content-type": "text/html"})
        if u == FOREIGN:
            return rt.fulfill(status=200, body=b"\x00\x00\x00\x18ftypmp42", headers={"content-type": "video/mp4"})
        if "screenshots/" in u:
            # As the real CDN (bd4 curl -I, 21:5xZ): 200 image/jpeg, access-control-allow-origin "*".
            return rt.fulfill(status=200, body=b"", headers={"content-type": "image/jpeg",
                                                             "access-control-allow-origin": "*"})
        return rt.abort()
    return handle


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    b = None
    try:
        b = pw.chromium.launch(headless=True)
        yield b
    finally:
        if b is not None:
            b.close()
        pw.stop()


class _Ctx:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


def _process(browser, tmp_path, *, force=False, player=True, rail=False):
    import bulk_downloader.runner as runner_module
    import bulk_downloader.runner_extractors as rx
    from bulk_downloader.runner import SiteRunner

    r = SiteRunner("whoreshub-o1567", {"name": "whoreshub-o1567", "download_dir": str(tmp_path), "wait": 0,
                                       "login_url": f"{ORIGIN}/login/", "min_resolution": 1080,
                                       "verify_integrity": False, "embed_metadata": False})
    r.jobs[URL] = {"force_download": force}
    for name in ("_dedup_preflight", "_check_redirect"):
        setattr(r, name, lambda *_a, **_k: "")
    for name in ("_handle_auto_teach_check", "_stash_dedup_check", "_try_plugin_extractor",
                 "_try_ytdlp_untaught"):
        setattr(r, name, lambda *_a, **_k: False)
    for name in ("_check_cookies_or_relogin", "_handle_captcha_check"):
        setattr(r, name, lambda *_a, **_k: True)
    for name in ("_apply_stealth_library_to_page", "_warm_session"):
        setattr(r, name, lambda *_a, **_k: None)
    r._screenshot = lambda *_a, **_k: ""
    r._PAGE_MEDIA_WAIT_S = 0.5
    r.states, r.transfers, r.clicks, r.failures = [], [], [], []
    r._update_job = lambda _u, status, msg="", **_k: r.states.append((status, str(msg)))
    r._handle_failure = lambda _u, msg, *_a, **_k: r.failures.append(msg)
    r._do_download = lambda _p, _c, _u, best, *_a, **_k: r.clicks.append(best)
    r._do_direct_http_download = lambda **k: r.transfers.append(k["file_url"]) or True

    ctx = browser.new_context()
    page = ctx.new_page()
    page.route("**/*", _route(_html(player, rail).encode()))
    try:
        with mock.patch.object(rx, "db_log", lambda *a, **k: None), \
                mock.patch.object(runner_module, "db_log", lambda *a, **k: None), \
                mock.patch.object(runner_module, "_try_scrapling_turnstile", lambda *_a: None), \
                mock.patch.object(runner_module._interstitial, "clear_gates", lambda *_a, **_k: None):
            r._process_one(None, URL, persistent_ctx=_Ctx(page))
    finally:
        ctx.close()
    return r


def test_control_the_dom_best_is_an_unproven_screenshot(browser):
    """Fixture shape guard: as on the real page, the scorer's winner is a screenshot JPG without identity proof."""
    from bulk_downloader import detect
    ctx = browser.new_context()
    try:
        page = ctx.new_page()
        page.route("**/*", _route(_html().encode()))
        page.goto(URL, wait_until="domcontentloaded")
        best = detect.find_best_download(page, "", learned={}, runner=None)
        src = best["locator"].get_attribute("data-src") if best else None
    finally:
        ctx.close()
    assert best and best.get("_no_identity_proof") and "/videos_screenshots/" in (src or ""), (best, src)


def test_the_scene_player_1080p_is_downloaded_not_held_on_the_screenshot(browser, tmp_path):
    r = _process(browser, tmp_path)
    holds = [m for s, m in r.states if s == "needs_review"]
    assert not any("public-tier" in m for m in holds), (
        "WHORESHUB_POSTER_HELD_BEFORE_SCENE_PLAYER: the member-rendition gate held the unproven screenshot "
        f"although the scene's own KVS player lists a 1080p file: {holds}")
    assert r.clicks == [], f"clicked the screenshot: {r.clicks}"
    assert r.transfers == [F1080], (r.transfers, r.states, r.failures)


def test_control_without_scene_player_the_public_tier_hold_is_unchanged(browser, tmp_path):
    """No scene player media: the dl95-pegasproductions-2b hold on the public-tier pick still fires."""
    r = _process(browser, tmp_path, player=False)
    holds = [m for s, m in r.states if s == "needs_review"]
    assert r.transfers == [] and r.clicks == [], (r.transfers, r.clicks)
    assert len(holds) == 1 and holds[0].startswith("Only a public-tier file found: 1.jpg"), r.states


def test_a_related_rail_video_of_another_scene_is_never_taken_for_the_screenshot(browser, tmp_path):
    """Lens B9-B (O1568): the page-media step asks only the scene's OWN player (scene_own_only). With no KVS
    flashvars and a related-rail <video> playing another scene's 1080p file, the public-tier hold stands and
    nothing is transferred -- the other scene's file is not this job's download."""
    r = _process(browser, tmp_path, player=False, rail=True)
    holds = [m for s, m in r.states if s == "needs_review"]
    assert FOREIGN not in r.transfers and r.transfers == [], (
        f"WHORESHUB_FOREIGN_RAIL_VIDEO_TAKEN: {r.transfers} states={r.states}")
    assert len(holds) == 1 and holds[0].startswith("Only a public-tier file found: 1.jpg"), r.states


@pytest.mark.parametrize("unproven, asks_player", [(True, True), (False, False)])
def test_only_an_unproven_public_tier_winner_asks_the_scene_player(monkeypatch, unproven, asks_player):
    """Lens B9-B (O1568) control: the scene-player detour is for a winner WITHOUT identity proof. A public-tier
    winner the scorer proved is this scene's keeps the dl95-pegasproductions-2b hold unchanged."""
    import bulk_downloader.runner as runner_module

    win = ORIGIN + "/get_file/0/x/118000/118048/screenshots/1.jpg/"

    class _View:
        def __init__(self, *_a):
            self.why = ""

        def public(self, urls):
            return set(urls)

        def readable(self):
            return True

    mr = runner_module._member_rendition
    monkeypatch.setattr(mr, "is_login_site", lambda _c: True)
    monkeypatch.setattr(mr, "element_url", lambda loc, _b: loc)
    monkeypatch.setattr(mr, "LoggedOutView", _View)
    monkeypatch.setattr(runner_module, "db_log", lambda *a, **k: None)
    asked, states = [], []

    class _Runner:
        site_id = "whoreshub-o1567"

        def __init__(self):
            self.config = {"name": "whoreshub-o1567", "login_url": f"{ORIGIN}/login/"}
            self.jobs = {URL: {}}

        def _fallback_to_page_media(self, page, url, why, scene_own_only=False):
            asked.append(scene_own_only)
            return True

        def _screenshot(self, *_a):
            return ""

        def _update_job(self, _u, status, msg="", **_k):
            states.append((status, msg))

    class _Page:
        url = URL

    best = {"locator": win, "text": "1.jpg", "_all_candidates": []}
    if unproven:
        best["_no_identity_proof"] = True
    got = runner_module._prefer_member_rendition(_Runner(), _Page(), URL, best)
    assert got is None
    if asks_player:
        assert asked == [True] and states == [], (asked, states)
    else:
        assert asked == [], f"WHORESHUB_PROVEN_WINNER_DETOURED_TO_PAGE_MEDIA: {asked}"
        assert [s for s, _m in states] == ["needs_review"], states
