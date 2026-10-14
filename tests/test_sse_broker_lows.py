"""sse-broker-lows r1: media-serving FINDINGS F4, F5, F9.

F4  the per-(event_type, key) throttle table was never pruned: one entry
    per download URL for the life of the process (10k keys -> 10k).
F5  a publish inside the throttle window was dropped with no trailing
    flush: progress 97 then 100 within 1 s delivered only 97.
F9  /api/stream had no per-client cap; every SSE connection holds a WSGI
    worker for its life, so one client could hold the whole pool.

Every assertion message starts with SSE_BROKER_LOWS so a failure names
the finding it reopens.
"""
from __future__ import annotations

import json
import time

import pytest

BD_GATE_SCOPE = "module"

DIAG = "SSE_BROKER_LOWS"


def _broker():
    from bulk_downloader.sse_broker import _Broker
    return _Broker()


def _cap(name, default):
    from bulk_downloader import sse_broker
    return getattr(sse_broker, name, default)


def _drain(broker, sub, total_s, idle_s=None, want=None):
    """Pull everything that arrives within total_s (bounded poll, no
    fixed sleep). Stops early after idle_s with nothing new, or once
    `want` messages have arrived."""
    out = []
    deadline = time.time() + total_s
    last = time.time()
    while time.time() < deadline:
        msg = broker.pull(sub, timeout=min(0.1, max(deadline - time.time(), 0.001)))
        if msg is not None:
            out.append((time.time(), msg))
            last = time.time()
            if want is not None and len(out) >= want:
                break
        elif idle_s is not None and time.time() - last >= idle_s:
            break
    return out


def _values(msgs, field="p"):
    return [json.loads(m.split("data: ", 1)[1])[field] for _t, m in msgs]


# ── F4: bounded throttle table ─────────────────────────────────────────

def test_f4_distinct_keys_do_not_grow_the_table_without_bound():
    broker = _broker()
    cap = _cap("THROTTLE_KEYS_MAX", 1024)
    for i in range(10_000):
        broker.publish("download_progress", {"i": i},
                       throttle_key=f"site:https://x.invalid/{i}")
    tracked = broker.get_status()["throttle_keys_tracked"]
    assert tracked <= cap, (
        f"{DIAG} F4: 10000 distinct throttle keys -> table holds {tracked} "
        f"(bound {cap})")


def test_f4_expired_keys_are_swept_on_the_next_publish():
    broker = _broker()
    idle = broker.subscribe()
    for i in range(500):
        broker.publish("download_progress", {"i": i},
                       throttle_key=f"k{i}", throttle_s=0.01)
    _drain(broker, idle, 0.05)  # let every 10 ms window close
    broker.publish("download_progress", {"i": "new"},
                   throttle_key="k-new", throttle_s=0.01)
    tracked = broker.get_status()["throttle_keys_tracked"]
    assert tracked == 1, (
        f"{DIAG} F4: 500 keys whose window closed + 1 new publish -> table "
        f"holds {tracked}, expected 1")


def test_f4_control_one_key_many_publishes_is_one_entry():
    broker = _broker()
    for i in range(10_000):
        broker.publish("download_progress", {"i": i}, throttle_key="one")
    assert broker.get_status()["throttle_keys_tracked"] == 1


def test_f4_evicted_key_sends_its_held_event(monkeypatch):
    from bulk_downloader import sse_broker
    # A small table keeps the flood under the 200-event subscriber queue.
    cap = 8
    monkeypatch.setattr(sse_broker, "THROTTLE_KEYS_MAX", cap, raising=False)
    broker = _broker()
    sub = broker.subscribe()
    broker.publish("download_progress", {"p": 97}, throttle_key="first",
                   throttle_s=60.0)
    broker.publish("download_progress", {"p": 100}, throttle_key="first",
                   throttle_s=60.0)
    for i in range(cap + 1):
        broker.publish("other", {"i": i}, throttle_key=f"o{i}",
                       throttle_s=60.0)
    got = [m for _t, m in _drain(broker, sub, 1.0, idle_s=0.2)
           if m.startswith("event: download_progress")]
    assert _values([(0, m) for m in got]) == [97, 100], (
        f"{DIAG} F4: key evicted while holding 100 -> download_progress "
        f"delivered {_values([(0, m) for m in got])}, expected [97, 100]")


# ── F5: trailing flush ────────────────────────────────────────────────

def test_f5_last_event_inside_the_window_is_delivered():
    broker = _broker()
    sub = broker.subscribe()
    t0 = time.time()
    broker.publish("download_progress", {"p": 97}, throttle_key="url")
    broker.publish("download_progress", {"p": 100}, throttle_key="url")
    msgs = _drain(broker, sub, 3.0, idle_s=1.6)
    got = _values(msgs)
    assert got == [97, 100], (
        f"{DIAG} F5: progress 97 then 100 within 1 s -> delivered {got}, "
        f"expected [97, 100]")
    # The throttle still holds: 100 arrives when the 1 s window closes.
    assert msgs[1][0] - t0 >= 0.9, (
        f"{DIAG} F5: trailing event sent {msgs[1][0] - t0:.3f}s after the "
        f"first, inside the 1 s window")


