"""Row 722 live (kink.com/shoot/108452, 2026-09-15 06:1xZ, logged in): the
scene page carries a VISIBLE Bootstrap dropdown toggle

    <button class="btn btn-outline-gray-light buy-shoot" data-bs-toggle="dropdown">Download</button>

whose menu holds HIDDEN quality anchors (4K / 1080p / 720p / 480p, each an
``<a class="dropdown-item" href="/shoot/108452/download?filename=...mp4">``),
beside a second "Download Images" toggle whose only item is a ``.zip``.

The standard click path clicked once, waited the full expect_download budget,
and refused with

    Clicked but no download started -- looks like a modal-trigger button -- set Trigger Selector

A human clicks Download, then clicks 4K.  The operator ordered the app to find
the actual way to extract the downloads itself, with no per-site selector, so
the runner now opens a dropdown toggle when the winning candidate scored 0 and
carries no href, re-collects the now-visible menu anchors, and picks by the
existing quality preference (4K > 1080p > ...), video items before image/zip.

The page is rendered in a local headless chromium from an inline fixture
served by ``page.route``.  NO LIVE SITE IS TOUCHED; a missing browser SKIPS.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-722-dd.test"
SCENE_URL = ORIGIN + "/shoot/108452"
NAV_URL = ORIGIN + "/shoot/108453"
DIRECT_URL = ORIGIN + "/shoot/108454"

# Bootstrap-like: the menu is display:none until the toggle's click handler
# adds `show`; no Bootstrap JS is loaded, the handler is inline.
_DROPDOWN_JS = """
<script>
document.addEventListener('click', function (ev) {
  var t = ev.target.closest('[data-bs-toggle=dropdown]');
  document.querySelectorAll('.dropdown-menu.show').forEach(function (m) {
    if (!t || m.previousElementSibling !== t) m.classList.remove('show'); });
  if (t) {
    var menu = t.nextElementSibling;
    menu.classList.toggle('show');
    t.setAttribute('aria-expanded', menu.classList.contains('show'));
    ev.preventDefault();
  }
});
</script>
<style>.dropdown-menu{display:none}.dropdown-menu.show{display:block}</style>
"""

SCENE_HTML = """<!doctype html><html><head><title>Shoot 108452</title>
%s</head><body>
<nav><a href="/">Home</a> <a href="/shoots">All shoots</a></nav>
<h1>Scene 108452</h1>
<div class="dropdown">
  <button class="btn btn-outline-gray-light buy-shoot" type="button"
          data-bs-toggle="dropdown" aria-expanded="false">Download Images</button>
  <ul class="dropdown-menu">
    <li><a class="dropdown-item" href="/shoot/108452/download?filename=108452_images.zip">Images (ZIP)</a></li>
  </ul>
</div>
<div class="dropdown">
  <button class="btn btn-outline-gray-light buy-shoot" type="button"
          data-bs-toggle="dropdown" aria-expanded="false">Download</button>
  <ul class="dropdown-menu">
    <li><a class="dropdown-item" href="/shoot/108452/download?filename=108452_shoot_4k.mp4">4K</a></li>
    <li><a class="dropdown-item" href="/shoot/108452/download?filename=108452_shoot_1080p.mp4">1080p</a></li>
    <li><a class="dropdown-item" href="/shoot/108452/download?filename=108452_shoot_720p.mp4">720p</a></li>
    <li><a class="dropdown-item" href="/shoot/108452/download?filename=108452_shoot_480p.mp4">480p</a></li>
  </ul>
</div>
</body></html>""" % _DROPDOWN_JS

# Negative control 1: a toggle whose menu holds only nav links.
NAV_HTML = """<!doctype html><html><head><title>Shoot 108453</title>
%s</head><body>
<h1>Scene 108453</h1>
<div class="dropdown">
  <button class="btn" type="button" data-bs-toggle="dropdown"
          aria-expanded="false">Download</button>
  <ul class="dropdown-menu">
    <li><a class="dropdown-item" href="/account">Account</a></li>
    <li><a class="dropdown-item" href="/shoots">All shoots</a></li>
  </ul>
