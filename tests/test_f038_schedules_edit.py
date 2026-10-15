"""F038 (APP-FEATURE-QUEUE): a recurring-capture schedule can be EDITED.

The row's bar is "create TEST schedule, edit, delete". Create and delete
existed; edit did not (no store function, no route, no SPA control):
    POST /api/schedules/<id>/update   body {cadence_hours?, label?}

RED on fe1b4dc9: capture_schedules has no update_schedule and the route
404s. GREEN: cadence/label change in place, next_run re-timed from the
last run, CSRF-gated, 400 on bad params, 404 on an unknown id.

r2 (REFUTE cx99): F1 an edit may not give a site two enabled schedules at one
cadence (409, nothing written, one enabled row per cadence); F2 a fractional cadence
is 400 with nothing written, never truncated.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"


def _fresh_db():
    from bulk_downloader.db import db_init
    db_init()


def _new_client():
    """Paired test client carrying a bd_session cookie + its CSRF token."""
    from bulk_downloader import app as A
    _fresh_db()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, csrf


def _row(sid):
    from bulk_downloader import capture_schedules as cs
    return next(s for s in cs.list_schedules() if s["id"] == sid)


def test_store_update_changes_cadence_and_label():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    assert hasattr(cs, "update_schedule"), "F038-RED: no update_schedule in capture_schedules"
    rid = cs.add_schedule(site_id="f038-store", cadence_hours=24, label="before")
    assert cs.update_schedule(rid, cadence_hours=6, label="after") == {"status": "ok"}
    r = _row(rid)
    assert (r["cadence_hours"], r["label"]) == (6, "after"), f"F038: edit not applied: {r}"
    assert r["site_id"] == "f038-store"
    cs.remove_schedule(rid)


def test_store_update_retimes_next_run_from_last_run():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    rid = cs.add_schedule(site_id="f038-retime", cadence_hours=24)
    cs.run_one(rid, enqueue_fn=lambda site, urls: 0)
    last = _row(rid)["last_run_ts"]
    assert last > 0
    assert cs.update_schedule(rid, cadence_hours=2) == {"status": "ok"}
    r = _row(rid)
    assert abs(r["next_run_ts"] - (last + 2 * 3600)) < 1, f"F038: next_run not re-timed: {r}"
    cs.remove_schedule(rid)


def test_store_update_rejects_bad_params_and_unknown_id():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    rid = cs.add_schedule(site_id="f038-bad", cadence_hours=24, label="keep")
    for bad in (0, -3, "x", 1.5, "1.5", True, None):
        kw = {} if bad is None else {"cadence_hours": bad}
        assert cs.update_schedule(rid, **kw) == {"status": "invalid"}, f"F038: cadence {bad!r} accepted"
    assert cs.update_schedule(rid, cadence_hours=12.0) == {"status": "ok"}   # integral float is whole
    assert cs.update_schedule(rid, cadence_hours=24) == {"status": "ok"}
    assert (_row(rid)["cadence_hours"], _row(rid)["label"]) == (24, "keep")
    assert cs.update_schedule(10**9, label="nope") == {"status": "missing"}
    cs.remove_schedule(rid)


def test_post_update_requires_csrf():
    c, csrf = _new_client()
    rid = c.post("/api/schedules", json={"site_id": "f038-csrf", "cadence_hours": 9},
                 headers={"X-CSRF-Token": csrf}).get_json()["id"]
    r = c.post(f"/api/schedules/{rid}/update", json={"cadence_hours": 3})
    assert r.status_code == 403, f"F038: update not CSRF-gated ({r.status_code})"
    assert _row(rid)["cadence_hours"] == 9, "F038: a CSRF-refused update still wrote"
    # Positive control: the same request WITH the token is accepted, so the 403
    # above is the gate on a live route, not a refusal of a missing one.
    ok = c.post(f"/api/schedules/{rid}/update", json={"cadence_hours": 3},
                headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200, f"F038-RED: update route answered {ok.status_code}"
    c.post(f"/api/schedules/{rid}/remove", headers={"X-CSRF-Token": csrf})


def test_post_create_edit_delete_roundtrip():
    """The row's bar: create TEST schedule, edit, delete -- over the API."""
    c, csrf = _new_client()
    H = {"X-CSRF-Token": csrf}
    rid = c.post("/api/schedules", json={"site_id": "f038-api", "cadence_hours": 720,
                                         "label": "TEST-f038"}, headers=H).get_json()["id"]
    r = c.post(f"/api/schedules/{rid}/update",
               json={"cadence_hours": 48, "label": "TEST-f038-edited"}, headers=H)
    assert r.status_code == 200, f"F038-RED: update route answered {r.status_code}"
    assert r.get_json() == {"ok": True, "id": rid}
    got = [s for s in c.get("/api/schedules").get_json()["schedules"] if s["id"] == rid]
    assert got and (got[0]["cadence_hours"], got[0]["label"]) == (48, "TEST-f038-edited"), got
    bad = c.post(f"/api/schedules/{rid}/update", json={"cadence_hours": 0}, headers=H)
    assert bad.status_code == 400
    assert c.post(f"/api/schedules/{rid}/remove", headers=H).get_json()["ok"] is True
    gone = c.post(f"/api/schedules/{rid}/update", json={"label": "x"}, headers=H)
    assert gone.status_code == 404


