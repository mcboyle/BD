"""Performance lab — in-app memory audit + load injector.

Dev-only diagnostics, gated like the rest of /api/dev/* via
dev_tools.is_dev_mode(). Two capabilities:

  • memory audit  — snapshot process RSS, interpreter heap, thread and
    child-process counts, and the app's bounded in-memory structures;
    optional tracemalloc tracking; a settle-and-diff audit that flags
    growth. Strictly read-only.

  • load injector — push synthetic load (RSS, OS threads, object
    churn, DB queue rows) so the audit can observe behaviour under
    stress. Magnitude is operator-set — this is a stress tool, not
    throttled — but every profile is cancellable and fully reversible:
    synthetic DB rows live under a reserved site_id and purge in one
    statement; held allocations release on stop/purge.

Nothing in this module runs at import time, and no thread is spawned
until start_injection() is explicitly called.
"""
from __future__ import annotations

import gc
import os
import threading
import time
import tracemalloc
import uuid
from typing import Optional

# Reserved synthetic site_id for the DB-queue load profile. Has no
# matching real site; purge is a single DELETE on this id.
_LOADTEST_SITE_ID = "__loadtest__"


# ════════════════════ memory audit (read-only) ════════════════════

def _rss_bytes() -> Optional[int]:
    """Resident set size of this process, best-effort. Linux /proc
    first (the live deployment), then resource.getrusage. Returns None
    rather than raising when neither is available."""
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024   # kB → bytes
    except Exception:
        pass
    try:
        import resource
        # Linux ru_maxrss is kB (the live target); good enough as a
        # fallback when /proc is unavailable.
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except Exception:
        return None


def _child_process_count() -> dict:
    """Count Chromium/ffmpeg-ish processes — a proxy for leaked
    Playwright contexts / undead ffmpeg children. Linux /proc walk;
    returns {} when /proc is not available."""
    out = {"chromium": 0, "ffmpeg": 0, "total_procs": 0}
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            out["total_procs"] += 1
            try:
                with open(f"/proc/{entry}/comm", encoding="utf-8") as fh:
                    comm = fh.read().strip().lower()
            except Exception:
                continue
            if "chrom" in comm or "headless" in comm:
                out["chromium"] += 1
            elif "ffmpeg" in comm:
                out["ffmpeg"] += 1
    except Exception:
        return {}
    return out


def _interpreter_stats() -> dict:
    objs = gc.get_objects()
    by_type: dict = {}
    for o in objs:
        n = type(o).__name__
        by_type[n] = by_type.get(n, 0) + 1
    top = sorted(by_type.items(), key=lambda kv: kv[1], reverse=True)[:12]
    return {
        "gc_objects": len(objs),
        "gc_counts": list(gc.get_count()),
        "gc_garbage": len(gc.garbage),
        "top_types": [{"type": t, "count": c} for t, c in top],
    }


def _tracemalloc_top(limit: int = 12) -> Optional[list]:
    if not tracemalloc.is_tracing():
        return None
    try:
        stats = tracemalloc.take_snapshot().statistics("lineno")[:limit]
        return [{"where": str(s.traceback),
                 "size_kb": round(s.size / 1024, 1),
                 "count": s.count} for s in stats]
    except Exception:
        return None


def _app_structures(runners=None) -> dict:
    """Sizes of the app's bounded in-memory structures — the things a
    memory audit actually cares about. `runners` is app.runners,
    passed in by the caller to avoid a circular import."""
    out: dict = {}
    try:
        from . import mass_import as _mi
        out["mass_import_jobs"] = len(_mi._jobs)
        out["mass_import_jobs_cap"] = _mi._MAX_RETAINED_JOBS
    except Exception:
        pass
    try:
        from . import runner as _r
        out["bw_history"] = len(_r._bw_history)
    except Exception:
        pass
    if runners:
        per = []
        total_jobs = 0
        for sid, rn in list(runners.items()):
            try:
                j = len(getattr(rn, "jobs", {}) or {})
                total_jobs += j
                per.append({
                    "site_id": sid, "jobs": j,
                    "urls": len(getattr(rn, "urls", []) or []),
                    "event_log": len(getattr(rn, "_event_log", []) or []),
                })
            except Exception:
                continue
        out["runners"] = per
        out["runner_count"] = len(per)
        out["total_queued_jobs"] = total_jobs
    return out


def snapshot(runners=None) -> dict:
    """A full point-in-time memory picture. Read-only; safe anytime."""
    rss = _rss_bytes()
    return {
        "ts": time.time(),
        "rss_bytes": rss,
        "rss_mb": round(rss / (1024 * 1024), 1) if rss else None,
        "threads": threading.active_count(),
        "processes": _child_process_count(),
        "interpreter": _interpreter_stats(),
        "tracemalloc_tracing": tracemalloc.is_tracing(),
        "tracemalloc": _tracemalloc_top(),
        "app_structures": _app_structures(runners),
    }


