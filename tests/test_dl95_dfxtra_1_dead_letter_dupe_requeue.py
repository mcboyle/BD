"""dl95-dfxtra-1 (DOT95 LIVE gap on dl95-dailymotion-6, LIVE-RESULT-A1-A.md#GAP): a dead_letter dupe was not offered.

Measured on test2: dfxtra livecam/autologin -- History "failed", queue status "dead_letter" (re-login retries
exhausted) -> add_url said "0 added . 1 dupe", retryable_dupes [], no Requeue (dm6_dl_2_after_add.png), because
BULK_RETRY_STATUSES was (failed, needs_review). GREEN: dead_letter is retryable -- named in retryable_dupes by both
enqueue routes, and bulk_retry re-queues it with its retry budget cleared, in memory and in the queue table.
Real Flask test client on the isolated BD_HOME the conftest autouse fixture provides; no network.
"""
import sys

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

DEAD = "https://members.dfxtra.com/livecam/autologin?modelName=SabrinaKole"
DONE = "https://members.dfxtra.com/livecam/autologin?modelName=BellaCherry"


@pytest.fixture(scope="module", autouse=True)
def _restore_bd_modules_after_file():
    """Put the bulk_downloader module table back when this file finishes (1034 leaker census)."""
    saved = {m: mod for m, mod in sys.modules.items() if m == "bulk_downloader" or m.startswith("bulk_downloader.")}
    try:
        yield
    finally:
        for m in [m for m in sys.modules if m == "bulk_downloader" or m.startswith("bulk_downloader.")]:
            del sys.modules[m]
        sys.modules.update(saved)


def _setup():
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    from bulk_downloader import app as a
    from bulk_downloader.db import db_queue_dead_letter, queue_bulk_upsert
    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "dfxtra"}).get_json()["id"]
    runner = a.runners[sid]
    reason = "Session expired -- re-login retries exhausted"
    with runner._lock:
        runner.jobs[DEAD] = {"status": "dead_letter", "message": reason, "ts": "now", "ord": 0, "priority": "",
                             "retries": 3, "retry_after": 99}
        runner.jobs[DONE] = {"status": "done", "message": "", "ts": "now", "ord": 10, "priority": "", "retries": 0}
    queue_bulk_upsert(sid, [DEAD, DONE])
    assert db_queue_dead_letter(sid, DEAD, reason)
    return c, sid, runner


def test_single_add_of_a_dead_letter_url_names_it_retryable():
    c, sid, runner = _setup()
    body = c.post("/api/queue/v2/add_url", json={"site_id": sid, "url": DEAD}).get_json()
    assert (body["added"], body["dupes"]) == (0, 1), body  # the finding's "0 added . 1 dupe"
    assert body.get("retryable_dupes") == [DEAD], body
    assert runner.jobs[DEAD]["status"] == "dead_letter"  # adding alone never re-queues


def test_list_add_names_the_dead_letter_dupe_not_the_done_one():
    c, sid, _ = _setup()
    body = c.post(f"/api/sites/{sid}/load_urls", json={"text": f"{DEAD}\n{DONE}"}).get_json()
    assert body.get("retryable_dupes") == [DEAD], body


def test_bulk_retry_requeues_the_dead_letter_job_in_memory_and_in_the_queue_table():
    from bulk_downloader.db import queue_load
    c, sid, runner = _setup()
    r = c.post(f"/api/sites/{sid}/bulk_retry", json={"urls": [DEAD, DONE]}).get_json()
    assert r == {"ok": True, "retried": 1}, r
    assert (runner.jobs[DEAD]["status"], runner.jobs[DEAD]["retries"], runner.jobs[DEAD]["retry_after"]) == ("pending", 0, 0)
    assert runner.jobs[DONE]["status"] == "done"
    row = next(x for x in queue_load(sid) if x["url"] == DEAD)
    assert (row["status"], row["retries"], row["retry_after"]) == ("pending", 0, 0), row
