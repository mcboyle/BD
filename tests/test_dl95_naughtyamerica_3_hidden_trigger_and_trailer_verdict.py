"""dl95-naughtyamerica-3 (harness-work/UIUX-20260928/download-95/A9-A/tpl95/naughtyamerica-proof-0416/RESULT.md#D2;
probes harness-work/UIUX-20260928/download-95/fix/dl95-naughtyamerica-3-B16-B/diag_na3*.json): on a logged-in scene
the learned Download tab (a.ui-tabs-anchor) is attached but sits in div#more-info-container, display:none until the
visible "MORE INFO" toggle is clicked. The trigger loop's visible-wait timed out and the exception was swallowed, so
no trigger line was logged; the job then closed needs_review "Only a trailer/preview ... needs a login" on a page
that has logout links.

Now: a skipped trigger is logged with its reason; a learned ``reveal_selectors`` control opens the hidden section
before the loop; and a trailer-only verdict on a page with a logout control does not claim a login is needed.
Scrubbed DOM fixtures served by page.route in local headless chromium; no live site, no login, no credentials.
"""

from __future__ import annotations

import ast
import importlib
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

runner = importlib.import_module("bulk_downloader.runner")

ORIGIN = "https://members.fixture-na.test"
SCENE_URL = ORIGIN + "/scene/fixture-scene-101"
TRAILER = ORIGIN + "/media/fixturescene_trailer_720p.mp4"
TRIG = "a.ui-tabs-anchor[href='#download-options-menu']"
REVEAL = "span#more-info"

# Scrubbed shape of the measured scene (diag_na3.json chain): trigger inside a display:none container.
SCENE_HTML = """<!doctype html><html><body>
<header><a href="/logout">Log Out</a></header>
<span id="more-info" class="more-info-button" onclick="window.__reveals=(window.__reveals||0)+1;
  document.getElementById('more-info-container').style.display='block'">MORE INFO</span>
<div id="more-info-container" style="display:none"><div class="more-info-right"><div id="tabs">
  <ul><li class="download-tab"><a class="ui-tabs-anchor" href="#download-options-menu"
    onclick="document.getElementById('download-options-menu').style.display='block';return false">Download</a></li></ul>
  <div id="download-options-menu" style="display:none"><a class="download-title" href="#">4K (Full Movie)</a></div>
</div></div></div>
</body></html>"""

VISIBLE_TRIGGER_HTML = """<!doctype html><html><body>
<span id="more-info" onclick="window.__reveals=(window.__reveals||0)+1">MORE INFO</span>
<a class="ui-tabs-anchor" href="#download-options-menu">Download</a>
</body></html>"""


def _trailer_html(logged_in):
    header = '<a href="/logout">Log Out</a>' if logged_in else '<a href="/login">Log In</a>'
    return (f"<!doctype html><html><body><header>{header}</header><h1>Fixture scene</h1>"
            f'<video src="{TRAILER}" preload="metadata"></video></body></html>')


def _launch(p):
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(headless=True, timeout=20000,
                                 args=["--no-sandbox", "--disable-dev-shm-usage"])
    except PlaywrightError as e:
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}")


@contextmanager
def _page(body):
    from playwright.sync_api import sync_playwright

    def handler(route, request):
        if request.url == SCENE_URL:
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
            page.wait_for_timeout(200)
            yield page
        finally:
            browser.close()


def _helper(name):
    fn = getattr(runner, name, None)
    if fn is None:
        pytest.fail(f"NA3-RED: runner has no {name} -- a hidden learned trigger is skipped silently "
                    f"and never revealed")
    return fn


def test_na3_a_hidden_learned_trigger_is_named_attached_but_hidden():
    """The measured failure: the visible-wait times out; the reason logged is 'attached but hidden'."""
    reason = _helper("_trigger_skip_reason")
    with _page(SCENE_HTML) as page:
        with pytest.raises(Exception):
            runner._locate_trigger(page, TRIG, timeout_ms=300)
        got = reason(page, TRIG)
        absent = reason(page, "a#no-such-trigger")
    assert got == "attached but hidden", f"NA3-RED: hidden trigger reason is {got!r}"
    assert absent == "not on the page", absent