def audit(settle_seconds: float = 3.0, runners=None) -> dict:
    """Take a baseline, settle (gc.collect + wait), take a second
    snapshot, and report deltas with heuristic findings. Run a load
    profile first, then this, to see growth under / after stress.

    The findings are tuned for an *idle* settle window — growth during
    a window with no work running is the signal of a leak."""
    try:
        settle_seconds = max(0.0, min(float(settle_seconds), 60.0))
    except Exception:
        settle_seconds = 3.0
    base = snapshot(runners)
    gc.collect()
    time.sleep(settle_seconds)
    final = snapshot(runners)

    d_rss = None
    if base["rss_bytes"] and final["rss_bytes"]:
        d_rss = final["rss_bytes"] - base["rss_bytes"]
    d_objs = (final["interpreter"]["gc_objects"]
              - base["interpreter"]["gc_objects"])
    d_threads = final["threads"] - base["threads"]

    findings = []
    if d_rss is not None and d_rss > 50 * 1024 * 1024:
        findings.append(f"RSS grew {d_rss // (1024 * 1024)} MB during a "
                        f"{settle_seconds:.0f}s idle settle — possible leak")
    if d_objs > 50_000:
        findings.append(f"interpreter object count grew by {d_objs:,} "
                        "while idle — possible object retention")
    if d_threads > 0:
        findings.append(f"thread count rose by {d_threads} during an idle "
                        "window — threads may not be getting joined")
    procs = final.get("processes") or {}
    if procs.get("chromium", 0) > 8:
        findings.append(f"{procs['chromium']} Chromium processes alive — "
                        "check for leaked Playwright contexts")
    if final["interpreter"]["gc_garbage"] > 0:
        findings.append(f"{final['interpreter']['gc_garbage']} uncollectable "
                        "objects in gc.garbage — reference cycles with __del__")
    aps = final.get("app_structures") or {}
    mij, cap = aps.get("mass_import_jobs"), aps.get("mass_import_jobs_cap")
    if mij is not None and cap and mij > cap:
        findings.append(f"mass_import._jobs at {mij} exceeds its {cap} cap")

    return {
        "settle_seconds": settle_seconds,
        "baseline": base,
        "final": final,
        "deltas": {
            "rss_bytes": d_rss,
            "rss_mb": round(d_rss / (1024 * 1024), 1)
                      if d_rss is not None else None,
            "gc_objects": d_objs,
            "threads": d_threads,
        },
        "findings": findings or ["no growth flagged in this window"],
    }


def tracemalloc_start(frames: int = 15) -> dict:
    """Begin tracemalloc tracking so subsequent snapshots carry a
    top-allocations breakdown. Has runtime overhead — opt-in."""
    if tracemalloc.is_tracing():
        return {"ok": True, "already_tracing": True}
    try:
        frames = max(1, min(int(frames), 50))
    except Exception:
        frames = 15
    tracemalloc.start(frames)
    return {"ok": True, "already_tracing": False, "frames": frames}


def tracemalloc_stop() -> dict:
    was = tracemalloc.is_tracing()
    if was:
        tracemalloc.stop()
    return {"ok": True, "was_tracing": was}


# ═══════════════════════ load injector ════════════════════════════

_LOAD_PROFILES = ("memory", "threads", "objects", "queue")

_load_lock = threading.RLock()
_load_state: dict = {"running": False}   # current / last run summary
_load_cancel = threading.Event()
# References held so a profile's footprint is real until stop/purge:
_load_blobs: list = []      # bytearrays — the memory profile
_load_objects: list = []    # objects    — the object-churn profile
_load_threads: list = []    # busy threads — the thread profile


def injection_status() -> dict:
    with _load_lock:
        return dict(_load_state)


def start_injection(profile: str, magnitude, runners=None) -> dict:
    """Begin a synthetic-load run in a background thread.

    profile   — one of _LOAD_PROFILES
    magnitude — profile-specific size knob (memory: MB; threads: thread
                count; objects: thousands of objects; queue: row count).
                Operator-set and deliberately not capped low.
    """
    profile = str(profile or "").strip().lower()
    if profile not in _LOAD_PROFILES:
        return {"ok": False,
                "error": f"unknown profile {profile!r}; choose from "
                         f"{', '.join(_LOAD_PROFILES)}"}
    try:
        magnitude = int(magnitude)
    except Exception:
        return {"ok": False, "error": "magnitude must be an integer"}
    if magnitude <= 0:
        return {"ok": False, "error": "magnitude must be positive"}

    with _load_lock:
        if _load_state.get("running"):
            return {"ok": False,
                    "error": "a load run is already active — stop it first"}
        run_id = uuid.uuid4().hex[:12]
        _load_cancel.clear()
        _load_state.clear()
        _load_state.update({
            "running": True, "run_id": run_id, "profile": profile,
            "magnitude": magnitude, "progress": 0, "detail": "starting",
            "error": "", "started_ts": time.time(), "finished_ts": None,
        })
    t = threading.Thread(target=_load_worker,
                         args=(profile, magnitude, runners),
                         daemon=True, name=f"load-inject-{run_id}")
    t.start()
    return {"ok": True, "run_id": run_id,
            "profile": profile, "magnitude": magnitude}


