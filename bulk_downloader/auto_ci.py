"""bulk_downloader.auto_ci -- A6: continuous CI / auto-regression + snapshot +
rollback.

On a change, snapshot a baseline, run the regression band, and on a regression
assemble a restorable rollback artifact (auto-rollback when a rollback applier
is injected). PURE orchestration over injected fns.

IMPORTANT -- the in-sandbox regression band run here is ADVISORY. The BINDING
full-suite gate is ``capture.sh`` on stash; the verdict carries
``binding == "stash:capture.sh"`` so no caller mistakes a green sandbox band for
a deploy gate. (Same discipline the rest of the project follows: build_release /
verify_release prove internal consistency; the on-stash full suite is the gate.)

The toggle ``auto_ci`` is DEFAULT OFF and NOT keystone-required: snapshot and
rollback RESTORE prior state and never overwrite a serving template with new
content. Fail-safe: a throwing runner is treated CONSERVATIVELY (cannot prove
green -> ``regressed=True``, staged for attention), never raised.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from . import lifecycle_automation as la

_BINDING_GATE = "stash:capture.sh"


def snapshot_baseline(snapshot_fns: Dict[str, Callable[[], Any]]) -> Dict[str, Any]:
    """Call each injected snapshot fn and collect its handle. A failing snapshot
    is recorded as ``None`` (isolated), never raised -- a partial baseline still
    lets the cycle proceed and flags the gap."""
    base: Dict[str, Any] = {}
    for name, fn in (snapshot_fns or {}).items():
        try:
            base[name] = fn()
        except Exception:
            base[name] = None
    return base


def run_regression(*, run_fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Run the regression band via ``run_fn`` (returns ``{passed, failed,
    failures}``). Classifies regressed = failed>0. ADVISORY -- binding gate is
    stash. A throwing runner is conservative: regressed=True with the error."""
    try:
        res = run_fn() or {}
    except Exception as e:
        return {"regressed": True, "advisory": True, "passed": 0, "failed": None,
                "failures": [], "error": str(e)[:160], "binding": _BINDING_GATE}
    failed = int(res.get("failed") or 0)
    failures = list(res.get("failures") or [])
    return {"regressed": failed > 0 or bool(failures), "advisory": True,
            "passed": int(res.get("passed") or 0), "failed": failed,
            "failures": failures, "binding": _BINDING_GATE}


def make_rollback_artifact(baseline: Dict[str, Any],
                           regression: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble a restorable rollback artifact from the baseline + the regression
    evidence."""
    return {"baseline": baseline, "failures": list(regression.get("failures") or []),
            "created_at": int(time.time()), "restorable": True,
            "binding": _BINDING_GATE}


def auto_ci_cycle(change: Dict[str, Any], *,
                  snapshot_fns: Optional[Dict[str, Callable[[], Any]]] = None,
                  run_fn: Optional[Callable[[], Dict[str, Any]]] = None,
                  rollback_fn: Optional[Callable[[Dict[str, Any]], Any]] = None
                  ) -> Dict[str, Any]:
    """Gated A6 entry. No-op when ``auto_ci`` is disabled (DEFAULT OFF).

    On a change: snapshot baseline -> run regression -> on regression, assemble a
    rollback artifact and (if ``rollback_fn`` injected) auto-rollback; otherwise
    stage the artifact for the operator. The sandbox band is ADVISORY
    (``binding == "stash:capture.sh"``)."""
    if not la.is_enabled("auto_ci"):
        return {"ok": True, "skipped": "auto_ci disabled"}

    baseline = snapshot_baseline(snapshot_fns or {})
    if run_fn is None:
        return {"ok": True, "regressed": None, "baseline": baseline,
                "note": "no runner supplied -- nothing verified",
                "binding": _BINDING_GATE, "rolled_back": False}

    regression = run_regression(run_fn=run_fn)
    out: Dict[str, Any] = {"ok": True, "regressed": regression["regressed"],
                           "baseline": baseline, "regression": regression,
                           "binding": _BINDING_GATE, "rolled_back": False}

    if regression["regressed"]:
        artifact = make_rollback_artifact(baseline, regression)
        out["artifact"] = artifact
        if rollback_fn is not None:
            try:
                rollback_fn(artifact)
                out["rolled_back"] = True
            except Exception as e:
                out["rollback_error"] = str(e)[:160]
                out["rolled_back"] = False
    return out
