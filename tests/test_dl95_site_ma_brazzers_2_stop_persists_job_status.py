"""dl95-site-ma-brazzers-2 (O1513 A5-A finding D2): after Stop the jobs stayed "running" in the queue table.

Measured on test2 (site-ma-brazzers/BZSTOP): Stop -> ok, site "stopped", .part frozen, yet
/api/sites/<sid>/queue/counts kept 2 jobs "running" for 90+ s. SiteRunner.stop() moved in-memory jobs to "stopped"
but never persisted them, so everything reading the queue table (counts, a restart's _restore_queue) still saw
"running". GREEN: stop() persists the transition like bulk_pause does.

Hermetic: fresh_app (clean workdir, in-memory sqlite); no browser is started -- the running jobs are claimed rows.
"""
from __future__ import annotations

import bulk_downloader.app as bd_app
from bulk_downloader.db import queue_count_by_status, queue_load, queue_upsert

BD_GATE_SCOPE = "module"

URLS = ["https://site-ma.brazzers.com/scene/11524413/a-rosie-day-for-keiran",
        "https://site-ma.brazzers.com/scene/11522551/miss-lexa-first-blowbang"]
WAITING = "https://site-ma.brazzers.com/scene/11520000/still-waiting"
DONE = "https://site-ma.brazzers.com/scene/11510000/already-done"


def _site_with_jobs():
    sid, err = bd_app._create_site({"name": "site-ma-brazzers", "login_url": "https://site-ma.brazzers.com/login"})
    assert err is None and sid
    runner = bd_app.runners[sid]
    for url, status in [(URLS[0], "running"), (URLS[1], "running"), (WAITING, "pending"), (DONE, "done")]:
        with runner._lock:
            runner.jobs[url] = {"status": status, "message": "", "ts": ""}
        queue_upsert(sid, url, status=status, message="")  # what the claim/_update_job wrote
    assert queue_count_by_status(sid) == {"running": 2, "pending": 1, "done": 1}
    return sid, runner


def test_stop_persists_stopped_so_counts_have_no_running_jobs(fresh_app):
    sid, runner = _site_with_jobs()
    runner.stop()
    assert {runner.jobs[u]["status"] for u in URLS} == {"stopped"}  # memory, as before
    body = fresh_app.get(f"/api/sites/{sid}/queue/counts").get_json()
    assert body["counts"] == {"stopped": 3, "done": 1}, body


def test_a_restart_after_stop_restores_stopped_not_recovered_running(fresh_app):
    sid, runner = _site_with_jobs()
    runner.stop()
    rows = {r["url"]: r for r in queue_load(sid)}
    assert {rows[u]["status"] for u in URLS + [WAITING]} == {"stopped"}
    assert rows[DONE]["status"] == "done"  # untouched
    assert all(rows[u]["message"] == "Stopped" for u in URLS)


def test_stop_with_nothing_in_flight_writes_nothing(fresh_app):
    sid, err = bd_app._create_site({"name": "idle", "login_url": "https://idle.example/login"})
    runner = bd_app.runners[sid]
    queue_upsert(sid, DONE, status="done", message="")
    with runner._lock:
        runner.jobs[DONE] = {"status": "done", "message": "", "ts": ""}
    runner.stop()
    assert queue_count_by_status(sid) == {"done": 1}
