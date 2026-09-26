"""v3.66.1679 -- MOD3 PG schema parity, baseline backfill, history content
probe (row 127, Stage 3.2/4 prerequisite).

DEFECTS (FINDING-ROW127-SHADOW-READ-bd-pm-A-20260926T1358Z.md, classes 2-4):
  2. PARITY. _PG_SCHEMA hand-wrote DDL that had drifted from db.py in 5 of 6
     mirrored tables; the hub logged 'column "ts_added" does not exist' x59.
     ensure_schema() could only CREATE, never add a column to an existing table.
  3. NO BACKFILL. PG held only rows written since dual-write was switched on
     (queue: sqlite=60 pg=0), so every aggregate shadow read diverged by design.
  4. CONTENT. Same row count, different content on history. Cause reproduced
     here: a mirrored history INSERT let Postgres assign its own BIGSERIAL id,
     so SQLite and PG disagreed on which row `WHERE id = ?` meant and every
     id-keyed UPDATE/DELETE (batch_ops, library, storage_rebalance) landed on a
     different PG row or none.

The real-PG tests run in this module's own schema (tests/mod3_pg_isolation.py)
and drop the six mirrored tables first, so every count below is exact.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tests"))

import mod3_pg_isolation
from bulk_downloader import pg_backend

_MODULE = "test_v3_66_1679_mod3_pg_parity_backfill.py"
_TABLES = ("captures", "history", "host_throughput", "push_subscriptions",
           "queue", "session_history")
_PG_TYPE = {"BIGINT": "bigint", "TEXT": "text", "BYTEA": "bytea",
            "DOUBLE PRECISION": "double precision"}


def _pg_available():
    if not mod3_pg_isolation.real_dsn():
        return False, "no MOD3_PG_TEST_DSN in the environment"
    try:
        import psycopg
    except ImportError:
        return False, "psycopg not installed (optional dep)"
    dsn = mod3_pg_isolation.dsn_for(_MODULE)
    if not dsn:
        return False, "could not create this module's isolated schema"
    try:
        with psycopg.connect(dsn, connect_timeout=5):
            return True, dsn
    except Exception as e:
        return False, f"postgres unreachable: {type(e).__name__}"


@pytest.fixture(autouse=True)
def _isolated_history_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.delenv("MOD3_CUTOVER", raising=False)


def _reload(monkeypatch, dsn=None, shadow=None):
    for k, v in (("MOD3_PG_DSN", dsn), ("MOD3_SHADOW_READ", shadow)):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    from bulk_downloader import db
    from bulk_downloader import pg_backend as pg
    importlib.reload(pg)
    importlib.reload(db)
    return db, pg


def _need_pg():
    ok, why = _pg_available()
    if not ok:
        pytest.skip(f"REAL-PG parity/backfill not verifiable here: {why}")
    return why


def _drop_mirror_tables(dsn):
    import psycopg
    with psycopg.connect(dsn) as c:
        for t in _TABLES:
            c.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        c.commit()


def _full_sqlite_schema(db):
    """The LIVE SQLite shape: db_init + every migration + the lazy
    host_throughput table. Returns the raw sqlite3 connection's PRAGMA view."""
    db.db_init()
    from bulk_downloader import migrations
    migrations.apply_pending(backup_first=False, zero_downtime=False)
    with db.db_conn() as cx:
        raw = getattr(cx, "_cx", cx)
        db._ensure_host_throughput_table(raw)
        raw.commit()
        return {t: {r[1]: _PG_TYPE[pg_backend._pg_type(r[2])]
                    if hasattr(pg_backend, "_pg_type") else r[2]
                    for r in raw.execute(f"PRAGMA table_info({t})")}
                for t in _TABLES}


def _pg_cols(dsn, table):
    import psycopg
    with psycopg.connect(dsn) as c:
        return {r[0]: r[1] for r in c.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,))}


def _raw(db):
    """The seam's underlying sqlite3 connection: writes through it are NOT
    mirrored, which is exactly the pre-dual-write baseline being modelled."""
    cm = db.db_conn()
    cx = cm.__enter__()
    return cm, getattr(cx, "_cx", cx)


