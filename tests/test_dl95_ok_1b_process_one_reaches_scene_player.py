"""dl95-ok-1b (A5-A/RESULT-ok.md D1): the ok scene page's own player source through the whole _process_one flow.

The retained real capture (A5-A/ok/playercap/dom-before.html) has no download button: find_best_download returns
the player's poster <img> (videos_screenshots/.../960x540/1.jpg, score 540) admitted WITHOUT identity proof, so the
"if not best" scene-player fallback is never reached. Against the default min_resolution 1080 the job was held as
"Best is 540p ... (no identity proof)" naming the poster, although THIS scene's player lists 360p/480p/720p files.

Rule: a no-identity-proof winner below min_resolution is held on the scene's PROVEN player files when they exist
("Best is 720p (below 1080p)"); Approve then downloads the 720p player file, never the poster.

Hermetic: headless Chromium; the job URL is fulfilled from a reduced copy of the capture's player markup and every
other request is aborted. Transfers are recorded by stubs, never performed.
"""
from __future__ import annotations

from unittest import mock

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

URL = "https://ok.fixture.test/video/785100/"
GET = "https://ok.fixture.test/get_file/13/{h}/785000/785100/785100{s}.mp4/"
F720 = GET.format(h="1912258e8e056bd2a2289c760e6b8493", s="_720p")
POSTER = "https://static.fixture.test/contents/videos_screenshots/785000/785100/960x540/1.jpg"
POSTER_DIV = f'''<div class="kt-player player-wrap" style="width:100%;height:0;padding-bottom:56.25%">
<img src="{POSTER}" data-src="{POSTER}" width="640" height="360" alt="Julia Ann and Jennifer White Wild Threesome Session"
 style="position:absolute;top:0;left:0;width:100%;height:100%"></div>'''
PLAYER = f'''<video id="my-video" class="video-js" preload="none">
<source src="{GET.format(h='fb0a3fb2877d5d1487b9550ba1dd49c4', s='_360p')}" type="video/mp4" title="360p" label="360p">
<source src="{GET.format(h='1d35343ca66fd8e09a83d771758f7106', s='')}" type="video/mp4" title="480p" label="480p">
<source src="{F720}" type="video/mp4" title="720p" label="720p"></video>'''


def _html(player=True):
    return f"<!doctype html><html><body><h1>Scene 785100</h1>{POSTER_DIV}{PLAYER if player else ''}</body></html>"


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


def _process(browser, tmp_path, *, force, player=True):
    import bulk_downloader.runner as runner_module
    import bulk_downloader.runner_extractors as rx
    from bulk_downloader.runner import SiteRunner

    r = SiteRunner("ok1b-e2e", {"name": "ok1b-e2e", "download_dir": str(tmp_path), "wait": 0,
                                "min_resolution": 1080, "verify_integrity": False, "embed_metadata": False})
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
    r.states, r.transfers, r.clicks, r.failures = [], [], [], []
    r._update_job = lambda _u, status, msg="", **_k: r.states.append((status, str(msg)))
    r._handle_failure = lambda _u, msg, *_a, **_k: r.failures.append(msg)
    r._do_download = lambda _p, _c, _u, best, *_a, **_k: r.clicks.append(best)
    r._do_direct_http_download = lambda **k: r.transfers.append(k["file_url"]) or True

    body = _html(player).encode()
    ctx = browser.new_context()
    page = ctx.new_page()
    page.route("**/*", lambda rt: rt.fulfill(status=200, body=body, headers={"content-type": "text/html"})
               if rt.request.url == URL else rt.abort())
    try:
        with mock.patch.object(rx, "db_log", lambda *a, **k: None), \
                mock.patch.object(runner_module, "db_log", lambda *a, **k: None), \
                mock.patch.object(runner_module, "_try_scrapling_turnstile", lambda *_a: None), \
                mock.patch.object(runner_module._interstitial, "clear_gates", lambda *_a, **_k: None):
            r._process_one(None, URL, persistent_ctx=_Ctx(page))
    finally:
        ctx.close()
    return r


def test_control_the_dom_best_is_the_unproven_poster(browser):
    """Fixture shape guard: the scorer returns the poster at 540 without identity proof, as on the real capture."""
    from bulk_downloader import detect
    ctx = browser.new_context()
    try:
        page = ctx.new_page()
        page.route("**/*", lambda rt: rt.fulfill(status=200, body=_html().encode(),
                                                 headers={"content-type": "text/html"})
                   if rt.request.url == URL else rt.abort())
        page.goto(URL, wait_until="domcontentloaded")
        best = detect.find_best_download(page, "", learned={}, runner=None)
    finally:
        ctx.close()
    assert best and best.get("score") == 540 and best.get("_no_identity_proof"), best


def test_hold_names_the_scene_player_720p_not_the_poster(browser, tmp_path):
    r = _process(browser, tmp_path, force=False)
    holds = [m for s, m in r.states if s == "needs_review"]
    assert r.transfers == [] and r.clicks == [], (r.transfers, r.clicks)
    assert len(holds) == 1, r.states
    assert holds[0].startswith("Best is 720p (below 1080p)"), (
        f"OK1B_HOLD_ON_POSTER: the hold names the unproven poster, not the scene's own player files: {holds[0]!r}")
    assert "720p:720p" in holds[0] and "360p:360p" in holds[0], holds[0]


def test_approve_downloads_the_scene_player_720p(browser, tmp_path):
    r = _process(browser, tmp_path, force=True)
    assert r.clicks == [], "Approve clicked the poster instead of taking the player file"
    assert r.transfers == [F720], (r.transfers, r.states, r.failures)
    assert r.states[-1][0] == "done", r.states


def test_control_without_scene_player_the_poster_hold_is_unchanged(browser, tmp_path):
    """No proven scene files: the no-identity-proof hold is the pre-existing button-path message."""
    r = _process(browser, tmp_path, force=False, player=False)
    holds = [m for s, m in r.states if s == "needs_review"]
    assert r.transfers == [] and r.clicks == [], (r.transfers, r.clicks)
    assert len(holds) == 1 and holds[0].startswith("Best is 540p (below 1080p) (no identity proof"), r.states
