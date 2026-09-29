"""dl95-porndig-3 (HIGH; download-95/B6-B/tpl/FINDING-O1517-porndig-justporn-B6-B.md#porndig).

Measured on test2 4953203f, porndig 43c18b89 /videos/242541/: POST /api/queue/v2/cancel answered 200 ok twice
(03:00:38Z, 03:01:03Z) and the job read "stopped: Cancelled by user" -- then progress ticks flipped it back to
"running: 37%" and the .part kept growing (160 -> 356 MB). The 03:01:31Z restart found the row "running", re-ran
it, and it finished "done" (449 MB).

Cancel only marks the JOB "stopped" (app_queue: runner._update_job(url, "stopped", "Cancelled by user")). The HTTP
transfer only looks at the SITE stop event, the progress ticks write "running" over "stopped" (memory and the queue
row), and a stopped transfer's error sends _do_download into its browser re-click fallback.

GEN 2 (B6-B UPDATE 03:40Z): Cancel was ignored on 4 paths / 2 hosts -- porndig HTTP, cumlouder learned-src (.95),
cumlouder Direct (.183), beeg HLS (.95). Every byte loop and every cancel_check read only the SITE stop.

PM 04:2xZ (bd-review-scratch/fk1-cx2/BLOCKED.md): the BROWSER download path is this row's too -- save_as blocked until the
browser held the whole file; Stop/Cancel must Download.cancel() the bytes in flight.

Contract after the fix: once a job is stopped, every transfer path moving its bytes (single-stream, parallel,
_do_direct_http_download, the HLS cancel_check, the browser download) stops, and the job and its queue row stay "stopped". Nothing re-marks it "running", so a restart does not re-run it,
and a stopped transfer is not retried through the browser. Re-arming through "pending" still works (control).
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

BD_GATE_SCOPE = "module"

JOB = "https://www.porndig.com/videos/242541/scene"
TOTAL = 96 * 1024 * 1024
PIECE = 256 * 1024


class _SlowOrigin(BaseHTTPRequestHandler):
    """Streams TOTAL bytes at ~16 MB/s; honours HEAD and Range (the parallel path's probe and chunks)."""

    protocol_version = "HTTP/1.1"
    sent: ClassVar[list] = [0]   # bytes put on the wire (a preallocated .part says nothing)

    def log_message(self, *_a):
        return

    def _span(self):
        rng = self.headers.get("Range")
        if not rng:
            return 200, 0, TOTAL - 1
        a, _, b = rng.split("=", 1)[1].partition("-")
        return 206, int(a), (int(b) if b else TOTAL - 1)

    def _head(self, status, a, b):
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Disposition", 'attachment; filename="scene.mp4"')
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {a}-{b}/{TOTAL}")
        self.send_header("Content-Length", str(b - a + 1))
        self.end_headers()

    def do_HEAD(self):
        self._head(200, 0, TOTAL - 1)

    def do_GET(self):
        status, a, b = self._span()
        self._head(status, a, b)
        left = b - a + 1
        try:
            while left > 0:
                n = min(PIECE, left)
                self.wfile.write(b"\0" * n)
                self.sent[0] += n
                left -= n
                time.sleep(n / (16 * 1024 * 1024))
        except (BrokenPipeError, ConnectionResetError):
            return


@pytest.fixture
def origin():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), type("_Origin", (_SlowOrigin,), {"sent": [0]}))
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/scene.mp4", srv.RequestHandlerClass.sent
    srv.shutdown()
    srv.server_close()


class _Ctx:
    def cookies(self, _urls=None):
        return []


@pytest.fixture
def runner(clean_workdir):
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    db_init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)

    def make(**config):
        r = SiteRunner("dl95pd3", {"name": "dl95pd3", "use_curl_cffi": False,
                                   "download_dir": str(clean_workdir / "dl"), **config})
        r._update_job(JOB, "pending", "Queued")
        r._update_job(JOB, "running", "Claimed by worker")
        return r
    return make


def _row_status(url=JOB):
    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        row = cx.execute("SELECT status FROM queue WHERE site_id=? AND url=?", ("dl95pd3", url)).fetchone()
    return row[0] if row else None


