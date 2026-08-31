"""Row 421: a broken ``alert_events`` table must report UNKNOWN, never silence.

MEASURED at v3.66.1362. ``alerts_engine._condition_was_held()`` returned False
on ANY exception and ``_record_event()`` swallowed its own INSERT failure, so a
rule whose metric genuinely tripped recorded nothing, answered the duration gate
False forever, left ``result['fired']`` False and never reached
``_fire_actions()``.  Nothing raised and nothing logged, and the production path
made the silence total: ``bg_scheduler`` registers ``evaluate`` behind a wrapper
that discards the returned dict, so ``fired=0`` was observed by no one.  The
disk-full, failure-rate and account-health rules exist to warn that the
capture/download machinery is degrading; precisely when the database those
alerts watch is failing, the alerting layer reported nothing.  CLAUDE.md A7: an
unavailable measurement is UNKNOWN, never OK.

The store here is broken the way a real one breaks -- the table exists but its
schema is not the engine's, so ``CREATE TABLE IF NOT EXISTS`` does not repair
it and every SELECT and INSERT the engine issues raises at the sqlite boundary.
Each verdict test asserts that boundary produced nonzero real failures before it
judges anything, so a fixture that quietly failed to break the table cannot
manufacture a green (or a red).

The tripping rule uses ``bd_disk_free_gb``, whose value comes from
``shutil.disk_usage`` and NOT from the database -- so the metric provably trips
while the event store is unreadable, which is the exact seam under test.
"""
from __future__ import annotations

import contextlib
import sqlite3

import pytest

BD_GATE_SCOPE = "module"


# The corrupt shape: the name is taken by a table the engine cannot use.
_CORRUPT_DDL = ("CREATE TABLE alert_events("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, corrupt_marker TEXT)")

_HEALTHY_COLUMNS = ["id", "rule_id", "ts", "metric_value", "kind", "message"]


def _tripping_rule(**over) -> dict:
    """Free disk space <= 1e12 GB. True on any host this suite can run on."""
    rule = {"id": "row421_trips", "name": "Row 421 tripping rule",
            "metric": "bd_disk_free_gb", "op": "<=", "threshold": 1.0e12,
            "duration_minutes": 1, "severity": "fail",
            "cooldown_minutes": 60, "actions": []}
    rule.update(over)
    return rule


def _quiet_rule(**over) -> dict:
    """Free disk space <= 0.0 GB. False whenever the filesystem has any room."""
    rule = _tripping_rule(id="row421_quiet", name="Row 421 quiet rule",
                          threshold=0.0)
    rule.update(over)
    return rule


# --- fixtures ---------------------------------------------------------------

class _Boundary:
    """Every statement alerts_engine issues, and whether sqlite refused it."""

    def __init__(self) -> None:
        self.ok: list[str] = []
        self.failed: list[tuple[str, BaseException]] = []

    @staticmethod
    def _verb(sql: str) -> str:
        return sql.strip().split(None, 1)[0].upper() if sql.strip() else ""

    def failed_on(self, verb: str, table: str = "alert_events") -> int:
        return sum(1 for sql, _ in self.failed
                   if self._verb(sql) == verb and table in sql)

    def ok_on(self, verb: str, table: str = "alert_events") -> int:
        return sum(1 for sql in self.ok
                   if self._verb(sql) == verb and table in sql)


@pytest.fixture
def engine(clean_workdir):
    """A fresh isolated install with the alerts schema created."""
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import alerts_engine as ae
    ae._ensure_tables()
    assert _columns() == _HEALTHY_COLUMNS, (
        "precondition: the fixture did not create the engine's own "
        f"alert_events schema; got {_columns()}")
    return ae


@pytest.fixture
def s_cfg(clean_workdir) -> dict:
    return {"row421site": {"download_dir": str(clean_workdir)}}


@pytest.fixture
def boundary(monkeypatch) -> _Boundary:
    """Record every statement at the real DB boundary, re-raising unchanged."""
    from bulk_downloader import db as _db
    log = _Boundary()
    real = _db.db_conn

    class _SpyConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *a, **kw):
            try:
                cur = self._inner.execute(sql, *a, **kw)
            except BaseException as exc:      # observe, never absorb
                log.failed.append((sql, exc))
                raise
            log.ok.append(sql)
            return cur

        def __getattr__(self, name):
            return getattr(self._inner, name)

    @contextlib.contextmanager
    def _spy(path=None):
        with real(path) as cx:
            yield _SpyConn(cx)

    monkeypatch.setattr(_db, "db_conn", _spy)
    return log


