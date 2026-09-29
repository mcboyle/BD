"""dl95-pussyspace-1 (harness-work/UIUX-20260928/download-95/A5-A/RESULT-pussyspace.md#D1, HIGH).

On a pussyspace scene the scorer's candidates were "1080p /1080p/" (a category listing), "HD Porn cat/hd/" (nav) and
"Download /dl/<id>/" (a page whose link is behind an image-text captcha). Clicking fired no download and both jobs went to
needs_review -- while the scene's own player was streaming the video (the app logged "hls manifest detected").

Fix, two parts:
  1. candidate_filter: a root quality/category listing ("/1080p/", "/hd/", "/cat/hd/") is a navigation URL; a resolution
     label alone never overrides it.
  2. _do_download: when the winner is rejected by the nav gate, or its click fires no download, go back to the scene and
     hand the player's own media to the Row 722 page-media path before any needs_review.

  3. (review-shape R1) the fallback keeps the site's min_resolution: the extractor takes only an option at or above it,
     and only a job forced by Approve takes a lower one. Below it -> needs_review. Re-diffed onto main (T147) as the ONE
     fallback shared with tpl95-site-ma-brazzers-1: the extractor is called as a click miss (click_miss_floor), so the
     brazzers trailer filter holds here too (tests/test_dl95_pussyspace_1_rediff_scope.py).

Hermetic: a 127.0.0.1 site shaped like the measured scene, headless Chromium. Parts 1-2 use a recording extractor stub;
part 3 uses the REAL Row 722 extractor (ExtractorsMixin) with only the byte transfer recorded.
"""
from __future__ import annotations

import ast
import http.server
import threading
from pathlib import Path

import pytest

from bulk_downloader import (
    candidate_filter,
    runner_extractors,
    runner_transport,
    spa_media_extract,
)

BD_GATE_SCOPE = "module"

HOST = "www.pussyspace.test"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"S" * 2048
SCENE = b"""<!doctype html><html><body>
<video id="player" muted autoplay src="/media/scene-6167743-720p.mp4"></video>
<a class="btn btn-default" href="/dl/6167743/">Download</a> <a href="/1080p/">1080p</a> <a href="cat/hd/">HD Porn</a>
</body></html>"""
LISTING = b"<!doctype html><html><body><h1>1080p videos</h1><a href='/vid-1-other/'>other</a></body></html>"
BARE = b"<!doctype html><html><body><h1>no player here</h1></body></html>"


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            routes = {"/vid-6167743-scene/": (200, "text/html", SCENE), "/1080p/": (200, "text/html", LISTING),
                      "/vid-9-bare/": (200, "text/html", BARE),
                      "/media/scene-6167743-720p.mp4": (200, "video/mp4", MP4)}
            code, ctype, body = routes.get(self.path, (404, "text/plain", b"nope"))
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


class _Runner(runner_transport.TransportMixin):
    _PAGE_MEDIA_WAIT_S = 3.0

    def __init__(self, finish=True):
        self.calls = []
        self._finish = finish
        self.config = {"min_resolution": 0}
        self.jobs = {}
        self._lock = threading.Lock()

    def _try_spa_api_media_extractor(self, url, page, min_height=0, click_miss_floor=None):
        media = page.evaluate(spa_media_extract.PAGE_MEDIA_JS) or []
        self.calls.append({"url": url, "page_url": page.url, "media": media, "min_height": min_height,
                           "click_miss_floor": click_miss_floor})
        return self._finish and any(m.endswith("720p.mp4") for m in media)


# ── part 1: nav links are not renditions ────────────────────────────────────

@pytest.mark.parametrize("path,text", [("/1080p/", "1080p"), ("/hd/", "HD"), ("/cat/hd/", "HD Porn"),
                                       ("/4k/", "4K"), ("/720p/?page=2", "720p")])
def test_root_quality_or_category_listing_is_a_navigation_url(path, text):
    v = candidate_filter.classify(url=f"https://{HOST}{path}", text=text, page_host=HOST)
    assert not v.accepted and "navigation URL" in v.rejections, v


@pytest.mark.parametrize("path,text", [
    ("/dl/6167743/1080p/", "1080p"),            # download endpoint: strong signal
    ("/1080p/scene-6167743.mp4", "1080p"),     # a media file under the listing: strong signal
    ("/1080p-scene-6167743/", "1080p"),        # a slug that merely starts with the label
    ("/hd-videos/", "HD"),
])
def test_controls_real_renditions_and_slugs_are_not_caught(path, text):
    v = candidate_filter.classify(url=f"https://{HOST}{path}", text=text, page_host=HOST)
    assert "navigation URL" not in v.rejections, (path, v)


# ── part 2: a dud winner falls back to the scene's own media ────────────────

