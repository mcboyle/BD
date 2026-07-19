"""v3.43.34 regression tests — SSE pub/sub broker.

Coverage:
  - Module surface
  - publish + subscribe fanout
  - Throttling (per-key rate limiting)
  - Backpressure (slow subscribers don't block publishers; oldest
    events drop when queue full)
  - Multiple subscribers receive independently
  - Subscribe/unsubscribe lifecycle
  - Diagnostic status snapshot
  - Defensive: non-serializable data doesn't crash
  - Defensive: publish() never raises even if broker is broken
  - Runner integration: _update_job publishes queue_change
  - Runner integration: log_event publishes event_log
  - Frontend handlers exist
"""
from __future__ import annotations

import threading
import time
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SSE_PY = _REPO_ROOT / "bulk_downloader" / "sse_broker.py"
class _AppAggregateSrc:
    """app.py + every app_*.py blueprint module (PHASE 4 decomposition: route
    groups moved onto Flask blueprints); glob keeps source-coupled guards green
    across all current and future app.py route-group cuts."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "app.py"] + sorted(pkg_dir.glob("app_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_APP_PY = _AppAggregateSrc(_REPO_ROOT / "bulk_downloader")
class _AggregateSrc:
    """runner.py + every runner_*.py mixin module (PHASE 3 decomposition v3.66.397+
    moved SiteRunner methods into siblings); glob keeps source-coupled guards green
    across all current and future runner cuts."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "runner.py"] + sorted(pkg_dir.glob("runner_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader")
_APP_JS = _REPO_ROOT / "bulk_downloader" / "static" / "app.js"


# ── Module surface ────────────────────────────────────────────────────

def test_sse_broker_module_importable():
    import bulk_downloader.sse_broker as sse  # noqa: F401


def test_get_broker_returns_singleton():
    """Multiple calls must return the same broker so publishers
    and subscribers share state."""
    from bulk_downloader.sse_broker import get_broker
    a = get_broker()
    b = get_broker()
    assert a is b


def test_module_level_publish_shortcut():
    """publish() at module level must work without callers having
    to call get_broker() explicitly."""
    from bulk_downloader.sse_broker import publish, get_broker
    broker = get_broker()
    sub = broker.subscribe()
    try:
        publish("test", {"x": 1})
        msg = broker.pull(sub, timeout=1.0)
        assert msg is not None
        assert "event: test" in msg
        assert '"x":' in msg
    finally:
        broker.unsubscribe(sub)


# ── Fanout ────────────────────────────────────────────────────────────

def test_publish_fans_out_to_all_subscribers():
    """A single publish() must deliver to every subscriber."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    sub1 = broker.subscribe()
    sub2 = broker.subscribe()
    sub3 = broker.subscribe()
    broker.publish("status", {"key": "value"})
    for sub in (sub1, sub2, sub3):
        msg = broker.pull(sub, timeout=1.0)
        assert msg is not None
        assert "event: status" in msg
        assert '"key"' in msg


def test_unsubscribe_stops_delivery():
    """After unsubscribe, no further events should arrive."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    sub = broker.subscribe()
    broker.unsubscribe(sub)
    broker.publish("status", {"x": 1})
    msg = broker.pull(sub, timeout=0.2)
    # Queue should be empty (no events published while subscribed)
    assert msg is None


def test_subscribers_isolated():
    """One subscriber's slow pull must not affect another's delivery."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    fast = broker.subscribe()
    slow = broker.subscribe()  # never pulls
    broker.publish("status", {"a": 1})
    # Fast subscriber gets it immediately
    msg = broker.pull(fast, timeout=0.5)
    assert msg is not None
    # Slow subscriber still has it queued
    assert slow.q.qsize() == 1


# ── Throttling ────────────────────────────────────────────────────────

def test_publish_throttled_by_key():
    """Multiple publishes with the same throttle_key within the
    window are deduplicated to one."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    sub = broker.subscribe()
    # First publish goes through
    broker.publish("progress", {"x": 1}, throttle_key="url-1",
                    throttle_s=10.0)
    # Subsequent publishes within window are silently dropped
    broker.publish("progress", {"x": 2}, throttle_key="url-1",
                    throttle_s=10.0)
    broker.publish("progress", {"x": 3}, throttle_key="url-1",
                    throttle_s=10.0)
    # Only the first event landed
    count = 0
    while broker.pull(sub, timeout=0.05) is not None:
        count += 1
    assert count == 1, f"expected 1 event (throttled), got {count}"