# --- helpers ----------------------------------------------------------------

def _columns() -> list[str]:
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        return [r[1] for r in cx.execute("PRAGMA table_info(alert_events)")]


def _break_the_store() -> None:
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("DROP TABLE IF EXISTS alert_events")
        cx.execute(_CORRUPT_DDL)


def _make_the_store_unwritable() -> None:
    """The second break shape: reads succeed, every write is refused.

    The corrupt-schema fixture fails at the FIRST statement evaluate() issues,
    which is a read -- so on its own it can never reach _record_event() and a
    regression that restored that helper's ``except: pass`` would escape.
    """
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("""CREATE TRIGGER alert_events_readonly
                      BEFORE INSERT ON alert_events
                      BEGIN SELECT RAISE(ABORT, 'alert_events is read-only');
                      END""")


def _seed_event(rule_id: str, kind: str, ts: float, value: float = 1.0) -> None:
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("""INSERT INTO alert_events(
            rule_id, ts, metric_value, kind, message) VALUES (?,?,?,?,?)""",
            (rule_id, ts, value, kind, "seed"))


def _kind_counts(rule_id: str) -> dict:
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        rows = cx.execute("""SELECT kind, COUNT(*) FROM alert_events
                             WHERE rule_id = ? GROUP BY kind""",
                          (rule_id,)).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _row_total() -> int:
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        return int(cx.execute("SELECT COUNT(*) FROM alert_events")
                   .fetchone()[0])


def _only(results: list, rule_id: str) -> dict:
    matches = [r for r in results if r["rule_id"] == rule_id]
    assert len(matches) == 1, (
        f"expected exactly one {rule_id} result, got {matches}")
    return matches[0]


def _fire_spy(monkeypatch, ae) -> list:
    calls: list = []
    monkeypatch.setattr(ae, "_fire_actions",
                        lambda rule, value: calls.append((rule["id"], value)))
    return calls


# --- precondition: the corrupt store really breaks at the sqlite boundary ----

def test_the_corrupt_store_really_fails_at_the_sqlite_boundary(engine, boundary):
    """Prove the fixture, not the engine. Exact counts at the DB boundary.

    Runs before every verdict below: if this cannot break the table, the
    verdict tests are measuring nothing. It holds identically before and after
    the fix -- it asserts what sqlite refused, not what the engine returned.
    """
    ae = engine
    _break_the_store()

    # The engine's own repair attempt does NOT undo it.
    ae._ensure_tables()
    assert _columns() == ["id", "corrupt_marker"], (
        "CREATE TABLE IF NOT EXISTS repaired the corrupt table; the fixture "
        f"cannot break the store at all (columns={_columns()})")

    # Direct proof: both statement shapes the engine uses raise.
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        with pytest.raises(sqlite3.Error) as sel:
            cx.execute("SELECT kind, ts FROM alert_events LIMIT 1")
    assert "kind" in str(sel.value), str(sel.value)
    with _db.db_conn() as cx:
        with pytest.raises(sqlite3.Error) as ins:
            cx.execute("""INSERT INTO alert_events(
                rule_id, ts, metric_value, kind, message) VALUES (?,?,?,?,?)""",
                ("row421_trips", 1.0, 1.0, "tripped", ""))
    assert "rule_id" in str(ins.value), str(ins.value)

    # Exactly one refused statement per engine helper call, zero accepted.
    before_sel = boundary.failed_on("SELECT")
    with contextlib.suppress(Exception):
        ae._condition_was_held("row421_trips", duration_minutes=0)
    assert boundary.failed_on("SELECT") - before_sel == 1, (
        "_condition_was_held issued a number of failing SELECTs other than 1: "
        f"{boundary.failed}")

    before_ins = boundary.failed_on("INSERT")
    with contextlib.suppress(Exception):
        ae._record_event("row421_trips", "tripped", 1.0, "m")
    assert boundary.failed_on("INSERT") - before_ins == 1, (
        "_record_event issued a number of failing INSERTs other than 1: "
        f"{boundary.failed}")

    assert boundary.ok_on("SELECT") == 0 and boundary.ok_on("INSERT") == 0, (
        "a statement against the corrupt alert_events SUCCEEDED; the store is "
        f"not actually broken: ok={boundary.ok}")


# --- RED: the defect itself -------------------------------------------------

