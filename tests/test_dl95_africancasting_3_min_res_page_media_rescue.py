"""dl95-africancasting-3 (harness-work/UIUX-20260928/download-95/A1-A/FINDINGS-A1-A.md#A3, v3.66.1706 on test2):
a members scene page whose DOM holds only RELATED-SCENE cards ("HD | 27:38 | <other title>") made the min-resolution
gate refuse the job -- "below 1080p; got 720p; saw: 720p(?):HD | 27:38 | ..." -- although the page itself had
fetched the scene's own 1080p file (v3.66.1552 downloaded that same scene via "spa-api source=page-media tier=1080").

Row 722 consults the page-media/API extractor only when the DOM has NO candidate, so a single badge guess blocks it.
The fix consults it in the refusal arm as well, bounded by min_height=min_resolution: it takes over only with an
option at or above min_resolution, otherwise the refusal runs unchanged. Row 701's candidate population is untouched.

Fixture pages served by page.route in a local headless chromium; no live site, no login, no credentials.
"""

from __future__ import annotations

import inspect
import sys
from contextlib import contextmanager

import pytest

BD_GATE_SCOPE = "module"

ORIGIN = "https://fixture-ac3.test"
SCENE_URL = ORIGIN + "/video/fixture-scene-looking-for-a-meal-2651.html"
OWN_1080 = ORIGIN + "/media/fixture-scene-looking-for-a-meal-2651_1080p.mp4"
OWN_720 = ORIGIN + "/media/fixture-scene-looking-for-a-meal-2651_720p.mp4"


def _card(slug, dur, title):
    # block children, so the card's text reads "HD\n27:38\n<title>" exactly as in the evidence
    return (
        f'<a class="thumb" href="/video/{slug}.html"><div class="badge">HD</div>'
        f'<div class="dur">{dur}</div><div class="t">{title}</div></a>'
    )


CARDS = "".join(
    _card(s, d, t)
    for s, d, t in (
        ("other-scene-one-1001", "27:38", "Other scene one ain't"),
        ("other-scene-two-1002", "28:36", "Other scene two got what"),
        ("other-scene-three-1003", "33:55", "Other scene three getting"),
    )
)


def _html(media):
    fetches = "".join(f"fetch('{m}').catch(()=>{{}});" for m in media)
    return f"""<!doctype html><html><body>
<nav><a href="/">Home</a> <a href="/videos">Videos</a></nav>
<h1>Fixture scene looking for a meal</h1>
<div id="player"></div>
<section class="related"><h2>Related scenes</h2>{CARDS}</section>
<script>{fetches} window.__fetched = true;</script>
</body></html>"""


def _launch(p):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(
            headless=True,
            timeout=20000,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
    except PlaywrightError as e:
        pytest.fail(
            f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}"
        )


@contextmanager
def _page(media):
    from playwright.sync_api import sync_playwright

    body = _html(media)

    def handler(route, request):
        if request.url.startswith(SCENE_URL):
            route.fulfill(status=200, content_type="text/html", body=body)
        elif request.url.endswith(".mp4"):
            route.fulfill(status=200, content_type="video/mp4", body=b"\x00" * 64)
        else:
            route.fulfill(status=404, content_type="text/plain", body="nope")

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            page.route(ORIGIN + "/**", handler)
            page.goto(SCENE_URL, wait_until="load")
            page.wait_for_function("() => window.__fetched === true", timeout=10000)
            page.wait_for_timeout(300)
            yield page
        finally:
            browser.close()


class _Stub:
    site_id = "fixtureac3"

    def __init__(self, tmp_path):
        self.config = {"name": "fixture", "download_dir": str(tmp_path)}
        self.jobs, self.events, self.transfers = [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.jobs.append((status, message, extra))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append(file_url)
        with open(output_path, "wb") as fh:
            fh.write(b"\x00" * 16)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _runner(tmp_path, monkeypatch):
    from bulk_downloader import runner_extractors as rx

    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    return type("StubRunner", (_Stub, rx.ExtractorsMixin), {})(tmp_path)


def test_precondition_the_dom_best_is_a_720_badge_guess_below_1080(monkeypatch):
    """The evidence shape: the DOM scorer's best is a related card's 'HD' badge (720, size unknown).

    Since dl95-africancasting-4 (_title_shaped_label_only) a card whose text carries a TITLE no longer
    scores on its tier word, so the measured "HD | 27:38 | <title>" card yields no DOM candidate and
    row 722's no-candidate arm takes the page media. A badge-only card (no title) still yields the
    sub-min UNKNOWN guess, which is what reaches the refusal arm under test here."""
    from bulk_downloader.detect import find_best_download

    with _page([OWN_1080]) as page:
        assert not find_best_download(page), "a titled related card scored as this scene's quality"
    badge_only = ('<a class="thumb" href="/video/other-scene-one-1001.html"><div class="badge">HD</div>'
                  '<div class="dur">27:38</div></a>')
    monkeypatch.setattr(sys.modules[__name__], "CARDS", badge_only)
    with _page([OWN_1080]) as page:
        best = find_best_download(page)
    assert best, (
        "fixture no longer yields a DOM candidate -- the gate would not run and this test measures nothing"
    )
    assert 0 < best["score"] < 1080, best
    assert not best.get("size"), best
    assert best.get("_no_identity_proof"), (
        best
    )  # Row 701 UNKNOWN-tier admission, as logged on test2


def test_refusal_arm_takes_the_pages_own_1080_file(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    with _page([OWN_720, OWN_1080]) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page, min_height=1080)
    assert took is True, f"page media 1080 not taken: events={r.events}"
    assert r.transfers == [OWN_1080], r.transfers


def test_min_height_never_downloads_below_min_resolution(tmp_path, monkeypatch):
    """Negative control: only a 720 file on the page -> no takeover, no bytes; the refusal stays."""
    r = _runner(tmp_path, monkeypatch)
    with _page([OWN_720]) as page:
        took = r._try_spa_api_media_extractor(SCENE_URL, page, min_height=1080)
    assert took is False
    assert r.transfers == [], r.transfers


def test_the_refusal_arm_consults_the_bounded_extractor_before_needs_review():
    """Wiring: inside the min-resolution refusal, the bounded rescue runs before the needs_review update."""
    from bulk_downloader import runner as rmod

    src = inspect.getsource(rmod)
    gate = src.index(
        'if min_res>0 and best["score"]>0 and best["score"]<min_res and not forced:'
    )
    rescue = src.find(
        "and self._try_spa_api_media_extractor(url, page, min_height=min_res)", gate
    )
    trigger = src.find('if (best.get("_no_identity_proof")', gate)
    assert gate < trigger < rescue, (
        "the rescue must be bounded to refusals made without identity proof"
    )
    refuse = src.find('self._update_job(url,"needs_review",msg,screenshot=ss)', gate)
    assert rescue != -1, (
        "the min-resolution refusal arm does not consult the page-media/API extractor"
    )
    assert gate < rescue < refuse, (gate, rescue, refuse)
