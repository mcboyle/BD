"""lifecycle_drift.py — drift detection + toggle-gated lifecycle responses (A5).

Sub-waves 3 (low-teeth) + 4 (high-teeth), built on the keystone (sub-wave 2)
and the toggle/state substrate (sub-wave 1).

The teeth ladder, every rung default-OFF:
  L1 sweep              — per enabled template: drift vs gold + selector staleness (read-only)
  L2 validation_gate    — rc=1 if any enabled template drifted past threshold (read-only)
  L3 flag needs_review  — advisory flag; template STAYS usable (mild write)
  L4 quarantine         — status enabled->quarantined; template stops being used (write)
  L5 repair             — re-derive -> keystone diff -> swap; lands at REVIEWED, not enabled (write)

Mechanism vs automation (the core safety separation):
  * `_flag` / `_quarantine` / `_repair` are MECHANISMS — they act unconditionally
    (so an operator or a test can drive them directly).
  * `auto_*_if_enabled` are the AUTOMATION wrappers — each checks its toggle via
    lifecycle_automation.is_enabled and NO-OPS when off. The sweep calls only the
    auto wrappers, so nothing mutates unless its toggle (and, for the mutators,
    the keystone) is on. Default-OFF therefore == today's behaviour.

Every write that changes a template goes through the keystone snapshot first, so
it is rollback-able. Repair deliberately lands at `reviewed` (passed the gold
diff) — re-enabling is a separate explicit step, never automatic.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import lifecycle_automation as la
from . import template_keystone as tk

_SUFFIX = ".template.json"


def _reviewed_dir(reviewed_dir=None) -> Path:
    if reviewed_dir is not None:
        return Path(reviewed_dir)
    return tk._project_root() / "templates" / "reviewed"


def _host_of(path: Path) -> str:
    return path.name[:-len(_SUFFIX)]


def _enabled_templates(reviewed_dir=None) -> List[Path]:
    rd = _reviewed_dir(reviewed_dir)
    if not rd.is_dir():
        return []
    out = []
    for fp in sorted(rd.glob("*" + _SUFFIX)):
        try:
            t = json.loads(fp.read_text("utf-8"))
        except Exception:
            continue
        if t.get("status") == la.STATUS_ENABLED:
            out.append(fp)
    return out


def _selector_stale(host: str) -> Optional[bool]:
    """Runtime selector staleness from selector_drift, if available."""
    try:
        from . import selector_drift
        return bool(selector_drift.is_stale(host))
    except Exception:
        return None


# ── L1: sweep (read-only) ────────────────────────────────────────────────────

def sweep(reviewed_dir=None) -> Dict[str, Any]:
    """Per enabled template: structural drift vs gold + runtime staleness.
    Pure read — never mutates. The connective tissue that surfaces 'which
    templates need attention' (the gap AUTOMATION_POLICY named)."""
    rows = []
    for fp in _enabled_templates(reviewed_dir):
        host = _host_of(fp)
        try:
            cand = json.loads(fp.read_text("utf-8"))
        except Exception:
            continue
        dr = tk.drift_against_gold(host, cand, reviewed_dir=reviewed_dir)
        rows.append({
            "host": host,
            "drift": dr.get("drift", 0) if dr.get("ok") else None,
            "stale": _selector_stale(host),
            "baseline": dr.get("baseline"),
        })
    flagged = [r for r in rows if (r["drift"] or 0) > 0 or r["stale"]]
    return {"ok": True, "checked": len(rows), "needing_attention": len(flagged),
            "rows": rows}


# ── L2: validation gate (read-only) ──────────────────────────────────────────

def validation_gate(reviewed_dir=None, max_drift: int = 0) -> Dict[str, Any]:
    """rc=1 if any enabled template drifted beyond `max_drift`. For use as an
    optional release/CI gate. Read-only."""
    s = sweep(reviewed_dir)
    offenders = [r for r in s["rows"] if (r["drift"] or 0) > max_drift]
    return {"ok": True, "rc": 1 if offenders else 0,
            "max_drift": max_drift, "offenders": offenders,
            "checked": s["checked"]}


# ── mechanisms (act unconditionally; used by operator/tests/auto wrappers) ────

