"""Row 376: streaming buffers must not each become a database write."""
from __future__ import annotations

import statistics
import threading
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"

_SMALL_BUFFER_BYTES = 8183
_SMALL_BUFFER_COUNT = 64
_PAGE_URL = "https://page.invalid/scene"
_FILE_URL = "https://cdn.invalid/scene.mp4"


class _Slot:
    def release(self):
        return None


class _Context:
    def cookies(self):
        return []


class _CffiResponse:
    def __init__(
        self,
        chunks,
        *,
        status_code=200,
        before_yield=None,
        raise_after=None,
    ):
        self.status_code = status_code
        self.headers = {
            "Content-Length": str(sum(len(chunk) for chunk in chunks))
        }
        self._chunks = list(chunks)
        self._before_yield = before_yield
        self._raise_after = raise_after
        self.observed_sizes = []
        self.requested_chunk_sizes = []
        self.closed = False

    def iter_content(self, chunk_size=None):
        self.requested_chunk_sizes.append(chunk_size)
        for index, chunk in enumerate(self._chunks):
            self.observed_sizes.append(len(chunk))
            if self._before_yield is not None:
                self._before_yield(index)
            yield chunk
            if self._raise_after == index + 1:
                raise RuntimeError("fixture stream failure")

    def close(self):
        self.closed = True


def _new_runner(*, use_curl_cffi=True):
    from bulk_downloader import runner as runner_mod

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "row376"
    runner.config = {
        "parallel_chunks": 1,
        "use_curl_cffi": use_curl_cffi,
        "auto_chunk_size": False,
        "chunk_size_mb": 4,
    }
    runner._stop = threading.Event()
    runner._pause = threading.Event()
    runner._pause.set()
    runner._lock = threading.Lock()
    runner._worker_threads = []
    runner.jobs = {}
    runner._state = "running"
    runner._stop_auto_retry = lambda: None
    runner._pick_fastest_mirror = lambda url: url
    runner._current_cap_mbps = lambda: 0
    runner._download_proxy_url = lambda: None
    runner._observe_throughput = lambda *args: None
    runner._update_job = lambda *args, **kwargs: None
    runner.log_event = lambda *args, **kwargs: None
    runner.log = type(
        "Log", (), {"warning": lambda *args, **kwargs: None}
    )()
    return runner


def _instrument_accounting(monkeypatch):
    from bulk_downloader import daily_budget, db, rate_limit
    from bulk_downloader import runner_transport as transport

    budget_writes = []
    bandwidth_writes = []
    monkeypatch.setattr(
        daily_budget,
        "record_site_bytes",
        lambda site_id, n_bytes, **kwargs: budget_writes.append(
            (site_id, n_bytes)
        ),
    )
    # Keep the batching tests deterministic even on a loaded test host. The
    # separate schedule test below advances this clock deliberately.
    monkeypatch.setattr(daily_budget.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        transport,
        "record_bandwidth",
        lambda n_bytes: bandwidth_writes.append(n_bytes),
    )
    monkeypatch.setattr(rate_limit, "acquire", lambda url: _Slot())
    monkeypatch.setattr(db, "queue_upsert", lambda *args, **kwargs: None)
    return budget_writes, bandwidth_writes


def _capture_retry_worker(
    monkeypatch, daily_budget, *, fail_construct=False,
    fail_start=False, reset=True
):
    class CapturedWorkers(list):
        pass

    scheduled = CapturedWorkers()
    scheduled.waits = []
    if reset:
        with daily_budget._RETRY_CONDITION:
            daily_budget._RETRY_DELTAS.clear()
            daily_budget._RETRY_WORKER = None

    class FakeThread:
        def __init__(self, *, target, daemon, name):
            if fail_construct:
                raise RuntimeError("fixture cannot construct retry thread")
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            if fail_start:
                raise RuntimeError("fixture cannot start retry thread")
            scheduled.append(self)

    monkeypatch.setattr(daily_budget.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        daily_budget,
        "_retry_wait_locked",
        lambda timeout: scheduled.waits.append(timeout),
    )
    return scheduled


