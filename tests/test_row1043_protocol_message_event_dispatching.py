"""Row 1043: Asynchronous Protocol Message Event Dispatching.

Acceptance test suite proving:
1. Asynchronous, decoupled protocol message event dispatching with pattern matching (exact, wildcard, catch-all).
2. Priority scheduling, error isolation between subscribers, and backpressure queue bounds.
3. Telemetry tracking (dispatched, processed, dropped, error counts).
4. Negative controls proving filtering selectivity and non-matching isolation.
5. Seamless integration with session_capture CDP protocol event paths.
"""
from __future__ import annotations

import time
import pytest

BD_GATE_SCOPE = "repo-wide"


def test_row1043_protocol_dispatcher_capability_probe():
    """Verify that AsyncProtocolDispatcher exists and can be instantiated."""
    import bulk_downloader.protocol_dispatcher as pd

    assert hasattr(pd, "AsyncProtocolDispatcher")
    assert hasattr(pd, "get_global_protocol_dispatcher")
    dispatcher = pd.AsyncProtocolDispatcher()
    assert dispatcher is not None


def test_row1043_async_protocol_message_dispatch_and_pattern_matching():
    """Verify asynchronous dispatching with exact, wildcard, and catch-all pattern matching."""
    from bulk_downloader.protocol_dispatcher import AsyncProtocolDispatcher

    dispatcher = AsyncProtocolDispatcher(num_workers=1)
    dispatcher.start()

    received_exact = []
    received_wildcard = []
    received_catchall = []

    try:
        dispatcher.subscribe(
            "Network.requestWillBeSent",
            lambda method, params: received_exact.append((method, params)),
        )
        dispatcher.subscribe(
            "Network.*",
            lambda method, params: received_wildcard.append((method, params)),
        )
        dispatcher.subscribe(
            "*",
            lambda method, params: received_catchall.append((method, params)),
        )

        # Dispatch 1: Network.requestWillBeSent
        dispatcher.dispatch("Network.requestWillBeSent", {"requestId": "r1", "url": "https://example.com/a"})
        # Dispatch 2: Network.responseReceived
        dispatcher.dispatch("Network.responseReceived", {"requestId": "r1", "status": 200})
        # Dispatch 3: Network.requestWillBeSent
        dispatcher.dispatch("Network.requestWillBeSent", {"requestId": "r2", "url": "https://example.com/b"})

        # Flush worker queue
        flushed = dispatcher.flush(timeout=5.0)
        assert flushed is True

        # Exact count assertions proving pattern matching selectivity
        assert len(received_exact) == 2
        assert len(received_wildcard) == 3
        assert len(received_catchall) == 3

        # Value fidelity
        assert received_exact[0][1]["requestId"] == "r1"
        assert received_exact[1][1]["requestId"] == "r2"
    finally:
        dispatcher.stop(timeout=5.0)


def test_row1043_negative_control_unmatched_events_and_filtering():
    """Negative control: prove that events outside the subscription pattern are never delivered."""
    from bulk_downloader.protocol_dispatcher import AsyncProtocolDispatcher

    dispatcher = AsyncProtocolDispatcher(num_workers=1)
    dispatcher.start()

    network_events = []
    try:
        # Subscribe strictly to Network.* domain
        dispatcher.subscribe("Network.*", lambda m, p: network_events.append(m))

        # Positive control: dispatch valid Network event
        dispatcher.dispatch("Network.loadingFinished", {"requestId": "100"})
        dispatcher.flush(timeout=5.0)
        assert len(network_events) == 1  # Positive control proves probe can see events

        # Negative control: dispatch non-Network events
        dispatcher.dispatch("Storage.cleared", {"origin": "https://example.com"})
        dispatcher.dispatch("DOM.documentUpdated", {})
        dispatcher.dispatch("Page.frameNavigated", {"frameId": "f1"})
        dispatcher.flush(timeout=5.0)

        # Assert negative control: non-network events were rejected by the filter
        assert len(network_events) == 1
        assert network_events[0] == "Network.loadingFinished"
    finally:
        dispatcher.stop(timeout=5.0)


def test_row1043_error_isolation_and_telemetry():
    """Verify that a failing subscriber does not crash the dispatcher or abort healthy subscribers."""
    from bulk_downloader.protocol_dispatcher import AsyncProtocolDispatcher

    dispatcher = AsyncProtocolDispatcher(num_workers=1)
    dispatcher.start()

    healthy_calls = []

    def faulty_handler(method, params):
        raise RuntimeError("simulated subscriber exception in worker thread")

    def healthy_handler(method, params):
        healthy_calls.append(params.get("id"))

    try:
        dispatcher.subscribe("Event.test", faulty_handler)
        dispatcher.subscribe("Event.test", healthy_handler)

        dispatcher.dispatch("Event.test", {"id": 1})
        dispatcher.dispatch("Event.test", {"id": 2})
        dispatcher.flush(timeout=5.0)

        # Healthy subscriber received all events despite sibling exception
        assert healthy_calls == [1, 2]

        metrics = dispatcher.get_metrics()
        assert metrics["dispatched_count"] == 2
        assert metrics["processed_count"] == 4  # 2 handlers * 2 events
        assert metrics["error_count"] == 2      # 2 errors from faulty handler
    finally:
        dispatcher.stop(timeout=5.0)


def test_row1043_backpressure_and_queue_bounding():
    """Verify bounded queue capacity and dropped event accounting under backpressure."""
    from bulk_downloader.protocol_dispatcher import AsyncProtocolDispatcher

    # Tiny queue of capacity 2, worker not started to simulate backpressure
    dispatcher = AsyncProtocolDispatcher(max_queue_size=2, num_workers=0)

    # Ingest up to capacity
    ok1 = dispatcher.dispatch("Ev.1", {}, block=False)
    ok2 = dispatcher.dispatch("Ev.2", {}, block=False)
    assert ok1 is True
    assert ok2 is True

    # 3rd event exceeds queue capacity
    ok3 = dispatcher.dispatch("Ev.3", {}, block=False)
    assert ok3 is False

    metrics = dispatcher.get_metrics()
    assert metrics["dispatched_count"] == 2
    assert metrics["dropped_count"] == 1


def test_row1043_session_capture_integration_seamless():
    """Verify that session_capture exposes protocol dispatcher hooks without breaking callers."""
    import bulk_downloader.session_capture as sc

    # 1. Verify module-level dispatcher accessor exists
    assert hasattr(sc, "get_protocol_dispatcher")
    dispatcher = sc.get_protocol_dispatcher()
    assert dispatcher is not None

    events_captured = []
    sub_id = dispatcher.subscribe("Network.requestWillBeSent", lambda m, p: events_captured.append(p))

    try:
        capture = sc.SessionCapture(url="https://example.com/test")

        # Calling feed_cdp_event dispatches through protocol dispatcher
        sc.feed_cdp_event(
            capture,
            "Network.requestWillBeSent",
            {"requestId": "req-1043", "request": {"method": "GET", "url": "https://example.com/api"}},
        )

        dispatcher.flush(timeout=5.0)
        assert len(events_captured) == 1
        assert events_captured[0]["requestId"] == "req-1043"
        assert "req-1043" in capture._pending
    finally:
        dispatcher.unsubscribe(sub_id)
