"""Autonomy governance foundation (Phase A). Implements the AUTONOMY_POLICY_v2 model.

THIS MODULE ADDS NO AUTONOMOUS EXECUTION. It is the brakes and the dashboard, built
before any engine:
  * the 2-D policy MODEL (action classes by write-target × involvement levels),
  * versioned policy STORAGE + a stable policy HASH,
  * an immutable DECISION-SNAPSHOT recorder + reader,
  * an INDEPENDENT kill switch (separate file from the policy it freezes),
  * a guardrail REGISTRY + the enforcement primitive `can_autonomously`.

It does NOT order queues, refresh dashboards, mutate templates/selectors/profiles,
write the corpus, retire debt, launch captures, or run logins. Those are later phases
(Class B automation, guardrails, oracle) and stay OFF here. The policy defaults to the
safe inherited posture (suggest-don't-apply); every correctness-critical and
footprint-affecting action defaults to Approve-each. Several Class-C actions are
PERMANENTLY pinned at Approve-each and can never advance to auto.

All persistent state lives under the runtime store root (outside the package, never
shipped). All writes are atomic (.tmp + replace) and utf-8. No module-level I/O.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.cockpit_core import confine, tasks_root

POLICY_MODEL_VERSION = "v2"  # the design doc this implements

# ── Axis 1: action classes, by WRITE-TARGET (not by action name) ─────────────
ACTION_CLASSES: Dict[str, Dict[str, str]] = {
    "A": {"name": "Advisory / read-only",
          "write_target": "nothing, or an advisory-only store",
          "desc": "Produces information a human reads; changes no actionable state."},
    "B": {"name": "Reversible housekeeping",
          "write_target": "derived / regenerable state only",
          "desc": "Writes only regenerable state no correctness decision depends on."},
    "C": {"name": "Correctness-critical mutation",
          "write_target": "live config or corpus",
          "desc": "Writes state the system trusts as truth; silent failure possible."},
    "D": {"name": "Footprint-affecting",
          "write_target": "a third party (irreversible)",
          "desc": "Authenticated/behavioral interaction with external sites; "
                  "cannot be rolled back."},
}

# ── Axis 2: human-involvement levels ─────────────────────────────────────────
LEVELS = {0: "observe", 1: "suggest", 2: "approve_each", 3: "auto_with_guardrails"}
_LEVEL_OF_NAME = {v: k for k, v in LEVELS.items()}

# Per-class guardrails required before Level 3 is even AVAILABLE. Class A is
# read-only (Level 3 not applicable); Class D is irreversible (Level 3 never
# available regardless of guardrails).
CLASS_LEVEL3_GUARDRAILS: Dict[str, Optional[List[str]]] = {
    "A": None,  # n/a — read-only
    "B": ["kill_switch", "action_logging", "reversibility"],
    "C": ["kill_switch", "decision_snapshot", "correctness_oracle", "rollback",
          "blast_radius_cap", "backlog_cap", "review_window_failclosed",
          "self_throttle"],
    "D": None,  # never — irreversible (see doc §2/§5.9)
}

# Class-C actions PERMANENTLY pinned at Approve-each (never advance to auto).
# These write ground truth or change governance. (Doc §6.)
PINNED_APPROVE_EACH = (
    "corpus_writes",
    "validation_debt_retirement",
    "correction_debt_retirement",
    "finding_confirmation_or_falsification",
    "login_template_changes",
    "release_approval",
    "posture_policy_changes",
    "automation_policy_changes",   # the policy's own edit path is human-only (§7)
)

# Safe default posture (the system as it stands today).
DEFAULT_LEVELS = {"A": "observe", "B": "suggest", "C": "approve_each",
                  "D": "approve_each"}

# Guardrails that genuinely exist at the end of Phase A. Everything else is built
# in later phases and is reported as not-yet-built.
# Guardrails that genuinely exist in code, by release phase. Hand-maintained manifest
# (autonomy_policy must not import the modules that implement them — would be
# circular). Phase A: kill_switch + decision_snapshot. Phase B: action_logging +
# reversibility (tools/autonomy_housekeeping.py). Phase C: rollback, blast_radius_cap,
# backlog_cap, review_window_failclosed, self_throttle (tools/autonomy_guardrails.py).
# Phase E: correctness_oracle (tools/autonomy_oracle.py).
#
# IMPORTANT: a complete guardrail set does NOT enable Class C auto. Class C still
# defaults to Approve-each, and any actual autonomous action additionally requires a
# per-site eligibility grant (tools/autonomy_oracle.class_c_site_eligible) which is
# empty by design — issuing such a grant is a separate governance decision with no
# mechanism in this build. There is also no Class C apply path in the codebase.
_BUILT_GUARDRAILS = {"kill_switch", "decision_snapshot",          # Phase A
                     "action_logging", "reversibility",           # Phase B
                     "rollback", "blast_radius_cap", "backlog_cap",
                     "review_window_failclosed", "self_throttle",  # Phase C
                     "correctness_oracle"}                         # Phase E
_PHASE_A_GUARDRAILS_BUILT = _BUILT_GUARDRAILS  # back-compat alias
_ALL_GUARDRAILS = sorted({g for gs in CLASS_LEVEL3_GUARDRAILS.values() if gs for g in gs})


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ── storage roots (runtime; never shipped) ──────────────────────────────────
def _gov_root() -> Path:
    p = tasks_root() / "governance"
    return p


def _policy_path() -> Path:
    return _gov_root() / "automation_policy.json"


def _audit_path() -> Path:
    return _gov_root() / "automation_policy_audit.jsonl"


def _freeze_path() -> Path:
    # SEPARATE file from the policy — the kill switch must not be governed by the
    # policy it can freeze (doc §5.6).
    return _gov_root() / "automation_freeze.json"


def _guardrails_path() -> Path:
    return _gov_root() / "automation_guardrails.json"


def _snapshots_dir() -> Path:
    return _gov_root() / "decision_snapshots"


def _atomic_write_json(p: Path, obj: Any) -> None:
    """Atomic write per the project's .tmp + replace state-file invariant."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(p)


