"""F2.4 — daily ops digest.

A once-a-day "what changed" notification: assemble a small set of
operator-meaningful counters, compare them to the previous digest's
snapshot, and fire ONE apprise notification — but only when something
moved. A day on which every tracked counter is identical to the last
digest is a *zero-delta* day and stays silent (the operator's inbox is
not pinged to say "nothing happened").

Design mirrors `template_canary` (the other daily, toggle-gated,
state-persisting, apprise-dispatching bg task):

  * ``ENABLE_KEY`` opt-in, **default OFF** — registering the bg task is
    behaviour-neutral (one global_config read per cadence) until an
    operator turns it on. A digest that auto-fired on deploy would be a
    behaviour change; this is opt-in like every other automation toggle.
  * fail-soft everywhere — a missing data source contributes 0, a
    missing/disabled notifier is swallowed; the task never raises and
    never competes with real work.
  * the apprise EVENT name lives here (not in ``notify_apprise``); the
    dispatcher treats an unknown event as fire-immediately, so no
    DEFAULT_POLICY entry and no edit to ``notify_apprise`` is required.

Reporting posture: counts only — kinds/totals, never values/PII. The
metrics are aggregate integers (timeline volume, error/warning counts,
drafts-pending), so there is nothing value-bearing to leak.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# ── tunables ──────────────────────────────────────────────────────────────
ENABLE_KEY = "automation.daily_digest_enabled"
# How far back the volume counters look. The bg cadence is daily, so a
# 24h window matches one digest period.
DEFAULT_WINDOW_HOURS = 24
# Don't re-fire within this many seconds (the daily cadence guards the
# normal case; this protects against a manual re-invocation / double tick).
DEFAULT_MIN_SPACING_S = 12 * 3600.0
_STATE_FILENAME = "daily_digest_state.json"

# A custom apprise event tag. The dispatcher treats an unknown event as
# ("per_event", 0, 0.0) — fires immediately — which is what a daily digest
# wants. No DEFAULT_POLICY entry in notify_apprise is required.
EVENT_DAILY_DIGEST = "daily_digest"

# Human labels for the digest body (also the canonical metric key order).
_METRIC_LABELS = {
    "events_24h": "Timeline events (24h)",
    "errors_24h": "Errors (24h)",
    "warnings_24h": "Warnings (24h)",
    "drafts_pending": "Drafts awaiting review",
}


# ── enable gate ─────────────────────────────────────────────────────────────
def _enabled() -> bool:
    try:
        from . import global_config
        return bool(global_config.get(ENABLE_KEY, False))
    except Exception:
        return False


# ── state persistence (fail-soft, atomic-ish) ───────────────────────────────
def _state_path() -> Path:
    home = os.environ.get("BD_HOME") or "."
    return Path(home).resolve() / _STATE_FILENAME


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read persisted state; empty skeleton on any error — never raises."""
    p = path or _state_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("state root not an object")
        data.setdefault("last_run", 0.0)
        data.setdefault("last_sent", 0.0)
        data.setdefault("snapshot", {})
        if not isinstance(data["snapshot"], dict):
            data["snapshot"] = {}
        return data
    except Exception:
        return {"last_run": 0.0, "last_sent": 0.0, "snapshot": {}}


def save_state(state: Dict[str, Any], path: Optional[Path] = None) -> bool:
    """Persist atomically-ish (tmp + replace). Returns success; never raises."""
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


# ── metric collection (each source guarded → int) ───────────────────────────
def collect_metrics(*, window_hours: int = DEFAULT_WINDOW_HOURS) -> Dict[str, int]:
    """Gather the daily ops counters. Every source is wrapped so a missing
    or failing module contributes 0 rather than breaking the digest. Returns
    a flat dict of ints keyed by the canonical metric names."""
    events = errors = warnings = drafts = 0

    try:
        from . import timeline as _tl
        s = _tl.summary(since_hours=window_hours) or {}
        events = int(s.get("total", 0) or 0)
        sev = s.get("by_severity") or {}
        if isinstance(sev, dict):
            errors = int(sev.get("error", 0) or 0) + int(sev.get("critical", 0) or 0)
            warnings = int(sev.get("warning", 0) or 0) + int(sev.get("warn", 0) or 0)
    except Exception:
        pass

    try:
        from . import template_manager as _tm
        listing = _tm.list_templates() or {}
        d = listing.get("drafts")
        if isinstance(d, (list, tuple)):
            drafts = len(d)
        elif isinstance(d, int):
            drafts = d
    except Exception:
        pass

    return {
        "events_24h": events,
        "errors_24h": errors,
        "warnings_24h": warnings,
        "drafts_pending": drafts,
    }