def _atomic_write(fp: Path, obj: Dict[str, Any]) -> None:
    tmp = fp.with_suffix(fp.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), "utf-8")
    os.replace(tmp, fp)  # atomic


def _flag(host: str, reason: str = "", reviewed_dir=None) -> Dict[str, Any]:
    """L3 mechanism: set the advisory needs_review flag. Template STAYS enabled
    and usable. Snapshots gold first so even this benign write is rollback-able."""
    h = tk._safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    rd = _reviewed_dir(reviewed_dir)
    fp = rd / f"{h}{_SUFFIX}"
    if not fp.is_file():
        return {"ok": False, "error": "template not found"}
    tk.snapshot_gold(h, reviewed_dir=rd)
    t = json.loads(fp.read_text("utf-8"))
    t[la.NEEDS_REVIEW_FLAG] = True
    if reason:
        t["needs_review_reason"] = reason[:200]
    t["needs_review_at"] = int(time.time())
    _atomic_write(fp, t)
    return {"ok": True, "flagged": h, "still_usable": True}


def _fire_template_quarantined(host: str, reason: str, kind: str,
                               evidence) -> bool:
    """Emit `template.quarantined`, exception-isolated (a plugin error must
    never break a quarantine). Value-safe payload."""
    try:
        from . import plugins as _plugins
        _plugins.fire_hook("template.quarantined", {
            "host": host, "reason": (reason or "")[:200],
            "kind": kind, "evidence": evidence,
        })
        return True
    except Exception:
        return False


def _quarantine(host: str, reason: str = "", reviewed_dir=None, *,
                kind: str = "drift", evidence=None) -> Dict[str, Any]:
    """L4 mechanism: status enabled->quarantined (template stops being matched).

    A3 first-class quarantine: take an A0 GENERATIONAL backup first (restorable;
    ABORT if it fails — never quarantine without a recovery point), then the
    legacy single gold .bak, validate the transition, record kind + evidence,
    write atomically, and fire `template.quarantined`. A `risky` quarantine is
    marked ``auto_promotable=False`` so A5 can never auto-promote it."""
    h = tk._safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    rd = _reviewed_dir(reviewed_dir)
    fp = rd / f"{h}{_SUFFIX}"
    if not fp.is_file():
        return {"ok": False, "error": "template not found"}
    t = json.loads(fp.read_text("utf-8"))
    cur = t.get("status")
    try:
        la.assert_transition(cur, la.STATUS_QUARANTINED)
    except ValueError as e:
        return {"ok": False, "error": str(e)[:160]}
    # A0 keystone gate: a restorable generational backup BEFORE any write. A
    # backup failure ABORTS the quarantine (live untouched) — first-class
    # quarantine is always recoverable.
    try:
        from . import template_backup as _tb
        _bk = _tb.backup_template(h, reviewed_dir=rd, reason=f"quarantine:{kind}")
    except Exception as _e:
        _bk = {"ok": False, "error": f"backup module error: {_e}"[:120]}
    if not _bk.get("ok"):
        return {"ok": False,
                "error": f"backup failed; quarantine aborted: {_bk.get('error')}"}
    tk.snapshot_gold(h, reviewed_dir=rd)
    t["status"] = la.STATUS_QUARANTINED
    t["quarantined_at"] = int(time.time())
    t["quarantine_kind"] = kind
    if reason:
        t["quarantine_reason"] = reason[:200]
    if evidence is not None:
        t["quarantine_evidence"] = evidence
    if kind == "risky":
        # A risky-content quarantine must never be eligible for A5 auto-promote.
        t["auto_promotable"] = False
    _atomic_write(fp, t)
    _fire_template_quarantined(h, reason, kind, evidence)
    return {"ok": True, "quarantined": h, "kind": kind,
            "auto_promotable": t.get("auto_promotable", True),
            "backup_ts": _bk.get("ts"),
            "usable": la.is_usable(la.STATUS_QUARANTINED)}