# ── 0. Translation units (no Postgres) ─────────────────────────────────────

class TestTranslation:
    @pytest.mark.parametrize("decl,want", [
        ("INTEGER", "BIGINT"), ("INT", "BIGINT"), ("BIGINT", "BIGINT"),
        ("TEXT", "TEXT"), ("VARCHAR(40)", "TEXT"), ("CLOB", "TEXT"),
        ("REAL", "DOUBLE PRECISION"), ("FLOAT", "DOUBLE PRECISION"),
        ("DOUBLE", "DOUBLE PRECISION"), ("BLOB", "BYTEA"),
        ("", "TEXT"), (None, "TEXT"), ("NUMERIC", "TEXT"),
    ])
    def test_pg_type_per_sqlite_affinity(self, decl, want):
        assert pg_backend._pg_type(decl) == want

    @pytest.mark.parametrize("dflt,want", [
        (None, None),
        ("strftime('%Y-%m-%dT%H:%M:%S','now')", pg_backend._PG_TS_DEFAULT
         if hasattr(pg_backend, "_PG_TS_DEFAULT") else "?"),
        ("strftime('%s','now')", "extract(epoch from now())"),
        ("0", "0"), ("-1.5", "-1.5"), ("'pending'", "'pending'"),
        ("''", "''"), ("NULL", "NULL"), ("'it''s'", "'it''s'"),
        ("datetime('now')", None), ("random()", None),
    ])
    def test_pg_default_translates_or_drops(self, dflt, want):
        assert pg_backend._pg_default(dflt) == want

    def test_column_fragment_keeps_not_null_only_with_a_default(self):
        assert pg_backend._sqlite_col_to_pg(
            "egress_ip", "TEXT", 1, "'UNKNOWN'") == \
            '"egress_ip" TEXT NOT NULL DEFAULT \'UNKNOWN\''
        assert pg_backend._sqlite_col_to_pg("p256dh", "TEXT", 1, None) == \
            '"p256dh" TEXT'
        assert pg_backend._sqlite_col_to_pg("n", "INTEGER", 0, "0") == \
            '"n" BIGINT DEFAULT 0'

    def test_rowid_tables_are_derived_from_the_ddl(self):
        assert pg_backend._ROWID_TABLES == frozenset(
            {"history", "session_history"})

    def test_rowid_is_prepended_to_a_single_row_insert(self):
        sql = "INSERT INTO history(site_id,status) VALUES(%s,%s)"
        assert pg_backend._with_rowid(sql, ("s", "done"), 42) == (
            "INSERT INTO history(id, site_id,status) VALUES(%s, %s,%s)",
            (42, "s", "done"))

    def test_unalignable_inserts_are_refused_not_guessed(self):
        sql = "INSERT INTO history(site_id) VALUES(%s)"
        assert pg_backend._with_rowid(sql, ("s",), None) is None
        assert pg_backend._with_rowid(sql, {"a": 1}, 3) is None
        assert pg_backend._with_rowid(
            "INSERT INTO history SELECT * FROM x", (), 3) is None


# ── 1. PARITY on real Postgres ──────────────────────────────────────────────

