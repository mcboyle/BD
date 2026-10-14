"""v3.43.34: SSE pub/sub broker — real push, not server-side polling.

The v3.36 SSE implementation polls server state every 1s in the
generator and hash-compares to detect changes. That works but has
two costs:

  1. Up to 1s of latency between a state change and the client
     seeing it (the generator might be mid-sleep)
  2. Constant server-side CPU on the hash-compare loop, even when
     nothing's changing

This module replaces the polling generator with a proper pub/sub
broker:

  - Mutations call `publish(event_type, data)` which fans out to
    every subscriber's queue
  - SSE generators pull from their queue with a blocking get +
    timeout — zero CPU when idle, immediate delivery when something
    publishes
  - Multiple subscribers (multiple browser tabs, mobile + desktop)
    share zero state and don't fight each other

## Event types

  - `status` — full state snapshot (replaces old 1s poll)
  - `dashboard` — aggregated health bar data
  - `event_log` — single new event log entry, as it's added
  - `download_progress` — per-URL progress updates (throttled to
    once per second per URL to avoid hammering the client with
    sub-second progress noise on big files)
  - `queue_change` — queue rows added/removed
  - `error` — server-side error visible to the client

## Throttling

Some publishers fire VERY rapidly (download progress fires per
chunk written — could be 100/sec on a fast file). The broker
throttles per-(event_type, key) at 1/sec to keep the wire light.
A publish inside the window is not lost: the newest one is held and
sent when the window closes (trailing flush), so "97% then 100%" ends
on 100%. The flush is driven by subscribers' pull() calls, so with no
SSE client connected nothing is sent (there is nobody to send it to).
The per-key table is bounded (THROTTLE_KEYS_MAX, least recently sent
evicted first; an evicted key's held event is sent at eviction).

## Per-client stream cap

Each /api/stream connection holds one WSGI worker thread for its whole
life. acquire_stream() caps open streams per client address
(STREAMS_PER_CLIENT_MAX) so one client cannot starve the pool.

## Backpressure

Each subscriber has a bounded queue (max 200 events). If a slow
client can't drain that fast, OLDER events are dropped — newest
wins. Prevents memory blowup from a stuck client.

## Compatibility

When this broker is unavailable (import fails, or the call
publish() raises), publishers MUST silently no-op. The legacy
poll loop still runs on the client as a safety net, so missing
SSE events just means slightly stale state until next poll.
"""
from __future__ import annotations

import heapq
import json
import queue
import threading
import time
from collections import OrderedDict
from typing import Any

from . import dev_events as _dev_events  # import-clean ring-buffer tap


# Per-subscriber queue cap. If a client can't drain this fast,
# we drop oldest to make room. 200 events typically = 2-5 seconds
# of high-activity payload.
SUBSCRIBER_QUEUE_MAX = 200

# How often a given (event_type, key) can be republished. Keeps
# rapidly-firing progress events (1 per chunk write) from
# saturating the wire when N=4 chunks at 100ms each = 40 events/sec.
THROTTLE_S = 1.0

# Max (event_type, key) entries kept for throttling. Keys are per URL
# (and per URL+status), so a long run would otherwise grow the table
# for the life of the process.
THROTTLE_KEYS_MAX = 1024

# Max concurrent /api/stream connections per client address. A browser
# needs one per open tab; the waitress pool is 30 threads by default.
STREAMS_PER_CLIENT_MAX = 4


class _Subscriber:
    """One active SSE client connection. Owns a bounded queue
    that the publisher drops events into and the SSE generator
    pulls from."""
    __slots__ = ("q", "created_at", "id")

    def __init__(self, sub_id: int):
        self.q = queue.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        self.created_at = time.time()
        self.id = sub_id


class _StreamSlot:
    """One admitted /api/stream connection. release() is idempotent so
    every exit path (generator finally, response close, teardown) can
    call it."""
    __slots__ = ("_broker", "client", "_released")

    def __init__(self, broker, client: str):
        self._broker = broker
        self.client = client
        self._released = False

    def release(self):
        self._broker._release_stream(self)


