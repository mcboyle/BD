from __future__ import annotations
import sqlite3
VERSION = 5
NAME = "add_history_library_back_ref"

def up(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "library_id" not in cols:
        cx.execute("ALTER TABLE history ADD COLUMN library_id INTEGER DEFAULT NULL")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_history_lib ON history(library_id)")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_history_library_id_archive" in tables:
        cx.execute("""
            UPDATE history
            SET library_id = (
                SELECT library_id FROM _downgrade_history_library_id_archive
                WHERE _downgrade_history_library_id_archive.id = history.id
            )
            WHERE id IN (SELECT id FROM _downgrade_history_library_id_archive)
        """)

def down(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(history)").fetchall()}
    if "library_id" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_history_library_id_archive (
            id INTEGER PRIMARY KEY,
            library_id INTEGER,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_history_library_id_archive (id, library_id)
        SELECT id, library_id FROM history WHERE library_id IS NOT NULL
    """)
    cx.execute("DROP INDEX IF EXISTS idx_history_lib")
    try:
        cx.execute("ALTER TABLE history DROP COLUMN library_id")
    except Exception:
        cols_keep = [c for c in cols if c != "library_id"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE history_recreate_tmp AS SELECT {cols_csv} FROM history WHERE 0")
        cx.execute(f"INSERT INTO history_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM history")
        cx.execute("DROP TABLE history")
        cx.execute("ALTER TABLE history_recreate_tmp RENAME TO history")
