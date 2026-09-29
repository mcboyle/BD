"""dl95-beeg-1-live-1: an unproven winner yields to the stream that names this scene.

LIVE on test2 (harness-work/DOT95-LANE/live-dl95-beeg-1/LIVE-RESULT-B6-B.md,
tplproof-beeg.json): https://beeg.com/-0920833012505915 went "Clicking [6K] -- no
identity proof" -> needs_review "Saw: 6K(?):Gattouz0 / Wife's infidelity lea... |
auto(?):/saved", 0 bytes, while the journal logged this scene's own HLS manifests
(media=hls4A/av1_1080p/920833012...).  Measured on the live page (2026-09-29):
that "Gattouz0" tile links to /-0920833012505915 -- the scene ITSELF, not another
one -- and the winner on other loads is the sidebar's "/saved".  The player is a
blob:, the 250-entry resource-timing buffer is full of thumbnails before the
manifests load, and the feed moves the address bar on to the next scene
(/-0722051844550416), whose streams it prefetches.

Contract: before clicking a winner admitted WITHOUT identity proof, the runner
consults only the streams whose path carries the JOB's scene id -- read from
the manifests its watcher saw -- and takes the tallest; with none, the click
runs as before.

Fixture: verbatim XHTML excerpts of the live DOM (XMLSerializer, so the feed's
nested anchors survive): sidebar, player, the scene's own unit and one other.
Manifest URL shapes are the live ones with the signed key blanked.  Every
request is fulfilled or aborted in-process; nothing leaves the host.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

SCENE = "https://beeg.com/-0920833012505915"
NEXT = "/-0722051844550416"
CDN = "https://video.beeg.com/key=K,end=1790737048,limit=10/data=8d3f36b223/media=hls4A/"
OWN_MASTER = CDN + (
    "multi=426x240:240p:YXZjMS42NDAwMUUsbXA0YS40MC4y,640x360:360p:YXZjMS42NDAwMUYsbXA0YS40MC4y,"
    "854x480:480p:YXZjMS42NDAwMUYsbXA0YS40MC4y,1280x720:720p:YXZjMS42NDAwMjAsbXA0YS40MC4y,"
    "1920x1080:1080p:YXZjMS42NDAwMkEsbXA0YS40MC4y,426x240:av1_240p:YXYwMS4wLjAxTS4wOCxtcDRhLjQwLjI,"
    "640x360:av1_360p:YXYwMS4wLjA0TS4wOCxtcDRhLjQwLjI,854x480:av1_480p:YXYwMS4wLjA1TS4wOCxtcDRhLjQwLjI,"
    "1280x720:av1_720p:YXYwMS4wLjA4TS4wOCxtcDRhLjQwLjI,1920x1080:av1_1080p:YXYwMS4wLjA5TS4wOCxtcDRhLjQwLjI,"
    "426x240:h265_240p:aHZjMS4xLjYuTDYzLjkwLG1wNGEuNDAuMg,640x360:h265_360p:aHZjMS4xLjYuTDkwLjkwLG1wNGEuNDAuMg,"
    "854x480:h265_480p:aHZjMS4xLjYuTDkzLjkwLG1wNGEuNDAuMg,1280x720:h265_720p:aHZjMS4xLjYuSDEyMC45MCxtcDRhLjQwLjI,"
    "1920x1080:h265_1080p:aHZjMS4xLjYuSDEyMy45MCxtcDRhLjQwLjI/_TPL_/920833012505915.mp4.m3u8")
OWN_720 = CDN + "av1_720p/920833012505915.mp4.m3u8"
NEXT_CDN = "https://video.beeg.com/key=K,end=1790737052,limit=10/data=b7df005684/media=hls4A/"
NEXT_MASTER = NEXT_CDN + (
    "multi=426x240:240p:YXZjMS42NDAwMTUsbXA0YS40MC4y,640x360:360p:YXZjMS42NDAwMUUsbXA0YS40MC4y,"
    "854x480:480p:YXZjMS42NDAwMUYsbXA0YS40MC4y,1280x720:720p:YXZjMS42NDAwMjAsbXA0YS40MC4y"
    "/_TPL_/722051844550416.mp4.m3u8")
NEXT_720 = NEXT_CDN + "720p/722051844550416.mp4.m3u8"
# A clip with no identity, requested early enough to sit in the timing buffer:
# the unproven page-media population takes it, the proven one must not.
TEASER = "https://beeg.com/static/teaser_1080p.mp4"

FEED = (Path(__file__).parent / "fixtures" / "dl95_beeg_scene_feed.xhtml").read_text(
    encoding="utf-8")


def _script(streams, early=()):
    fetches = ";".join(f"await fetch({u!r}).catch(() => null)" for u in streams)
    first = "".join(f"await fetch({u!r}).catch(() => null);" for u in early)
    return (
        "<script>(async () => {"
        f"{first}"
        # thumbnails first, as measured: the resource-timing buffer is full
        "await Promise.all([...Array(250).keys()].map("
        "i => fetch('/thumbs/' + i + '.webp').catch(() => null)));"
        f"{fetches};"
        f"history.replaceState(null, '', {NEXT!r});"
        "window.__fed = true; })();</script>")


@contextmanager
def _page(streams, early=()):
    from playwright.sync_api import sync_playwright

    body = FEED.replace("<!--FIXTURE-SCRIPT-->", _script(streams, early))

    def handler(route, request):
        u = request.url
        if u.startswith(SCENE):
            route.fulfill(status=200, content_type="application/xhtml+xml", body=body)
        elif ".m3u8" in u:
            route.fulfill(status=200, content_type="application/vnd.apple.mpegurl",
                          body="#EXTM3U\n#EXT-X-ENDLIST\n")
        elif u.startswith("https://beeg.com/"):
            route.fulfill(status=200, content_type="video/mp4", body=b"\x00" * 64)
        else:
            route.abort()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            context.route("**/*", handler)
            page = context.new_page()
            yield page
        finally:
            browser.close()


class _Stub:
    site_id = "fixturebeeg"

    def __init__(self, tmp_path):
        self.config = {"name": "beeg", "download_dir": str(tmp_path), "min_resolution": 1080}
        self.jobs, self.updates, self.events, self.streams = {}, [], [], []
        self._spa_api_capture = None
        self._stop = type("Stop", (), {"is_set": staticmethod(lambda: False)})()

    def _update_job(self, url, status, message, **extra):
        self.updates.append((status, message))

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))

    def _screenshot(self, page, url):
        return None

    def _do_direct_http_download(self, page_url, file_url, output_path, referer=""):
        self.streams.append(file_url)
        Path(output_path).write_bytes(b"\x00" * 16)
        return True

    def _hls_download_guarded(self, _hls, manifest_url, output_path, **kwargs):
        self.streams.append(manifest_url)
        Path(output_path).write_bytes(b"\x00" * 32)
        return _hls.DownloadResult(ok=True, output_path=output_path, bytes_written=32)

    def _size_on_disk_after_tagging(self, path, fallback):
        return fallback


def _load(tmp_path, monkeypatch):
    """A runner host with the REAL manifest watcher on the page's context."""
    from bulk_downloader import hls_downloader, runner_browser
    from bulk_downloader import runner_extractors as rx

    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    monkeypatch.setattr(hls_downloader, "is_available", lambda: True)
    return type("StubRunner", (_Stub, rx.ExtractorsMixin, runner_browser.BrowserMixin),
                {})(tmp_path)


