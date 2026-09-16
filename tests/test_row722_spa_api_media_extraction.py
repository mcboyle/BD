"""Row 722 G5 (tiny4k.com, 2026-09-15): a Nuxt SPA scene page renders nav and
ad strips only -- 0 download controls, 0 <source>, 0 h1 -- so the DOM scraper
(``find_best_download``) returns None and the run ends in "No download button
found".  The page had already fetched everything a human's Downloads click
uses: a same-site ``/api/members/releases/<slug>`` JSON (sent with a custom
``x-site`` header the API refuses without) whose ``downloadOptions`` carry
label/quality/filename but NO url, and whose ``streams`` carry direct mp4
URLs; a filename-only option resolves through the record's
``downloads?filename=`` sub-resource to ``{"url": <signed CDN URL>}``.

The fix adds a generic API/media extraction fallback consulted ONLY when the
DOM yielded no candidate: remember the same-site /api/ JSON the page fetched
(with the SPA's own replayable headers), walk it for download-like options,
rank by resolution, resolve through the page's own session, and hand the URL
to the runner's existing direct-download path.

Fixture pages served by ``page.route`` in a local headless chromium; NO LIVE
SITE IS TOUCHED and no login is started (a missing browser SKIPS).
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from urllib.parse import parse_qs, urlparse

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-722.test"
SCENE_URL = ORIGIN + "/members/video/enchanting-fixture"
API_URL = ORIGIN + "/api/members/releases/enchanting-fixture"
ANCHOR_URL = ORIGIN + "/members/video/anchor-fixture"
CDN = "https://cdn.fixture-722.test/content"

# The SPA: nav only, a script that fetches the release record with the
# site header (as the live Nuxt app does), and an unrelated ad <video>
# without a media extension (as on the live page).
SPA_HTML = """<!doctype html><html><body>
<nav><a href="/members">Home</a> <a href="/members/movies">Movies</a></nav>
<video autoplay muted src="https://ads.fixture-722.test/offer_video/6fca3124"></video>
<div id="app"></div>
<script>
fetch('/api/members/releases/enchanting-fixture',
      {headers: {'x-site': location.host, 'accept': 'application/json'},
       credentials: 'include'})
  .then(r => r.json()).then(j => { window.__release = j;
    document.getElementById('app').textContent = 'loaded ' + j.title; });