def _repair(host: str, fresh_template: Dict[str, Any], *,
            reviewed_dir=None, max_drift: int = 0) -> Dict[str, Any]:
    """L5 mechanism: re-derive -> keystone diff -> swap IF drift within tolerance,
    then land at REVIEWED (passed the gold diff). Re-enabling stays a separate
    explicit step — repair never auto-returns a template to service."""
    h = tk._safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    if not isinstance(fresh_template, dict):
        return {"ok": False, "error": "fresh_template must be a dict"}
    rd = _reviewed_dir(reviewed_dir)
    # land the repaired candidate at reviewed (not enabled)
    repaired = dict(fresh_template)
    repaired["status"] = la.STATUS_REVIEWED
    repaired["repaired_at"] = int(time.time())
    res = tk.safe_overwrite(h, repaired, reviewed_dir=rd,
                            gate=lambda drift: drift <= max_drift)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}
    if not res.get("swapped"):
        return {"ok": True, "repaired": False, "drift": res.get("drift"),
                "reason": "drift exceeded tolerance; live untouched, stage retained"}
    return {"ok": True, "repaired": True, "drift": res.get("drift"),
            "landed_status": la.STATUS_REVIEWED}


# ── automation wrappers (toggle-gated; the sweep calls these) ─────────────────

def auto_flag_if_enabled(host: str, reason: str = "", reviewed_dir=None) -> Dict[str, Any]:
    if not la.is_enabled("auto_flag"):
        return {"ok": True, "skipped": "auto_flag disabled"}
    return _flag(host, reason, reviewed_dir=reviewed_dir)


def auto_quarantine_if_enabled(host: str, reason: str = "", reviewed_dir=None) -> Dict[str, Any]:
    # is_enabled double-gates: toggle AND keystone_available.
    if not la.is_enabled("auto_quarantine"):
        return {"ok": True, "skipped": "auto_quarantine disabled or keystone absent"}
    return _quarantine(host, reason, reviewed_dir=reviewed_dir)


def auto_quarantine_on_drift_if_enabled(host: str, bundle: Dict[str, Any], *,
                                        reviewed_dir=None) -> Dict[str, Any]:
    """A3: route an A1 drift bundle to auto-quarantine. Acts ONLY on an
    over-threshold bundle (recommendation == "quarantine") and only with the
    auto_quarantine toggle + keystone. The bundle is recorded as evidence."""
    if (bundle or {}).get("recommendation") != "quarantine":
        return {"ok": True, "skipped": "below threshold (recommendation != quarantine)"}
    if not la.is_enabled("auto_quarantine"):
        return {"ok": True, "skipped": "auto_quarantine disabled or keystone absent"}
    ev = {"drift": bundle.get("drift"), "ts": bundle.get("ts"),
          "threshold": bundle.get("threshold"),
          "diff_sample": (bundle.get("diff_lines") or [])[:5]}
    return _quarantine(host, f"drift={bundle.get('drift')} over threshold",
                       reviewed_dir=reviewed_dir, kind="drift", evidence=ev)


def auto_quarantine_risky_if_enabled(host: str, reason: str = "", *,
                                     reviewed_dir=None, evidence=None) -> Dict[str, Any]:
    """A3: quarantine a risky-selector template (kind="risky" -> never
    auto-promotable). Gated by auto_quarantine toggle + keystone."""
    if not la.is_enabled("auto_quarantine"):
        return {"ok": True, "skipped": "auto_quarantine disabled or keystone absent"}
    return _quarantine(host, reason, reviewed_dir=reviewed_dir,
                       kind="risky", evidence=evidence)


def auto_repair_if_enabled(host: str, fresh_template: Dict[str, Any], *,
                           reviewed_dir=None, max_drift: int = 0) -> Dict[str, Any]:
    if not la.is_enabled("auto_repair"):
        return {"ok": True, "skipped": "auto_repair disabled or keystone absent"}
    return _repair(host, fresh_template, reviewed_dir=reviewed_dir, max_drift=max_drift)


