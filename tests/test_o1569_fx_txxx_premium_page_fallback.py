"""fx-txxx-premium-page-fallback (O1569 Cut D; T175 final sweep, bd 10.0.70.50, results/bd/TXXX-T175-PRODUCT-H3-B.md).

txxx scene 99939 shows the site's PREMIUM member download link. The built-in txxx_network template's learned row
`a[href*='/download/']` matched it, the learned fast path resolved it (https://member.txxx.com/download/?video=99939)
and saved the answer -- a 123,471 B HTML page -- as the scene file. Integrity then failed it "MP4 file is incomplete
(no moov atom)": status failed, and the scene's public HLS stream (the one the previous PASS came from, "720p via
scene-stream") was never tried.

Contract: an HTML document is never kept as the media. It is moved to _failed/ and the scene's own page media (the
public stream) is tried next; with no page media the row is needs_review naming the HTML answer, never a false
"no moov atom" failure. A learned link that answers with real media still lands unchanged.

Hermetic: 127.0.0.1 origin (scene pages in the live shape: relative learned /download/ link + inline player HLS;
the premium page is synthetic HTML of the row's size), real HLS built by ffmpeg, real chromium page (BD's cloak),
real hls_downloader + ffprobe, real IntegrityMixin. The HTTP transfer is a plain urllib GET of the learned URL.
"""
from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import threading
import urllib.request

import pytest

from bulk_downloader import runner_extractors, runner_integrity, runner_transport

BD_GATE_SCOPE = "module"
DIAG = "FX_TXXX_PREMIUM_PAGE_SAVED_AS_MEDIA"

UUID = "5f0c2a4e-99a3-4f6e-8a51-0b7f1e6c9939"
MASTER_720 = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=763904,RESOLUTION=854x480,NAME="480p"\nhls-480p.m3u8\n'
    '#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1327104,RESOLUTION=1280x720,NAME="720p"\nhls-720p.m3u8\n')
# The premium member download page: site chrome, a "get premium" form, no media. Padded to the row's 123,471 B.
_PREMIUM = ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Download - TXXX.COM Premium</title>'
            '</head><body><header><a href="/">TXXX</a><a href="/premium/">Premium</a><a href="/login/">Log in</a></header>'
            '<main class="premium-download"><h1>Download in HD</h1><p>Downloads are available to premium members.</p>'
            '<form action="/premium/checkout/" method="post"><button>Get Premium</button></form></main>')
_TAIL = " --></body></html>"
PREMIUM_PAGE = (_PREMIUM + "<!-- " + "x" * (123_471 - len(_PREMIUM) - len("<!-- ") - len(_TAIL)) + _TAIL).encode()
assert len(PREMIUM_PAGE) == 123_471


def _tool(name):
    path = shutil.which(name)
    if not path:
        pytest.fail(f"{name} is required: the segmented transfer and the landed probe under test use it")
    return path


def _probe_height(path):
    out = subprocess.run([_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=height", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True, timeout=30).stdout
    return int(json.loads(out)["streams"][0]["height"])


def _scene(base, download_href, with_player):
    player = (f"html5player.setVideoHLS('{base}/tok/{UUID}/0/hls.m3u8');" if with_player else "")
    return ("<!doctype html><html><head><title>Sharon fait la pute a la cave - TXXX.COM</title></head><body>"
            f'<div id="player"><script>var html5player = {{setVideoHLS() {{}}}};{player}</script></div>'
            f'<div class="video-actions"><a class="btn-download" href="{download_href}">Download</a></div>'
            "</body></html>").encode()


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    root = tmp_path_factory.mktemp("cdn")
    for name, size in (("480p", "854x480"), ("720p", "1280x720")):
        subprocess.run([_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=5",
                        "-t", "2", "-pix_fmt", "yuv420p", "-g", "5", "-f", "hls", "-hls_time", "1",
                        "-hls_segment_filename", str(root / f"seg-{name}-%d.ts"), str(root / f"hls-{name}.m3u8")],
                       check=True, timeout=120)
    subprocess.run([_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=5",
                    "-t", "2", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(root / "full.mp4")],
                   check=True, timeout=60)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), None)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    pages = {
        "/videos/99939/sharon/": _scene(base, "/download/?video=99939", True),        # the row
        "/videos/99940/nostream/": _scene(base, "/download/?video=99940", False),     # premium page, no stream
        "/videos/99941/realfile/": _scene(base, "/download/real/?video=99941", True),  # control: link is media
        f"/tok/{UUID}/0/hls.m3u8": MASTER_720.encode(),
    }

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            leaf = path.rsplit("/", 1)[-1]
            ctype = "text/html; charset=utf-8"
            if path in pages:
                body = pages[path]
                if path.endswith(".m3u8"):
                    ctype = "application/vnd.apple.mpegurl"
            elif path == "/download/real/":
                body, ctype = (root / "full.mp4").read_bytes(), "video/mp4"
            elif path == "/download/":
                body = PREMIUM_PAGE
            elif path.startswith("/tok/") and (root / leaf).is_file():
                body = (root / leaf).read_bytes()
                ctype = "application/vnd.apple.mpegurl" if leaf.endswith(".m3u8") else "video/mp2t"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv.RequestHandlerClass = H
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield base
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