def test_click_that_navigated_to_a_listing_returns_to_the_scene_and_uses_its_media(site, page):
    scene = f"{site}/vid-6167743-scene/"
    page.goto(scene, wait_until="domcontentloaded")
    page.click("a[href='/1080p/']")               # the dud click: navigates, no download event
    page.wait_for_load_state("domcontentloaded")
    assert page.url.endswith("/1080p/"), "fixture: the click must have left the scene"
    r = _Runner()

    assert r._fallback_to_page_media(page, scene, "clicked candidate fired no download") is True
    assert len(r.calls) == 1 and r.calls[0]["url"] == scene
    assert r.calls[0]["page_url"].rstrip("/") == scene.rstrip("/"), "must read the scene, not the listing"
    assert any(m.endswith("/media/scene-6167743-720p.mp4") for m in r.calls[0]["media"]), r.calls


def test_no_media_on_the_page_is_not_a_success(site, page):
    url = f"{site}/vid-9-bare/"
    page.goto(url, wait_until="domcontentloaded")
    r = _Runner()
    assert r._fallback_to_page_media(page, url, "x") is False
    assert r.calls and r.calls[0]["media"] == [], "bounded wait, then the extractor decides (nothing to take)"


def test_without_the_page_media_path_it_declines():
    class Bare(runner_transport.TransportMixin):
        config, jobs, _lock = {}, {}, threading.Lock()
    assert Bare()._fallback_to_page_media(page=None, page_url="https://x.test/", why="x") is False


def _do_download():
    src = Path(runner_transport.__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_do_download":
            return node
    raise AssertionError("_do_download not found")


def test_do_download_consults_the_fallback_before_both_needs_review_exits():
    fn = _do_download()
    guards = [n for n in ast.walk(fn) if isinstance(n, ast.If) and isinstance(n.test, ast.BoolOp)
              and any(isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                      and v.func.attr == "_fallback_to_page_media" for v in n.test.values)]
    assert len(guards) == 2, "the nav-gate rejection and the no-download-event branch must each try the page media"
    for g in guards:
        assert any(isinstance(s, ast.Return) for s in g.body)
        assert any(isinstance(v, ast.UnaryOp) and isinstance(v.operand, ast.Name) and v.operand.id == "probe"
                   for v in g.test.values), "a GCW probe must never start a transfer"
    reviews = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Constant) and n.value == "needs_review"]
    gate_line = next(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == "_gate_reject"
                     and isinstance(n.ctx, ast.Load))
    for g in guards:
        later = [ln for ln in reviews if ln > g.lineno]
        assert later, "each fallback must come before a needs_review write"
    assert min(g.lineno for g in guards) > gate_line


# ── part 3: the fallback keeps min_resolution (real Row 722 extractor) ─────────

class _RealExtractorRunner(runner_transport.TransportMixin, runner_extractors.ExtractorsMixin):
    _PAGE_MEDIA_WAIT_S = 3.0
    site_id = "dl95ps1"

    def __init__(self, tmp_path, min_resolution=None, forced=False, scene=""):
        self.config = {"name": "dl95-ps1", "download_dir": str(tmp_path)}
        if min_resolution is not None:
            self.config["min_resolution"] = min_resolution
        self.jobs = {scene: {"force_download": True}} if forced else {}
        self._lock = threading.Lock()
        self.updates, self.transfers = [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message="", **extra):
        self.updates.append((status, message))

    def log_event(self, *a, **k):
        pass

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append(file_url)
        Path(output_path).write_bytes(MP4)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _fallback_with_real_extractor(site, page, tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    scene = f"{site}/vid-6167743-scene/"
    page.goto(scene, wait_until="domcontentloaded")
    page.click("a[href='/1080p/']")
    page.wait_for_load_state("domcontentloaded")
    r = _RealExtractorRunner(tmp_path, scene=scene, **kw)
    return r, r._fallback_to_page_media(page, scene, "clicked candidate fired no download")


def test_a_720p_player_stream_under_the_default_min_resolution_is_not_done(site, page, tmp_path, monkeypatch):
    """The lens probe's shape: the winner claimed 1080p, the player streams 720p, min_resolution is the default 1080."""
    r, took = _fallback_with_real_extractor(site, page, tmp_path, monkeypatch)
    assert took is False, r.updates
    assert r.transfers == [], "a sub-threshold page stream was transferred"
    assert not any(st == "done" for st, _ in r.updates), r.updates


def test_a_player_stream_at_min_resolution_is_taken(site, page, tmp_path, monkeypatch):
    """Positive control: the same page with min_resolution 720 -- the real extractor finishes the job."""
    r, took = _fallback_with_real_extractor(site, page, tmp_path, monkeypatch, min_resolution=720)
    assert took is True, r.updates
    assert len(r.transfers) == 1 and r.transfers[0].endswith("/media/scene-6167743-720p.mp4"), r.transfers
    assert r.updates and r.updates[-1][0] == "done", r.updates


def test_a_job_forced_by_approve_takes_the_lower_stream(site, page, tmp_path, monkeypatch):
    r, took = _fallback_with_real_extractor(site, page, tmp_path, monkeypatch, forced=True)
    assert took is True and len(r.transfers) == 1, r.updates