</script>
</body></html>"""

RELEASE_JSON = {
    "title": "Enchanting Fixture", "cachedSlug": "enchanting-fixture",
    "videoId": 60167,
    "streams": [CDN + "/stream_mp4_1080.mp4?hash=s1",
                CDN + "/stream_mp4_480.mp4?hash=s2",
                CDN + "/stream_mp4_720.mp4?hash=s3"],
    "downloadOptions": [
        {"label": "DOWNLOAD HD", "format": "mp4", "quality": "1080",
         "filename": "fixture-enchanting-1080.mp4"},
        {"label": "DOWNLOAD HD", "format": "mp4", "quality": "2160",
         "filename": "fixture-enchanting-2160.mp4"},
        {"label": "DOWNLOAD HD", "format": "mp4", "quality": "480",
         "filename": "fixture-enchanting-480.mp4"},
        {"label": "DOWNLOAD HD", "format": "mp4", "quality": "720",
         "filename": "fixture-enchanting-720.mp4"},
    ],
    "galleryZipUrl": CDN + "/hq_gallery_zip_images.zip?hash=g",
}

# The negative control: a plain page with a real download anchor.
ANCHOR_HTML = """<!doctype html><html><body>
<h1>Anchor Fixture</h1>
<a href="/files/anchor-fixture-1080p.mp4" class="download-btn">Download 1080p MP4</a>
</body></html>"""

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


class _Served:
    """What the fixture API saw (the test asserts on it)."""
    def __init__(self):
        self.downloads_requests = []


def _serve(served):
    def route_handler(route, request):
        u = urlparse(request.url)
        path = u.path
        if request.url.startswith(SCENE_URL):
            route.fulfill(status=200, content_type="text/html", body=SPA_HTML)
        elif request.url.startswith(ANCHOR_URL):
            route.fulfill(status=200, content_type="text/html", body=ANCHOR_HTML)
        elif path == urlparse(API_URL).path + "/downloads":
            fn = (parse_qs(u.query).get("filename") or [""])[0]
            served.downloads_requests.append(
                {"filename": fn, "x-site": request.headers.get("x-site")})
            if request.headers.get("x-site") != "fixture-722.test":
                route.fulfill(status=404, content_type="application/json",
                              body=json.dumps({"message": "Not found"}))
            else:
                q = re.search(r"(\d{3,4})\.mp4$", fn)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"url": f"{CDN}/download_mp4_{q.group(1) if q else '0'}.mp4?hash=d&download=1&filename={fn}"}))
        elif path == urlparse(API_URL).path:
            if request.headers.get("x-site") != "fixture-722.test":
                route.fulfill(status=404, content_type="application/json",
                              body=json.dumps({"message": "Not found"}))
            else:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(RELEASE_JSON))
        else:
            route.fulfill(status=404, content_type="text/plain", body="nope")
    return route_handler


@contextmanager
def _page(url, capture_factory=None):
    from playwright.sync_api import sync_playwright
    served = _Served()
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.route(ORIGIN + "/**", _serve(served))
            page.route("https://ads.fixture-722.test/**",
                       lambda r, q: r.fulfill(status=404, body=""))
            capture = capture_factory(page) if capture_factory else None
            page.goto(url, wait_until="load")
            page.wait_for_function(
                "() => document.body.innerText.length > 0", timeout=10000)
            if url == SCENE_URL:
                page.wait_for_function("() => !!window.__release", timeout=10000)
            yield page, served, capture
        finally:
            browser.close()


class _Stub:
    """The smallest runner the mixin needs: config, job/event sinks, and a
    recording stand-in for the transfer path."""
    site_id = "fixture722"

    def __init__(self, tmp_path):
        self.config = {"name": "fixture", "download_dir": str(tmp_path)}
        self.jobs = []
        self.events = []
        self.transfers = []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.jobs.append((status, message, extra))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append({"file_url": file_url, "referer": referer,
                               "output_path": output_path})
        with open(output_path, "wb") as fh:
            fh.write(b"\x00" * 16)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _make_runner(tmp_path, monkeypatch):
    from bulk_downloader import runner_extractors as rx
    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    cls = type("StubRunner", (_Stub, rx.ExtractorsMixin), {})
    return cls(tmp_path)


def _install_capture(runner):
    from bulk_downloader.spa_media_extract import ApiCapture

    def factory(page):
        runner._spa_api_capture = ApiCapture(SCENE_URL).install(page)
        return runner._spa_api_capture
    return factory


def test_precondition_the_spa_page_yields_no_dom_candidate():
    from bulk_downloader.detect import find_best_download
    with _page(SCENE_URL) as (page, _served, _c):
        assert page.locator("a[href*='.mp4'], a[download], button").count() == 0
        assert not find_best_download(page), \
            "fixture drifted: the SPA page must yield NO DOM download candidate"


def test_spa_page_with_api_download_options_is_downloaded_via_the_api_fallback(
        tmp_path, monkeypatch):
    """RED before the fix: the runner has no API/media extraction fallback,
    so a page whose only download route is the API JSON ends in
    'No download button found'."""
    from bulk_downloader import runner_extractors as rx
    assert hasattr(rx.ExtractorsMixin, "_try_spa_api_media_extractor"), (
        "no API/media extraction fallback: the DOM yielded nothing and the "
        "runner never consults the same-site /api/ JSON the page fetched "
        "(downloadOptions) nor the media the page requested")
    runner = _make_runner(tmp_path, monkeypatch)
    with _page(SCENE_URL, _install_capture(runner)) as (page, served, capture):
        recs = capture.records()
        assert [r["url"] for r in recs] == [API_URL], \
            f"the capture must remember exactly the release record, got {recs!r}"
        assert recs[0]["headers"].get("x-site") == "fixture-722.test"
        assert "cookie" not in recs[0]["headers"]
        took_over = runner._try_spa_api_media_extractor(SCENE_URL, page)
    assert took_over is True, f"fallback did not take over; events={runner.events}"
    assert len(runner.transfers) == 1
    got = runner.transfers[0]["file_url"]
    assert "download_mp4_2160.mp4" in got, (
        f"the best option is the 2160 download (resolved through the record's "
        f"downloads sub-resource with the page's own headers), got {got}")
    # The resolve went through the page's session with the SPA's header.
    assert served.downloads_requests == [
        {"filename": "fixture-enchanting-2160.mp4", "x-site": "fixture-722.test"}]
    assert runner.transfers[0]["referer"] == SCENE_URL
    assert runner.jobs[-1][0] == "done"
    assert os.path.basename(runner.transfers[0]["output_path"]) == "fixture-enchanting-2160.mp4"


def test_ranking_prefers_resolution_over_list_order_and_api_over_page_media():
    """Pure: the live record lists 1080 first; 2160 must still win, and the
    unlabeled page media never outranks a labelled API option."""
    from bulk_downloader import spa_media_extract as spa
    cands = spa.api_candidates(SCENE_URL, [{"url": API_URL, "headers": {},
                                            "json": RELEASE_JSON}])
    cands += spa.page_media_candidates(
        SCENE_URL, ["https://ads.fixture-722.test/offer_video/6fca3124",
                    CDN + "/trailer_720.mp4"])
    ranked = spa.rank_candidates(cands)
    assert ranked[0]["height"] == 2160 and ranked[0]["source"] == "api:downloadOptions", \
        f"ranking must put the 2160 option first, got {ranked[0]!r}"
    assert ranked[0]["resolve_url"] == API_URL + "/downloads?filename=fixture-enchanting-2160.mp4"
    heights = [c["height"] for c in ranked]
    assert heights == sorted(heights, reverse=True), heights
    # The extension-less ad video is not a media candidate at all.
    assert all("offer_video" not in c["url"] for c in ranked)
    # A stream URL (direct mp4) ranks below the same-height download option.
    at_1080 = [c["source"] for c in ranked if c["height"] == 1080]
    assert at_1080[0] == "api:downloadOptions" and "api:streams" in at_1080


def test_negative_control_a_real_download_anchor_is_unchanged_and_the_api_fallback_is_not_consulted(
        tmp_path, monkeypatch):
    from bulk_downloader.detect import find_best_download
    runner = _make_runner(tmp_path, monkeypatch)
    with _page(ANCHOR_URL, _install_capture(runner)) as (page, served, capture):
        best = find_best_download(page)
        assert best and "1080" in (best.get("text") or ""), \
            f"the anchor page must still be won by its DOM anchor, got {best!r}"
        assert capture.records() == []
        # Even if asked, the fallback has nothing here and moves no bytes.
        assert runner._try_spa_api_media_extractor(ANCHOR_URL, page) is False
    assert runner.transfers == [] and served.downloads_requests == []
    # The runner consults the fallback ONLY inside the no-DOM-candidate arm.
    import inspect
    from bulk_downloader import runner as rmod
    lines = inspect.getsource(rmod).splitlines()
    wired = [i for i, ln in enumerate(lines)
             if ln.strip() == "if self._try_spa_api_media_extractor(url, page):"]
    assert len(wired) == 1, f"expected exactly one wiring of the fallback, found {len(wired)}"
    indent = len(lines[wired[0]]) - len(lines[wired[0]].lstrip())
    guard = next(ln for ln in reversed(lines[:wired[0]])
                 if ln.strip() and (len(ln) - len(ln.lstrip())) < indent)
    assert guard.strip() == "if not best:", \
        f"the API/media fallback must be wired under `if not best:`; enclosing line is {guard.strip()!r}"


def test_the_api_path_harvests_the_page_title_before_the_history_row(
        tmp_path, monkeypatch):
    """Row 722 live (tiny4k, run-0840Z): the file landed but the history row
    had no title, because the API path never crosses the transport boundary
    that calls ``_capture_website_title``. The path must harvest the title
    itself, BEFORE ``db_log`` writes the row."""
    from bulk_downloader import runner_extractors as rx
    order = []
    runner = _make_runner(tmp_path, monkeypatch)
    monkeypatch.setattr(rx, "db_log", lambda *a, **k: order.append("db_log"))
    runner._capture_website_title = lambda page, url: order.append(("title", url))
    with _page(SCENE_URL, _install_capture(runner)) as (page, served, capture):
        assert runner._try_spa_api_media_extractor(SCENE_URL, page) is True
    assert order == [("title", SCENE_URL), "db_log"], (
        "the API/media path wrote the history row without harvesting the page "
        "title (HISTORY-TITLE-EMPTY): %r" % (order,))


def test_negative_control_a_runner_without_a_title_harvester_still_completes(
        tmp_path, monkeypatch):
    """Mixin hosts without SiteRunner's title methods keep working."""
    from bulk_downloader import runner_extractors as rx
    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    runner = _make_runner(tmp_path, monkeypatch)
    assert not hasattr(runner, "_capture_website_title")
    with _page(SCENE_URL, _install_capture(runner)) as (page, served, capture):
        assert runner._try_spa_api_media_extractor(SCENE_URL, page) is True
    assert runner.jobs[-1][0] == "done"

