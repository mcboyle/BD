"""dev_suite.perf_metrics -- performance & runtime metrics

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

from ._common import (
    _percentile)



# ── 11. leak scan ──────────────────────────────────────────────────

def leak_scan() -> dict:
    """Scan for resource-leak signals — orphan Chromium/ffmpeg
    processes, an accumulation of rl_*.json files, an oversized
    screenshots directory."""
    findings = []
    out: dict = {"findings": findings}
    try:
        from bulk_downloader import perf_lab as _pl
        procs = _pl._child_process_count()
        out["processes"] = procs
        if procs.get("chromium", 0) > 8:
            findings.append(f"{procs['chromium']} Chromium processes alive "
                            "— possible leaked Playwright contexts")
        if procs.get("ffmpeg", 0) > 4:
            findings.append(f"{procs['ffmpeg']} ffmpeg processes alive "
                            "— possible orphaned segment downloads")
    except Exception:
        out["processes"] = {}
    try:
        rl = sorted(p for p in os.listdir(".")
                    if p.startswith("rl_") and p.endswith(".json"))
        out["rate_limit_files"] = rl
        if len(rl) > 20:
            findings.append(f"{len(rl)} rl_*.json files in cwd — may "
                            "include stale entries for deleted sites")
    except Exception:
        out["rate_limit_files"] = []
    try:
        ss = Path("screenshots")
        if ss.is_dir():
            files = [p for p in ss.rglob("*") if p.is_file()]
            total = sum(p.stat().st_size for p in files)
            out["screenshots"] = {"files": len(files), "bytes": total}
            if total > 500 * 1024 * 1024:
                findings.append(f"screenshots dir is "
                                f"{total // (1024 * 1024)} MB — consider "
                                "cleanup")
        else:
            out["screenshots"] = {"files": 0, "bytes": 0}
    except Exception:
        out["screenshots"] = {}
    out["verdict"] = ("no leak signals" if not findings
                      else f"{len(findings)} leak signal(s)")
    return out



# ── 27. rate-limit state inspector (D-44) ──────────────────────────

def rate_limit_state(runners=None) -> dict:
    """Per-site rate-limit state — each runner's _rl_until cooldown
    timer and whether it is limited right now — plus the rl_*.json
    cooldown files on disk. Caller passes app.runners. Only the
    current cooldown is tracked; there is no historical 429 log."""
    import time as _t
    now = _t.time()
    sites = []
    for sid, rn in list((runners or {}).items()):
        try:
            until = float(getattr(rn, "_rl_until", 0.0) or 0.0)
            limited = bool(until and now < until)
            sites.append({
                "site_id": sid,
                "rate_limited": limited,
                "rl_until_epoch": until,
                "seconds_remaining": (max(0, int(until - now))
                                      if limited else 0),
            })
        except Exception as e:
            sites.append({"site_id": sid, "error": str(e)[:120]})
    rl_files = []
    try:
        rl_files = sorted(p.name for p in Path(".").glob("rl_*.json"))
    except Exception:
        pass
    limited_now = [s for s in sites if s.get("rate_limited")]
    return {
        "runner_count": len(sites),
        "rate_limited_now": len(limited_now),
        "sites": sites,
        "rl_files_on_disk": rl_files,
        "verdict": ("no sites are rate-limited" if not limited_now
                    else f"{len(limited_now)} site(s) rate-limited"),
    }



# ── 30. endpoint latency histogram (D-64) ──────────────────────────

def latency_histogram() -> dict:
    """p50 / p95 / p99 / max request latency per route, computed from
    the in-process request ring buffer (the most recent requests).
    Routes are sorted slowest-p95 first."""
    from bulk_downloader import dev_metrics as _dm
    reqs = _dm.request_snapshot()
    by_rule: dict = {}
    for r in reqs:
        by_rule.setdefault(r["rule"], []).append(r["duration_ms"])
    routes = []
    for rule, vals in by_rule.items():
        vals.sort()
        routes.append({
            "rule": rule,
            "count": len(vals),
            "p50_ms": round(_percentile(vals, 50), 2),
            "p95_ms": round(_percentile(vals, 95), 2),
            "p99_ms": round(_percentile(vals, 99), 2),
            "max_ms": round(vals[-1], 2),
        })
    routes.sort(key=lambda x: x["p95_ms"], reverse=True)
    return {
        "sampled_requests": len(reqs),
        "route_count": len(routes),
        "routes": routes,
        "verdict": (f"{len(reqs)} request(s) across {len(routes)} "
                    "route(s)" if reqs
                    else "no requests recorded yet"),
    }



# ── 31. slow-endpoint flagger (D-67) ───────────────────────────────

def route_timing() -> dict:
    """OBS-2 (v3.66.658): per-route latency percentiles (p50/p95/max + count) over
    the in-process recent-request ring buffer, for the status-page timing panel.
    Thin wrapper over dev_metrics.route_percentiles() -- the 'OBS-2 core' that
    already existed but had no consumer. Read-only; mirrors slow_endpoints/error_rate."""
    from bulk_downloader import dev_metrics as _dm
    pct = _dm.route_percentiles() or {}
    routes = sorted(
        ({"rule": rule, **stats} for rule, stats in pct.items()),
        key=lambda x: x.get("p95", 0.0), reverse=True)
    return {"route_count": len(routes), "routes": routes}


def slow_endpoints(threshold_ms=1000) -> dict:
    """Requests in the in-process ring buffer whose latency exceeded
    threshold_ms, grouped by route (slowest first)."""
    try:
        threshold_ms = max(1.0, float(threshold_ms))
    except Exception:
        threshold_ms = 1000.0
    from bulk_downloader import dev_metrics as _dm
    reqs = _dm.request_snapshot()
    slow = [r for r in reqs if r["duration_ms"] > threshold_ms]
    by_rule: dict = {}
    for r in slow:
        e = by_rule.setdefault(r["rule"], {"rule": r["rule"],
                                           "count": 0, "max_ms": 0.0})
        e["count"] += 1
        e["max_ms"] = round(max(e["max_ms"], r["duration_ms"]), 2)
    routes = sorted(by_rule.values(), key=lambda x: x["max_ms"],
                    reverse=True)
    return {
        "threshold_ms": threshold_ms,
        "sampled_requests": len(reqs),
        "slow_request_count": len(slow),
        "routes": routes,
        "verdict": (f"no requests over {threshold_ms:.0f}ms"
                    if not slow
                    else f"{len(slow)} request(s) over "
                         f"{threshold_ms:.0f}ms across "
                         f"{len(routes)} route(s)"),
    }



# ── 32. error-rate panel (D-62) ────────────────────────────────────

def error_rate() -> dict:
    """4xx / 5xx counts per route from the in-process request ring
    buffer."""
    from bulk_downloader import dev_metrics as _dm
    reqs = _dm.request_snapshot()
    by_rule: dict = {}
    total_4xx = total_5xx = 0
    for r in reqs:
        status = r["status"]
        e = by_rule.setdefault(r["rule"], {"rule": r["rule"],
                                           "total": 0, "4xx": 0,
                                           "5xx": 0})
        e["total"] += 1
        if 400 <= status < 500:
            e["4xx"] += 1
            total_4xx += 1
        elif status >= 500:
            e["5xx"] += 1
            total_5xx += 1
    routes = [e for e in by_rule.values() if e["4xx"] or e["5xx"]]
    routes.sort(key=lambda x: (x["5xx"], x["4xx"]), reverse=True)
    return {
        "sampled_requests": len(reqs),
        "total_4xx": total_4xx,
        "total_5xx": total_5xx,
        "routes_with_errors": routes,
        "verdict": (f"{total_4xx} 4xx, {total_5xx} 5xx in {len(reqs)} "
                    "sampled request(s)" if reqs
                    else "no requests recorded yet"),
    }



# ── 33. exception ring-buffer (D-66) ───────────────────────────────

def exception_log(limit=50) -> dict:
    """The most recent unhandled request exceptions with tracebacks,
    from the in-process ring buffer — newest first."""
    try:
        limit = max(1, min(int(limit), 200))
    except Exception:
        limit = 50
    from bulk_downloader import dev_metrics as _dm
    excs = _dm.exception_snapshot()
    recent = list(reversed(excs))[:limit]
    return {
        "captured": len(excs),
        "returned": len(recent),
        "exceptions": recent,
        "verdict": ("no unhandled exceptions captured" if not excs
                    else f"{len(excs)} unhandled exception(s) "
                         "captured since start"),
    }



# ── 34. thread dump with stacks (D-59) ─────────────────────────────

def thread_dump() -> dict:
    """Full stack trace of every live thread — the deep version of the
    name-only thread inventory. Uses sys._current_frames()."""
    import sys as _sys
    import threading as _th
    import traceback as _tb
    names = {t.ident: t.name for t in _th.enumerate()}
    threads = []
    for ident, frame in _sys._current_frames().items():
        stack = _tb.format_stack(frame)
        threads.append({
            "ident": ident,
            "name": names.get(ident, "?"),
            "frame_count": len(stack),
            "top_frame": (stack[-1].strip()[:300] if stack else ""),
            "stack": [s.rstrip() for s in stack],
        })
    threads.sort(key=lambda t: t["name"])
    return {"thread_count": len(threads), "threads": threads}



# ── 35. deadlock / stall detector (D-60) ───────────────────────────

# Innermost-frame signatures of *intentional* idle waits — a thread
# parked here is waiting for work, not stalled. Suppresses false
# alarms (an idle worker in Queue.get, a Condition wait, etc.).
_IDLE_WAIT_HINTS = (" in wait", " in select", " in poll", "condition",
                    "queue.py", "selectors.py", "selector_events",
                    "_bootstrap", "epoll", "kqueue", "event.wait",
                    "wait(", "join(")



def deadlock_detector() -> dict:
    """Heuristic stall / deadlock check. Python exposes no
    lock-ownership data, so this cannot name which lock is held by
    whom. Instead it takes two thread-stack snapshots ~0.6s apart and
    flags any thread — other than the one running this check — whose
    innermost frame is byte-for-byte identical in both AND does not
    look like an intentional idle wait (a Condition / Queue / Event /
    selector wait). A thread frozen at the same spot for 0.6s is the
    symptom worth a closer look; two or more at once is the classic
    deadlock shape. Pair this with thread_dump."""
    import sys as _sys
    import threading as _th
    import time as _t
    import traceback as _tb

    me = _th.get_ident()  # the inspecting thread is, by definition,
    #                       running — never a stall suspect

    def _top_frames():
        out = {}
        for ident, frame in _sys._current_frames().items():
            if ident == me:
                continue
            stack = _tb.format_stack(frame)
            out[ident] = (stack[-1].rstrip() if stack else "")
        return out

    names = {t.ident: t.name for t in _th.enumerate()}
    snap1 = _top_frames()
    _t.sleep(0.6)
    snap2 = _top_frames()

    suspects = []
    for ident in sorted(set(snap1) & set(snap2)):
        top = snap2[ident]
        if snap1[ident] != top:
            continue  # the thread advanced — not stalled
        if any(h in top.lower() for h in _IDLE_WAIT_HINTS):
            continue  # an intentional idle wait
        suspects.append({"ident": ident,
                         "name": names.get(ident, "?"),
                         "stuck_at": top.strip()[:300]})
    deadlock = len(suspects) >= 2
    if not suspects:
        verdict = "no stalled threads — nothing frozen across 0.6s"
    elif deadlock:
        verdict = (f"{len(suspects)} threads frozen at the same spot "
                   "across 0.6s — classic deadlock shape; inspect "
                   "with thread_dump")
    else:
        verdict = ("1 thread frozen across 0.6s — not a deadlock by "
                   "itself (could be a long blocking call); inspect "
                   "with thread_dump")
    return {
        "live_threads_scanned": len(snap2),
        "stalled_suspects": suspects,
        "deadlock_suspected": deadlock,
        "verdict": verdict,
    }