def _refresh(host: str, fresh_template: Dict[str, Any], *,
             reviewed_dir=None, max_drift: int = 0) -> Dict[str, Any]:
    """L5' mechanism: refresh an ENABLED template with a fresh capture that
    still matches gold within tolerance — the template STAYS enabled (kept
    current + in service). Distinct from repair, which lands a broken/quarantined
    template at `reviewed`. Refresh requires the template to currently be enabled
    and the fresh candidate to pass the gold-drift gate; otherwise live is left
    untouched (the keystone guarantees the snapshot + no-swap-on-reject)."""
    h = tk._safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    if not isinstance(fresh_template, dict):
        return {"ok": False, "error": "fresh_template must be a dict"}
    rd = _reviewed_dir(reviewed_dir)
    fp = rd / f"{h}{_SUFFIX}"
    if not fp.is_file():
        return {"ok": False, "error": "template not found"}
    cur = json.loads(fp.read_text("utf-8")).get("status")
    if cur != la.STATUS_ENABLED:
        return {"ok": False, "error": f"refresh requires an enabled template (is {cur!r})"}
    refreshed = dict(fresh_template)
    refreshed["status"] = la.STATUS_ENABLED          # stays in service
    refreshed["refreshed_at"] = int(time.time())
    res = tk.safe_overwrite(h, refreshed, reviewed_dir=rd,
                            gate=lambda drift: drift <= max_drift)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}
    if not res.get("swapped"):
        return {"ok": True, "refreshed": False, "drift": res.get("drift"),
                "reason": "drift exceeded tolerance; live untouched, stage retained"}
    return {"ok": True, "refreshed": True, "drift": res.get("drift"),
            "landed_status": la.STATUS_ENABLED}


def auto_refresh_if_enabled(host: str, fresh_template: Dict[str, Any], *,
                            reviewed_dir=None, max_drift: int = 0) -> Dict[str, Any]:
    if not la.is_enabled("auto_refresh"):
        return {"ok": True, "skipped": "auto_refresh disabled or keystone absent"}
    return _refresh(host, fresh_template, reviewed_dir=reviewed_dir, max_drift=max_drift)


# ── A2: self-healing refresh (full gate -> A0 write -> re-verify -> keep/restore)
def _default_gate(t: Dict[str, Any]) -> List[str]:
    """The full promote gate: blocking lint + promote_gate_errors. Lazy imports
    keep this module import-light and sidestep the template_manager<->lifecycle
    cycle (function-local). Returns a list of error strings ([] == clean)."""
    errs: List[str] = []
    try:
        from . import selector_lint as _sl
        issues = _sl.lint_template(t or {})
        if _sl.has_blocking_issues(issues):
            errs.extend(i.to_dict().get("message", "blocking selector") for i in issues)
    except Exception as e:
        errs.append(f"lint unavailable: {e}"[:120])
    try:
        from .template_manager import promote_gate_errors as _pge
        errs.extend(_pge(t or {}))
    except Exception as e:
        errs.append(f"gate unavailable: {e}"[:120])
    return errs


def _default_verify(t: Dict[str, Any]) -> bool:
    """Post-write health check: a written template is healthy iff it still has
    no blocking lint and passes the promote gate."""
    return not _default_gate(t)


