"""Promotion Activity (Phase G / G6) — append-only AUDIT of governance-state transitions.

This is the record that ties G1–G5 together. As a site moves through the governance
pipeline — gaining or losing evidence-qualification (G1), its trust crossing the floor
(G3), its evidence going overdue for re-validation (G4) — this log captures the movement
for audit.

Crucially, it records movement; it does **not** cause it, and it never PROMOTES or applies
anything. Because there is no Class C apply path, `participation_eligible` never transitions
to True — the log shows evidence/trust/validation movements with participation pinned at
False throughout. "Promotion Activity" names the *governance* pipeline, not an applied
change.

Mechanics:
  * `record_transition(site, field, before, after, by, reason)` — append one transition to
    the append-only log.
  * `scan_and_record(by)` — compute each site's current governance state, diff against the
    last snapshot, append a transition for every changed field, and save the new snapshot.
    Host-scheduled (cron/CLI), never a cockpit button. This is the writer that ties the
    chain together.
  * `activity_log`, `site_activity`, `promotion_overview`, `promotion_status` — read-only
    views over the log.

The activity log is append-only (it only grows; matching the guardrail alerts log); the
snapshot is a state file written atomically (`.tmp` + replace, UTF-8). No module-level I/O.
It performs no forbidden mutation (no corpus/policy/credential writes, no apply, no
promotion of a real change).
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import autonomy_oracle as ao
from tools import autonomy_eligibility as el
from tools import autonomy_trust as atr
from tools import autonomy_validation as av

TRACKED_FIELDS = ("evidence_qualified", "participation_eligible", "trust_eligible",
                  "oracle_tier", "validation_status")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _promo_root() -> Path:
    from tools.cockpit_core import tasks_root
    return tasks_root() / "governance" / "promotion"


def _activity_path() -> Path:
    return _promo_root() / "activity.jsonl"


def _snapshot_path() -> Path:
    return _promo_root() / "snapshot.json"


def _append_activity(rec: Dict[str, Any]) -> None:
    p = _activity_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:        # append-only audit log
        f.write(json.dumps(rec) + "\n")


def _read_activity(limit: int = 200) -> List[Dict[str, Any]]:
    p = _activity_path()
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out[-limit:]


def _load_snapshot() -> Dict[str, Any]:
    p = _snapshot_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_snapshot(snap: Dict[str, Any]) -> None:
    p = _snapshot_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    tmp.replace(p)


def _state_of(site: str) -> Dict[str, Any]:
    """Current governance state for a site, composed from G1/G3/G4. Read-only."""
    elig = el.evaluate_site(site)
    return {"evidence_qualified": elig["evidence_qualified"],
            "participation_eligible": elig["participation_eligible"],  # always False
            "trust_eligible": atr.trust_eligible(site),
            "oracle_tier": ao.oracle_verdict(site)["tier"],
            "validation_status": av.validation_schedule(site)["status"]}


def record_transition(site: str, field: str, before: Any, after: Any,
                      by: str = "system", reason: str = "") -> Dict[str, Any]:
    """Append one governance-state transition to the append-only audit log. This records a
    transition; it never promotes or applies anything."""
    rec = {"ts": _now(), "site": site, "field": field, "before": before, "after": after,
           "by": by, "reason": reason}
    _append_activity(rec)
    return {"ok": True, **rec}


def scan_and_record(by: str = "system", sites: Optional[List[str]] = None) -> Dict[str, Any]:
    """Diff each site's current governance state against the last snapshot and append a
    transition for every changed field. Host-scheduled; never a cockpit button. Records
    movement only — never promotes or applies. First scan establishes a baseline and
    records nothing."""
    sites = sites if sites is not None else ao._all_sites()
    snap = _load_snapshot()
    new_snap: Dict[str, Any] = {}
    transitions: List[Dict[str, Any]] = []
    for s in sites:
        cur = _state_of(s)
        new_snap[s] = cur
        prev = snap.get(s, {})
        for f in TRACKED_FIELDS:
            if f in prev and prev.get(f) != cur.get(f):
                transitions.append(record_transition(s, f, prev.get(f), cur.get(f),
                                                      by=by, reason="scan"))
    _save_snapshot(new_snap)
    return {"ok": True, "scanned": len(sites), "transitions": len(transitions),
            "changes": transitions,
            "_note": "Append-only audit of governance-state transitions. Records movement "
                     "(e.g. a site losing evidence-qualification, or trust crossing the "
                     "floor); it never PROMOTES or applies anything. participation_eligible "
                     "never transitions to True (no Class C apply path)."}


def activity_log(limit: int = 200) -> Dict[str, Any]:
    """Recent governance transitions (oldest first within the tail). Read-only."""
    rows = _read_activity(limit)
    return {"entries": rows, "count": len(rows),
            "_note": "Append-only audit log. Nothing here was applied — these are "
                     "governance-state transitions, not applied changes."}


def site_activity(site: str, limit: int = 200) -> Dict[str, Any]:
    """Transition history for one site. Read-only."""
    rows = [r for r in _read_activity(10000) if r.get("site") == site][-limit:]
    return {"site": site, "entries": rows, "count": len(rows),
            "_note": "Per-site governance transition history. Read-only."}


def promotion_overview(sites: Optional[List[str]] = None) -> Dict[str, Any]:
    """Current governance state per site + recent transition count. Read-only."""
    sites = sites if sites is not None else ao._all_sites()
    rows = [{"site": s, **_state_of(s)} for s in sites]
    recent = _read_activity(50)
    return {"sites": rows, "site_count": len(rows),
            "recent_transition_count": len(recent),
            "any_participation_eligible": any(r["participation_eligible"] for r in rows),
            "_note": "Read-only. participation_eligible is False everywhere (no apply "
                     "path). The activity log records governance movement, never an "
                     "applied promotion."}


def promotion_status() -> Dict[str, Any]:
    """Compact status for the cockpit header. Read-only."""
    rows = _read_activity(10000)
    return {"total_transitions": len(rows),
            "tracked_fields": list(TRACKED_FIELDS),
            "_note": "Append-only governance-transition audit. Ties together eligibility, "
                     "trust, and validation movement. It records transitions; it never "
                     "promotes or applies anything."}
