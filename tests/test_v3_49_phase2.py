"""v3.49 (Phase 2) — regression tests for the bulk queue API.

Uses an inline _setup() helper rather than a named pytest fixture,
since the project's custom test runner only supports autouse fixtures.
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

def _setup():
    """Returns (client, sid, app_module). Caller's autouse fixture must
    have set BD_HOME + cwd already.

    v3.66.22: removed an unreachable save/restore block that sat
    below the ``return c, sid, a`` line — the same dead-code
    defect shape as v3.66.21's fix in
    ``test_v3_53_phase6.py::_get_server``. The block looked like a
    sys.modules restore but never ran, so the unused
    ``saved_modules`` capture above the return was paired with
    nothing. In practice this wasn't a polluter — the outer
    ``bd_module_wipe`` marker on this file already wraps each test
    with a proper conftest save+wipe+restore (see
    ``tests/conftest.py`` lines 112-133) — but the dead lines were
    confusing future readers and matched a class of defect the
    project has been retiring, so the cleanup is worth shipping.
    The inner wipe below stays because some non-pytest runners in
    the project's history don't honor markers.
    """
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/sites", json={"name": "TestSite"})
    sid = r.get_json()["id"]
    runner = a.runners[sid]
    with runner._lock:
        for i in range(10):
            runner.jobs[f"https://example.com/v{i}"] = {
                "status": "pending", "message": "", "ts": "now",
                "ord": i * 10, "priority": ""}
    from bulk_downloader.db import queue_bulk_upsert
    queue_bulk_upsert(sid, [f"https://example.com/v{i}" for i in range(10)])
    return c, sid, a


def test_bulk_mark_basic():
    c, sid, _ = _setup()
    r = c.post(f"/api/sites/{sid}/jobs/bulk_mark", json={
        "urls": [f"https://example.com/v{i}" for i in range(5)],
        "status": "failed", "message": "test"})
    body = r.get_json()
    assert body["ok"] is True
    assert body["affected"] == 5
    assert body["requested"] == 5


def test_bulk_mark_rejects_invalid_status():
    c, sid, _ = _setup()
    r = c.post(f"/api/sites/{sid}/jobs/bulk_mark", json={
        "urls": ["https://example.com/v0"], "status": "running"})
    assert r.status_code == 400


def test_bulk_mark_rejects_empty_url_list():
    c, sid, _ = _setup()
    r = c.post(f"/api/sites/{sid}/jobs/bulk_mark", json={
        "urls": [], "status": "failed"})
    assert r.status_code == 400


def test_bulk_mark_rejects_oversized_url_list():
    c, sid, _ = _setup()
    r = c.post(f"/api/sites/{sid}/jobs/bulk_mark", json={
        "urls": [f"u{i}" for i in range(5001)],
        "status": "failed"})
    assert r.status_code == 400


def test_bulk_mark_tolerates_unknown_urls():
    c, sid, _ = _setup()
    r = c.post(f"/api/sites/{sid}/jobs/bulk_mark", json={
        "urls": ["https://example.com/v0",
                 "https://example.com/nonexistent",
                 "https://example.com/v1"],
        "status": "failed"})
    body = r.get_json()
    assert body["ok"] is True
    assert body["affected"] == 2
    assert body["requested"] == 3


def test_bulk_delete_removes_from_queue():
    c, sid, a = _setup()
    urls = [f"https://example.com/v{i}" for i in range(3)]
    r = c.post(f"/api/sites/{sid}/jobs/bulk_delete",
               json={"urls": urls})
    body = r.get_json()
    assert body["ok"] is True
    assert body["affected"] == 3
    runner = a.runners[sid]
    with runner._lock:
        for u in urls:
            assert u not in runner.jobs


def test_bulk_priority_sets_label():
    c, sid, a = _setup()
    urls = [f"https://example.com/v{i}" for i in range(3)]
    r = c.post(f"/api/sites/{sid}/jobs/bulk_priority",
               json={"urls": urls, "priority": "high"})
    body = r.get_json()
    assert body["ok"] is True
    assert body["affected"] == 3
    assert body["priority"] == "high"
    runner = a.runners[sid]
    with runner._lock:
        for u in urls:
            assert runner.jobs[u]["priority"] == "high"


def test_bulk_priority_truncates_long_value():
    c, sid, _ = _setup()
    r = c.post(f"/api/sites/{sid}/jobs/bulk_priority", json={
        "urls": ["https://example.com/v0"], "priority": "x" * 100})
    body = r.get_json()
    assert body["ok"] is True
    assert len(body["priority"]) <= 20


def test_reorder_updates_ord_atomically():
    c, sid, a = _setup()
    ordering = {f"https://example.com/v{i}": (5 - i) * 10
                for i in range(5)}
    r = c.post(f"/api/sites/{sid}/jobs/reorder",
               json={"ordering": ordering})
    body = r.get_json()
    assert body["ok"] is True
    assert body["affected"] == 5
    runner = a.runners[sid]
    with runner._lock:
        for i in range(5):
            assert runner.jobs[f"https://example.com/v{i}"]["ord"] == (5 - i) * 10


def test_reorder_rejects_non_integer_ord():
    c, sid, _ = _setup()
    r = c.post(f"/api/sites/{sid}/jobs/reorder", json={
        "ordering": {"https://example.com/v0": "not-a-number"}})
    assert r.status_code == 400


def test_reorder_rejects_empty_ordering():
    c, sid, _ = _setup()
    r = c.post(f"/api/sites/{sid}/jobs/reorder",
               json={"ordering": {}})
    assert r.status_code == 400


def test_jobs_detail_returns_full_state():
    c, sid, _ = _setup()
    r = c.get(f"/api/sites/{sid}/jobs/detail?url=https://example.com/v0")
    body = r.get_json()
    assert body["ok"] is True
    assert body["site_id"] == sid
    assert body["url"] == "https://example.com/v0"
    assert body["job"]["status"] == "pending"
    assert "history" in body
    assert "history_count" in body


def test_jobs_detail_rejects_missing_url():
    c, sid, _ = _setup()
    r = c.get(f"/api/sites/{sid}/jobs/detail")
    assert r.status_code == 400


def test_jobs_detail_404_for_unknown_url():
    c, sid, _ = _setup()
    r = c.get(f"/api/sites/{sid}/jobs/detail"
              f"?url=https://example.com/nope")
    assert r.status_code == 404


def test_pause_all_then_resume_all():
    c, sid, a = _setup()
    r = c.post("/api/runners/pause_all")
    body = r.get_json()
    assert body["ok"] is True
    assert body["paused"] >= 1
    runner = a.runners[sid]
    assert getattr(runner, "_state", "") != "running"
    r = c.post("/api/runners/resume_all")
    body = r.get_json()
    assert body["ok"] is True


def test_db_queue_bulk_delete_helper():
    from bulk_downloader.db import (
        db_init, queue_bulk_upsert, queue_bulk_delete, queue_count)
    db_init()
    sid = "test_chunking"
    urls = [f"https://example.com/v{i}" for i in range(1200)]
    queue_bulk_upsert(sid, urls)
    assert queue_count(sid) == 1200
    deleted = queue_bulk_delete(sid, urls[:700])
    assert deleted == 700
    assert queue_count(sid) == 500


def test_db_queue_bulk_mark_helper():
    from bulk_downloader.db import (
        db_init, queue_bulk_upsert, queue_bulk_mark, db_conn)
    db_init()
    sid = "test_mark"
    urls = [f"https://example.com/v{i}" for i in range(5)]
    queue_bulk_upsert(sid, urls)
    affected = queue_bulk_mark(
        sid, urls + ["https://example.com/ghost"], "failed",
        message="bulk test")
    assert affected == 5
    with db_conn() as cx:
        rows = cx.execute(
            "SELECT status FROM queue WHERE site_id=?", (sid,)
        ).fetchall()
    assert all(r["status"] == "failed" for r in rows)


def test_db_queue_bulk_mark_rejects_invalid_status():
    from bulk_downloader.db import db_init, queue_bulk_mark
    db_init()
    raised = False
    try:
        queue_bulk_mark("sid", ["u1"], "garbage")
    except ValueError as e:
        raised = "status must be one of" in str(e)
    assert raised


def test_db_queue_reorder_helper():
    from bulk_downloader.db import (
        db_init, queue_bulk_upsert, queue_reorder, db_conn)
    db_init()
    sid = "test_reorder"
    urls = [f"https://example.com/v{i}" for i in range(5)]
    queue_bulk_upsert(sid, urls)
    new_ordering = {u: (5 - i) * 10 for i, u in enumerate(urls)}
    affected = queue_reorder(sid, new_ordering)
    assert affected == 5
    with db_conn() as cx:
        rows = {r["url"]: r["ord"] for r in cx.execute(
            "SELECT url, ord FROM queue WHERE site_id=?", (sid,)
        ).fetchall()}
    assert rows[urls[0]] == 50
    assert rows[urls[4]] == 10


def test_db_queue_reorder_empty_dict_returns_zero():
    from bulk_downloader.db import db_init, queue_reorder
    db_init()
    assert queue_reorder("any", {}) == 0


# ── v3.62.x: queue_bulk_update + N+1 write-storm elimination ────────
# The runner's bulk_* operations (pause/resume/retry/approve, retry-all,
# folder-scan done-marking) used to loop queue_upsert — one DB
# transaction PER URL. On a large selection that is a write storm that
# serializes against the download workers and stalls the whole app.
# They now route through single-transaction bulk primitives.

def test_db_queue_bulk_update_helper():
    """queue_bulk_update sets the same fields on N URLs and chunks
    past SQLite's parameter limit (1200 > 999)."""
    from bulk_downloader.db import (
        db_init, queue_bulk_upsert, queue_bulk_update, db_conn)
    db_init()
    sid = "test_bulk_update"
    urls = [f"https://example.com/v{i}" for i in range(1200)]
    queue_bulk_upsert(sid, urls)
    affected = queue_bulk_update(sid, urls, status="failed", retries=0)
    assert affected == 1200
    with db_conn() as cx:
        rows = cx.execute(
            "SELECT COUNT(*) FROM queue WHERE site_id=? AND status='failed'",
            (sid,)).fetchone()[0]
    assert rows == 1200


