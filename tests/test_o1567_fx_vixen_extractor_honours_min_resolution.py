"""O1567 fx-vixen-member-download, part 1 (bd4, results/bd4/blacked-cookies.md; bd4 journal 2026-09-29 23:20-23:21Z).

With the operator's member session imported, blacked (min_resolution 1080, quality_preference 4320..720) closed the
scene DONE as "Vixen 480p MP4 (849.7 MB) via video_src" (vixen_done "480p via video_src (avail: [480])"): the Vixen
extractor took the page's streaming <video src> (cdn .../mp4_480/...) and downloaded it without the min_resolution
hold every other path applies. The same run on vixen fell through to the DOM path and was held "Best is 480p (below
1080p) -- Approve to force". The member page offers 4K/1080p downloads (DOWNLOAD control in the app's own screenshot).

Rule: the Vixen extractor never closes a job below the site's min_resolution unless the job is forced (Approve); it
declines (returns False) so the caller's DOM path runs and, failing a better tier, holds needs_review.

Hermetic: headless Chromium; the scene URL is fulfilled from a reduced page carrying the streaming <video src>, every
other request is aborted. Transfers are recorded by stubs, never performed.
"""
from __future__ import annotations

from unittest import mock

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

URL = "https://www.vixen.com/videos/fit-babe-needs-cum"
SRC = "https://cdn.vixen.com/video/mp4_{t}/107227/1785239722624/VIXEN_107227_{t}P.mp4?validfrom=1&validto=2&hash=fixture"


def _html(tier):
    return (f"<!doctype html><html><head><title>Fit Babe Needs Cum - VIXEN</title></head><body>"
            f"<h1>Fit Babe Needs Cum</h1><video src=\"{SRC.format(t=tier)}\" preload=\"none\"></video></body></html>")


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


def _run(browser, tmp_path, *, tier, force=False):
    import bulk_downloader.runner as runner_module
    import bulk_downloader.runner_extractors as rx
    from bulk_downloader.runner import SiteRunner

    r = SiteRunner("vixen-o1567", {"name": "vixen-o1567", "download_dir": str(tmp_path), "wait": 0,
                                   "min_resolution": 1080,
                                   "quality_preference": "4320,3160,2880,2160,1440,1080,720",
                                   "use_vixen_extractor": True, "verify_integrity": False,
                                   "embed_metadata": False})
    r.jobs[URL] = {"force_download": force}
    r.states, r.transfers = [], []
    r._update_job = lambda _u, status, msg="", **_k: r.states.append((status, str(msg)))
    r._do_direct_http_download = lambda **k: r.transfers.append(k["file_url"]) or True
    r._embed_metadata_if_mp4 = lambda *_a, **_k: None
    body = _html(tier).encode()
    ctx = browser.new_context()
    page = ctx.new_page()
    page.route("**/*", lambda rt: rt.fulfill(status=200, body=body, headers={"content-type": "text/html"})
               if rt.request.url == URL else rt.abort())
    try:
        page.goto(URL, wait_until="domcontentloaded")
        with mock.patch.object(rx, "db_log", lambda *a, **k: None), \
                mock.patch.object(runner_module, "db_log", lambda *a, **k: None):
            took = r._try_vixen_extractor(URL, page)
    finally:
        ctx.close()
    return took, r


def test_a_480p_stream_is_not_closed_done_below_min_resolution(browser, tmp_path):
    took, r = _run(browser, tmp_path, tier=480)
    assert not took and r.transfers == [], (
        "VIXEN_EXTRACTOR_BYPASSES_MIN_RESOLUTION: the extractor downloaded the 480p stream on a min_resolution=1080 "
        f"site instead of declining to the DOM path: took={took} transfers={r.transfers} states={r.states}")
    assert not any(s == "done" for s, _m in r.states), r.states


def test_control_a_forced_job_still_takes_the_480p_stream(browser, tmp_path):
    """Approve (force_download) lifts the hold, as on every other path."""
    took, r = _run(browser, tmp_path, tier=480, force=True)
    assert took and r.transfers == [SRC.format(t=480)], (took, r.transfers, r.states)


def test_control_a_tier_at_or_above_min_resolution_is_downloaded(browser, tmp_path):
    took, r = _run(browser, tmp_path, tier=2160)
    assert took and r.transfers == [SRC.format(t=2160)], (took, r.transfers, r.states)