def self_heal(host: str, candidate: Dict[str, Any], *, reviewed_dir=None,
              max_drift: int = 0, gate_fn=None, verify_fn=None,
              normalize: bool = True) -> Dict[str, Any]:
    """A2: self-healing refresh of an ENABLED host from a fresh capture.

    Flow: normalize -> FULL gate -> A0 backup + write (drift-gated) -> RE-VERIFY
    -> keep, or AUTO-RESTORE on regression. No operator checkpoint when every
    gate is green and the backup succeeds; any uncertainty (gate failure, drift
    over tolerance, post-write regression) stages for review or auto-restores --
    live is never left broken. Gated by the auto_refresh toggle (keystone-gated).
    `gate_fn`/`verify_fn` default to the real promote gate; injectable for tests.
    """
    if not la.is_enabled("auto_refresh"):
        return {"ok": True, "skipped": "auto_refresh disabled or keystone absent"}
    h = tk._safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    rd = _reviewed_dir(reviewed_dir)
    fp = rd / f"{h}{_SUFFIX}"
    if not fp.is_file():
        return {"ok": True, "handled": False, "reason": "no live template (first version)"}
    try:
        cur = json.loads(fp.read_text("utf-8")).get("status")
    except Exception:
        return {"ok": True, "handled": False, "reason": "live template parse failed"}
    if cur != la.STATUS_ENABLED:
        return {"ok": True, "handled": False,
                "reason": f"self-heal targets enabled hosts only (is {cur!r})"}

    cand = dict(candidate)
    if normalize:
        try:
            from .template_normalize import normalize_draft as _nd
            cand = _nd(cand)
        except Exception:
            pass

    # FULL GATE — any failure stages for review; never writes a non-passing
    # capture over a serving template.
    errs = (gate_fn or _default_gate)(cand)
    if errs:
        return {"ok": True, "handled": True, "self_healed": False,
                "staged_for_review": True, "gate_errors": list(errs)[:5],
                "reason": "gate failed; staged for review (live untouched)"}

    # A0 backup + drift-gated write (safe_overwrite snapshots + backs up first,
    # and leaves live UNTOUCHED if the drift gate rejects the swap).
    refreshed = dict(cand)
    refreshed["status"] = la.STATUS_ENABLED          # stays in service
    refreshed["refreshed_at"] = int(time.time())
    res = tk.safe_overwrite(h, refreshed, reviewed_dir=rd,
                            gate=lambda drift: drift <= max_drift)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}
    if not res.get("swapped"):
        # Uncertainty: drift exceeded tolerance -> stage for review (the keystone
        # already retained the stage; live is byte-identical).
        return {"ok": True, "handled": True, "self_healed": False,
                "staged_for_review": True, "drift": res.get("drift"),
                "reason": "drift over tolerance; staged for review"}

    # RE-VERIFY the written template; a regression AUTO-RESTORES the A0 backup.
    try:
        live_now = json.loads(fp.read_text("utf-8"))
    except Exception:
        live_now = {}
    if not (verify_fn or _default_verify)(live_now):
        from . import template_backup as _tb
        rb = _tb.restore_template(h, reviewed_dir=rd)   # latest generation = pre-write
        return {"ok": True, "handled": True, "self_healed": False,
                "restored": bool(rb.get("ok")), "drift": res.get("drift"),
                "reason": "post-write verify failed; auto-restored to pre-write gold"}

    return {"ok": True, "handled": True, "self_healed": True,
            "drift": res.get("drift"), "checkpoint": False,
            "landed_status": la.STATUS_ENABLED}


def auto_self_heal_if_enabled(host: str, candidate: Dict[str, Any], *,
                              reviewed_dir=None, max_drift: int = 0) -> Dict[str, Any]:
    """Toggle-gated entry for the capture-ingest / autonomy paths."""
    if not la.is_enabled("auto_refresh"):
        return {"ok": True, "skipped": "auto_refresh disabled or keystone absent"}
    return self_heal(host, candidate, reviewed_dir=reviewed_dir, max_drift=max_drift)


def _auto_refresh_max_drift() -> int:
    """Operator-tunable swap tolerance for capture-time auto-refresh
    (global_config `automation.auto_refresh_max_drift`, default 0 = swap only if
    the fresh candidate re-matches gold exactly). Fail-safe to 0 on any error."""
    try:
        from . import global_config
        return int(global_config.get("automation.auto_refresh_max_drift", 0))
    except Exception:
        return 0