def _transfer(r, origin, dest, cancel_at=None):
    """Run the real HTTP transfer; Cancel exactly as app_queue does once `cancel_at` bytes were served."""
    file_url, sent = origin
    out = {}

    def run():
        try:
            out["result"] = r._http_download(JOB, None, _Ctx(), file_url, dest)
        except Exception as exc:  # noqa: BLE001 -- the outcome under test
            out["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    if cancel_at is not None:
        deadline = time.monotonic() + 20
        while sent[0] < cancel_at and t.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert t.is_alive(), f"precondition: the transfer outlives the cancel point, got {out}"
        r._update_job(JOB, "stopped", "Cancelled by user")      # app_queue api_queue_v2_cancel
        out["at_cancel"] = sent[0]
    t.join(timeout=30)
    assert not t.is_alive(), "DL95_CANCEL_TRANSFER_KEPT_RUNNING: the transfer never returned"
    return out


@pytest.mark.parametrize("config", [{"parallel_chunks": 1},
                                    {"parallel_chunks": 2, "parallel_min_size_mb": 1}],
                         ids=["single-stream", "parallel"])
def test_cancel_stops_the_transfer_and_the_job_stays_stopped(runner, origin, clean_workdir, config, monkeypatch):
    r = runner(**config)
    parallel_runs = []
    real_parallel = type(r)._http_download_parallel
    monkeypatch.setattr(type(r), "_http_download_parallel",
                        lambda self, *a, **k: (parallel_runs.append(1), real_parallel(self, *a, **k))[1])
    dest = clean_workdir / "dl" / "scene.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = _transfer(r, origin, dest, cancel_at=4 * 1024 * 1024)
    assert bool(parallel_runs) == (config["parallel_chunks"] > 1), f"precondition: transfer path {parallel_runs}"
    time.sleep(0.5)
    served = origin[1][0]
    assert "stopped" in str(out.get("error", "")).lower() and not dest.exists(), (
        f"DL95_CANCEL_TRANSFER_KEPT_RUNNING: {out} served={served}/{TOTAL}")
    # Loopback socket buffers let the origin run a few MB ahead of the reader, so the bound is
    # "stopped far short of the file", not "zero bytes after Cancel". Unfixed, it serves all of it.
    assert served < TOTAL // 2, f"DL95_CANCEL_TRANSFER_KEPT_RUNNING: served {served}/{TOTAL} after Cancel at {out}"
    job = r.jobs[JOB]
    assert (job["status"], job["message"]) == ("stopped", "Cancelled by user"), f"DL95_CANCEL_REVIVED: {job}"
    assert _row_status() == "stopped", f"DL95_CANCEL_REVIVED: queue row {_row_status()!r} (a restart re-runs it)"


def test_without_cancel_the_same_transfer_completes(runner, origin, clean_workdir):
    """Control: the harness can say yes -- the gate does not stop a live job."""
    r = runner(parallel_chunks=1)
    dest = clean_workdir / "dl" / "scene.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = _transfer(r, origin, dest)
    assert "error" not in out and dest.stat().st_size == TOTAL, out
    assert r.jobs[JOB]["status"] == "running" and _row_status() == "running"


@pytest.mark.parametrize("late", [("running", "Downloading 37%"), ("failed", "HTTP failed"),
                                  ("needs_review", "Best is 480p"), ("done", "Saved")])
def test_a_cancelled_runs_trailing_writes_never_replace_stopped(runner, late):
    r = runner()
    r._update_job(JOB, "stopped", "Cancelled by user")
    assert r._update_job(JOB, *late, file_size=1) is False
    assert (r.jobs[JOB]["status"], r.jobs[JOB]["message"]) == ("stopped", "Cancelled by user")
    assert _row_status() == "stopped", f"DL95_CANCEL_REVIVED: {late}"


def test_running_never_overwrites_stopped_but_pending_rearms(runner):
    r = runner()
    r._update_job(JOB, "stopped", "Cancelled by user")
    assert r._update_job(JOB, "running", "Downloading 37%", file_size=1) is False
    assert r.jobs[JOB]["status"] == "stopped" and _row_status() == "stopped"
    r._update_job(JOB, "pending", "Re-added")                   # control: the re-arm transition
    assert r._update_job(JOB, "running", "Claimed by worker") is not False
    assert r.jobs[JOB]["status"] == "running" and _row_status() == "running"


def test_the_queue_row_keeps_stopped_against_a_direct_running_upsert(runner):
    from bulk_downloader.db import queue_upsert

    runner()._update_job(JOB, "stopped", "Cancelled by user")
    queue_upsert("dl95pd3", JOB, status="running", message="Downloading 40%", file_size=2)
    assert _row_status() == "stopped", "DL95_CANCEL_REVIVED: the progress tick's own upsert"
    fresh = JOB + "?new"
    queue_upsert("dl95pd3", fresh, status="running", message="x")   # control: a new row still inserts
    assert _row_status(fresh) == "running"


def test_cancel_stops_the_direct_http_path(runner, origin, clean_workdir):
    """cumlouder Direct / learned-src: _do_direct_http_download (spa-api, library and direct-media routes)."""
    r = runner()
    dest = clean_workdir / "dl" / "direct.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    file_url, sent = origin
    out = {}
    t = threading.Thread(target=lambda: out.update(
        ok=r._do_direct_http_download(JOB, file_url, str(dest), referer=JOB)), daemon=True)
    t.start()
    deadline = time.monotonic() + 20
    while sent[0] < 4 * 1024 * 1024 and t.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert t.is_alive(), f"precondition: the transfer outlives the cancel point, got {out}"
    r._update_job(JOB, "stopped", "Cancelled by user")
    t.join(timeout=30)
    time.sleep(0.5)
    assert out.get("ok") is False and sent[0] < TOTAL // 2, (
        f"DL95_CANCEL_TRANSFER_KEPT_RUNNING: direct_http {out} served {sent[0]}/{TOTAL}")
    assert r.jobs[JOB]["status"] == "stopped"


