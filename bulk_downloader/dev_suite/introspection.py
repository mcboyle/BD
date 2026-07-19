"""dev_suite.introspection -- core read-only state inspectors

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
    _proc_uptime_seconds)



# ── 1. route map ───────────────────────────────────────────────────

def route_map(app) -> dict:
    """Every registered Flask route. `app` is the Flask app object,
    passed in by the caller."""
    routes = []
    for rule in app.url_map.iter_rules():
        methods = sorted(m for m in rule.methods
                         if m not in ("HEAD", "OPTIONS"))
        routes.append({
            "rule": str(rule),
            "methods": methods,
            "endpoint": rule.endpoint,
            "dev_only": str(rule).startswith("/api/dev/"),
        })
    routes.sort(key=lambda r: r["rule"])
    return {"count": len(routes), "routes": routes}



# ── 2. thread inventory ────────────────────────────────────────────

def thread_inventory() -> dict:
    """Every live thread by identity — daemon session-keepers,
    schedulers, the load injector's threads, etc. A growing or
    duplicated set here points at a thread leak."""
    threads = []
    for t in threading.enumerate():
        threads.append({
            "name": t.name,
            "ident": t.ident,
            "daemon": t.daemon,
            "alive": t.is_alive(),
        })
    threads.sort(key=lambda x: x["name"])
    return {"count": len(threads), "threads": threads}



# ── 3. DB overview ─────────────────────────────────────────────────

def db_overview() -> dict:
    """Tables with row counts, index count, journal mode, and the
    on-disk size of the .db / -wal / -shm files."""
    from bulk_downloader.constants import DB_PATH  # noqa: F401 (legacy import kept for back-compat)
    from bulk_downloader import db as _db
    _resolved = _db._resolve_db_path()
    info: dict = {"db_path": str(_resolved)}
    for suffix, key in (("", "db_bytes"), ("-wal", "wal_bytes"),
                        ("-shm", "shm_bytes")):
        p = str(_resolved) + suffix
        info[key] = os.path.getsize(p) if os.path.exists(p) else 0
    try:
        with _db.db_conn() as cx:
            info["journal_mode"] = cx.execute(
                "PRAGMA journal_mode").fetchone()[0]
            tables = [r[0] for r in cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name")]
            tbl = []
            for name in tables:
                try:
                    # name comes from sqlite_master, not user input
                    n = cx.execute(
                        f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                except Exception:
                    n = None
                tbl.append({"table": name, "rows": n})
            info["tables"] = tbl
            info["table_count"] = len(tbl)
            info["index_count"] = cx.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='index'").fetchone()[0]
    except Exception as e:
        info["error"] = str(e)[:200]
    return info



# ── 4. runner state ────────────────────────────────────────────────

def runner_state(runners=None) -> dict:
    """Per-site live operational state. `runners` is app.runners,
    passed in to avoid a circular import."""
    out = []
    for sid, rn in list((runners or {}).items()):
        try:
            pause_ev = getattr(rn, "_pause", None)
            stop_ev = getattr(rn, "_stop", None)
            out.append({
                "site_id": sid,
                "state": getattr(rn, "_state", "?"),
                "urls": len(getattr(rn, "urls", []) or []),
                "jobs": len(getattr(rn, "jobs", {}) or {}),
                "worker_threads": len(
                    getattr(rn, "_worker_threads", []) or []),
                # _pause is set() when the runner is allowed to run
                "paused": (not pause_ev.is_set()) if pause_ev else None,
                "stopping": stop_ev.is_set() if stop_ev else None,
                "rate_limited_until": getattr(rn, "_rl_until", 0.0),
            })
        except Exception as e:
            out.append({"site_id": sid, "error": str(e)[:120]})
    return {"runner_count": len(out), "runners": out}



# ── 5. log tail ────────────────────────────────────────────────────

def log_tail(n=200) -> dict:
    """Last n lines of logs/bulk_downloader.log. Reads from the file
    end so a large rotated log is not loaded whole."""
    try:
        n = max(1, min(int(n), 5000))
    except Exception:
        n = 200
    log_path = Path("logs") / "bulk_downloader.log"
    if not log_path.exists():
        return {"path": str(log_path), "exists": False, "lines": []}
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                step = min(8192, size)
                size -= step
                fh.seek(size)
                data = fh.read(step) + data
        lines = data.decode("utf-8", "replace").splitlines()[-n:]
        return {"path": str(log_path), "exists": True,
                "line_count": len(lines), "lines": lines}
    except Exception as e:
        return {"path": str(log_path), "error": str(e)[:200]}



def process_info() -> dict:
    """PID, real uptime, open file-descriptor count, RSS, thread
    count — a quick liveness fingerprint of the running process."""
    info = {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "cwd": os.getcwd(),
        "uptime_seconds": _proc_uptime_seconds(),
        "thread_count": threading.active_count(),
    }
    try:
        info["open_fds"] = len(os.listdir("/proc/self/fd"))
    except Exception:
        info["open_fds"] = None
    try:
        from bulk_downloader import perf_lab as _pl
        rss = _pl._rss_bytes()
        info["rss_mb"] = round(rss / (1024 * 1024), 1) if rss else None
    except Exception:
        info["rss_mb"] = None
    return info



# ════════════════ validation / self-check tools ═══════════════════
# Read-only checks. Each returns per-item results plus a verdict; none
# raise on a failed check — a failed check is data, not an exception.

# ── 9. invariant self-audit ────────────────────────────────────────

def invariant_audit() -> dict:
    """Runtime checks of the DANGER_MAP invariants that are checkable
    without a running download — WAL mode, the session-keeper
    pause/no-resume pair, the two resolution tables, the vault
    resolver, the central dispatch method."""
    checks = []

    def _c(name, ok, detail):
        checks.append({"check": name,
                       "status": "ok" if ok else "fail",
                       "detail": detail})

    try:                                            # INV-004
        from bulk_downloader import db as _db
        with _db.db_conn() as cx:
            mode = cx.execute("PRAGMA journal_mode").fetchone()[0]
        _c("db_wal_mode", mode == "wal", f"journal_mode={mode}")
    except Exception as e:
        _c("db_wal_mode", False, f"could not read: {e}"[:120])

    try:                                            # INV-001
        from bulk_downloader import session_keeper as _sk
        _c("keeper_pause_present", hasattr(_sk, "pause_site_keepers"),
           "session_keeper.pause_site_keepers")
        resume = [n for n in ("resume_site_keepers", "resume_keeper",
                              "resume_all") if hasattr(_sk, n)]
        _c("keeper_no_resume", not resume,
           "no resume_* (correct)" if not resume
           else f"FORBIDDEN resume function present: {resume}")
    except Exception as e:
        _c("keeper_invariants", False,
           f"session_keeper import failed: {e}"[:120])

    try:                                            # INV-005
        from bulk_downloader import heuristic_scoring as _hs, detect as _dt
        ok = hasattr(_hs, "RESOLUTION_TIERS") and hasattr(_dt, "res_label")
        _c("resolution_tables_present", ok,
           "heuristic_scoring.RESOLUTION_TIERS + detect.res_label")
    except Exception as e:
        _c("resolution_tables_present", False, f"import failed: {e}"[:120])

    try:                                            # INV-006
        from bulk_downloader import secrets_store as _ss
        _c("vault_resolver_present", hasattr(_ss, "resolve_password"),
           "secrets_store.resolve_password")
    except Exception as e:
        _c("vault_resolver_present", False, f"import failed: {e}"[:120])

    try:                                            # central dispatch
        from bulk_downloader.runner import SiteRunner
        _c("dispatch_method_present",
           hasattr(SiteRunner, "_process_one"),
           "runner.SiteRunner._process_one")
    except Exception as e:
        _c("dispatch_method_present", False, f"import failed: {e}"[:120])

    n_fail = sum(1 for c in checks if c["status"] == "fail")
    return {
        "checks": checks,
        "passed": len(checks) - n_fail,
        "failed": n_fail,
        "verdict": "all invariants hold" if not n_fail
                   else f"{n_fail} invariant check(s) FAILED",
    }



# ── 10. template audit ─────────────────────────────────────────────

def template_audit() -> dict:
    """Structural validation of every registered login template:
    non-empty user/pass/submit selectors, distinct primary user vs
    pass selectors, no selector shared between the two lists."""
    try:
        from bulk_downloader.login_templates_data import LOGIN_TEMPLATES
    except Exception as e:
        return {"error": f"could not load templates: {e}"[:160]}
    results = []
    for t in LOGIN_TEMPLATES:
        issues = []
        login = t.get("login") or {}
        uf = login.get("user_field") or []
        pf = login.get("pass_field") or []
        sb = login.get("submit_btn") or []
        if not uf:
            issues.append("no user_field selectors")
        if not pf:
            issues.append("no pass_field selectors")
        if not sb:
            issues.append("no submit_btn selectors")
        if uf and pf and uf[0] == pf[0]:
            issues.append(f"user/pass share primary selector {uf[0]!r}")
        shared = set(uf) & set(pf)
        if shared:
            issues.append(
                f"selector(s) in both user and pass: {sorted(shared)}")
        results.append({"id": t.get("id"), "host": t.get("host"),
                        "ok": not issues, "issues": issues})
    bad = [r for r in results if not r["ok"]]
    return {
        "template_count": len(results),
        "clean": len(results) - len(bad),
        "with_issues": len(bad),
        "templates": results,
        "verdict": "all templates structurally valid" if not bad
                   else f"{len(bad)} template(s) have issues",
    }



# ════════════════ maintenance actions (state-changing) ════════════
# Unlike everything above, these CHANGE state. The route layer gates
# them behind dev-mode AND CSRF. Each is safe and reversible: a GC is
# always safe, a checkpoint only flushes the WAL, the log level can be
# set back, and the SQL console refuses anything but a single SELECT.

# ── 13. force garbage collection ───────────────────────────────────

def force_gc() -> dict:
    """Run a full gc.collect() and report what it freed. Safe anytime
    — this only reclaims already-unreachable objects."""
    import gc
    before = len(gc.get_objects())
    collected = gc.collect()
    after = len(gc.get_objects())
    return {
        "ok": True,
        "unreachable_collected": collected,
        "objects_before": before,
        "objects_after": after,
        "objects_freed": before - after,
        "gc_garbage": len(gc.garbage),
    }