# ── policy model + hash ──────────────────────────────────────────────────────
def _default_policy() -> Dict[str, Any]:
    return {"policy_model": POLICY_MODEL_VERSION, "version": 0,
            "levels": dict(DEFAULT_LEVELS), "thresholds": {},
            "updated_at": None, "updated_by": None}


def load_policy() -> Dict[str, Any]:
    """The current policy. Computed-on-read default (safe posture) when no file
    exists yet — nothing runtime-stateful ships in the package."""
    p = _policy_path()
    if not p.is_file():
        return _default_policy()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return _default_policy()
    d.setdefault("policy_model", POLICY_MODEL_VERSION)
    d.setdefault("version", 0)
    base = dict(DEFAULT_LEVELS)
    base.update(d.get("levels") or {})
    d["levels"] = base
    d.setdefault("thresholds", {})
    return d


def policy_hash(policy: Optional[Dict[str, Any]] = None) -> str:
    """Stable hash of the load-bearing policy state (levels + thresholds + model).
    Decision snapshots reference this so a change is reproducible against the rules
    as they were."""
    policy = policy or load_policy()
    canon = json.dumps({"policy_model": policy.get("policy_model"),
                        "levels": policy.get("levels"),
                        "thresholds": policy.get("thresholds")},
                        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ── guardrail registry ───────────────────────────────────────────────────────
def guardrail_registry() -> Dict[str, Dict[str, Any]]:
    """Which Level-3 guardrails exist. Phase A ships kill_switch + decision_snapshot;
    everything else is built later and reported not-yet-built."""
    built = set(_PHASE_A_GUARDRAILS_BUILT)
    p = _guardrails_path()
    if p.is_file():
        try:
            saved = json.loads(p.read_text(encoding="utf-8"))
            for k, v in (saved or {}).items():
                if v.get("built"):
                    built.add(k)
        except Exception:
            pass
    return {g: {"built": g in built} for g in _ALL_GUARDRAILS}


def _missing_guardrails(action_class: str) -> List[str]:
    req = CLASS_LEVEL3_GUARDRAILS.get(action_class)
    if not req:
        return []
    reg = guardrail_registry()
    return [g for g in req if not reg.get(g, {}).get("built")]


# ── the matrix report (read-only) ────────────────────────────────────────────
def _level3_availability(action_class: str) -> Dict[str, Any]:
    """Whether Level 3 (auto) is available for a class right now, and why not."""
    if action_class == "A":
        return {"available": False, "reason": "read-only — autonomy not applicable"}
    if CLASS_LEVEL3_GUARDRAILS.get(action_class) is None:
        return {"available": False,
                "reason": "irreversible (third-party) — auto never available; "
                          "Approve-each with periodic re-authorization (doc §5.9)"}
    missing = _missing_guardrails(action_class)
    if missing:
        return {"available": False, "missing_guardrails": missing,
                "reason": "required guardrails not yet built"}
    return {"available": True, "reason": "all required guardrails present"}


def policy_report() -> Dict[str, Any]:
    """The 2-D matrix as the operator sees it: per class — configured level, the
    available ceiling and why, required vs present guardrails. Read-only."""
    pol = load_policy()
    levels = pol["levels"]
    classes = []
    for c, meta in ACTION_CLASSES.items():
        avail3 = _level3_availability(c)
        req = CLASS_LEVEL3_GUARDRAILS.get(c) or []
        reg = guardrail_registry()
        # ceiling: highest currently-selectable level
        if c == "A":
            ceiling = "observe"
        elif avail3["available"]:
            ceiling = "auto_with_guardrails"
        else:
            ceiling = "approve_each"
        classes.append({
            "class": c, "name": meta["name"], "write_target": meta["write_target"],
            "description": meta["desc"],
            "configured_level": levels.get(c, DEFAULT_LEVELS[c]),
            "selectable_ceiling": ceiling,
            "level3": avail3,
            "level3_guardrails_required": req,
            "level3_guardrails_present": [g for g in req if reg.get(g, {}).get("built")],
        })
    return {
        "policy_model": pol.get("policy_model"),
        "policy_version": pol.get("version"),
        "policy_hash": policy_hash(pol),
        "levels_legend": LEVELS,
        "classes": classes,
        "pinned_approve_each": list(PINNED_APPROVE_EACH),
        "kill_switch": freeze_status(),
        "_note": "2-D autonomy policy (action class by write-target \u00d7 involvement "
                 "level). Classes move independently. Level 3 (auto) is unavailable "
                 "until a class's guardrails are built; Class A is read-only and "
                 "Class D is irreversible (never auto). Pinned actions never advance "
                 "past Approve-each. Read-only view; nothing here executes.",
    }


# ── audited policy mutation (operator-driven; nothing auto-calls this) ───────
def _append_audit(entry: Dict[str, Any]) -> None:
    p = _audit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, separators=(",", ":"))
    with p.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_audit(limit: int = 200) -> List[Dict[str, Any]]:
    p = _audit_path()
    if not p.is_file():
        return []
    out = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    except Exception:
        pass
    return out[-limit:]


