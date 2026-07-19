"""Phase 2 Cut 2.1 (v3.66.617): queue lanes + priorities + dependencies +
dead-letter.

Priorities already exist (priority column, high/normal). This cut adds:
  * LANES     -- a `lane` column so jobs partition into independent processing
                 lanes (default 'default').
  * DEPENDS   -- a `depends_on` column: a job is not READY to dispatch until the
                 job it depends on has status 'done'. If the dependency is
                 permanently gone (dead_letter/failed) the dependent is BLOCKED
                 (never dispatched, surfaced to the operator) rather than run.
  * DEAD-LETTER -- retry-exhausted jobs move to a TERMINAL 'dead_letter' status
                 (not plain 'failed', which housekeeping re-picks) with a reason;
                 listable + explicitly requeue-able. (Same pattern plugins.py
                 already uses for webhook sinks.)

Decision logic lives in the pure, testable bulk_downloader/queue_policy module;
the runner wires it (the runtime dequeue/retry path is gated on-stash).

RED on 616: no lane/depends_on columns; no queue_policy module; no dead-letter
db helpers.
"""
import os
import tempfile


def _isolated_db():
    d = tempfile.mkdtemp()
    os.chdir(d)
    from bulk_downloader import db
    db.db_init()
    return db


# ── schema: lane + depends_on columns ────────────────────────────────────
def test_queue_table_has_lane_and_depends_on():
    db = _isolated_db()
    with db.db_conn() as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(queue)").fetchall()}
    assert "lane" in cols, "queue table missing `lane` column"
    assert "depends_on" in cols, "queue table missing `depends_on` column"


def test_lane_and_depends_on_persist_via_upsert():
    db = _isolated_db()
    db.queue_upsert("s1", "http://x/a", lane="fast", depends_on="http://x/z")
    rows = db.queue_load("s1")
    r = [x for x in rows if x["url"] == "http://x/a"][0]
    assert r["lane"] == "fast", f"lane not persisted: {r}"
    assert r["depends_on"] == "http://x/z", f"depends_on not persisted: {r}"


def test_lane_defaults_to_default():
    db = _isolated_db()
    db.queue_upsert("s1", "http://x/b", status="pending")
    r = [x for x in db.queue_load("s1") if x["url"] == "http://x/b"][0]
    assert (r["lane"] or "default") == "default", f"lane default wrong: {r['lane']!r}"


# ── dead-letter ──────────────────────────────────────────────────────────
def test_db_queue_dead_letter_moves_to_terminal_status():
    db = _isolated_db()
    db.queue_upsert("s1", "http://x/fail", status="failed", retries=5)
    assert hasattr(db, "db_queue_dead_letter"), "db.db_queue_dead_letter missing"
    db.db_queue_dead_letter("s1", "http://x/fail", "max retries exhausted")
    r = [x for x in db.queue_load("s1") if x["url"] == "http://x/fail"][0]
    assert r["status"] == "dead_letter", f"not dead-lettered: {r['status']}"
    assert "exhaust" in (r["message"] or "").lower()


def test_dead_letter_jobs_are_listable():
    db = _isolated_db()
    db.queue_upsert("s1", "http://x/1", status="done")
    db.queue_upsert("s1", "http://x/2", status="pending")
    db.db_queue_dead_letter("s1", "http://x/3", "boom") if False else None
    db.queue_upsert("s1", "http://x/3", status="pending")
    db.db_queue_dead_letter("s1", "http://x/3", "boom")
    rows, _ = db.queue_search(site_id="s1", status="dead_letter")
    urls = {r["url"] for r in rows}
    assert urls == {"http://x/3"}, f"dead-letter listing wrong: {urls}"


def test_db_queue_requeue_dead_letter_resets_job():
    db = _isolated_db()
    db.queue_upsert("s1", "http://x/dl", status="pending")
    db.db_queue_dead_letter("s1", "http://x/dl", "boom")
    assert hasattr(db, "db_queue_requeue_dead_letter"), "db.db_queue_requeue_dead_letter missing"
    db.db_queue_requeue_dead_letter("s1", "http://x/dl")
    r = [x for x in db.queue_load("s1") if x["url"] == "http://x/dl"][0]
    assert r["status"] == "pending", f"requeue did not reset status: {r['status']}"
    assert (r["retries"] or 0) == 0, "requeue did not reset retries"


