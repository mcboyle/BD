"""dl95-porn00-3: Approve lifts the KVS flashvars floor; a below-floor hold says so.

LIVE on test2 (harness-work/DOT95-LANE/live-dl95-porn00-1-live-1/LIVE-RESULT-B6-B.md,
approve-0310.log, journal-approve-0310.txt; build 40b943f5): the porn00 scene's KVS
player declares 360p and 720p; under the default 1080 minimum the job held
"Clicked but no download started -- looks like a modal-trigger button -- set Trigger
Selector".  bulk_approve answered "Approved -- will force-download below threshold",
the rerun logged "spa-api: KVS flashvars option(s) below 1080p not offered: 360p,
720p" again and held again with 0 bytes: the KVS arm bounded itself by
min_resolution whatever the job's force flag said, so no approval could lift it,
and the hold blamed a modal trigger.

Contract: an Approved job takes the best KVS option whatever its height; an
unapproved one is held with the button path's words, "Best is 720p (below 1080p)
-- Approve to force".  An option of unknown height is still never offered unforced.

Fixture: the live porn00 scene's own flashvars <script> (tests/fixtures/
dl95_porn00_kvs_flashvars_scene.html; per-visitor values blanked).  Every request
is fulfilled or aborted in-process.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

SCENE = "https://www.porn00.org/video/adriana-chechik-black-duo-destroy-babe-s-cunt/"
FILES = "https://www.porn00.org/get_file/3/"
F360 = FILES + "49a1c31bce7b54c8cbbb119197fcdffc/42000/42561/42561.mp4/?v-acctoken=X"
F720 = FILES + "8ca1aa035847b78be82b87e47b0141c1/42000/42561/42561_720p.mp4/?v-acctoken=X"
PAGE = (Path(__file__).parent / "fixtures" / "dl95_porn00_kvs_flashvars_scene.html").read_text(
    encoding="utf-8")
# The same page with no height anywhere (labels blank, the alt file renamed):
# two options of unknown height.
UNLABELLED = (PAGE.replace("video_url_text: '360p'", "video_url_text: ''")
              .replace("video_alt_url_text: '720p'", "video_alt_url_text: ''")
              .replace("42561_720p.mp4", "42561_alt.mp4"))


@contextmanager
def _page(html):
    from playwright.sync_api import sync_playwright

    def handler(route, request):
        if request.url == SCENE:
            route.fulfill(status=200, content_type="text/html", body=html)
        else:
            route.abort()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", handler)
            page.goto(SCENE, wait_until="load")
            yield page
        finally:
            browser.close()


def _host(tmp_path, monkeypatch, forced):
    from bulk_downloader import runner_extractors as rx
    from bulk_downloader import runner_transport as rt

    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})

    class Host(rt.TransportMixin, rx.ExtractorsMixin):
        _PAGE_MEDIA_WAIT_S = 0.5
        site_id = "fixtureporn00"

        def __init__(self):
            self.config = {"name": "porn00", "download_dir": str(tmp_path)}
            self.jobs = {SCENE: {"force_download": True}} if forced else {}
            self._lock = threading.Lock()
            self.updates, self.transfers = [], []
            self._spa_api_capture = None

        def _update_job(self, url, status, message="", **extra):
            self.updates.append((status, message))

        def log_event(self, *a, **k):
            pass

        def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
            self.transfers.append(file_url)
            Path(output_path).write_bytes(b"\x00" * 16)
            return True

        def _size_on_disk_after_tagging(self, path, fallback):
            return fallback

    return Host()


def _after_dud_click(html, tmp_path, monkeypatch, forced):
    """The live path: a clicked candidate fired nothing -> the page's own media."""
    host = _host(tmp_path, monkeypatch, forced)
    with _page(html) as page:
        took = host._fallback_to_page_media(page, SCENE, "clicked candidate fired no download")
    return host, took


def test_an_approved_job_takes_the_kvs_players_720_file(tmp_path, monkeypatch):
    host, took = _after_dud_click(PAGE, tmp_path, monkeypatch, forced=True)
    assert took is True and host.transfers == [F720], (
        f"DL95_PORN00_3_APPROVE_NOT_HONOURED took={took} transfers={host.transfers} "
        f"updates={host.updates}")
    assert host.updates[-1][0] == "done", host.updates


def test_an_unapproved_job_is_held_with_the_floor_named(tmp_path, monkeypatch):
    host, took = _after_dud_click(PAGE, tmp_path, monkeypatch, forced=False)
    assert host.transfers == [], host.transfers
    assert took is True and host.updates and host.updates[-1][0] == "needs_review", (
        f"DL95_PORN00_3_HOLD_UNNAMED took={took} updates={host.updates}")
    assert host.updates[-1][1].startswith(
        "Best is 720p (below 1080p) — Approve to force. Saw: 720p:720p | 360p:360p"), host.updates


def test_the_no_candidate_arm_holds_with_the_same_words(tmp_path, monkeypatch):
    """Row 722's no-DOM-candidate call (no min_height): the KVS options reach its hold."""
    host = _host(tmp_path, monkeypatch, forced=False)
    with _page(PAGE) as page:
        took = host._try_spa_api_media_extractor(SCENE, page)
    assert took is True and host.transfers == [], (took, host.transfers)
    assert host.updates[-1][1].startswith("Best is 720p (below 1080p) — Approve to force"), (
        host.updates)


def test_the_refusal_arm_still_answers_a_miss(tmp_path, monkeypatch):
    """africancasting-3's arm asks with min_height and no hold: it keeps its own wording."""
    host = _host(tmp_path, monkeypatch, forced=False)
    with _page(PAGE) as page:
        took = host._try_spa_api_media_extractor(SCENE, page, min_height=1080)
    assert took is False and host.transfers == [] and host.updates == [], host.updates


@pytest.mark.parametrize("forced,expected", [(False, []), (True, [F360])],
                         ids=["unapproved", "approved"])
def test_an_unknown_height_is_offered_only_when_approved(tmp_path, monkeypatch, forced, expected):
    host, took = _after_dud_click(UNLABELLED, tmp_path, monkeypatch, forced=forced)
    assert host.transfers == expected, (host.transfers, host.updates)
    assert took is bool(expected), (took, host.updates)


def test_the_no_candidate_arm_never_takes_an_unknown_height_unapproved(tmp_path, monkeypatch):
    """With no min_height to filter it, an unknown-height KVS option would pass the
    known-height hold and download unmeasured; the KVS arm still withholds it."""
    host = _host(tmp_path, monkeypatch, forced=False)
    with _page(UNLABELLED) as page:
        took = host._try_spa_api_media_extractor(SCENE, page)
    assert took is False and host.transfers == [], (took, host.transfers, host.updates)