def _run_sequential(monkeypatch, tmp_path, response):
    from curl_cffi import requests as cffi_requests

    runner = _new_runner()
    budget_writes, bandwidth_writes = _instrument_accounting(monkeypatch)
    response.budget_writes = budget_writes
    response.bandwidth_writes = bandwidth_writes
    monkeypatch.setattr(
        cffi_requests, "request", lambda *args, **kwargs: response
    )
    final_path = Path(tmp_path) / "scene.mp4"
    result = runner._http_download(
        _PAGE_URL, object(), _Context(), _FILE_URL, final_path
    )
    return {
        "runner": runner,
        "result": result,
        "final_path": final_path,
        "response": response,
        "budget_writes": budget_writes,
        "bandwidth_writes": bandwidth_writes,
    }


def _assert_small_buffer_precondition(response):
    observed = response.observed_sizes
    assert len(observed) == _SMALL_BUFFER_COUNT
    assert len(observed) > 32, "fixture did not exercise a many-buffer stream"
    assert statistics.median(observed) == _SMALL_BUFFER_BYTES
    assert max(observed) == _SMALL_BUFFER_BYTES
    assert response.requested_chunk_sizes == [4 * 1024 * 1024]


def test_many_small_cffi_buffers_make_one_budget_write_without_losing_bytes(
    monkeypatch, tmp_path
):
    chunks = [b"x" * _SMALL_BUFFER_BYTES] * _SMALL_BUFFER_COUNT
    response = _CffiResponse(chunks)

    run = _run_sequential(monkeypatch, tmp_path, response)

    _assert_small_buffer_precondition(response)
    transferred = _SMALL_BUFFER_BYTES * _SMALL_BUFFER_COUNT
    assert run["result"] == (transferred, transferred)
    assert run["final_path"].stat().st_size == transferred
    assert len(run["budget_writes"]) == 1, (
        "a sub-second transfer must flush its accumulated daily bytes once; "
        f"observed {len(run['budget_writes'])} database writes for "
        f"{len(response.observed_sizes)} small response buffers"
    )
    assert sum(n for _, n in run["budget_writes"]) == transferred
    assert sum(run["bandwidth_writes"]) == transferred
    assert run["runner"]._daily_byte_accumulators == set()


def test_stopped_stream_flushes_the_exact_partial_byte_total(monkeypatch, tmp_path):
    runner = _new_runner()
    budget_writes, bandwidth_writes = _instrument_accounting(monkeypatch)
    chunks = [b"s" * _SMALL_BUFFER_BYTES] * _SMALL_BUFFER_COUNT

    def stop_before_ninth_buffer(index):
        if index == 8:
            runner._stop.set()

    response = _CffiResponse(chunks, before_yield=stop_before_ninth_buffer)
    from curl_cffi import requests as cffi_requests
    from bulk_downloader.constants import _HTTPDownloadFailed

    monkeypatch.setattr(
        cffi_requests, "request", lambda *args, **kwargs: response
    )
    final_path = Path(tmp_path) / "stopped.mp4"

    with pytest.raises(_HTTPDownloadFailed, match="stopped"):
        runner._http_download(
            _PAGE_URL, object(), _Context(), _FILE_URL, final_path
        )

    assert len(response.observed_sizes) == 9
    assert len(response.observed_sizes) > 8, (
        "interrupted fixture did not yield many small response buffers"
    )
    assert statistics.median(response.observed_sizes) == _SMALL_BUFFER_BYTES
    partial_path = final_path.with_suffix(final_path.suffix + ".part")
    expected_partial = 8 * _SMALL_BUFFER_BYTES
    assert not final_path.exists()
    assert partial_path.stat().st_size == expected_partial
    assert len(budget_writes) == 1
    assert sum(n for _, n in budget_writes) == expected_partial
    assert sum(bandwidth_writes) == expected_partial


def test_stream_failure_flushes_the_exact_partial_byte_total(monkeypatch, tmp_path):
    chunks = [b"f" * _SMALL_BUFFER_BYTES] * _SMALL_BUFFER_COUNT
    response = _CffiResponse(chunks, raise_after=5)
    from bulk_downloader.constants import _HTTPDownloadFailed

    with pytest.raises(_HTTPDownloadFailed, match="fixture stream failure"):
        _run_sequential(monkeypatch, tmp_path, response)

    assert len(response.observed_sizes) == 5
    assert len(response.observed_sizes) > 1, (
        "failed fixture did not yield multiple small response buffers"
    )
    assert statistics.median(response.observed_sizes) == _SMALL_BUFFER_BYTES
    expected_partial = 5 * _SMALL_BUFFER_BYTES
    partial_path = Path(tmp_path) / "scene.mp4.part"
    assert partial_path.stat().st_size == expected_partial
    assert len(response.budget_writes) == 1
    assert sum(n for _, n in response.budget_writes) == expected_partial
    assert sum(response.bandwidth_writes) == expected_partial


