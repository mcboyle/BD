from __future__ import annotations
import sqlite3
VERSION = 10
NAME = "add_library_title_source"

def up(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(library)").fetchall()}
    if "title_source" not in cols:
        cx.execute("ALTER TABLE library ADD COLUMN title_source TEXT DEFAULT ''")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_library_title_source_archive" in tables:
        cx.execute("""
            UPDATE library
            SET title_source = (
                SELECT title_source FROM _downgrade_library_title_source_archive
                WHERE _downgrade_library_title_source_archive.id = library.id
            )
            WHERE id IN (SELECT id FROM _downgrade_library_title_source_archive)
        """)

def down(cx: sqlite3.Connection) -> None:
    cols = {r[1] for r in cx.execute("PRAGMA table_info(library)").fetchall()}
    if "title_source" not in cols:
        return
    cx.execute("""
        CREATE TABLE IF NOT EXISTS _downgrade_library_title_source_archive (
            id INTEGER PRIMARY KEY,
            title_source TEXT,
            archived_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    cx.execute("""
        INSERT OR REPLACE INTO _downgrade_library_title_source_archive (id, title_source)
        SELECT id, title_source FROM library WHERE title_source != ''
    """)
    try:
        cx.execute("ALTER TABLE library DROP COLUMN title_source")
    except Exception:
        cols_keep = [c for c in cols if c != "title_source"]
        cols_csv = ", ".join((f'"{c}"' for c in cols_keep))
        cx.execute(f"CREATE TABLE library_recreate_tmp AS SELECT {cols_csv} FROM library WHERE 0")
        cx.execute(f"INSERT INTO library_recreate_tmp ({cols_csv}) SELECT {cols_csv} FROM library")
        cx.execute("DROP TABLE library")
        cx.execute("ALTER TABLE library_recreate_tmp RENAME TO library")
