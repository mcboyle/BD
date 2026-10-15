from __future__ import annotations
import sqlite3
VERSION = 8
NAME = "add_history_bytes_fetched"

def up(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "bytes_fetched" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN bytes_fetched INTEGER DEFAULT NULL")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_bytes_archive" in tables:
        cx.execute("""
            UPDATE history
            SET bytes_fetched = (
                SELECT bytes_fetched FROM _downgrade_history_bytes_archive
                WHERE _downgrade_history_bytes_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_bytes_archive)
        """)

def down(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "bytes_fetched" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_bytes_archive (
            id INTEGER PRIMARY KEY,
            bytes_fetched INTEGER,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_bytes_archive (id, bytes_fetched)
        SELECT id, bytes_fetched FROM history WHERE bytes_fetched IS NOT NULL
    """)
    try:
        cx.execute("ALTER TABLE history DROP COLUMN bytes_fetched")
    except Exception:
        cols_keep = [c for c in cols if c != "bytes_fetched"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")
