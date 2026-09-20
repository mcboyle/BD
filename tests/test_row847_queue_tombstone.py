"""Row 847: AUTOMATED-BROKEN-LINK-AND-HTTP-410-TOMBSTONE-DETECTOR.

ACCEPTANCE (project-knowledge/IMPROVEMENT_BACKLOG.md row 847):
  (1) accurate classification of permanent delete responses (404, 410, deletion signals)
  (2) immediate suppression of retry attempts in production scheduler
  (3) un-tombstone API endpoint functions correctly
  (4) production app registers endpoints without manual test registration (E2)
  (5) live runner jobs in memory synchronize status with DB (E3)
  (6) permanent failures in runner automatically transition to tombstone (E1)
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import tombstone
except ImportError:
    tombstone = None

import bulk_downloader.app as app_mod
from bulk_downloader import db
from bulk_downloader.runner_scheduler import SchedulerMixin


class MockRunner(SchedulerMixin):
    def __init__(self, site_id: str, config: dict | None = None):
        self.site_id = site_id
        self.config = config or {}
        self.jobs = {}
        self._job_progress_samples = {}
        self._worker_current_urls = {}
        self._worker_heartbeats = {}
        self._worker_heartbeats_lock = type("Lock", (), {"__enter__": lambda s: None, "__exit__": lambda s, *a: None})()
        self._run_lifecycle_lock = type("Lock", (), {"__enter__": lambda s: None, "__exit__": lambda s, *a: None})()
        self._lock = type("Lock", (), {"__enter__": lambda s: None, "__exit__": lambda s, *a: None})()
        self.log = type("Log", (), {"warning": lambda s, *a, **k: None, "info": lambda s, *a, **k: None})()

    def _job_status_writer(self):
        class Writer:
            def __enter__(s):
                return lambda: None
            def __exit__(s, *a):
                pass
        return Writer()

    def log_event(self, *a, **k):
        pass

    def _fmt_dur(self, s):
        return f"{int(s)}s"

    def _worker_generation_is_current(self, g):
        return True

    def _update_job_current(self, url, status, message, **extra):
        from bulk_downloader.runner import SiteRunner
        return SiteRunner._update_job_current(self, url, status, message, **extra)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path, monkeypatch):
    """Use an isolated temp database directory for each test."""
    db_file = tmp_path / "test_queue.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    db.db_init()
    yield tmp_path


def _client():
    return app_mod.app.test_client()


# ---- (1) Classification of permanent delete responses -----------------

def test_classify_permanent_responses():
    assert tombstone is not None, "bulk_downloader.tombstone module must exist"
    # HTTP status codes
    assert tombstone.classify(status_code=404) is True
    assert tombstone.classify(status_code=410) is True
    assert tombstone.classify(status_code="404") is True
    assert tombstone.classify(status_code="410") is True

    # Site-specific deletion text signals
    assert tombstone.classify(message="This content has been removed by the uploader") is True
    assert tombstone.classify(message="video has been deleted") is True
    assert tombstone.classify(message="content no longer available") is True
    assert tombstone.classify(message="this page has been removed") is True
    assert tombstone.classify(message="HTTP 404: Not Found") is True
    assert tombstone.classify(message="410 Gone") is True

    # Negative controls: transient or unclassified stay False
    assert tombstone.classify(status_code=503) is False
    assert tombstone.classify(status_code=500) is False
    assert tombstone.classify(status_code=429) is False
    assert tombstone.classify(status_code=200) is False
    assert tombstone.classify(message="connection reset by peer") is False
    assert tombstone.classify(message="timed out") is False


# ---- (2) Production App Route Exposure (E2) ---------------------------

def test_production_app_exposes_tombstone_routes():
    """E2: Normal production app must expose tombstone endpoints without test-only register."""
    client = _client()

    # Positive control: existing queue endpoint is present
    pos = client.post("/api/queue/dead_letter/requeue", json={})
    assert pos.status_code != 404, "positive control /api/queue/dead_letter/requeue must exist"

    # Both tombstone routes must be present in normal app (400 on missing required fields, NOT 404)
    r1 = client.post("/api/queue/tombstone", json={})
    assert r1.status_code == 400, (
        f"Expected 400 from /api/queue/tombstone on empty body, got {r1.status_code} ({r1.get_data(as_text=True)})"
    )

    r2 = client.post("/api/queue/tombstone/untombstone", json={})
    assert r2.status_code == 400, (
        f"Expected 400 from /api/queue/tombstone/untombstone on empty body, got {r2.status_code} ({r2.get_data(as_text=True)})"
    )


# ---- (3) Immediate Suppression of Retry Attempts & Memory Sync (E3) ----

def test_auto_retry_scan_suppresses_tombstoned_job():
    """E3: When a job is tombstoned, _auto_retry_scan MUST NOT overwrite it with pending,
    even with auto_retry_failed=True, auto_retry_classify=False, and an expired timer."""
    assert tombstone is not None, "bulk_downloader.tombstone module must exist"
    site_id = "s1"
    url = "http://example.com/dead404"

    db.queue_upsert(site_id, url, status="failed", message="404 not found")

    runner = MockRunner(site_id, config={
        "auto_retry_failed": True,
        "auto_retry_classify": False,
        "auto_retry_schedule": "1s",
    })
    runner.jobs[url] = {
        "status": "failed",
        "message": "404 not found",
        "next_auto_retry_at": 1,  # Expired timer in the past
        "auto_retry_count": 0,
    }

    # Tombstone the URL
    ok = tombstone.tombstone_url(site_id, url, reason="404 not found", _runner=runner)
    assert ok is True
    assert tombstone.is_tombstoned(site_id, url) is True

    # Check live runner memory synchronization
    assert runner.jobs[url]["status"] == "tombstone"
    assert runner.jobs[url]["next_auto_retry_at"] == -1

    # Now execute real _auto_retry_scan()
    runner._auto_retry_scan()

    # Must remain tombstoned in memory and DB; MUST NOT be reverted to pending
    assert runner.jobs[url]["status"] == "tombstone"
    assert runner.jobs[url].get("auto_retry_count", 0) == 0

    rows = [r for r in db.queue_load(site_id) if r["url"] == url]
    assert len(rows) == 1
    assert rows[0]["status"] == "tombstone", f"DB status was overwritten: {rows[0]['status']}"


# ---- (4) Automatic Transition in Production Runner (E1) ----------------

def test_runner_auto_transitions_permanent_failure_to_tombstone():
    """E1: When a job fails in runner with 404/410, it automatically becomes tombstone."""
    site_id = "s1"
    url = "http://example.com/gone410"

    db.queue_upsert(site_id, url, status="running")
    runner = MockRunner(site_id)
    runner.jobs[url] = {"status": "running"}

    # Simulate worker failing with 410 Gone
    runner._update_job_current(url, "failed", "HTTP 410 Gone", status_code=410)

    # Must be transitioned to tombstone status automatically
    assert runner.jobs[url]["status"] == "tombstone"
    assert tombstone.is_tombstoned(site_id, url) is True

    rows = [r for r in db.queue_load(site_id) if r["url"] == url]
    assert len(rows) == 1
    assert rows[0]["status"] == "tombstone"


def test_runner_transient_failure_does_not_tombstone():
    """Negative control: transient failure (503) remains failed, NOT tombstone."""
    site_id = "s1"
    url = "http://example.com/temp503"

    db.queue_upsert(site_id, url, status="running")
    runner = MockRunner(site_id)
    runner.jobs[url] = {"status": "running"}

    runner._update_job_current(url, "failed", "HTTP 503 Service Unavailable", status_code=503)

    assert runner.jobs[url]["status"] == "failed"
    if tombstone is not None:
        assert tombstone.is_tombstoned(site_id, url) is False

    rows = [r for r in db.queue_load(site_id) if r["url"] == url]
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"


# ---- (5) Un-tombstone API Endpoint & Functionality ---------------------

def test_tombstone_and_untombstone_api():
    """Acceptance (3): un-tombstone API endpoint functions correctly."""
    site_id = "s1"
    url = "http://example.com/recover"
    db.queue_upsert(site_id, url, status="failed", message="404 not found")

    client = _client()

    # Step 1: Tombstone via API
    r_tomb = client.post("/api/queue/tombstone", json={
        "site_id": site_id,
        "url": url,
        "reason": "404 not found"
    })
    assert r_tomb.status_code == 200, r_tomb.get_data(as_text=True)
    body_tomb = r_tomb.get_json()
    assert body_tomb["ok"] is True
    assert tombstone.is_tombstoned(site_id, url) is True

    row = [r for r in db.queue_load(site_id) if r["url"] == url][0]
    assert row["status"] == "tombstone"

    # Step 2: Untombstone via API
    r_untomb = client.post("/api/queue/tombstone/untombstone", json={
        "site_id": site_id,
        "url": url
    })
    assert r_untomb.status_code == 200, r_untomb.get_data(as_text=True)
    body_untomb = r_untomb.get_json()
    assert body_untomb["ok"] is True
    assert tombstone.is_tombstoned(site_id, url) is False

    # Row must be back to pending with retry counters reset
    row_after = [r for r in db.queue_load(site_id) if r["url"] == url][0]
    assert row_after["status"] == "pending"
    assert row_after["retries"] == 0


def test_tombstone_url_no_matching_row_records_tombstone_pattern():
    """Tombstone pattern is recorded even if the row is not in queue."""
    site_id = "s1"
    url = "http://example.com/unseen404"
    ok = tombstone.tombstone_url(site_id, url, reason="404 not found")
    assert ok is False  # No queue row updated
    assert tombstone.is_tombstoned(site_id, url) is True


def test_api_validation_and_not_found_handling():
    client = _client()
    # Missing fields -> 400
    r1 = client.post("/api/queue/tombstone", json={"site_id": "s1"})
    assert r1.status_code == 400
    r2 = client.post("/api/queue/tombstone", json={"url": "http://example.com/x"})
    assert r2.status_code == 400

    # Non-existent queue row -> 404
    r3 = client.post("/api/queue/tombstone", json={"site_id": "s1", "url": "http://example.com/nonexistent"})
    assert r3.status_code == 404

    # Untombstone on non-tombstoned queue row -> 404
    db.queue_upsert("s1", "http://example.com/live", status="pending")
    r4 = client.post("/api/queue/tombstone/untombstone", json={"site_id": "s1", "url": "http://example.com/live"})
    assert r4.status_code == 404


def test_untombstone_preserves_non_tombstoned_rows():
    """WHERE status='tombstone' predicate must never touch pending or done rows."""
    site_id = "s1"
    url = "http://example.com/done_job"
    db.queue_upsert(site_id, url, status="done")

    # Call untombstone
    ok = tombstone.untombstone(site_id, url)
    assert ok is False

    row = [r for r in db.queue_load(site_id) if r["url"] == url][0]
    assert row["status"] == "done"


def test_exact_count_and_fixture_shape():
    """Exact count assertion: verifying counts of tombstoned vs pending rows."""
    site_id = "s_counts"
    db.queue_upsert(site_id, "http://a/1", status="pending")
    db.queue_upsert(site_id, "http://a/2", status="failed")
    db.queue_upsert(site_id, "http://a/3", status="failed")
    db.queue_upsert(site_id, "http://a/4", status="done")

    # Initial state
    all_rows = db.queue_load(site_id)
    assert len(all_rows) == 4, f"Expected exactly 4 seeded rows, got {len(all_rows)}"

    # Tombstone two failed rows
    assert tombstone.tombstone_url(site_id, "http://a/2", reason="404 not found") is True
    assert tombstone.tombstone_url(site_id, "http://a/3", reason="410 gone") is True

    # Check exact counts
    rows_after = db.queue_load(site_id)
    tombstones = [r for r in rows_after if r["status"] == "tombstone"]
    pending = [r for r in rows_after if r["status"] == "pending"]
    done = [r for r in rows_after if r["status"] == "done"]
    failed = [r for r in rows_after if r["status"] == "failed"]

    assert len(tombstones) == 2, f"Expected 2 tombstoned, got {len(tombstones)}"
    assert len(pending) == 1, f"Expected 1 pending, got {len(pending)}"
    assert len(done) == 1, f"Expected 1 done, got {len(done)}"
    assert len(failed) == 0, f"Expected 0 failed, got {len(failed)}"


# ---- (6) Self-mutation Census Proofs (Mutants 1-7) --------------------

def test_auto_retry_loop_invokes_scan():
    """Mutant 1: delete-the-call _auto_retry_scan@bulk_downloader/runner_scheduler.py:131.
    Verify that _auto_retry_loop actually executes _auto_retry_scan."""
    import threading
    site_id = "s_loop"
    url = "http://example.com/loop_dead"
    runner = MockRunner(site_id, config={"auto_retry_failed": True, "auto_retry_schedule": "1s"})
    runner.jobs[url] = {
        "status": "failed",
        "status_code": 404,
        "message": "404 Not Found",
        "next_auto_retry_at": 1,
    }

    runner._auto_retry_stop = threading.Event()
    call_count = [0]

    def fast_wait(timeout=None):
        call_count[0] += 1
        if call_count[0] >= 2:
            runner._auto_retry_stop.set()
        return runner._auto_retry_stop.is_set()

    runner._auto_retry_stop.wait = fast_wait
    runner.maybe_preemptive_relogin = lambda: None
    runner._scan_subscriptions = lambda: None

    runner._auto_retry_loop()

    # If _auto_retry_scan was invoked in the loop, the 404 failed job transitioned to tombstone
    assert runner.jobs[url]["status"] == "tombstone"
    assert runner.jobs[url]["next_auto_retry_at"] == -1


def test_untombstone_syncs_runner_memory():
    """Mutant 2: delete-the-call _sync_runner_jobs@bulk_downloader/tombstone.py:153.
    Untombstone with _runner must reset live runner job state to pending and retries to 0."""
    site_id = "s_sync"
    url = "http://example.com/untomb_sync"
    db.queue_upsert(site_id, url, status="failed")

    runner = MockRunner(site_id)
    runner.jobs[url] = {
        "status": "tombstone",
        "message": "was tombstoned",
        "next_auto_retry_at": -1,
        "auto_retry_count": 5,
    }

    ok_t = tombstone.tombstone_url(site_id, url, reason="404", _runner=runner)
    assert ok_t is True
    assert runner.jobs[url]["status"] == "tombstone"

    # Now untombstone passing _runner
    ok_u = tombstone.untombstone(site_id, url, _runner=runner)
    assert ok_u is True
    assert runner.jobs[url]["status"] == "pending"
    assert runner.jobs[url]["message"] == "untombstoned"
    assert runner.jobs[url]["next_auto_retry_at"] == 0
    assert runner.jobs[url]["auto_retry_count"] == 0


def test_retry_policy_classify_failure_integration():
    """Mutant 3: invert-the-branch classify@bulk_downloader/retry_policy.py:214.
    Verify retry_policy.classify_failure returns permanent for tombstone classifications
    and does NOT return permanent for transient / rate-limited failures."""
    from bulk_downloader import retry_policy as rp

    # Permanent responses identified by tombstone.classify
    assert rp.classify_failure(message="video has been deleted") == "permanent"
    assert rp.classify_failure(message="this page has been removed") == "permanent"
    assert rp.classify_failure(status_code=404) == "permanent"
    assert rp.classify_failure(status_code=410) == "permanent"

    # Negative controls: must NOT be permanent
    assert rp.classify_failure(status_code=503, message="service unavailable") == "transient"
    assert rp.classify_failure(status_code=500, message="internal server error") == "transient"
    assert rp.classify_failure(status_code=429, message="rate limit exceeded") == "rate_limited"


def test_pending_url_already_downloadable_classification():
    """Mutant 4: delete-the-call classify@bulk_downloader/runner.py:879.
    Verify _pending_url_already_downloadable calls candidate_filter.classify."""
    from bulk_downloader.runner import _pending_url_already_downloadable

    # Direct media URL is classified as downloadable
    assert _pending_url_already_downloadable("https://example.com/asset.mp4") is True
    assert _pending_url_already_downloadable("https://example.com/page.html") is False


def test_auto_retry_scan_detects_and_tombstones_unrecorded_permanent_failure():
    """Mutants 5 & 7:
    Mutant 5: invert-the-branch classify@bulk_downloader/runner_scheduler.py:254
    Mutant 7: delete-the-call tombstone_url@bulk_downloader/runner_scheduler.py:255
    When _auto_retry_scan encounters a failed job with a permanent error that has not yet
    been recorded in db_tombstones, it MUST call tombstone_url (recording in DB) and
    transition memory status to tombstone. Conversely, transient failures MUST NOT be tombstoned."""
    site_id = "s_scan_detect"
    url_perm = "http://example.com/unrecorded_410"
    url_trans = "http://example.com/transient_502"

    db.queue_upsert(site_id, url_perm, status="failed", message="410 Gone")
    db.queue_upsert(site_id, url_trans, status="failed", message="502 Bad Gateway")

    runner = MockRunner(site_id, config={"auto_retry_failed": True, "auto_retry_schedule": "1s"})
    runner.jobs[url_perm] = {
        "status": "failed",
        "status_code": 410,
        "message": "410 Gone",
        "next_auto_retry_at": 1,
    }
    runner.jobs[url_trans] = {
        "status": "failed",
        "status_code": 502,
        "message": "502 Bad Gateway",
        "next_auto_retry_at": 1,
    }

    # Before scan, neither URL is recorded in db_tombstones
    assert tombstone.is_tombstoned(site_id, url_perm) is False
    assert tombstone.is_tombstoned(site_id, url_trans) is False

    runner._auto_retry_scan()

    # Mutant 7 check: tombstone_url must have been called, persisting to DB
    assert tombstone.is_tombstoned(site_id, url_perm) is True, "tombstone_url was not called by scheduler scan"
    # Mutant 5 check: branch was not inverted
    assert runner.jobs[url_perm]["status"] == "tombstone"
    assert runner.jobs[url_perm]["next_auto_retry_at"] == -1
    # Transient job must NOT have been converted to tombstone
    assert runner.jobs[url_trans]["status"] != "tombstone"
    assert tombstone.is_tombstoned(site_id, url_trans) is False


def test_auto_retry_scan_uses_classify_failure_class_config():
    """Mutant 6: delete-the-call classify_failure@bulk_downloader/runner_scheduler.py:275.
    When auto_retry_classify is enabled, classify_failure determines fail_class and its max_attempts.
    For 'auth' errors (max_attempts=3), a job with auto_retry_count=3 MUST NOT be retried."""
    site_id = "s_auth_cap"
    url = "http://example.com/auth_fail"
    db.queue_upsert(site_id, url, status="failed", message="401 Unauthorized")

    runner = MockRunner(site_id, config={
        "auto_retry_failed": True,
        "auto_retry_classify": True,
        "auto_retry_max_attempts": 10,  # Site max is 10, but 'auth' class max is 3
    })
    runner.jobs[url] = {
        "status": "failed",
        "status_code": 401,
        "message": "401 Unauthorized",
        "auto_retry_count": 3,
        "next_auto_retry_at": 1,
    }

    runner._auto_retry_scan()

    # With classify_failure active, fail_class='auth', max_attempts=3, so attempt 3 >= 3 means no retry
    assert runner.jobs[url].get("auto_retry_count") == 3
    assert runner.jobs[url].get("next_auto_retry_at") == 1


