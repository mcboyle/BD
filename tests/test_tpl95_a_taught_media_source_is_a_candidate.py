"""tpl95-cumlouder-1 / youjizz-1 / redtube-1 -- a template row on a player <source>
is a candidate, and its URL is fetched rather than clicked.

Measured on test2, 2026-09-29 (O1517 template runs):

* cumlouder: learned.download = {row_selectors: [..., "video source[src*='.mp4']"],
  url_attribute: "src"}. In the rendered scene DOM that selector matches two
  <source> elements (cumlouder-rendered-dom.txt), yet the run logged "Finding
  download button..." and then spa-api "chose 0p from page-media": the learned
  rows produced no candidate.
* youjizz: row "video#yj-fluid source[type='application/x-mpegURL']" (the HLS
  master) was never used; the job ended needs_review "below 1080p; got 720p;
  saw: 720p(?):HD /highdefinition/1.html" -- the site-nav link.

ROOT CAUSE. ``detect._find_best_download``'s learned loop admits only
elements it could CLICK: ``if not el.is_visible(): continue``. A <source> has
no layout box, so Playwright reports it not visible and every such row is
dropped before scoring -- the wide sweep (or the spa-api tier) then decides.
Yet the transport was built for exactly this row: ``_do_download``'s Phase 5.7
fast path reads ``url_attribute`` off a ``_via_learned`` winner and fetches it,
never clicking. The second half: that fast path handed an HLS manifest to the
plain HTTP leg, which would save the playlist text as the video; the stream
route (v3.66.819) was consulted only when no direct URL had been extracted.

CONTRACT.
* A learned row that matches a media element (<source>/<video>) whose
  resolved url_attribute carries a URL is a candidate: scored on its URL and
  its ``label`` (the player quality menu text), and returned ``_via_learned``.
* A media element with no URL to read (attribute absent, or the url_attribute
  slot for that selector empty = click-and-capture) is NOT a candidate: it can
  be neither fetched nor clicked. The drop is counted, not silent.
* A learned direct URL that is a stream goes to the segmented downloader.

Hermetic: the fixture is served at the scene URL through ``page.route``; no
request leaves the host.
"""

BD_GATE_SCOPE = "module"

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from bulk_downloader import runner_transport as transport
from bulk_downloader.runner_auth import AuthMixin
from bulk_downloader.detect import find_best_download
from bulk_downloader.runner_util import gate_candidate_url

_FIX = Path(__file__).parent / "fixtures" / "tpl95_template_media_rows"

CUMLOUDER_URL = "https://www.cumlouder.com/porn-video/rock-hard-cock/"
CUMLOUDER_LEARNED = {  # cumlouder-learned-before-v2.json, verbatim
    "row_selectors": ["video#cum_player source[type='video/mp4']",
                      "video source[src*='.mp4']"],
    "url_attribute": "src",
}
CUMLOUDER_SRC = ("https://mediacdnst.cumlouder.com/xl/episodio_72/desktop02.mp4"
                 "?secure=8N8kjb3o4s6FyEja9Ju8sA%3D%3D%2C1790651968")

YOUJIZZ_URL = ("https://www.youjizz.com/videos/hardcore-fuck-with-slutty-"
               "stepmom-penny-barber-is-what-stepson-dreams-about-127905651.html")
YOUJIZZ_LEARNED = {  # youjizz-template-request.json, verbatim
    "row_selectors": ["video#yj-fluid source[type='application/x-mpegURL']",
                      "video source[src*='.m3u8']"],
    "url_attribute": "src",
}


@contextmanager
def _served(fixture, url):
    sync_playwright = pytest.importorskip(
        "playwright.sync_api").sync_playwright
    html = (_FIX / fixture).read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            requested = []

            def _serve(route):
                requested.append(route.request.url)
                if route.request.url == url:
                    route.fulfill(status=200, content_type="text/html",
                                  body=html)
                else:
                    route.abort()
            page.route("**/*", _serve)
            page.goto(url, wait_until="load")
            yield page
        finally:
            browser.close()


