"""Malformed bulk limits must not select every history row."""

import sqlite3
from contextlib import contextmanager

from bulk_downloader import batch_ops, db

BD_GATE_SCOPE = "module"


def test_negative_bulk_limit_selects_no_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    with sqlite3.connect(db_path) as cx:
        cx.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, file_size INTEGER)")
        cx.executemany("INSERT INTO history VALUES (?, 1)", [(1,), (2,), (3,)])

    @contextmanager
    def connection():
        cx = sqlite3.connect(db_path)
        cx.row_factory = sqlite3.Row
        try:
            yield cx
        finally:
            cx.close()

    monkeypatch.setattr(db, "db_conn", connection)
    bounded = batch_ops.bulk_delete({"limit": 1}, dry_run=True)
    invalid = batch_ops.bulk_delete({"limit": -1}, dry_run=True)

    assert bounded["candidates_matched"] == 1
    assert invalid["candidates_matched"] == 0
    assert invalid["processed"] == 0


def test_negative_dedup_scan_limit_is_refused(tmp_path, monkeypatch):
    db_path = tmp_path / "history.sqlite"
    with sqlite3.connect(db_path) as cx:
        cx.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, site_id TEXT, "
                   "filename TEXT, file_size INTEGER, status TEXT)")
        cx.executemany("INSERT INTO history VALUES (?, 's', ?, ?, 'done')",
                       [(i, f"/nonexistent/{i}", 1) for i in (1, 2, 3)])

    @contextmanager
    def connection():
        cx = sqlite3.connect(db_path)
        cx.row_factory = sqlite3.Row
        try:
            yield cx
        finally:
            cx.close()

    monkeypatch.setattr(db, "db_conn", connection)
    bounded = batch_ops.bulk_dedup_scan(min_file_size_mb=0, limit=1)
    invalid = batch_ops.bulk_dedup_scan(min_file_size_mb=0, limit=-1)

    assert bounded["total_files_scanned"] == 1
    assert invalid.get("error") and invalid["duplicate_groups"] == []
    assert "total_files_scanned" not in invalid