@contextmanager
def _scene(streams, tmp_path, monkeypatch, early=()):
    runner = _load(tmp_path, monkeypatch)
    with _page(streams, early) as page:
        runner._install_adaptive_manifest_capture(page.context)
        page.goto(SCENE, wait_until="load")
        page.wait_for_function("() => window.__fed === true", timeout=20000)
        page.wait_for_timeout(300)
        yield runner, page


def test_precondition_the_live_shape(tmp_path, monkeypatch):
    """What the job met: an unproven DOM winner, a full timing buffer, a moved address bar."""
    from bulk_downloader import detect, spa_media_extract

    with _scene([OWN_MASTER, OWN_720, NEXT_MASTER], tmp_path, monkeypatch) as (runner, page):
        best = detect.find_best_download(page)
        page_media = page.evaluate(spa_media_extract.PAGE_MEDIA_JS)
        moved = page.url
        detected = [e["url"] for e in runner.manifest_urls]
    assert best and best.get("_no_identity_proof"), best
    assert not [u for u in page_media if ".m3u8" in u], page_media
    assert moved.endswith(NEXT), moved
    assert OWN_MASTER in detected and NEXT_MASTER in detected, detected


def test_the_unproven_arm_takes_this_scenes_own_stream(tmp_path, monkeypatch):
    with _scene([OWN_720, OWN_MASTER, NEXT_MASTER, NEXT_720], tmp_path, monkeypatch) as (
            runner, page):
        took = runner._try_spa_api_media_extractor(SCENE, page, proven_only=True)
    assert took is True and runner.streams == [OWN_MASTER], (
        f"DL95_BEEG_1_LIVE_1_OWN_STREAM_NOT_TAKEN took={took} streams={runner.streams} "
        f"events={runner.events[-3:]}")
    assert runner.updates[-1][0] == "done" and "1080p" in runner.updates[-1][1], runner.updates