def test_a_tripping_rule_with_a_broken_store_reports_unknown(
        engine, s_cfg, boundary, monkeypatch, capsys):
    """The verdict. Before the fix: fired=False, no error, nothing logged."""
    ae = engine
    rule = _tripping_rule()

    # Precondition 1: the metric is computable and genuinely trips.
    value = ae._evaluate_metric(rule["metric"], s_cfg=s_cfg)
    assert value is not None, "precondition: the disk metric did not resolve"
    assert value <= rule["threshold"], (
        f"precondition: {value} <= {rule['threshold']} is false, so the rule "
        f"does not trip and this test would prove nothing")

    _break_the_store()
    fires = _fire_spy(monkeypatch, ae)
    capsys.readouterr()

    out = ae.evaluate(s_cfg=s_cfg, rules=[rule])
    err = capsys.readouterr().err

    # Precondition 2: the run really did hit the broken store.
    assert boundary.failed_on("SELECT") >= 1, (
        f"evaluate() never issued a failing alert_events SELECT: {boundary.ok}")
    assert boundary.ok_on("SELECT") == 0 and boundary.ok_on("INSERT") == 0, (
        f"a statement against the corrupt store succeeded: {boundary.ok}")

    result = _only(out["results"], rule["id"])
    assert out["evaluated"] == 1
    assert result["tripping"] is True and out["tripping"] == 1, (
        "the metric measurement itself must stay truthful")

    # The contract.
    assert result.get("status") == "unknown", (
        "an unreadable/unwritable alert_events store left the rule reporting "
        f"{result.get('status')!r}; A7 requires a distinct UNKNOWN state "
        f"rather than a silent fired=False. result={result}")
    assert "unavailable" in (result.get("error") or "").lower(), (
        f"the per-rule error does not name the unavailable store: {result}")
    assert out["unknown"] == 1, (
        f"evaluate() did not count the unknown rule: {out}")
    assert rule["id"] in err and "alert" in err.lower(), (
        "nothing was logged, so the state is invisible past the bg_scheduler "
        f"wrapper that discards the returned dict. stderr={err!r}")

    # And the original silence is still absent in the other direction.
    assert result["fired"] is False and out["fired"] == 0
    assert fires == [], f"_fire_actions ran against an unreadable store: {fires}"


def test_last_fire_age_never_answers_never_fired_from_a_broken_store(
        engine, boundary):
    """The cooldown reader's own seam, which no evaluate() shape can reach.

    None here means "measured, never fired" and OPENS the cooldown gate, so an
    unreadable store producing it would let a rule fire on an unmeasured
    cooldown -- the same collapse in the opposite direction. evaluate() always
    fails at an earlier statement, so this is asserted directly.
    """
    ae = engine
    rid = "row421_lastfire"

    # Measured-and-empty really does answer None on a healthy store.
    assert ae._last_fire_age(rid) is None, (
        "precondition: a healthy empty store must still report never-fired")

    _break_the_store()
    before = boundary.failed_on("SELECT")
    with pytest.raises(ae.AlertEventStoreUnavailable):
        ae._last_fire_age(rid)
    assert boundary.failed_on("SELECT") - before == 1, (
        f"expected exactly one refused SELECT, got {boundary.failed}")


def test_a_lost_tripped_insert_is_unknown_not_silence(
        engine, s_cfg, boundary, monkeypatch, capsys):
    """The write seam on its own. Reads work; the INSERT is refused.

    This is the shape the corrupt-schema test cannot reach: there the first
    read fails, so _record_event() is never called. Here the read answers
    "no history", the engine tries to record the trip, and the row is lost --
    which silently disarms the duration gate for every later pass.
    """
    ae = engine
    rule = _tripping_rule(id="row421_nowrite")
    _make_the_store_unwritable()

    # Precondition: reads work, writes do not -- both proven at the boundary.
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        assert cx.execute("SELECT kind, ts FROM alert_events LIMIT 1"
                          ).fetchone() is None, (
            "precondition: the store should read cleanly and be empty")
    with _db.db_conn() as cx:
        with pytest.raises(sqlite3.Error) as ins:
            cx.execute("""INSERT INTO alert_events(
                rule_id, ts, metric_value, kind, message) VALUES (?,?,?,?,?)""",
                (rule["id"], 1.0, 1.0, "tripped", ""))
    assert "read-only" in str(ins.value), str(ins.value)

    fires = _fire_spy(monkeypatch, ae)
    capsys.readouterr()
    # The probes above went through the same spy, so count only from here.
    before_ins = boundary.failed_on("INSERT")
    before_sel = boundary.ok_on("SELECT")
    out = ae.evaluate(s_cfg=s_cfg, rules=[rule])
    err = capsys.readouterr().err

    assert boundary.ok_on("SELECT") - before_sel >= 1, (
        f"the read half never succeeded, so this is the corrupt-schema shape "
        f"again rather than the write seam: {boundary.failed}")
    assert boundary.failed_on("INSERT") - before_ins == 1, (
        f"expected exactly one refused INSERT, got {boundary.failed}")

    result = _only(out["results"], rule["id"])
    assert result["tripping"] is True
    assert result.get("status") == "unknown", (
        f"a lost 'tripped' row read as an ordinary non-fire: {result}")
    assert out["unknown"] == 1
    assert rule["id"] in err, f"the lost write was not logged: {err!r}"
    assert fires == []
    assert _row_total() == 0, "the refused INSERT must not have landed"