# ── detection: the taught <source> row is the candidate ──────────────────

def test_precondition_the_source_rows_match_and_are_reported_not_visible():
    # Positive control for the RED: the selector DOES match, and the element
    # IS reported not visible -- so a visibility gate is what drops it.
    with _served("cumlouder_player_rendered.html", CUMLOUDER_URL) as page:
        loc = page.locator("video source[src*='.mp4']")
        assert loc.count() == 2
        assert loc.first.is_visible() is False
        assert page.locator("video#cum_player source[type='video/mp4']").count() == 0


def test_cumlouder_the_labelled_source_row_wins_via_learned():
    with _served("cumlouder_player_rendered.html", CUMLOUDER_URL) as page:
        best = find_best_download(page, "", learned=CUMLOUDER_LEARNED)
        assert best is not None, "the taught <source> rows produced no candidate"
        assert best.get("_via_learned") is True, (
            f"winner did not come from the template rows: {best.get('text')!r}")
        assert best["_learned_sel"] == "video source[src*='.mp4']"
        assert best["locator"].get_attribute("src") == CUMLOUDER_SRC
        # the player's quality label is the tier the operator sees
        assert best["score"] == 1080
        # the next boundary on the transport chain: the runtime nav gate
        # must accept the media URL it will read off this element
        abs_url, reject = gate_candidate_url(
            best["locator"], CUMLOUDER_URL, url_attr="src",
            learned_sel=best["_learned_sel"], text=best.get("text", ""))
        assert abs_url == CUMLOUDER_SRC
        assert reject == ""


def test_youjizz_the_hls_source_row_wins_over_the_nav_link():
    with _served("youjizz_player.html", YOUJIZZ_URL) as page:
        best = find_best_download(page, "", learned=YOUJIZZ_LEARNED)
        assert best is not None and best.get("_via_learned") is True, (
            f"template HLS row unused; winner={best and best.get('text')!r}")
        assert best["_learned_sel"] == YOUJIZZ_LEARNED["row_selectors"][0]
        src = best["locator"].get_attribute("src")
        assert ".m3u8" in src
        assert "highdefinition" not in best.get("text", "")


REDTUBE_URL = "https://www.redtube.com/191397851"
REDTUBE_LEARNED = {  # redtube-template-request.json, verbatim
    "row_selectors": ["video.mgp_videoElement source[type='video/mp4']",
                      "video.mgp_videoElement"],
    "url_attribute": "src",
}


class _Events:
    def __init__(self):
        self.events = []

    def log_event(self, kind, message, extra=None, **_kw):
        self.events.append((kind, message, extra or {}))


def test_negative_control_live_redtube_rows_name_nothing_to_fetch_and_say_so():
    # The live player carries no URL before playback, so the redtube template
    # rows are neither fetchable nor clickable: no learned candidate, and the
    # drop is COUNTED rather than silent.
    events = _Events()
    with _served("redtube_player_live.html", REDTUBE_URL) as page:
        assert page.locator("video.mgp_videoElement").count() == 1
        best = find_best_download(page, "", learned=REDTUBE_LEARNED,
                                  runner=events)
        assert not (best and best.get("_via_learned"))
    summaries = [x for k, _m, x in events.events
                 if k == "candidate_admission_filtered"]
    assert summaries and summaries[-1].get("media_without_url") == 1, events.events


@pytest.mark.parametrize("learned, why", [
    ({"row_selectors": ["video source[src*='.mp4']"],
      "url_attribute": "data-src"},
     "the resolved url_attribute is absent on the element"),
    ({"row_selectors": ["video source[src*='.mp4']"],
      "url_attribute": [""]},
     "the selector's url_attribute slot is empty (click-and-capture)"),
])
def test_negative_control_a_media_row_with_no_url_to_read_is_not_a_candidate(
        learned, why):
    with _served("cumlouder_player_rendered.html", CUMLOUDER_URL) as page:
        best = find_best_download(page, "", learned=learned)
        assert not (best and best.get("_via_learned")), (
            f"a <source> that can be neither fetched nor clicked was admitted "
            f"({why})")


