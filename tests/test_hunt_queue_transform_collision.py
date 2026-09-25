"""A skipped URL rewrite must leave both queue copies intact."""

import sqlite3
import threading
from contextlib import contextmanager
from types import SimpleNamespace

from bulk_downloader import db
from bulk_downloader.runner_queue import QueueMixin

BD_GATE_SCOPE = "module"


def test_bulk_transform_persists_only_applied_rewrites(monkeypatch):
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE queue (site_id TEXT, url TEXT, UNIQUE(site_id, url))")
    for url in ("https://x/a", "https://x/b", "https://x/c"):
        cx.execute("INSERT INTO queue VALUES (?, ?)", ("site", url))
    cx.commit()

    @contextmanager
    def local_db_conn():
        yield cx
        cx.commit()

    monkeypatch.setattr(db, "db_conn", local_db_conn)
    runner = SimpleNamespace(
        _lock=threading.RLock(),
        jobs={url: {"status": "pending"} for url in ("https://x/a", "https://x/b", "https://x/c")},
        urls=["https://x/a", "https://x/b", "https://x/c"],
        site_id="site",
        log=SimpleNamespace(error=lambda *args: None),
        log_event=lambda *args, **kwargs: None,
    )
    count = QueueMixin.bulk_url_transform(
        runner,
        [("https://x/a", "https://x/b"), ("https://x/c", "https://x/d")],
    )

    assert count == 1
    expected = {"https://x/a", "https://x/b", "https://x/d"}
    assert set(runner.jobs) == expected
    assert set(runner.urls) == expected
    assert {row[0] for row in cx.execute("SELECT url FROM queue WHERE site_id = 'site'")} == expected


def test_bulk_transform_drops_old_row_when_db_already_holds_target(monkeypatch):
    """Memory accepts A->B (B not in memory) but the table already holds B: A must not survive in SQLite,
    or it comes back as a second job on reload."""
    cx = sqlite3.connect(":memory:")
    cx.execute("CREATE TABLE queue (site_id TEXT, url TEXT, UNIQUE(site_id, url))")
    for url in ("https://x/a", "https://x/b"):
        cx.execute("INSERT INTO queue VALUES (?, ?)", ("site", url))
    cx.commit()

    @contextmanager
    def local_db_conn():
        yield cx
        cx.commit()

    monkeypatch.setattr(db, "db_conn", local_db_conn)
    runner = SimpleNamespace(
        _lock=threading.RLock(),
        jobs={"https://x/a": {"status": "pending"}},
        urls=["https://x/a"],
        site_id="site",
        log=SimpleNamespace(error=lambda *args: None),
        log_event=lambda *args, **kwargs: None,
    )
    assert QueueMixin.bulk_url_transform(runner, [("https://x/a", "https://x/b")]) == 1
    assert set(runner.jobs) == {"https://x/b"}
    assert {row[0] for row in cx.execute("SELECT url FROM queue WHERE site_id = 'site'")} == {"https://x/b"}
