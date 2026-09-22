"""Row 1013: Ephemeral In-Memory Hot Write Buffer for High-Frequency Queue State.

Validates in-memory queue state buffering, update coalescing, time and capacity-based
flush triggers, terminal state auto-flush, dirty state tracking, point-in-time read consistency,
three-state buffer health (O1224), and active caller integration in bulk_downloader.db.

RED on baseline: bulk_downloader.hot_write_buffer does not exist.
"""
from __future__ import annotations

import time

import pytest

BD_GATE_SCOPE = "module"


def test_module_exports():
    """RED assertion 1: Base must provide ephemeral in-memory hot write buffer."""
    try:
        from bulk_downloader import hot_write_buffer
    except ImportError:
        pytest.fail("Base lacks ephemeral in-memory hot write buffer for high-frequency queue state (bulk_downloader.hot_write_buffer)")

    assert hasattr(hot_write_buffer, "BufferHealthState")
    assert hasattr(hot_write_buffer, "BufferHealthResult")
    assert hasattr(hot_write_buffer, "QueueItemState")
    assert hasattr(hot_write_buffer, "BufferFlushPolicy")
    assert hasattr(hot_write_buffer, "HotWriteBuffer")
    assert hasattr(hot_write_buffer, "create_hot_write_buffer")


def test_hot_write_buffer_coalescing():
    """Verify multiple rapid updates to the same queue item coalesce into a single dirty state."""
    from bulk_downloader.hot_write_buffer import HotWriteBuffer

    flushed_records: list[dict] = []
    buf = HotWriteBuffer(flush_sink=lambda items: flushed_records.extend(items))

    # Rapid progress updates for item 101
    buf.record_update(item_id="101", state="downloading", progress_percent=10.0, bytes_downloaded=1000)
    buf.record_update(item_id="101", state="downloading", progress_percent=25.0, bytes_downloaded=2500)
    buf.record_update(item_id="101", state="downloading", progress_percent=50.0, bytes_downloaded=5000)

    # In-memory overlay reflects latest state
    item = buf.get_state("101")
    assert item is not None
    assert item.progress_percent == 50.0
    assert item.bytes_downloaded == 5000

    # Only 1 dirty item pending
    assert buf.dirty_count == 1
    assert len(flushed_records) == 0

    # Flush emits single coalesced item
    flushed_count = buf.flush()
    assert flushed_count == 1
    assert len(flushed_records) == 1
    assert flushed_records[0]["item_id"] == "101"
    assert flushed_records[0]["progress_percent"] == 50.0
    assert buf.dirty_count == 0


def test_capacity_flush_trigger():
    """Verify buffer automatically triggers flush when reaching max_buffer_size threshold."""
    from bulk_downloader.hot_write_buffer import BufferFlushPolicy, HotWriteBuffer

    flushed_batches: list[list[dict]] = []
    policy = BufferFlushPolicy(max_buffer_size=3, auto_flush_terminal=False)
    buf = HotWriteBuffer(policy=policy, flush_sink=lambda items: flushed_batches.append(items))

    buf.record_update("item-1", state="downloading", progress_percent=10.0)
    buf.record_update("item-2", state="downloading", progress_percent=20.0)
    assert len(flushed_batches) == 0

    # 3rd item triggers capacity flush
    buf.record_update("item-3", state="downloading", progress_percent=30.0)
    assert len(flushed_batches) == 1
    assert len(flushed_batches[0]) == 3
    assert buf.dirty_count == 0


def test_terminal_state_immediate_flush():
    """Verify terminal status (completed, failed, cancelled) immediately flushes to ensure durability."""
    from bulk_downloader.hot_write_buffer import BufferFlushPolicy, HotWriteBuffer

    flushed_items: list[dict] = []
    policy = BufferFlushPolicy(max_buffer_size=100, auto_flush_terminal=True)
    buf = HotWriteBuffer(policy=policy, flush_sink=lambda items: flushed_items.extend(items))

    buf.record_update("job-42", state="downloading", progress_percent=99.0)
    assert len(flushed_items) == 0

    # Transitioning to completed triggers auto-flush
    buf.record_update("job-42", state="completed", progress_percent=100.0, is_terminal=True)
    assert len(flushed_items) == 1
    assert flushed_items[0]["item_id"] == "job-42"
    assert flushed_items[0]["state"] == "completed"
    assert buf.dirty_count == 0


