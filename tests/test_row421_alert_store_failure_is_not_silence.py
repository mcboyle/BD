"""Row 421 -- a broken alert_events table must not silence every alert.

``_condition_was_held`` returned False on ANY exception and ``_record_event``
swallowed its own INSERT failure, so in ``evaluate()`` a rule whose metric
genuinely trips recorded nothing, the duration gate answered False forever
while the table was locked/corrupt/unwritable, ``fired`` stayed False and
``_fire_actions`` never ran.  The production path made the silence total:
``bg_scheduler`` registered ``alerts_engine.evaluate`` every 60 seconds
through a wrapper that DISCARDED the returned dict, so ``fired=0`` was
observed by no one.

Why it matters for operator truth: the disk-full, failure-rate and
account-health rules exist to warn that the capture/download machinery is
degrading, and precisely when the database -- the shared substrate those
alerts watch -- is failing, the alerting layer reported nothing rather than
UNKNOWN.  The operator learns of the outage from the outage.

THE MIRROR DEFECT IS TESTED TOO.  An alerting layer that reports UNKNOWN when
it CAN measure is as broken as one that reported silence when it could not,
so the positive control fires a genuinely held condition exactly once with
cooldown honoured, and the negative control proves a rule that is not
tripping fires nothing and raises no spurious UNKNOWN.
"""
from __future__ import annotations

import contextlib
import sqlite3

import pytest

from bulk_downloader import alerts_engine as ae


BD_GATE_SCOPE = "module"


_TRIPPING_RULE = {
    "id": "row421_disk_low",
    "name": "Disk space low",
    "metric": "row421_disk_free_gb",
    "op": "<=", "threshold": 10.0,
    "duration_minutes": 0, "severity": "fail",
    "cooldown_minutes": 60,
    "actions": ["dashboard"],
}


# ── the DB boundary, instrumented ─────────────────────────────────────────

class _BrokenCursor:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _BrokenEventStore:
    """alert_events is unreadable AND unwritable; everything else works.

    A store that failed EVERY statement would also break ``_ensure_tables``
    and ``_evaluate_metric``, and the rule would then never trip -- an
    unrelated early refusal manufacturing a green (CLAUDE.md A7).  So the
    breakage is confined to the exact table the row names, and the counts
    below prove which statements actually failed.
    """

    def __init__(self):
        self.select_failures = 0
        self.insert_failures = 0
        self.other_statements = 0

    @contextlib.contextmanager
    def conn(self):
        yield self

    def execute(self, sql, params=()):
        verb = sql.strip().split()[0].upper()
        if "alert_events" in sql and verb in ("SELECT", "INSERT"):
            if verb == "SELECT":
                self.select_failures += 1
            else:
                self.insert_failures += 1
            raise sqlite3.OperationalError(
                "database is locked: alert_events")
        self.other_statements += 1
        return _BrokenCursor()


class _ReadableButUnwritableStore:
    """The other half of the row's shape: the table READS fine and refuses
    every write (a read-only database file, a full disk, a failed WAL).

    Kept distinct from ``_BrokenEventStore`` deliberately.  When the SELECT
    raises, ``_record_event`` is never reached, so a single store could not
    prove the row's "the INSERT actually fails AND the SELECT actually
    raises" -- one arm each, with its own nonzero counts.
    """

    def __init__(self):
        self.cx = sqlite3.connect(":memory:", isolation_level=None)
        self.selects = 0
        self.insert_failures = 0

    @contextlib.contextmanager
    def conn(self):
        yield self

    def execute(self, sql, params=()):
        verb = sql.strip().split()[0].upper()
        if "alert_events" in sql and verb == "INSERT":
            self.insert_failures += 1
            raise sqlite3.OperationalError(
                "attempt to write a readonly database: alert_events")
        if "alert_events" in sql and verb == "SELECT":
            self.selects += 1
        return self.cx.execute(sql, params)


