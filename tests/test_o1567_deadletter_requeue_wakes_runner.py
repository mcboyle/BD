"""O1567 fx-deadletter-requeue-runner: requeue must reach the live runner.

Live (wrk-191, o1513-a6-hustler1-live, evidence/hustler1-requeue-stale.json):
POST /api/queue/dead_letter/requeue returned ok and the DB row read "pending",
but the site's runner still held the job as dead_letter in memory. A start then
ran nothing (home "Idle - 0 queued") and retry_one answered "can't retry from
status 'dead_letter'" until the app was restarted and reloaded the DB.
"""
BD_GATE_SCOPE = "module"

import json
import os
import tempfile
import threading

import bulk_downloader.app as app_mod
from bulk_downloader import app_state, db


class _Runner:
    def __init__(self, jobs):
        self._lock = threading.RLock()
        self.jobs = jobs
        self.calls = []

    def _update_job(self, url, status, message="", **kw):
        self.calls.append((url, status, kw))
        self.jobs[url]["status"] = status


def test_requeue_moves_the_runner_job_to_pending():
    d = tempfile.mkdtemp()
    os.chdir(d)
    db.db_init()
    db.queue_upsert("s1", "http://x/dead", status="pending")
    db.db_queue_dead_letter("s1", "http://x/dead", "max retries exhausted")
    rn = _Runner({"http://x/dead": {"status": "dead_letter"}})
    saved = app_state.runners.get("s1")
    app_state.runners["s1"] = rn
    try:
        r = app_mod.app.test_client().post(
            "/api/queue/dead_letter/requeue",
            json={"site_id": "s1", "url": "http://x/dead"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert json.loads(r.data)["ok"] is True
        row = [x for x in db.queue_load("s1") if x["url"] == "http://x/dead"][0]
        assert row["status"] == "pending"  # positive control: DB side (pre-existing)
        assert rn.jobs["http://x/dead"]["status"] == "pending", (
            "runner job still dead_letter after requeue: a start will run nothing")
        assert rn.calls and rn.calls[-1][2].get("retries") == 0
    finally:
        if saved is None:
            app_state.runners.pop("s1", None)
        else:
            app_state.runners["s1"] = saved