def test_f5_window_coalesces_to_the_newest():
    broker = _broker()
    sub = broker.subscribe()
    for p in (10, 11, 12, 13):
        broker.publish("download_progress", {"p": p}, throttle_key="url",
                       throttle_s=0.2)
    got = _values(_drain(broker, sub, 1.5, idle_s=0.5))
    assert got == [10, 13], (
        f"{DIAG} F5: 10,11,12,13 inside one window -> delivered {got}, "
        f"expected [10, 13]")


def test_f5_control_mid_window_publishes_are_still_throttled():
    broker = _broker()
    sub = broker.subscribe()
    broker.publish("download_progress", {"p": 1}, throttle_key="url",
                   throttle_s=10.0)
    broker.publish("download_progress", {"p": 2}, throttle_key="url",
                   throttle_s=10.0)
    assert _values(_drain(broker, sub, 0.5)) == [1]


def test_f5_held_event_is_a_snapshot_of_its_data():
    broker = _broker()
    sub = broker.subscribe()
    data = {"p": 50}
    broker.publish("download_progress", {"p": 1}, throttle_key="url",
                   throttle_s=0.2)
    broker.publish("download_progress", data, throttle_key="url",
                   throttle_s=0.2)
    data["p"] = 999
    got = _values(_drain(broker, sub, 1.5, idle_s=0.5))
    assert got == [1, 50], (
        f"{DIAG} F5: data mutated after a held publish -> delivered {got}, "
        f"expected [1, 50]")


def test_f5_window_restarts_after_the_trailing_send():
    broker = _broker()
    sub = broker.subscribe()
    broker.publish("download_progress", {"p": 1}, throttle_key="url",
                   throttle_s=0.3)
    broker.publish("download_progress", {"p": 2}, throttle_key="url",
                   throttle_s=0.3)
    first = _drain(broker, sub, 1.0, want=2)
    assert _values(first) == [1, 2], f"{DIAG} F5: got {_values(first)}"
    # Publish straight after the trailing send of 2.
    broker.publish("download_progress", {"p": 3}, throttle_key="url",
                   throttle_s=0.3)
    sent = _drain(broker, sub, 1.0, idle_s=0.45)
    assert _values(sent) == [3]
    # 3 was published right after 2 was sent, so it waited for a window.
    assert sent[0][0] - first[1][0] >= 0.25, (
        f"{DIAG} F5: event after a trailing send skipped the window")


# ── F9: per-client stream cap ─────────────────────────────────────────

def test_f9_acquire_caps_per_client_and_release_is_idempotent():
    broker = _broker()
    if not hasattr(broker, "acquire_stream"):
        pytest.fail(f"{DIAG} F9: broker has no per-client stream cap "
                    f"(acquire_stream missing)")
    cap = _cap("STREAMS_PER_CLIENT_MAX", 4)
    slots = [broker.acquire_stream("10.0.0.5") for _ in range(cap)]
    assert all(slots)
    assert broker.acquire_stream("10.0.0.5") is None
    assert broker.acquire_stream("10.0.0.6") is not None  # other client
    slots[0].release()
    slots[0].release()  # second release frees nothing more
    again = broker.acquire_stream("10.0.0.5")
    assert again is not None
    assert broker.acquire_stream("10.0.0.5") is None
    for s in slots[1:] + [again]:
        s.release()
    assert broker.get_status()["open_streams"] == 1  # 10.0.0.6 only


def _open_stream(fresh_app, addr):
    return fresh_app.get("/api/stream", buffered=False,
                         environ_base={"REMOTE_ADDR": addr})


def test_f9_route_refuses_streams_over_the_cap(fresh_app):
    # Each open test-client stream holds a pushed request context
    # (stream_with_context), so streams are closed newest first.
    cap = _cap("STREAMS_PER_CLIENT_MAX", 4)
    addr = "10.77.9.1"
    opened = []
    try:
        for _ in range(cap):
            r = _open_stream(fresh_app, addr)
            opened.append(r)
            assert r.status_code == 200
        extra = _open_stream(fresh_app, addr)
        extra.close()
        assert extra.status_code == 429, (
            f"{DIAG} F9: client {addr} with {cap} open /api/stream "
            f"connections opened one more -> {extra.status_code}, "
            f"expected 429 (each holds a worker)")
        assert extra.get_json()["limit"] == cap
        # Closing one stream frees its slot.
        opened.pop().close()
        again = _open_stream(fresh_app, addr)
        opened.append(again)
        assert again.status_code == 200, (
            f"{DIAG} F9: slot not released when the stream closed -> "
            f"{again.status_code}")
        # Another client is not affected by this one's cap.
        other = _open_stream(fresh_app, "10.77.9.2")
        opened.append(other)
        assert other.status_code == 200
    finally:
        while opened:
            opened.pop().close()


def test_f9_closed_streams_release_every_slot(fresh_app):
    from bulk_downloader import sse_broker
    addr = "10.77.9.3"
    cap = _cap("STREAMS_PER_CLIENT_MAX", 4)
    for _ in range(cap * 3):
        r = _open_stream(fresh_app, addr)
        try:
            assert r.status_code == 200, (
                f"{DIAG} F9: sequential open/close leaked slots -> "
                f"{r.status_code}")
        finally:
            r.close()
    streams = getattr(sse_broker.get_broker(), "_streams", {})
    assert addr not in streams
