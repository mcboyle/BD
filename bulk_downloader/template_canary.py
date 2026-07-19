"""Template canary (Track F · F3.3).

Periodically replays the synthetic-test fixtures against the *enabled*
templates and alerts when a site's pass-rate drops materially versus the
last good run — so a real breakage is detected by the canary BEFORE it
shows up as a wave of job failures.

Design constraints (all load-bearing):

  • READ / ALERT ONLY. The canary never touches an enabled template, never
    promotes, never writes a draft. It compares pass-rates and (optionally)
    fires a notification. Promotion stays 100% human.

  • PURE local replay. ``synthetic_tests.run_all`` is HTTP-free fixture
    replay (see that module's docstring) — the canary therefore cannot
    compete with real download jobs for site bandwidth. The roadmap's
    "rate floor so canaries never compete with real jobs" framing assumed
    live-site probing; that does not apply here. We keep a min-spacing
    guard only to avoid redundant local work, not to protect a site.

  • TOGGLE-GATED, DEFAULT OFF. ``scheduled_canary`` no-ops unless
    ``automation.template_canary_enabled`` is on. Registering the bg task
    is therefore behaviour-neutral: one global_config read per cadence
    until an operator opts in. Mirrors the lifecycle.drift_sweep pattern.

  • FAIL-SOFT everywhere. Any error (no fixtures, no DB, notify down,
    unreadable state file) degrades to a quiet no-op; a canary problem can
    never tighten, block, or break anything in the pipeline.

State lives at ``$BD_HOME/template_canary_state.json``:

    {
      "baseline": {"<site>": <pass_rate float 0..100>, ...},
      "last_run": <epoch float>,
      "last_result": { "ran_at", "total", "passed", "failed",
                       "pass_rate", "per_site": {<site>: rate},
                       "alerts": [ {site, baseline, current, drop} ] }
    }

The baseline is the per-site pass-rate from the most recent run that did
NOT alert for that site (i.e. the last known-good). A site that regresses
keeps its old (higher) baseline so a sustained outage keeps alerting and a
recovery resets it cleanly.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── tunables ──────────────────────────────────────────────────────────────
ENABLE_KEY = "automation.template_canary_enabled"
# A site must drop at least this many *percentage points* below its
# baseline pass-rate to raise an alert (default 25pt — a real selector
# break collapses a fixture set, a flaky single fixture does not).
DEFAULT_DROP_PCT = 25.0
# Don't re-run within this many seconds (min spacing; the bg cadence is
# daily, this guards against double-fires / manual re-invocation).
DEFAULT_MIN_SPACING_S = 3600.0
# A site needs at least this many fixtures run for its rate to be
# trustworthy enough to alert on (one fixture flapping is noise).
DEFAULT_MIN_FIXTURES = 2
_STATE_FILENAME = "template_canary_state.json"

# A custom apprise event tag. The dispatcher treats an unknown event as
# ("per_event", 0, 0.0) — fires immediately — which is what we want for a
# breakage alert. No DEFAULT_POLICY entry is required.
EVENT_TEMPLATE_CANARY = "template_canary"


# ── state persistence (fail-soft) ───────────────────────────────────────────
def _state_path() -> Path:
    home = os.environ.get("BD_HOME") or "."
    return Path(home).resolve() / _STATE_FILENAME


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the persisted canary state. Returns an empty skeleton on any
    error (missing file, bad JSON, unreadable) — never raises."""
    p = path or _state_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("state root not an object")
        data.setdefault("baseline", {})
        data.setdefault("last_run", 0.0)
        data.setdefault("last_result", None)
        if not isinstance(data["baseline"], dict):
            data["baseline"] = {}
        return data
    except Exception:
        return {"baseline": {}, "last_run": 0.0, "last_result": None}


def save_state(state: Dict[str, Any], path: Optional[Path] = None) -> bool:
    """Persist state atomically-ish (tmp + replace). Returns True on
    success, False on any error — never raises."""
    p = path or _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