def test_the_min_resolution_arm_sees_the_scene_stream_too(tmp_path, monkeypatch):
    """The africancasting-3 refusal arm reads the same proven population."""
    with _scene([OWN_MASTER, NEXT_MASTER], tmp_path, monkeypatch) as (runner, page):
        took = runner._try_spa_api_media_extractor(SCENE, page, min_height=1080)
    assert took is True and runner.streams == [OWN_MASTER], (
        f"DL95_BEEG_1_LIVE_1_OWN_STREAM_NOT_TAKEN streams={runner.streams}")


def test_only_the_next_scenes_streams_leave_the_click_alone(tmp_path, monkeypatch):
    """Negative control: nothing names this scene -> no takeover, no bytes, while
    the unproven population on the same page does take the teaser (positive control)."""
    with _scene([NEXT_MASTER, NEXT_720], tmp_path, monkeypatch, early=[TEASER]) as (
            runner, page):
        took = runner._try_spa_api_media_extractor(SCENE, page, proven_only=True)
        assert took is False and runner.streams == [], (
            f"DL95_BEEG_1_LIVE_1_FOREIGN_STREAM_TAKEN streams={runner.streams}")
        unproven = runner._try_spa_api_media_extractor(SCENE, page)
    assert unproven is True and runner.streams == [TEASER], runner.streams


def test_an_own_stream_below_minimum_is_held_not_downloaded(tmp_path, monkeypatch):
    with _scene([OWN_720, NEXT_MASTER], tmp_path, monkeypatch) as (runner, page):
        took = runner._try_spa_api_media_extractor(SCENE, page, proven_only=True)
    assert took is True and runner.streams == [], runner.streams
    assert runner.updates[-1][0] == "needs_review" and "720p" in runner.updates[-1][1], (
        runner.updates)


@pytest.mark.parametrize("url,expected", [
    (OWN_MASTER, (1080, "920833012505915.mp4")),
    (OWN_720, (720, "920833012505915.mp4")),
    ("https://video.beeg.com/x/1080p/0920833012505915.mp4", (1080, "0920833012505915.mp4")),
    (NEXT_MASTER, None),                                     # another scene
    ("https://video.beeg.com/key=920833012505915/1080p/master.m3u8", None),  # id in a token
    ("https://video.beeg.com/920833012505915/thumb.webp", None),             # not media
], ids=["own-master", "own-rendition", "leading-zero", "next-scene", "id-in-token", "not-media"])
def test_scene_stream_identity_and_height(url, expected):
    from bulk_downloader.spa_media_extract import scene_stream_candidates

    got = scene_stream_candidates(SCENE, [url])
    assert [(c["height"], c["filename"]) for c in got] == ([expected] if expected else [])


