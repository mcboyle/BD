"""dl95-file-examples-1 (harness-work/UIUX-20260928/download-95/RERUN-file-examples-B6-B.md#PER FORMAT, MED).

A job URL that is itself a direct media href (candidate_filter: media_extension) was handed to the page scorer when the
direct fetch did not land. On test2 the stale file-examples storage URLs answer with an HTML 404 page carrying ads, and
the scorer admitted "Advertisement" elements as download candidates: 15 jobs went to needs_review as "Best is 240p",
including 1920x1080 files; bulk-approve then clicked the ads.

Rule: a media_extension job URL never reaches find_best_download. The page's own media (spa-api) may still finish it;
otherwise the job fails with the navigation's real status and content type (a 404 is a permanent failure).

Hermetic: a 127.0.0.1 server and headless Chromium; the runner is ExtractorsMixin + TelemetryMixin._classify_error with
recording stubs for the side-effect methods. The _process_one wiring is checked on the AST, like
tests/test_row446_download_trigger_settle.py does.
"""
from __future__ import annotations

import ast
import http.server
import threading
from pathlib import Path

import pytest

from bulk_downloader import detect, runner_extractors, runner_telemetry

BD_GATE_SCOPE = "module"

AD_404 = b"""<!doctype html><html><head><title>404 Not Found</title></head><body>
<h1>Page not found</h1>
<div class="ad-slot"><a href="/ads/click?id=7&q=240p"><img src="/ads/banner.png" width="426" height="240">Advertisement</a></div>
<div class="ad-slot"><a href="/ads/click?id=8">Advert 240p</a></div>
</body></html>"""
SCENE = b"""<!doctype html><html><body><h1>Scene</h1><a class="download-btn" href="/files/scene-1080p.mp4">Download 1080p</a></body></html>"""


@pytest.fixture(scope="module")
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith("file_example_MP4_1920_18MG.mp4"):
                code, ctype, body = 404, "text/html; charset=UTF-8", AD_404
            elif self.path.startswith("/watch/"):
                code, ctype, body = 200, "text/html; charset=UTF-8", SCENE
            else:
                code, ctype, body = 404, "text/plain", b"nope"
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


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


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    try:
        yield ctx.new_page()
    finally:
        ctx.close()


class _Runner(runner_extractors.ExtractorsMixin):
    site_id = "dl95fe1"

    def __init__(self, spa_result=None):
        self.config = {"name": "dl95-fe1", "download_dir": ""}
        self.failures, self.spa_calls, self.scored = [], [], []
        self._spa_result = spa_result

    _classify_error = runner_telemetry.TelemetryMixin._classify_error

    def _handle_failure(self, url, message, screenshot=""):
        self.failures.append((url, message))

    def _screenshot(self, page, url):
        return ""

    def _update_job(self, *a, **k):
        pass

    def log_event(self, *a, **k):
        pass

    def _try_spa_api_media_extractor(self, url, page):
        self.spa_calls.append(url)
        if self._spa_result is None:  # the real extractor, on the real page
            return runner_extractors.ExtractorsMixin._try_spa_api_media_extractor(self, url, page)
        return self._spa_result


def test_control_the_scorer_admits_the_ad_on_the_404_page(server, page):
    """Fixture control: without the rule, the page scorer returns an ad as the download. This is the defect's input."""
    page.goto(f"{server}/storage/gone/file_example_MP4_1920_18MG.mp4", wait_until="domcontentloaded")
    best = detect.find_best_download(page, "")
    assert best, "fixture drifted: the scorer must find the ad candidate on this 404 page"
    assert "ads/click" in str(best.get("href") or best.get("url") or best), best


def test_media_url_on_a_404_ad_page_fails_permanent_with_the_real_status(server, page):
    url = f"{server}/storage/gone/file_example_MP4_1920_18MG.mp4"
    page.goto(url, wait_until="domcontentloaded")
    r = _Runner()

    assert r._direct_media_url_handled(url, page) is True
    assert r.spa_calls == [url], "the page's own media (spa-api) must still be consulted first"
    assert len(r.failures) == 1, r.failures
    msg = r.failures[0][1]
    assert "HTTP 404" in msg and "text/html" in msg, msg
    assert r._classify_error(msg) == "permanent", msg
    assert "no download button found" not in msg.lower(), "must not count toward the no-button auto-pause"


def test_media_url_finished_by_the_page_media_path_is_not_failed(server, page):
    url = f"{server}/storage/gone/file_example_MP4_1920_18MG.mp4"
    page.goto(url, wait_until="domcontentloaded")
    r = _Runner(spa_result=True)
    assert r._direct_media_url_handled(url, page) is True
    assert r.failures == []


def test_page_url_is_left_to_the_scorer(server, page):
    """Negative control: a normal scene page is not touched -- no spa-api call, no failure, scorer path proceeds."""
    url = f"{server}/watch/scene-1"
    page.goto(url, wait_until="domcontentloaded")
    r = _Runner(spa_result=True)
    assert r._direct_media_url_handled(url, page) is False
    assert r.spa_calls == [] and r.failures == []


def _process_one():
    src = (Path(runner_extractors.__file__).parent / "runner.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == "_process_one":
            return node
    raise AssertionError("_process_one not found")


def test_process_one_consults_the_rule_before_the_scorer_and_returns_on_it():
    fn = _process_one()
    guard_ifs = [n for n in ast.walk(fn) if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Call) and isinstance(n.test.func, ast.Attribute)
                 and n.test.func.attr == "_direct_media_url_handled"]
    assert len(guard_ifs) == 1, "_process_one must call `if self._direct_media_url_handled(url, page): return` once"
    g = guard_ifs[0]
    assert [a.id for a in g.test.args if isinstance(a, ast.Name)] == ["url", "page"]
    assert any(isinstance(s, ast.Return) for s in g.body), "the handled branch must return before scoring"
    scorer_lines = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == "find_best_download"]
    assert scorer_lines, "find_best_download is not called in _process_one"
    assert g.lineno < min(scorer_lines), (g.lineno, scorer_lines)


@pytest.mark.parametrize("url", [
    "https://file-examples.com/storage/fe39b64e106abacd59c0c85/2018/04/file_example_AVI_1920_2_3MG.avi",
    "https://file-examples.com/wp-content/storage/2017/04/file_example_MP4_480_1_5MG.mp4?x=1",
    "https://cdn.example/hls/master.m3u8",
])
def test_media_paths_are_claimed_by_the_rule(url):
    r = _Runner(spa_result=True)
    assert r._direct_media_url_handled(url, page=None) is True and r.spa_calls == [url]


@pytest.mark.parametrize("url", [
    "https://example.com/player?src=clip.mp4",      # the extension is a query value: a page
    "https://example.com/videos/scene-1080p",
    "https://example.com/download/123",             # a download endpoint is not a media path
])
def test_page_like_urls_are_not_claimed(url):
    r = _Runner(spa_result=True)
    assert r._direct_media_url_handled(url, page=None) is False and r.spa_calls == []