class _HealthyEventStore:
    """One real in-memory SQLite, reused across ``db_conn()`` calls."""

    def __init__(self):
        self.cx = sqlite3.connect(":memory:", isolation_level=None)
        self.statements = 0

    @contextlib.contextmanager
    def conn(self):
        yield self

    def execute(self, sql, params=()):
        self.statements += 1
        return self.cx.execute(sql, params)

    def rows(self, kind=None):
        if kind is None:
            return self.cx.execute(
                "SELECT rule_id, kind FROM alert_events ORDER BY id").fetchall()
        return self.cx.execute(
            "SELECT rule_id, kind FROM alert_events WHERE kind = ? ORDER BY id",
            (kind,)).fetchall()


def _install_store(monkeypatch, store):
    """Patch the db module the helpers import at CALL time."""
    from bulk_downloader import db as _db
    monkeypatch.setattr(_db, "db_conn", store.conn)
    return store


def _install_metric(monkeypatch, value):
    """A measured metric, so the rule's tripping is not itself in doubt."""
    seen = {"calls": 0}

    def _metric(name, *, s_cfg=None):
        seen["calls"] += 1
        assert name == _TRIPPING_RULE["metric"], name
        return value

    monkeypatch.setattr(ae, "_evaluate_metric", _metric)
    return seen


def _count_fire_actions(monkeypatch):
    fired = []
    monkeypatch.setattr(
        ae, "_fire_actions", lambda rule, value: fired.append((rule["id"], value)))
    return fired


# ── RED arm: an unreadable/unwritable event store must report UNKNOWN ─────

@pytest.mark.parametrize("mode", ["unreadable", "unwritable"])
def test_an_unavailable_event_store_reports_unknown_not_silence(monkeypatch,
                                                                mode):
    store = _install_store(
        monkeypatch,
        _BrokenEventStore() if mode == "unreadable"
        else _ReadableButUnwritableStore())
    metric = _install_metric(monkeypatch, 2.0)          # 2.0 <= 10.0 -> trips
    fired = _count_fire_actions(monkeypatch)

    report = ae.evaluate(s_cfg={}, rules=[_TRIPPING_RULE])

    # PRECONDITIONS, all asserted before any verdict.
    assert metric["calls"] == 1, metric
    assert report["evaluated"] == 1, report
    result = report["results"][0]
    assert result["value"] == 2.0, result
    assert result["tripping"] is True, (
        "the rule must genuinely trip, or this measures nothing")
    assert report["tripping"] == 1, report
    # The DB boundary really failed, with exact nonzero counts, and the two
    # arms fail at DIFFERENT statements -- which is the point of running both.
    if mode == "unreadable":
        assert store.select_failures == 1, store.select_failures
        assert store.insert_failures == 0, (
            "the SELECT raises first, so the INSERT is never reached")
        expected_words = "database is locked: alert_events"
    else:
        assert store.selects == 1, store.selects
        assert store.insert_failures == 1, store.insert_failures
        expected_words = "attempt to write a readonly database: alert_events"

    # THE CONTRACT: an unavailable event store is UNKNOWN, not a silent
    # fired=False.
    assert report.get("unknown") == 1, report
    assert result.get("store_unavailable") is True, result
    # The store's OWN words, not a paraphrase (CLAUDE.md A7).
    assert expected_words in result.get("error", ""), result

    # It still must NOT fire: a fire that cannot be recorded cannot be
    # cooldown-gated either, and would storm every 60 seconds.
    assert result["fired"] is False, result
    assert report["fired"] == 0, report
    assert fired == [], fired


def test_the_unknown_state_is_logged_as_well_as_returned(monkeypatch, capsys):
    """"nothing raises or logs" was half the defect."""
    _install_store(monkeypatch, _BrokenEventStore())
    _install_metric(monkeypatch, 2.0)
    _count_fire_actions(monkeypatch)

    ae.evaluate(s_cfg={}, rules=[_TRIPPING_RULE])

    captured = capsys.readouterr()
    assert "row421_disk_low" in captured.err, captured.err
    assert "database is locked: alert_events" in captured.err, captured.err


