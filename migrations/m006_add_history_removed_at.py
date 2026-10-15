from __future__ import annotations
import sqlite3
VERSION = 6
NAME = "add_history_removed_at"

def up(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "removed_at" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN removed_at REAL DEFAULT NULL")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_removed_at_archive" in tables:
        cx.execute("""
            UPDATE history
            SET removed_at = (
                SELECT removed_at FROM _downgrade_history_removed_at_archive
                WHERE _downgrade_history_removed_at_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_removed_at_archive)
        """)

def down(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "removed_at" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_removed_at_archive (
            id INTEGER PRIMARY KEY,
            removed_at REAL,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_removed_at_archive (id, removed_at)
        SELECT id, removed_at FROM history WHERE removed_at IS NOT NULL
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN removed_at")
    except Exception:
        cols_keep = [c for c in cols if c != "removed_at"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")