def set_policy_level(action_class: str, level: str, by: str,
                     reason: str = "") -> Dict[str, Any]:
    """Set a class's involvement level. AUDITED + atomic + versioned. This is the
    code-level hard gate (doc §3/§10 step 2): it REFUSES Level 3 when the class's
    guardrails are absent, when the class is read-only (A) or irreversible (D). The
    policy's own edit path is governance — this is operator-driven; nothing in the
    system auto-calls it."""
    if action_class not in ACTION_CLASSES:
        return {"ok": False, "error": f"unknown action class {action_class!r}"}
    if level not in _LEVEL_OF_NAME:
        return {"ok": False, "error": f"unknown level {level!r}"}
    if not by:
        return {"ok": False, "error": "an operator identity (by) is required"}

    if level == "auto_with_guardrails":
        if action_class == "A":
            return {"ok": False, "error": "Class A is read-only; Level 3 not applicable"}
        if CLASS_LEVEL3_GUARDRAILS.get(action_class) is None:
            return {"ok": False, "error": "Class D is irreversible; auto is never "
                    "available (Approve-each + periodic re-authorization)"}
        missing = _missing_guardrails(action_class)
        if missing:
            return {"ok": False, "error": "Level 3 blocked — guardrails not built",
                    "missing_guardrails": missing}

    pol = load_policy()
    before = dict(pol["levels"])
    if before.get(action_class) == level:
        return {"ok": True, "unchanged": True, "levels": before,
                "version": pol.get("version", 0)}
    pol["levels"][action_class] = level
    pol["version"] = int(pol.get("version", 0)) + 1
    pol["updated_at"] = _now()
    pol["updated_by"] = by
    new_hash = policy_hash(pol)
    _atomic_write_json(_policy_path(), pol)
    _append_audit({"ts": _now(), "by": by, "action": "set_policy_level",
                   "class": action_class, "from": before.get(action_class),
                   "to": level, "version": pol["version"], "policy_hash": new_hash,
                   "reason": reason})
    return {"ok": True, "class": action_class, "from": before.get(action_class),
            "to": level, "version": pol["version"], "policy_hash": new_hash}