def test_queue_bulk_update_unknown_columns_dropped():
    """Unknown column names are dropped, not interpolated into SQL."""
    from bulk_downloader.db import db_init, queue_bulk_upsert, queue_bulk_update
    db_init()
    sid = "test_bulk_update_bad"
    urls = [f"https://example.com/v{i}" for i in range(3)]
    queue_bulk_upsert(sid, urls)
    # 'evil' is not a real column — dropped; 'status' still applied
    affected = queue_bulk_update(sid, urls, status="done", evil="x")
    assert affected == 3
    # all-unknown / empty field sets are no-ops, not errors
    assert queue_bulk_update(sid, urls, evil="x") == 0
    assert queue_bulk_update(sid, []) == 0


def test_queue_bulk_update_is_one_transaction():
    """The whole point of the fix: N URLs cost ONE transaction, not
    one per URL. queue_bulk_update must open db_conn exactly once even
    for a list far larger than the chunk size."""
    from bulk_downloader import db as dbm
    dbm.db_init()
    sid = "test_bulk_update_txn"
    urls = [f"https://example.com/v{i}" for i in range(1200)]
    dbm.queue_bulk_upsert(sid, urls)
    calls = {"n": 0}
    real = dbm.db_conn
    def _counting_conn():
        calls["n"] += 1
        return real()
    dbm.db_conn = _counting_conn
    try:
        dbm.queue_bulk_update(sid, urls, status="pending", retries=0)
    finally:
        dbm.db_conn = real
    assert calls["n"] == 1, \
        f"queue_bulk_update opened {calls['n']} transactions — expected 1"


