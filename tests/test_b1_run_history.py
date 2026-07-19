"""B1 (post-365) — run-history substrate.

Persists job lifecycle + outcomes via the existing db.py / db_conn() (NOT a new
DB file): two tables `job_runs` + `run_events`, written at the runner's existing
state transitions. The store is *advisory* — a history-write failure must never
propagate into the download path (covered in test_b1_run_history_advisory.py).

Surfaces:
  GET /api/runs                 -> {ok, runs:[...]}        (most-recent first)
  GET /api/runs/<id>/timeline   -> {ok, run:{...}, events:[...]}

Custom runner: zero-arg functions, no pytest builtins. DB-backed against the
per-run BD_HOME (db_init creates the base tables; run_history.init() adds ours).

RED-first: bulk_downloader.run_history does not exist yet, so the import + every
route 404s on pristine source.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _fresh():
    from bulk_downloader import db, run_history as rh
    db.db_init()
    rh.init()
    with db.db_conn() as cx:
        cx.execute("DELETE FROM run_events")
        cx.execute("DELETE FROM job_runs")


def test_init_is_idempotent():
    from bulk_downloader import run_history as rh
    # Two calls must not raise (CREATE TABLE IF NOT EXISTS pattern).
    rh.init()
    rh.init()


def test_record_start_event_finish_round_trip():
    from bulk_downloader import db, run_history as rh
    _fresh()
    rid = rh.record_run_start("siteA", "https://example.test/a")
    assert isinstance(rid, int) and rid > 0, rid
    rh.record_run_event(rid, "progress", "fetched manifest")
    rh.record_run_finish(rid, "done")
    with db.db_conn() as cx:
        run = cx.execute("SELECT * FROM job_runs WHERE id=?", (rid,)).fetchone()
        evs = cx.execute(
            "SELECT * FROM run_events WHERE run_id=? ORDER BY id", (rid,)
        ).fetchall()
    assert run["site_id"] == "siteA"
    assert run["url"] == "https://example.test/a"
    assert run["status"] == "done"
    assert run["finished_at"] is not None
    # start + progress + finish are all events on the run timeline.
    types = [e["event_type"] for e in evs]
    assert "progress" in types
    assert "finish" in types


def test_api_runs_lists_most_recent_first():
    from bulk_downloader import app as a, run_history as rh
    _fresh()
    r1 = rh.record_run_start("s1", "u1"); rh.record_run_finish(r1, "done")
    r2 = rh.record_run_start("s2", "u2"); rh.record_run_finish(r2, "failed")
    c = a.app.test_client()
    resp = c.get("/api/runs")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_json()
    assert body["ok"] is True
    ids = [run["id"] for run in body["runs"]]
    assert ids[0] == r2, "most-recent run must sort first"
    assert r1 in ids


def test_api_run_timeline_returns_run_plus_events():
    from bulk_downloader import app as a, run_history as rh
    _fresh()
    rid = rh.record_run_start("s", "u")
    rh.record_run_event(rid, "progress", "step 1")
    rh.record_run_finish(rid, "done")
    c = a.app.test_client()
    resp = c.get(f"/api/runs/{rid}/timeline")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_json()
    assert body["ok"] is True
    assert body["run"]["id"] == rid
    assert any(e["event_type"] == "progress" for e in body["events"])


def test_api_run_timeline_unknown_id_404s():
    from bulk_downloader import app as a, run_history as rh  # import gates on the module existing
    rh.init()
    c = a.app.test_client()
    resp = c.get("/api/runs/99999999/timeline")
    assert resp.status_code == 404, resp.status_code
    # Distinguish a route-present "unknown id" 404 from Flask's default
    # route-absent 404: ours returns the run-history JSON envelope.
    body = resp.get_json()
    assert body is not None and body.get("ok") is False, body
