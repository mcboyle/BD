"""Row 1015: Adaptive SQLite B-Tree Page Freelist Monitor with Idle-Cycle Incremental Vacuuming.

Provides SQLite B-tree freelist ratio monitoring, page reclamation threshold calculation,
and non-blocking idle-cycle incremental vacuuming.

RED on baseline: db_maintenance lacks FreelistMonitor and IncrementalVacuumController.
"""
from __future__ import annotations

import sqlite3

BD_GATE_SCOPE = "repo-wide"


def test_red_maintenance_reclaims_freelist():
    """On base, run_sqlite_maintenance reports ok but leaves freelist pages unreclaimable.
    After deleting rows on an INCREMENTAL auto_vacuum DB, the base maintenance path
    does not reclaim freelist pages."""
    from bulk_downloader import db_maintenance

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("PRAGMA auto_vacuum = INCREMENTAL")
    cursor.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, data TEXT)")
    cursor.executemany(
        "INSERT INTO t (data) VALUES (?)", [("X" * 1000,) for _ in range(500)]
    )
    conn.commit()
    cursor.execute("DELETE FROM t WHERE id <= 400")
    conn.commit()

    fl_before = cursor.execute("PRAGMA freelist_count").fetchone()[0]
    assert fl_before > 0, "positive control: freelist has pages after delete"

    result = db_maintenance.run_sqlite_maintenance(conn)
    assert result["ok"] is True

    assert result.get("freelist_freed", 0) > 0, (
        f"maintenance must reclaim freelist pages, got freelist_freed={result.get('freelist_freed', 0)}"
    )
    fl_after = cursor.execute("PRAGMA freelist_count").fetchone()[0]
    assert fl_after < fl_before, f"freelist must decrease: {fl_before} -> {fl_after}"
    conn.close()


def test_freelist_monitor_metrics():
    """Verify FreelistMonitor measures B-tree page counts, freelist pages, and ratio."""
    from bulk_downloader.sqlite_freelist_vacuum import FreelistMonitor

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("PRAGMA page_size = 4096")
    cursor.execute("PRAGMA auto_vacuum = INCREMENTAL")
    cursor.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, content TEXT)")

    # Insert 1000 rows
    cursor.executemany("INSERT INTO test_data (content) VALUES (?)", [("X" * 1000,) for _ in range(1000)])
    conn.commit()

    monitor = FreelistMonitor(freelist_ratio_threshold=0.10)
    initial_metrics = monitor.inspect(conn)
    assert initial_metrics.page_count > 0
    assert initial_metrics.freelist_count == 0
    assert initial_metrics.freelist_ratio == 0.0
    assert initial_metrics.auto_vacuum_mode == 2  # INCREMENTAL
    assert not monitor.should_vacuum(conn)

    # Delete 500 rows to populate the freelist
    cursor.execute("DELETE FROM test_data WHERE id <= 500")
    conn.commit()

    post_delete = monitor.inspect(conn)
    assert post_delete.freelist_count > 0
    assert post_delete.freelist_ratio > 0.10
    assert post_delete.freelist_bytes == post_delete.freelist_count * post_delete.page_size
    assert monitor.should_vacuum(conn) is True
    conn.close()


def test_incremental_vacuum_step():
    """Verify IncrementalVacuumController reclaims pages step-by-step."""
    from bulk_downloader.sqlite_freelist_vacuum import (
        FreelistMonitor,
        IncrementalVacuumController,
    )

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("PRAGMA auto_vacuum = INCREMENTAL")
    cursor.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, content TEXT)")
    cursor.executemany("INSERT INTO test_data (content) VALUES (?)", [("Y" * 2000,) for _ in range(800)])
    conn.commit()

    cursor.execute("DELETE FROM test_data WHERE id <= 600")
    conn.commit()

    monitor = FreelistMonitor()
    before = monitor.inspect(conn)
    assert before.freelist_count > 10

    controller = IncrementalVacuumController(pages_per_step=5)
    pages_freed = controller.step_vacuum(conn, pages=5)
    assert pages_freed == 5

    after = monitor.inspect(conn)
    assert after.freelist_count == before.freelist_count - 5
    conn.close()


def test_idle_cycle_vacuum_with_interruption():
    """Verify idle cycle incremental vacuuming respects idle predicate and limits."""
    from bulk_downloader.sqlite_freelist_vacuum import IncrementalVacuumController

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("PRAGMA auto_vacuum = INCREMENTAL")
    cursor.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, payload TEXT)")
    cursor.executemany("INSERT INTO items (payload) VALUES (?)", [("Z" * 1500,) for _ in range(1000)])
    conn.commit()

    cursor.execute("DELETE FROM items WHERE id <= 800")
    conn.commit()

    controller = IncrementalVacuumController(pages_per_step=10)

    # Interrupt after 2 steps
    step_counter = 0

    def is_idle():
        nonlocal step_counter
        step_counter += 1
        return step_counter <= 2

    report = controller.run_idle_cycle(conn, is_idle_callback=is_idle, max_duration_seconds=1.0)
    assert report["steps_completed"] == 2
    assert report["pages_freed"] == 20
    assert report["interrupted"] is True
    assert report["final_freelist"] < report["initial_freelist"]
    conn.close()


def test_non_incremental_vacuum_handling():
    """Verify handling when database is not configured for incremental vacuum."""
    from bulk_downloader.sqlite_freelist_vacuum import (
        FreelistMonitor,
        IncrementalVacuumController,
    )

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("PRAGMA auto_vacuum = NONE")
    cursor.execute("CREATE TABLE t (x INT)")
    cursor.execute("INSERT INTO t VALUES (1)")
    conn.commit()

    monitor = FreelistMonitor()
    metrics = monitor.inspect(conn)
    assert metrics.auto_vacuum_mode == 0

    controller = IncrementalVacuumController()
    report = controller.run_idle_cycle(conn)
    assert report["supported"] is False
    assert report["pages_freed"] == 0
    conn.close()