def test_runner_bulk_ops_do_not_loop_queue_upsert():
    """Guard: the runner's bulk_* / reorder / retry_failed methods must
    persist via the bulk primitives, never a per-URL queue_upsert loop
    (the N+1 write storm). Pins the rewiring so it cannot regress."""
    import os
    import bulk_downloader
    runner_path = os.path.join(
        os.path.dirname(bulk_downloader.__file__), "runner.py")
    import glob as _glob
    _pkg = os.path.dirname(bulk_downloader.__file__)
    src = "\n".join(open(q, encoding="utf-8").read()
                    for q in [runner_path] + sorted(_glob.glob(os.path.join(_pkg, "runner_*.py"))))

    def _method_src(name):
        start = src.find(f"\n    def {name}(")
        assert start != -1, f"{name} not found in runner.py"
        nxt = src.find("\n    def ", start + 1)
        return src[start:nxt if nxt != -1 else len(src)]

    for name in ("load_urls", "reorder_urls", "bulk_priority",
                 "bulk_delete", "bulk_approve", "bulk_pause",
                 "bulk_resume", "bulk_retry", "bulk_reorder",
                 "retry_failed"):
        body = _method_src(name)
        assert "queue_upsert(" not in body, \
            f"{name} still calls queue_upsert — likely a per-URL " \
            f"N+1 loop; use a bulk primitive instead"