def stop_injection() -> dict:
    """Signal the active run to stop. Held allocations are NOT freed —
    call purge() for that."""
    _load_cancel.set()
    with _load_lock:
        running = bool(_load_state.get("running"))
    return {"ok": True, "was_running": running}


def purge(runners=None) -> dict:
    """Release ALL synthetic load: cancel the run, free held memory and
    objects, signal load-threads to exit, delete synthetic queue rows.
    Safe to call anytime — also the cleanup entry point."""
    _load_cancel.set()
    freed: dict = {}
    with _load_lock:
        freed["memory_chunks_freed"] = len(_load_blobs)
        freed["objects_freed"] = len(_load_objects)
        freed["load_threads_signalled"] = len(_load_threads)
        _load_blobs.clear()
        _load_objects.clear()
        _load_threads.clear()
    gc.collect()
    rows = 0
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            r = cx.execute("SELECT COUNT(*) FROM queue WHERE site_id=?",
                           (_LOADTEST_SITE_ID,)).fetchone()
            rows = int(r[0]) if r else 0
        _db.queue_delete_site(_LOADTEST_SITE_ID)
    except Exception as e:
        freed["queue_purge_error"] = str(e)[:200]
    freed["queue_rows_deleted"] = rows
    return {"ok": True, "freed": freed}


def _set(**kw):
    with _load_lock:
        _load_state.update(kw)


def _load_worker(profile, magnitude, runners):
    try:
        if profile == "memory":
            _profile_memory(magnitude)
        elif profile == "threads":
            _profile_threads(magnitude)
        elif profile == "objects":
            _profile_objects(magnitude)
        elif profile == "queue":
            _profile_queue(magnitude)
    except Exception as e:
        _set(error=f"{type(e).__name__}: {e}"[:200])
    finally:
        with _load_lock:
            _load_state["running"] = False
            _load_state["finished_ts"] = time.time()
            if not _load_state.get("error"):
                _load_state["detail"] = (
                    "cancelled" if _load_cancel.is_set() else "complete")


def _profile_memory(target_mb):
    """Allocate target_mb MB in 4 MB chunks, touching every page so the
    RSS cost is real (not just reserved). Held until stop/purge."""
    chunk = 4 * 1024 * 1024
    chunks = max(1, (target_mb * 1024 * 1024) // chunk)
    for i in range(chunks):
        if _load_cancel.is_set():
            return
        b = bytearray(chunk)
        for p in range(0, chunk, 4096):
            b[p] = 1
        _load_blobs.append(b)
        _set(progress=i + 1, detail=f"allocated {(i + 1) * 4} MB "
             f"/ {target_mb} MB held")
        time.sleep(0.01)


def _profile_threads(n):
    """Spawn n OS threads that idle on the cancel event — grows the
    live thread count. They exit cleanly on stop/purge."""
    for i in range(n):
        if _load_cancel.is_set():
            return
        t = threading.Thread(target=_load_cancel.wait,
                              daemon=True, name=f"load-thread-{i}")
        t.start()
        _load_threads.append(t)
        _set(progress=i + 1, detail=f"spawned {i + 1} / {n} idle threads")
        if i % 50 == 0:
            time.sleep(0.005)


def _profile_objects(thousands):
    """Create thousands*1000 small objects and hold them — grows the
    interpreter heap / gc object count. Released on stop/purge."""
    total = thousands * 1000
    made = 0
    while made < total:
        if _load_cancel.is_set():
            return
        for _ in range(min(1000, total - made)):
            _load_objects.append(
                {"k": made, "payload": "x" * 64, "nested": {"a": [made] * 4}})
            made += 1
        _set(progress=made, detail=f"created {made:,} / {total:,} objects")
        time.sleep(0.005)


def _profile_queue(n):
    """Insert n synthetic rows into the queue table under the reserved
    loadtest site_id — stresses the DB write path. purge() removes
    them in one DELETE; no real site is touched."""
    from . import db as _db
    run_id = injection_status().get("run_id", "x")
    done = 0
    while done < n:
        if _load_cancel.is_set():
            return
        size = min(2000, n - done)
        urls = [f"http://loadtest.invalid/synthetic/{run_id}/{done + i}"
                for i in range(size)]
        _db.queue_bulk_upsert(_LOADTEST_SITE_ID, urls, ord_start=done)
        done += size
        _set(progress=done, detail=f"inserted {done:,} / {n:,} queue rows")
        time.sleep(0.005)