def test_step_vacuum_preserves_caller_transaction():
    """Verify step_vacuum does not commit the caller's pending writes."""
    from bulk_downloader.sqlite_freelist_vacuum import IncrementalVacuumController

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("PRAGMA auto_vacuum = INCREMENTAL")
    cursor.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, data TEXT)")
    cursor.executemany(
        "INSERT INTO t (data) VALUES (?)", [("Y" * 1000,) for _ in range(500)]
    )
    conn.commit()
    cursor.execute("DELETE FROM t WHERE id <= 400")
    conn.commit()

    fl_before = cursor.execute("PRAGMA freelist_count").fetchone()[0]
    assert fl_before > 0, "positive control: freelist exists"

    conn.execute("INSERT INTO t (data) VALUES ('SENTINEL-UNCOMMITTED')")

    ctl = IncrementalVacuumController(pages_per_step=50)
    freed = ctl.step_vacuum(conn, pages=50)
    assert freed > 0, "positive control: pages were freed"

    conn.rollback()

    count = conn.execute(
        "SELECT count(*) FROM t WHERE data = 'SENTINEL-UNCOMMITTED'"
    ).fetchone()[0]
    assert count == 0, (
        f"step_vacuum must not commit caller's pending writes, but SENTINEL survived rollback (count={count})"
    )
    conn.close()


def test_db_maintenance_integration():
    """Verify db_maintenance module integrates idle freelist maintenance."""
    from bulk_downloader import db_maintenance

    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()
    cursor.execute("PRAGMA auto_vacuum = INCREMENTAL")
    cursor.execute("CREATE TABLE blobs (id INT, data BLOB)")
    cursor.executemany("INSERT INTO blobs VALUES (?, ?)", [(i, b"0" * 4096) for i in range(200)])
    conn.commit()
    cursor.execute("DELETE FROM blobs WHERE id <= 100")
    conn.commit()

    report = db_maintenance.run_idle_freelist_maintenance(conn, max_duration_seconds=0.5)
    assert isinstance(report, dict)
    assert report["pages_freed"] > 0
    assert report["final_freelist"] < report["initial_freelist"]
    conn.close()


def test_db_init_enables_incremental_auto_vacuum(tmp_path, monkeypatch):
    """Verify db_init() automatically upgrades database to auto_vacuum=INCREMENTAL (O1238)."""
    from bulk_downloader import db

    db_file = tmp_path / "test_init_autovacuum.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))

    # Create initial DB with auto_vacuum = 0 (NONE)
    raw_cx = sqlite3.connect(str(db_file))
    raw_cx.execute("CREATE TABLE dummy (x INT)")
    raw_cx.commit()
    raw_cx.close()

    check_cx = sqlite3.connect(str(db_file))
    assert check_cx.execute("PRAGMA auto_vacuum").fetchone()[0] == 0
    check_cx.close()

    # Run production db_init()
    db.db_init()

    # Confirm auto_vacuum was upgraded to 2 (INCREMENTAL)
    upgraded_cx = sqlite3.connect(str(db_file))
    mode = upgraded_cx.execute("PRAGMA auto_vacuum").fetchone()[0]
    upgraded_cx.close()
    assert mode == 2, f"db_init must enable auto_vacuum=INCREMENTAL (mode 2), got {mode}"


def test_bg_scheduler_idle_freelist_maintenance_task(tmp_path, monkeypatch):
    """Verify bg_scheduler registers and executes sqlite.idle_freelist_maintenance task."""
    from bulk_downloader import bg_scheduler, db

    db_file = tmp_path / "test_scheduler_maint.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    monkeypatch.setattr(bg_scheduler, "is_idle", lambda: True)

    # Initialize DB (which sets auto_vacuum=2)
    db.db_init()

    # Populate freelist pages
    with db.db_conn() as cx:
        cx.executemany(
            "INSERT INTO history (site_id, url, status, message) VALUES (?, ?, 'done', ?)",
            [(f"s{i}", f"http://example.com/{i}", "X" * 2000) for i in range(100)],
        )
    with db.db_conn() as cx:
        cx.execute("DELETE FROM history WHERE id < 80")

    with db.db_conn() as cx:
        fl_before = cx.execute("PRAGMA freelist_count").fetchone()[0]
    assert fl_before > 0, "positive control: freelist has pages"

    # Register default background tasks
    bg_scheduler.register_default_tasks()
    with bg_scheduler._lock:
        task = bg_scheduler._tasks.get("sqlite.idle_freelist_maintenance")

    assert task is not None, "sqlite.idle_freelist_maintenance must be registered"
    assert callable(task["fn"])

    # Execute task through background scheduler mechanism
    task["fn"]()

    with db.db_conn() as cx:
        fl_after = cx.execute("PRAGMA freelist_count").fetchone()[0]
    assert fl_after < fl_before, f"scheduler task must reclaim freelist: {fl_before} -> {fl_after}"


def test_db_vacuum_product_path(tmp_path, monkeypatch):
    """Verify the operator vacuum entry point db_vacuum succeeds after a prune (full VACUUM, RULING-2200)."""
    from bulk_downloader import db

    db_file = tmp_path / "test_db_vacuum.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))

    db.db_init()
    with db.db_conn() as cx:
        cx.executemany(
            "INSERT INTO history (site_id, url, status) VALUES (?, ?, 'done')",
            [(f"site{i}", f"url{i}") for i in range(50)],
        )
    with db.db_conn() as cx:
        cx.execute("DELETE FROM history WHERE id < 30")

    ok = db.db_vacuum()
    assert ok is True