class _PauseGate:
    def __init__(self, budget_writes):
        self._set = True
        self.budget_writes = budget_writes
        self.totals_seen_while_paused = []

    def is_set(self):
        return self._set

    def clear(self):
        self._set = False

    def wait(self, timeout=None):
        if not self._set:
            self.totals_seen_while_paused.append(
                sum(n for _, n in self.budget_writes)
            )
            self._set = True
        return True


def test_pause_flushes_pending_bytes_before_waiting(monkeypatch, tmp_path):
    from curl_cffi import requests as cffi_requests

    runner = _new_runner()
    budget_writes, bandwidth_writes = _instrument_accounting(monkeypatch)
    gate = _PauseGate(budget_writes)
    runner._pause = gate

    def pause_before_second_buffer(index):
        if index == 1:
            gate.clear()

    chunks = [b"p" * _SMALL_BUFFER_BYTES] * 2
    response = _CffiResponse(chunks, before_yield=pause_before_second_buffer)
    monkeypatch.setattr(
        cffi_requests, "request", lambda *args, **kwargs: response
    )
    final_path = Path(tmp_path) / "paused.mp4"

    result = runner._http_download(
        _PAGE_URL, object(), _Context(), _FILE_URL, final_path
    )

    total = 2 * _SMALL_BUFFER_BYTES
    assert response.observed_sizes == [_SMALL_BUFFER_BYTES] * 2
    assert gate.totals_seen_while_paused == [_SMALL_BUFFER_BYTES]
    assert result == (total, total)
    assert [n for _, n in budget_writes] == [
        _SMALL_BUFFER_BYTES,
        _SMALL_BUFFER_BYTES,
    ]
    assert sum(bandwidth_writes) == total


def test_one_large_buffer_remains_one_write_and_exact(monkeypatch, tmp_path):
    large = b"l" * (4 * 1024 * 1024)
    response = _CffiResponse([large])

    run = _run_sequential(monkeypatch, tmp_path, response)

    assert response.observed_sizes == [len(large)]
    assert response.requested_chunk_sizes == [len(large)]
    assert run["result"] == (len(large), len(large))
    assert run["budget_writes"] == [("row376", len(large))]
    assert sum(run["bandwidth_writes"]) == len(large)


def test_parallel_small_buffers_share_one_budget_flush_and_exact_total(
    monkeypatch, tmp_path
):
    from curl_cffi import requests as cffi_requests
    from bulk_downloader import resume

    runner = _new_runner()
    budget_writes, bandwidth_writes = _instrument_accounting(monkeypatch)
    observed_sizes = []
    requested_chunk_sizes = []
    total = _SMALL_BUFFER_BYTES * _SMALL_BUFFER_COUNT

    class RangeResponse:
        status_code = 206

        def __init__(self, length):
            full, tail = divmod(length, _SMALL_BUFFER_BYTES)
            self.chunks = [b"r" * _SMALL_BUFFER_BYTES] * full
            if tail:
                self.chunks.append(b"r" * tail)

        def iter_content(self, chunk_size=None):
            requested_chunk_sizes.append(chunk_size)
            for chunk in self.chunks:
                observed_sizes.append(len(chunk))
                yield chunk

        def close(self):
            return None

    def range_request(method, url, **kwargs):
        raw_range = kwargs["headers"]["Range"]
        start, end = (
            int(value) for value in raw_range.removeprefix("bytes=").split("-")
        )
        return RangeResponse(end - start + 1)

    monkeypatch.setattr(cffi_requests, "request", range_request)
    monkeypatch.setattr(resume, "head_probe", lambda *args, **kwargs: {})
    final_path = Path(tmp_path) / "parallel.mp4"

    result = runner._http_download_parallel(
        _PAGE_URL, _Context(), _FILE_URL, final_path, total=total, n_chunks=2
    )

    assert len(observed_sizes) == _SMALL_BUFFER_COUNT
    assert len(observed_sizes) > 32
    assert statistics.median(observed_sizes) == _SMALL_BUFFER_BYTES
    assert max(observed_sizes) == _SMALL_BUFFER_BYTES
    assert requested_chunk_sizes == [1024 * 1024, 1024 * 1024]
    assert result == (total, total)
    assert final_path.stat().st_size == total
    assert len(budget_writes) == 1
    assert sum(n for _, n in budget_writes) == total
    assert sum(bandwidth_writes) == total
    assert runner._daily_byte_accumulators == set()


