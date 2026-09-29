"""dl95-reptyle-5 (HIGH): per-job Cancel did not abort the reptyle (TeamSkeet ant-modal tier click) download.

Live, v3.66.1712 (harness-work/UIUX-20260928/download-95/B7-B/p1/reptyle/tplv2/app-log-cancel-then-done.txt and
tplv2b/CANCEL-GHOST.md): the learned hit ``.ant-modal.download-modal ... button.modal-download-button`` has no href,
so the click fires a browser Download whose URL the app then fetches over HTTP (use_http_dl). POST
/api/queue/v2/cancel answered 200 -> "stopped: Cancelled by user"; the next progress tick flipped the job back to
"running: 12% 922.0 MB/7.3 GB"; a second Cancel answered {"ok":true,"previous_status":"running"}; the job left the
queue's running/waiting lists while the .part kept growing (870 -> 1,251 MB in 20 s, the "ghost"), and it ended
"done: Saved: freaky-fembots_skyla_sun_full_2160.mp4" (7,850,830,272 B).

Same class as dl95-porndig-3 (HTTP loop read only the SITE stop; progress wrote "running" over "stopped"). This file
pins the reptyle shape end to end through the real ``_do_download`` click path, the real HTTP transfer and the real
cancel/queue endpoints: after Cancel the bytes stop, the .part stops growing, the job stays stopped (a second Cancel
is refused, not accepted from "running"), it is listed as terminal "stopped", and nothing saves it "done".
The browser save_as leg (Download.cancel) is folded into dl95-porndig-3b (gen3 / dp13).
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

SID = "dl95rp5"
JOB = "https://app.reptyle.com/movies/31964?tab=scenes"
LEAF = "freaky-fembots_skyla_sun_full_2160.mp4"
MODAL_SEL = ('.ant-modal.download-modal .ant-space-horizontal:has(div:text-is("2160p")) '
             'button.modal-download-button')
PIECE = 256 * 1024
BIG = 96 * 1024 * 1024


class _SlowCdn(BaseHTTPRequestHandler):
    """The signed CDN URL the tier click's Download names: ~16 MB/s, Range aware; counts bytes put on the wire."""

    protocol_version = "HTTP/1.1"
    total: ClassVar[int] = BIG
    sent: ClassVar[list] = [0]

    def log_message(self, *_a):
        return

    def _send_head(self, status, a, b):
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {a}-{b}/{self.total}")
        self.send_header("Content-Length", str(b - a + 1))
        self.end_headers()

    def do_HEAD(self):
        self._send_head(200, 0, self.total - 1)

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng:
            lo, _, hi = rng.split("=", 1)[1].partition("-")
            status, a, b = 206, int(lo), (int(hi) if hi else self.total - 1)
        else:
            status, a, b = 200, 0, self.total - 1
        self._send_head(status, a, b)
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


@contextmanager
def _cdn(total):
    handler = type("_Cdn", (_SlowCdn,), {"sent": [0], "total": total})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/signed/{LEAF}?token=t", handler.sent
    finally:
        srv.shutdown()
        srv.server_close()


class _Download:
    """What page.expect_download hands back after the modal's tier button is clicked."""

    def __init__(self, url):
        self.url = url
        self.suggested_filename = LEAF
        self.cancels = 0

    def cancel(self):
        self.cancels += 1


class _ModalButton:
    """button.modal-download-button: no href, the click alone starts the download."""

    def __init__(self):
        self.clicks = 0

    def evaluate(self, _js):
        return [["type", "button"], ["class", "ant-btn ant-btn-primary modal-download-button"]]

    def get_attribute(self, _name):
        return None

    def click(self):
        self.clicks += 1


class _Page:
    url = JOB

    def __init__(self, download):
        self.download = download

    def evaluate(self, _js):
        return None

    @contextmanager
    def expect_download(self, *, timeout):
        yield type("_Info", (), {"value": self.download})()

    def title(self):
        return "My Gamer Girl Fembot"


class _Ctx:
    def cookies(self, _urls=None):
        return []


@pytest.fixture
def rig(clean_workdir, monkeypatch):
    from flask import Flask

    from bulk_downloader import app_state
    from bulk_downloader.app_queue import queue_bp
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    db_init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    r = SiteRunner(SID, {"name": SID, "use_curl_cffi": False, "use_http_dl": True, "parallel_chunks": 1,
                         "min_resolution": 0, "verify_integrity": False, "verify_hash": False,
                         "download_dir": str(clean_workdir / "dl")})
    r._update_job(JOB, "pending", "Queued")
    r._update_job(JOB, "running", "Claimed by worker")
    monkeypatch.setattr(app_state, "runners", {SID: r})
    monkeypatch.setattr(app_state, "s_cfg", {SID: {"name": SID}})
    app = Flask(__name__)
    app.register_blueprint(queue_bp)
    return r, app.test_client(), clean_workdir / "dl"