def test_time_based_flush_staleness():
    """Verify check_flush_staleness triggers flush after flush_interval_seconds elapses."""
    from bulk_downloader.hot_write_buffer import BufferFlushPolicy, HotWriteBuffer

    flushed_items: list[dict] = []
    policy = BufferFlushPolicy(flush_interval_seconds=0.05)
    buf = HotWriteBuffer(policy=policy, flush_sink=lambda items: flushed_items.extend(items))

    buf.record_update("job-99", state="downloading", progress_percent=5.0)
    assert len(flushed_items) == 0

    # Immediate staleness check does nothing
    buf.flush_if_stale()
    assert len(flushed_items) == 0

    # Wait for interval to pass
    time.sleep(0.06)
    buf.flush_if_stale()
    assert len(flushed_items) == 1
    assert flushed_items[0]["item_id"] == "job-99"


def test_fallback_store_read_consistency():
    """Verify get_state consults in-memory overlay first, falling back to persistent reader."""
    from bulk_downloader.hot_write_buffer import HotWriteBuffer

    persisted_db = {
        "job-1": {"item_id": "job-1", "state": "queued", "progress_percent": 0.0},
        "job-2": {"item_id": "job-2", "state": "queued", "progress_percent": 0.0},
    }

    buf = HotWriteBuffer(fallback_reader=lambda item_id: persisted_db.get(item_id))

    # job-1 is in hot buffer
    buf.record_update("job-1", state="downloading", progress_percent=45.0)

    # Reads hot overlay
    st1 = buf.get_state("job-1")
    assert st1 is not None
    assert st1.state == "downloading"
    assert st1.progress_percent == 45.0

    # Reads fallback store
    st2 = buf.get_state("job-2")
    assert st2 is not None
    assert st2.state == "queued"
    assert st2.progress_percent == 0.0

    # Non-existent item
    assert buf.get_state("job-unknown") is None


def test_context_manager_clean_flush_on_exit():
    """Verify HotWriteBuffer flushes all pending dirty updates upon exiting context."""
    from bulk_downloader.hot_write_buffer import HotWriteBuffer

    flushed_items: list[dict] = []
    with HotWriteBuffer(flush_sink=lambda items: flushed_items.extend(items)) as buf:
        buf.record_update("task-A", state="paused", progress_percent=12.0)
        buf.record_update("task-B", state="downloading", progress_percent=78.0)
        assert len(flushed_items) == 0

    assert len(flushed_items) == 2
    ids = {it["item_id"] for it in flushed_items}
    assert ids == {"task-A", "task-B"}


def test_three_state_health_fails_closed_per_o1224():
    """Verify unmeasurable / invalid buffer policy fails closed to UNVERIFIABLE."""
    from bulk_downloader.hot_write_buffer import (
        BufferFlushPolicy,
        BufferHealthState,
        HotWriteBuffer,
    )

    buf = HotWriteBuffer()
    health = buf.check_health()
    assert health.state == BufferHealthState.HEALTHY
    assert health.is_healthy is True

    # Artificially unconfigured / invalid policy
    buf.policy = BufferFlushPolicy.__new__(BufferFlushPolicy)
    buf.policy.max_buffer_size = 0
    health_invalid = buf.check_health()
    assert health_invalid.state == BufferHealthState.UNVERIFIABLE
    assert health_invalid.is_healthy is False