# ── pure policy: queue_policy module ─────────────────────────────────────
def test_should_dead_letter_predicate():
    from bulk_downloader import queue_policy as qp
    assert qp.should_dead_letter(2, 2) is True
    assert qp.should_dead_letter(3, 2) is True
    assert qp.should_dead_letter(1, 2) is False
    assert qp.should_dead_letter(0, 2) is False


def test_dependency_satisfied_gates_on_done():
    from bulk_downloader import queue_policy as qp
    jobs = {
        "A": {"url": "A", "status": "pending", "depends_on": ""},
        "B": {"url": "B", "status": "pending", "depends_on": "A"},
    }
    # B depends on A which is not done -> not satisfied
    assert qp.dependency_satisfied(jobs["B"], jobs) is False
    # once A is done -> satisfied
    jobs["A"]["status"] = "done"
    assert qp.dependency_satisfied(jobs["B"], jobs) is True
    # a job with no dependency is always satisfied
    assert qp.dependency_satisfied(jobs["A"], jobs) is True


def test_dependency_blocked_when_dep_permanently_gone():
    from bulk_downloader import queue_policy as qp
    jobs = {
        "A": {"url": "A", "status": "dead_letter", "depends_on": ""},
        "B": {"url": "B", "status": "pending", "depends_on": "A"},
    }
    # dep is dead-lettered -> can never be satisfied -> blocked
    assert qp.dependency_blocked(jobs["B"], jobs) is True
    assert qp.dependency_satisfied(jobs["B"], jobs) is False
    # a missing dependency (unknown url) is also treated as blocked (can't satisfy)
    jobs["B"]["depends_on"] = "does-not-exist"
    assert qp.dependency_blocked(jobs["B"], jobs) is True


def test_order_ready_jobs_priority_then_ord_within_lane():
    from bulk_downloader import queue_policy as qp
    jobs = {
        "u1": {"url": "u1", "status": "pending", "lane": "default", "priority": "normal", "ord": 1, "depends_on": ""},
        "u2": {"url": "u2", "status": "pending", "lane": "default", "priority": "high", "ord": 2, "depends_on": ""},
        "u3": {"url": "u3", "status": "pending", "lane": "default", "priority": "normal", "ord": 0, "depends_on": ""},
    }
    ready = qp.order_ready_jobs(jobs)
    order = [j["url"] for j in ready]
    # high priority first, then by ord ascending among normals
    assert order[0] == "u2", f"high priority not first: {order}"
    assert order[1:] == ["u3", "u1"], f"ord tiebreak wrong: {order}"


def test_order_ready_jobs_excludes_unready_and_terminal():
    from bulk_downloader import queue_policy as qp
    jobs = {
        "A": {"url": "A", "status": "pending", "lane": "default", "priority": "normal", "ord": 0, "depends_on": ""},
        "B": {"url": "B", "status": "pending", "lane": "default", "priority": "normal", "ord": 1, "depends_on": "A"},
        "C": {"url": "C", "status": "done", "lane": "default", "priority": "normal", "ord": 2, "depends_on": ""},
        "D": {"url": "D", "status": "dead_letter", "lane": "default", "priority": "normal", "ord": 3, "depends_on": ""},
    }
    ready = [j["url"] for j in qp.order_ready_jobs(jobs)]
    # A ready; B not (dep A not done); C/D terminal excluded
    assert ready == ["A"], f"ready set wrong: {ready}"


def test_order_ready_jobs_partitions_by_lane():
    from bulk_downloader import queue_policy as qp
    jobs = {
        "f1": {"url": "f1", "status": "pending", "lane": "fast", "priority": "normal", "ord": 0, "depends_on": ""},
        "s1": {"url": "s1", "status": "pending", "lane": "slow", "priority": "normal", "ord": 0, "depends_on": ""},
    }
    by_lane = qp.order_ready_jobs_by_lane(jobs)
    assert set(by_lane.keys()) == {"fast", "slow"}, f"lanes wrong: {by_lane.keys()}"
    assert [j["url"] for j in by_lane["fast"]] == ["f1"]
    assert [j["url"] for j in by_lane["slow"]] == ["s1"]