def on_fresh_capture(host: str, candidate: Dict[str, Any], *,
                     reviewed_dir=None) -> Dict[str, Any]:
    """A5 capture-time auto-refresh dispatcher (DEFAULT-OFF / inert).

    Called when a fresh capture is promoted for a host. Routes to the
    keystone-backed refresh ONLY when an operator has turned on a capture-time
    mode AND the live template for `host` is currently ENABLED. In every default
    or ineligible case it returns {"handled": False, ...}, so the caller's normal
    write path runs unchanged (byte-identical when all toggles are off, and on a
    first promote where no enabled live template exists yet).

    Operator modes (orthogonal toggles, all default OFF; confirm wins if both on):
      auto_refresh + auto_refresh_confirm    -> snapshot gold + stage only; the
          operator confirms the swap later via template_keystone.commit_swap.
      auto_refresh + auto_refresh_on_capture -> drift-gated swap now; tolerance =
          automation.auto_refresh_max_drift (default 0). Over-tolerance leaves
          live byte-identical (keystone no-swap-on-reject) and retains the stage.
    The auto_refresh master toggle is keystone-gated, so neither swap nor stage
    is reachable without the backup keystone present.
    """
    if not la.is_enabled("auto_refresh"):
        return {"handled": False, "reason": "auto_refresh master off"}
    h = tk._safe_host(host)
    if not h:
        return {"handled": False, "reason": "invalid host"}
    rd = _reviewed_dir(reviewed_dir)
    fp = rd / f"{h}{_SUFFIX}"
    if not fp.is_file():
        return {"handled": False, "reason": "no live template (first version)"}
    try:
        cur = json.loads(fp.read_text("utf-8")).get("status")
    except Exception:
        return {"handled": False, "reason": "live template parse failed"}
    if cur != la.STATUS_ENABLED:
        return {"handled": False, "reason": f"live not enabled ({cur!r})"}

    # confirm mode is the conservative choice and wins if both are on.
    if la.is_enabled("auto_refresh_confirm"):
        snap = tk.snapshot_gold(h, reviewed_dir=rd)
        staged = dict(candidate)
        staged["status"] = la.STATUS_ENABLED
        st = tk.stage_template(h, staged, reviewed_dir=rd)
        return {"handled": True, "ok": bool(st.get("ok")), "mode": "confirm",
                "swapped": False, "staged": st.get("staged"), "gold": snap.get("gold"),
                "note": "staged for operator confirm (template_keystone.commit_swap)"}

    if la.is_enabled("auto_refresh_on_capture"):
        md = _auto_refresh_max_drift()
        out = {"handled": True, "mode": "on_capture", "max_drift": md}
        out.update(auto_refresh_if_enabled(h, candidate, reviewed_dir=rd, max_drift=md))
        return out

    return {"handled": False, "reason": "no capture-time mode enabled (sweep-driven only)"}


# ── sweep-driven response orchestration ──────────────────────────────────────

def sweep_and_respond(reviewed_dir=None) -> Dict[str, Any]:
    """Run the sweep and apply the toggle-gated responses per offender.
    Precedence: quarantine (strongest) over flag — never both. Refresh is NOT
    driven here (it needs a fresh capture, which the sweep does not have); it is
    triggered from the capture-ingest path via auto_refresh_if_enabled. With all
    response toggles off this is exactly `sweep` plus no-op wrappers (read-only)."""
    s = sweep(reviewed_dir)
    responses = []
    for r in s["rows"]:
        if not ((r["drift"] or 0) > 0 or r["stale"]):
            continue
        host = r["host"]
        reason = f"drift={r['drift']} stale={r['stale']}"
        q = auto_quarantine_if_enabled(host, reason, reviewed_dir=reviewed_dir)
        if q.get("quarantined"):
            responses.append({"host": host, "action": "quarantine", "result": q})
            continue
        f = auto_flag_if_enabled(host, reason, reviewed_dir=reviewed_dir)
        if f.get("flagged"):
            responses.append({"host": host, "action": "flag", "result": f})
        else:
            responses.append({"host": host, "action": "none",
                              "result": "responses disabled"})
    return {"ok": True, "swept": s["checked"], "needing_attention": s["needing_attention"],
            "responses": responses}


# ── A1: drift-as-a-gate (stage + route a review bundle; never auto-change) ────
# Distinct from the mutating responses above: this layer STAGES a review bundle
# (diff lines + recommendation) to a review queue and fires `drift.detected`,
# but never touches a live template. Above the drift threshold the bundle's
# recommendation escalates to `quarantine` — the routed signal A3 consumes.

_DRIFT_REVIEW_DIRNAME = ".drift_review"


def _drift_review_root(reviewed_dir=None) -> Path:
    # Mirrors the gold-backup layout: a queue dir under templates/ (the parent
    # of reviewed/), kept clear of the *.template.json glob so a staged bundle
    # is never mistaken for a template.
    return _reviewed_dir(reviewed_dir).parent / _DRIFT_REVIEW_DIRNAME


def _new_ts() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + format(time.monotonic_ns() & 0xFFFFFF, "06x")


