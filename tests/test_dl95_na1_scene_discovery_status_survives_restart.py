"""dl95-naughtyamerica-1: a scene-discovery run cut by a restart must not read RUNNING forever.

Measured on test2 (harness-work/UIUX-20260928/download-95/A5-A/BLOCKER-restarts.md,
_discovery/sd1/SD1__NA.png): discovery for naughtyamerica started 23:16Z; the service
restarted under it, and the DOM analyzer still showed "Discovering scenes..." with the
button disabled. The run row in scene_crawl_runs stays RUNNING because the thread that
would have finished it died with the process, and crawl_status reads the row verbatim.

Contract pinned here: a RUNNING row that no live crawl in this process owns is reported
-- and persisted -- as CANCELLED with a restart reason; a RUNNING row whose crawl is live
stays RUNNING.
"""
from __future__ import annotations

# An ordinary module test: its subject is the module under test, not the tree.
BD_GATE_SCOPE = "module"

import importlib
import sqlite3


def _crawler():
    return importlib.import_module("bulk_downloader.scene_crawler")


def _seed_running(sc, db_path, run_id="orphan-run", site_id="na"):
    sc._create_run(run_id, site_id, "https://na.example/members/videos", db_path)
    return run_id


def _row_state(db_path, run_id):
    with sqlite3.connect(db_path) as cx:
        return cx.execute(
            "SELECT state, finished_at, error FROM scene_crawl_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()


def test_orphaned_running_run_reads_cancelled(tmp_path):
    sc = _crawler()
    db_path = str(tmp_path / "sd.sqlite")
    run_id = _seed_running(sc, db_path)
    # The process that started it is gone: nothing in _ACTIVE owns the run.
    assert sc._ACTIVE.get("na") is None

    status = sc.crawl_status(site_id="na", db_path=db_path)
    assert status["state"] != "RUNNING", (
        "DL95-NA1: scene-discovery run orphaned by a restart still reads RUNNING")
    assert status["state"] == "CANCELLED"
    assert "restart" in status["error"].lower()
    assert status["finished_at"]

    state, finished_at, error = _row_state(db_path, run_id)
    assert state == "CANCELLED" and finished_at and "restart" in error.lower(), (
        "DL95-NA1: the terminal state was reported but not persisted")
    # The run_id lookup path agrees.
    assert sc.crawl_status(site_id="na", run_id=run_id, db_path=db_path)["state"] == "CANCELLED"


def test_live_running_run_stays_running(tmp_path, monkeypatch):
    """Control: a run a live crawl thread owns is untouched."""
    sc = _crawler()
    db_path = str(tmp_path / "sd.sqlite")
    run_id = _seed_running(sc, db_path, run_id="live-run")
    monkeypatch.setitem(sc._ACTIVE, "na", run_id)

    status = sc.crawl_status(site_id="na", db_path=db_path)
    assert status["state"] == "RUNNING"
    assert _row_state(db_path, run_id)[0] == "RUNNING"


def test_finished_runs_are_not_rewritten(tmp_path):
    """Control: a terminal row keeps its own state and result."""
    sc = _crawler()
    db_path = str(tmp_path / "sd.sqlite")
    run_id = _seed_running(sc, db_path, run_id="done-run")
    sc._finish_run(run_id, {"state": "COMPLETED", "discovered": 3}, db_path)

    status = sc.crawl_status(site_id="na", db_path=db_path)
    assert status["state"] == "COMPLETED"
    assert status["discovered"] == 3


def test_stale_running_snapshot_never_overwrites_a_terminal_write(tmp_path):
    """Race: crawl_status read the row RUNNING, then the crawl wrote its terminal
    state and left _ACTIVE before the orphan check ran. The UPDATE must be a no-op
    (guarded on RUNNING) and the reply must be the terminal row, not CANCELLED."""
    sc = _crawler()
    db_path = str(tmp_path / "sd.sqlite")
    run_id = _seed_running(sc, db_path, run_id="raced-run")
    with sc.db.db_conn(db_path) as cx:
        stale = cx.execute(
            "SELECT * FROM scene_crawl_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert stale["state"] == "RUNNING"
    sc._finish_run(run_id, {"state": "COMPLETED", "discovered": 3}, db_path)
    assert sc._ACTIVE.get("na") is None

    row = sc._cancel_orphaned_run(stale, db_path)
    assert row["state"] == "COMPLETED", (
        "DL95-NA1: the orphan check overwrote a crawl's terminal write"
    )
    assert _row_state(db_path, run_id)[0] == "COMPLETED"