def test_a_cancelled_multi_conn_leg_opens_no_second_leg(runner, origin, clean_workdir, monkeypatch):
    """A7-A residual: multi_conn returns False because the job was cancelled -- that is not "not viable"."""
    from bulk_downloader import runner_transport as transport

    r = runner(use_multi_conn=True)
    file_url, sent = origin

    def cancelled_leg(self, page_url, *_a, **_k):
        self._update_job(page_url, "stopped", "Cancelled by user")
        return False

    monkeypatch.setattr(transport, "_MULTI_CONN_AVAILABLE", True)
    monkeypatch.setattr(transport, "_mconn", object())
    monkeypatch.setattr(type(r), "_try_multi_conn_download", cancelled_leg)
    dest = clean_workdir / "dl" / "mc.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    assert r._do_direct_http_download(JOB, file_url, str(dest), referer=JOB) is False
    time.sleep(0.3)
    assert sent[0] == 0, f"DL95_CANCEL_SECOND_LEG_OPENED: single-stream fallback served {sent[0]} bytes"


def test_the_hls_cancel_check_sees_the_jobs_cancel(runner, clean_workdir, monkeypatch):
    """beeg HLS: the spa-api arm hands ffmpeg a cancel_check; it must turn True on this job's Cancel."""
    from bulk_downloader import hls_downloader

    r = runner(min_resolution=0)
    seen = []

    def fake_hls(_hls, _url, output_path, **kw):
        check = kw["cancel_check"]
        seen.append(check())                                    # live job: keep going
        r._update_job(JOB, "stopped", "Cancelled by user")      # app_queue api_queue_v2_cancel
        seen.append(check())                                    # must stop now
        return type("_R", (), {"ok": False, "error": "cancelled", "bytes_written": 0})()

    class _Page:
        url = JOB

        def evaluate(self, _js):
            return ["https://cdn.beeg.invalid/scene/1080/index.m3u8"]

    monkeypatch.setattr(hls_downloader, "is_available", lambda: True)
    monkeypatch.setattr(type(r), "_hls_download_guarded", lambda self, *a, **k: fake_hls(*a, **k))
    r._try_spa_api_media_extractor(JOB, _Page())
    assert seen == [False, True], f"DL95_HLS_CANCEL_IGNORED: cancel_check before/after Cancel = {seen}"


@contextmanager
def _browser_download(file_url):
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            page = br.new_context(accept_downloads=True).new_page()
            page.set_content(f'<a id="dl" href="{file_url}">Download 1080p</a>')
            with page.expect_download(timeout=15000) as info:
                page.click("#dl")
            yield info.value
        finally:
            br.close()


