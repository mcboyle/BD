"""Row 127 O1435: cutover read scope guard.

read_authoritative() must never send a SELECT on tables outside
_MIRRORED_TABLES to Postgres: SQLite already serves those tables, so the
fallback is free -- a PG round-trip that 42Ps on a missing table only
disengages cutover (FINDING-ROW127-FLIP-RED-UNDEFINEDTABLE). Out-of-scope
reads return None BEFORE any connect, leave degraded_reason untouched, and
are counted on /api/health .mod3.cutover.fallback_reasons.
"""
import pytest

from bulk_downloader import pg_backend as pg

BD_GATE_SCOPE = "module"


class _FakeCursor:
    def __init__(self, fake):
        self._fake = fake

    def execute(self, sql, params):
        self._fake.executed.append((sql, tuple(params or ())))
        if self._fake.exc is not None:
            raise self._fake.exc

    def fetchall(self):
        return list(self._fake.rows)


class _FakeCx:
    def __init__(self, fake):
        self._fake = fake

    def cursor(self, row_factory=None):
        return _FakeCursor(self._fake)

    def close(self):
        pass


class _FakePg:
    """Stand-in for pg_backend._connect(): a connect counter + fake cx."""

    def __init__(self, exc=None, rows=()):
        self.exc = exc
        self.rows = list(rows)
        self.executed = []
        self.connects = 0

    def __call__(self):
        self.connects += 1
        return _FakeCx(self)


@pytest.fixture
def engaged(monkeypatch):
    monkeypatch.setattr(pg, "cutover_engaged", lambda: True)
    monkeypatch.setitem(pg._stats, "degraded_reason", None)
    monkeypatch.setattr(pg, "_cutover_fallback_reasons",
                        {"scope": 0, "untranslatable": 0, "unproven": 0,
                         "unreachable": 0, "error": 0})
    # every shape proven: the proven gate has its own tests (soak completion)
    monkeypatch.setattr(pg, "_proven_sync", lambda force=False: None)
    monkeypatch.setattr(pg, "_is_proven", lambda sql: True)
    return monkeypatch


def test_out_of_scope_select_falls_back_without_connect(engaged, monkeypatch):
    import psycopg.errors
    fake = _FakePg(exc=psycopg.errors.UndefinedTable("settings"))
    monkeypatch.setattr(pg, "_connect", fake)
    result = pg.read_authoritative("SELECT id FROM settings")
    assert result is None
    assert fake.connects == 0, "out-of-scope read must not touch Postgres"
    assert pg.stats()["degraded_reason"] is None
    assert pg.cutover_fallback_reasons()["scope"] == 1


def test_in_scope_select_still_reaches_postgres(engaged, monkeypatch):
    fake = _FakePg(rows=({"id": 7, "status": "done"},))
    monkeypatch.setattr(pg, "_connect", fake)
    result = pg.read_authoritative(
        "SELECT id, status FROM history WHERE site_id = ?", ("co-1",))
    assert fake.connects == 1
    assert result is not None and result[0]["status"] == "done"
    assert pg.cutover_fallback_reasons()["scope"] == 0


def test_join_mirrored_and_unmirrored_falls_back(engaged, monkeypatch):
    fake = _FakePg(rows=({"id": 1},))
    monkeypatch.setattr(pg, "_connect", fake)
    result = pg.read_authoritative(
        "SELECT h.id FROM history h JOIN settings s ON s.id = h.id")
    assert result is None
    assert fake.connects == 0
    assert pg.cutover_fallback_reasons()["scope"] == 1


def test_unreachable_and_error_fallbacks_counted(engaged, monkeypatch):
    fake = _FakePg(exc=RuntimeError("boom"))
    monkeypatch.setattr(pg, "_connect", fake)
    assert pg.read_authoritative("SELECT id FROM history") is None
    assert pg.cutover_fallback_reasons()["error"] == 1
    monkeypatch.setattr(pg, "_connect", lambda: None)
    assert pg.read_authoritative("SELECT id FROM history") is None
    assert pg.cutover_fallback_reasons()["unreachable"] == 1


def test_shadow_compare_scope_skip_uses_same_guard(engaged, monkeypatch):
    fake = _FakePg(rows=())
    monkeypatch.setattr(pg, "_connect", fake)
    monkeypatch.setattr(pg, "shadow_read_enabled", lambda: True)
    monkeypatch.setattr(pg, "_shadow_skip_reasons", {})
    assert pg._in_read_scope("SELECT id FROM settings") is False
    assert pg._in_read_scope("SELECT id FROM history") is True
    assert pg.shadow_compare("SELECT id FROM settings", (), ()) is None
    assert pg.shadow_skip_reasons().get("scope") == 1


# Census: every table named by a SELECT in db.py / alerts_engine.py that is
# NOT mirrored must be on this explicit list, so the SQLite-served fallback
# set stays visible when the mirror set or the callers change.
EXPECTED_UNMIRRORED_READ_TABLES = [
    "_bd_fts_docs",        # temp FTS doc map (temp._bd_fts_docs)
    "_bd_prune_keep",      # TEMP table in the prune subselect
    "alert_events",        # alerts engine, SQLite-only
    "alert_rules",         # alerts engine, SQLite-only
    "history_fts",         # FTS5 virtual table over history
    "library",             # file library, SQLite-only
    "site_run_intent",     # dl95-evilangel-1 per-site run intent, SQLite-only
    "sqlite_master",       # schema introspection
]

_SQL_FROM = r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)"


def _select_tables(path):
    import re
    import tokenize
    pat = re.compile(_SQL_FROM, re.IGNORECASE)
    tables = set()
    with tokenize.open(path) as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type != tokenize.STRING:
                continue
            s = tok.string
            if not re.search(r"\bSELECT\b[\s\S]*\bFROM\b", s,
                             re.IGNORECASE):
                continue
            for m in pat.finditer(s):
                name = m.group(1).split(".")[-1].lower()
                tables.add(name)
    return tables


def test_unmirrored_read_table_census_matches_expected_list():
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    named = set()
    for rel in ("bulk_downloader/db.py",
                "bulk_downloader/alerts_engine.py"):
        named |= _select_tables(os.path.join(here, rel))
    unmirrored = sorted(named - set(pg._MIRRORED_TABLES))
    assert unmirrored == EXPECTED_UNMIRRORED_READ_TABLES, unmirrored