def test_parallel_failure_then_resume_counts_only_bytes_from_each_run(
    monkeypatch, tmp_path
):
    from bulk_downloader import resume
    from bulk_downloader.constants import _HTTPDownloadFailed
    from curl_cffi import requests as cffi_requests

    runner = _new_runner()
    budget_writes, bandwidth_writes = _instrument_accounting(monkeypatch)
    partial_buffer_count = 3
    total_buffer_count = 8
    partial_bytes = partial_buffer_count * _SMALL_BUFFER_BYTES
    total = total_buffer_count * _SMALL_BUFFER_BYTES
    requested_ranges = []

    class RangeResponse:
        status_code = 206

        def __init__(self, chunks, *, fail_after=False):
            self.chunks = chunks
            self.fail_after = fail_after

        def iter_content(self, chunk_size=None):
            assert chunk_size == 1024 * 1024
            yield from self.chunks
            if self.fail_after:
                raise RuntimeError("fixture parallel stream failure")

        def close(self):
            return None

    def range_request(method, url, **kwargs):
        raw_range = kwargs["headers"]["Range"]
        start, end = (
            int(value) for value in raw_range.removeprefix("bytes=").split("-")
        )
        requested_ranges.append((start, end))
        if len(requested_ranges) == 1:
            assert start == 0
            return RangeResponse(
                [b"a" * _SMALL_BUFFER_BYTES] * partial_buffer_count,
                fail_after=True,
            )
        assert start == partial_bytes
        return RangeResponse(
            [b"b" * _SMALL_BUFFER_BYTES]
            * (total_buffer_count - partial_buffer_count)
        )

    monkeypatch.setattr(cffi_requests, "request", range_request)
    monkeypatch.setattr(resume, "head_probe", lambda *args, **kwargs: {})
    final_path = Path(tmp_path) / "parallel-resume.mp4"

    with pytest.raises(_HTTPDownloadFailed, match="fixture parallel stream failure"):
        runner._http_download_parallel(
            _PAGE_URL, _Context(), _FILE_URL, final_path,
            total=total, n_chunks=1,
        )

    checkpoint = resume.load(final_path)
    assert checkpoint is not None
    assert checkpoint["chunks"][0]["done_bytes"] == partial_bytes
    assert budget_writes == [("row376", partial_bytes)]
    assert sum(bandwidth_writes) == partial_bytes
    assert runner._daily_byte_accumulators == set()

    result = runner._http_download_parallel(
        _PAGE_URL, _Context(), _FILE_URL, final_path,
        total=total, n_chunks=1,
    )

    remaining = total - partial_bytes
    assert requested_ranges == [(0, total - 1), (partial_bytes, total - 1)]
    assert result == (total, remaining)
    assert final_path.read_bytes() == (
        b"a" * partial_bytes + b"b" * remaining
    )
    assert resume.load(final_path) is None
    assert len(budget_writes) == 2
    assert sum(n for _, n in budget_writes) == total
    assert sum(bandwidth_writes) == total
    assert runner._daily_byte_accumulators == set()


def test_budget_accumulator_flush_schedule_is_bounded_and_lossless(monkeypatch):
    from bulk_downloader import daily_budget

    now = [0.0]
    writes = []
    monkeypatch.setattr(daily_budget.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        daily_budget,
        "record_site_bytes",
        lambda site_id, n_bytes, **kwargs: writes.append((site_id, n_bytes)),
    )
    accumulator = daily_budget.DailyByteAccumulator("row376")

    accumulator.add(10)
    now[0] = 0.999
    accumulator.add(20)
    assert writes == []

    now[0] = 1.0
    accumulator.add(30)
    assert writes == [("row376", 60)]

    now[0] = 1.5
    accumulator.add(40)
    assert writes == [("row376", 60)]
    accumulator.flush()
    accumulator.flush()
    assert writes == [("row376", 60), ("row376", 40)]
    assert sum(n for _, n in writes) == 100