# ── pure delta + formatting ──────────────────────────────────────────────────
def compute_delta(curr: Dict[str, int],
                  prev: Dict[str, int]) -> Dict[str, int]:
    """Per-key signed change of curr vs prev (missing prev key == 0)."""
    out: Dict[str, int] = {}
    for k, v in (curr or {}).items():
        try:
            out[k] = int(v) - int((prev or {}).get(k, 0) or 0)
        except Exception:
            out[k] = 0
    return out


def is_zero_delta(delta: Dict[str, int]) -> bool:
    """True iff no tracked counter moved (a quiet day)."""
    return all(int(v) == 0 for v in (delta or {}).values())


def _fmt_delta(n: int) -> str:
    n = int(n)
    if n == 0:
        return "—"
    return f"+{n}" if n > 0 else str(n)


def build_body(metrics: Dict[str, int],
               delta: Dict[str, int],
               rehearsal: Optional[Dict[str, Any]] = None) -> str:
    """Render the notification body: one line per metric, value + change.
    Counts only — nothing value-bearing.

    X-AUTO-1 (v3.66.706): a restore REHEARSAL verdict, when supplied, is reported here.
    `rehearsal` None -> the body is byte-identical to the pre-706 digest."""
    lines = []
    for key, label in _METRIC_LABELS.items():
        if key not in metrics:
            continue
        lines.append(f"{label}: {int(metrics.get(key, 0))} ({_fmt_delta(delta.get(key, 0))})")
    # Any metric not in the label map (forward-compat) still gets shown.
    for key in metrics:
        if key not in _METRIC_LABELS:
            lines.append(f"{key}: {int(metrics.get(key, 0))} ({_fmt_delta(delta.get(key, 0))})")
    body = "Changes since the last digest:\n" + "\n".join(lines)
    if rehearsal:
        if rehearsal.get("ok"):
            extra = "Restore rehearsal: OK"
            age = rehearsal.get("age_days")
            if age is not None:
                extra += f" (backup {age}d old)"
        else:
            # The loudest line in the digest: the recovery path you rely on is broken.
            extra = ("Restore rehearsal: FAILED -- the latest backup did NOT restore: "
                     f"{rehearsal.get('error') or 'unknown error'}")
        body += "\n\n" + extra
    return body


def _default_notify(title: str, body: str) -> None:
    """Fire one notification via the shared apprise dispatcher. Fail-open:
    a missing/disabled notifier or any error is swallowed."""
    try:
        from . import notify_apprise as _na
        _na.get_dispatcher().notify(EVENT_DAILY_DIGEST, title, body)
    except Exception:
        pass


# ── unconditional core (gate lives in scheduled_digest) ──────────────────────
def run_digest(*,
               now: Optional[float] = None,
               metrics: Optional[Dict[str, int]] = None,
               state_path: Optional[Path] = None,
               dispatch: bool = True,
               _notifier: Optional[Callable[[str, str], None]] = None,
               _metrics_fn: Optional[Callable[[], Dict[str, int]]] = None,
               rehearsal: Optional[Dict[str, Any]] = None
               ) -> Dict[str, Any]:
    """Run one digest pass UNCONDITIONALLY (the enable gate is in
    ``scheduled_digest``). Compares current metrics to the persisted
    snapshot; on a non-zero delta, dispatches and advances the snapshot;
    on a zero delta, stays silent but still records the run. Never raises.

    ``metrics`` / ``_metrics_fn`` / ``_notifier`` are injection seams for
    tests so a pass can run with no live DB and no real notifier.
    """
    ts = float(now if now is not None else time.time())
    try:
        if metrics is not None:
            curr = {k: int(v) for k, v in metrics.items()}
        else:
            curr = (_metrics_fn or collect_metrics)()
            curr = {k: int(v) for k, v in (curr or {}).items()}
    except Exception:
        curr = {}

    state = load_state(state_path)
    prev = state.get("snapshot", {}) or {}
    delta = compute_delta(curr, prev)
    zero = is_zero_delta(delta)
    # X-AUTO-1 (706) -- THE LOAD-BEARING RULE. This digest is zero-delta-SILENT to
    # protect the operator's inbox. But a FAILED restore rehearsal on a quiet day is
    # exactly what must NOT be silent: silence would read as "all fine" while the
    # recovery path you depend on is broken. A failed rehearsal therefore FORCES the
    # notification through. A PASSING rehearsal does NOT -- the escape hatch stays
    # narrow, or the digest would fire daily and get muted, defeating its purpose.
    rehearsal_failed = bool(rehearsal) and not bool(rehearsal.get("ok"))

    state["last_run"] = ts
    sent = False
    reason = "zero_delta"
    if not zero or rehearsal_failed:
        title = ("BulkDownloader: RESTORE REHEARSAL FAILED" if rehearsal_failed
                 else "BulkDownloader daily digest")
        body = build_body(curr, delta, rehearsal=rehearsal)
        if rehearsal_failed:
            reason = "rehearsal_failed"
        if dispatch:
            (_notifier or _default_notify)(title, body)
            sent = True
            state["last_sent"] = ts
        else:
            reason = "delta_no_dispatch"
        # advance the baseline so tomorrow's delta is measured from today,
        # whether or not we actually dispatched.
        state["snapshot"] = curr
        if sent:
            reason = "sent"
    save_state(state, state_path)

    return {
        "ran_at": ts,
        "metrics": curr,
        "delta": delta,
        "zero_delta": zero,
        "sent": sent,
        "reason": reason,
    }


