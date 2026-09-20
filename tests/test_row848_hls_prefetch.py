"""Behavior coverage for the row848 ordered segment prefetch helper."""

BD_GATE_SCOPE = "module"

import threading
import time
from hashlib import sha256

from bulk_downloader.hls_prefetch import fetch_segments_in_order


def test_prefetch_keeps_a_sliding_window_and_assembles_in_input_order():
    """Removing the order buffer would scramble bytes as workers finish."""
    started: list[str] = []
    lock = threading.Lock()

    def fetch(url: str) -> bytes:
        with lock:
            started.append(url)
        if url == "one":
            time.sleep(0.04)
        return {"one": b"A", "two": b"B", "three": b"C"}[url]

    assembled = fetch_segments_in_order(
        ["one", "two", "three"], fetch, window_size=2
    )

    assert started[:2] == ["one", "two"]
    assert assembled == b"ABC"
    assert sha256(assembled).hexdigest() == (
        "b5d4045c3f466fa91fe2cc6abe79232a1a57cdf104f7a26e716e0a1e2789df78"
    )


def test_a_failed_segment_is_refetched_in_place_and_the_window_continues():
    """REFUTE E2: one transient error refetches THAT segment only -- total fetch calls == N + failures,
    never a restart from segment 0 (the first cut refetched every already-fetched byte)."""
    attempts: list[str] = []
    first_failure = True

    def fetch(url: str) -> bytes:
        nonlocal first_failure
        attempts.append(url)
        if url == "two" and first_failure:
            first_failure = False
            raise OSError("temporary network failure")
        return {"one": b"A", "two": b"B", "three": b"C"}[url]

    assert fetch_segments_in_order(["one", "two", "three"], fetch, window_size=2) == b"ABC"
    assert attempts.count("one") == 1 and attempts.count("two") == 2 and attempts.count("three") == 1
    assert len(attempts) == 3 + 1

    # late blip in a long stream: N + 1 calls, not ~2N
    calls: list[int] = []
    blip = {60}

    def fetch_long(url: str) -> bytes:
        i = int(url)
        calls.append(i)
        if i in blip:
            blip.discard(i)
            raise OSError("blip")
        return bytes([i])

    urls = [str(i) for i in range(64)]
    assert fetch_segments_in_order(urls, fetch_long, window_size=8) == bytes(range(64))
    assert len(calls) == 64 + 1, len(calls)

    # a persistent failure propagates after `retries` sequential attempts, from the failing segment
    import pytest
    attempts.clear()

    def always_fails(url: str) -> bytes:
        attempts.append(url)
        if url == "two":
            raise OSError("down")
        return b"x"

    with pytest.raises(OSError, match="down"):
        fetch_segments_in_order(["one", "two", "three"], always_fails, window_size=2, retries=2)
    assert attempts.count("two") == 1 + 2


def test_resident_bytes_are_bounded_by_the_window_not_the_stream():
    """REFUTE E1: 64 x 256 KB through a window of 8 written to a sink -- peak resident memory stays
    below 3 x window x segment (the first cut's peak was 2 x the whole stream)."""
    import tracemalloc

    segment = 256 * 1024
    urls = [str(i) for i in range(64)]
    written: list[int] = []

    def fetch(url: str) -> bytes:
        return bytes([int(url) % 251]) * segment

    tracemalloc.start()
    try:
        fetch_segments_in_order(urls, fetch, window_size=8, sink=lambda data: written.append(len(data)))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert written == [segment] * 64
    assert peak < 3 * 8 * segment, f"peak {peak} bytes is not window-bounded (stream = {64 * segment})"

    # control: the joined form does hold the whole stream (documented convenience for short streams)
    tracemalloc.start()
    try:
        assert len(fetch_segments_in_order(urls, fetch, window_size=8)) == 64 * segment
        _, peak_joined = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak_joined >= 64 * segment


def test_iter_segments_yields_in_order_and_closing_early_cancels_the_window():
    from bulk_downloader.hls_prefetch import iter_segments_in_order
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        time.sleep(0.005)
        return url.encode()

    it = iter_segments_in_order([str(i) for i in range(50)], fetch, window_size=4)
    first_three = [next(it) for _ in range(3)]
    it.close()
    assert first_three == [b"0", b"1", b"2"]
    assert len(fetched) < 50  # the remaining segments were not fetched after close
