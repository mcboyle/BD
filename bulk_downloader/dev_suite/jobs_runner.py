"""dev_suite.jobs_runner -- runner/job/scheduling

Split from the dev_suite.py monolith (v3.66.395, pure code motion; surface preserved
via dev_suite/__init__.py). See kb/decomp/dev_suite/.
"""


from __future__ import annotations
import os
import sys
import threading
from pathlib import Path
import re as _sec_re
import json as _cfg_json
import re as _cfg_re
import os as _dl_os
import re as _dl_re



# ── 28. session-keeper heartbeat monitor (D-23) ────────────────────

def keeper_monitor() -> dict:
    """Per-(site,account) session-keeper state — current state, last
    heartbeat / login age, whether a browser handle is held, and
    consecutive failures. Read-only: it never calls into the keeper's
    Playwright browser (that object is thread-bound — INV-001), it
    only inspects the keeper's own state dict and attributes."""
    import time as _t
    try:
        from bulk_downloader import session_keeper as _sk
    except Exception as e:
        return {"error": f"session_keeper import failed: {e}"[:160]}
    now = _t.time()
    keepers = []
    for key, kp in list(getattr(_sk, "_keepers", {}).items()):
        try:
            st = dict(getattr(kp, "state", {}) or {})
            started = float(getattr(kp, "_browser_started_at", 0.0) or 0)
            hb = float(st.get("last_heartbeat_ts", 0.0) or 0.0)
            login = float(st.get("last_login_ts", 0.0) or 0.0)
            keepers.append({
                "site_id": getattr(kp, "site_id",
                                   key[0] if key else "?"),
                "account_idx": getattr(kp, "account_idx",
                                       key[1] if key else None),
                "state": st.get("state", "?"),
                "browser_handle_present":
                    getattr(kp, "_browser", None) is not None,
                "browser_age_seconds": (round(now - started, 1)
                                        if started else None),
                "last_heartbeat_age_seconds": (round(now - hb, 1)
                                               if hb else None),
                "last_login_age_seconds": (round(now - login, 1)
                                           if login else None),
                "consecutive_failures":
                    st.get("consecutive_failures", 0),
                "last_detail": str(st.get("last_detail", ""))[:160],
            })
        except Exception as e:
            keepers.append({"key": str(key), "error": str(e)[:120]})
    failing = [k for k in keepers
               if (k.get("consecutive_failures") or 0) > 0]
    return {
        "keeper_count": len(keepers),
        "with_failures": len(failing),
        "keepers": keepers,
        "verdict": (f"{len(keepers)} keeper(s), none failing"
                    if not failing
                    else f"{len(failing)} keeper(s) with "
                         "consecutive failures"),
    }



# ── 49. runner fleet console + job replay (U30: D-19 + D-18) ───────
#
# SCOPE NOTE — the backlog framed D-18+D-19 as a runner-control (A)
# pair assuming neither existed. In fact:
#   • D-19 (pause/resume) ALREADY exists — /api/sites/<sid>/pause,
#     /resume, /stop, plus /bulk_pause//bulk_resume. Re-implementing
#     it would be a refactor nobody asked for (lesson E10). What is
#     genuinely missing is the *console* half — a one-call read-only
#     view of every runner's state. That is runner_console below.
#   • D-18 (job replay) — bulk retry exists (batch_ops.bulk_retry),
#     but a single-URL targeted replay-by-id does not. job_replay
#     below adds exactly that, and DELEGATES to batch_ops.bulk_retry
#     with a one-element id_in filter — it does not hand-write the
#     history table.
# So U30 ships: runner_console (R) + job_replay (A). It deliberately
# does NOT add a second pause/resume path.

def runner_console():
    """D-19 (R) — one-call fleet view of every live SiteRunner: its
    state (running / paused / stopped / window_paused / ...), worker
    count, queue/job counts. Read-only — the inspection half of the
    pause/resume console (the action half already exists at
    /api/sites/<sid>/pause//resume)."""
    try:
        from bulk_downloader import app as _app
    except Exception as e:
        return {"tool": "runner_console", "ok": False,
                "error": f"app module unavailable: {e}"}
    runners = getattr(_app, "runners", {}) or {}
    fleet = []
    state_tally = {}
    for sid, runner in list(runners.items()):
        state = getattr(runner, "_state", "unknown")
        state_tally[state] = state_tally.get(state, 0) + 1
        jobs = getattr(runner, "jobs", {}) or {}
        job_status = {}
        for j in jobs.values():
            st = j.get("status", "?")
            job_status[st] = job_status.get(st, 0) + 1
        wt = getattr(runner, "_worker_threads", []) or []
        fleet.append({
            "site_id": sid,
            "state": state,
            "worker_threads": len(wt),
            "total_jobs": len(jobs),
            "job_status_counts": job_status,
            "paused": state in ("paused", "window_paused",
                                "maintenance_paused", "low_disk",
                                "paused_no_button"),
        })
    fleet.sort(key=lambda r: r["site_id"])
    paused_n = sum(1 for r in fleet if r["paused"])
    return {
        "tool": "runner_console",
        "ok": True,
        "runner_count": len(fleet),
        "state_tally": state_tally,
        "paused_runners": paused_n,
        "runners": fleet,
        "verdict": (f"{len(fleet)} runner(s): "
                    f"{len(fleet) - paused_n} active, "
                    f"{paused_n} paused"
                    if fleet else "no runners (none in test mode, or "
                                  "no sites configured)"),
        "note": ("read-only console — to pause/resume a site use the "
                 "existing /api/sites/<sid>/pause and /resume routes"),
    }



