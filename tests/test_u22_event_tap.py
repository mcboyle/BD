"""U22 — event-broker tap (D-65) + SSE-event tap UI (D-105).

Covers the dev_events ring buffer, the event_tap tool, the broker ->
tap wiring (the one fail-safe line added to sse_broker.publish), and
both routes. The D-105 page's JS is syntax-checked at build time.
"""
from bulk_downloader import dev_events, sse_broker
from bulk_downloader import dev_suite as ds


# ── dev_events ring buffer ─────────────────────────────────────────

def test_record_and_recent_newest_first():
    dev_events.reset()
    dev_events.record("a", "{}", 1)
    dev_events.record("b", "{}", 2)
    items = dev_events.recent(10)
    assert [e["event_type"] for e in items] == ["b", "a"]
    assert items[0]["seq"] == 2


def test_recent_honours_limit():
    dev_events.reset()
    for i in range(20):
        dev_events.record("e", "{}", 0)
    assert len(dev_events.recent(5)) == 5


def test_buffer_is_bounded_by_maxlen():
    dev_events.reset()
    for i in range(dev_events._MAX_EVENTS + 80):
        dev_events.record("x", "{}", 0)
    s = dev_events.stats()
    assert s["buffered"] == dev_events._MAX_EVENTS
    # lifetime counter keeps the true total even after eviction
    assert s["total_recorded"] == dev_events._MAX_EVENTS + 80


def test_payload_is_truncated():
    dev_events.reset()
    dev_events.record("big", "z" * 5000, 0)
    payload = dev_events.recent(1)[0]["payload"]
    assert len(payload) < 5000
    assert "truncated" in payload


def test_record_never_raises_on_bad_input():
    dev_events.reset()
    # non-str event_type / payload / recipients must not blow up
    dev_events.record(None, None, "not-an-int")
    assert dev_events.stats()["buffered"] == 1


def test_reset_clears_buffer_and_counter():
    dev_events.record("e", "{}", 0)
    dev_events.reset()
    s = dev_events.stats()
    assert s["buffered"] == 0 and s["total_recorded"] == 0


# ── event_tap tool (D-65) ──────────────────────────────────────────

def test_event_tap_returns_recent_events():
    dev_events.reset()
    dev_events.record("status", '{"k":1}', 3)
    r = ds.event_tap(limit=10)
    assert r["ok"] is True
    assert r["returned"] == 1
    assert r["events"][0]["event_type"] == "status"
    assert r["events"][0]["recipients"] == 3


def test_event_tap_clamps_limit():
    dev_events.reset()
    # absurd / non-numeric limits must not raise
    assert ds.event_tap(limit=999999)["ok"] is True
    assert ds.event_tap(limit="garbage")["ok"] is True


# ── broker -> tap wiring ───────────────────────────────────────────

def test_publish_is_captured_by_the_tap():
    dev_events.reset()
    sse_broker.publish("teststatus", {"job": 7, "state": "running"})
    events = dev_events.recent(5)
    assert any(e["event_type"] == "teststatus" for e in events)
    tapped = [e for e in events if e["event_type"] == "teststatus"][0]
    assert "running" in tapped["payload"]


def test_throttled_publish_is_not_tapped():
    dev_events.reset()
    sse_broker.publish("progress", {"p": 1}, throttle_key="job9")
    # second publish on the same key inside the window is dropped
    sse_broker.publish("progress", {"p": 2}, throttle_key="job9")
    assert dev_events.stats()["total_recorded"] == 1


def test_non_serializable_publish_does_not_break_tap():
    dev_events.reset()
    # A circular reference is one of the few things json.dumps rejects
    # even with default=str. The broker returns early before building
    # the message, so the tap must record nothing (no half-event).
    circular = {}
    circular["self"] = circular
    sse_broker.publish("weird", circular)
    assert dev_events.stats()["total_recorded"] == 0


# ── routes ─────────────────────────────────────────────────────────

def test_event_tap_route_returns_json(fresh_app):
    dev_events.reset()
    dev_events.record("routed", "{}", 0)
    resp = fresh_app.get("/api/dev/event_tap?limit=5")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert any(e["event_type"] == "routed" for e in body["events"])


def test_event_tap_ui_route_serves_html(fresh_app):
    resp = fresh_app.get("/api/dev/event_tap_ui")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "<!DOCTYPE html>" in text
    # it polls the D-65 endpoint and is self-contained (no app.js)
    assert "/api/dev/event_tap" in text
    assert "app.js" not in text