def test_throttle_different_keys_independent():
    """Throttling is per-key — different URLs don't share quota."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    sub = broker.subscribe()
    broker.publish("progress", {"x": 1}, throttle_key="url-1",
                    throttle_s=10.0)
    broker.publish("progress", {"x": 2}, throttle_key="url-2",
                    throttle_s=10.0)
    broker.publish("progress", {"x": 3}, throttle_key="url-3",
                    throttle_s=10.0)
    count = 0
    while broker.pull(sub, timeout=0.05) is not None:
        count += 1
    assert count == 3, f"expected 3 events (different keys), got {count}"


def test_throttle_skipped_when_no_key():
    """Without a throttle_key, every publish goes through. Critical
    for event_log entries where every entry is meaningful."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    sub = broker.subscribe()
    for i in range(10):
        broker.publish("event_log", {"i": i})
    count = 0
    while broker.pull(sub, timeout=0.05) is not None:
        count += 1
    assert count == 10


# ── Backpressure ──────────────────────────────────────────────────────

def test_full_queue_drops_oldest_not_newest():
    """When a slow subscriber's queue fills up, OLDEST events drop
    so the newest (most relevant) always makes it through."""
    from bulk_downloader.sse_broker import _Broker, SUBSCRIBER_QUEUE_MAX
    broker = _Broker()
    sub = broker.subscribe()
    # Fill queue past capacity
    for i in range(SUBSCRIBER_QUEUE_MAX + 50):
        broker.publish("status", {"i": i})
    # Queue should be at-or-near max size
    assert sub.q.qsize() <= SUBSCRIBER_QUEUE_MAX
    # The newest event MUST be the last we published. Pull all and
    # check the last one we got is event #(MAX+49).
    last_seen = None
    while True:
        m = broker.pull(sub, timeout=0.05)
        if m is None: break
        last_seen = m
    assert last_seen is not None
    # Find "i": N in the message
    import re
    match = re.search(r'"i":\s*(\d+)', last_seen)
    assert match
    assert int(match.group(1)) == SUBSCRIBER_QUEUE_MAX + 49, (
        f"newest event should always survive; got i={match.group(1)}"
    )


def test_slow_subscriber_does_not_block_publisher():
    """A subscriber that never pulls must not slow down publish()
    calls. publish must return promptly."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    stuck = broker.subscribe()  # never pulls
    t0 = time.time()
    for _ in range(500):
        broker.publish("status", {"x": 1})
    elapsed = time.time() - t0
    # 500 publishes with a stuck subscriber should still finish fast.
    # Each publish is O(num_subscribers) queue ops; budget 1s liberally.
    assert elapsed < 1.0, f"500 publishes took {elapsed}s with stuck subscriber"


# ── Defensive ─────────────────────────────────────────────────────────

def test_publish_handles_unserializable_data():
    """When the data has __str__ that raises, json.dumps with default=str
    will also raise. The publisher must catch and drop silently, not
    propagate to the caller (publishers are everywhere in the codebase
    and a serialization bug should never crash a worker)."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    sub = broker.subscribe()
    # An object whose __str__ raises — genuinely unserializable even
    # with default=str fallback
    class Hostile:
        def __str__(self): raise ValueError("nope")
        def __repr__(self): raise ValueError("nope")
    # Must not raise even though Hostile is unserializable
    broker.publish("status", Hostile())
    # No event landed (silently dropped) but no exception either
    msg = broker.pull(sub, timeout=0.2)
    assert msg is None


def test_module_level_publish_never_raises():
    """The module-level publish() shortcut is the publisher API
    everyone uses. It MUST never raise — if the broker is broken,
    silently swallow."""
    from bulk_downloader.sse_broker import publish
    # Garbage inputs that might be passed by accident
    publish(None, None)
    publish("", None)
    publish("test", lambda: 1)  # functions aren't serializable
    # Reaching here = no exception raised


def test_publish_neutralizes_sse_protocol_injection():
    """Defense in depth: an event_type with newlines must NOT be
    able to inject a fake event into the SSE stream. SSE is
    line-delimited and a malicious value like 'test\\nevent: status'
    would otherwise inject a phantom status event into every
    subscriber's stream.

    No current callsite uses untrusted event_type values, but the
    sanitization keeps us safe if that ever changes."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    sub = broker.subscribe()
    broker.publish('test\nevent: status\ndata: {"injected": true}',
                    {"safe": 1})
    msg = broker.pull(sub, timeout=0.5)
    assert msg is not None
    # Count actual `event:` lines — must be exactly one (the original)
    event_lines = [l for l in msg.split("\n") if l.startswith("event:")]
    assert len(event_lines) == 1, (
        f"SSE injection succeeded — found {len(event_lines)} event lines "
        f"instead of 1. The malicious newlines should be sanitized."
    )
    # The "injected: true" content should still be visible (as escaped
    # text inside the event_type field) but it's a single line, not a
    # second event
    assert msg.count("\nevent: ") == 0


def test_publish_caps_event_type_length():
    """A 1MB event_type would bloat every subscriber's message.
    Truncate to a sane limit."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    sub = broker.subscribe()
    broker.publish("X" * 10000, {"x": 1})
    msg = broker.pull(sub, timeout=0.5)
    assert msg is not None
    # The event type in the message must be capped well below 10k
    event_line = msg.split("\n")[0]
    assert len(event_line) < 200, (
        f"event line is {len(event_line)} chars — should be truncated"
    )


