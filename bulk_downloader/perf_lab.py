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


# v3.66.819 -- how many orphans we will name before truncating. The payload is
# served over HTTP and read by a live check; a runaway leak must not turn it into
# a thousand-entry dump.
_ORPHAN_DETAIL_CAP = 20


def _proc_parent_and_state(root: str, entry: str):
    """(ppid, state) from /proc/<pid>/status, or (None, None) if unreadable.

    `status` rather than `stat`: stat's comm field is parenthesised and may
    itself contain spaces or parentheses, so field-splitting stat is a known way
    to read the wrong column. status is line-oriented and unambiguous.
    """
    try:
        with open(f"{root}/{entry}/status", encoding="utf-8") as fh:
            body = fh.read()
    except Exception:
        return None, None
    ppid = state = None
    for line in body.splitlines():
        if line.startswith("PPid:"):
            try:
                ppid = int(line.split()[1])
            except Exception:
                ppid = None
        elif line.startswith("State:"):
            parts = line.split(None, 1)
            state = parts[1].strip() if len(parts) > 1 else None
        if ppid is not None and state is not None:
            break
    return ppid, state


def _descends_from(pid: int, ancestor: int, ppid_of: dict) -> bool:
    """Is `pid` anywhere below `ancestor` in the process tree?

    A single PPid comparison is not enough: Playwright interposes its node
    driver, so a browser BD launched has the DRIVER as its parent, not BD. The
    chain has to be walked. `seen` guards against a cycle -- /proc is sampled
    non-atomically, so a pid can be recycled between reads and produce one.
    """
    seen = set()
    cur = pid
    while cur and cur not in seen:
        if cur == ancestor:
            return True
        seen.add(cur)
        cur = ppid_of.get(cur)
        if cur in (0, 1, None):
            return False
    return False


def _parent_is_gone(ppid: int, live_pids: set) -> bool:
    """Has this process's launcher died?

    True when the parent is init (pid 1 -- the classic reparent) or when the
    recorded ppid is not in the table at all, which happens if the parent exited
    between the two reads. Both mean nobody is holding this process.

    False for a browser whose parent is alive and simply is not us: an operator
    running tools/capture_session.py or tools/nav_probe.py owns those, and they
    are not a leak.
    """
    if ppid is None:
        return True
    if ppid <= 1:
        return True
    return ppid not in live_pids


def _user_data_dir(cmdline: str):
    """The --user-data-dir a browser was launched with, if it says.

    Reported as DETAIL so the operator can tell WHICH browser leaked. Never used
    as a filter: BD launches browsers both with a stable BD-owned profile
    (login_impl/manual.py, login_impl/replay.py) and with plain launch(), whose
    profile is an ephemeral /tmp/playwright_chromiumdev_profile-XXXXXX -- and
    renderer children carry no --user-data-dir at all (measured: 1 of 6). A
    filter on this would miss every orphan from the plain-launch path.
    """
    for tok in cmdline.split(" "):
        if tok.startswith("--user-data-dir="):
            return tok.split("=", 1)[1] or None
    return None


