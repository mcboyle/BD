from __future__ import annotations
import sqlite3
VERSION = 4
NAME = "create_tags_tables"

def up(cx: sqlite3.Connection) -> None:
    cx.execute("""CREATE TABLE IF NOT EXISTS tags(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        color TEXT DEFAULT '',
        description TEXT DEFAULT '',
        created_at REAL NOT NULL DEFAULT 0
    )""")
    cx.execute("""CREATE TABLE IF NOT EXISTS library_tags(
        library_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        added_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (library_id, tag_id),
        FOREIGN KEY (library_id) REFERENCES library(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
    )""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_libtag_tag ON library_tags(tag_id)")
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "_downgrade_tags_archive" in tables:
        cx.execute("INSERT OR IGNORE INTO tags SELECT * FROM _downgrade_tags_archive")
    if "_downgrade_library_tags_archive" in tables:
        cx.execute("INSERT OR IGNORE INTO library_tags SELECT * FROM _downgrade_library_tags_archive")

def down(cx: sqlite3.Connection) -> None:
    tables = {r[0] for r in cx.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "tags" in tables:
        cx.execute("CREATE TABLE IF NOT EXISTS _downgrade_tags_archive AS SELECT * FROM tags")
    if "library_tags" in tables:
        cx.execute("CREATE TABLE IF NOT EXISTS _downgrade_library_tags_archive AS SELECT * FROM library_tags")
    cx.execute("DROP INDEX IF EXISTS idx_libtag_tag")
    cx.execute("DROP TABLE IF EXISTS library_tags")
    cx.execute("DROP TABLE IF EXISTS tags")
