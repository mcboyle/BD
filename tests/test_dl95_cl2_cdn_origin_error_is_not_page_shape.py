"""dl95-cumlouder-2: a CDN origin-error page is a site outage, not a missing download button.

Measured on test2 (harness-work/DOT95-LANE/live-dl-f3/app-shot-url1-fail-0001.png,
watch-cumlouder-2.log): www.cumlouder.com answered with Cloudflare's "Connection timed
out -- Error code 522" page. The worker recorded "[page_shape] No download button found",
which fails with no retry and counts toward the paused_no_button auto-pause.

Contract pinned here:
  - ``runner._handle_cdn_origin_error_page`` recognises a Cloudflare 52x error page,
    routes the job through ``_handle_failure`` with a message that classifies as
    ``transient`` (retried), and leaves the no-button streak untouched;
  - an ordinary page with no download control, and a page that merely mentions
    Cloudflare, are not claimed (the page_shape path still owns them).
"""
from __future__ import annotations

# An ordinary module test: its subject is the module under test, not the tree.
BD_GATE_SCOPE = "module"

from contextlib import contextmanager

import pytest

from bulk_downloader import runner as runner_module
from bulk_downloader.runner_telemetry import TelemetryMixin

URL = "https://www.cumlouder.com/porn-video/fucking-the-babysitter/"

# The shape of Cloudflare's 5xx origin-error template (title, "Error code 522",
# the Browser / Cloudflare / Host diagram), trimmed to the text the check reads.
_CF_522 = """<!DOCTYPE html><html lang="en-US"><head>
<title>www.cumlouder.com | 522: Connection timed out</title></head><body>
<div id="cf-wrapper"><div id="cf-error-details" class="cf-error-details-wrapper">
<h1><span class="cf-error-type">Connection timed out</span>
<span class="cf-error-code">Error code 522</span></h1>
<div>Visit <a href="https://www.cloudflare.com/">cloudflare.com</a> for more information.</div>
<div id="cf-browser-status">You Browser Working</div>
<div id="cf-cloudflare-status">Ashburn Cloudflare Working</div>
<div id="cf-host-status">www.cumlouder.com Host Error</div>
<h2>What happened?</h2><p>The initial connection between Cloudflare's network and the
origin web server timed out. As a result, the web page can not be displayed.</p>
<p>Cloudflare Ray ID: a426b603ddade63b</p></div></div></body></html>"""

_PLAIN_NO_BUTTON = """<!DOCTYPE html><html><head><title>Some scene | Site</title></head>
<body><main><h1>Some scene</h1><p>Trailer coming soon.</p></main></body></html>"""

_MENTIONS_CLOUDFLARE = """<!DOCTYPE html><html><head><title>Help | Site</title></head>
<body><main><h1>Why is the site slow?</h1><p>We use Cloudflare. An error code 522
means our origin timed out; if you see one, try again later.</p></main></body></html>"""


@contextmanager
def _page(html, status=200):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(
                status=status, content_type="text/html", body=html))
            page.goto(URL, wait_until="load")
            yield page
        finally:
            browser.close()


class _RunnerStub:
    site_id = "cumlouder"

    def __init__(self):
        self.config = {"name": "cumlouder"}
        self._consec_no_btn = 4
        self.failures = []

    def _handle_failure(self, url, message, screenshot=""):
        self.failures.append((url, message, screenshot))


def _handler():
    handler = getattr(runner_module, "_handle_cdn_origin_error_page", None)
    assert handler is not None, (
        "DL95-CL2: no CDN origin-error check before the page_shape miss -- a Cloudflare "
        "522 page is recorded as 'No download button found'")
    return handler


def test_cloudflare_522_is_a_transient_outage_not_a_page_shape_miss():
    runner = _RunnerStub()
    with _page(_CF_522, status=522) as page:
        handled = _handler()(runner, page, URL, "cf522.png")

    assert handled is True
    assert runner._consec_no_btn == 4, (
        "DL95-CL2: a site outage advanced the no-download-button pause streak")
    assert len(runner.failures) == 1
    url, message, shot = runner.failures[0]
    assert (url, shot) == (URL, "cf522.png")
    assert "522" in message
    assert "no download button found" not in message.lower()
    assert TelemetryMixin._classify_error(runner, message) == "transient", message


def test_page_without_a_download_control_is_left_to_page_shape():
    """Control: an ordinary page with no control is still a page_shape miss."""
    runner = _RunnerStub()
    with _page(_PLAIN_NO_BUTTON) as page:
        assert _handler()(runner, page, URL, "x.png") is False
    assert runner.failures == [] and runner._consec_no_btn == 4


def test_page_that_only_mentions_cloudflare_is_not_an_outage():
    """Control: prose about Cloudflare errors on a 200 page is not the error page."""
    runner = _RunnerStub()
    with _page(_MENTIONS_CLOUDFLARE) as page:
        assert _handler()(runner, page, URL, "x.png") is False
    assert runner.failures == []


def test_worker_checks_for_the_outage_before_counting_a_no_button_miss():
    """The worker's zero-candidate branch consults the outage check before it
    advances the no-button streak (structural: _process_one needs a live site)."""
    import inspect

    src = inspect.getsource(runner_module.SiteRunner._process_one)
    check = src.find("_handle_cdn_origin_error_page(self, page, url, ss)")
    streak = src.find("self._consec_no_btn+=1")
    assert check != -1, "DL95-CL2: _process_one never consults the CDN origin-error check"
    assert streak != -1 and check < streak, (
        "DL95-CL2: the no-button streak advances before the outage check runs")