def _click_and_download(r, dl_dir, file_url):
    """The real _do_download on the modal tier winner, in a worker thread (as the runner's worker runs it)."""
    button, download = _ModalButton(), _Download(file_url)
    best = {"locator": button, "score": 2160, "size": 0, "text": "2160p", "_all_candidates": [],
            "_learned_sel": MODAL_SEL, "_via_learned": True}
    out = {"button": button, "download": download}

    def run():
        try:
            r._do_download(_Page(download), _Ctx(), JOB, best, Path(dl_dir), "2160p")
        except Exception as exc:  # noqa: BLE001 -- the outcome under test
            out["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def _history_statuses():
    from bulk_downloader.db import db_conn

    with db_conn() as cx:
        return [row[0] for row in cx.execute("SELECT status FROM history WHERE url=?", (JOB,)).fetchall()]


def _part_sizes(dl_dir):
    return {p.name: p.stat().st_size for p in Path(dl_dir).glob("*.part")}


def _queue_buckets(client):
    body = client.get("/api/queue/v2").get_json()
    return {k: [j.get("status", k) for j in body.get(k, []) if j.get("url") == JOB]
            for k in ("running", "waiting", "terminal")}


def test_cancel_stops_the_modal_tier_download_and_it_is_never_saved(rig):
    r, client, dl_dir = rig
    with _cdn(BIG) as (file_url, sent):
        t, out = _click_and_download(r, dl_dir, file_url)
        deadline = time.monotonic() + 20
        while sent[0] < 4 * 1024 * 1024 and t.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert t.is_alive(), f"precondition: the transfer outlives the cancel point, got {out}"
        assert out["button"].clicks == 1, "precondition: the ant-modal tier button was clicked (no href route)"
        first = client.post("/api/queue/v2/cancel", json={"site_id": SID, "url": JOB})
        assert first.status_code == 200 and first.get_json()["ok"] is True, first.get_json()
        at_cancel = sent[0]
        t.join(timeout=30)
        assert not t.is_alive(), "DL95_REPTYLE_CANCEL_GHOST: the modal-tier transfer never returned after Cancel"
        time.sleep(0.5)
        served = sent[0]
        before = _part_sizes(dl_dir)
        time.sleep(0.5)
        after = _part_sizes(dl_dir)
        assert all(after.get(k, 0) <= v for k, v in before.items()), (
            f"DL95_REPTYLE_CANCEL_GHOST: .part still growing after Cancel {before} -> {after}")
    # Loopback buffers let the origin run a few MB ahead of the reader: "far short of the file", not zero.
    assert served < BIG // 2, (
        f"DL95_REPTYLE_CANCEL_GHOST: served {served}/{BIG} after Cancel at {at_cancel}; job={r.jobs.get(JOB)}")
    job = r.jobs[JOB]
    assert (job["status"], job["message"]) == ("stopped", "Cancelled by user"), f"DL95_REPTYLE_CANCEL_REVIVED: {job}"
    assert not (dl_dir / LEAF).exists() and "done" not in _history_statuses(), (
        f"DL95_REPTYLE_CANCELLED_JOB_SAVED: {job} history={_history_statuses()}")
    second = client.post("/api/queue/v2/cancel", json={"site_id": SID, "url": JOB})
    assert second.status_code == 400 and "stopped" in second.get_json().get("error", ""), (
        f"DL95_REPTYLE_CANCEL_REVIVED: the 2nd Cancel found it live: {second.get_json()}")
    buckets = _queue_buckets(client)
    assert buckets == {"running": [], "waiting": [], "terminal": ["stopped"]}, (
        f"DL95_REPTYLE_CANCEL_GHOST: queue view {buckets}")
    assert "error" not in out, f"DL95_REPTYLE_CANCEL_RAISED: {out['error']!r}"


def test_without_cancel_the_same_modal_tier_download_is_saved(rig):
    """Control: the rig can say yes -- the same click + HTTP transfer, left alone, lands and is saved done."""
    r, client, dl_dir = rig
    total = 6 * 1024 * 1024
    with _cdn(total) as (file_url, sent):
        t, out = _click_and_download(r, dl_dir, file_url)
        t.join(timeout=30)
    assert not t.is_alive() and "error" not in out, out
    assert out["download"].cancels >= 1, "precondition: the browser's own Download is dropped for the HTTP fetch"
    assert (dl_dir / LEAF).stat().st_size == total and sent[0] >= total, sorted(p.name for p in dl_dir.iterdir())
    assert r.jobs[JOB]["status"] == "done", r.jobs[JOB]
    assert _queue_buckets(client)["terminal"] == ["done"]
