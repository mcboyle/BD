"""v3.66.723 -- AF5: the automation safety nets become VISIBLE.

Two guardrails ship, work, and report to NOBODY:

  706 restore rehearsal -- the verdict IS persisted (BD_HOME/<state file>) and
      `backup_verify.last_rehearsal()` reads it back. That reader is called by
      NOTHING but its own test. No route, no surface.

  708 pipeline halt -- WORSE. `run_host_cycle` returns {halted, halt_reason,
      errors}; `scheduled_pipeline` passes it up; and `bg_scheduler`'s task
      wrapper THROWS THE RETURN VALUE AWAY. It is not persisted anywhere. The
      comment above that code says:

          "The halt must be VISIBLE: a guardrail the operator cannot see fired
           is not a guardrail. This surfaces on the AF5 timeline / AF7 digest."

      It surfaces on neither. The comment ASSERTS the property instead of
      DERIVING it -- an aspirational doc, which is how this codebase's dominant
      failure shape begins.

AF5 is the readout: persist the cycle verdict, and put both verdicts behind one
route the GUI can render.

NON-NEGOTIABLE -- UNKNOWN IS A THIRD STATE, AND IT FAILS.
    The whole point of a safety net readout is to answer "did it fire?". A
    readout that has never seen a run must say SO. It must not say "ok" because
    it found no failures -- that is the house failure shape exactly: a check
    whose denominator excludes the thing being asked about, reporting clean
    truthfully and uselessly. "I have never run" and "I ran and passed" are
    DIFFERENT ANSWERS and the operator must be able to tell them apart.

    So: no rehearsal on record -> state "unknown", ok False.
        no cycle on record      -> state "unknown", ok False.
    Silence is not consent.

RED-first: every test below fails on pristine v3.66.722 (automation_status does
not exist; /api/automation/status 404s; the scheduler discards the verdict).
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    return tmp_path


def _fresh_db(tmp_path, monkeypatch):
    """Point the db at a throwaway file so the cycle table is genuinely empty."""
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    from bulk_downloader import db as _db

    monkeypatch.setattr(_db, "_resolve_db_path", lambda: str(tmp_path / "queue.db"))
    return _db


# ── UNKNOWN IS A THIRD STATE ────────────────────────────────────────────────
def test_rehearsal_never_run_reads_unknown_not_ok(home, monkeypatch):
    """No verdict on disk. The readout must NOT infer 'fine' from 'no failures'."""
    from bulk_downloader import automation_status as AS

    r = AS.rehearsal_status()
    assert r["state"] == "unknown"
    assert r["ok"] is False, "'never rehearsed' must never read as ok"


def test_pipeline_never_ran_reads_unknown_not_ok(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    from bulk_downloader import automation_status as AS

    p = AS.pipeline_status()
    assert p["state"] == "unknown"
    assert p["ok"] is False, "'never ran' must never read as ok"


def test_overall_status_is_not_ok_when_nothing_is_known(tmp_path, monkeypatch):
    """The aggregate must not launder two unknowns into a green light."""
    _fresh_db(tmp_path, monkeypatch)
    from bulk_downloader import automation_status as AS

    s = AS.status()
    assert s["ok"] is False
    assert s["rehearsal"]["state"] == "unknown"
    assert s["pipeline"]["state"] == "unknown"


# ── 706: the persisted rehearsal verdict reaches a surface ──────────────────
def test_rehearsal_failure_is_read_back(home, monkeypatch):
    from bulk_downloader import backup_verify as BV
    from bulk_downloader import automation_status as AS

    BV._write_rehearsal({"ok": False, "path": "/b/x.zip", "checked_at": 1.0,
                         "age_days": 3.0, "error": "archive is corrupt"})
    r = AS.rehearsal_status()
    assert r["state"] == "failed"
    assert r["ok"] is False
    assert "corrupt" in r["error"]


def test_rehearsal_pass_is_read_back(home, monkeypatch):
    from bulk_downloader import backup_verify as BV
    from bulk_downloader import automation_status as AS

    BV._write_rehearsal({"ok": True, "path": "/b/x.zip", "checked_at": 1.0,
                         "age_days": 0.5, "error": ""})
    r = AS.rehearsal_status()
    assert r["state"] == "ok"
    assert r["ok"] is True
    assert r["age_days"] == 0.5


# ── 708: the halt is PERSISTED, not dropped ─────────────────────────────────
def test_halted_cycle_is_persisted_and_readable(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    from bulk_downloader import automation_status as AS

    AS.record_cycle({"ran": True, "reason": "ok", "hosts": 2,
                     "halted": ["a.test"]})
    p = AS.pipeline_status()
    assert p["state"] == "halted"
    assert p["ok"] is False
    assert p["halted"] == ["a.test"]
    assert p["hosts"] == 2


def test_clean_cycle_is_persisted_and_reads_ok(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    from bulk_downloader import automation_status as AS

    AS.record_cycle({"ran": True, "reason": "ok", "hosts": 2, "halted": []})
    p = AS.pipeline_status()
    assert p["state"] == "ok"
    assert p["ok"] is True


def test_latest_cycle_wins(tmp_path, monkeypatch):
    """A halt last night that is CLEAN this morning must not still read halted."""
    _fresh_db(tmp_path, monkeypatch)
    from bulk_downloader import automation_status as AS

    AS.record_cycle({"ran": True, "reason": "ok", "hosts": 1, "halted": ["a.test"]})
    AS.record_cycle({"ran": True, "reason": "ok", "hosts": 1, "halted": []})
    assert AS.pipeline_status()["state"] == "ok"


def test_disabled_pipeline_does_not_forge_a_run(tmp_path, monkeypatch):
    """A no-op pass (toggle OFF) is NOT evidence the pipeline is healthy. It must
    not write a row that would read as 'ok' -- that would manufacture a green
    light out of a feature that never executed."""
    _fresh_db(tmp_path, monkeypatch)
    from bulk_downloader import automation_status as AS

    AS.record_cycle({"ran": False, "reason": "disabled"})
    assert AS.pipeline_status()["state"] == "unknown"


# ── the scheduler must STOP DISCARDING the verdict ──────────────────────────
def test_scheduled_pipeline_persists_its_verdict(tmp_path, monkeypatch):
    """The 708 bug in one assertion: scheduled_pipeline ran, halted, and the
    verdict must survive the call rather than being returned into the void."""
    _fresh_db(tmp_path, monkeypatch)
    from bulk_downloader import automation_pipeline as AP
    from bulk_downloader import automation_status as AS

    monkeypatch.setattr(AP, "_enabled", lambda: True)
    monkeypatch.setattr(AP, "run_pipeline",
                        lambda h, c, p, **k: {"ok": False, "host": h,
                                              "halted_at": "lint"})
    AP.scheduled_pipeline(policy={"trusted_auto_hosts": ["a.test"]})

    p = AS.pipeline_status()
    assert p["state"] == "halted", "the halt was returned and then thrown away"
    assert p["halted"] == ["a.test"]


# ── the route ───────────────────────────────────────────────────────────────
def _client():
    from bulk_downloader.app import app

    return app.test_client()


def test_status_route_exists_and_reports_both_nets(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    from bulk_downloader import backup_verify as BV

    BV._write_rehearsal({"ok": False, "path": "/b/x.zip", "checked_at": 1.0,
                         "age_days": 9.0, "error": "archive is corrupt"})
    from bulk_downloader import automation_status as AS

    AS.record_cycle({"ran": True, "reason": "ok", "hosts": 1, "halted": ["a.test"]})

    r = _client().get("/api/automation/status")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is False
    assert j["rehearsal"]["state"] == "failed"
    assert j["pipeline"]["state"] == "halted"


def test_status_route_is_read_only(tmp_path, monkeypatch):
    """A readout must not be a lever. POST must not be routed here."""
    _fresh_db(tmp_path, monkeypatch)
    r = _client().post("/api/automation/status", json={})
    assert r.status_code in (404, 405)