def test_r2_f1_edit_into_duplicate_cadence_is_refused_without_write():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    a = cs.add_schedule(site_id="f038-dup", cadence_hours=24, label="a")
    b = cs.add_schedule(site_id="f038-dup", cadence_hours=48, label="b")
    res = cs.update_schedule(b, cadence_hours=24, label="b2")
    assert res == {"status": "duplicate", "existing_id": a}, f"F038-R2-F1: duplicate edit not refused: {res}"
    assert (_row(b)["cadence_hours"], _row(b)["label"]) == (48, "b"), "F038-R2-F1: refused edit still wrote"
    enabled_24 = [s for s in cs.list_schedules()
                  if s["site_id"] == "f038-dup" and s["cadence_hours"] == 24 and s["enabled"]]
    assert len(enabled_24) == 1, f"F038-R2-F1: two enabled 24h rows: {enabled_24}"
    # A label-only edit and an edit to the row's own cadence are not collisions.
    assert cs.update_schedule(b, label="b3") == {"status": "ok"}
    assert cs.update_schedule(a, cadence_hours=24) == {"status": "ok"}
    # A disabled row never fires, so it does not block (and is not blocked).
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("UPDATE capture_schedules SET enabled = 0 WHERE id = ?", (a,))
    assert cs.update_schedule(b, cadence_hours=24) == {"status": "ok"}
    cs.remove_schedule(a); cs.remove_schedule(b)


def test_r2_api_duplicate_409_and_fractional_400_write_nothing():
    c, csrf = _new_client()
    H = {"X-CSRF-Token": csrf}
    a = c.post("/api/schedules", json={"site_id": "f038-api2", "cadence_hours": 24}, headers=H).get_json()["id"]
    b = c.post("/api/schedules", json={"site_id": "f038-api2", "cadence_hours": 6}, headers=H).get_json()["id"]
    dup = c.post(f"/api/schedules/{b}/update", json={"cadence_hours": 24}, headers=H)
    assert dup.status_code == 409, f"F038-R2-F1: duplicate edit answered {dup.status_code}"
    assert dup.get_json()["existing_id"] == a
    frac = c.post(f"/api/schedules/{b}/update", json={"cadence_hours": 1.5}, headers=H)
    assert frac.status_code == 400, f"F038-R2-F2: fractional cadence answered {frac.status_code}"
    rows = {s["id"]: s["cadence_hours"] for s in c.get("/api/schedules").get_json()["schedules"]}
    assert rows[b] == 6, f"F038-R2: refused edits wrote: {rows}"
    ok = c.post(f"/api/schedules/{b}/update", json={"cadence_hours": 12}, headers=H)
    assert ok.status_code == 200      # control: a whole, non-colliding cadence is accepted
    for sid in (a, b):
        c.post(f"/api/schedules/{sid}/remove", headers=H)