</div>
</body></html>""" % _DROPDOWN_JS

# Negative control 2: a visible direct download anchor beside a dropdown.
DIRECT_HTML = """<!doctype html><html><head><title>Shoot 108454</title>
%s</head><body>
<h1>Scene 108454</h1>
<a class="btn" href="/shoot/108454/download?filename=108454_shoot_1080p.mp4">Download 1080p</a>
<div class="dropdown">
  <button class="btn" type="button" data-bs-toggle="dropdown"
          aria-expanded="false">More</button>
  <ul class="dropdown-menu">
    <li><a class="dropdown-item" href="/shoot/108454/download?filename=108454_shoot_720p.mp4">720p</a></li>
  </ul>
</div>
</body></html>""" % _DROPDOWN_JS

# Row 722: an operator-named chrome only (BD_PW_CHROME). The retired sandbox
# home default that tests/test_element_pick_selector.py still carries is a
# ratcheted population (tests/test_sandbox_home_stays_retired.py) and exists
# on no host this runs on; the fallback below is Playwright's own chromium.
CHROME = os.environ.get("BD_PW_CHROME", "")


def _launch(p):
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if CHROME and os.path.exists(CHROME):
        try:
            return p.chromium.launch(headless=True, timeout=20000, args=args,
                                     executable_path=CHROME)
        except Exception:
            pass
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=args)
    except Exception as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip (T5): {e}")


def _serve(route, request):
    url = request.url
    path = url.split("?", 1)[0]
    if "/download" in path:
        name = url.split("filename=", 1)[-1] or "download.bin"
        route.fulfill(status=200, content_type="application/octet-stream",
                      headers={"Content-Disposition":
                               f'attachment; filename="{name}"'},
                      body=b"\x00" * 64)
    elif path == NAV_URL:
        route.fulfill(status=200, content_type="text/html", body=NAV_HTML)
    elif path == DIRECT_URL:
        route.fulfill(status=200, content_type="text/html", body=DIRECT_HTML)
    else:
        route.fulfill(status=200, content_type="text/html", body=SCENE_HTML)


@contextmanager
def _scene_page(url=SCENE_URL):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1000, "height": 760})
            page.route(ORIGIN + "/**", _serve)
            page.goto(url, wait_until="load")
            yield page
        finally:
            browser.close()


def _menu_open(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('.dropdown-menu')]"
        ".map(m => m.classList.contains('show'))")


def _best(page):
    from bulk_downloader.detect import find_best_download
    best = find_best_download(page)
    assert best, "the wide sweep found nothing on the fixture"
    return best


def test_precondition_the_sweep_wins_a_score_0_candidate_and_the_menu_is_shut():
    with _scene_page() as page:
        best = _best(page)
        assert best["score"] == 0, best["text"]
        assert _menu_open(page) == [False, False]


def test_the_runner_opens_the_dropdown_and_picks_4k(capsys):
    """THE ROW: the toggle is opened, the menu re-collected, 4K picked."""
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    with _scene_page() as page:
        best = _best(page)
        picked = _open_dropdown_download_options(page, best, "best", 0)
        assert picked and picked.get("option"), (
            "the dropdown toggle was never opened; the runner would refuse "
            "with the modal-trigger hint: %r" % (picked,))
        opt = picked["option"]
        assert opt["score"] == 2160, opt
        assert "4k.mp4" in (opt["locator"].get_attribute("href") or "")
        assert opt["locator"].is_visible()
        assert picked["count"] == 4, picked
        assert picked["toggle_label"] == "Download", picked
        # The picked item downloads through the normal expect_download click.
        with page.expect_download(timeout=10000) as dli:
            opt["locator"].click()
        assert dli.value.suggested_filename == "108452_shoot_4k.mp4"
        err = capsys.readouterr().err
        assert "download: opened dropdown 'Download' -> 4 option(s), picked 4K" in err, err


def test_quality_preference_picks_1080p_from_the_menu():
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    with _scene_page() as page:
        picked = _open_dropdown_download_options(page, _best(page), "1080,best", 0)
        assert picked["option"]["score"] == 1080, picked


def test_video_items_win_over_the_images_zip_menu():
    """Both toggles read 'Download'; the ZIP-only menu must not be picked."""
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    with _scene_page() as page:
        picked = _open_dropdown_download_options(page, _best(page), "best", 0)
        href = picked["option"]["locator"].get_attribute("href") or ""
        assert href.endswith(".mp4") and ".zip" not in href, href


def test_negative_a_menu_of_nav_links_yields_nothing_and_the_hint_stands():
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    with _scene_page(NAV_URL) as page:
        best = _best(page)
        assert best["score"] == 0
        picked = _open_dropdown_download_options(page, best, "best", 0)
        assert not (picked and picked.get("option")), picked


def test_negative_a_visible_direct_anchor_is_taken_without_opening_any_dropdown():
    from bulk_downloader.runner_transport import _open_dropdown_download_options
    with _scene_page(DIRECT_URL) as page:
        best = _best(page)
        assert best["score"] == 1080, best["text"]
        picked = _open_dropdown_download_options(page, best, "best", 0)
        assert picked is None, picked
        assert _menu_open(page) == [False]


class _FakeLocator:
    def __init__(self):
        self.clicked = 0

    def get_attribute(self, _name):
        return None

    def click(self, **_kw):
        self.clicked += 1


class _FakeDownloadCtx:
    def __init__(self):
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        from playwright.sync_api import TimeoutError as PWTimeout
        raise PWTimeout("no download")


class _FakePage:
    url = SCENE_URL

    def expect_download(self, timeout=0):
        return _FakeDownloadCtx()

    def evaluate(self, *_a, **_k):
        return None

    def title(self):
        return "fixture"


def _runner_seam(monkeypatch, tmp_path, best, consulted):
    from bulk_downloader import runner_transport as rt

    def _fake_open(page, b, qpref, min_res):
        consulted.append((b["text"], qpref))
        return None

    monkeypatch.setattr(rt, "_open_dropdown_download_options", _fake_open)
    monkeypatch.setattr(rt, "_arm_popup_grant_capture",
                        lambda page: (lambda: None, lambda: None))
    monkeypatch.setattr(rt, "gate_candidate_url",
                        lambda *a, **k: ("", ""))
    monkeypatch.setattr(rt, "db_log", lambda *a, **k: None)
    r = rt.TransportMixin.__new__(rt.TransportMixin)
    r.config = {"name": "fixture", "quality_preference": "best"}
    r.site_id = 1
    r.jobs = {}
    r.messages = []
    r._update_job = lambda url, status, msg, **kw: r.messages.append((status, msg))
    r._screenshot = lambda page, url: ""
    r._do_download(_FakePage(), None, SCENE_URL, best, tmp_path, "?")
    return r


def test_runner_seam_consults_the_helper_when_score_is_0(monkeypatch, tmp_path):
    consulted = []
    best = {"locator": _FakeLocator(), "text": "Download", "score": 0,
            "size": 0, "_all_candidates": []}
    r = _runner_seam(monkeypatch, tmp_path, best, consulted)
    assert consulted == [("Download", "best")], consulted
    # The helper found nothing: the existing hint is unchanged.
    assert r.messages[-1][0] == "needs_review"
    assert "modal-trigger" in r.messages[-1][1], r.messages


def test_runner_seam_skips_the_helper_when_a_quality_scored(monkeypatch, tmp_path):
    consulted = []
    best = {"locator": _FakeLocator(), "text": "Download 1080p", "score": 1080,
            "size": 0, "_all_candidates": []}
    _runner_seam(monkeypatch, tmp_path, best, consulted)
    assert consulted == [], consulted