def test_a_malformed_timestamp_is_unknown_not_a_quiet_false(
        engine, s_cfg, monkeypatch, capsys):
    """The row's third break shape: the table reads, the ts does not parse."""
    ae = engine
    rule = _tripping_rule(id="row421_badts")
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("""INSERT INTO alert_events(
            rule_id, ts, metric_value, kind, message)
            VALUES (?,?,?,?,?)""",
            (rule["id"], "not-a-timestamp", 1.0, "tripped", "seed"))
    with _db.db_conn() as cx:
        raw = cx.execute("SELECT ts FROM alert_events WHERE rule_id = ?",
                         (rule["id"],)).fetchone()
    assert raw is not None and raw[0] == "not-a-timestamp", (
        f"precondition: the malformed ts was not stored, got {raw!r}")

    _fire_spy(monkeypatch, ae)
    capsys.readouterr()
    out = ae.evaluate(s_cfg=s_cfg, rules=[rule])
    err = capsys.readouterr().err

    result = _only(out["results"], rule["id"])
    assert result.get("status") == "unknown", (
        f"a malformed ts read as 'condition not held' instead of UNKNOWN: "
        f"{result}")
    assert out["unknown"] == 1
    assert rule["id"] in err, f"the malformed ts was not logged: {err!r}"


def test_the_scheduler_wrapper_still_surfaces_the_unknown(
        engine, s_cfg, monkeypatch, capsys):
    """The production path. bg_scheduler discards the dict, so the log is the
    only channel that survives -- assert the discard, then assert the log."""
    ae = engine
    from bulk_downloader import bg_scheduler as bs

    saved = dict(bs._tasks)
    try:
        bs.register_default_tasks(s_cfg_getter=lambda: s_cfg)
        task = bs._tasks.get("alerts_engine.evaluate")
        assert task is not None, (
            "precondition: the alerts task is not registered, so this test is "
            f"not exercising the production path: {sorted(bs._tasks)}")
        fn = task["fn"]

        _break_the_store()
        _fire_spy(monkeypatch, ae)
        capsys.readouterr()
        returned = fn()
        err = capsys.readouterr().err
    finally:
        bs._tasks.clear()
        bs._tasks.update(saved)

    assert returned is None, (
        "precondition: the wrapper no longer discards evaluate()'s dict, so "
        "this test's premise has changed")
    assert "alert" in err.lower() and "unknown" in err.lower(), (
        "the scheduler ran a full evaluation pass against a broken event "
        f"store and said nothing. stderr={err!r}")


# --- POSITIVE CONTROL -------------------------------------------------------

