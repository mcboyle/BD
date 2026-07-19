"""queue_load must not 500 when the `queue` table does not exist yet.

Footgun #1 (add-site 500): ``_create_site`` constructs a ``SiteRunner`` (which
calls ``queue_load``) before the queue schema is guaranteed to exist. Under
stale bytecode or a first-call ordering where ``db_init`` has not run, the
``SELECT * FROM queue`` raises ``sqlite3.OperationalError("no such table:
queue")`` and the add-site POST returns HTTP 500. ``queue_load`` should instead
lazily ensure the schema and return ``[]`` for a brand-new table.

This test deliberately does NOT request the ``fresh_app`` fixture (which would
run ``db_init``). The autouse ``clean_workdir`` fixture gives it a fresh, empty
working directory, so the ``queue`` table genuinely does not exist -- the exact
condition the bug needs.
"""

from __future__ import annotations

from bulk_downloader import db


def test_queue_load_tolerates_missing_queue_table():
    # No db_init() / fresh_app: the queue table does not exist in this fresh
    # workdir. Pristine source raises OperationalError here; the fix returns [].
    rows = db.queue_load("site-that-does-not-exist")
    assert rows == []


def test_queue_load_self_heals_schema_then_persists():
    # After the tolerant first call, the schema exists and a normal round-trip
    # works -- i.e. the heal created the real table, not a throwaway.
    assert db.queue_load("brand-new-site") == []
    db.queue_upsert("brand-new-site", "https://example.com/v/1")
    rows = db.queue_load("brand-new-site")
    assert [r["url"] for r in rows] == ["https://example.com/v/1"]
