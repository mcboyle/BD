"""Ordered, bounded-concurrency segment retrieval for HLS callers.

A sliding-window PREFETCH BUFFER: at most ``window_size`` segments are in flight or held, and each
segment is handed to the caller (yielded, or written to a sink) the moment it is the head of the
window -- resident bytes stay <= window x segment for a stream of any length (correctness REFUTE
E1: the first cut joined every segment into one bytes object, 2x the whole stream in RAM). A failed
segment is refetched in place -- sequentially, up to ``retries`` times -- and the window continues;
the already-fetched bytes are never fetched again (REFUTE E2: one late blip restarted from 0).
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Iterator, Sequence


def iter_segments_in_order(
    segment_urls: Sequence[str],
    fetch_segment: Callable[[str], bytes],
    *,
    window_size: int = 8,
    retries: int = 1,
) -> Iterator[bytes]:
    """Yield each segment's bytes in input order, prefetching up to ``window_size`` ahead.

    A segment whose concurrent fetch raised is refetched sequentially (``retries`` attempts) before
    the error propagates; segments behind it stay in flight. Consumers that stop early cancel the
    window (generator close) instead of fetching the rest.
    """
    if window_size < 1:
        raise ValueError("window_size must be at least 1")
    if retries < 0:
        raise ValueError("retries must be at least 0")
    urls = list(segment_urls)
    if not urls:
        return

    futures: dict[int, Future[bytes]] = {}
    with ThreadPoolExecutor(max_workers=min(window_size, len(urls))) as executor:
        try:
            next_to_submit = 0
            while next_to_submit < min(window_size, len(urls)):
                futures[next_to_submit] = executor.submit(fetch_segment, urls[next_to_submit])
                next_to_submit += 1
            for index in range(len(urls)):
                future = futures.pop(index)
                try:
                    data = future.result()
                except Exception as exc:
                    data = _refetch(urls[index], fetch_segment, retries, exc)
                if next_to_submit < len(urls):
                    futures[next_to_submit] = executor.submit(fetch_segment, urls[next_to_submit])
                    next_to_submit += 1
                yield data
        finally:
            for future in futures.values():
                future.cancel()


def _refetch(url: str, fetch_segment: Callable[[str], bytes], retries: int, first: Exception) -> bytes:
    """Sequential in-place retries for ONE segment; the last error propagates when all fail."""
    error: Exception = first
    for _ in range(retries):
        try:
            return fetch_segment(url)
        except Exception as exc:  # noqa: BLE001 - the caller's fetch raised; retried, then re-raised below
            error = exc
    raise error


def fetch_segments_in_order(
    segment_urls: Sequence[str],
    fetch_segment: Callable[[str], bytes],
    *,
    window_size: int = 8,
    retries: int = 1,
    sink: Callable[[bytes], object] | None = None,
) -> bytes:
    """Fetch every segment through the window. With ``sink`` each segment is written as it arrives
    and b"" is returned (the caller holds nothing); without a sink the joined bytes are returned --
    a convenience for short streams, NOT for a multi-GB HLS (use ``sink`` or iter_segments_in_order)."""
    if sink is not None:
        for data in iter_segments_in_order(segment_urls, fetch_segment, window_size=window_size, retries=retries):
            sink(data)
        return b""
    return b"".join(iter_segments_in_order(segment_urls, fetch_segment, window_size=window_size, retries=retries))