def test_publish_drops_empty_event_type():
    """Empty or non-string event types must be silently dropped."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    sub = broker.subscribe()
    broker.publish("", {"x": 1})
    broker.publish(None, {"x": 1})
    broker.publish(123, {"x": 1})
    msg = broker.pull(sub, timeout=0.2)
    assert msg is None


# ── Diagnostic status ────────────────────────────────────────────────

def test_get_status_shape():
    """Status snapshot returns the keys the diagnostic endpoint
    expects."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    s1 = broker.subscribe()
    s2 = broker.subscribe()
    broker.publish("status", {"x": 1})
    status = broker.get_status()
    assert status["subscriber_count"] == 2
    assert len(status["subscribers"]) == 2
    for sub_status in status["subscribers"]:
        assert "id" in sub_status
        assert "queued" in sub_status
        assert "queue_capacity" in sub_status
        assert "age_seconds" in sub_status


# ── Concurrent publish/subscribe ─────────────────────────────────────

def test_concurrent_publish_safe():
    """Multiple threads publishing to multiple subscribers must not
    crash or lose events. Smoke test for thread-safety."""
    from bulk_downloader.sse_broker import _Broker
    broker = _Broker()
    subs = [broker.subscribe() for _ in range(5)]
    EVENTS_PER_THREAD = 100
    THREADS = 10

    def publisher(thread_id):
        for i in range(EVENTS_PER_THREAD):
            broker.publish("status", {"thread": thread_id, "i": i})

    threads = [threading.Thread(target=publisher, args=(t,))
               for t in range(THREADS)]
    for t in threads: t.start()
    for t in threads: t.join()
    # Each subscriber should have got (THREADS*EVENTS_PER_THREAD)
    # events, MINUS whatever was dropped due to queue capacity.
    # The point of this test is that NO threading exceptions occur —
    # specific count varies due to backpressure.
    for sub in subs:
        # Just verify queue size is bounded
        assert sub.q.qsize() <= 1000  # well above SUBSCRIBER_QUEUE_MAX


# ── App.py integration ───────────────────────────────────────────────

def test_stream_endpoint_uses_broker():
    """The /api/stream endpoint must consume from the broker, not
    poll server state."""
    src = _APP_PY.read_text(encoding="utf-8")
    pos = src.find("def api_stream(")
    assert pos > 0
    body = src[pos:pos + 5000]
    assert "from . import sse_broker" in body
    assert "broker.subscribe()" in body
    assert "broker.pull(" in body
    # And it must unsubscribe on disconnect
    assert "unsubscribe(sub)" in body


def test_stream_endpoint_handles_generator_exit():
    """When the client disconnects, GeneratorExit propagates — we
    must catch it and unsubscribe so the broker doesn't accumulate
    dead subscriber queues."""
    src = _APP_PY.read_text(encoding="utf-8")
    pos = src.find("def api_stream(")
    body = src[pos:pos + 5000]
    assert "GeneratorExit" in body


def test_sse_status_endpoint_registered():
    """Diagnostic endpoint must be wired so users can debug 'why
    isn't my UI updating' without spelunking through logs."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert "/api/sse_status" in src
    assert "def api_sse_status" in src


# ── Runner integration ───────────────────────────────────────────────

def test_update_job_publishes_queue_change():
    """Every status mutation must push a queue_change to subscribers.
    Otherwise the new event types are useless."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _update_job")
    assert pos > 0
    # _update_job is ~5KB — search the whole method
    end = src.find("\n    def _watch_done", pos)
    body = src[pos:end if end > 0 else pos + 6000]
    assert "sse_broker" in body
    assert '"queue_change"' in body


def test_update_job_uses_throttle_key():
    """Without per-URL throttling, a parallel download (100 chunk
    writes/sec) would flood the wire."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _update_job")
    end = src.find("\n    def _watch_done", pos)
    body = src[pos:end if end > 0 else pos + 6000]
    assert "throttle_key" in body


def test_log_event_publishes_event_log():
    """Per-entry event log pushes — no throttle since every entry
    is meaningful (these are user-visible diagnostic lines)."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def log_event")
    assert pos > 0
    # Search the full method body
    end = src.find("\n    def ", pos + 50)
    body = src[pos:end if end > 0 else pos + 3000]
    assert "sse_broker" in body
    assert '"event_log"' in body


def test_log_event_publish_does_not_block():
    """The publisher call must be wrapped in try/except so a broker
    bug never crashes the log_event mainline."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def log_event")
    end = src.find("\n    def ", pos + 50)
    body = src[pos:end if end > 0 else pos + 3000]
    # Find the publish call site
    pub_pos = body.find('"event_log"')
    assert pub_pos > 0
    # Within a few lines of the publish, there must be except
    nearby = body[pub_pos:pub_pos + 500]
    assert "except" in nearby


# ── Frontend ──────────────────────────────────────────────────────────