@pytest.fixture(scope="module")
def page():
    from bulk_downloader import cloak

    with cloak.cloaked_page(headless=True, config={"browser_backend": "playwright"},
                            viewport={"width": 1280, "height": 720}) as p:
        yield p


class _Runner(runner_transport.TransportMixin, runner_extractors.ExtractorsMixin, runner_integrity.IntegrityMixin):
    _PAGE_MEDIA_WAIT_S = 1.0
    site_id = "0ce0eef8"

    def __init__(self, tmp_path):
        self.config = {"name": "txxx", "download_dir": str(tmp_path / "dl"), "min_resolution": 720,
                       "learned": {"download": {"row_selectors": ["a[href*='/download/']"], "url_attribute": "href"}}}
        self.jobs = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.updates, self.events, self.segmented, self.direct = [], [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message="", **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message="", url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return ""

    def _build_mirror_urls(self, file_url):
        return []

    def _probe_for_higher_tier(self, file_url, referer=""):
        return file_url

    def _run_http_attempts_with_resume(self, page_url, page, ctx, file_url, attempt_urls, final_path):
        # The learned fast path's HTTP leg: fetch the learned URL and save whatever it answers, as the app does.
        self.direct.append(file_url)
        with urllib.request.urlopen(file_url, timeout=30) as resp:
            body = resp.read()
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(body)
        return len(body), len(body)

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.segmented.append(manifest_url)
        return _hls.download(manifest_url, output_path, **kwargs)

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


@pytest.fixture
def run(origin, page, tmp_path, monkeypatch):
    for mod in (runner_extractors, runner_transport, runner_integrity):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})

    def _run(route):
        scene = origin + route
        page.goto(scene, wait_until="domcontentloaded")
        r = _Runner(tmp_path)
        loc = page.locator("a[href*='/download/']").first
        # Positive control: the template's learned row really matches the scene's download link.
        assert "/download/" in (loc.get_attribute("href") or "")
        best = {"_via_learned": True, "_learned_sel": "a[href*='/download/']", "score": 0, "locator": loc,
                "text": "Download"}
        r._do_download(page, None, scene, best, tmp_path / "dl", "")
        dl = tmp_path / "dl"
        landed = sorted(p for p in dl.rglob("*") if p.is_file() and "_failed" not in p.parts) if dl.exists() else []
        failed = sorted((dl / "_failed").glob("*")) if (dl / "_failed").exists() else []
        return r, landed, failed

    return _run


def _is_html(path):
    return path.read_bytes()[:64].lstrip().lower().startswith((b"<!doctype html", b"<html"))


def test_the_premium_page_is_not_the_scene_the_public_stream_is(run):
    r, landed, failed = run("/videos/99939/sharon/")
    assert r.direct and "/download/?video=99939" in r.direct[0], f"learned link not taken first: {r.direct}"
    kept_html = [p.name for p in landed if _is_html(p)]
    assert not kept_html, f"{DIAG}: the premium HTML page was kept as the media: {kept_html} updates={r.updates[-3:]}"
    assert r.segmented, (f"{DIAG}: no fallback to the scene's public stream after the learned link answered HTML; "
                         f"updates={r.updates[-3:]}")
    media = [p for p in landed if not _is_html(p)]
    assert media and _probe_height(media[0]) == 720, f"public 720p stream did not land: {landed}"
    assert r.updates[-1][0] == "done", r.updates[-3:]
    assert failed and all(_is_html(p) for p in failed), f"the HTML answer is kept in _failed/ as evidence: {failed}"
    assert not any("moov" in m for _s, m in r.updates), r.updates