def test_a_held_condition_on_a_healthy_store_fires_exactly_once(
        engine, s_cfg, monkeypatch, capsys):
    """A genuinely held condition still fires once, and cooldown still holds."""
    import time as _time
    ae = engine
    rule = _tripping_rule(id="row421_pos", duration_minutes=1,
                          cooldown_minutes=60)
    _seed_event(rule["id"], "tripped", _time.time() - 600.0)
    assert _kind_counts(rule["id"]) == {"tripped": 1}, (
        "precondition: the held-condition seed row was not written")

    fires = _fire_spy(monkeypatch, ae)
    capsys.readouterr()

    first = ae.evaluate(s_cfg=s_cfg, rules=[rule])
    r1 = _only(first["results"], rule["id"])
    assert r1["tripping"] is True
    assert r1["fired"] is True and first["fired"] == 1, (
        f"a held condition on a healthy store did not fire: {r1}")
    assert r1.get("status") == "ok" and "error" not in r1, (
        f"a healthy measured fire reported a non-ok status: {r1}")
    assert first["unknown"] == 0
    assert fires == [(rule["id"], r1["value"])], (
        f"_fire_actions call count is not exactly 1: {fires}")
    assert _kind_counts(rule["id"]) == {"tripped": 1, "fired": 1}, (
        f"exact event rows after the fire: {_kind_counts(rule['id'])}")

    # Second pass: the last event is 'fired', so the engine re-records a trip
    # and the duration gate legitimately refuses. No second fire.
    second = ae.evaluate(s_cfg=s_cfg, rules=[rule])
    r2 = _only(second["results"], rule["id"])
    assert r2["fired"] is False and second["fired"] == 0
    assert r2.get("status") == "ok"
    assert _kind_counts(rule["id"]) == {"tripped": 2, "fired": 1}

    # Third pass: age the fresh trip past duration_minutes so ONLY cooldown can
    # refuse. Without the cooldown gate this would fire a second time.
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cur = cx.execute("""UPDATE alert_events SET ts = ?
                            WHERE rule_id = ? AND kind = 'tripped'""",
                         (_time.time() - 600.0, rule["id"]))
        assert cur.rowcount == 2, (
            f"precondition: expected to age 2 tripped rows, aged {cur.rowcount}")
    assert ae._condition_was_held(rule["id"], duration_minutes=1) is True, (
        "precondition: the duration gate is not open, so the third pass would "
        "be refused by duration rather than by cooldown")
    last_fire = ae._last_fire_age(rule["id"])
    assert last_fire is not None and last_fire < 60 * 60, (
        f"precondition: cooldown is not the active gate (last fire {last_fire}s)")

    third = ae.evaluate(s_cfg=s_cfg, rules=[rule])
    r3 = _only(third["results"], rule["id"])
    assert r3["fired"] is False and third["fired"] == 0, (
        f"cooldown was not honoured: {r3}")
    assert r3.get("status") == "ok" and third["unknown"] == 0

    assert len(fires) == 1, f"total _fire_actions calls across 3 passes: {fires}"
    assert _kind_counts(rule["id"]) == {"tripped": 2, "fired": 1}, (
        f"final exact event rows: {_kind_counts(rule['id'])}")
    assert "unknown" not in capsys.readouterr().err.lower(), (
        "a fully healthy positive control logged an UNKNOWN")


# --- NEGATIVE CONTROL -------------------------------------------------------

def test_a_rule_that_is_not_tripping_reports_no_spurious_unknown(
        engine, s_cfg, monkeypatch, capsys):
    ae = engine
    rule = _quiet_rule()

    value = ae._evaluate_metric(rule["metric"], s_cfg=s_cfg)
    assert value is not None, "precondition: the disk metric did not resolve"
    assert not (value <= rule["threshold"]), (
        f"precondition: {value} <= {rule['threshold']} is TRUE, so this is not "
        f"a non-tripping rule and the control proves nothing")

    fires = _fire_spy(monkeypatch, ae)
    capsys.readouterr()
    out = ae.evaluate(s_cfg=s_cfg, rules=[rule])
    err = capsys.readouterr().err

    result = _only(out["results"], rule["id"])
    assert result["tripping"] is False
    assert result["fired"] is False and out["fired"] == 0
    assert result.get("status") == "ok", f"spurious non-ok status: {result}"
    assert "error" not in result, f"spurious error on a quiet rule: {result}"
    assert out["unknown"] == 0, f"spurious UNKNOWN on a quiet rule: {out}"
    assert fires == []
    assert _row_total() == 0, (
        f"a non-tripping rule wrote {_row_total()} alert_events row(s)")
    assert err == "", f"a quiet rule on a healthy store logged: {err!r}"


def test_a_quiet_rule_over_a_broken_store_is_also_unknown(
        engine, s_cfg, boundary, monkeypatch, capsys):
    """The store is genuinely unavailable, so 'nothing to clear' is not a
    measurement either. This is not spurious -- the negative control above
    proves the healthy quiet path stays silent."""
    ae = engine
    rule = _quiet_rule(id="row421_quiet_broken")
    _break_the_store()
    _fire_spy(monkeypatch, ae)
    capsys.readouterr()

    out = ae.evaluate(s_cfg=s_cfg, rules=[rule])
    err = capsys.readouterr().err

    assert boundary.failed_on("SELECT") >= 1, (
        f"the quiet path never touched the broken store: {boundary.ok}")
    result = _only(out["results"], rule["id"])
    assert result["tripping"] is False
    assert result.get("status") == "unknown", (
        f"a broken store on the clear path read as ok: {result}")
    assert out["unknown"] == 1
    assert rule["id"] in err
