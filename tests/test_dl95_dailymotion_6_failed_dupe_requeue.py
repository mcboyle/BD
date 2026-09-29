"""dl95-dailymotion-6 (O1513 A5-A finding D6): re-adding a FAILED URL said "0 added · 2 dupes" and did nothing.

load_urls counts a URL already in runner.jobs as a dupe whatever its status, so a failed job stayed failed and
the Add URLs toast read as success. GREEN: both enqueue routes name the dupes that are failed / needs_review
jobs (retryable_dupes -- exactly what /api/sites/<sid>/bulk_retry re-queues), so the dialog can say so and offer
Requeue; bulk_retry on that list re-queues them. A dupe that is pending/done is not offered.

Real Flask test client on the isolated BD_HOME the conftest autouse fixture provides; no network.
"""
import sys

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

FAILED = "https://example.com/video/failed-1"
FAILED2 = "https://example.com/video/failed-2"
REVIEW = "https://example.com/video/review-1"
DONE = "https://example.com/video/done-1"
PENDING = "https://example.com/video/pending-1"


def _setup():
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    from bulk_downloader import app as a
    c = a.app.test_client()
    sid = c.post("/api/sites", json={"name": "Dl95Site"}).get_json()["id"]
    runner = a.runners[sid]
    with runner._lock:
        for i, (url, status) in enumerate(
            [(FAILED, "failed"), (FAILED2, "failed"), (REVIEW, "needs_review"), (DONE, "done"), (PENDING, "pending")]
        ):
            runner.jobs[url] = {"status": status, "message": "No download button found", "ts": "now",
                                "ord": i * 10, "priority": "", "retries": 3}
    from bulk_downloader.db import queue_bulk_upsert
    queue_bulk_upsert(sid, [FAILED, FAILED2, REVIEW, DONE, PENDING])
    return c, sid, runner


def test_single_add_of_a_failed_url_names_it_retryable():
    c, sid, runner = _setup()
    body = c.post("/api/queue/v2/add_url", json={"site_id": sid, "url": FAILED}).get_json()
    assert (body["added"], body["dupes"]) == (0, 1), body  # the finding's "0 added · 1 dupe"
    assert body.get("retryable_dupes") == [FAILED], body
    assert runner.jobs[FAILED]["status"] == "failed"  # adding alone never re-queues


def test_single_add_of_a_done_or_pending_url_offers_nothing():
    c, sid, _ = _setup()
    for url in (DONE, PENDING):
        body = c.post("/api/queue/v2/add_url", json={"site_id": sid, "url": url}).get_json()
        assert (body["added"], body["dupes"], body.get("retryable_dupes")) == (0, 1, []), body


def test_list_add_names_exactly_the_failed_and_review_dupes():
    c, sid, _ = _setup()
    text = "\n".join([FAILED, DONE, FAILED2, PENDING, REVIEW, "https://example.com/video/new-1"])
    body = c.post(f"/api/sites/{sid}/load_urls", json={"text": text}).get_json()
    assert (body["added"], body["dupes_skipped"]) == (1, 5), body
    assert body.get("retryable_dupes") == [FAILED, FAILED2, REVIEW], body


def test_list_add_with_no_dupes_offers_nothing():
    c, sid, _ = _setup()
    body = c.post(f"/api/sites/{sid}/load_urls", json={"text": "https://example.com/video/new-2"}).get_json()
    assert (body["added"], body["dupes_skipped"], body.get("retryable_dupes")) == (1, 0, []), body


def test_requeueing_the_named_dupes_via_bulk_retry_makes_them_pending():
    c, sid, runner = _setup()
    body = c.post(f"/api/sites/{sid}/load_urls", json={"text": f"{FAILED}\n{REVIEW}\n{DONE}"}).get_json()
    r = c.post(f"/api/sites/{sid}/bulk_retry", json={"urls": body["retryable_dupes"]}).get_json()
    assert r == {"ok": True, "retried": 2}, r
    assert [runner.jobs[u]["status"] for u in (FAILED, REVIEW, DONE)] == ["pending", "pending", "done"]
    assert runner.jobs[FAILED]["retries"] == 0


def test_retryable_urls_matches_bulk_retry_statuses():
    from bulk_downloader.runner_queue import BULK_RETRY_STATUSES
    _, _, runner = _setup()
    assert runner.retryable_urls([PENDING, REVIEW, FAILED, REVIEW, DONE]) == [REVIEW, FAILED]
    assert set(BULK_RETRY_STATUSES) == {"failed", "needs_review"}
