"""template_keystone.py — backup-before-overwrite for reviewed templates (A5).

The AUTOMATION_POLICY keystone: "automated gold backup → stage → diff → swap".
It is the HARD PREREQUISITE for any write-side automation (auto-refresh,
auto-repair). Its irreducible safety guarantee:

    A reviewed template is NEVER overwritten without first snapshotting the
    current live version to its gold backup, and the live file only ever
    changes via an ATOMIC swap (os.replace) of a fully-written stage file —
    so the live template can never be left torn/partial, and any overwrite is
    rollback-able to the gold.

The DIFF is reused from tools/template_drift_report (the existing gold-vs-
candidate section diffs) — the keystone does not invent a parallel diff.

stdlib-only (runs on stash with plain python3, like template_drift_report).
This module performs file writes (stage/swap/snapshot) but ONLY under explicit
calls — nothing here runs automatically, and the higher automations gate every
call behind their default-OFF toggles.

Layout (host = "example.com"):
    templates/reviewed/example.com.template.json        live (matched if enabled)
    templates/reviewed/example.com.template.json.bak    gold (rollback point)
    templates/reviewed/example.com.template.json.stage  staged candidate (transient)
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_REVIEWED_SUBDIR = ("templates", "reviewed")
_SUFFIX = ".template.json"
_GOLD_SUFFIX = ".template.json.bak"
_STAGE_SUFFIX = ".template.json.stage"


def _project_root() -> Path:
    # Resolve module-relative (bulk_downloader/template_keystone.py -> repo root)
    # so tools/ and templates/ resolve correctly regardless of cwd — the custom
    # test runner changes working directory, and cwd-based resolution broke the
    # diff import. BD_ROOT overrides for explicit relocation.
    env = os.environ.get("BD_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


def _reviewed_dir(reviewed_dir: Optional[str | Path] = None) -> Path:
    if reviewed_dir is not None:
        return Path(reviewed_dir)
    return _project_root().joinpath(*_REVIEWED_SUBDIR)


def _safe_host(host: str) -> Optional[str]:
    """Reject anything that could escape the reviewed dir or isn't a bare host."""
    if not host or not isinstance(host, str):
        return None
    if "/" in host or "\\" in host or ".." in host or host.startswith("."):
        return None
    return host


def _paths(host: str, reviewed_dir=None):
    rd = _reviewed_dir(reviewed_dir)
    return (rd / f"{host}{_SUFFIX}",
            rd / f"{host}{_GOLD_SUFFIX}",
            rd / f"{host}{_STAGE_SUFFIX}")


# ── primitives ───────────────────────────────────────────────────────────────

def snapshot_gold(host: str, *, reviewed_dir=None, force: bool = False) -> Dict[str, Any]:
    """Copy the current live reviewed template to its gold .bak — the rollback
    point. No-op (ok=True, snapshotted=False) if there is no live template.
    Will not clobber an existing gold unless `force` (the first snapshot wins;
    the gold stays the last-known-good until an explicit re-baseline)."""
    h = _safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    live, gold, _ = _paths(h, reviewed_dir)
    if not live.is_file():
        return {"ok": True, "snapshotted": False, "reason": "no live template"}
    if gold.exists() and not force:
        return {"ok": True, "snapshotted": False, "reason": "gold already exists",
                "gold": str(gold)}
    shutil.copy2(live, gold)
    return {"ok": True, "snapshotted": True, "gold": str(gold)}


def stage_template(host: str, new_template: Dict[str, Any], *,
                   reviewed_dir=None) -> Dict[str, Any]:
    """Write `new_template` to the transient .stage file (never to live)."""
    h = _safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    if not isinstance(new_template, dict):
        return {"ok": False, "error": "new_template must be a dict"}
    live, _, stage = _paths(h, reviewed_dir)
    stage.parent.mkdir(parents=True, exist_ok=True)
    stage.write_text(json.dumps(new_template, indent=2), "utf-8")
    return {"ok": True, "staged": str(stage)}