def test_no_page_media_is_needs_review_naming_the_html_answer(run):
    r, landed, failed = run("/videos/99940/nostream/")
    assert not landed, f"{DIAG}: something was kept as the media: {[p.name for p in landed]}"
    status, msg = r.updates[-1]
    assert status == "needs_review", f"{DIAG}: expected needs_review, got {r.updates[-3:]}"
    assert "HTML" in msg and "/download/" in msg, msg
    assert "video=99940" not in msg, "the query string is not echoed into the row message"
    assert r.segmented == []
    assert failed and all(_is_html(p) for p in failed), f"the HTML answer is kept in _failed/ as evidence: {failed}"


def test_a_learned_link_that_answers_media_still_lands(run):
    """Control: the learned fast path is unchanged when the link is the file."""
    r, landed, _failed = run("/videos/99941/realfile/")
    assert r.segmented == [], f"a media answer must not be replaced by the player stream: {r.segmented}"
    assert len(landed) == 1 and not _is_html(landed[0]) and _probe_height(landed[0]) == 720, landed
    assert r.updates[-1][0] == "done", r.updates[-3:]


# E2 (lens bd-worker-C2-C, VERDICT REFUTE r1): the guard above covers _do_download only. The extractors that save through
# runner_transport._do_direct_http_download (plugin, library, jsonapi, vixen, dl8, aylo) took any 200 body -- the same
# premium page landed as "Saved: download.mp4" done. The REAL _do_direct_http_download runs here; only the extractor's
# answer is a stub.
def _plugin(origin, tmp_path, monkeypatch, media_url):
    from bulk_downloader import plugins
    for mod in (runner_extractors, runner_transport, runner_integrity):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_extractors, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(plugins, "get_extractor", lambda site_id: (lambda url, ctx: {"video_url": origin + media_url}))
    r = _Runner(tmp_path)
    ok = r._try_plugin_extractor(origin + "/videos/99939/sharon/")
    dl = tmp_path / "dl"
    landed = sorted(p for p in dl.rglob("*") if p.is_file() and "_failed" not in p.parts) if dl.exists() else []
    failed = sorted((dl / "_failed").glob("*")) if (dl / "_failed").exists() else []
    return r, ok, landed, failed


def test_e2_a_plugin_video_url_answering_html_is_not_saved_done(origin, tmp_path, monkeypatch):
    r, ok, landed, failed = _plugin(origin, tmp_path, monkeypatch, "/download/?video=99939")
    kept_html = [p.name for p in landed if _is_html(p)]
    assert not ok and not kept_html and not [u for u in r.updates if u[0] == "done"], (
        f"{DIAG}_E2: the plugin extractor's HTML answer was saved done: ok={ok} kept={kept_html} updates={r.updates[-2:]}")
    assert failed and all(_is_html(p) for p in failed), f"the page body is kept in _failed/ as evidence: {failed}"
    assert [m for k, m in r.events if k == "direct_http_not_media" and "/download/" in m and "video=" not in m], r.events


def test_e2_control_a_plugin_video_url_answering_media_lands(origin, tmp_path, monkeypatch):
    """Positive control: the same harness and REAL transport land a real media answer done."""
    r, ok, landed, _failed = _plugin(origin, tmp_path, monkeypatch, "/download/real/?video=99941")
    assert ok and r.updates[-1][0] == "done", r.updates[-2:]
    assert len(landed) == 1 and _probe_height(landed[0]) == 720, landed


@pytest.mark.parametrize("head,page", [
    (b"\xef\xbb\xbf\n<!DOCTYPE html><html>", True),
    (b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code></Error>', True),
    (b'{"error":"premium only"}', True),
    (b'<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011">', False),
    (b"\x00\x00\x00\x20ftypisom<html>", False),
    (b"\x47\x40\x00\x10\x00", False),
    (b"#EXTM3U\n", False),
])
def test_page_body_predicate(head, page):
    assert runner_transport.TransportMixin._is_page_body(head) is page