# ── pure drop-detection ─────────────────────────────────────────────────────
def _per_site_rates(run: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """From a synthetic_tests.run_all result, derive per-site
    {pass_rate, total} (pass_rate as 0..100). Robust to missing keys."""
    out: Dict[str, Dict[str, float]] = {}
    per = (run or {}).get("per_site") or {}
    if not isinstance(per, dict):
        return out
    for sid, rec in per.items():
        if not isinstance(rec, dict):
            continue
        passed = float(rec.get("passed", 0) or 0)
        failed = float(rec.get("failed", 0) or 0)
        total = passed + failed
        rate = (passed / total * 100.0) if total > 0 else 0.0
        out[str(sid)] = {"pass_rate": round(rate, 2), "total": total}
    return out


def compare_to_baseline(
    run: Dict[str, Any],
    baseline: Dict[str, float],
    *,
    drop_pct: float = DEFAULT_DROP_PCT,
    min_fixtures: int = DEFAULT_MIN_FIXTURES,
) -> List[Dict[str, Any]]:
    """Return a list of alert dicts for sites whose current pass-rate fell
    at least ``drop_pct`` percentage points below their baseline.

    A site with no baseline (first ever observation) never alerts — it
    establishes the baseline instead. A site with fewer than
    ``min_fixtures`` fixtures this run is skipped (too noisy to trust).
    Pure function; never raises on shape surprises (best-effort).
    """
    alerts: List[Dict[str, Any]] = []
    rates = _per_site_rates(run)
    for sid, rec in rates.items():
        if rec["total"] < min_fixtures:
            continue
        base = baseline.get(sid)
        if base is None:
            continue  # establishing baseline; no alert on first sight
        try:
            base_f = float(base)
        except (TypeError, ValueError):
            continue
        cur = rec["pass_rate"]
        drop = round(base_f - cur, 2)
        if drop >= drop_pct:
            alerts.append({
                "site": sid,
                "baseline": round(base_f, 2),
                "current": cur,
                "drop": drop,
                "fixtures": int(rec["total"]),
            })
    alerts.sort(key=lambda a: a["drop"], reverse=True)
    return alerts


def _next_baseline(
    run: Dict[str, Any],
    prev_baseline: Dict[str, float],
    alert_sites: set,
) -> Dict[str, float]:
    """Compute the baseline to persist after this run.

    • A site that alerted KEEPS its prior (higher) baseline, so a sustained
      outage keeps alerting until it recovers — we don't quietly lower the
      bar to a broken state.
    • A site that did NOT alert adopts its current pass-rate as the new
      known-good baseline (covers first-sight and recovery).
    """
    rates = _per_site_rates(run)
    out = dict(prev_baseline)
    for sid, rec in rates.items():
        if sid in alert_sites:
            # keep prior baseline (or seed if somehow absent)
            out.setdefault(sid, rec["pass_rate"])
        else:
            out[sid] = rec["pass_rate"]
    return out


# ── alert dispatch (fail-open) ──────────────────────────────────────────────
def _dispatch_alerts(alerts: List[Dict[str, Any]]) -> None:
    """Fire one notification summarising the breakages. Fail-open: a
    missing/disabled notifier or any error is swallowed."""
    if not alerts:
        return
    try:
        from . import notify_apprise as _na
        lines = [
            f"{a['site']}: {a['baseline']:.0f}% -> {a['current']:.0f}% "
            f"(-{a['drop']:.0f}pt, {a['fixtures']} fixtures)"
            for a in alerts
        ]
        title = f"BulkDownloader: {len(alerts)} template canary alert(s)"
        body = "Synthetic-test pass-rate dropped for:\n" + "\n".join(lines)
        _na.get_dispatcher().notify(EVENT_TEMPLATE_CANARY, title, body)
    except Exception:
        pass


# ── orchestration ───────────────────────────────────────────────────────────
def _enabled() -> bool:
    """Read the opt-in flag, fail-safe OFF."""
    try:
        from . import global_config
        return bool(global_config.get(ENABLE_KEY, False))
    except Exception:
        return False


def run_canary(
    *,
    s_cfg: Optional[dict] = None,
    drop_pct: float = DEFAULT_DROP_PCT,
    min_fixtures: int = DEFAULT_MIN_FIXTURES,
    state_path: Optional[Path] = None,
    dispatch: bool = True,
    _run_all=None,
) -> Dict[str, Any]:
    """Run one canary pass UNCONDITIONALLY (the enable gate lives in
    ``scheduled_canary``). Replays fixtures, compares to baseline, persists
    the updated baseline + last_result, optionally dispatches alerts.

    Returns the last_result dict. ``_run_all`` is an injection seam for
    tests; defaults to ``synthetic_tests.run_all``. Fail-soft: returns a
    skeleton result on any error.
    """
    try:
        runner = _run_all
        if runner is None:
            from . import synthetic_tests as _st
            runner = _st.run_all
        run = runner(s_cfg=s_cfg) or {}
    except Exception:
        return {"ran_at": time.time(), "total": 0, "passed": 0,
                "failed": 0, "pass_rate": 0.0, "per_site": {},
                "alerts": [], "error": "run_all_failed"}

    state = load_state(state_path)
    baseline = state.get("baseline", {}) or {}
    alerts = compare_to_baseline(
        run, baseline, drop_pct=drop_pct, min_fixtures=min_fixtures)
    alert_sites = {a["site"] for a in alerts}
    state["baseline"] = _next_baseline(run, baseline, alert_sites)
    state["last_run"] = time.time()

    rates = _per_site_rates(run)
    result = {
        "ran_at": state["last_run"],
        "total": int(run.get("total", 0) or 0),
        "passed": int(run.get("passed", 0) or 0),
        "failed": int(run.get("failed", 0) or 0),
        "pass_rate": round(float(run.get("pass_rate", 0.0) or 0.0), 2),
        "per_site": {s: r["pass_rate"] for s, r in rates.items()},
        "alerts": alerts,
    }
    state["last_result"] = result
    save_state(state, state_path)

    if dispatch and alerts:
        _dispatch_alerts(alerts)
    return result


def scheduled_canary(
    *,
    s_cfg: Optional[dict] = None,
    min_spacing_s: float = DEFAULT_MIN_SPACING_S,
    state_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """The bg-scheduler entry point. NO-OPS unless the opt-in flag is on.
    Honours a min-spacing guard so a re-invocation inside the window is a
    cheap no-op. Never raises.

    Returns a small status dict describing what it did (handy for tests /
    a manual trigger): {"ran": bool, "reason": str, ...}.
    """
    try:
        if not _enabled():
            return {"ran": False, "reason": "disabled"}
        state = load_state(state_path)
        last = float(state.get("last_run", 0.0) or 0.0)
        if last and (time.time() - last) < float(min_spacing_s):
            return {"ran": False, "reason": "min_spacing"}
        result = run_canary(s_cfg=s_cfg, state_path=state_path)
        return {"ran": True, "reason": "ok",
                "alerts": len(result.get("alerts", [])),
                "pass_rate": result.get("pass_rate", 0.0)}
    except Exception as e:  # belt-and-braces; the inner calls are already soft
        return {"ran": False, "reason": f"error:{type(e).__name__}"}


# ── read surface (additive into /api/data/template_health) ───────────────────
def canary_status(state_path: Optional[Path] = None) -> Dict[str, Any]:
    """A small, secret-free snapshot for the template_health panel.

    Surfaces only: whether the feature is enabled, when it last ran, the
    aggregate pass-rate, per-site pass-rates, and the current alert list
    (site + pass-rate numbers only — no fixture bodies, no URLs, no PII).
    Always returns a dict; never raises.
    """
    try:
        state = load_state(state_path)
        lr = state.get("last_result")
        return {
            "enabled": _enabled(),
            "last_run": float(state.get("last_run", 0.0) or 0.0),
            "last_result": lr if isinstance(lr, dict) else None,
            "baseline_sites": len(state.get("baseline", {}) or {}),
        }
    except Exception:
        return {"enabled": False, "last_run": 0.0,
                "last_result": None, "baseline_sites": 0}
