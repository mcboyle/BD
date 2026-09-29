"""tpl95-pornhoarder-1: a reviewed template's trigger and rows reach into iframes.

Live shape (pornhoarder, 2026-09-29, harness-work/FIX/tpl95-pornhoarder-1-*):
the scene page holds no media; its player is a cross-site iframe
(player.php) whose ``#play-button`` submits a POST form, and the reply embeds
the hoster player in a further iframe. The template's trigger and row
selectors only ever ran against the top document, so the job failed
"[page_shape] No download button found".

Hermetic: a loopback server serves the scene on 127.0.0.1 and the player on
``localhost`` (a different site, so Chromium puts it out of process as it
does live), and the three production seams run against real Chromium frames:
``runner._locate_trigger``, ``detect.find_best_download`` and
``SiteRunner._do_download``'s direct-URL path (HTTP transfer faked).
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

_LEARNED = {"trigger_selectors": ["#play-button"],
            "row_selectors": ["a.dl"], "url_attribute": "href"}

_PLAYER = """<!doctype html><html><body>
<form method="post" action="/player?video=abc">
  <div id="play-button" class="play-button" style="width:200px;height:120px"
       onclick="this.closest('form').submit()">Play</div>
</form></body></html>"""

_PLAYER_AFTER_POST = """<!doctype html><html><body>
<iframe id="hoster" src="/embed/abc" width="640" height="360"></iframe>
</body></html>"""

_EMBED = """<!doctype html><html><body>
<a class="dl" href="/media/scene-abc_1080p.mp4">Download 1080p</a>
</body></html>"""


def _scene(player_url, top_extra="", player_attrs='width="700" height="420"'):
    return ("<!doctype html><html><body><h1>Scene abc</h1>" + top_extra
            + f'<iframe src="{player_url}" {player_attrs}></iframe>'
            "</body></html>")


class _Site:
    def __init__(self):
        self.top_extra = ""
        self.player_path = "/player?video=abc"
        self.player_attrs = 'width="700" height="420"'
        site = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, body):
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.startswith("/scene/"):
                    self._send(_scene(site.player_origin + site.player_path,
                                      site.top_extra, site.player_attrs))
                elif self.path.startswith("/player"):
                    self._send(_PLAYER)
                elif self.path.startswith("/embed/"):
                    self._send(_EMBED)
                elif self.path.startswith("/wrap"):
                    self._send('<!doctype html><html><body><iframe '
                               'src="/embed/ad" width="300" height="200">'
                               "</iframe></body></html>")
                elif self.path.startswith("/blank"):
                    self._send("<!doctype html><html><body></body></html>")
                else:
                    self.send_error(404)

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self._send(_PLAYER_AFTER_POST)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = self.server.server_address[1]
        self.scene_url = f"http://127.0.0.1:{port}/scene/abc"
        self.player_origin = f"http://localhost:{port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site():
    s = _Site()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def page():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            yield browser.new_page()
        finally:
            browser.close()


def _wait_frame(page, needle, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for f in page.frames:
            if needle in f.url:
                try:
                    f.wait_for_load_state("load", timeout=2000)
                    return f
                except Exception:
                    pass
        # Not time.sleep: the sync API only learns of a newly attached frame
        # while it is pumping browser events.
        page.wait_for_timeout(50)
    raise AssertionError(f"fixture frame {needle!r} never loaded")


def _locate_trigger():
    from bulk_downloader import runner
    locate = getattr(runner, "_locate_trigger", None)
    assert locate is not None, (
        "PH-FRAME: the runner looks a trigger up in the top document only "
        "(page.locator(sel).first); an iframe's play control is never found")
    return locate


def _open_player(page, site):
    """Fixture setup: press play inside the player frame directly."""
    page.goto(site.scene_url)
    _wait_frame(page, "/player").locator("#play-button").click()
    return _wait_frame(page, "/embed/")


def test_ph_frame_trigger_inside_an_iframe_is_found_and_clicked(site, page):
    page.goto(site.scene_url)
    player = _wait_frame(page, "/player")
    assert page.locator("#play-button").count() == 0  # precondition
    scope, loc = _locate_trigger()(page, "#play-button", timeout_ms=500)
    assert scope is player, scope
    loc.click()
    embed = _wait_frame(page, "/embed/")
    assert embed.parent_frame is player


def test_ph_frame_learned_row_in_a_nested_frame_is_the_learned_winner(
        site, page):
    from bulk_downloader.detect import find_best_download
    embed = _open_player(page, site)
    best = find_best_download(page, "", learned=_LEARNED)
    assert best and best.get("_via_learned"), (
        f"PH-FRAME: learned row a.dl inside the hoster iframe was not a "
        f"candidate; got {best!r}")
    assert best["_learned_sel"] == "a.dl"
    assert best["_frame_url"] == embed.url


def test_ph_frame_direct_url_resolves_against_the_candidate_document(
        site, page, clean_workdir):
    from bulk_downloader.db import db_init
    from bulk_downloader.migrations import apply_pending
    from bulk_downloader.runner import SiteRunner

    embed = _open_player(page, site)
    loc = embed.locator("a.dl").first
    best = {"locator": loc, "text": "Download 1080p", "score": 1080,
            "size": 0, "_via_learned": True, "_learned_sel": "a.dl",
            "_all_candidates": [], "_frame_url": embed.url}
    db_init()
    assert apply_pending(backup_first=False)["errors"] == 0
    dl_dir = clean_workdir / "downloads"
    dl_dir.mkdir()
    runner = SiteRunner("pornhoarder", {
        "name": "PornHoarder", "download_dir": str(dl_dir),
        "filename_template": "{filename}", "verify_integrity": False,
        "verify_hash": False, "use_http_dl": True,
        "learned": {"download": _LEARNED}})
    fetched = []

    def _http_download(page_url, page_, ctx_, file_url, final_path):
        fetched.append(file_url)
        Path(final_path).parent.mkdir(parents=True, exist_ok=True)
        Path(final_path).write_bytes(b"\0" * 64)
        return 64, 64

    runner._http_download = _http_download
    try:
        runner._do_download(page, None, site.scene_url, best, dl_dir, "1080p")
    finally:
        try:
            runner.stop()
            runner._stop_auto_retry()
        except Exception:
            pass
    want = f"{site.player_origin}/media/scene-abc_1080p.mp4"
    assert fetched == [want], (
        f"PH-FRAME: frame candidate fetched as {fetched}, not {want} -- its "
        f"relative href was resolved against the embedding page")


# ── negative controls: the top document keeps priority, nothing is invented ──

def test_ph_frame_control_top_document_row_wins_without_a_frame_url(
        site, page):
    from bulk_downloader.detect import find_best_download
    site.top_extra = '<a class="dl" href="/media/top_1080p.mp4">Download 1080p</a>'
    site.player_path = "/embed/abc"  # a frame row exists as well
    page.goto(site.scene_url)
    _wait_frame(page, "/embed/")
    best = find_best_download(page, "", learned=_LEARNED)
    assert best and best.get("_via_learned")
    assert "top_1080p" in (best["locator"].get_attribute("href") or "")
    assert "_frame_url" not in best


def test_ph_frame_control_top_document_trigger_keeps_priority(site, page):
    site.top_extra = '<div id="play-button">Play here</div>'
    page.goto(site.scene_url)
    _wait_frame(page, "/player")
    scope, loc = _locate_trigger()(page, "#play-button", timeout_ms=500)
    assert scope is page
    assert loc.inner_text() == "Play here"


def test_ph_frame_control_selector_nowhere_raises_and_finds_nothing(
        site, page):
    from bulk_downloader.detect import find_best_download
    page.goto(site.scene_url)
    _wait_frame(page, "/player")
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    with pytest.raises(PlaywrightTimeout):
        _locate_trigger()(page, "#no-such-trigger", timeout_ms=300)
    best = find_best_download(page, "", learned={
        "row_selectors": ["a.no-such-row"], "url_attribute": "href"})
    assert not (best and best.get("_via_learned"))


# ── cx-worker-1 R1: a document nobody can see is not searched ────────────────
# Playwright reports an element visible inside a display:none iframe's own
# document, so the frame's embedding chain has to be proved rendered.

@pytest.mark.parametrize("hidden", [
    'style="display:none"',                       # the iframe itself
    'style="visibility:hidden"',
    'width="0" height="0"',
    # G3 (cx-worker-1 gen-2 R1): rendered box, but nobody can see it.
    'style="opacity:0;width:640px;height:360px"',
    'style="position:absolute;left:-9999px;top:0;width:640px;height:360px"',
])
def test_ph_frame_hidden_iframe_row_is_never_the_learned_winner(
        site, page, hidden):
    from bulk_downloader.detect import find_best_download
    site.player_path = "/blank"
    site.top_extra = (f'<iframe {hidden} '
                      f'src="{site.player_origin}/embed/ad"></iframe>')
    page.goto(site.scene_url)
    _wait_frame(page, "/embed/ad")
    best = find_best_download(page, "", learned=_LEARNED)
    assert not (best and best.get("_via_learned")), (
        f"PH-FRAME-HIDDEN: learned row taken from a hidden iframe ({hidden}): "
        f"{best.get('_frame_url')!r}")


def test_ph_frame_row_under_a_hidden_ancestor_frame_is_skipped(site, page):
    from bulk_downloader.detect import find_best_download
    site.player_path = "/wrap"          # visible inner iframe ...
    site.player_attrs = 'style="display:none"'  # ... inside a hidden one
    page.goto(site.scene_url)
    _wait_frame(page, "/embed/ad")
    best = find_best_download(page, "", learned=_LEARNED)
    assert not (best and best.get("_via_learned")), (
        f"PH-FRAME-HIDDEN: row taken under a hidden ancestor frame: "
        f"{best.get('_frame_url')!r}")


def test_ph_frame_trigger_in_a_hidden_iframe_is_not_located(site, page):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    site.player_attrs = 'style="display:none"'
    page.goto(site.scene_url)
    _wait_frame(page, "/player")
    with pytest.raises(PlaywrightTimeout):
        _locate_trigger()(page, "#play-button", timeout_ms=300)


def test_ph_frame_control_visible_nested_player_row_still_wins(site, page):
    from bulk_downloader.detect import find_best_download
    site.player_path = "/wrap"          # both frames rendered
    page.goto(site.scene_url)
    embed = _wait_frame(page, "/embed/ad")
    best = find_best_download(page, "", learned=_LEARNED)
    assert best and best.get("_via_learned")
    assert best["_frame_url"] == embed.url


@pytest.mark.parametrize("wrapper", [
    'style="opacity:0"',
    'style="display:none"',
])
def test_ph_frame_iframe_inside_an_unseen_wrapper_is_skipped(site, page,
                                                             wrapper):
    from bulk_downloader.detect import find_best_download
    site.player_path = "/blank"
    site.top_extra = (f'<div {wrapper}><iframe width="640" height="360" '
                      f'src="{site.player_origin}/embed/ad"></iframe></div>')
    page.goto(site.scene_url)
    _wait_frame(page, "/embed/ad")
    best = find_best_download(page, "", learned=_LEARNED)
    assert not (best and best.get("_via_learned")), (
        f"PH-FRAME-HIDDEN: row taken from an iframe under a {wrapper} "
        f"wrapper: {best.get('_frame_url')!r}")