def test_blocked_database_flush_for_one_site_does_not_block_another_add(
    monkeypatch,
):
    from bulk_downloader import daily_budget

    database_entered = threading.Event()
    release_database = threading.Event()
    other_add_finished = threading.Event()
    writes = []

    def record(site_id, n_bytes, **kwargs):
        if site_id == "row376-blocked":
            database_entered.set()
            assert release_database.wait(timeout=5)
        writes.append((site_id, n_bytes))
        return True

    monkeypatch.setattr(daily_budget, "_today_ymd", lambda: "2026-08-29")
    monkeypatch.setattr(daily_budget, "record_site_bytes", record)
    blocked = daily_budget.DailyByteAccumulator("row376-blocked")
    independent = daily_budget.DailyByteAccumulator("row376-independent")
    blocked.add(10)

    blocked_flush = threading.Thread(target=blocked.flush)
    blocked_flush.start()
    assert database_entered.wait(timeout=5)

    def add_independent_bytes():
        independent.add(20)
        other_add_finished.set()

    independent_add = threading.Thread(target=add_independent_bytes)
    independent_add.start()
    assert other_add_finished.wait(timeout=5), (
        "site B's in-memory add waited on site A's blocked database flush"
    )

    release_database.set()
    blocked_flush.join(timeout=5)
    independent_add.join(timeout=5)
    assert not blocked_flush.is_alive()
    assert not independent_add.is_alive()
    assert independent.flush() is True
    assert writes == [
        ("row376-blocked", 10),
        ("row376-independent", 20),
    ]


