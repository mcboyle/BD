from __future__ import annotations
import sqlite3
VERSION = 3
NAME = "create_library_table"

def up(cx: sqlite3.Connection) -> None:
    cx.execute("""CREATE TABLE IF NOT EXISTS library(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        history_id INTEGER,
        site_id TEXT,
        file_path TEXT NOT NULL UNIQUE,
        file_exists INTEGER NOT NULL DEFAULT 1,
        file_size INTEGER DEFAULT 0,
        file_mtime REAL DEFAULT 0,
        last_scanned REAL DEFAULT 0,
        watched INTEGER NOT NULL DEFAULT 0,
        watched_at REAL DEFAULT NULL,
        cover_thumb TEXT DEFAULT '',
        duration_s REAL DEFAULT NULL,
        resolution TEXT DEFAULT '',
        codec TEXT DEFAULT '',
        performer TEXT DEFAULT '',
        studio TEXT DEFAULT '',
        year INTEGER DEFAULT NULL,
        title TEXT DEFAULT '',
        title_source TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        rating INTEGER DEFAULT NULL,
        added_at REAL NOT NULL DEFAULT 0,
        FOREIGN KEY(history_id) REFERENCES history(id) ON DELETE SET NULL
    )""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_site ON library(site_id)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_watched ON library(watched)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_studio ON library(studio)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_performer ON library(performer)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_year ON library(year)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_added_at ON library(added_at DESC)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_rating ON library(rating)")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_lib_exists ON library(file_exists)")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_library_archive" in tables:
        lib_cols = [r[1] for r in cx.execute("PRAGMA table_info(library)").fetchall()]
        arch_cols = [r[1] for r in cx.execute("PRAGMA table_info(_downgrade_library_archive)").fetchall()]
        common = [c for c in lib_cols if c in arch_cols]
        if common:
            cols_csv = ", ".join((f'"{c}"' for c in common))
            cx.execute(f"INSERT OR IGNORE INTO library ({cols_csv}) SELECT {cols_csv} FROM _downgrade_library_archive")

def down(cx: sqlite3.Connection) -> None:
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "library" not in tables:
        return
    cx.execute("CREATE TABLE IF NOT EXISTS _downgrade_library_archive AS SELECT * FROM library")
    for idx in ["idx_lib_site", "idx_lib_watched", "idx_lib_studio", "idx_lib_performer", "idx_lib_year", "idx_lib_added_at", "idx_lib_rating", "idx_lib_exists", "idx_lib_history_id"]:
        cx.execute(f"DROP INDEX IF EXISTS {idx}")
    cx.execute("DROP TABLE IF EXISTS library")