def safety_demote(action_class: str, to_level: str, by: str,
                  reason: str = "") -> Dict[str, Any]:
    """Authorized automatic DE-ESCALATION (doc §5.7). Can ONLY lower a class's
    involvement level — never raise it. This is distinct from `set_policy_level` (the
    deliberate human governance edit, which the pin guards): a safety demotion reduces
    autonomy and is therefore always safe. Records a distinct audit action so it is
    never confused with a human governance change. Refuses to raise."""
    if action_class not in ACTION_CLASSES:
        return {"ok": False, "error": f"unknown action class {action_class!r}"}
    if to_level not in _LEVEL_OF_NAME:
        return {"ok": False, "error": f"unknown level {to_level!r}"}
    pol = load_policy()
    cur = pol["levels"].get(action_class, DEFAULT_LEVELS.get(action_class))
    if _LEVEL_OF_NAME[to_level] >= _LEVEL_OF_NAME.get(cur, 0):
        # never raise (or no-op equal) — a demotion must strictly reduce autonomy
        return {"ok": True, "unchanged": True, "level": cur,
                "_note": "safety_demote only lowers; current level is already at or "
                         "below the requested level"}
    before = cur
    pol["levels"][action_class] = to_level
    pol["version"] = int(pol.get("version", 0)) + 1
    pol["updated_at"] = _now()
    pol["updated_by"] = f"safety:{by}"
    new_hash = policy_hash(pol)
    _atomic_write_json(_policy_path(), pol)
    _append_audit({"ts": _now(), "by": by, "action": "safety_demote",
                   "class": action_class, "from": before, "to": to_level,
                   "version": pol["version"], "policy_hash": new_hash,
                   "reason": reason})
    return {"ok": True, "class": action_class, "from": before, "to": to_level,
            "version": pol["version"], "demoted": True}


# ── independent kill switch (doc §5.6) ───────────────────────────────────────
def freeze_status() -> Dict[str, Any]:
    p = _freeze_path()
    if not p.is_file():
        return {"frozen": False, "by": None, "ts": None, "reason": None}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {"frozen": bool(d.get("frozen")), "by": d.get("by"),
                "ts": d.get("ts"), "reason": d.get("reason")}
    except Exception:
        # if the freeze file is unreadable, FAIL SAFE — treat as frozen
        return {"frozen": True, "by": None, "ts": None,
                "reason": "freeze file unreadable — failing safe (treated as frozen)"}


def is_frozen() -> bool:
    """The contract every future automation checks before acting. Independent of the
    policy file."""
    return freeze_status()["frozen"]


def freeze(by: str, reason: str = "") -> Dict[str, Any]:
    """Emergency global freeze. Stops all automation (when automation exists). Written
    to a SEPARATE file so a bad policy edit cannot disable the brakes. Audited."""
    if not by:
        return {"ok": False, "error": "an operator identity (by) is required"}
    rec = {"frozen": True, "by": by, "ts": _now(), "reason": reason}
    _atomic_write_json(_freeze_path(), rec)
    _append_audit({"ts": rec["ts"], "by": by, "action": "freeze", "reason": reason})
    return {"ok": True, **rec}


def unfreeze(by: str, reason: str = "") -> Dict[str, Any]:
    if not by:
        return {"ok": False, "error": "an operator identity (by) is required"}
    rec = {"frozen": False, "by": by, "ts": _now(), "reason": reason}
    _atomic_write_json(_freeze_path(), rec)
    _append_audit({"ts": rec["ts"], "by": by, "action": "unfreeze", "reason": reason})
    return {"ok": True, **rec}