def test_na3_reveal_selectors_open_the_hidden_trigger_then_it_is_clicked():
    reveal = _helper("_reveal_hidden_trigger")
    with _page(SCENE_HTML) as page:
        clicked = reveal(page, [TRIG], [REVEAL])
        _scope, loc = runner._locate_trigger(page, TRIG, timeout_ms=2000)
        loc.click()
        menu_visible = page.locator("#download-options-menu").is_visible()
    assert clicked == REVEAL, f"NA3-RED: the reveal control was not clicked ({clicked!r})"
    assert menu_visible, "NA3-RED: the revealed Download tab did not open the menu"


def test_na3_control_no_reveal_when_the_trigger_is_visible_or_none_is_learned():
    reveal = _helper("_reveal_hidden_trigger")
    with _page(VISIBLE_TRIGGER_HTML) as page:
        got_visible = reveal(page, [TRIG], [REVEAL])
        clicks = page.evaluate("() => window.__reveals || 0")
    with _page(SCENE_HTML) as page:
        got_none = reveal(page, [TRIG], [])
        still_hidden = not page.locator(TRIG).is_visible()
    assert (got_visible, clicks) == ("", 0), (got_visible, clicks)
    assert got_none == "" and still_hidden, got_none


def _process_one_src():
    src = Path(runner.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == "_process_one":
            return ast.get_source_segment(src, node)
    raise AssertionError("_process_one not found")


def test_na3_the_trigger_loop_reveals_first_and_logs_every_skip():
    """Wiring: the reveal runs before the trigger loop, and the loop's handler logs the skip reason."""
    body = _process_one_src()
    loop = body.find("for tsel in triggers_to_try:")
    rev = body.find('_reveal_hidden_trigger(\n                page, triggers_to_try, learned_dl.get("reveal_selectors")')
    assert loop > 0, "trigger loop not found"
    assert 0 < rev < loop, "NA3-RED: _process_one never reveals a hidden learned trigger before the loop"
    seg = body[loop:body.find("# v3.43.73: Scrapling-based selector recovery.", loop)]
    assert "except Exception: continue" not in seg, "NA3-RED: the trigger loop still swallows a skip silently"
    assert "_trigger_skip_reason(page, tsel)" in seg and "sys.stderr.write" in seg, \
        "NA3-RED: the trigger loop does not log why a trigger was skipped"


class _Stub:
    site_id = "fixturena"

    def __init__(self, tmp_path):
        self.config = {"name": "fixture", "download_dir": str(tmp_path)}
        self.jobs, self.events, self.transfers = [], [], []
        self._spa_api_capture = None

    def _update_job(self, url, status, message, **extra):
        self.jobs.append((status, message, extra))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return "fixture-shot.png"

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.transfers.append(file_url)
        return True

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _runner(tmp_path, monkeypatch):
    from bulk_downloader import runner_extractors as rx

    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    return type("StubRunner", (_Stub, rx.ExtractorsMixin), {})(tmp_path)


def test_na3_a_logged_in_trailer_only_page_does_not_claim_a_login(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    with _page(_trailer_html(logged_in=True)) as page:
        handled = r._try_spa_api_media_extractor(SCENE_URL, page)
    assert handled is True and r.transfers == [], (handled, r.transfers)
    status, msg = r.jobs[-1][0], r.jobs[-1][1]
    assert status == "needs_review", r.jobs
    assert "needs a login" not in msg, f"NA3-RED: logged-in trailer-only verdict claims a login is needed: {msg}"
    assert "fixturescene_trailer_720p.mp4" in msg and "logged in" in msg, msg


def test_na3_control_a_logged_out_trailer_only_page_still_says_log_in(tmp_path, monkeypatch):
    r = _runner(tmp_path, monkeypatch)
    with _page(_trailer_html(logged_in=False)) as page:
        r._try_spa_api_media_extractor(SCENE_URL, page)
    assert r.jobs and r.jobs[-1][0] == "needs_review", r.jobs
    assert "needs a login" in r.jobs[-1][1], r.jobs[-1][1]