# ── transport: a learned manifest URL goes to the segmented downloader ───

class _Source:
    def __init__(self, src):
        self.src = src

    def get_attribute(self, name):
        return self.src if name == "src" else None

    def evaluate(self, _script, *_a):
        return [["src", self.src]]

    def click(self):  # pragma: no cover - a <source> is never clicked
        raise AssertionError("a <source> element was clicked")


class _Page:
    def __init__(self, url):
        self.url = url

    def title(self):
        return "scene"

    def evaluate(self, *_a, **_kw):
        return None

    @contextmanager
    def expect_download(self, *, timeout):  # pragma: no cover
        raise AssertionError("expect_download reached for a learned URL")
        yield


class _HlsResult:
    ok = True
    error = None
    error_detail = ""
    bytes_written = 4096


class _Runner(transport.TransportMixin):
    # The production login-wall check on the downloaded file (SiteRunner gets
    # it from AuthMixin); the fixture bytes are not a login page.
    _login_wall_rejects = AuthMixin._login_wall_rejects

    def __init__(self, learned):
        self.site_id = "tpl95"
        self.config = {"name": "tpl95", "use_http_dl": True,
                       "verify_hash": False, "verify_integrity": False,
                       "learned": {"download": learned}}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {}
        self.log = logging.getLogger("tpl95")
        self.http = []
        self.hls = []

    def _http_download(self, _page_url, _page, _ctx, file_url, final_path):
        Path(final_path).write_bytes(b"x" * 4096)
        self.http.append(file_url)
        return (4096, 4096)

    def _hls_download_guarded(self, _hls, url, dest, **_kw):
        Path(dest).write_bytes(b"x" * 4096)
        self.hls.append(url)
        return _HlsResult()

    def _pw_save(self, dl, final_path):  # pragma: no cover
        raise AssertionError("no browser download exists to save")

    def _probe_for_higher_tier(self, url, **_kwargs):
        return url

    def _build_mirror_urls(self, _url):
        return []

    def _screenshot(self, *_args, **_kwargs):
        return ""

    def _update_job(self, *_a, **_kw):
        pass

    def _handle_failure(self, url, message):  # pragma: no cover
        raise AssertionError(f"failure: {message}")

    def _size_on_disk_after_tagging(self, _path, downloaded_size):
        return downloaded_size


def _drive(tmp_path, monkeypatch, learned, src, page_url):
    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity",
                        lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *_a, **_kw: None)
    monkeypatch.setattr(transport.staging_claim, "reserve",
                        lambda path, _identity: (path, path.with_suffix(".part")))
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)
    runner = _Runner(learned)
    runner._do_download(
        _Page(page_url), object(), page_url,
        {"locator": _Source(src), "score": 0, "size": 0, "text": src,
         "_via_learned": True, "_learned_sel": learned["row_selectors"][0],
         "_all_candidates": []},
        Path(tmp_path), "?")
    return runner


def test_a_learned_manifest_url_goes_to_the_segmented_downloader(
        tmp_path, monkeypatch):
    src = "https://cdne-mobile.youjizz.com/videos/127905651/master.m3u8?h=1"
    runner = _drive(tmp_path, monkeypatch, YOUJIZZ_LEARNED, src, YOUJIZZ_URL)
    assert runner.hls == [src], (
        f"learned HLS master not streamed; http leg fetched {runner.http!r}")
    assert runner.http == []


def test_negative_control_a_learned_mp4_url_stays_on_the_http_leg(
        tmp_path, monkeypatch):
    runner = _drive(tmp_path, monkeypatch, CUMLOUDER_LEARNED, CUMLOUDER_SRC,
                    CUMLOUDER_URL)
    assert runner.http == [CUMLOUDER_SRC]
    assert runner.hls == []