class TestParity:
    def test_fresh_pg_schema_matches_the_live_sqlite_schema(self, monkeypatch):
        """RED on 8d6a55cd8: 6 tables drifted (queue lacked ts_added ...)."""
        dsn = _need_pg()
        _drop_mirror_tables(dsn)
        db, pg = _reload(monkeypatch, dsn=dsn)
        sq = _full_sqlite_schema(db)
        # fixture shape proven nonzero before any verdict
        assert all(sq[t] for t in _TABLES), {t: len(sq[t]) for t in _TABLES}
        assert sum(len(v) for v in sq.values()) >= 60, sq
        assert pg.ensure_schema() is True, pg.stats()
        drift = {}
        for t in _TABLES:
            pgc = _pg_cols(dsn, t)
            missing = sorted(set(sq[t]) - set(pgc))
            wrong = sorted(f"{c}:{sq[t][c]}!={pgc[c]}"
                           for c in set(sq[t]) & set(pgc) if sq[t][c] != pgc[c])
            if missing or wrong:
                drift[t] = {"missing": missing, "type": wrong}
        assert drift == {}, f"PG schema drifted from SQLite: {drift}"
        rep = pg.schema_parity()
        assert all(not rep[t]["missing"] and not rep[t]["type_mismatch"]
                   for t in _TABLES), rep

    def test_ensure_schema_adds_columns_to_an_existing_legacy_table(
            self, monkeypatch):
        """The hub's shape: a queue table created by the OLD hand DDL (no
        ts_added), holding a row. ensure_schema must ADD, never drop."""
        dsn = _need_pg()
        import psycopg
        _drop_mirror_tables(dsn)
        with psycopg.connect(dsn) as c:
            c.execute("CREATE TABLE queue(site_id TEXT NOT NULL, url TEXT NOT "
                      "NULL, status TEXT NOT NULL DEFAULT 'pending', ord "
                      "BIGINT DEFAULT 0, legacy_only TEXT)")
            c.execute("INSERT INTO queue(site_id, url, legacy_only) "
                      "VALUES ('s', 'u', 'keep')")
            c.commit()
        db, pg = _reload(monkeypatch, dsn=dsn)
        _full_sqlite_schema(db)
        assert pg.ensure_schema() is True, pg.stats()
        with psycopg.connect(dsn) as c:
            rows = c.execute("SELECT site_id, url, legacy_only FROM queue "
                             "WHERE site_id=%s ORDER BY ord, ts_added",
                             ("s",)).fetchall()
            n_idx = c.execute(
                "SELECT count(*) FROM pg_indexes WHERE indexname = "
                "'mod3_pk_queue'").fetchone()[0]
        assert rows == [("s", "u", "keep")], rows
        assert n_idx == 1, "no unique key for backfill's ON CONFLICT"
        assert "legacy_only" in _pg_cols(dsn, "queue"), "a column was dropped"

    def test_negative_control_a_planted_drift_is_reported(self, monkeypatch):
        dsn = _need_pg()
        _drop_mirror_tables(dsn)
        db, pg = _reload(monkeypatch, dsn=dsn)
        _full_sqlite_schema(db)
        assert pg.ensure_schema() is True
        cm, raw = _raw(db)
        try:
            raw.execute("ALTER TABLE queue ADD COLUMN planted_drift TEXT")
            raw.commit()
        finally:
            cm.__exit__(None, None, None)
        rep = pg.schema_parity()
        assert rep["queue"]["missing"] == ["planted_drift"], rep["queue"]
        assert all(not rep[t]["missing"] for t in _TABLES if t != "queue"), rep
        # ... and the fix path closes it
        assert pg.ensure_schema() is True
        assert pg.schema_parity()["queue"]["missing"] == []


# ── 2. BACKFILL on real Postgres ────────────────────────────────────────────

_SEED = {
    "history": ("INSERT INTO history(site_id, url, status) VALUES (?,?,?)",
                [("s1", f"u{i}", "done") for i in range(7)]),
    "queue": ("INSERT INTO queue(site_id, url, status) VALUES (?,?,?)",
              [("s1", f"q{i}", "pending") for i in range(5)]),
    "push_subscriptions": (
        "INSERT INTO push_subscriptions(endpoint, p256dh, auth) VALUES (?,?,?)",
        [(f"e{i}", "k", "a") for i in range(2)]),
    "session_history": (
        "INSERT INTO session_history(ts, site_id, event_type) VALUES (?,?,?)",
        [(1.5 + i, "s1", "login") for i in range(3)]),
    "captures": ("INSERT INTO captures(rel_path, name) VALUES (?,?)",
                 [(f"c/{i}", f"n{i}") for i in range(4)]),
    "host_throughput": ("INSERT INTO host_throughput(host) VALUES (?)",
                        [(f"h{i}",) for i in range(3)]),
}


