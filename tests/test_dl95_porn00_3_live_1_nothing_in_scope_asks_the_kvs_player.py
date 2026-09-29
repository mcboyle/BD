"""dl95-porn00-3-live-1: a nothing-in-scope DOM still asks the scene's own KVS player.

LIVE on test2 (harness-work/DOT95-LANE/live-dl95-porn00-3/LIVE-RESULT-B6-B.md,
journal-hold-0608.txt, approve-0609.log; build 4f9c4c6f): the porn00 scene's player
controls were filtered and its one remaining candidate, the site link "HD PORN"
(https://www.porn00.org), was refused as foreign, so Row 701's nothing-in-scope
outcome held "Nothing in scope -- ... (refused: foreign)" with no spa-api/KVS line.
bulk_approve then re-ran into the same hold with 0 bytes. The KVS player's own
360p/720p (read at 03:08Z on 8189d74f) was never consulted.

Contract: before the nothing-in-scope hold, the scene's page media is asked
(``_fallback_to_page_media``). An Approved job takes the 720p file; an unapproved
one is held "Best is 720p (below 1080p) -- Approve to force"; a page with no media
of its own keeps the nothing-in-scope hold, worded as before.

Fixture: the live porn00 scene's flashvars page (tests/fixtures/
dl95_porn00_kvs_flashvars_scene.html). Every request is fulfilled or aborted in-process.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

BD_GATE_SCOPE = "module"

SCENE = "https://www.porn00.org/video/adriana-chechik-black-duo-destroy-babe-s-cunt/"
F720 = ("https://www.porn00.org/get_file/3/8ca1aa035847b78be82b87e47b0141c1/42000/42561/"
        "42561_720p.mp4/?v-acctoken=X")
PAGE = (Path(__file__).parent / "fixtures" / "dl95_porn00_kvs_flashvars_scene.html").read_text(
    encoding="utf-8")
NO_MEDIA = "<!doctype html><html><body><a href='https://www.porn00.org/'>HD PORN</a></body></html>"
# The live sentinel: detect._NoInScopeCandidates carries the refused site link.
NOTHING_IN_SCOPE = {
    "_no_in_scope_candidates": True,
    "_excluded_candidates": [{"score": 720, "text": "HD PORN https://www.porn00.org",
                              "reason": "foreign"}],
}


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


def _run(html, tmp_path, monkeypatch, forced):
    from bulk_downloader import runner as rn
    from bulk_downloader import runner_extractors as rx
    from bulk_downloader import runner_transport as rt

    for mod in (rn, rx):
        monkeypatch.setattr(mod, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})

    class Host(rt.TransportMixin, rx.ExtractorsMixin):
        _PAGE_MEDIA_WAIT_S = 0.5
        site_id = "fixtureporn00"
        _handle_nothing_in_scope = rn.SiteRunner._handle_nothing_in_scope

        def __init__(self):
            self.config = {"name": "porn00", "download_dir": str(tmp_path)}
            self.jobs = {SCENE: {"force_download": True}} if forced else {}
            self._lock = threading.Lock()
            self.updates, self.transfers = [], []
            self._spa_api_capture = None

        def _screenshot(self, page, url):
            return None

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

    host = Host()
    with _page(html) as page:
        handled = host._handle_nothing_in_scope(page, SCENE, dict(NOTHING_IN_SCOPE))
    return host, handled


def test_an_approved_nothing_in_scope_job_takes_the_kvs_720_file(tmp_path, monkeypatch):
    host, handled = _run(PAGE, tmp_path, monkeypatch, forced=True)
    assert handled is True and host.transfers == [F720], (
        f"DL95_PORN00_3_LIVE_1_KVS_NOT_ASKED handled={handled} transfers={host.transfers} "
        f"updates={host.updates}")
    assert host.updates[-1][0] == "done", host.updates


def test_an_unapproved_nothing_in_scope_job_is_held_with_the_floor_named(tmp_path, monkeypatch):
    host, handled = _run(PAGE, tmp_path, monkeypatch, forced=False)
    assert handled is True and host.transfers == [], (handled, host.transfers)
    assert host.updates[-1][0] == "needs_review" and host.updates[-1][1].startswith(
        "Best is 720p (below 1080p) — Approve to force"), (
        f"DL95_PORN00_3_LIVE_1_HOLD_MISSTATED: {host.updates}")


def test_a_page_with_no_media_of_its_own_keeps_the_nothing_in_scope_hold(tmp_path, monkeypatch):
    """Negative control: the new ask adds nothing when the scene has no media."""
    host, handled = _run(NO_MEDIA, tmp_path, monkeypatch, forced=True)
    assert handled is True and host.transfers == [], (handled, host.transfers)
    assert host.updates == [(
        "needs_review",
        "Nothing in scope — no candidate could be attributed to this scene (refused: foreign)."
        " Refused: 720p:HD PORN https://www.porn00.org[foreign]")], host.updates


# Lens cx-worker-1 R1: the page-media sweep also carries OTHER scenes' media (a related
# rail's <video>). A page refused as nothing-in-scope must not be rescued by it.
FOREIGN = "https://cdn.fixture.test/unrelated-scene-1080p.mp4"
RELATED_RAIL = f'<aside class="related"><video src="{FOREIGN}" preload="none"></video></aside>'


def test_a_related_scenes_video_is_never_the_rescue(tmp_path, monkeypatch):
    for forced in (False, True):
        host, handled = _run(f"<!doctype html><html><body>{RELATED_RAIL}</body></html>",
                             tmp_path, monkeypatch, forced=forced)
        assert handled is True and host.transfers == [], (
            f"DL95_PORN00_3_LIVE_1_FOREIGN_MEDIA_TAKEN forced={forced} {host.transfers}")
        assert host.updates[-1][1].startswith("Nothing in scope"), host.updates


def test_the_kvs_player_beats_a_taller_related_video(tmp_path, monkeypatch):
    """The scene's own 720p wins over a related scene's 1080p on the same page."""
    html = PAGE.replace("</body>", RELATED_RAIL + "</body>")
    assert html != PAGE
    host, handled = _run(html, tmp_path, monkeypatch, forced=True)
    assert handled is True and host.transfers == [F720], (
        f"DL95_PORN00_3_LIVE_1_FOREIGN_MEDIA_TAKEN {host.transfers}")


def test_a_real_find_is_not_touched(tmp_path, monkeypatch):
    """Control: a normal winner is not a nothing-in-scope outcome; nothing is asked."""
    from bulk_downloader import runner as rn

    class Probe:
        def _fallback_to_page_media(self, *a):
            raise AssertionError("page media asked for a real find")

    assert rn.SiteRunner._handle_nothing_in_scope(Probe(), None, SCENE, {"score": 720}) is False
    assert rn.SiteRunner._handle_nothing_in_scope(Probe(), None, SCENE, None) is False
