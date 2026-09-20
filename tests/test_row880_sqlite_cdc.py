"""Row 880: AUTOMATIC-SQLITE-TO-POSTGRES-TRANSACTIONAL-CDC-STREAM.

Acceptance criteria, one test class each:
  1. WAL change frame parsing reproduces SQLite transactions in Postgres.
  2. Zero data loss during simulated concurrent writes.
  3. Clean recovery on restart.

A real Postgres server is not assumed to be present in-sandbox (mirrors the
MOD3_PG_TEST_DSN skip convention in test_v3_66_1011_mod3_pg_isolation.py), so
(1)-(3) run against a ``FakeSink`` that mirrors ``PostgresSink``'s
``apply_events`` contract in memory -- this exercises the WAL-parsing and
diff logic exactly as the real sink would receive it. A final smoke test
exercises the real ``PostgresSink`` against SQLITE_CDC_PG_TEST_DSN when set,
and asserts the sink's fail-open contract (disabled, counted, never raising) otherwise.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import threading

import pytest

from bulk_downloader import sqlite_cdc as cdc

# Judges only bulk_downloader/sqlite_cdc.py, not a tree-wide property.
BD_GATE_SCOPE = "module"


class FakeSink:
    """In-memory stand-in for PostgresSink: same apply_events contract, no
    network. {table: {pk: row_dict}}"""

    def __init__(self):
        self.tables: dict = {}
        self.calls = 0
        self.ensured: list = []

    def ensure_table(self, table, pk, columns):
        self.ensured.append((table, pk, list(columns)))
        return True

    def apply_events(self, table, pk, events):
        self.calls += 1
        store = self.tables.setdefault(table, {})
        for ev in events:
            if ev["op"] == "delete":
                store.pop(ev["pk"], None)
            else:
                store[ev["pk"]] = dict(ev["row"])
        return True


def _make_db(path):
    cx = sqlite3.connect(path)
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA wal_autocheckpoint=0")
    cx.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, v TEXT)")
    cx.commit()
    return cx


def _current_rows(db_path, table="items", pk="id"):
    cx = sqlite3.connect(db_path)
    cx.row_factory = sqlite3.Row
    try:
        return {str(r[pk]): dict(r)
                for r in cx.execute(f"SELECT * FROM {table}").fetchall()}
    finally:
        cx.close()


# ── (1) WAL frame parsing reproduces transactions ────────────────────────

def test_wal_frame_parsing_finds_each_committed_transaction(tmp_path):
    db_path = str(tmp_path / "cdc.db")
    cx = _make_db(db_path)
    cx.execute("INSERT INTO items VALUES (1, 'a')")
    cx.commit()
    cx.execute("INSERT INTO items VALUES (2, 'b')")
    cx.commit()
    cx.execute("UPDATE items SET v = 'a2' WHERE id = 1")
    cx.commit()

    txns = list(cdc.iter_wal_transactions(db_path + "-wal"))
    # CREATE TABLE + 3 DML commits => at least 4 committed transactions.
    assert len(txns) >= 4, txns
    for t in txns:
        assert t.commit_size > 0
        assert len(t.pages) >= 1
    cx.close()


def test_cdc_stream_reproduces_sqlite_transactions_in_the_sink(tmp_path):
    db_path = str(tmp_path / "cdc.db")
    cx = _make_db(db_path)
    sink = FakeSink()
    stream = cdc.SqliteCDCStream(db_path, [("items", "id")], sink=sink,
                                  state_path=str(tmp_path / "state.json"))

    cx.execute("INSERT INTO items VALUES (1, 'a')")
    cx.commit()
    r1 = stream.poll()
    assert r1["transactions"] >= 1
    assert sink.tables["items"] == _current_rows(db_path)

    cx.execute("INSERT INTO items VALUES (2, 'b')")
    cx.execute("UPDATE items SET v = 'a2' WHERE id = 1")
    cx.commit()
    stream.poll()
    assert sink.tables["items"] == _current_rows(db_path)

    cx.execute("DELETE FROM items WHERE id = 2")
    cx.commit()
    stream.poll()
    assert sink.tables["items"] == _current_rows(db_path)
    assert "2" not in sink.tables["items"]
    cx.close()


# ── (2) zero data loss under concurrent writes ───────────────────────────

def test_zero_data_loss_during_concurrent_writes(tmp_path):
    db_path = str(tmp_path / "cdc.db")
    cx = _make_db(db_path)
    cx.close()
    sink = FakeSink()
    stream = cdc.SqliteCDCStream(db_path, [("items", "id")], sink=sink,
                                  state_path=str(tmp_path / "state.json"))

    errors = []

    def writer(worker_id, count):
        try:
            wcx = sqlite3.connect(db_path, timeout=30)
            for i in range(count):
                pk = worker_id * 1000 + i
                for attempt in range(50):
                    try:
                        wcx.execute("INSERT INTO items VALUES (?, ?)",
                                    (pk, f"w{worker_id}-{i}"))
                        wcx.commit()
                        break
                    except sqlite3.OperationalError:
                        continue
            wcx.close()
        except Exception as e:  # pragma: no cover - surfaced via errors list
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(w, 20)) for w in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors
    stream.poll()
    expected = _current_rows(db_path)
    assert len(expected) == 6 * 20
    assert sink.tables["items"] == expected


# ── (3) clean recovery on restart ────────────────────────────────────────

def test_clean_recovery_on_restart(tmp_path):
    db_path = str(tmp_path / "cdc.db")
    state_path = str(tmp_path / "state.json")
    cx = _make_db(db_path)
    sink = FakeSink()

    stream_a = cdc.SqliteCDCStream(db_path, [("items", "id")], sink=sink,
                                    state_path=state_path)
    cx.execute("INSERT INTO items VALUES (1, 'a')")
    cx.execute("INSERT INTO items VALUES (9, 'stale')")
    cx.commit()
    stream_a.poll()
    assert sink.tables["items"] == _current_rows(db_path)
    assert os.path.exists(state_path)
    with open(state_path, encoding="utf-8") as fh:
        persisted = json.load(fh)
    assert persisted["snapshots"]["items"]["1"]["v"] == "a"
    del stream_a  # simulate process exit

    # writes happen while nothing is polling: a previously-mirrored row is
    # deleted, two new rows are added, and an existing one is updated.
    cx.execute("DELETE FROM items WHERE id = 9")
    cx.execute("INSERT INTO items VALUES (2, 'b')")
    cx.execute("INSERT INTO items VALUES (3, 'c')")
    cx.commit()
    cx.execute("UPDATE items SET v = 'a2' WHERE id = 1")
    cx.commit()

    # a fresh instance ("restart") must pick up exactly the delta, with no
    # loss and no re-emission of rows already mirrored. If it failed to load
    # the persisted snapshot it would diff against an empty baseline: id=9's
    # deletion would never reach the sink (it isn't in the "old" side either)
    # and it would stay behind as a stale row forever.
    stream_b = cdc.SqliteCDCStream(db_path, [("items", "id")], sink=sink,
                                    state_path=state_path)
    result = stream_b.poll()
    assert sink.tables["items"] == _current_rows(db_path)
    assert "9" not in sink.tables["items"]
    assert result["events"] == 4  # delete(9) + insert(2) + insert(3) + update(1)
    cx.close()


# ── PostgresSink: fail-open behaviour, exercised without a live server ───

class _FakeCursorThatFails:
    """Stands in for a connected-but-misbehaving psycopg connection: the
    connect succeeds but every statement raises, exactly like a schema
    mismatch or a dropped connection mid-transaction."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *a, **k):
        raise RuntimeError("boom")

    def close(self):
        pass


