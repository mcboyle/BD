"""Cut 8 (first write surface): recurring-capture schedules.

A new store `bulk_downloader/capture_schedules.py` mirroring the
scheduled_exports table pattern, plus CRUD routes:
    GET  /api/schedules                 -> list
    POST /api/schedules                 -> add (CSRF, validated, idempotent)
    POST /api/schedules/<id>/remove      -> delete (CSRF)
    POST /api/schedules/<id>/run_now     -> force-run one (CSRF)

The recurring task enqueues through the EXISTING run path via an injected
`enqueue_fn(site_id, urls) -> int` (the same dependency-injection seam
`discovery` uses). It NEVER touches capture / extraction internals.

§8.6 write discipline: input validation, CSRF gating, idempotency vs
double-submit, and a next_run guard against scheduler double-fire.

RED on pristine 378: the module + all four routes are absent.
"""
from __future__ import annotations

import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _fresh_db():
    from bulk_downloader.db import db_init
    db_init()


def _new_client():
    """Paired test client carrying a bd_session cookie + its CSRF token."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, csrf


# ── store: validation ─────────────────────────────────────────────────

def test_add_rejects_bad_params():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    assert cs.add_schedule(site_id="", cadence_hours=24) is None       # no site
    assert cs.add_schedule(site_id="s1", cadence_hours=0) is None      # bad cadence
    assert cs.add_schedule(site_id="s1", cadence_hours=-3) is None


def test_add_list_remove_roundtrip():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    rid = cs.add_schedule(site_id="siteA", cadence_hours=12, label="nightly")
    assert isinstance(rid, int)
    rows = cs.list_schedules()
    assert any(r["id"] == rid and r["site_id"] == "siteA" for r in rows)
    assert cs.remove_schedule(rid) is True
    assert all(r["id"] != rid for r in cs.list_schedules())
    assert cs.remove_schedule(rid) is False  # already gone


# ── store: idempotency vs double-submit ───────────────────────────────

def test_idempotent_add_dedupes():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    a = cs.add_schedule(site_id="dup", cadence_hours=6)
    b = cs.add_schedule(site_id="dup", cadence_hours=6)
    assert a == b  # identical add returns the existing id
    same = [r for r in cs.list_schedules() if r["site_id"] == "dup"]
    assert len(same) == 1  # no duplicate row


# ── store: run_due uses the injected enqueue seam, guards double-fire ──

def test_run_due_enqueues_via_injected_fn():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    cs.add_schedule(site_id="runme", cadence_hours=1)
    seen = []

    def _fake_enqueue(site_id, urls):
        seen.append(site_id)
        return 1

    out = cs.run_due(enqueue_fn=_fake_enqueue, now=time.time() + 7200)
    assert out["ran"] >= 1
    assert "runme" in seen


def test_run_due_next_run_guard_blocks_immediate_refire():
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    cs.add_schedule(site_id="once", cadence_hours=24)
    calls = {"n": 0}

    def _fake_enqueue(site_id, urls):
        calls["n"] += 1
        return 1

    t = time.time() + 100000
    cs.run_due(enqueue_fn=_fake_enqueue, now=t)
    cs.run_due(enqueue_fn=_fake_enqueue, now=t + 1)  # same instant-ish
    assert calls["n"] == 1  # second pass is not yet due again


def test_run_due_swallows_enqueue_errors():
    """A failing enqueue marks the row failed, never raises."""
    _fresh_db()
    from bulk_downloader import capture_schedules as cs
    cs.add_schedule(site_id="boom", cadence_hours=1)

    def _bad(site_id, urls):
        raise RuntimeError("enqueue blew up")

    out = cs.run_due(enqueue_fn=_bad, now=time.time() + 7200)
    assert isinstance(out, dict)  # returned, not raised


# ── seam guard: the store must not reach into capture/extraction ──────

def test_store_does_not_import_capture_or_extraction():
    src = (_REPO_ROOT / "bulk_downloader" / "capture_schedules.py").read_text()
    for forbidden in ("extraction_core", "session_capture", "dom_capture",
                      "dom_recorder", "capture_bodies", "capture_session"):
        assert forbidden not in src, f"must not couple to {forbidden}"


# ── routes ────────────────────────────────────────────────────────────

def test_get_schedules_is_200():
    _fresh_db()
    from bulk_downloader import app as A
    r = A.app.test_client().get("/api/schedules")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["ok"] is True
    assert isinstance(d["schedules"], list)


def test_post_add_requires_csrf():
    """A session-bearing client with NO csrf token is refused."""
    c, _csrf = _new_client()
    r = c.post("/api/schedules", json={"site_id": "x", "cadence_hours": 12})
    assert r.status_code == 403


def test_post_add_with_csrf_succeeds():
    c, csrf = _new_client()
    r = c.post("/api/schedules",
               json={"site_id": "viaapi", "cadence_hours": 8},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ok"] is True
    assert isinstance(r.get_json()["id"], int)


def test_post_add_bad_params_is_400():
    c, csrf = _new_client()
    r = c.post("/api/schedules",
               json={"site_id": "", "cadence_hours": 0},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_post_remove_and_run_now():
    c, csrf = _new_client()
    H = {"X-CSRF-Token": csrf}
    rid = c.post("/api/schedules",
                 json={"site_id": "rm", "cadence_hours": 5},
                 headers=H).get_json()["id"]
    rn = c.post(f"/api/schedules/{rid}/run_now", headers=H)
    assert rn.status_code == 200, rn.get_json()
    rm = c.post(f"/api/schedules/{rid}/remove", headers=H)
    assert rm.status_code == 200, rm.get_json()
    assert rm.get_json()["ok"] is True
