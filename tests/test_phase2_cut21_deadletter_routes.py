"""Phase 2 Cut 2.1 route surface: dead-letter list + requeue endpoints.

GET  /api/queue/dead_letter          -> list dead-lettered jobs (value-free)
POST /api/queue/dead_letter/requeue  -> {site_id, url} requeue one back to pending

Bare test client (no bd_session cookie -> _check_csrf no-session bypass), same as
the other queue route tests.

RED on 616: routes do not exist (404).
"""
import json
import os
import tempfile

import bulk_downloader.app as app_mod
from bulk_downloader import db


def _client():
    return app_mod.app.test_client()


def _seed_dead_letter():
    # isolate the DB in a temp cwd, seed a dead-lettered + a live job
    d = tempfile.mkdtemp()
    os.chdir(d)
    db.db_init()
    db.queue_upsert("s1", "http://x/live", status="pending")
    db.queue_upsert("s1", "http://x/dead", status="pending")
    db.db_queue_dead_letter("s1", "http://x/dead", "max retries exhausted")


def test_dead_letter_list_route():
    _seed_dead_letter()
    r = _client().get("/api/queue/dead_letter")
    assert r.status_code == 200, r.get_data(as_text=True)
    b = json.loads(r.data)
    assert b["ok"] is True
    urls = {row["url"] for row in b["jobs"]}
    assert "http://x/dead" in urls, f"dead job not listed: {urls}"
    assert "http://x/live" not in urls, "a live job leaked into the dead-letter list"


def test_dead_letter_requeue_route():
    _seed_dead_letter()
    r = _client().post("/api/queue/dead_letter/requeue",
                       json={"site_id": "s1", "url": "http://x/dead"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert json.loads(r.data)["ok"] is True
    row = [x for x in db.queue_load("s1") if x["url"] == "http://x/dead"][0]
    assert row["status"] == "pending", f"requeue did not reset status: {row['status']}"


def test_dead_letter_requeue_missing_fields_400():
    _seed_dead_letter()
    r = _client().post("/api/queue/dead_letter/requeue", json={})
    assert r.status_code == 400
