import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from bulk_downloader import pg_backend

BD_GATE_SCOPE = "module"

NAMES = ("plain_table", "select", "table name", "dash-name", 'say"hello',
         'evil"); DROP TABLE plain_table; --')
EXPECTED = [("id", "INTEGER", 0, None, 1),
            ("payload", "TEXT", 1, "'fixture'", 0)]


@pytest.fixture(params=[False, True], ids=["raw", "proxy"])
def database(request, monkeypatch):
    from bulk_downloader import db

    raw = sqlite3.connect(":memory:")
    for name in NAMES:
        identifier = '"' + name.replace('"', '""') + '"'
        raw.execute(f"CREATE TABLE {identifier} (id INTEGER PRIMARY KEY, "
                    "payload TEXT NOT NULL DEFAULT 'fixture')")
        raw.execute(f"INSERT INTO {identifier} (id) VALUES (7)")

    @contextmanager
    def connection():
        yield SimpleNamespace(_cx=raw) if request.param else raw

    monkeypatch.setattr(db, "db_conn", connection)
    try:
        yield raw
    finally:
        raw.close()


@pytest.mark.parametrize("name", NAMES)
def test_schema_metadata_preserves_literal_table_name(database, name):
    assert len(database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) == len(NAMES)
    columns, error = pg_backend._sqlite_columns([name])
    assert error is None, f"SQLITE_PRAGMA_IDENTIFIER: metadata lookup failed for {name!r}: {error}"
    assert columns == {name: EXPECTED}
    assert database.execute('SELECT id, payload FROM plain_table').fetchall() == [(7, "fixture")]


def test_missing_table_retains_empty_metadata(database):
    columns, error = pg_backend._sqlite_columns(["absent_table", "plain_table"])
    assert error is None
    assert columns == {"absent_table": [], "plain_table": EXPECTED}
