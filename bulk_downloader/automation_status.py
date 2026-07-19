"""automation_status -- AF5: make the automation safety nets VISIBLE (v3.66.723).

Two guardrails shipped and reported to nobody:

  * 706 restore rehearsal: the verdict IS persisted and `backup_verify.
    last_rehearsal()` reads it back -- but that reader had no caller. A reader
    with no consumer is a verdict nobody hears.
  * 708 pipeline halt: `run_host_cycle` RETURNS {halted, halt_reason, errors},
    `scheduled_pipeline` passes it up, and the scheduler's task wrapper threw
    the return value away. It was persisted NOWHERE.

This module is the read layer for both, plus the missing write for the second.

THE DESIGN RULE, and it is the whole point:

    UNKNOWN IS A THIRD STATE, AND IT FAILS.

A readout exists to answer "did the net fire?". If it has never seen a run it
must SAY SO -- not report "ok" on the grounds that it found no failures. That
inference ("no failures found" -> "healthy") is this codebase's dominant bug
shape: a check whose denominator structurally excludes the thing being asked
about, reporting clean truthfully and uselessly. The 710 config ratchet read
open=0 for exactly that reason, and the 0 was never real.

So "I have never rehearsed" and "I rehearsed and it passed" are DIFFERENT
ANSWERS here, and `ok` is False for the first one. Silence is not consent.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, List, Optional

# The three states any net can be in. UNKNOWN is not a degenerate OK.
UNKNOWN = "unknown"
OK = "ok"
FAILED = "failed"
HALTED = "halted"


def _ensure_table() -> None:
    try:
        from . import db as _db

        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS automation_cycles(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                ok INTEGER NOT NULL,
                reason TEXT,
                hosts INTEGER,
                halted TEXT
            )""")
    except Exception as e:  # pragma: no cover - schema init is best-effort
        sys.stderr.write(f"[automation_status] schema init failed: {e}\n")


def record_cycle(result: Optional[Dict[str, Any]]) -> bool:
    """Persist the verdict of ONE autonomous pipeline pass.

    Returns True if a row was written.

    A pass that did NOT run (toggle off, min-spacing, error before dispatch) is
    deliberately NOT recorded. A no-op is not evidence of health, and writing it
    would manufacture a green light out of a feature that never executed --
    which is precisely the lie this module exists to stop telling.

    Never raises: this is called from the scheduler, and a readout that can take
    out the loop it reports on is worse than no readout.
    """
    if not isinstance(result, dict) or not result.get("ran"):
        return False
    _ensure_table()
    try:
        from . import db as _db

        halted = list(result.get("halted") or [])
        with _db.db_conn() as cx:
            cx.execute(
                "INSERT INTO automation_cycles(ts, ok, reason, hosts, halted) "
                "VALUES (?,?,?,?,?)",
                (time.time(), 0 if halted else 1,
                 str(result.get("reason") or "")[:200],
                 int(result.get("hosts") or 0),
                 json.dumps(halted)[:2000]),
            )
        return True
    except Exception as e:
        # Say so. A silently-dropped verdict is how 708 happened in the first place.
        sys.stderr.write(
            f"[automation_status] could not persist the cycle verdict "
            f"({type(e).__name__}: {e}); a halt will not be visible to the operator\n")
        return False


def rehearsal_status() -> Dict[str, Any]:
    """The 706 verdict, or an honest UNKNOWN."""
    verdict = None
    try:
        from . import backup_verify as _bv

        verdict = _bv.last_rehearsal()
    except Exception:
        verdict = None

    if not isinstance(verdict, dict):
        return {"state": UNKNOWN, "ok": False,
                "detail": "no restore rehearsal has ever been recorded",
                "checked_at": None, "age_days": None, "error": ""}

    ok = bool(verdict.get("ok"))
    return {
        "state": OK if ok else FAILED,
        "ok": ok,
        "detail": ("the latest backup restored cleanly" if ok
                   else "the latest backup did NOT restore"),
        "checked_at": verdict.get("checked_at"),
        "age_days": verdict.get("age_days"),
        "path": verdict.get("path") or "",
        "error": verdict.get("error") or "",
    }


def pipeline_status() -> Dict[str, Any]:
    """The 708 verdict of the MOST RECENT pass that actually ran, or UNKNOWN.

    Latest-wins: a halt last night that is clean this morning must not still
    read halted, or the operator learns to ignore it.
    """
    row = None
    try:
        from . import db as _db

        _ensure_table()
        with _db.db_conn() as cx:
            row = cx.execute(
                "SELECT ts, ok, reason, hosts, halted FROM automation_cycles "
                "ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
        row = None

    if row is None:
        return {"state": UNKNOWN, "ok": False,
                "detail": "the automation pipeline has never completed a run",
                "ran_at": None, "hosts": 0, "halted": []}

    try:
        halted: List[str] = json.loads(row["halted"] or "[]")
    except Exception:
        halted = []
    ok = not halted and bool(row["ok"])
    return {
        "state": OK if ok else HALTED,
        "ok": ok,
        "detail": ("the last pass completed on every host" if ok
                   else f"the last pass HALTED on {len(halted)} host(s)"),
        "ran_at": row["ts"],
        "reason": row["reason"] or "",
        "hosts": int(row["hosts"] or 0),
        "halted": halted,
    }


def _disco_status() -> Dict[str, Any]:
    """A-DISCO's net (v3.66.788), fail-soft. Delegated to disco_runner (the single
    owner of the disco_runs schema). A read error is UNKNOWN, never a false green."""
    try:
        from . import disco_runner as _dr
        return _dr.disco_status()
    except Exception:
        return {"state": UNKNOWN, "ok": False, "enabled": None,
                "detail": "A-DISCO status is unreadable"}


def status() -> Dict[str, Any]:
    """Both always-on nets, the opt-in A-DISCO net, and one honest aggregate.

    `ok` is True only when BOTH always-on nets (rehearsal + pipeline) are
    known-good. Two unknowns do not launder into a green light. A-DISCO is an
    OPT-IN net: it is reported for visibility but does NOT drag the aggregate --
    an operator who never turned it on should not see automation health go red.
    """
    reh = rehearsal_status()
    pipe = pipeline_status()
    return {
        "ok": bool(reh["ok"] and pipe["ok"]),
        "rehearsal": reh,
        "pipeline": pipe,
        "disco": _disco_status(),
    }
