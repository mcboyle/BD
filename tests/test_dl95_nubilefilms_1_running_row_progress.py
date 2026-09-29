"""dl95-nubilefilms-1: Queue/Home NOW RUNNING rows read "0% | — · ETA —" while bytes flow.

test2 00:17Z (harness-work/UIUX-20260928/download-95/A5-A/FINDING-progress-ui.md, QP__progress-3.png) and
re-measured by A8-A: /api/queue/v2 served progress 0 / bytes_done 0 for a cumlouder job whose message
read "Direct 62% • 33.0 MB". The v2 builder copied job keys (progress, bytes_done, bytes_total,
eta_seconds, rate_human) that no producer ever writes; the transfer ticks record `file_size` and the
runner's per-job byte-rate sample, and knew the total only in the message text.

Fix: every progress tick also passes `bytes_total`; v2 derives percent / bytes / rate / ETA from
`file_size`, `bytes_total` and the live (<=5 s old) rate sample.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import contextlib
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = str(Path(__file__).resolve().parents[1])
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

pytestmark = pytest.mark.bd_module_wipe

_URL = "https://example.invalid/nubile/257960"
MB = 1024 * 1024


@pytest.fixture
def runner_db(clean_workdir):
    from bulk_downloader.db import db_init
    db_init()
    (clean_workdir / "screenshots").mkdir(exist_ok=True)
    return clean_workdir


def _registered(sid, runner):
    from bulk_downloader import app_state as st
    st.s_cfg[sid] = {"name": sid}
    st.runners[sid] = runner

    def _cleanup():
        st.runners.pop(sid, None)
        st.s_cfg.pop(sid, None)
    return _cleanup


def _running_row(url):
    from bulk_downloader import app as a
    body = a.app.test_client().get("/api/queue/v2").get_json()
    rows = [r for r in body.get("running", []) if r["url"] == url]
    assert len(rows) == 1, body
    return rows[0]


def _ticks(runner, total):
    """Two transfer ticks 0.2 s apart, as a producer reports them (1% per
    tick, so ~18 s of transfer remain)."""
    runner._update_job(_URL, "running", "⬇ 10%", file_size=total // 10, bytes_total=total)
    time.sleep(0.2)
    runner._update_job(_URL, "running", "⬇ 11%", file_size=total * 11 // 100, bytes_total=total)


def test_a_running_transfer_shows_its_progress_rate_and_eta(runner_db):
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner("nf1_t1", {"name": "nf1_t1"})
    _ticks(r, 400 * MB)
    cleanup = _registered("nf1_t1", r)
    try:
        row = _running_row(_URL)
    finally:
        cleanup()
    assert row["progress"] == 11, ("DL95-NUBILEFILMS-1: running row shows %r%% while the "
                                   "job is 11%% done" % row["progress"])
    assert row["bytes_done"] == 44 * MB and row["bytes_total"] == 400 * MB, row
    assert row["rate_human"] and row["rate_human"] != "0 B/s", row
    assert isinstance(row["eta_seconds"], int) and row["eta_seconds"] > 0, row


def test_negative_unknown_total_has_bytes_and_rate_but_no_percent(runner_db):
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner("nf1_t2", {"name": "nf1_t2"})
    r._update_job(_URL, "running", "Direct • 1 MB", file_size=1 * MB)
    time.sleep(0.2)
    r._update_job(_URL, "running", "Direct • 3 MB", file_size=3 * MB)
    cleanup = _registered("nf1_t2", r)
    try:
        row = _running_row(_URL)
    finally:
        cleanup()
    assert row["progress"] == 0 and row["eta_seconds"] is None, row
    assert row["bytes_done"] == 3 * MB and row["rate_human"], row


def test_negative_a_stalled_transfer_has_no_rate_or_eta(runner_db, monkeypatch):
    from bulk_downloader.runner import SiteRunner
    r = SiteRunner("nf1_t3", {"name": "nf1_t3"})
    _ticks(r, 400 * MB)
    r._job_progress_samples[_URL]["at"] -= 60      # last tick a minute ago
    cleanup = _registered("nf1_t3", r)
    try:
        row = _running_row(_URL)
    finally:
        cleanup()
    assert row["progress"] == 11, row
    assert row["rate_human"] == "" and row["eta_seconds"] is None, row


# ── a real producer: the library Direct path passes its total ──────────────

class _SlowResponse:
    status_code = 200

    def __init__(self, chunks):
        self.chunks = chunks
        self.headers = {"content-length": str(sum(len(c) for c in chunks))}

    def iter_bytes(self, _size):
        for i, c in enumerate(self.chunks):
            if i:
                time.sleep(1.05)            # the Direct path emits at most 1/s
            yield c


def test_the_direct_path_tick_carries_the_total(monkeypatch, tmp_path):
    import httpx
    from bulk_downloader import ssrf_transport
    from bulk_downloader.runner_transport import TransportMixin

    ticks = []

    class _Runner(TransportMixin):
        site_id = "nf1"

        def __init__(self):
            self.config = {}
            self._stop = threading.Event()

        def _download_proxy_url(self):
            return None

        def _regional_gateway_router(self):
            return None

        def _update_job(self, url, status, message, **extra):
            ticks.append(extra)

    body = [b"a" * 1000, b"b" * 1000, b"c" * 1000]
    resp = _SlowResponse(body)
    monkeypatch.setattr(ssrf_transport, "guarded_transport", lambda *a, **k: None)
    monkeypatch.setattr(ssrf_transport, "owning_stream",
                        lambda *a, **k: contextlib.nullcontext(resp))
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: object())
    assert _Runner()._do_direct_http_download(
        "https://example.org/page", "https://example.org/video", str(tmp_path / "v.mp4"))
    progress = [t for t in ticks if "file_size" in t]
    assert progress, ticks
    assert all(t.get("bytes_total") == 3000 for t in progress), progress
