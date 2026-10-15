from __future__ import annotations
import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from bulk_downloader import db
from bulk_downloader import migrations as mg
BD_GATE_SCOPE = "repo-wide"

def _create_isolated_db() -> tuple[str, str]:
    d = tempfile.mkdtemp(prefix="test_migration_reversibility_")
    db_path = os.path.join(d, "test_history.db")
    return (d, db_path)

def _get_table_columns(cx: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = cx.execute(f'PRAGMA table_info("{table}")').fetchall()
        return {r[1] for r in rows}
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return set()

def _get_table_indexes(cx: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = cx.execute(f'PRAGMA index_list("{table}")').fetchall()
        return {r[1] for r in rows}
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return set()

def _get_all_tables(cx: sqlite3.Connection) -> set[str]:
    rows = cx.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return {r[0] for r in rows}

def verify_registry_integrity() -> None:
    assert len(mg._MIGRATIONS) >= 11, f"Expected at least 11 registered migrations, found {len(mg._MIGRATIONS)}"
    missing_downgrades: list[str] = []
    for m in mg._MIGRATIONS:
        v = m.get("version")
        name = m.get("name")
        down_fn = m.get("down_fn")
        if not callable(down_fn):
            missing_downgrades.append(f"v{v}: {name}")
    if missing_downgrades:
        formatted = "\n".join((f"  - {item}" for item in missing_downgrades))
        raise AssertionError("The following migrations lack an explicit downgrade step (down_fn):\n" + formatted)
    mig_dir = ROOT / "migrations"
    assert mig_dir.is_dir(), f"migrations/ directory does not exist at {mig_dir}"
    mig_files = sorted(mig_dir.glob("m*.py"))
    assert len(mig_files) >= 11, f"Expected at least 11 migration modules in migrations/, found {len(mig_files)}"

def seed_test_data(cx: sqlite3.Connection) -> dict[str, Any]:
    cx.execute("""
        INSERT INTO history (id, site_id, site_name, url, status, filename, file_size, message, ts)
        VALUES (101, 'site_a', 'Site Alpha', 'https://example.com/item1', 'done', 'item1.mp4', 1048576, 'OK', 1700000000.0)
    """)
    cx.execute("""
        INSERT INTO history (id, site_id, site_name, url, status, filename, file_size, message, ts)
        VALUES (102, 'site_b', 'Site Beta', 'https://example.com/item2', 'done', 'item2.mp4', 2097152, 'OK', 1700000100.0)
    """)
    cols = _get_table_columns(cx, "history")
    if "retry_after" in cols:
        cx.execute("UPDATE history SET retry_after = '2026-12-31T23:59:59Z' WHERE id = 101")
    if "library_id" in cols:
        cx.execute("UPDATE history SET library_id = 501 WHERE id = 101")
    if "removed_at" in cols:
        cx.execute("UPDATE history SET removed_at = 1700000500.0 WHERE id = 102")
    if "honeypot_score" in cols:
        cx.execute("UPDATE history SET honeypot_score = 0.85 WHERE id = 101")
    if "bytes_fetched" in cols:
        cx.execute("UPDATE history SET bytes_fetched = 1048576 WHERE id = 101")
    if "transfer_mode" in cols:
        cx.execute("UPDATE history SET transfer_mode = 'segmented' WHERE id = 101")
    if "egress_ip" in cols:
        cx.execute("UPDATE history SET egress_ip = '10.0.70.164' WHERE id = 101")
    tables = _get_all_tables(cx)
    if "library" in tables:
        cx.execute("""
            INSERT OR REPLACE INTO library (id, history_id, site_id, file_path, file_exists, file_size, added_at, title, title_source)
            VALUES (501, 101, 'site_a', '/media/item1.mp4', 1, 1048576, 1700000010.0, 'Alpha Video 1', 'meta_tag')
        """)
    if "tags" in tables:
        cx.execute("""
            INSERT OR REPLACE INTO tags (id, name, color, description, created_at)
            VALUES (1, 'Favorites', '#ff0000', 'Top picks', 1700000005.0)
        """)
    if "library_tags" in tables:
        cx.execute("""
            INSERT OR REPLACE INTO library_tags (library_id, tag_id, added_at)
            VALUES (501, 1, 1700000015.0)
        """)
    if "queue" in tables:
        cx.execute("""
            INSERT OR REPLACE INTO queue (site_id, url, status, ord, ts_added)
            VALUES ('site_a', 'https://example.com/queue1', 'pending', 1, 1700000020.0)
        """)
    cx.commit()
    return {"history_id": 101, "retry_after": "2026-12-31T23:59:59Z", "library_id": 501, "removed_at": 1700000500.0, "honeypot_score": 0.85, "bytes_fetched": 1048576, "transfer_mode": "segmented", "egress_ip": "10.0.70.164", "library_title": "Alpha Video 1", "library_title_source": "meta_tag", "tag_name": "Favorites"}

def run_reversibility_cycle(db_path: str | None=None, verbose: bool=False) -> None:
    verify_registry_integrity()
    temp_dir = None
    if db_path is None:
        temp_dir, db_path = _create_isolated_db()
    saved_db_path = db.DB_PATH
    try:
        db.DB_PATH = db_path
        db.db_init()
        with db.db_conn() as cx:
            base_tables = _get_all_tables(cx)
            assert "history" in base_tables, "Base history table must exist"
        out = mg.apply_pending(backup_first=False)
        if verbose:
            print(f"[step-up] applied: {out.get('applied')}, errors: {out.get('errors')}")
        assert out.get("errors", 0) == 0, f"Forward migration failed: {out}"
        applied = mg.applied_versions()
        max_v = max((m["version"] for m in mg._MIGRATIONS))
        assert max_v in applied, f"Max migration v{max_v} must be applied, got {applied}"
        with db.db_conn() as cx:
            lib_indexes = _get_table_indexes(cx, "library")
            hist_indexes = _get_table_indexes(cx, "history")
            q_indexes = _get_table_indexes(cx, "queue")
            assert "idx_lib_history_id" in lib_indexes, "idx_lib_history_id must exist on library"
            assert "idx_hist_url_status" in hist_indexes, "idx_hist_url_status must exist on history"
            assert "idx_hist_active_site_ts" in hist_indexes, "idx_hist_active_site_ts must exist on history"
            assert "idx_q_site_status_ord" in q_indexes, "idx_q_site_status_ord must exist on queue"
            assert "idx_hist_library_id" in hist_indexes, "idx_hist_library_id must exist on history"
            seeded = seed_test_data(cx)
        if verbose:
            print("[step-down] Downgrading to version 0...")
        down_out = mg.downgrade_to(target_version=0, backup_first=False)
        if verbose:
            print(f"[step-down] reverted: {down_out.get('reverted')}, errors: {down_out.get('errors')}")
        assert down_out.get("errors", 0) == 0, f"Downgrade failed: {down_out}"
        with db.db_conn() as cx:
            remaining_tables = _get_all_tables(cx)
            assert "library" not in remaining_tables, "library table must be dropped after downgrade"
            assert "tags" not in remaining_tables, "tags table must be dropped after downgrade"
            assert "library_tags" not in remaining_tables, "library_tags table must be dropped after downgrade"
            assert "_downgrade_library_archive" in remaining_tables, "library shadow archive must exist"
            assert "_downgrade_tags_archive" in remaining_tables, "tags shadow archive must exist"
            assert "_downgrade_history_retry_after_archive" in remaining_tables, "retry_after shadow archive must exist"
            assert "_downgrade_history_library_id_archive" in remaining_tables, "library_id shadow archive must exist"
            assert "_downgrade_history_removed_at_archive" in remaining_tables, "removed_at shadow archive must exist"
            assert "_downgrade_history_honeypot_archive" in remaining_tables, "honeypot shadow archive must exist"
            assert "_downgrade_history_bytes_archive" in remaining_tables, "bytes shadow archive must exist"
            assert "_downgrade_history_transfer_mode_archive" in remaining_tables, "transfer_mode shadow archive must exist"
            assert "_downgrade_library_title_source_archive" in remaining_tables, "title_source shadow archive must exist"
            assert "_downgrade_history_egress_ip_archive" in remaining_tables, "egress_ip shadow archive must exist"
            archived_egress = cx.execute("SELECT egress_ip FROM _downgrade_history_egress_ip_archive WHERE id = ?", (seeded["history_id"],)).fetchone()
            assert archived_egress and archived_egress[0] == seeded["egress_ip"], f"Archived egress_ip mismatch: {archived_egress}"
            archived_retry = cx.execute("SELECT retry_after FROM _downgrade_history_retry_after_archive WHERE id = ?", (seeded["history_id"],)).fetchone()
            assert archived_retry and archived_retry[0] == seeded["retry_after"], f"Archived retry_after mismatch: {archived_retry}"
            hist_cols = _get_table_columns(cx, "history")
            for dropped_col in ["retry_after", "library_id", "removed_at", "honeypot_score", "bytes_fetched", "transfer_mode", "egress_ip"]:
                assert dropped_col not in hist_cols, f"Column {dropped_col} must be dropped from history"
            applied_after_down = mg.applied_versions()
            assert len(applied_after_down) == 0, f"Expected 0 applied versions, got {applied_after_down}"
        if verbose:
            print("[step-up] Re-applying migrations to verify lossless restoration...")
        reup_out = mg.apply_pending(backup_first=False)
        assert reup_out.get("errors", 0) == 0, f"Re-application failed: {reup_out}"
        with db.db_conn() as cx:
            restored_hist = cx.execute("SELECT retry_after, egress_ip, transfer_mode, bytes_fetched FROM history WHERE id = ?", (seeded["history_id"],)).fetchone()
            assert restored_hist is not None, "History row 101 must exist"
            assert restored_hist[0] == seeded["retry_after"], f"retry_after not restored: {restored_hist[0]}"
            assert restored_hist[1] == seeded["egress_ip"], f"egress_ip not restored: {restored_hist[1]}"
            assert restored_hist[2] == seeded["transfer_mode"], f"transfer_mode not restored: {restored_hist[2]}"
            assert restored_hist[3] == seeded["bytes_fetched"], f"bytes_fetched not restored: {restored_hist[3]}"
            restored_lib = cx.execute("SELECT title, title_source FROM library WHERE id = ?", (seeded["library_id"],)).fetchone()
            assert restored_lib is not None, "Library row 501 must be restored"
            assert restored_lib[0] == seeded["library_title"], f"Library title not restored: {restored_lib[0]}"
            assert restored_lib[1] == seeded["library_title_source"], f"Library title_source not restored: {restored_lib[1]}"
        if verbose:
            print("[SUCCESS] All reversibility and data preservation tests passed!")
    finally:
        db.close_all_pooled_connections()
        db.DB_PATH = saved_db_path
        if temp_dir and os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

def test_migration_reversibility_pytest():
    run_reversibility_cycle(verbose=False)

def main() -> None:
    parser = argparse.ArgumentParser(description="Migration Reversibility Test Harness")
    parser.add_argument("--step-up", action="store_true", help="Test step-up migration application")
    parser.add_argument("--step-down", action="store_true", help="Test step-down migration rollback")
    parser.add_argument("--db-path", type=str, default=None, help="Path to test SQLite DB")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()
    run_reversibility_cycle(db_path=args.db_path, verbose=args.verbose)
    print("MIGRATION_REVERSIBILITY_PASS: All step-up and step-down verifications passed.")
if __name__ == "__main__":
    main()
