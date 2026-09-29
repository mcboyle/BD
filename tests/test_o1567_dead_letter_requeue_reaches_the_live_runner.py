"""O1567 fx-dead-letter-requeue-live: a dead-letter requeue must reach the live runner, not only the DB row.

MEASURED on test4 (10.0.70.85) 2026-09-29: POST /api/queue/dead_letter/requeue {site_id, url} answered ok:true
and GET /api/sites/<sid>/queue showed the row "pending / requeued from dead-letter" (site-ma-bangbros 21:34Z,
site-ma-brazzers 21:57Z), but POST /api/sites/<sid>/start ran nothing ("Idle . 0 queued", no journal line for the
site) until the service was restarted and the queue was re-read from the DB.

Cause: the route only runs db_queue_requeue_dead_letter; the site's live SiteRunner keeps the job in its
in-memory ``jobs`` as ``dead_letter`` (with its spent retries), and Start works from that map.

Rule: when the site has a live runner, the requeue also moves the runner's job back to pending with retries and
backoff cleared -- the same write /api/sites/<sid>/retry_one makes.
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import bulk_downloader.app as app_mod  # noqa: E402
from bulk_downloader import app_state, db  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

BD_GATE_SCOPE = "module"

SID = "o1567dlq"
DEAD = "https://site-ma.example.com/scene/11524249/"


def _seed():
    d = tempfile.mkdtemp()
    os.chdir(d)
    db.db_init()
    db.queue_upsert(SID, DEAD, status="pending")
    db.db_queue_dead_letter(SID, DEAD, "auth-required, retries exhausted")
    r = SiteRunner(SID, {"name": "o1567dlq", "max_retries": 2})
    r._update_job(DEAD, "dead_letter", "Session expired -- re-login retries exhausted", retries=2)
    return r


def _requeue(runner):
    app_state.runners[SID] = runner
    try:
        return app_mod.app.test_client().post(
            "/api/queue/dead_letter/requeue", json={"site_id": SID, "url": DEAD})
    finally:
        app_state.runners.pop(SID, None)


def test_requeue_moves_the_live_runner_job_back_to_pending():
    runner = _seed()
    resp = _requeue(runner)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert json.loads(resp.data)["ok"] is True
    job = runner.jobs[DEAD]
    assert job["status"] == "pending", (
        f"O1567 DLQ: DB row requeued but the live runner still holds the job {job['status']!r}; "
        "Start runs nothing until a restart")
    assert job.get("retries", 0) == 0, f"O1567 DLQ: requeued job kept its spent retries ({job.get('retries')})"


def test_control_db_row_is_requeued_and_a_site_without_a_runner_still_works():
    _seed()
    resp = app_mod.app.test_client().post(
        "/api/queue/dead_letter/requeue", json={"site_id": SID, "url": DEAD})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    row = [x for x in db.queue_load(SID) if x["url"] == DEAD][0]
    assert row["status"] == "pending"