def _seed(db):
    _full_sqlite_schema(db)
    cm, raw = _raw(db)
    try:
        for sql, rows in _SEED.values():
            raw.executemany(sql, rows)
        raw.commit()
    finally:
        cm.__exit__(None, None, None)


def _pg_count(dsn, t):
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]


class TestBackfill:
    def test_backfill_copies_exact_counts_then_zero(self, monkeypatch):
        """RED on 8d6a55cd8: AttributeError -- pg_backend has no backfill."""
        dsn = _need_pg()
        _drop_mirror_tables(dsn)
        db, pg = _reload(monkeypatch, dsn=dsn)
        _seed(db)
        assert pg.ensure_schema() is True
        assert all(_pg_count(dsn, t) == 0 for t in _TABLES)
        first = pg.backfill()
        want = {t: len(rows) for t, (_, rows) in _SEED.items()}
        assert {t: first[t].get("copied") for t in _TABLES} == want, first
        assert {t: first[t]["source"] for t in _TABLES} == want, first
        assert {t: _pg_count(dsn, t) for t in _TABLES} == want
        second = pg.backfill()
        assert {t: second[t].get("copied") for t in _TABLES} == \
            {t: 0 for t in _TABLES}, second
        assert {t: second[t]["skipped"] for t in _TABLES} == want, second

    def test_backfill_leaves_a_planted_pg_row_alone(self, monkeypatch):
        dsn = _need_pg()
        import psycopg
        _drop_mirror_tables(dsn)
        db, pg = _reload(monkeypatch, dsn=dsn)
        _seed(db)
        assert pg.ensure_schema() is True
        with psycopg.connect(dsn) as c:
            c.execute("INSERT INTO queue(site_id, url, status) "
                      "VALUES ('pg-only', 'x', 'planted')")
            c.commit()
        res = pg.backfill(["queue"])
        assert res["queue"]["copied"] == 5, res
        assert _pg_count(dsn, "queue") == 6
        with psycopg.connect(dsn) as c:
            assert c.execute("SELECT status FROM queue WHERE site_id="
                             "'pg-only'").fetchall() == [("planted",)]

    def test_backfilled_ids_match_and_the_sequence_is_advanced(
            self, monkeypatch):
        dsn = _need_pg()
        import psycopg
        _drop_mirror_tables(dsn)
        db, pg = _reload(monkeypatch, dsn=dsn)
        _seed(db)
        pg.backfill(["history"])
        cm, raw = _raw(db)
        try:
            sq = sorted(tuple(r) for r in raw.execute(
                "SELECT id, url, status FROM history"))
        finally:
            cm.__exit__(None, None, None)
        with psycopg.connect(dsn) as c:
            got = sorted(c.execute("SELECT id, url, status FROM history"))
            nxt = c.execute("SELECT nextval(pg_get_serial_sequence("
                            "'history', 'id'))").fetchone()[0]
        assert got == sq and len(sq) == 7, (sq, got)
        assert nxt == max(r[0] for r in sq) + 1

    def test_negative_control_backfill_refuses_an_unmirrored_table(
            self, monkeypatch):
        dsn = _need_pg()
        _db, pg = _reload(monkeypatch, dsn=dsn)
        res = pg.backfill(["alert_events"])
        assert list(res) == ["alert_events"], res
        assert "not a mirrored table" in res["alert_events"]["error"], res
        assert "alert_events" not in {
            r for r in _pg_cols(dsn, "alert_events")}

    def test_cli_entry_runs_backfill_and_prints_json(self, monkeypatch, capsys):
        dsn = _need_pg()
        _drop_mirror_tables(dsn)
        db, pg = _reload(monkeypatch, dsn=dsn)
        _seed(db)
        assert pg.main(["backfill", "captures"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["captures"]["copied"] == 4, out
        assert pg.main(["backfill", "nope"]) == 1
        r = subprocess.run([sys.executable, "-m", "bulk_downloader.pg_backend"],
                           cwd=str(_REPO), capture_output=True, text=True,
                           timeout=60, check=False)
        assert r.returncode == 2 and "usage:" in r.stderr, (r.returncode,
                                                             r.stderr[-400:])


# ── 2b. BACKFILL UNDER DUAL-WRITE (lens REFUTE on tree 001bcbef) ─────────────
#
# The lens's probe, kept as a test: a seam UPDATE of a row the backfill has
# READ but not yet inserted into PG. Before the fix the UPDATE's mirror hit 0
# PG rows, then the batch inserted the stale snapshot and ON CONFLICT DO
# NOTHING kept it: sqlite=done, pg=pending.

class _HookCur:
    def __init__(self, cur, hook):
        self._cur, self._hook = cur, hook

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._cur.close()

    def executemany(self, sql, rows):
        rows = list(rows)
        self._hook(rows)
        return self._cur.executemany(sql, rows)

    @property
    def rowcount(self):
        return self._cur.rowcount


class _HookCx:
    def __init__(self, cx, hook):
        self._cx, self._hook = cx, hook

    def cursor(self, *a, **k):
        return _HookCur(self._cx.cursor(*a, **k), self._hook)

    def __getattr__(self, name):
        return getattr(self._cx, name)


def _queue_status(db, dsn, url):
    import psycopg
    cm, raw = _raw(db)
    try:
        sq = [tuple(r) for r in raw.execute(
            "SELECT status FROM queue WHERE site_id='s1' AND url=?", (url,))]
    finally:
        cm.__exit__(None, None, None)
    with psycopg.connect(dsn) as c:
        pgr = c.execute("SELECT status FROM queue WHERE site_id='s1' AND "
                        "url=%s", (url,)).fetchall()
    return sq, pgr


class TestBackfillUnderDualWrite:
    def _run(self, monkeypatch, concurrent):
        import threading
        dsn = _need_pg()
        _drop_mirror_tables(dsn)
        db, pg = _reload(monkeypatch, dsn=dsn)
        _seed(db)
        assert pg.ensure_schema() is True
        monkeypatch.setattr(pg, "_BACKFILL_BATCH", 2)
        state = {"fired": False, "finished_before_insert": None,
                 "error": None, "thread": None}
        done = threading.Event()

        def writer():
            try:
                with db.db_conn() as cx:     # the dual-write seam
                    cx.execute("UPDATE queue SET status=? WHERE site_id=? "
                               "AND url=?", ("done", "s1", "q3"))
            except Exception as e:           # surfaced by the assertion below
                state["error"] = repr(e)
            finally:
                done.set()

        def hook(rows):
            if not concurrent or state["fired"]:
                return
            if any("q3" in r for r in rows):   # q3 READ, not yet inserted
                state["fired"] = True
                t = threading.Thread(target=writer, daemon=True)
                state["thread"] = t
                t.start()
                state["finished_before_insert"] = done.wait(1.0)

        real = pg._connect

        def hooked_connect():
            c = real()
            return _HookCx(c, hook) if c is not None else None

        monkeypatch.setattr(pg, "_connect", hooked_connect)
        res = pg.backfill(["queue"])
        if state["thread"] is not None:
            state["thread"].join(15)
        return db, dsn, res, state

    def test_control_without_a_concurrent_write(self, monkeypatch):
        db, dsn, res, state = self._run(monkeypatch, concurrent=False)
        assert res["queue"]["copied"] == 5, res
        assert state["fired"] is False
        assert _queue_status(db, dsn, "q3") == ([("pending",)], [("pending",)])

    def test_concurrent_update_between_read_and_insert_is_not_lost(
            self, monkeypatch):
        """RED on tree 001bcbef: sqlite=[('done',)] pg=[('pending',)]."""
        db, dsn, res, state = self._run(monkeypatch, concurrent=True)
        assert state["fired"] is True, "the probe never fired: q3 not batched"
        assert state["error"] is None, state
        assert res["queue"]["copied"] == 5, res
        sq, pgr = _queue_status(db, dsn, "q3")
        assert sq == [("done",)], sq
        assert pgr == sq, (
            f"PG kept the stale snapshot: sqlite={sq!r} pg={pgr!r} "
            f"writer_finished_before_insert={state['finished_before_insert']}")


# ── 3. CONTENT PROBE: history.status both sides ────────────────────────────

def _both_status(db, dsn, site):
    import psycopg
    cm, raw = _raw(db)
    try:
        sq = [tuple(r) for r in raw.execute(
            "SELECT status FROM history WHERE site_id=?", (site,))]
    finally:
        cm.__exit__(None, None, None)
    with psycopg.connect(dsn) as c:
        pgr = c.execute("SELECT status FROM history WHERE site_id=%s",
                        (site,)).fetchall()
    return sq, pgr


class TestContentProbe:
    def _arm(self, monkeypatch):
        dsn = _need_pg()
        _drop_mirror_tables(dsn)
        # baseline rows that pre-date dual-write: they move SQLite's id
        # counter and never reach PG -- the hub's state before any backfill.
        db, pg = _reload(monkeypatch, dsn=None)
        db.db_init()
        cm, raw = _raw(db)
        try:
            raw.executemany("INSERT INTO history(site_id, url, status) "
                            "VALUES (?,?,?)",
                            [("older", f"o{i}", "done") for i in range(3)])
            raw.commit()
        finally:
            cm.__exit__(None, None, None)
        db, pg = _reload(monkeypatch, dsn=dsn, shadow="1")
        _full_sqlite_schema(db)
        assert pg.ensure_schema() is True, pg.stats()
        return db, pg, dsn

    def test_history_status_agrees_after_a_normal_write_and_id_update(
            self, monkeypatch):
        """RED on 8d6a55cd8: sqlite=[('done',)] pg=[] (bytes_fetched missing,
        mirror INSERT failed). With parity alone: sqlite=[('done',)]
        pg=[('failed',)] -- PG's BIGSERIAL gave the row id 1, SQLite id 4."""
        db, pg, dsn = self._arm(monkeypatch)
        db.db_log("probe", "Probe", "http://p/1", "failed")   # normal path
        cm, raw = _raw(db)
        try:
            hid = raw.execute("SELECT id FROM history WHERE site_id='probe'"
                              ).fetchone()[0]
        finally:
            cm.__exit__(None, None, None)
        assert hid == 4, hid        # fixture shape: ids really are offset
        with db.db_conn() as cx:    # batch_ops' id-keyed status reset
            cx.execute("UPDATE history SET status = ?, message = '' "
                       "WHERE id = ?", ("done", hid))
        sq, pgr = _both_status(db, dsn, "probe")
        assert sq == [("done",)], sq
        assert pg._rows_equal(sq, pgr), (
            f"history.status DIVERGED: sqlite={sq!r} pg={pgr!r} "
            f"mirror={pg.stats()!r}")
        before = pg.shadow_stats()
        with db.db_conn() as cx:
            cx.execute("SELECT status FROM history WHERE site_id=?",
                       ("probe",)).fetchall()
        after = pg.shadow_stats()
        assert after["compared"] == before["compared"] + 1, after
        assert after["diverged"] == before["diverged"], after

    def test_negative_control_a_planted_mismatch_is_caught(self, monkeypatch):
        db, pg, dsn = self._arm(monkeypatch)
        import psycopg
        db.db_log("probe", "Probe", "http://p/1", "done")
        with psycopg.connect(dsn) as c:
            c.execute("UPDATE history SET status='TAMPERED' "
                      "WHERE site_id='probe'")
            c.commit()
        sq, pgr = _both_status(db, dsn, "probe")
        assert (sq, pgr) == ([("done",)], [("TAMPERED",)])
        assert not pg._rows_equal(sq, pgr)
        before = pg.shadow_stats()
        with db.db_conn() as cx:
            cx.execute("SELECT status FROM history WHERE site_id=?",
                       ("probe",)).fetchall()
        assert pg.shadow_stats()["diverged"] == before["diverged"] + 1

    def test_an_id_less_history_insert_is_skipped_not_forked(
            self, monkeypatch):
        """executemany has no per-row lastrowid: the mirror must refuse the
        id-less INSERT (counted skipped) rather than let PG pick an id."""
        db, pg, dsn = self._arm(monkeypatch)
        before = pg.stats()
        with db.db_conn() as cx:
            cx.executemany("INSERT INTO history(site_id, status) VALUES (?,?)",
                           [("many", "done"), ("many", "done")])
        after = pg.stats()
        assert after["skipped"] == before["skipped"] + 2, after
        assert after["failed"] == before["failed"], after
        assert _both_status(db, dsn, "many")[1] == []


def _pg_native_history_id(dsn, site):
    """A Postgres-native INSERT (no id) -- what cutover and the 803/804 canaries
    do. Returns the id PG's own sequence handed out."""
    import psycopg
    with psycopg.connect(dsn) as c:
        rid = c.execute("INSERT INTO history(site_id, status) VALUES (%s,%s) "
                        "RETURNING id", (site, "native")).fetchone()[0]
        c.commit()
    return rid


class TestSerialSequence:
    """An explicit-id mirror INSERT does not touch PG's BIGSERIAL sequence.
    RED on 4ca03678 (no _advance_serial): the native INSERT draws id 1, and
    test_v3_66_804 TestRealCutover dies on history_pkey."""

    def test_a_native_insert_after_mirrored_rows_does_not_reuse_their_ids(
            self, monkeypatch):
        db, pg, dsn = TestContentProbe()._arm(monkeypatch)
        for i in range(2):                        # SQLite ids 4 and 5
            db.db_log("seq", "Seq", f"http://s/{i}", "done")
        assert pg.stats()["failed"] == 0, pg.stats()
        rid = _pg_native_history_id(dsn, "native")
        assert rid > 5, (
            f"PG's sequence handed out id {rid}, which the mirror already "
            "wrote or SQLite already owns")

    def test_an_older_explicit_id_never_moves_the_sequence_backwards(
            self, monkeypatch):
        db, pg, dsn = TestContentProbe()._arm(monkeypatch)
        for i in range(2):                        # SQLite ids 4 and 5
            db.db_log("seq", "Seq", f"http://s/{i}", "done")
        assert pg.mirror("INSERT INTO history(site_id, status) VALUES (?,?)",
                         ("late", "done"), rowid=2) is True, pg.stats()
        rid = _pg_native_history_id(dsn, "native")
        assert rid > 5, f"sequence moved backwards: native id {rid}"


class TestZeroRowInsert:
    """The seam passes cursor.lastrowid only when rowcount == 1. sqlite3 leaves
    lastrowid at the PREVIOUS insert's id when a statement inserts nothing, so
    without the guard a 0-row INSERT is mirrored under a stale id (surviving
    mutant M4 of the r2 cut)."""

    def test_inserted_rowid_is_none_for_an_or_ignore_conflict(self, tmp_path):
        import sqlite3
        cx = sqlite3.connect(str(tmp_path / "m4.db"))
        cx.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, k TEXT UNIQUE)")
        cur = cx.execute("INSERT INTO t(k) VALUES ('a')")
        assert pg_backend.inserted_rowid(cur) == 1        # control
        cur = cx.execute("INSERT OR IGNORE INTO t(k) VALUES ('a')")
        assert (cur.rowcount, cur.lastrowid) == (0, 1)    # stale, by sqlite3
        assert pg_backend.inserted_rowid(cur) is None
        cx.close()

    def test_a_zero_row_insert_through_the_seam_is_not_mirrored(
            self, monkeypatch):
        db, pg, dsn = TestContentProbe()._arm(monkeypatch)
        db.db_log("real", "Real", "http://r/1", "done")      # SQLite id 4
        cm, raw = _raw(db)
        try:   # the insert is silently dropped: rowcount 0, lastrowid stale
            raw.execute("CREATE TRIGGER m4_ignore BEFORE INSERT ON history "
                        "WHEN NEW.site_id = 'ignored' "
                        "BEGIN SELECT RAISE(IGNORE); END")
            raw.commit()
        finally:
            cm.__exit__(None, None, None)
        before = pg.stats()
        with db.db_conn() as cx:
            cx.execute("INSERT INTO history(site_id, status) VALUES (?,?)",
                       ("ignored", "done"))
        after = pg.stats()
        sq, pgr = _both_status(db, dsn, "ignored")
        assert sq == [], sq
        assert pgr == [], (
            f"a 0-row SQLite INSERT reached PG under a stale id: {pgr!r}")
        assert after["failed"] == before["failed"], after
        assert after["skipped"] == before["skipped"] + 1, after


class TestForkRepair:
    """A PG row squatting a SQLite id with other content is a fork (the
    pre-alignment mirror let PG pick ids). SQLite is authoritative: backfill
    and the mirror both replace it (RULING-ROW127-PARITY-R3-803, option B).
    RED on 4ca03678: backfill ON CONFLICT DO NOTHING kept 'forked'."""

    def _forked(self, monkeypatch):
        dsn = _need_pg()
        _drop_mirror_tables(dsn)
        db, pg = _reload(monkeypatch, dsn=None)
        db.db_init()
        db.db_log("truth", "Truth", "http://t/1", "done")      # SQLite id 1
        db, pg = _reload(monkeypatch, dsn=dsn)
        _full_sqlite_schema(db)
        assert pg.ensure_schema() is True, pg.stats()
        import psycopg
        with psycopg.connect(dsn) as c:
            c.execute("INSERT INTO history(id, site_id, url, status) "
                      "VALUES (1, 'forked', 'http://f/1', 'failed')")
            c.commit()
        return db, pg, dsn

    def _pg_row(self, dsn, rid):
        import psycopg
        with psycopg.connect(dsn) as c:
            return c.execute("SELECT site_id, url, status FROM history "
                             "WHERE id = %s", (rid,)).fetchall()

    def test_backfill_replaces_a_forked_row_then_is_idempotent(
            self, monkeypatch):
        _, pg, dsn = self._forked(monkeypatch)
        first = pg.backfill(["history"])["history"]
        assert first["copied"] == 1, first
        assert self._pg_row(dsn, 1) == [("truth", "http://t/1", "done")]
        second = pg.backfill(["history"])["history"]
        assert (second["copied"], second["skipped"]) == (0, 1), second

    def test_negative_control_the_fork_is_real_before_backfill(
            self, monkeypatch):
        _, _, dsn = self._forked(monkeypatch)
        assert self._pg_row(dsn, 1) == [("forked", "http://f/1", "failed")]

    def test_a_mirrored_insert_onto_a_squatted_id_replaces_it(
            self, monkeypatch):
        db, pg, dsn = self._forked(monkeypatch)
        import psycopg
        with psycopg.connect(dsn) as c:
            c.execute("INSERT INTO history(id, site_id, status) "
                      "VALUES (2, 'squat', 'native')")
            c.commit()
        db.db_log("next", "Next", "http://n/2", "done")        # SQLite id 2
        assert pg.stats()["failed"] == 0, pg.stats()
        assert self._pg_row(dsn, 2) == [("next", "http://n/2", "done")]


class TestCutoverSyncsSequences:
    def test_engage_advances_every_rowid_sequence_past_max_id(
            self, monkeypatch):
        """A PG-native write after cutover must never draw a used id, even
        when rows arrived without the mirror (restore, manual copy)."""
        dsn = _need_pg()
        _, pg, _ = TestContentProbe()._arm(monkeypatch)
        import psycopg
        with psycopg.connect(dsn) as c:
            c.execute("INSERT INTO history(id, site_id, status) "
                      "VALUES (40, 'restored', 'done')")
            c.commit()
        assert pg._sync_serials() is True
        assert _pg_native_history_id(dsn, "after-cutover") == 41
