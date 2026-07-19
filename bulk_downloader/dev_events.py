"""dev_events.py — bounded in-memory tap of SSE broker events.

A ring buffer the dev-tools event-tap (U22 / D-65) reads.
sse_broker.publish() records into it via record(). This module does
NO work at import — just a deque and a lock — so importing it from the
broker is free and import-safe (mirrors dev_metrics.py).

The buffer is bounded by construction (deque maxlen): the real leak
class in a memory-safe language is never-evicted retention, so the
cap is structural, not a periodic prune.
"""
from __future__ import annotations

import threading
import time
from collections import deque

# Ring-buffer capacity. The broker fires rapidly (progress events), so
# this is a short rolling window of the most recent events, not a log.
_MAX_EVENTS = 300

_lock = threading.Lock()
_events: deque = deque(maxlen=_MAX_EVENTS)
_seq = 0


def record(event_type, payload, recipients=0):
    """Append one published SSE event to the ring buffer.

    Called from sse_broker.publish() on the hot path — O(1), and must
    never raise (the caller already wraps it, but be defensive too).
    The payload is stored as a truncated summary, never the full
    object, so the buffer's memory stays bounded.
    """
    global _seq
    try:
        summary = payload if isinstance(payload, str) else str(payload)
        if len(summary) > 300:
            summary = summary[:300] + "...(truncated)"
        try:
            recipients = int(recipients)
        except (TypeError, ValueError):
            recipients = 0
        # Build the entry fully BEFORE taking the lock / bumping the
        # sequence, so a bad field can never leave _seq and the buffer
        # out of step (total_recorded stays exactly == events appended).
        entry = {
            "ts": round(time.time(), 3),
            "event_type": str(event_type)[:64],
            "payload": summary,
            "recipients": recipients,
        }
        with _lock:
            _seq += 1
            entry["seq"] = _seq
            _events.append(entry)
    except Exception:
        # A tap must never affect event delivery.
        pass


def recent(limit=50):
    """Most-recent-first list of recorded events (newest `limit`)."""
    with _lock:
        items = list(_events)
    items.reverse()
    if limit and limit > 0:
        items = items[:limit]
    return items


def stats():
    """Buffer occupancy and lifetime count."""
    with _lock:
        return {"buffered": len(_events), "capacity": _MAX_EVENTS,
                "total_recorded": _seq}


def reset():
    """Test helper — clear the buffer and the sequence counter."""
    global _seq
    with _lock:
        _events.clear()
        _seq = 0