def _child_process_count(proc_root: str = "/proc",
                         self_pid: int = None) -> dict:
    """Classify browser/ffmpeg processes: live, zombie, or actually ORPHANED.

    v3.66.819 -- THIS USED TO BE A SUBSTRING MATCH CALLED AN ORPHAN COUNT.
    The whole measurement was `"chrom" in comm or "headless" in comm` over every
    process in /proc. It read no PPid, so nothing established that a process was
    orphaned, and it was scoped to no process tree, so nothing established that
    BD launched it -- yet "orphan" and "leaked Playwright contexts" were asserted
    on it by dev_suite.leak_scan and by live check L33.

    A live Chromium is a TREE. Measured with one real headless browser and a
    single blank page: 6 matching processes, all descendants of the launching
    pid. The deploy host measured peak 22 during a real download, so leak_scan's
    `> 8` threshold fired and L33 reported "Playwright contexts may be leaking"
    about a working fetch. CLAUDE.md section 0's inverse -- a gate that cries
    wolf gets switched off.

    ZOMBIES ARE NOT LEAKS, and this is the part that is easy to get wrong.
    Measured immediately after a CORRECT browser.close(): 2 processes remain at
    ppid=1 in state Z for ~3 seconds, then vanish. They are not descendants of
    the app, so the natural definition of orphaned flags them on every healthy
    close. A zombie holds no browser, no port, no profile lock and no memory
    beyond its process-table entry. `State: Z` excludes it exactly.

    NOR IS SOMEBODY ELSE'S BROWSER. "Not a descendant of the app" is not the
    same as "orphaned", and this repo ships two counter-examples:
    tools/capture_session.py and tools/nav_probe.py are standalone operator CLIs
    that launch their own browsers. Those are nobody's leak -- their launcher is
    alive -- but they are not descendants of the Flask app, so a
    descendant-test-only predicate calls them orphans, and an operator running a
    capture session by hand while the checks sample would see a phantom leak.
    They land in `chromium_foreign`.

    So an orphan is: matches a browser comm, is NOT in state Z, is NOT a
    descendant of the running app, AND its parent is gone -- reparented to init,
    or pointing at a pid no longer in the table. That last clause is what the
    word actually means, and it holds regardless of who launched the browser.
    Browsers stranded by a previous app instance qualify, which is a real leak
    class the old number could not tell from a busy download.

    The four buckets partition `chromium`: live + zombie + foreign + orphan.

    UNKNOWN IS NOT ZERO. `chromium_orphan` is None -- not 0 -- whenever the
    classification could not be made: /proc has no status for a confirmed
    browser, or the app's own pid is not visible in this /proc at all (a
    different namespace, or an injected root). Both would otherwise make every
    browser on the box read as an orphan.

    `chromium`, `ffmpeg` and `total_procs` keep their exact old meanings, because
    perf_lab's own report, dev_suite.leak_scan and the existing tests read them.
    `chromium` is still the raw comm-match count across every state.

    `proc_root` and `self_pid` exist so the classification can be tested against
    a posed process table; production calls pass neither.
    """
    out = {"chromium": 0, "ffmpeg": 0, "total_procs": 0,
           "chromium_live": 0, "chromium_zombie": 0,
           "chromium_foreign": 0, "chromium_orphan": 0, "orphan_detail": []}
    if self_pid is None:
        self_pid = os.getpid()
    root = str(proc_root)
    try:
        entries = [e for e in os.listdir(root) if e.isdigit()]
    except Exception:
        # Unchanged contract: no /proc at all means nothing was measured, and
        # {} is what every existing caller already handles. L33 turns this into
        # NA ("not exercisable here"), never into a PASS.
        return {}

    ppid_of: dict = {}
    browsers: list = []
    unclassifiable = False
    seen_self = False
    for entry in entries:
        out["total_procs"] += 1
        pid = int(entry)
        if pid == self_pid:
            seen_self = True
        try:
            with open(f"{root}/{entry}/comm", encoding="utf-8") as fh:
                comm = fh.read().strip().lower()
        except Exception:
            # The pid exited between listdir and read. It is gone, so it is not
            # leaking anything; skipping it is correct, and NOT knowing whether
            # it was a browser is not the same as failing to classify one.
            continue
        ppid, state = _proc_parent_and_state(root, entry)
        if ppid is not None:
            ppid_of[pid] = ppid
        # The original predicate, byte for byte. Note which clause fires: the
        # measured comm is 'chrome-headless' -- exactly 15 chars, Linux's comm
        # limit -- so the real executable name (chrome-headless-shell) is
        # TRUNCATED. Both clauses happen to match it; the cmdline, read below
        # for detail only, carries the untruncated path.
        if "chrom" in comm or "headless" in comm:
            out["chromium"] += 1
            if ppid is None or state is None:
                unclassifiable = True
                continue
            try:
                with open(f"{root}/{entry}/cmdline", "rb") as fh:
                    cmdline = fh.read().replace(b"\0", b" ").decode(
                        "utf-8", "replace")
            except Exception:
                cmdline = ""
            browsers.append((pid, comm, ppid, state, cmdline))
        elif "ffmpeg" in comm:
            out["ffmpeg"] += 1

    live_pids = set(ppid_of) | {p for p, _c, _pp, _s, _cl in browsers}
    for pid, comm, ppid, state, cmdline in browsers:
        if state.upper().startswith("Z"):
            out["chromium_zombie"] += 1
        elif _descends_from(pid, self_pid, ppid_of):
            out["chromium_live"] += 1
        elif not _parent_is_gone(ppid, live_pids):
            # SOMEBODY ELSE OWNS IT, and it is not orphaned.
            #
            # "Not a descendant of the app" is not the same as "orphaned", and
            # this repo has two shipped counter-examples: tools/capture_session.py
            # and tools/nav_probe.py are standalone operator CLIs that launch
            # their own browsers. Those browsers are nobody's leak -- their
            # launcher is sitting right there, alive -- but they are not
            # descendants of the Flask app either, so the descendant test alone
            # calls them orphans. An operator running a capture session by hand
            # while the live checks sample would have seen a phantom leak.
            #
            # An orphan is a process whose PARENT IS GONE: reparented to init
            # (ppid 1), or pointing at a pid that is no longer in the table.
            # That is the actual meaning of the word, and it holds regardless of
            # who launched the browser.
            out["chromium_foreign"] += 1
        else:
            out["chromium_orphan"] += 1
            if len(out["orphan_detail"]) < _ORPHAN_DETAIL_CAP:
                out["orphan_detail"].append({
                    "pid": pid, "comm": comm, "ppid": ppid,
                    "user_data_dir": _user_data_dir(cmdline)})

    if unclassifiable or (browsers and not seen_self):
        # Either a confirmed browser had no readable status, or this /proc does
        # not contain the pid we would measure descent from. In both cases a
        # count would be manufactured, and every live browser would read as an
        # orphan. Say unknown.
        out["chromium_orphan"] = None
        out["orphan_detail"] = []
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