def test_postgres_sink_enabled_reflects_dsn_presence():
    assert cdc.PostgresSink().enabled() is False
    assert cdc.PostgresSink(dsn="postgresql://127.0.0.1:1/x").enabled() is True


def test_postgres_sink_apply_events_fails_closed_without_a_dsn():
    sink = cdc.PostgresSink()
    ok = sink.apply_events("t", "id",
                            [{"op": "upsert", "pk": "1", "row": {"id": "1"}}])
    assert ok is False
    assert sink.stats()["failed"] == 1


def test_postgres_sink_degrades_when_psycopg_is_unavailable(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "psycopg", None)
    sink = cdc.PostgresSink(dsn="postgresql://ignored")
    assert sink._connect() is None
    assert "psycopg unavailable" in sink.stats()["degraded_reason"]


def test_postgres_sink_degrades_on_an_unreachable_dsn():
    sink = cdc.PostgresSink(dsn="postgresql://127.0.0.1:1/doesnotexist")
    assert sink._connect() is None
    assert sink.ensure_table("t", "id", ["id", "v"]) is False
    assert sink.stats()["degraded_reason"]


def test_postgres_sink_degrades_when_a_connected_statement_fails(monkeypatch):
    import psycopg

    sink = cdc.PostgresSink(dsn="postgresql://ignored")
    monkeypatch.setattr(psycopg, "connect",
                         lambda *a, **k: _FakeCursorThatFails())
    assert sink.ensure_table("t", "id", ["id", "v"]) is False
    assert sink.stats()["degraded_reason"]

    sink2 = cdc.PostgresSink(dsn="postgresql://ignored")
    monkeypatch.setattr(psycopg, "connect",
                         lambda *a, **k: _FakeCursorThatFails())
    ok = sink2.apply_events("t", "id",
                             [{"op": "upsert", "pk": "1", "row": {"id": "1"}}])
    assert ok is False
    assert sink2.stats()["failed"] == 1
    assert sink2.stats()["degraded_reason"]