def test_caller_db_queue_upsert_populates_hot_buffer(tmp_path, monkeypatch):
    """Verify production caller db.queue_upsert records state into hot write buffer."""
    import sqlite3

    from bulk_downloader import db

    db_file = str(tmp_path / "test_hot.db")
    monkeypatch.setattr(db, "DB_PATH", db_file)
    with sqlite3.connect(db_file) as cx:
        cx.execute("""CREATE TABLE queue (
            site_id TEXT, url TEXT, status TEXT, message TEXT, retries INT,
            retry_after INT, screenshot TEXT, force_download INT, priority TEXT,
            ord INT, filename TEXT, listing_title TEXT, file_size INT, lane TEXT,
            depends_on TEXT, ts_updated TEXT, PRIMARY KEY(site_id, url)
        )""")

    db.queue_upsert("site-alpha", "http://example.com/test-1", status="downloading", file_size=4096)

    hot_state = db.queue_get_hot_state("site-alpha", "http://example.com/test-1")
    assert hot_state is not None
    assert hot_state["item_id"] == "site-alpha:http://example.com/test-1"
    assert hot_state["state"] == "downloading"
    assert hot_state["bytes_downloaded"] == 4096


def test_caller_db_queue_bulk_update_populates_hot_buffer(tmp_path, monkeypatch):
    """Verify production caller db.queue_bulk_update records state into hot write buffer."""
    import sqlite3

    from bulk_downloader import db

    db_file = str(tmp_path / "test_hot_bulk.db")
    monkeypatch.setattr(db, "DB_PATH", db_file)
    with sqlite3.connect(db_file) as cx:
        cx.execute("""CREATE TABLE queue (
            site_id TEXT, url TEXT, status TEXT, message TEXT, retries INT,
            retry_after INT, screenshot TEXT, force_download INT, priority TEXT,
            ord INT, filename TEXT, listing_title TEXT, file_size INT, lane TEXT,
            depends_on TEXT, ts_updated TEXT, PRIMARY KEY(site_id, url)
        )""")
        cx.execute("INSERT INTO queue (site_id, url, status) VALUES ('site-beta', 'http://example.com/b1', 'pending')")
        cx.execute("INSERT INTO queue (site_id, url, status) VALUES ('site-beta', 'http://example.com/b2', 'pending')")

    urls = ["http://example.com/b1", "http://example.com/b2"]
    db.queue_bulk_update("site-beta", urls, status="paused", priority="high")

    st1 = db.queue_get_hot_state("site-beta", "http://example.com/b1")
    st2 = db.queue_get_hot_state("site-beta", "http://example.com/b2")
    assert st1 is not None and st1["state"] == "paused"
    assert st2 is not None and st2["state"] == "paused"


def test_caller_db_queue_terminal_status_flushes_buffer(tmp_path, monkeypatch):
    """Verify terminal status in queue_upsert marks item terminal in hot buffer."""
    import sqlite3

    from bulk_downloader import db

    db_file = str(tmp_path / "test_hot_term.db")
    monkeypatch.setattr(db, "DB_PATH", db_file)
    with sqlite3.connect(db_file) as cx:
        cx.execute("""CREATE TABLE queue (
            site_id TEXT, url TEXT, status TEXT, message TEXT, retries INT,
            retry_after INT, screenshot TEXT, force_download INT, priority TEXT,
            ord INT, filename TEXT, listing_title TEXT, file_size INT, lane TEXT,
            depends_on TEXT, ts_updated TEXT, PRIMARY KEY(site_id, url)
        )""")

    db.queue_upsert("site-alpha", "http://example.com/term-1", status="completed", file_size=10240)
    hot_state = db.queue_get_hot_state("site-alpha", "http://example.com/term-1")
    assert hot_state is not None
    assert hot_state["is_terminal"] is True


def test_caller_db_queue_flush_and_stats():
    """Verify db helper methods for flush and stats expose underlying hot buffer."""
    from bulk_downloader import db

    flushed = db.queue_flush_hot_buffer()
    assert isinstance(flushed, int)

    stats = db.queue_hot_buffer_stats()
    assert isinstance(stats, dict)
    assert "buffered_items" in stats
    assert "dirty_items" in stats
    assert "total_recorded_updates" in stats