def test_failed_budget_write_is_retried_with_the_full_pending_delta(monkeypatch):
    from bulk_downloader import daily_budget

    now = [0.0]
    attempts = []
    successful = []
    scheduled = _capture_retry_worker(monkeypatch, daily_budget)

    def fail_once_then_record(site_id, n_bytes, **kwargs):
        attempts.append((site_id, n_bytes, kwargs.get("ymd")))
        if len(attempts) == 1:
            raise OSError("fixture database lock")
        successful.append(n_bytes)
        return True

    monkeypatch.setattr(daily_budget.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(daily_budget, "_today_ymd", lambda: "2026-08-29")
    monkeypatch.setattr(daily_budget, "record_site_bytes", fail_once_then_record)
    accumulator = daily_budget.DailyByteAccumulator("row376")

    accumulator.add(60)
    now[0] = 1.0
    accumulator.add(40)
    # The failed 100-byte batch moves to the shared broker; later bytes retain
    # their own exact ownership while that broker waits.
    now[0] = 1.5
    accumulator.add(20)
    accumulator.flush()

    assert attempts == [
        ("row376", 100, "2026-08-29"),
        ("row376", 20, "2026-08-29"),
    ]
    assert successful == [20]
    assert len(scheduled) == 1
    with daily_budget._RETRY_CONDITION:
        assert daily_budget._RETRY_DELTAS == {
            ("row376", "2026-08-29", 0): 100
        }

    scheduled[0].target()

    assert scheduled.waits == [1.0]
    assert attempts[-1] == ("row376", 100, "2026-08-29")
    assert sum(successful) == 120
    assert daily_budget._RETRY_DELTAS == {}


def test_retry_snapshot_preserves_same_key_delta_enqueued_during_write(
    monkeypatch,
):
    from bulk_downloader import daily_budget

    real_thread = threading.Thread
    retry_write_entered = threading.Event()
    release_retry_write = threading.Event()
    attempts = []
    successful = []
    scheduled = _capture_retry_worker(monkeypatch, daily_budget)

    def fail_initial_then_block_first_retry(site_id, n_bytes, **kwargs):
        attempts.append((site_id, n_bytes, kwargs.get("ymd")))
        if len(attempts) == 1:
            return False
        if len(attempts) == 2:
            retry_write_entered.set()
            assert release_retry_write.wait(timeout=5), (
                "fixture retry write was not released"
            )
        successful.append(n_bytes)
        return True

    monkeypatch.setattr(daily_budget, "_today_ymd", lambda: "2026-08-29")
    monkeypatch.setattr(
        daily_budget, "record_site_bytes", fail_initial_then_block_first_retry
    )
    accumulator = daily_budget.DailyByteAccumulator("row376-snapshot")
    accumulator.add(100)

    assert accumulator.flush() is False
    assert len(scheduled) == 1
    worker = real_thread(target=scheduled[0].target, daemon=True)
    worker.start()
    try:
        assert retry_write_entered.wait(timeout=5), (
            "fixture retry worker did not enter its snapshotted write"
        )
        assert daily_budget._enqueue_retry_delta(
            "row376-snapshot", "2026-08-29", 0, 40
        ) is True
        with daily_budget._RETRY_CONDITION:
            assert daily_budget._RETRY_DELTAS == {
                ("row376-snapshot", "2026-08-29", 0): 140
            }
        assert len(scheduled) == 1
    finally:
        release_retry_write.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert scheduled.waits == [1.0, 1.0]
    assert attempts == [
        ("row376-snapshot", 100, "2026-08-29"),
        ("row376-snapshot", 100, "2026-08-29"),
        ("row376-snapshot", 40, "2026-08-29"),
    ]
    assert successful == [100, 40]
    assert sum(successful) == 140
    with daily_budget._RETRY_CONDITION:
        assert daily_budget._RETRY_DELTAS == {}
        assert daily_budget._RETRY_WORKER is None


def test_many_failed_transfers_share_one_bounded_retry_owner(monkeypatch):
    from bulk_downloader import daily_budget

    attempts = []
    scheduled = _capture_retry_worker(monkeypatch, daily_budget)

    def database_down(site_id, n_bytes, **kwargs):
        attempts.append(n_bytes)
        return False

    monkeypatch.setattr(daily_budget, "_today_ymd", lambda: "2026-08-29")
    monkeypatch.setattr(daily_budget, "record_site_bytes", database_down)
    for _ in range(20):
        accumulator = daily_budget.DailyByteAccumulator("row376-many")
        accumulator.add(77)
        assert accumulator.flush() is False

    assert attempts == [77] * 20
    assert len(scheduled) == 1
    assert scheduled[0].daemon is True
    assert scheduled[0].name == "daily-byte-retry"
    with daily_budget._RETRY_CONDITION:
        assert daily_budget._RETRY_DELTAS == {
            ("row376-many", "2026-08-29", 0): 20 * 77
        }
        daily_budget._RETRY_DELTAS.clear()
        daily_budget._RETRY_WORKER = None


@pytest.mark.parametrize("failure_point", ["construct", "start"])
def test_retry_thread_failure_is_fail_silent_and_single_owned(
    monkeypatch, failure_point
):
    from bulk_downloader import daily_budget

    monkeypatch.setattr(daily_budget, "_today_ymd", lambda: "2026-08-29")
    monkeypatch.setattr(
        daily_budget, "record_site_bytes", lambda *args, **kwargs: False
    )
    _capture_retry_worker(
        monkeypatch,
        daily_budget,
        fail_construct=failure_point == "construct",
        fail_start=failure_point == "start",
    )
    accumulator = daily_budget.DailyByteAccumulator("row376-start-fail")
    accumulator.add(55)

    assert accumulator.flush() is False
    assert daily_budget._RETRY_WORKER is None
    assert daily_budget._RETRY_DELTAS == {
        ("row376-start-fail", "2026-08-29", 0): 55
    }

    scheduled = _capture_retry_worker(
        monkeypatch, daily_budget, reset=False
    )
    recorded = []
    monkeypatch.setattr(
        daily_budget,
        "record_site_bytes",
        lambda site_id, n_bytes, **kwargs: recorded.append(
            (site_id, n_bytes)
        ) or True,
    )
    unrelated = daily_budget.DailyByteAccumulator("row376-unrelated")
    unrelated.add(9)
    assert unrelated.flush() is True
    assert len(scheduled) == 1
    assert daily_budget._RETRY_DELTAS == {
        ("row376-start-fail", "2026-08-29", 0): 55
    }
    scheduled[0].target()
    assert scheduled.waits == [1.0]
    assert recorded == [
        ("row376-unrelated", 9),
        ("row376-start-fail", 55),
    ]
    assert daily_budget._RETRY_DELTAS == {}


def test_terminal_write_failure_retries_after_transfer_unregisters(
    monkeypatch, tmp_path
):
    from bulk_downloader import daily_budget
    from curl_cffi import requests as cffi_requests

    runner = _new_runner()
    _, bandwidth_writes = _instrument_accounting(monkeypatch)
    attempts = []
    successful_deltas = []
    scheduled = _capture_retry_worker(monkeypatch, daily_budget)

    def fail_once_then_record(site_id, n_bytes, **kwargs):
        attempts.append(n_bytes)
        if len(attempts) == 1:
            return False
        successful_deltas.append(n_bytes)
        return True

    monkeypatch.setattr(daily_budget, "record_site_bytes", fail_once_then_record)
    body = b"t" * _SMALL_BUFFER_BYTES
    response = _CffiResponse([body])
    monkeypatch.setattr(
        cffi_requests, "request", lambda *args, **kwargs: response
    )
    final_path = Path(tmp_path) / "terminal-retry.mp4"

    result = runner._http_download(
        _PAGE_URL, object(), _Context(), _FILE_URL, final_path
    )

    assert result == (len(body), len(body))
    assert attempts == [len(body)]
    assert successful_deltas == []
    assert len(scheduled) == 1
    assert runner._daily_byte_accumulators == set()

    scheduled[0].target()

    assert attempts == [len(body), len(body)]
    assert successful_deltas == [len(body)]
    assert sum(bandwidth_writes) == len(body)


def test_accumulator_preserves_the_day_each_buffer_was_written(monkeypatch):
    from bulk_downloader import daily_budget

    days = iter(["2026-08-29", "2026-08-30"])
    writes = []

    def record(site_id, n_bytes, **kwargs):
        writes.append((site_id, n_bytes, kwargs.get("ymd")))
        return True

    monkeypatch.setattr(daily_budget.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(daily_budget, "_today_ymd", lambda: next(days))
    monkeypatch.setattr(daily_budget, "record_site_bytes", record)
    accumulator = daily_budget.DailyByteAccumulator("row376")

    accumulator.add(10)
    accumulator.add(20)
    accumulator.flush()

    assert writes == [
        ("row376", 10, "2026-08-29"),
        ("row376", 20, "2026-08-30"),
    ]


def test_operator_reset_invalidates_buffered_and_retry_owned_bytes(monkeypatch):
    from bulk_downloader import daily_budget

    site_id = "row376-reset"
    today = ["2026-08-28"]
    attempts = []
    scheduled = _capture_retry_worker(monkeypatch, daily_budget)

    class Cursor:
        rowcount = 1

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            return Cursor()

    def database_locked(site, n_bytes, **kwargs):
        attempts.append((n_bytes, kwargs.get("ymd")))
        return False

    monkeypatch.setattr(daily_budget, "_today_ymd", lambda: today[0])
    monkeypatch.setattr(daily_budget, "_ensure_table", lambda: None)
    monkeypatch.setattr(daily_budget._db, "db_conn", lambda: Connection())
    monkeypatch.setattr(daily_budget, "record_site_bytes", database_locked)
    previous_day = daily_budget.DailyByteAccumulator(site_id)
    previous_day.add(7)
    today[0] = "2026-08-29"
    locally_buffered = daily_budget.DailyByteAccumulator(site_id)
    retry_owned = daily_budget.DailyByteAccumulator(site_id)
    locally_buffered.add(5)
    retry_owned.add(10)
    assert retry_owned.flush() is False
    assert attempts == [(10, "2026-08-29")]
    assert len(scheduled) == 1

    assert daily_budget.reset_today(site_id) is True
    scheduled[0].target()
    assert daily_budget._RETRY_DELTAS == {}

    monkeypatch.setattr(
        daily_budget,
        "record_site_bytes",
        lambda site, n_bytes, **kwargs: attempts.append(
            (n_bytes, kwargs.get("ymd"))
        ) or True,
    )
    # The local five-byte pre-reset delta is stale, but the prior day's pending
    # bytes remain valid; only post-reset bytes may rebuild today's total.
    assert locally_buffered.flush() is True
    assert previous_day.flush() is True
    locally_buffered.add(20)
    assert locally_buffered.flush() is True
    assert attempts == [
        (10, "2026-08-29"),
        (7, "2026-08-28"),
        (20, "2026-08-29"),
    ]


@pytest.mark.parametrize("operator_action", ["pause", "stop"])
def test_operator_interrupt_flushes_while_response_iterator_is_blocked(
    monkeypatch, tmp_path, operator_action
):
    from bulk_downloader.constants import _HTTPDownloadFailed
    from curl_cffi import requests as cffi_requests

    runner = _new_runner()
    budget_writes, bandwidth_writes = _instrument_accounting(monkeypatch)
    iterator_blocked = threading.Event()
    release_iterator = threading.Event()

    def block_before_second_buffer(index):
        if index == 1:
            iterator_blocked.set()
            assert release_iterator.wait(timeout=5), "fixture iterator was not released"

    response = _CffiResponse(
        [b"a" * _SMALL_BUFFER_BYTES, b"b" * _SMALL_BUFFER_BYTES],
        before_yield=block_before_second_buffer,
    )
    monkeypatch.setattr(
        cffi_requests, "request", lambda *args, **kwargs: response
    )
    final_path = Path(tmp_path) / f"blocked-{operator_action}.mp4"
    failures = []

    def download():
        try:
            runner._http_download(
                _PAGE_URL, object(), _Context(), _FILE_URL, final_path
            )
        except Exception as exc:
            failures.append(exc)

    worker = threading.Thread(target=download)
    worker.start()
    assert iterator_blocked.wait(timeout=5), "fixture did not block its iterator"

    getattr(runner, operator_action)()

    assert budget_writes == [("row376", _SMALL_BUFFER_BYTES)]
    assert sum(n for _, n in budget_writes) == _SMALL_BUFFER_BYTES
    assert sum(bandwidth_writes) == _SMALL_BUFFER_BYTES

    # End the fixture without letting its second buffer reach the file.
    if operator_action == "pause":
        runner.stop()
    release_iterator.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], _HTTPDownloadFailed)
    assert "stopped" in str(failures[0])
    assert budget_writes == [("row376", _SMALL_BUFFER_BYTES)]
    assert final_path.with_suffix(final_path.suffix + ".part").stat().st_size == (
        _SMALL_BUFFER_BYTES
    )


