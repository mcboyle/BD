"""Tests for bulk_downloader.dev_metrics — the in-process request and
exception ring buffers that feed the Tier-1 latency / error-rate /
exception tools."""
from bulk_downloader import dev_metrics as dm


def test_record_and_snapshot_request():
    dm.reset()
    dm.record_request("GET", "/x", "/x", 200, 12.5)
    snap = dm.request_snapshot()
    assert len(snap) == 1
    assert snap[0]["method"] == "GET"
    assert snap[0]["status"] == 200
    assert snap[0]["duration_ms"] == 12.5


def test_record_and_snapshot_exception():
    dm.reset()
    try:
        raise ValueError("boom")
    except ValueError as e:
        dm.record_exception(e, "/y")
    snap = dm.exception_snapshot()
    assert len(snap) == 1
    assert snap[0]["type"] == "ValueError"
    assert "boom" in snap[0]["message"]
    assert snap[0]["path"] == "/y"
    assert "ValueError" in snap[0]["traceback"]


def test_reset_clears_both_buffers():
    dm.record_request("GET", "/a", "/a", 200, 1.0)
    dm.record_exception(RuntimeError("x"))
    dm.reset()
    assert dm.request_snapshot() == []
    assert dm.exception_snapshot() == []


def test_buffers_are_bounded_by_construction():
    # the leak class is never-evicted retention (lesson J1) — both
    # buffers are deque(maxlen=...), bounded with no eviction code
    assert dm._requests.maxlen == dm._MAX_REQUESTS
    assert dm._exceptions.maxlen == dm._MAX_EXCEPTIONS


def test_request_buffer_evicts_oldest_past_maxlen():
    dm.reset()
    for i in range(dm._MAX_REQUESTS + 25):
        dm.record_request("GET", f"/r{i}", "/r", 200, 1.0)
    snap = dm.request_snapshot()
    assert len(snap) == dm._MAX_REQUESTS                       # capped
    assert snap[-1]["path"] == f"/r{dm._MAX_REQUESTS + 24}"    # newest
    dm.reset()