def job_replay(history_id=None, dry_run=True):
    """D-18 (A) — replay a single completed/failed download by its
    history-row id: re-queue it so a worker picks it up again. This
    delegates to batch_ops.bulk_retry with a one-element id_in filter
    (it does NOT hand-write the history table). dry_run=True (the
    default) reports what it WOULD do without changing anything."""
    if history_id is None:
        return {"tool": "job_replay", "ok": False,
                "error": "history_id is required"}
    try:
        history_id = int(history_id)
    except (TypeError, ValueError):
        return {"tool": "job_replay", "ok": False,
                "error": "history_id must be an integer"}
    dry_run = bool(dry_run) if not isinstance(dry_run, str) \
        else dry_run.lower() not in ("false", "0", "no", "")
    try:
        from bulk_downloader import batch_ops as _bo
    except Exception as e:
        return {"tool": "job_replay", "ok": False,
                "error": f"batch_ops unavailable: {e}"}
    try:
        # one-element id_in filter — bulk_retry resets the matching
        # history row to 'pending' (atomically, via its own db path)
        result = _bo.bulk_retry({"id_in": [history_id]},
                                dry_run=dry_run,
                                reset_to_status="pending")
    except Exception as e:
        return {"tool": "job_replay", "ok": False,
                "error": f"replay failed: {type(e).__name__}: {e}"}
    matched = result.get("candidates_matched", 0)
    if matched == 0:
        return {"tool": "job_replay", "ok": False,
                "history_id": history_id,
                "error": f"no history row with id {history_id}"}
    return {
        "tool": "job_replay",
        "ok": True,
        "history_id": history_id,
        "dry_run": dry_run,
        "matched": matched,
        "processed": result.get("processed", 0),
        "sample": result.get("sample"),
        "verdict": (f"dry run — history row {history_id} would be "
                    f"re-queued (reset to 'pending')" if dry_run
                    else f"history row {history_id} re-queued — a "
                         f"worker will pick it up"),
    }



# ── T41 / D-16 — download-window simulator ─────────────────────────

def window_simulate(*, window_spec, samples=None, window_enabled=True):
    """T41 / D-16 — given a window spec like '09:00-17:00,22:00-06:00'
    and a list of synthetic timestamps, report whether each falls in
    an active window. Stateless — does not touch the running scheduler.

    Args:
      window_spec: string in the same format as cfg['window_active_hours']
      samples: optional list of ISO datetime strings. If omitted,
               we sample 24 hours hourly from midnight.
      window_enabled: mirrors cfg['window_enabled']; if False, every
                      sample is in-window (no restriction).

    Returns {tool, ok, parsed_windows[], samples[], transitions[],
    open_fraction, verdict}.
    """
    import datetime as _dt
    out = {
        "tool": "window_simulate",
        "ok": True,
        "parsed_windows": [],
        "samples": [],
        "transitions": [],
        "open_fraction": 0.0,
        "verdict": "",
    }
    try:
        from bulk_downloader import download_window as _dw
    except Exception as e:
        out["ok"] = False
        out["verdict"] = f"download_window import failed: {e}"
        return out
    windows = _dw.parse_windows(window_spec or "")
    out["parsed_windows"] = [
        {"start_min": s, "end_min": e,
         "start_hhmm": f"{s//60:02d}:{s%60:02d}",
         "end_hhmm": f"{e//60:02d}:{e%60:02d}",
         "crosses_midnight": s >= e}
        for s, e in windows
    ]
    if samples is None:
        # Default: hourly samples for one day starting at midnight
        # local. We use today() as the anchor, but only the time-of-
        # day matters to in_window().
        today = _dt.date.today()
        samples_dt = [
            _dt.datetime.combine(today, _dt.time(h, 0))
            for h in range(24)
        ]
    else:
        samples_dt = []
        for s in samples:
            try:
                samples_dt.append(_dt.datetime.fromisoformat(str(s)))
            except (TypeError, ValueError):
                continue
    # Walk samples, record open/closed; record transitions.
    cfg = {"window_enabled": bool(window_enabled),
           "window_active_hours": window_spec or ""}
    prev_open: bool = None
    open_count = 0
    for t in samples_dt:
        is_open = _dw.site_in_window(cfg, now=t)
        out["samples"].append({
            "ts": t.isoformat(timespec="minutes"),
            "open": is_open,
        })
        if is_open:
            open_count += 1
        if prev_open is not None and prev_open != is_open:
            out["transitions"].append({
                "ts": t.isoformat(timespec="minutes"),
                "to": "open" if is_open else "closed",
            })
        prev_open = is_open
    if samples_dt:
        out["open_fraction"] = round(open_count / len(samples_dt), 4)
    n_open = sum(1 for s in out["samples"] if s["open"])
    n_total = len(out["samples"])
    out["verdict"] = (
        f"{n_open}/{n_total} sample(s) in-window "
        f"({len(out['parsed_windows'])} window(s), "
        f"{len(out['transitions'])} transition(s))")
    return out
