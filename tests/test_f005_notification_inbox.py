"""F005: the /notifications page has a notification inbox.

APP-FEATURE-QUEUE row F005 ("list loads; mark one read/unread; filter") FAILED
on the live app: /notifications rendered only channel configuration -- zero
list items, zero read/unread controls, zero filters. Every FIRED alert event is
now a notification:

    GET  /api/notifications/inbox?status=all|unread|read&severity=all|info|warn|fail
    POST /api/notifications/inbox/<id>/read   {"read": true|false}   (CSRF)

RED at BASE fe1b4dc9: neither route exists (GET 404, POST 404) and
alerts_engine has no notification_inbox / set_notification_read.
"""
from __future__ import annotations

# An ordinary module test: its subject is the inbox API, not the tree.
BD_GATE_SCOPE = "module"


def _client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, csrf


def _seed():
    """Three fired events (info, warn, fail) and one non-fired event, which
    the inbox must NOT list. Returns {rule_id: event_id} for the fired ones."""
    from bulk_downloader import alerts_engine as ae
    from bulk_downloader import db as _db
    ae._record_event("queue_backlog", "fired", 600.0, "600 pending")
    ae._record_event("high_failure_rate", "fired", 40.0, "40% failed")
    ae._record_event("disk_low", "fired", 2.0, "2 GB free")
    ae._record_event("disk_low", "tripping", 2.0, "not a notification")
    with _db.db_conn() as cx:
        rows = cx.execute("SELECT id, rule_id FROM alert_events "
                          "WHERE kind = 'fired'").fetchall()
    ids = {r[1]: r[0] for r in rows}
    # PRECONDITION: the fixture built exactly the shape the assertions read.
    assert sorted(ids) == ["disk_low", "high_failure_rate", "queue_backlog"], ids
    return ids


def _inbox(c, query=""):
    r = c.get("/api/notifications/inbox" + query)
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:200])
    return r.get_json()


def _mark(c, csrf, event_id, read):
    return c.post(f"/api/notifications/inbox/{event_id}/read",
                  json={"read": read}, headers={"X-CSRF-Token": csrf})


def test_list_loads_fired_alerts_newest_first():
    c, _csrf = _client()
    ids = _seed()
    body = _inbox(c)
    got = [(n["rule_id"], n["severity"], n["read"]) for n in body["notifications"]]
    assert got == [("disk_low", "fail", False),
                   ("high_failure_rate", "warn", False),
                   ("queue_backlog", "info", False)]
    assert (body["total"], body["unread"]) == (3, 3)
    assert body["notifications"][0]["id"] == ids["disk_low"]
    assert body["notifications"][0]["name"] == "Disk space low"
    assert body["notifications"][0]["message"] == "2 GB free"


def test_mark_one_read_then_unread():
    c, csrf = _client()
    ids = _seed()
    target = ids["high_failure_rate"]
    r = _mark(c, csrf, target, True)
    assert r.status_code == 200 and r.get_json() == {"ok": True, "id": target, "read": True}
    body = _inbox(c)
    assert (body["total"], body["unread"]) == (3, 2)
    assert {n["id"]: n["read"] for n in body["notifications"]} == {
        ids["disk_low"]: False, target: True, ids["queue_backlog"]: False}
    first_read_at = next(n["read_at"] for n in body["notifications"] if n["id"] == target)
    assert first_read_at is not None
    # Idempotent: a second mark-read keeps the first read_at.
    assert _mark(c, csrf, target, True).status_code == 200
    again = next(n for n in _inbox(c)["notifications"] if n["id"] == target)
    assert again["read_at"] == first_read_at

    r = _mark(c, csrf, target, False)
    assert r.status_code == 200 and r.get_json()["read"] is False
    body = _inbox(c)
    assert body["unread"] == 3
    assert all(n["read"] is False and n["read_at"] is None for n in body["notifications"])


def test_filter_by_status_and_severity():
    c, csrf = _client()
    ids = _seed()
    assert _mark(c, csrf, ids["queue_backlog"], True).status_code == 200

    unread = _inbox(c, "?status=unread")
    assert [n["rule_id"] for n in unread["notifications"]] == ["disk_low", "high_failure_rate"]
    read = _inbox(c, "?status=read")
    assert [n["rule_id"] for n in read["notifications"]] == ["queue_backlog"]
    fail = _inbox(c, "?severity=fail")
    assert [n["rule_id"] for n in fail["notifications"]] == ["disk_low"]
    assert _inbox(c, "?status=read&severity=fail")["notifications"] == []
    # The counts are whole-inbox, not the filtered page.
    assert (fail["total"], fail["unread"]) == (3, 2)


def test_refusals_carry_distinct_diagnostics():
    c, csrf = _client()
    ids = _seed()
    # Unknown filter -> 400 naming the parameter, never an empty list.
    r = c.get("/api/notifications/inbox?status=bogus")
    assert r.status_code == 400 and "status must be one of" in r.get_json()["error"]
    r = c.get("/api/notifications/inbox?severity=loud")
    assert r.status_code == 400 and "severity must be one of" in r.get_json()["error"]
    # No such fired event (the non-fired 'tripping' row included) -> 404.
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        tripping = cx.execute("SELECT id FROM alert_events WHERE kind = 'tripping'").fetchone()[0]
    for missing in (tripping, max(ids.values()) + 1000):
        r = _mark(c, csrf, missing, True)
        assert r.status_code == 404, r.status_code
        assert r.get_json()["error"] == f"no notification {missing}"
    # Body must carry a boolean.
    r = c.post(f"/api/notifications/inbox/{ids['disk_low']}/read",
               json={"read": "yes"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400 and "read: true|false" in r.get_json()["error"]
    # CSRF-gated write.
    r = c.post(f"/api/notifications/inbox/{ids['disk_low']}/read", json={"read": True})
    assert r.status_code == 403
    assert _inbox(c)["unread"] == 3  # none of the refusals wrote


def test_unreadable_store_is_500_not_empty(monkeypatch):
    c, _csrf = _client()
    _seed()
    from bulk_downloader import db as _db

    def _boom(*_a, **_k):
        raise RuntimeError("store locked")

    monkeypatch.setattr(_db, "db_conn", _boom)
    r = c.get("/api/notifications/inbox")
    assert r.status_code == 500
    assert "notification inbox unreadable" in r.get_json()["error"]
