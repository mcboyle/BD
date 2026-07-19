"""U30 — runner fleet console (D-19, read-only) + job replay (D-18,
an (A) action).

Scope: per-site pause/resume already exists (/api/sites/<sid>/pause
etc.) — U30 adds the read-only console half and a single-URL replay
that delegates to batch_ops.bulk_retry. It does NOT re-implement
pause/resume.
"""
from bulk_downloader import db
from bulk_downloader import dev_suite as ds


# ── runner_console (D-19) ──────────────────────────────────────────

def test_runner_console_empty_fleet(clean_workdir):
    # in test mode the runners dict is empty
    r = ds.runner_console()
    assert r["ok"] is True
    assert r["runner_count"] == 0
    assert r["runners"] == []
    # the console points at the existing action routes, not a new one
    assert "/api/sites/<sid>/pause" in r["note"]


def test_runner_console_reports_fleet(clean_workdir, monkeypatch):
    from bulk_downloader import app as bd_app

    class _FakeRunner:
        def __init__(self, state, jobs):
            self._state = state
            self.jobs = jobs
            self._worker_threads = [object(), object()]

    fake = {
        "site_a": _FakeRunner("running", {
            "u1": {"status": "done"}, "u2": {"status": "pending"}}),
        "site_b": _FakeRunner("paused", {"u3": {"status": "stopped"}}),
    }
    monkeypatch.setattr(bd_app, "runners", fake)
    r = ds.runner_console()
    assert r["runner_count"] == 2
    assert r["paused_runners"] == 1
    by_id = {x["site_id"]: x for x in r["runners"]}
    assert by_id["site_a"]["state"] == "running"
    assert by_id["site_a"]["worker_threads"] == 2
    assert by_id["site_a"]["job_status_counts"]["done"] == 1
    assert by_id["site_b"]["paused"] is True


# ── job_replay (D-18) ──────────────────────────────────────────────

def _make_history_row(url, status="failed"):
    db.db_log("s1", "Site One", url, status, message="boom")
    with db.db_conn() as cx:
        return cx.execute("SELECT id FROM history WHERE url=?",
                          (url,)).fetchone()[0]


def test_job_replay_requires_an_id(clean_workdir):
    db.db_init()
    assert ds.job_replay()["ok"] is False
    assert ds.job_replay(history_id="not-an-int")["ok"] is False


def test_job_replay_rejects_unknown_id(clean_workdir):
    db.db_init()
    r = ds.job_replay(history_id=424242)
    assert r["ok"] is False
    assert "no history row" in r["error"]


def test_job_replay_dry_run_changes_nothing(clean_workdir):
    db.db_init()
    hid = _make_history_row("https://example.com/dry")
    r = ds.job_replay(history_id=hid, dry_run=True)
    assert r["ok"] is True
    assert r["dry_run"] is True
    assert r["matched"] == 1
    # the row is untouched
    with db.db_conn() as cx:
        st = cx.execute("SELECT status FROM history WHERE id=?",
                        (hid,)).fetchone()[0]
    assert st == "failed"


def test_job_replay_real_requeues_the_row(clean_workdir):
    db.db_init()
    hid = _make_history_row("https://example.com/real")
    r = ds.job_replay(history_id=hid, dry_run=False)
    assert r["ok"] is True
    assert r["dry_run"] is False
    assert r["processed"] == 1
    # the row is reset to pending so a worker re-picks it
    with db.db_conn() as cx:
        st = cx.execute("SELECT status FROM history WHERE id=?",
                        (hid,)).fetchone()[0]
    assert st == "pending"


def test_job_replay_string_dry_run_flag_coerced(clean_workdir):
    db.db_init()
    hid = _make_history_row("https://example.com/coerce")
    # a JSON body could send "false" as a string
    r = ds.job_replay(history_id=hid, dry_run="false")
    assert r["dry_run"] is False
    with db.db_conn() as cx:
        st = cx.execute("SELECT status FROM history WHERE id=?",
                        (hid,)).fetchone()[0]
    assert st == "pending"


# ── routes ─────────────────────────────────────────────────────────

def test_runner_console_route(fresh_app):
    body = fresh_app.get("/api/dev/runner_console").get_json()
    assert body["ok"] is True
    assert "runners" in body


def test_job_replay_route_defaults_to_dry_run(fresh_app):
    db.db_log("s1", "Site One", "https://example.com/route",
              "failed", message="x")
    with db.db_conn() as cx:
        hid = cx.execute(
            "SELECT id FROM history WHERE url=?",
            ("https://example.com/route",)).fetchone()[0]
    resp = fresh_app.post("/api/dev/job_replay",
                          json={"history_id": hid})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["dry_run"] is True