def test_pause_during_file_write_flushes_post_write_bytes(monkeypatch, tmp_path):
    import builtins

    from curl_cffi import requests as cffi_requests

    runner = _new_runner()
    budget_writes, bandwidth_writes = _instrument_accounting(monkeypatch)
    write_started = threading.Event()
    release_write = threading.Event()
    iterator_blocked = threading.Event()
    release_iterator = threading.Event()
    final_path = Path(tmp_path) / "pause-write-race.mp4"
    partial_path = final_path.with_suffix(final_path.suffix + ".part")
    real_open = builtins.open

    class BlockingWriteFile:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def write(self, data):
            write_started.set()
            assert release_write.wait(timeout=5), "fixture write was not released"
            return self.wrapped.write(data)

    def blocking_open(path, mode="r", *args, **kwargs):
        wrapped = real_open(path, mode, *args, **kwargs)
        if Path(path) == partial_path and mode == "wb":
            return BlockingWriteFile(wrapped)
        return wrapped

    def block_before_second_buffer(index):
        if index == 1:
            iterator_blocked.set()
            assert release_iterator.wait(timeout=5), "fixture iterator was not released"

    response = _CffiResponse(
        [b"a" * _SMALL_BUFFER_BYTES, b"b" * _SMALL_BUFFER_BYTES],
        before_yield=block_before_second_buffer,
    )
    monkeypatch.setattr(builtins, "open", blocking_open)
    monkeypatch.setattr(
        cffi_requests, "request", lambda *args, **kwargs: response
    )
    failures = []

    def download():
        try:
            runner._http_download(
                _PAGE_URL, object(), _Context(), _FILE_URL, final_path
            )
        except Exception as exc:
            failures.append(exc)

    worker = threading.Thread(target=download)
    worker.start()
    assert write_started.wait(timeout=5), "fixture did not enter file write"

    runner.pause()
    assert budget_writes == []
    release_write.set()
    assert iterator_blocked.wait(timeout=5), "fixture did not block after write"

    assert budget_writes == [("row376", _SMALL_BUFFER_BYTES)]
    assert sum(bandwidth_writes) == _SMALL_BUFFER_BYTES

    runner.stop()
    release_iterator.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert "stopped" in str(failures[0])
    assert budget_writes == [("row376", _SMALL_BUFFER_BYTES)]
    assert partial_path.stat().st_size == _SMALL_BUFFER_BYTES
