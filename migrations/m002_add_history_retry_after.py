from __future__ import annotations
import sqlite3
VERSION = 2
NAME = "add_history_retry_after"

def up(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "retry_after" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN retry_after TEXT DEFAULT NULL")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_retry_after_archive" in tables:
        cx.execute("""
            UPDATE history
            SET retry_after = (
                SELECT retry_after FROM _downgrade_history_retry_after_archive
                WHERE _downgrade_history_retry_after_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_retry_after_archive)
        """)

def down(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "retry_after" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_retry_after_archive (
            id INTEGER PRIMARY KEY,
            retry_after TEXT,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_retry_after_archive (id, retry_after)
        SELECT id, retry_after FROM history WHERE retry_after IS NOT NULL
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN retry_after")
    except Exception:
        cols_keep = [c for c in cols if c != "retry_after"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")