class _Broker:
    """Process-wide pub/sub broker. Singleton — access via
    get_broker()."""

    def __init__(self):
        self._subscribers: dict[int, _Subscriber] = {}
        self._lock = threading.Lock()
        self._next_id = 0
        # Per-(event_type, key) throttle state, least recently sent
        # first: key -> [last_sent, throttle_s, held_msg or None].
        self._last_publish: OrderedDict = OrderedDict()
        self._throttle_lock = threading.Lock()
        # (due, key) for held events; stale items are skipped on pop.
        self._due: list = []
        # client address -> open /api/stream connections
        self._streams: dict[str, int] = {}

    # ── Subscribe / unsubscribe ────────────────────────────────────

    def subscribe(self) -> _Subscriber:
        """Register a new subscriber. Returns the subscriber handle
        which the SSE generator pulls from."""
        with self._lock:
            self._next_id += 1
            sub = _Subscriber(self._next_id)
            self._subscribers[sub.id] = sub
        return sub

    def unsubscribe(self, sub: _Subscriber):
        """Drop a subscriber. Called when the SSE generator exits
        (client disconnected, server reload, etc.)."""
        with self._lock:
            self._subscribers.pop(sub.id, None)

    # ── Publish ────────────────────────────────────────────────────

    def publish(self, event_type: str, data: Any,
                  throttle_key: str = "",
                  throttle_s: float = THROTTLE_S):
        """Fan out an event to every subscriber. Returns immediately;
        slow subscribers don't block publishers.

        Args:
          event_type: SSE event name (e.g. "status", "event_log")
          data: JSON-serializable payload
          throttle_key: when non-empty, throttle per (event_type,
            throttle_key) tuple — fast-firing progress events use
            this to avoid spam
          throttle_s: time window for throttling (default 1.0s)
        """
        if throttle_key:
            key = (event_type, throttle_key)
            now = time.time()
            with self._throttle_lock:
                self._sweep_throttle(now)
                ent = self._last_publish.get(key)
                if ent is not None and now - ent[0] < throttle_s:
                    # Inside the window: hold the newest (serialized now,
                    # so a later mutation of `data` can't change it) and
                    # send it when the window closes.
                    msg = self._format(event_type, data)
                    if msg is None:
                        return
                    if ent[2] is None:
                        heapq.heappush(self._due, (ent[0] + ent[1], key))
                    ent[2] = msg
                    return
                self._last_publish[key] = [now, throttle_s, None]
                self._last_publish.move_to_end(key)
                evicted = self._evict_throttle()
            for msg in evicted:
                self._deliver(msg)
        msg = self._format(event_type, data)
        if msg is not None:
            self._deliver(msg)

    def _format(self, event_type, data):
        """Build the SSE message. Returns (safe_event_type, payload, msg),
        or None for an unusable event type or non-serializable data."""
        # Defense in depth: sanitize event_type so a newline in the
        # value can't inject a fake event into the SSE stream. SSE is
        # line-delimited — a payload containing `\nevent: status\n`
        # would be parsed by the browser as a separate event.
        # Currently no callsite passes user-controlled event types,
        # but we want this to remain safe if that ever changes.
        if not isinstance(event_type, str) or not event_type:
            return None  # silently drop garbage event types
        # Strip newlines, carriage returns, and colons (which are also
        # significant in SSE field syntax)
        safe_event_type = (event_type.replace("\n", " ")
                                       .replace("\r", " ")
                                       .replace(":", "-"))
        # Cap length so a 1MB event-name can't blow the wire either
        if len(safe_event_type) > 64:
            safe_event_type = safe_event_type[:64]
        # Build the SSE-formatted message once; reused for every sub
        try:
            payload = json.dumps(data, default=str)
        except Exception:
            # Non-serializable data — drop silently. Better than
            # crashing the publisher.
            return None
        return (safe_event_type, payload,
                f"event: {safe_event_type}\ndata: {payload}\n\n")

    def _deliver(self, formatted):
        """Fan a formatted message out to every subscriber."""
        safe_event_type, payload, msg = formatted
        # Fan out. Hold the lock only long enough to snapshot the
        # subscriber list; queue operations are independent.
        with self._lock:
            subs = list(self._subscribers.values())
        # Dev-tools event tap (U22 / D-65). Records the event that is
        # about to be fanned out into a bounded ring buffer. Fully
        # fail-safe — wrapped so a tap bug can never affect delivery.
        try:
            _dev_events.record(safe_event_type, payload, len(subs))
        except Exception:
            pass
        for sub in subs:
            try:
                # put_nowait: don't block publishers on slow clients.
                # If the queue is full, drop the OLDEST event so the
                # newest still gets through (it's more relevant).
                sub.q.put_nowait(msg)
            except queue.Full:
                # Drain one, then push. Best-effort — a concurrent
                # subscriber pull might race but that's fine, the
                # next put attempt will succeed if there's room.
                try:
                    sub.q.get_nowait()
                    sub.q.put_nowait(msg)
                except (queue.Empty, queue.Full):
                    pass

    # ── Throttle table upkeep (caller holds _throttle_lock) ────────

    def _sweep_throttle(self, now: float):
        """Drop expired entries from the least-recently-sent end. An
        expired entry throttles nothing, so dropping it changes no
        behaviour; stop at the first live or held entry."""
        table = self._last_publish
        while table:
            ent = next(iter(table.values()))
            if ent[2] is not None or now - ent[0] < ent[1]:
                break
            table.popitem(last=False)

    def _evict_throttle(self) -> list:
        """Enforce THROTTLE_KEYS_MAX. Returns the held events of evicted
        keys; the caller sends them after releasing the lock."""
        out = []
        table = self._last_publish
        while len(table) > THROTTLE_KEYS_MAX:
            _key, ent = table.popitem(last=False)
            if ent[2] is not None:
                out.append(ent[2])
        if len(self._due) > 2 * THROTTLE_KEYS_MAX:
            # Items for evicted keys are dead; rebuild from live holds.
            self._due = [(e[0] + e[1], k) for k, e in table.items()
                         if e[2] is not None]
            heapq.heapify(self._due)
        return out

    def _flush_due(self, now: float):
        """Send every held event whose throttle window has closed."""
        out = []
        with self._throttle_lock:
            while self._due and self._due[0][0] <= now:
                _due, key = heapq.heappop(self._due)
                ent = self._last_publish.get(key)
                # Skip stale items: key evicted, nothing held, or sent
                # again since (a newer item covers the new window).
                if ent is None or ent[2] is None or now - ent[0] < ent[1]:
                    continue
                out.append(ent[2])
                ent[0], ent[2] = now, None
                self._last_publish.move_to_end(key)
        for msg in out:
            self._deliver(msg)

    def _next_due(self):
        with self._throttle_lock:
            return self._due[0][0] if self._due else None

    # ── Subscriber-side pull ───────────────────────────────────────

    def pull(self, sub: _Subscriber, timeout: float = 15.0) -> str | None:
        """Block until an event is available or timeout elapses.
        Returns the pre-formatted SSE message string, or None on
        timeout (in which case the SSE generator should send a
        heartbeat comment to keep the connection alive).

        Also sends held (throttled) events whose window has closed,
        waking early for the next one that is due."""
        deadline = time.time() + timeout
        while True:
            now = time.time()
            self._flush_due(now)
            wait = deadline - now
            due = self._next_due()
            if due is not None:
                wait = min(wait, max(due - now, 0.005))
            try:
                if wait <= 0:
                    return sub.q.get_nowait()
                return sub.q.get(timeout=wait)
            except queue.Empty:
                if time.time() >= deadline:
                    return None

    # ── Per-client stream cap ──────────────────────────────────────

    def acquire_stream(self, client: str) -> _StreamSlot | None:
        """Admit one /api/stream connection for `client`, or return
        None when it already holds STREAMS_PER_CLIENT_MAX."""
        client = client or "unknown"
        with self._lock:
            n = self._streams.get(client, 0)
            if n >= STREAMS_PER_CLIENT_MAX:
                return None
            self._streams[client] = n + 1
        return _StreamSlot(self, client)

    def _release_stream(self, slot: _StreamSlot):
        with self._lock:
            if slot._released:
                return
            slot._released = True
            n = self._streams.get(slot.client, 0) - 1
            if n > 0:
                self._streams[slot.client] = n
            else:
                self._streams.pop(slot.client, None)

    # ── Diagnostics ────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Snapshot for the /api/sse_status endpoint. Useful for
        debugging 'why isn't my UI updating' issues."""
        with self._lock:
            subs = list(self._subscribers.values())
            open_streams = sum(self._streams.values())
        now = time.time()
        return {
            "subscriber_count": len(subs),
            "subscribers": [
                {
                    "id": s.id,
                    "queued": s.q.qsize(),
                    "queue_capacity": SUBSCRIBER_QUEUE_MAX,
                    "age_seconds": round(now - s.created_at, 1),
                }
                for s in subs
            ],
            "throttle_keys_tracked": len(self._last_publish),
            "open_streams": open_streams,
        }


# Singleton accessor
_BROKER = None
_BROKER_LOCK = threading.Lock()


def get_broker() -> _Broker:
    """Module-level accessor. Lazy construction so import doesn't
    spin up the broker until something actually needs it."""
    global _BROKER
    if _BROKER is None:
        with _BROKER_LOCK:
            if _BROKER is None:
                _BROKER = _Broker()
    return _BROKER


def publish(event_type: str, data: Any,
              throttle_key: str = "",
              throttle_s: float = THROTTLE_S):
    """Module-level shortcut: get_broker().publish(...). Use this
    from publisher sites — concise and safe.

    Defensively wraps the broker call so a broker bug can't crash
    the caller. The SSE feed is a UX optimization, not load-bearing.
    """
    try:
        get_broker().publish(event_type, data,
                              throttle_key=throttle_key,
                              throttle_s=throttle_s)
    except Exception:
        pass