def test_the_scheduler_wrapper_no_longer_discards_the_result(monkeypatch,
                                                             capsys):
    """The production path. ``_run_alerts`` dropped evaluate()'s dict on the
    floor, so ``fired=0`` over a broken store was observed by no one."""
    from bulk_downloader import bg_scheduler as bg

    store = _install_store(monkeypatch, _BrokenEventStore())
    _install_metric(monkeypatch, 2.0)
    _count_fire_actions(monkeypatch)
    monkeypatch.setattr(ae, "DEFAULT_RULES", [_TRIPPING_RULE])

    monkeypatch.setattr(bg, "_tasks", {})
    bg.register_default_tasks(s_cfg_getter=lambda: {})

    # PRECONDITION: the task exists and is the one the row names.
    task = bg._tasks.get("alerts_engine.evaluate")
    assert task is not None, sorted(bg._tasks)
    assert task["last_status"] == "pending", task["last_status"]

    bg._run_one("alerts_engine.evaluate", task)

    # PRECONDITION: the DB boundary really failed during that run, so the
    # verdict below is about an unavailable store and not about some other
    # error the wrapper happened to raise.
    assert store.select_failures >= 1, store.select_failures
    assert task["run_count"] == 1, task["run_count"]
    # VISIBLE PAST THE WRAPPER: /api/bg/status reads last_status/last_error.
    assert task["last_status"] == "error", task
    assert "row421_disk_low" in task["last_error"], task["last_error"]
    assert "alert_events" in task["last_error"], task["last_error"]

    snapshot = bg.status()
    entry = [t for t in snapshot["tasks"]
             if t["name"] == "alerts_engine.evaluate"]
    assert len(entry) == 1, snapshot["tasks"]
    assert entry[0]["last_status"] == "error", entry[0]
    assert "alert_events" in entry[0]["last_error"], entry[0]


# ── POSITIVE CONTROL: a healthy store still fires, exactly once ───────────

def test_a_held_condition_on_a_healthy_store_still_fires_once(monkeypatch):
    """THE MIRROR DEFECT.  An alerting layer that refuses to fire when it CAN
    measure is as broken as one that stayed silent when it could not."""
    store = _install_store(monkeypatch, _HealthyEventStore())
    metric = _install_metric(monkeypatch, 2.0)
    fired = _count_fire_actions(monkeypatch)

    first = ae.evaluate(s_cfg={}, rules=[_TRIPPING_RULE])

    assert metric["calls"] == 1, metric
    assert first["results"][0]["tripping"] is True, first
    assert first.get("unknown", 0) == 0, first
    assert "error" not in first["results"][0], first["results"][0]
    assert first["fired"] == 1, first
    assert first["results"][0]["fired"] is True, first
    assert fired == [("row421_disk_low", 2.0)], fired

    # Exact nonzero counts of what was WRITTEN, read back from the store.
    kinds = [k for _rid, k in store.rows()]
    assert kinds.count("tripped") == 1, store.rows()
    assert kinds.count("fired") == 1, store.rows()

    # COOLDOWN HONOURED: a second pass inside the 60-minute window must not
    # fire again, and must not report UNKNOWN either.
    second = ae.evaluate(s_cfg={}, rules=[_TRIPPING_RULE])
    assert second["tripping"] == 1, second
    assert second["fired"] == 0, second
    assert second.get("unknown", 0) == 0, second
    assert fired == [("row421_disk_low", 2.0)], fired
    kinds = [k for _rid, k in store.rows()]
    assert kinds.count("fired") == 1, store.rows()


# ── NEGATIVE CONTROL: a quiet rule stays quiet, with no spurious UNKNOWN ──