def test_cancel_stops_the_browser_download_in_flight(runner, origin, clean_workdir):
    """The browser path: a Cancel mid-transfer cancels the Chromium download; the origin stops serving."""
    r = runner()
    file_url, sent = origin
    with _browser_download(file_url) as dl:
        def cancel_when_moving():
            deadline = time.monotonic() + 20
            while sent[0] < 4 * 1024 * 1024 and time.monotonic() < deadline:
                time.sleep(0.02)
            r._update_job(JOB, "stopped", "Cancelled by user")          # app_queue api_queue_v2_cancel
        t = threading.Thread(target=cancel_when_moving, daemon=True)
        t.start()
        stopped = r._browser_download_stopped(dl, JOB)
        t.join(timeout=30)
        failure = dl.failure()
    time.sleep(0.5)
    assert stopped is True and failure, f"DL95_BROWSER_DOWNLOAD_NOT_CANCELLED: stopped={stopped} failure={failure!r}"
    assert sent[0] < TOTAL // 2, f"DL95_CANCEL_TRANSFER_KEPT_RUNNING: browser served {sent[0]}/{TOTAL}"
    assert r.jobs[JOB]["status"] == "stopped"


def test_without_cancel_the_browser_download_completes(runner, origin, clean_workdir):
    """Control: the watcher waits for a live job's download and _pw_save then lands the whole file."""
    r = runner()
    dest = clean_workdir / "dl" / "browser.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _browser_download(origin[0]) as dl:
        assert r._browser_download_stopped(dl, JOB) is False
        size, _ = r._pw_save(dl, dest)
    assert size == TOTAL


# -- _do_download: a stopped HTTP leg is not retried through the browser ---------------------------------------

class _Download:
    url = "https://cdn.porndig.invalid/242541_1080.mp4"
    suggested_filename = "242541_1080.mp4"


class _Page:
    url = JOB

    def __init__(self):
        self.download_waits = 0

    def evaluate(self, _script):
        return None

    @contextmanager
    def expect_download(self, *, timeout):
        self.download_waits += 1
        holder = type("_Info", (), {"value": _Download()})()
        yield holder

    def title(self):
        return "242541"


class _Locator:
    clicks = 0

    def get_attribute(self, _name):
        return _Download.url

    def click(self):
        type(self).clicks += 1


@pytest.mark.parametrize("use_http_dl", [True, False], ids=["http-leg", "browser-arm"])
def test_a_stopped_http_leg_is_not_retried_through_the_browser(tmp_path, monkeypatch, use_http_dl):
    """The winner's href is the media file, so the HTTP leg runs with no click (porndig's sized-href shape)."""
    from bulk_downloader import runner_transport as transport

    class _R(transport.TransportMixin):
        def __init__(self):
            self.site_id = "dl95pd3"
            self.config = {"name": "dl95pd3", "use_http_dl": use_http_dl, "verify_hash": False,
                           "verify_integrity": False}
            self._lock = threading.RLock()
            self._stop = threading.Event()
            self.jobs = {JOB: {"status": "stopped", "message": "Cancelled by user"}}
            self.status, self.failures, self.saved = [], [], []

        def _http_download(self, *_a, **_k):
            raise transport._HTTPDownloadFailed("stopped")

        def _pw_save(self, _dl, _path):
            self.saved.append(_path)
            return (1, 1)

        def _probe_for_higher_tier(self, url, **_k):
            return url

        def _build_mirror_urls(self, _url):
            return []

        def _screenshot(self, *_a, **_k):
            return ""

        def _update_job(self, _url, state, message="", **_k):
            self.status.append((state, message))

        def _handle_failure(self, url, message, **_k):
            self.failures.append(message)

        def _size_on_disk_after_tagging(self, _path, size):
            return size

    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *_a, **_k: None)
    monkeypatch.setattr(transport.staging_claim, "reserve", lambda path, _i: (path, path.with_suffix(".part")))
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)
    _Locator.clicks = 0
    r, page = _R(), _Page()
    r._do_download(page, object(), JOB, {"locator": _Locator(), "score": 1080, "size": 0, "text": "1080p",
                                         "_all_candidates": []}, Path(tmp_path), "1080p")
    retried = [m for s, m in r.status if "retrying via browser" in m]
    assert not retried and not r.saved and _Locator.clicks == 0, (
        f"DL95_STOPPED_TRANSFER_RETRIED_BY_BROWSER: status={r.status} clicks={_Locator.clicks} saved={r.saved}")
    assert not r.failures, f"DL95_STOPPED_TRANSFER_FAILED: {r.failures}"