# ── immutable decision snapshots (doc §5.1) ──────────────────────────────────
def record_decision_snapshot(decision: Dict[str, Any], by: str) -> Dict[str, Any]:
    """Record an immutable snapshot for a proposed/autonomous change. Captures the
    decision inputs PLUS the policy state in effect (version + hash) so the decision
    is reproducible against the rules as they were. In Phase A nothing produces
    decisions yet — this is the recorder, ready for later phases. Returns the stored
    record (with its id)."""
    pol = load_policy()
    ts = _now()
    sid = hashlib.sha256(
        (by + "|" + json.dumps(decision, sort_keys=True, default=str)).encode("utf-8")
    ).hexdigest()[:16]
    rec = {
        "id": sid, "ts": ts, "recorded_by": by,
        "policy_version": pol.get("version"), "policy_hash": policy_hash(pol),
        "policy_model": pol.get("policy_model"),
        "decision": {
            "action_class": decision.get("action_class"),
            "action": decision.get("action"),
            "site": decision.get("site"),
            "input_evidence": decision.get("input_evidence"),
            "captures_used": decision.get("captures_used"),
            "scores_used": decision.get("scores_used"),
            "thresholds_used": decision.get("thresholds_used"),
            "artifacts_used": decision.get("artifacts_used"),
            "proposed_change": decision.get("proposed_change"),
        },
        "_immutable": True,
    }
    out = _snapshots_dir() / f"{sid}.json"
    if out.exists():  # never overwrite an immutable record
        return {"ok": True, "id": sid, "duplicate": True}
    _atomic_write_json(out, rec)
    return {"ok": True, "id": sid, "policy_hash": rec["policy_hash"],
            "policy_version": rec["policy_version"]}


def list_decision_snapshots(limit: int = 100) -> List[Dict[str, Any]]:
    d = _snapshots_dir()
    if not d.is_dir():
        return []
    rows = []
    for f in sorted(d.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            rows.append({"id": r.get("id"), "ts": r.get("ts"),
                         "recorded_by": r.get("recorded_by"),
                         "action_class": (r.get("decision") or {}).get("action_class"),
                         "action": (r.get("decision") or {}).get("action"),
                         "site": (r.get("decision") or {}).get("site"),
                         "policy_version": r.get("policy_version"),
                         "policy_hash": r.get("policy_hash")})
        except Exception:
            continue
    return rows[-limit:]


def get_decision_snapshot(snapshot_id: str) -> Optional[Dict[str, Any]]:
    safe = confine(f"{snapshot_id}.json", _snapshots_dir())
    if not safe or not safe.is_file():
        return None
    try:
        return json.loads(safe.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── the enforcement primitive (always False in Phase A) ──────────────────────
def can_autonomously(action_class: str) -> Dict[str, Any]:
    """May this class act autonomously RIGHT NOW? The primitive future phases call
    before any auto action. False whenever frozen, when the configured level is not
    Level 3, when guardrails are missing, or for read-only/irreversible classes.
    In Phase A this is False for every class — nothing is auto-eligible yet."""
    if is_frozen():
        return {"allowed": False, "reason": "automation is frozen (kill switch)"}
    pol = load_policy()
    level = pol["levels"].get(action_class, DEFAULT_LEVELS.get(action_class))
    if level != "auto_with_guardrails":
        return {"allowed": False, "level": level,
                "reason": "class is not at Level 3 (auto)"}
    avail = _level3_availability(action_class)
    if not avail.get("available"):
        return {"allowed": False, "level": level, **avail}
    return {"allowed": True, "level": level,
            "policy_hash": policy_hash(pol)}


def governance_status() -> Dict[str, Any]:
    """Compact summary for the status view header."""
    pol = load_policy()
    reg = guardrail_registry()
    return {
        "policy_model": pol.get("policy_model"),
        "policy_version": pol.get("version"),
        "policy_hash": policy_hash(pol),
        "levels": pol.get("levels"),
        "kill_switch": freeze_status(),
        "guardrails_built": sorted([g for g, v in reg.items() if v["built"]]),
        "guardrails_pending": sorted([g for g, v in reg.items() if not v["built"]]),
        "pinned_actions": len(PINNED_APPROVE_EACH),
        "decision_snapshots": len(list_decision_snapshots()),
        "any_class_autonomous": any(
            can_autonomously(c)["allowed"] for c in ACTION_CLASSES),
        "_note": "Phase A governance foundation. No class is autonomous; Class B "
                 "automation, rollback, oracle, and the rest of the guardrails are "
                 "later phases. Read-only view.",
    }