def drift_against_gold(host: str, candidate: Dict[str, Any], *,
                       reviewed_dir=None) -> Dict[str, Any]:
    """Drift of `candidate` vs the gold (else live), reusing the EXISTING
    section diffs from tools/template_drift_report. Returns a total count +
    human-readable lines. No file writes."""
    h = _safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    live, gold, _ = _paths(h, reviewed_dir)
    base = gold if gold.is_file() else live
    if not base.is_file():
        # No baseline at all → first version, treat as zero drift (nothing to
        # diff against). Caller decides whether a first version is allowed.
        return {"ok": True, "drift": 0, "lines": ["(no gold/live baseline — first version)"],
                "baseline": None}
    try:
        gold_t = json.loads(base.read_text("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"baseline parse failed: {e}"[:120]}
    try:
        import sys
        tools_dir = str(_project_root() / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import template_drift_report as tdr  # type: ignore
    except Exception as e:
        return {"ok": False, "error": f"drift report unavailable: {e}"[:120]}
    out: list = []
    total = 0
    for fn in (tdr.diff_selectors, tdr.diff_row_selectors, tdr.diff_resolutions,
               tdr.diff_api, tdr.diff_network_patterns):
        try:
            total += fn(candidate, gold_t, out)
        except Exception as e:  # a missing section must not crash the keystone
            out.append(f"  (diff section {fn.__name__} errored: {e})")
    return {"ok": True, "drift": total, "lines": out, "baseline": str(base)}


def commit_swap(host: str, *, reviewed_dir=None) -> Dict[str, Any]:
    """Atomically promote the staged file to live (os.replace). The stage must
    exist. Atomic on the same filesystem — live is never torn."""
    h = _safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    live, _, stage = _paths(h, reviewed_dir)
    if not stage.is_file():
        return {"ok": False, "error": "no staged template to swap"}
    os.replace(stage, live)  # atomic
    return {"ok": True, "swapped": str(live)}


def rollback_to_gold(host: str, *, reviewed_dir=None) -> Dict[str, Any]:
    """Restore the gold backup over live. The recovery path for a bad swap or
    a quarantine — only possible because snapshot_gold ran first."""
    h = _safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}
    live, gold, _ = _paths(h, reviewed_dir)
    if not gold.is_file():
        return {"ok": False, "error": "no gold backup to roll back to"}
    shutil.copy2(gold, live)
    return {"ok": True, "rolled_back": str(live), "from_gold": str(gold)}


# ── orchestration ────────────────────────────────────────────────────────────

def safe_overwrite(host: str, new_template: Dict[str, Any], *,
                   reviewed_dir=None,
                   gate: Optional[Callable[[int], bool]] = None
                   ) -> Dict[str, Any]:
    """The keystone flow: snapshot gold → stage → diff → swap-if-gate-passes.

    `gate(drift_count) -> bool`: True = allow the swap. Default (None) = always
    allow (a plain backed-up overwrite). Auto-refresh passes a low-drift gate;
    a manual operator overwrite passes None.

    Safety: the gold snapshot always runs first; if the gate rejects, the stage
    is left in place and LIVE IS UNTOUCHED (no swap) — the operator can inspect
    the stage and the drift, then decide. Live only ever changes via the atomic
    commit_swap. On reject, the staged candidate is retained for inspection.
    """
    h = _safe_host(host)
    if not h:
        return {"ok": False, "error": "invalid host"}

    # A0 keystone gate: take a generational, restorable backup of the current
    # live gold BEFORE anything else. If it cannot be taken, ABORT the write —
    # an autonomous overwrite must never clobber gold without a recovery point.
    # No live template -> backed_up=False is fine (a first write has no gold to
    # lose); only a genuine backup FAILURE blocks the write.
    try:
        from . import template_backup as _tb
        _bk = _tb.backup_template(h, reviewed_dir=reviewed_dir, reason="safe_overwrite")
    except Exception as _e:
        _bk = {"ok": False, "error": f"backup module error: {_e}"[:120]}
    if not _bk.get("ok"):
        return {"ok": False,
                "error": f"backup failed; write aborted: {_bk.get('error')}"}

    snap = snapshot_gold(h, reviewed_dir=reviewed_dir)
    if not snap.get("ok"):
        return {"ok": False, "error": f"snapshot failed: {snap.get('error')}"}

    st = stage_template(h, new_template, reviewed_dir=reviewed_dir)
    if not st.get("ok"):
        return {"ok": False, "error": f"stage failed: {st.get('error')}"}

    dr = drift_against_gold(h, new_template, reviewed_dir=reviewed_dir)
    if not dr.get("ok"):
        return {"ok": False, "error": f"diff failed: {dr.get('error')}"}
    drift = dr["drift"]

    allow = True if gate is None else bool(gate(drift))
    if not allow:
        return {"ok": True, "swapped": False, "drift": drift,
                "lines": dr["lines"], "gold": snap.get("gold"),
                "reason": "gate rejected; live untouched, stage retained",
                "staged": _paths(h, reviewed_dir)[2].as_posix()}

    sw = commit_swap(h, reviewed_dir=reviewed_dir)
    if not sw.get("ok"):
        return {"ok": False, "error": f"swap failed: {sw.get('error')}",
                "drift": drift}
    return {"ok": True, "swapped": True, "drift": drift, "lines": dr["lines"],
            "gold": snap.get("gold"), "live": sw["swapped"],
            "snapshotted": snap.get("snapshotted")}


def keystone_present() -> bool:
    """Capability probe used by lifecycle_automation.keystone_available(): the
    keystone exists and its diff dependency is importable."""
    try:
        import sys
        tools_dir = str(_project_root() / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import template_drift_report as _tdr  # noqa: F401
        return True
    except Exception:
        return False