# ── bg-scheduler entry point ─────────────────────────────────────────────────
def scheduled_digest(*,
                     now: Optional[float] = None,
                     min_spacing_s: float = DEFAULT_MIN_SPACING_S,
                     state_path: Optional[Path] = None) -> Dict[str, Any]:
    """The bg-scheduler entry point. NO-OPS unless the opt-in flag is on.
    Honours a min-spacing guard so a re-invocation inside the window is a
    cheap no-op. Never raises. Returns a small status dict for tests / a
    manual trigger: ``{"ran": bool, "reason": str, ...}``."""
    try:
        if not _enabled():
            return {"ran": False, "reason": "disabled"}
        state = load_state(state_path)
        last = float(state.get("last_run", 0.0) or 0.0)
        ts = float(now if now is not None else time.time())
        if last and (ts - last) < float(min_spacing_s):
            return {"ran": False, "reason": "min_spacing"}
        # X-AUTO-1 (706): run the restore REHEARSAL as part of the scheduled pass, so
        # the backups on disk are PROVEN restorable on a schedule rather than assumed.
        # Separately opt-in (default OFF) and fail-soft: a rehearsal that errors must
        # never take out the digest -- it degrades to a not-ok verdict, which the digest
        # then SHOUTS about (see run_digest's zero-delta rule).
        rehearsal = None
        try:
            from . import backup_verify as _bv
            if _bv.rehearsal_enabled():
                rehearsal = _bv.rehearse()
        except Exception as _e:
            # NOT a swallow: the error becomes a NOT-OK verdict, which run_digest then
            # SHOUTS about (it breaks the zero-delta silence). A rehearsal that errors
            # is a rehearsal that failed -- treating it as "fine" is the bug.
            rehearsal = {"ok": False, "checked_at": ts,
                         "error": f"rehearsal error: {type(_e).__name__}: {_e}"[:160]}
        result = run_digest(now=ts, state_path=state_path, rehearsal=rehearsal)
        return {"ran": True, "reason": result.get("reason", "ok"),
                "sent": bool(result.get("sent")),
                "zero_delta": bool(result.get("zero_delta")),
                "rehearsal_ok": (None if rehearsal is None
                                 else bool(rehearsal.get("ok")))}
    except Exception as e:  # belt-and-braces; inner calls are already soft
        return {"ran": False, "reason": f"error:{type(e).__name__}"}


# ── read surface (secret-free; not wired to a route by F2.4) ─────────────────
def digest_status(state_path: Optional[Path] = None) -> Dict[str, Any]:
    """Small, value-free snapshot (enabled / timings / last counters).
    Always returns a dict; never raises."""
    try:
        state = load_state(state_path)
        snap = state.get("snapshot", {})
        return {
            "enabled": _enabled(),
            "last_run": float(state.get("last_run", 0.0) or 0.0),
            "last_sent": float(state.get("last_sent", 0.0) or 0.0),
            "snapshot": snap if isinstance(snap, dict) else {},
        }
    except Exception:
        return {"enabled": False, "last_run": 0.0,
                "last_sent": 0.0, "snapshot": {}}
