"""Row 127 O1440/O1441: cutover function allowlist.

The 22:07Z re-flip went RED because an in-scope SELECT called datetime(),
which translate() passed through verbatim: Postgres raised UndefinedFunction,
read_authoritative() degraded and cutover disengaged
(FINDING-ROW127-REFLIP-RED-UNDEFINEDFUNCTION). translate() now refuses any
SELECT calling a function outside _FUNCTION_ALLOWLIST, so the read falls
back to SQLite BEFORE any connect and is counted as 'untranslatable'.
"""
import ast
import pathlib
import re

import pytest

from bulk_downloader import pg_backend as pg

BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parents[1]
RED_SQL = ("SELECT count(*) FROM history "
           "WHERE ts >= datetime('now','-1 hour')")


class _FakeCursor:
    def __init__(self, fake):
        self._fake = fake

    def execute(self, sql, params):
        self._fake.executed.append((sql, tuple(params or ())))

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

    def __init__(self, rows=()):
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
    fake = _FakePg(rows=[{"n": 1}])
    monkeypatch.setattr(pg, "_connect", fake)
    return fake


def test_red_sqlite_only_function_is_untranslatable():
    assert pg.translate(RED_SQL) is None
    assert isinstance(pg._FUNCTION_ALLOWLIST, frozenset)


def test_red_read_authoritative_falls_back_without_connect(engaged):
    assert pg.read_authoritative(RED_SQL) is None
    assert engaged.connects == 0
    assert pg._cutover_fallback_reasons["untranslatable"] == 1
    assert pg._stats["degraded_reason"] is None


def test_green_allowlisted_select_still_translates(engaged):
    sql = "SELECT count(*), max(ts) FROM history WHERE status = ?"
    assert pg.translate(sql) == \
        "SELECT count(*), max(ts) FROM history WHERE status = %s"
    rows = pg.read_authoritative(sql, ("done",))
    assert rows is not None and engaged.connects == 1


def test_green_call_inside_string_literal_is_not_a_call():
    sql = "SELECT count(*) FROM history WHERE url = 'datetime(' AND id = ?"
    assert pg.translate(sql) == sql.replace("?", "%s")


def test_green_sql_words_and_cast_are_not_calls():
    sql = ("SELECT CAST(id AS TEXT) FROM history WHERE id IN (?, ?) "
           "AND (status = 'done' OR EXISTS (SELECT 1 FROM history))")
    assert pg.translate(sql) is not None


@pytest.mark.parametrize("fn", [
    "julianday", "total", "printf", "group_concat", "date", "time",
    "strftime", "datetime", "ifnull", "instr", "round", "DATETIME"])
def test_sqlite_only_functions_refuse(fn):
    assert pg.translate(f"SELECT {fn}(ts) FROM history") is None


def test_writes_unaffected_by_allowlist():
    sql = "UPDATE history SET ts = datetime('now') WHERE id = ?"
    assert pg.translate(sql) == sql.replace("?", "%s")


def test_shadow_keeps_comparing_compat_now_reads(monkeypatch):
    # Negative control: the allowlist gates authoritative reads only. The
    # shadow sends _shadow_dialect's rendering (datetime() resolves to the
    # installed compat function), never an 'untranslatable' skip.
    sent = []
    monkeypatch.setattr(pg, "shadow_read_enabled", lambda: True)
    monkeypatch.setattr(pg, "_compat_ready", lambda force=False: True)
    monkeypatch.setattr(pg, "_record_shape", lambda sql, same: None)
    monkeypatch.setattr(pg, "_shadow_fetch",
                        lambda sql, params: (sent.append(sql) or [(1,)], False))
    assert pg.translate(RED_SQL) is None
    assert pg.shadow_compare(RED_SQL, (), [(1,)]) is True
    assert sent == [pg._shadow_dialect(RED_SQL)[0]]
    assert "datetime(" in sent[0]


_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _strings(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr):
            yield "".join(v.value if isinstance(v, ast.Constant) else "x"
                          for v in node.values)


def test_census_in_scope_select_functions_outside_allowlist():
    # Denominator: every str constant in bulk_downloader/*.py that
    # _in_read_scope() accepts (a SELECT reading only mirrored tables).
    names = set()
    for path in sorted((REPO / "bulk_downloader").glob("*.py")):
        for s in _strings(ast.parse(path.read_text(encoding="utf-8"))):
            if not pg._in_read_scope(s):
                continue
            for m in _CALL.finditer(pg._LITERAL.sub("''", s)):
                names.add(m.group(1).lower())
    names -= pg._PAREN_WORDS | {"cast"}
    assert "count" in names          # positive control: the probe can say yes
    assert names - pg._FUNCTION_ALLOWLIST == {
        "date", "datetime", "julianday", "strftime"}
