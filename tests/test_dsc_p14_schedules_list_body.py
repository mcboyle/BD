"""dsc-p14 (deep-scheduler-cron P14): a JSON body that is not an object.

POST /api/schedules and POST /api/schedules/<id>/update read
`body = request.json or {}` and then call body.get(...): a list (or a bare
string / number) body raised AttributeError inside the try and answered 500.
A malformed body is a client error: 400, and nothing is stored or changed.
pc_* tests are positive controls on the same routes.
"""
from __future__ import annotations

# An ordinary module test: its subject is the schedules routes, not the tree.
BD_GATE_SCOPE = "module"

DIAG = "BD-DSC-P14-NON-OBJECT-BODY"

_NON_OBJECTS = ([{"site_id": "p14-list", "cadence_hours": 6}], ["site_id"], "p14-text", 7)


def _new_client():
    """Paired test client carrying a bd_session cookie + its CSRF token."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, csrf


def _schedules():
    from bulk_downloader import capture_schedules as cs
    return cs.list_schedules()


def test_add_with_non_object_body_is_400():
    c, csrf = _new_client()
    before = _schedules()
    got = []
    for body in _NON_OBJECTS:
        r = c.post("/api/schedules", json=body, headers={"X-CSRF-Token": csrf})
        got.append((body, r.status_code, (r.get_json() or {}).get("error", "")[:80]))
    assert all(code == 400 for _, code, _ in got), f"{DIAG} add: {got}"
    assert _schedules() == before, "a rejected body must not store a schedule"


def test_update_with_non_object_body_is_400():
    c, csrf = _new_client()
    rid = c.post("/api/schedules", json={"site_id": "p14-upd", "cadence_hours": 5},
                 headers={"X-CSRF-Token": csrf}).get_json()["id"]
    before = _schedules()
    got = []
    for body in _NON_OBJECTS:
        r = c.post(f"/api/schedules/{rid}/update", json=body, headers={"X-CSRF-Token": csrf})
        got.append((body, r.status_code, (r.get_json() or {}).get("error", "")[:80]))
    assert all(code == 400 for _, code, _ in got), f"{DIAG} update: {got}"
    assert _schedules() == before, "a rejected body must not change a schedule"


def test_pc_object_bodies_still_work():
    c, csrf = _new_client()
    r = c.post("/api/schedules", json={"site_id": "p14-pc", "cadence_hours": 8, "label": "pc"},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    rid = r.get_json()["id"]
    u = c.post(f"/api/schedules/{rid}/update", json={"label": "pc2"}, headers={"X-CSRF-Token": csrf})
    assert u.status_code == 200 and u.get_json() == {"ok": True, "id": rid}
    assert [s["label"] for s in _schedules() if s["id"] == rid] == ["pc2"]
    # an empty or bad object is still the existing 400, not a new path
    assert c.post("/api/schedules", json={}, headers={"X-CSRF-Token": csrf}).status_code == 400
    assert c.post("/api/schedules", json={"site_id": "p14-pc", "cadence_hours": 0},
                  headers={"X-CSRF-Token": csrf}).status_code == 400