# ── real Postgres smoke test (skipped without a live DSN) ────────────────

def _pg_test_dsn():
    return (os.environ.get("SQLITE_CDC_PG_TEST_DSN") or "").strip()


def test_postgres_sink_applies_events_when_a_live_dsn_is_configured(tmp_path):
    dsn = _pg_test_dsn()
    if not dsn:
        # No live DSN in this environment: this is not a skip (T5). The sink's
        # fail-open contract is the thing asserted instead -- disabled, never
        # raising, every event counted as failed, no silent "success".
        sink = cdc.PostgresSink(dsn="")
        assert not sink.enabled()
        ok = sink.apply_events("row880_cdc_smoke", "id",
                               [{"op": "upsert", "pk": "1", "row": {"id": "1", "v": "x"}}])
        assert ok is False
        assert sink.stats()["failed"] == 1, sink.stats()
        return
    sink = cdc.PostgresSink(dsn=dsn)
    assert sink.enabled()
    table = "row880_cdc_smoke"
    sink.ensure_table(table, "id", ["id", "v"])
    ok = sink.apply_events(table, "id",
                            [{"op": "upsert", "pk": "1", "row": {"id": "1", "v": "x"}}])
    assert ok, sink.stats()


# ── FIXER (row880 REFUTE items 1, 3, 4) ──────────────────────────────────


class DownThenUpSink(FakeSink):
    """apply_events returns False (sink down) for the first N calls."""

    def __init__(self, down_for: int):
        super().__init__()
        self.down_for = down_for
        self.rejected = []

    def apply_events(self, table, pk, events):
        if self.calls < self.down_for:
            self.calls += 1
            self.rejected.append([dict(e) for e in events])
            return False
        return super().apply_events(table, pk, events)


def test_events_rejected_by_a_down_sink_are_replayed_when_it_recovers(tmp_path):
    """(1) A sink that refuses events must not advance the snapshot or the
    persisted state: the same diff is re-derived and applied once the sink
    is back -- zero data loss across the outage, including across a
    restart from the state file."""
    db_path = str(tmp_path / "app.db")
    cx = _make_db(db_path)
    sink = DownThenUpSink(down_for=2)
    state = str(tmp_path / "state.json")
    stream = cdc.SqliteCDCStream(db_path, [("items", "id")], sink=sink, state_path=state)

    cx.execute("INSERT INTO items VALUES (1, 'a')")
    cx.commit()
    r1 = stream.poll()                       # sink down: rejected, not advanced
    assert r1["events"] == 0 and sink.rejected and "items" not in sink.tables
    assert stream._snapshots.get("items", {}) == {}
    assert json.load(open(state))["snapshots"].get("items", {}) == {}

    cx.execute("INSERT INTO items VALUES (2, 'b')")
    cx.commit()
    restarted = cdc.SqliteCDCStream(db_path, [("items", "id")], sink=sink, state_path=state)
    r2 = restarted.poll()                    # still down: both rows re-offered
    assert r2["events"] == 0 and len(sink.rejected[-1]) == 2

    r3 = restarted.poll()                    # sink back: everything applied
    assert r3["events"] == 2
    assert sink.tables["items"] == _current_rows(db_path)
    assert json.load(open(state))["snapshots"]["items"] == restarted._snapshots["items"]


