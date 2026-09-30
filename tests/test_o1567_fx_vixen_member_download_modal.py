"""O1567 fx-vixen-member-download, part 2 (bd4, results/bd4/vixen-cookies.md; bd4 journal 2026-09-29 23:20Z).

With a working member session the vixen scene page (members.vixen.com) shows a DOWNLOAD control, but the
vixen_network template's triggers a:has-text('DOWNLOAD') and [class*=DownloadButton] were "not on the page": the
control is a <button data-test-component="DownloadButton"> with hashed styled-components classes. It opens a
VideoDownloadModal whose tier rows are <span>s (label span data-test-component="VideoDownloadLabel": "4K MP4 UHD",
"HD MP4 1080P", ...), not the <button>s the template's row selectors name; clicking a row makes the site start a
browser download from cdn-download.vixen.com/.../mp4_2160/... (bd4 probe of the site's own profile, 23:3xZ, markup
in results/bd4/fx-vixen-member-download-dom.md). The job fell to the page's 480p stream and was held.

Rule: the vixen_network template opens the member download modal and offers its tier rows, so the 4K row is the
job's pick (the page's 480p stream is below min_resolution and is declined -- part 1).

Hermetic: headless Chromium; the scene URL is fulfilled from a reduced copy of the member page's markup (button,
modal inserted on click, streaming <video>), every other request is aborted. The click-to-download is stubbed.
"""
from __future__ import annotations

import copy
from unittest import mock

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

URL = "https://members.vixen.com/videos/fit-babe-needs-cum"
STREAM = "https://cdn.vixen.com/video/mp4_480/107227/1785239722624/VIXEN_107227_480P.mp4?validfrom=1&hash=fixture"
ROWS = [("4K MP4 UHD", "7176 MB"), ("HD MP4 1080P", "3541 MB"), ("HD MP4 720P", "2373 MB"),
        ("SD MP4 480P", "602 MB"), ("480P MOBILE", "309 MB")]
MODAL = ('<div class="sc-1l9kjq3-0 bVKWjb"><div class="sc-8916vt-1 bxnlyM y1xf8a-0 gsBBuH" '
         'data-test-component="VideoDownloadModal" tabindex="0"><button data-test-component="CloseButton" '
         'class="sc-8916vt-0 dBPotd"></button><div class="y1xf8a-1 gQOKyN"><h3 data-test-component='
         '"VideoDownloadModalTitle" class="y1xf8a-2 fFYmAC">Download your preferred size</h3>'
         + "".join(f'<span class="y1xf8a-4 irDDPD" style="cursor:pointer;display:block"><span data-test-component='
                   f'"VideoDownloadLabel" class="y1xf8a-5 iPubce">{label}</span><span data-test-component='
                   f'"VideoDownloadSize" class="y1xf8a-6 gQawKl">{size}</span></span>' for label, size in ROWS)
         + "</div></div></div>")
PAGE = f"""<!doctype html><html><head><title>Fit Babe Needs Cum: VIXEN</title></head><body>
<h1>Fit Babe Needs Cum</h1><video src="{STREAM}" preload="none"></video>
<button data-test-component="DownloadButton" class="sc-1kcmfma-0 vtcrru-12 vtcrru-13 bGJtSj iKkuiT gtizM"
 onclick="document.getElementById('dlm').innerHTML = window.MODAL"><svg class="vtcrru-3 imXuUd"></svg><span>Download</span></button>
<div id="dlm"></div><script>window.MODAL = {MODAL!r};</script></body></html>"""


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


class _Ctx:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page


def _learned():
    from bulk_downloader import templates
    return {"download": copy.deepcopy((templates.get("vixen_network") or {})["learned"]["download"])}


def _process(browser, tmp_path):
    import bulk_downloader.runner as runner_module
    import bulk_downloader.runner_extractors as rx
    from bulk_downloader.runner import SiteRunner

    r = SiteRunner("vixen-o1567-2", {"name": "vixen-o1567-2", "download_dir": str(tmp_path), "wait": 0,
                                     "min_resolution": 1080, "use_vixen_extractor": True,
                                     "quality_preference": "4320,3160,2880,2160,1440,1080,720",
                                     "applied_template": "vixen_network", "learned": _learned(),
                                     "verify_integrity": False, "embed_metadata": False})
    r.jobs[URL] = {"force_download": False}
    for name in ("_dedup_preflight", "_check_redirect"):
        setattr(r, name, lambda *_a, **_k: "")
    for name in ("_handle_auto_teach_check", "_stash_dedup_check", "_try_plugin_extractor",
                 "_try_ytdlp_untaught"):
        setattr(r, name, lambda *_a, **_k: False)
    for name in ("_check_cookies_or_relogin", "_handle_captcha_check"):
        setattr(r, name, lambda *_a, **_k: True)
    for name in ("_apply_stealth_library_to_page", "_warm_session"):
        setattr(r, name, lambda *_a, **_k: None)
    r._screenshot = lambda *_a, **_k: ""
    r.states, r.transfers, r.clicks, r.failures = [], [], [], []
    r._update_job = lambda _u, status, msg="", **_k: r.states.append((status, str(msg)))
    r._handle_failure = lambda _u, msg, *_a, **_k: r.failures.append(msg)
    r._do_download = lambda _p, _c, _u, best, *_a, **_k: r.clicks.append(
        (best.get("score"), best["locator"].inner_text().split("\n")[0]))
    r._do_direct_http_download = lambda **k: r.transfers.append(k["file_url"]) or True
    body = PAGE.encode()
    ctx = browser.new_context()
    page = ctx.new_page()
    page.route("**/*", lambda rt: rt.fulfill(status=200, body=body, headers={"content-type": "text/html"})
               if rt.request.url == URL else rt.abort())
    try:
        with mock.patch.object(rx, "db_log", lambda *a, **k: None), \
                mock.patch.object(runner_module, "db_log", lambda *a, **k: None), \
                mock.patch.object(runner_module, "_try_scrapling_turnstile", lambda *_a: None), \
                mock.patch.object(runner_module._interstitial, "clear_gates", lambda *_a, **_k: None):
            r._process_one(None, URL, persistent_ctx=_Ctx(page))
    finally:
        ctx.close()
    return r


def test_the_member_modal_4k_row_is_the_pick(browser, tmp_path):
    r = _process(browser, tmp_path)
    assert r.transfers == [], f"the page's 480p stream was downloaded: {r.transfers}"
    assert r.clicks and r.clicks[0][0] == 2160 and r.clicks[0][1].upper().startswith("4K MP4 UHD"), (
        "VIXEN_MEMBER_DOWNLOAD_MODAL_MISSED: the template never opened the DownloadButton modal / offered its tier "
        f"rows; clicks={r.clicks} states={r.states[-3:]} failures={r.failures}")


def test_control_the_template_still_names_its_old_selectors():
    """The new selectors are added, not swapped: a site on the older markup keeps its triggers and rows."""
    dl = _learned()["download"]
    assert "a:has-text('DOWNLOAD')" in dl["trigger_selectors"] and "[class*=DownloadButton]" in dl["trigger_selectors"]
    assert "button:has-text('4K MP4 UHD')" in dl["row_selectors"], dl