def test_a_route_without_one_opaque_id_proves_nothing():
    from bulk_downloader.spa_media_extract import scene_stream_candidates

    slug = "https://site.example/videos/920833012505915/920833012505916"
    assert scene_stream_candidates(slug, [OWN_MASTER]) == []
    assert scene_stream_candidates("https://site.example/video/a-title", [OWN_MASTER]) == []


def test_runner_consults_the_proven_streams_before_the_unproven_click():
    from bulk_downloader import runner as rmod

    src = inspect.getsource(rmod)
    gate = src.index('if min_res>0 and best["score"]>0 and best["score"]<min_res and not forced:')
    arm = src.find("self._try_spa_api_media_extractor(url, page, proven_only=True)", gate)
    trigger = src.rfind('if (best.get("_no_identity_proof")', gate, arm)
    click = src.find('self._update_job(url,"running",f"Clicking [{lbl}]...{note}")', gate)
    assert gate < trigger < arm < click, (gate, trigger, arm, click)


OTHER_PLAYER = "https://cdn.example/get_file/1/key/1/22222222/22222222_2160p.mp4"


def _proven_only_on_a_moved_page(job, tmp_path, monkeypatch, watched=()):
    """The page has moved on to /video/22222222, whose video-js player offers 2160p."""
    from playwright.sync_api import sync_playwright

    runner = _load(tmp_path, monkeypatch)
    runner.manifest_urls = [{"url": u} for u in watched]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(status=200, body="<html></html>"))
            page.goto(job)
            page.evaluate("u => history.replaceState(null, '', u)",
                          "https://fixture.example/video/22222222")
            page.set_content(
                f'<video class="video-js"><source src="{OTHER_PLAYER}" type="video/mp4"></video>')
            took = runner._try_spa_api_media_extractor(job, page, proven_only=True)
        finally:
            browser.close()
    return runner, took


def test_every_proven_population_answers_to_the_job(tmp_path, monkeypatch):
    """cx-2 F1: the proven population is judged against the JOB, so its own stream wins."""
    own = "https://cdn.example/hls/1080p/11111111.mp4.m3u8"
    runner, took = _proven_only_on_a_moved_page(
        "https://fixture.example/video/11111111", tmp_path, monkeypatch, watched=[own])
    assert took and runner.streams == [own], (
        f"DL95_BEEG_1_LIVE_1_NEXT_SCENE_PLAYER_TAKEN streams={runner.streams}")


def test_a_job_that_names_no_scene_proves_nothing(tmp_path, monkeypatch):
    """proven_only with a slug job: the page's player is not the job's by proof -> the click runs."""
    runner, took = _proven_only_on_a_moved_page(
        "https://fixture.example/watch/a-slug", tmp_path, monkeypatch)
    assert took is False and runner.streams == [], (
        f"DL95_BEEG_1_LIVE_1_UNPROVEN_PLAYER_TAKEN streams={runner.streams}")


@pytest.mark.parametrize("job,strict,kept", [
    ("", False, True),                                             # legacy callers: page decides
    ("https://site.example/video/22222222/", False, True),         # the job IS the page's scene
    ("https://site.example/video/11111111", False, False),         # the page moved on
    ("https://site.example/watch/a-slug", False, True),            # job names no id: unchanged
    ("https://site.example/watch/a-slug", True, False),            # ...but proves nothing strict
], ids=["no-job", "same-scene", "moved-on", "slug-job", "slug-job-strict"])
def test_scene_player_identity_is_the_jobs(job, strict, kept):
    from bulk_downloader import spa_media_extract as spa

    page_url = "https://site.example/video/22222222/"
    src = "https://site.example/get_file/1/k/22000/22222222/22222222_1080p.mp4/"
    html = f'<video class="video-js"><source src="{src}" type="video/mp4"></video>'
    assert spa.scene_player_candidates(page_url, html) != [], "precondition: the player is parsed"
    got = spa.scene_player_candidates(page_url, html, job_url=job, strict=strict)
    assert bool(got) is kept, got