def test_sql_identifiers_are_quoted_with_embedded_quotes_doubled():
    """(3) Table/column/pk names are never interpolated raw."""
    assert cdc._ident('items') == '"items"'
    assert cdc._ident('we"ird') == '"we""ird"'
    assert cdc._ident('a"; DROP TABLE x; --') == '"a""; DROP TABLE x; --"'


# ── FIXER round 2 (E1 mirror bootstrap, E2 BLOB cells) ───────────────────

def test_stream_bootstraps_the_mirror_table_before_the_first_apply(tmp_path):
    """E1: a fresh mirror has no table; poll() creates it (ensure_table with
    the table's columns) before the first apply, once per table."""
    db = str(tmp_path / "a.db")
    cx = _make_db(db)
    cx.execute("INSERT INTO items(v) VALUES ('x')")
    cx.commit()
    sink = FakeSink()
    stream = cdc.SqliteCDCStream(db, [("items", "id")], sink=sink,
                                 state_path=str(tmp_path / "s.json"))
    stream.poll()
    assert sink.ensured == [("items", "id", ["id", "v"])]
    cx.execute("INSERT INTO items(v) VALUES ('y')")
    cx.commit()
    stream.poll()
    assert len(sink.ensured) == 1 and sink.calls == 2
    cx.close()


def test_stream_retries_bootstrap_and_drops_nothing_when_ensure_table_fails(tmp_path):
    class NoTableYet(FakeSink):
        def __init__(self):
            super().__init__()
            self.refuse = 1

        def ensure_table(self, table, pk, columns):
            if self.refuse:
                self.refuse -= 1
                return False
            return super().ensure_table(table, pk, columns)

    db = str(tmp_path / "a.db")
    cx = _make_db(db)
    cx.execute("INSERT INTO items(v) VALUES ('x')")
    cx.commit()
    sink = NoTableYet()
    stream = cdc.SqliteCDCStream(db, [("items", "id")], sink=sink,
                                 state_path=str(tmp_path / "s.json"))
    assert stream.poll()["events"] == 0 and sink.calls == 0
    assert stream.poll()["events"] == 1
    assert sink.tables["items"]["1"]["v"] == "x"
    cx.close()


def test_blob_cells_are_mirrored_and_persisted_without_raising(tmp_path):
    """E2: a BLOB value used to raise TypeError out of poll() at _save_state
    (after the events were already applied). Blobs travel as tagged base64
    text: JSON-safe state, TEXT-column-safe mirror, stable across restarts."""
    db = str(tmp_path / "a.db")
    cx = sqlite3.connect(db)
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("CREATE TABLE blobs(id INTEGER PRIMARY KEY, payload BLOB)")
    cx.execute("INSERT INTO blobs(payload) VALUES (?)", (b"\x00\xffbinary",))
    cx.commit()
    sink = FakeSink()
    state = str(tmp_path / "s.json")
    stream = cdc.SqliteCDCStream(db, [("blobs", "id")], sink=sink, state_path=state)
    assert stream.poll()["events"] == 1
    mirrored = sink.tables["blobs"]["1"]["payload"]
    assert isinstance(mirrored, str) and mirrored.startswith("base64:")
    assert base64.b64decode(mirrored[len("base64:"):]) == b"\x00\xffbinary"
    assert json.load(open(state))["snapshots"]["blobs"]["1"]["payload"] == mirrored
    # unchanged blob -> no event on the next poll, also after a restart
    assert stream.poll()["events"] == 0
    again = cdc.SqliteCDCStream(db, [("blobs", "id")], sink=FakeSink(), state_path=state)
    assert again.poll()["events"] == 0
    cx.close()
