"""dl95-reptyle-3 (O1513 A5-A finding D2): the first progress tick divided by the epoch.

Measured on test2: "0% • 1.0 MB/111.4 MB • 3 B/s". Both transfer loops start with last_update=0 so the first chunk
reports at once, and computed the rate as bytes/(now - last_update) -- i.e. bytes/(seconds since 1970). A download
that then stalls keeps showing that first-tick rate. GREEN: the rate window of the first tick opens at the transfer's
start (runner_transport._progress_rate, used by the sequential and the parallel loop alike).

Hermetic: the sequential loop runs on scripted httpx responses (the row-986 harness shape) with a fake wall clock
at a real epoch; no network.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest

from bulk_downloader import runner_transport as transport

BD_GATE_SCOPE = "module"

EPOCH = 1_790_000_000.0
MB = 1024 * 1024


class _Response:
    def __init__(self, chunks, content_length):
        self.status_code = 200
        self.headers = {"Content-Length": str(content_length)}
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self, chunk_size=None):
        yield from self._chunks


def _run_sequential(monkeypatch, tmp_path, chunks, total, step=0.25):
    import httpx

    from bulk_downloader import rate_limit
    from bulk_downloader import runner as runner_mod

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "reptyle"
    runner.config = {"parallel_chunks": 1, "use_curl_cffi": False}
    runner._stop = threading.Event()
    runner._pause = threading.Event()
    runner._pause.set()
    runner._pick_fastest_mirror = lambda url: url
    runner._recommended_chunk_bytes = lambda: MB
    runner._current_cap_mbps = lambda: 0
    runner._download_proxy_url = lambda: None
    runner._observe_throughput = lambda *args: None
    runner.log_event = lambda *args, **kwargs: None
    runner.log = type("Log", (), {"warning": lambda *args, **kwargs: None})()
    messages, speeds = [], []
    runner._update_job = lambda _url, _state, msg="", **_kw: messages.append(msg)
    runner.record_transfer_progress = lambda _url, _done, _total, speed, *a, **k: speeds.append(speed)
    clock = [EPOCH]

    def now():
        clock[0] += step
        return clock[0]

    monkeypatch.setattr(httpx.Client, "stream", lambda self, *args, **kwargs: _Response(chunks, total))
    monkeypatch.setattr(transport.time, "time", now)
    monkeypatch.setattr(transport, "record_bandwidth", lambda delta: None)
    slot = type("Slot", (), {"release": lambda self: None})()
    monkeypatch.setattr(rate_limit, "acquire", lambda url: slot)
    ctx = type("Ctx", (), {"cookies": lambda self: []})()
    result = runner._http_download(
        "https://app.reptyle.example/movies/30470", object(), ctx, "https://cdn.reptyle.example/30470.mp4",
        Path(tmp_path) / "30470.mp4")
    return result, messages, speeds, clock


def test_first_tick_rate_is_bytes_over_time_since_the_transfer_started(monkeypatch, tmp_path):
    chunks = [b"a" * MB, b"b" * MB, b"c" * MB]
    result, messages, speeds, _clock = _run_sequential(monkeypatch, tmp_path, chunks, total=3 * MB)
    assert result == (3 * MB, 3 * MB)
    progress = [m for m in messages if m.startswith("⬇")]
    assert progress and progress[0].startswith("\u2b07 33% ") and "/3.0 MB" in progress[0], messages
    # the fake clock advances 0.25 s per read, so 1 MB arrived within a few seconds of the start:
    # anything under 100 KB/s is the epoch-divided rate the finding saw ("3 B/s")
    assert speeds[0] > 100 * 1024, speeds
    rate = re.search(r"• ([0-9.]+ [KMG]?B)/s", progress[0]).group(1)
    assert rate.endswith(("KB", "MB", "GB")), progress[0]


@pytest.mark.parametrize("bytes_now,bytes_prev,now,last_tick,started,want", [
    (MB, 0, EPOCH + 2, 0, EPOCH, MB / 2),               # first tick: window opens at `started`
    (3 * MB, MB, EPOCH + 4, EPOCH + 2, EPOCH, MB),      # later tick: window opens at the previous tick
    (MB, 0, EPOCH, 0, EPOCH, MB / 0.001),               # zero-length window is bounded, never a ZeroDivisionError
])
def test_progress_rate(bytes_now, bytes_prev, now, last_tick, started, want):
    assert transport._progress_rate(bytes_now, bytes_prev, now, last_tick, started) == pytest.approx(want)


def test_both_transfer_loops_use_the_one_rate_helper():
    src = Path(transport.__file__).read_text(encoding="utf-8")

    def body(name):
        start = src.index(f"    def {name}(")
        nxt = src.find("\n    def ", start + 1)
        return src[start:nxt]

    for name in ("_http_download_claimed", "_http_download_parallel"):  # the sequential loop, the parallel monitor
        assert "_progress_rate(" in body(name), name
        assert "/(now-last_update)" not in body(name) and "/ max(0.001, now - last_update)" not in body(name), name
    par = body("_http_download_parallel")
    # resumed bytes were not fetched in the first window
    assert par.index("pre_resume_total = sum(resume_offset)") < par.index("last_total_bytes = pre_resume_total")
