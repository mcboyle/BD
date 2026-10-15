from __future__ import annotations
import sqlite3
VERSION = 9
NAME = "add_history_transfer_mode"

def up(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "transfer_mode" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN transfer_mode TEXT DEFAULT NULL")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_transfer_mode_archive" in tables:
        cx.execute("""
            UPDATE history
            SET transfer_mode = (
                SELECT transfer_mode FROM _downgrade_history_transfer_mode_archive
                WHERE _downgrade_history_transfer_mode_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_transfer_mode_archive)
        """)

def down(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "transfer_mode" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_transfer_mode_archive (
            id INTEGER PRIMARY KEY,
            transfer_mode TEXT,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_transfer_mode_archive (id, transfer_mode)
        SELECT id, transfer_mode FROM history WHERE transfer_mode IS NOT NULL
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN transfer_mode")
    except Exception:
        cols_keep = [c for c in cols if c != "transfer_mode"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")