def test_a_rule_that_is_not_tripping_reports_nothing_at_all(monkeypatch):
    store = _install_store(monkeypatch, _HealthyEventStore())
    metric = _install_metric(monkeypatch, 900.0)        # 900 <= 10 is False
    fired = _count_fire_actions(monkeypatch)

    report = ae.evaluate(s_cfg={}, rules=[_TRIPPING_RULE])

    assert metric["calls"] == 1, metric
    assert report["evaluated"] == 1, report
    assert report["results"][0]["tripping"] is False, report
    assert report["tripping"] == 0, report
    assert report["fired"] == 0, report
    assert report.get("unknown", 0) == 0, report
    assert "error" not in report["results"][0], report["results"][0]
    assert fired == [], fired
    assert store.rows() == [], store.rows()


def test_an_unavailable_metric_is_still_its_own_distinct_state(monkeypatch):
    """A7: do not collapse "the metric could not be computed" into "the event
    store is unavailable" -- they lead to different repairs."""
    _install_store(monkeypatch, _HealthyEventStore())
    _install_metric(monkeypatch, None)
    fired = _count_fire_actions(monkeypatch)

    report = ae.evaluate(s_cfg={}, rules=[_TRIPPING_RULE])

    result = report["results"][0]
    assert result["error"] == "metric unavailable", result
    assert result.get("store_unavailable") is not True, result
    assert report.get("unknown", 0) == 0, report
    assert report["fired"] == 0 and fired == [], (report, fired)


def test_the_scheduler_stays_ok_when_the_store_is_healthy(monkeypatch):
    """The mirror defect at the scheduler seam: a wrapper that now raises on
    a healthy pass would mark the task error every minute forever."""
    from bulk_downloader import bg_scheduler as bg

    _install_store(monkeypatch, _HealthyEventStore())
    _install_metric(monkeypatch, 900.0)
    _count_fire_actions(monkeypatch)
    monkeypatch.setattr(ae, "DEFAULT_RULES", [_TRIPPING_RULE])
    monkeypatch.setattr(bg, "_tasks", {})
    bg.register_default_tasks(s_cfg_getter=lambda: {})

    task = bg._tasks["alerts_engine.evaluate"]
    bg._run_one("alerts_engine.evaluate", task)

    assert task["run_count"] == 1, task
    assert task["last_status"] == "ok", task
    assert task["last_error"] == "", task["last_error"]


def test_the_helpers_raise_the_distinct_exception_rather_than_answering_false(
        monkeypatch):
    """The mechanism, asserted directly. ``False`` from
    ``_condition_was_held`` is a MEASURED answer (no such event); an
    unreadable table must not produce the same value."""
    store = _install_store(monkeypatch, _BrokenEventStore())

    with pytest.raises(ae.AlertEventStoreUnavailable) as held:
        ae._condition_was_held("row421_disk_low", duration_minutes=0)
    assert "alert_events" in str(held.value), str(held.value)
    assert store.select_failures == 1, store.select_failures

    with pytest.raises(ae.AlertEventStoreUnavailable):
        ae._last_fire_age("row421_disk_low")
    assert store.select_failures == 2, store.select_failures

    with pytest.raises(ae.AlertEventStoreUnavailable):
        ae._record_event("row421_disk_low", "tripped", 1.0, "m")
    assert store.insert_failures == 1, store.insert_failures

    # ... and the healthy store still answers, rather than raising.
    healthy = _install_store(monkeypatch, _HealthyEventStore())
    assert ae._condition_was_held("row421_disk_low", duration_minutes=0) is False
    assert ae._last_fire_age("row421_disk_low") is None
    ae._record_event("row421_disk_low", "tripped", 1.0, "m")
    assert [k for _r, k in healthy.rows()] == ["tripped"], healthy.rows()
    assert ae._condition_was_held("row421_disk_low", duration_minutes=0) is True