def build_review_bundle(host: str, drift, stale, *,
                        reviewed_dir=None, threshold: int = 0) -> Dict[str, Any]:
    """Assemble (but do not write) a review bundle for one drifted host:
    the diff lines vs gold, the drift count, and a recommendation. The
    recommendation escalates to `quarantine` above `threshold` (feeds A3),
    else `review`. Read-only."""
    drift_n = int(drift or 0)
    lines: List[str] = []
    base = None
    fp = _reviewed_dir(reviewed_dir) / f"{host}{_SUFFIX}"
    if fp.is_file():
        try:
            cand = json.loads(fp.read_text("utf-8"))
            dr = tk.drift_against_gold(host, cand, reviewed_dir=reviewed_dir)
            if dr.get("ok"):
                lines = dr.get("lines") or []
                base = dr.get("baseline")
        except Exception:
            lines = []
    recommendation = "quarantine" if drift_n > int(threshold) else "review"
    return {
        "host": host,
        "ts": _new_ts(),
        "drift": drift_n,
        "stale": bool(stale),
        "baseline": base,
        "diff_lines": lines,
        "recommendation": recommendation,
        "threshold": int(threshold),
    }


def stage_review_bundle(bundle: Dict[str, Any], *, reviewed_dir=None) -> Path:
    """Write a review bundle to the review queue:
    templates/.drift_review/<host>/<ts>/bundle.json. Returns the path."""
    host = bundle["host"]
    ts = bundle["ts"]
    dest = _drift_review_root(reviewed_dir) / host / ts
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "bundle.json"
    out.write_text(json.dumps(bundle, indent=2), "utf-8")
    return out


def _fire_drift_detected(bundle: Dict[str, Any]) -> bool:
    """Emit the `drift.detected` plugin event, exception-isolated (a plugin
    error must never break the sweep). Payload is value-safe: host, drift
    count, recommendation, ts — no secret material."""
    try:
        from . import plugins as _plugins
        _plugins.fire_hook("drift.detected", {
            "host": bundle["host"],
            "drift": bundle["drift"],
            "stale": bundle["stale"],
            "recommendation": bundle["recommendation"],
            "ts": bundle["ts"],
        })
        return True
    except Exception:
        return False


def stage_drift_reviews(reviewed_dir=None, *, threshold: int = 0,
                        fire_events: bool = True) -> Dict[str, Any]:
    """For every drifted/stale enabled template, stage a review bundle and
    (optionally) fire `drift.detected`. NEVER mutates a live template — this is
    the stage-and-route gate, distinct from sweep_and_respond's mutators."""
    s = sweep(reviewed_dir)
    staged: List[Dict[str, Any]] = []
    events = 0
    for r in s["rows"]:
        if not ((r["drift"] or 0) > 0 or r["stale"]):
            continue
        bundle = build_review_bundle(r["host"], r["drift"], r["stale"],
                                     reviewed_dir=reviewed_dir, threshold=threshold)
        path = stage_review_bundle(bundle, reviewed_dir=reviewed_dir)
        staged.append({"host": r["host"], "bundle": str(path),
                       "recommendation": bundle["recommendation"]})
        if fire_events and _fire_drift_detected(bundle):
            events += 1
    return {"ok": True, "checked": s["checked"], "staged": len(staged),
            "events_fired": events, "bundles": staged}


def scheduled_sweep(reviewed_dir=None) -> Dict[str, Any]:
    """bg_scheduler entry point. TOGGLE-GATED: a complete no-op unless the
    `drift_sweep` toggle is on, so registering this task is behaviour-neutral
    by default. When on, it runs sweep_and_respond (whose mutating responses are
    each further gated by their own default-OFF toggles) AND stages a review
    bundle + fires `drift.detected` per offender (A1 stage-and-route gate)."""
    if not la.is_enabled("drift_sweep"):
        return {"ok": True, "skipped": "drift_sweep disabled"}
    resp = sweep_and_respond(reviewed_dir=reviewed_dir)
    gate = stage_drift_reviews(reviewed_dir=reviewed_dir)
    resp["review_staged"] = gate["staged"]
    resp["drift_events_fired"] = gate["events_fired"]
    return resp
