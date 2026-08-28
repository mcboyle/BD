"""Health checklist (Phase 122, Block Q).

One-call "is BD healthy?" diagnostic. Each check returns
{name, status, message, severity} and the whole report rolls up to a
single overall status (ok / warn / fail).

Used by:
  • /api/health/checklist — operator dashboard panel
  • bdctl healthcheck — exit nonzero if anything's failing
  • External monitoring (Prometheus alerts off /metrics; this is the
    human-readable companion)

Checks run cheap — each <50ms typically. The expensive integrity
work lives in /api/bitrot/scan and /api/provenance/verify (manually
invoked).
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Optional


# Severity ranking. fail > warn > ok. Determines overall rollup.
SEV_OK = 0
SEV_WARN = 1
SEV_FAIL = 2

_SEV_LABEL = {SEV_OK: "ok", SEV_WARN: "warn", SEV_FAIL: "fail"}


def _check(name: str, fn) -> dict:
    """Run one check function with timing + error capture."""
    started = time.time()
    try:
        result = fn() or {}
    except Exception as e:
        return {
            "name": name,
            "status": "fail",
            "severity": SEV_FAIL,
            "message": f"check raised: {e}",
            "duration_ms": round((time.time() - started) * 1000, 1),
        }
    sev = result.get("severity", SEV_OK)
    return {
        "name": name,
        "status": _SEV_LABEL.get(sev, "ok"),
        "severity": sev,
        "message": result.get("message", ""),
        "details": result.get("details"),
        "duration_ms": round((time.time() - started) * 1000, 1),
    }


# ─── Individual checks ────────────────────────────────────────────────

def _check_database() -> dict:
    """SQLite reachable, WAL mode enabled, integrity ok."""
    from . import db as _db
    with _db.db_conn() as cx:
        row = cx.execute("PRAGMA journal_mode").fetchone()
        jm = (row[0] if row else "").lower()
        ic = cx.execute("PRAGMA quick_check").fetchone()
        ic_result = ic[0] if ic else "?"
    if jm != "wal":
        return {"severity": SEV_WARN,
                "message": f"journal_mode is '{jm}', expected 'wal' "
                           f"— concurrent writers may see locks",
                "details": {"journal_mode": jm}}
    if ic_result != "ok":
        return {"severity": SEV_FAIL,
                "message": f"integrity check returned '{ic_result}'"}
    return {"severity": SEV_OK,
            "message": f"WAL mode active, integrity {ic_result}"}


def _check_disk(s_cfg: Optional[dict]) -> dict:
    """All configured download_dirs, PLUS the capture-store / install root,
    exist + have headroom. The capture store often lives on a different
    path/volume than downloads and a full store is a distinct 'disk full' class
    the download-dir check misses; it is checked even with no sites (first boot)."""
    issues = []
    paths_checked = 0
    seen = set()
    # One enumerator, shared with the bit-rot scan (v3.66.930). It returns the
    # RAW configured paths precisely so this check can still report a missing
    # directory as an issue -- filtering for existence there would delete the
    # thing this function exists to find. The _by_site shape keeps the owner,
    # so the issue text still names which site the bad path belongs to.
    from .library_final import download_roots_by_site
    dirs = list(download_roots_by_site(s_cfg))
    # capture-store / install root (bulk_downloader/.. == PROJECT_ROOT)
    dirs.append(("capture_store",
                 os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    for sid, d in dirs:
        if not d or d in seen:
            continue
        seen.add(d)
        if not os.path.isdir(d):
            issues.append(f"{sid}: dir {d!r} does not exist")
            continue
        paths_checked += 1
        try:
            free_bytes = shutil.disk_usage(d).free
            free_gb = free_bytes / (1024**3)
            if free_gb < 1:
                issues.append(f"{sid}: only {free_gb:.1f} GB free at {d}")
            elif free_gb < 10:
                issues.append(f"{sid}: {free_gb:.1f} GB free at {d} (low)")
        except OSError as e:
            issues.append(f"{sid}: cannot stat {d}: {e}")
    if not issues:
        return {"severity": SEV_OK,
                "message": f"{paths_checked} path(s) ok (incl. capture store)"}
    severity = SEV_FAIL if any("does not exist" in i or "only" in i
                               for i in issues) else SEV_WARN
    return {"severity": severity,
            "message": "; ".join(issues[:3]) +
                       (f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""),
            "details": {"issues": issues}}


def _check_playwright() -> dict:
    """Playwright installed + chromium installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"severity": SEV_FAIL,
                "message": "playwright not installed: pip install playwright"}
    # Check chromium binary exists. The cheapest non-invasive probe
    # is asking for the executable path via the sync API.
    try:
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
            if not path or not os.path.exists(path):
                return {"severity": SEV_FAIL,
                        "message": "chromium not installed: "
                                   "playwright install chromium"}
    except Exception as e:
        return {"severity": SEV_WARN,
                "message": f"could not probe chromium: {e}"}
    return {"severity": SEV_OK, "message": "playwright + chromium ready"}


def _check_ytdlp() -> dict:
    """yt-dlp freshness, by VERSION COMPARISON rather than by age.

    @977. The predicate was ``age_days > 30``, and age cannot tell "you are
    behind" from "upstream has been quiet". MEASURED on the box 2026-08-09: the
    selftest said *"yt-dlp is 36 days old -- consider updating"* while the
    installed 2026.7.4 was the NEWEST release on the index (uploaded
    2026-07-04). The operator ran the update; it was necessarily a no-op. A
    check whose recommended action cannot clear it is section 0's
    over-sensitivity failure, and this one had been firing in every capture.

    Three outcomes, and the middle one is the reason this is not a one-line
    threshold bump:
      * behind  -> WARN, naming the version available so the operator knows
                   what updating would get them;
      * current -> OK, REGARDLESS of age. Being old is not a defect when there
                   is nothing newer;
      * unknown -> WARN, saying it could not check. It must NOT say "consider
                   updating", which would assert staleness never measured.

    On the third state: `selftest` has only ok/warn/fail, so "could not check"
    shares a status with "behind" and the distinction lives in the MESSAGE. That
    is weaker than section 0 asks for. Adding a fourth status would change the
    boot summary, the 07b_selftest.json artifact and every consumer of
    ok/warn/fail -- a wider blast radius than this fix should carry, and left
    deliberately rather than overlooked.
    """
    try:
        from . import ytdlp_updater as _yt
        info = _yt.status_dict() or {}
        ver = info.get("version")
        age = info.get("age_days")
        if not info.get("installed") or not ver:
            return {"severity": SEV_WARN,
                    "message": "yt-dlp version unknown"}
        latest = _yt.latest_version()
        if not latest:
            return {"severity": SEV_WARN,
                    "message": f"yt-dlp {ver}: could not reach the index, so "
                               f"whether a newer release exists is UNKNOWN"}
        if _yt.is_behind(ver, latest):
            return {"severity": SEV_WARN,
                    "message": f"yt-dlp {ver} is behind {latest} "
                               f"— update available"}
        agetxt = f", {age:.0f}d old" if age is not None else ""
        return {"severity": SEV_OK,
                "message": f"yt-dlp {ver} is current{agetxt}"}
    except Exception as e:
        return {"severity": SEV_WARN,
                "message": f"yt-dlp status unknown: {type(e).__name__}"}


def _ffmpeg_capability(ff: str) -> dict:
    """Probe an ffmpeg binary beyond mere presence: does it run, and does it
    support the mpegts muxer + https protocol BD needs for HLS/TS remuxing?
    Returns ``{"error": str}`` if it won't run, a capability status of
    ``unknown`` if either capability listing cannot be measured, or a measured
    ``{"missing": [caps]}`` result.  (Presence alone isn't enough -- a build
    that crashes on invocation or lacks mpegts/https still fails real
    downloads.)"""
    import subprocess
    try:
        r = subprocess.run([ff, "-hide_banner", "-version"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if r.returncode != 0:
        return {"error": f"-version exited {r.returncode}"}
    listings = {}
    for option in ("-muxers", "-protocols"):
        try:
            probe = subprocess.run(
                [ff, "-hide_banner", option],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return {
                "available": False,
                "capability_status": "unknown",
                "error": f"{option} unavailable: {type(e).__name__}: {e}",
            }
        if probe.returncode != 0:
            return {
                "available": False,
                "capability_status": "unknown",
                "error": f"{option} exited {probe.returncode}",
            }
        listings[option] = probe.stdout
    missing = []
    if "mpegts" not in listings["-muxers"]:
        missing.append("mpegts")
    if "https" not in listings["-protocols"]:
        missing.append("https")
    return {"available": True, "capability_status": "measured",
            "missing": missing}


def _check_ffmpeg() -> dict:
    """ffmpeg + ffprobe on PATH, and actually capable of the HLS/TS-over-https
    remuxing BD does. Presence alone isn't enough: a build that crashes on
    invocation or lacks the mpegts muxer / https protocol still fails real
    downloads (the static-ffmpeg HLS+https segfault class)."""
    from . import ffmpeg_bin          # MOD-4: probe the binary BD WILL RUN --
    ff = ffmpeg_bin.ffmpeg()          # probing PATH while the runner used a
    fp = ffmpeg_bin.ffprobe()         # pinned build would report the wrong one
    if not ff and not fp:
        return {"severity": SEV_WARN,
                "message": "ffmpeg/ffprobe not on PATH "
                           "— enrichment + thumbnails disabled"}
    if not ff or not fp:
        return {"severity": SEV_WARN,
                "message": f"partial: ffmpeg={'ok' if ff else 'missing'}, "
                           f"ffprobe={'ok' if fp else 'missing'}"}
    cap = _ffmpeg_capability(ff)
    if cap.get("capability_status") == "unknown":
        return {"severity": SEV_WARN,
                "message": "ffmpeg capability measurement unknown: "
                           f"{cap.get('error', 'capability lists unavailable')}"}
    if cap.get("error"):
        return {"severity": SEV_FAIL,
                "message": f"ffmpeg present but not usable: {cap['error']}"}
    missing = cap.get("missing") or []
    if missing:
        return {"severity": SEV_WARN,
                "message": f"ffmpeg missing {', '.join(missing)} support "
                           "— HLS/TS downloads may fail"}
    return {"severity": SEV_OK,
            "message": "ffmpeg + ffprobe ready (mpegts + https ok)"}


def _check_recent_failures() -> dict:
    """Last hour failure rate. Operator-visible signal."""
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            row = cx.execute("""SELECT
                  SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
                  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed
                FROM history
                WHERE ts >= datetime('now', '-1 hour')""").fetchone()
        d = int(row[0] or 0) if row else 0
        f = int(row[1] or 0) if row else 0
    except Exception as e:
        return {"severity": SEV_WARN,
                "message": f"could not check recent failures: {e}"}
    if d + f == 0:
        return {"severity": SEV_OK, "message": "no activity in last hour"}
    fail_pct = 100 * f / max(1, d + f)
    if fail_pct >= 50 and f >= 5:
        return {"severity": SEV_FAIL,
                "message": f"{fail_pct:.0f}% failure rate "
                           f"({f} failed / {d} done in last hour)"}
    if fail_pct >= 25 and f >= 3:
        return {"severity": SEV_WARN,
                "message": f"{fail_pct:.0f}% failure rate "
                           f"({f} failed / {d} done in last hour)"}
    return {"severity": SEV_OK,
            "message": f"{d} done, {f} failed last hour"}


def _check_circuit_breakers() -> dict:
    """Any hosts in open state?"""
    try:
        from . import circuit_breaker as _cb
        rep = _cb.report() or {}
    except Exception as e:
        return {"severity": SEV_WARN,
                "message": f"circuit breaker measurement unknown: "
                           f"{type(e).__name__}: {e}"[:200]}
    open_hosts = [h for h, s in rep.items() if s.get("state") == "open"]
    if not open_hosts:
        return {"severity": SEV_OK,
                "message": f"{len(rep)} host(s) tracked, none tripped"}
    return {"severity": SEV_WARN,
            "message": f"{len(open_hosts)} circuit(s) open: " +
                       ", ".join(open_hosts[:3]),
            "details": {"open_hosts": open_hosts}}


def _check_account_health(s_cfg: Optional[dict]) -> dict:
    """Any accounts scoring low?"""
    try:
        from . import account_health as _ah
        rows = _ah.report_all() or []
    except Exception as e:
        return {"severity": SEV_WARN,
                "message": f"account health measurement unknown: "
                           f"{type(e).__name__}: {e}"[:200]}
    low = [r for r in rows if r.get("score", 100) < 50]
    if not low:
        return {"severity": SEV_OK,
                "message": f"{len(rows)} account(s) tracked, all healthy"}
    return {"severity": SEV_WARN,
            "message": f"{len(low)} account(s) below 50: " +
                       ", ".join(f"{r['site_id']}/{r.get('account_id', '?')}"
                                 for r in low[:3])}


def _check_bitrot() -> dict:
    """Any open bit-rot issues?"""
    try:
        from . import bitrot as _br
        s = _br.stats()
    except Exception as e:
        return {"severity": SEV_WARN,
                "message": f"bitrot measurement unavailable: {e}"[:200]}
    if not s.get("available", True) or s.get("open_issues") is None:
        return {"severity": SEV_WARN,
                "message": "bitrot measurement unavailable",
                "details": s}
    n = s.get("open_issues")
    if n == 0:
        return {"severity": SEV_OK, "message": "no integrity issues"}
    sev = SEV_FAIL if n >= 10 else SEV_WARN
    return {"severity": sev,
            "message": f"{n} open integrity issue(s)",
            "details": s}


# ─── Public entry ─────────────────────────────────────────────────────

def _check_supervisor() -> dict:
    """Bandwidth supervisor state, surfaced in app health (supervisor -> health).
    App-PROCESS supervision is systemd's job (auto-restart) and /api/health
    reports uptime; this connects the in-app bandwidth limiter to the health
    view so 'supervisor' is visible alongside the rest."""
    try:
        from . import download_supervisor as _sup
        enabled = bool(_sup.is_enabled())
    except Exception as e:
        return {"severity": SEV_OK,
                "message": f"bandwidth supervisor unavailable: {e}"}
    if not enabled:
        return {"severity": SEV_OK,
                "message": "bandwidth supervisor idle (no throttle configured)"}
    try:
        cfg = (_sup.stats() or {}).get("config", {})
        gbps = cfg.get("global_bps", 0)
    except Exception:
        gbps = 0
    return {"severity": SEV_OK,
            "message": f"bandwidth supervisor active (global_bps={gbps})"}


def run_checklist(s_cfg: Optional[dict] = None) -> dict:
    """Run all checks and roll up to an overall status.

    Returns:
      {
        overall_status: 'ok' | 'warn' | 'fail',
        check_count: N,
        summary: {ok: N, warn: N, fail: N},
        checks: [{name, status, severity, message, duration_ms}, ...],
        generated_at: ts
      }"""
    results = [
        _check("database", _check_database),
        _check("disk", lambda: _check_disk(s_cfg)),
        _check("playwright", _check_playwright),
        _check("ytdlp", _check_ytdlp),
        _check("ffmpeg", _check_ffmpeg),
        _check("recent_failures", _check_recent_failures),
        _check("circuit_breakers", _check_circuit_breakers),
        _check("account_health", lambda: _check_account_health(s_cfg)),
        _check("bitrot", _check_bitrot),
        _check("supervisor", _check_supervisor),
    ]
    summary = {"ok": 0, "warn": 0, "fail": 0}
    worst = SEV_OK
    for r in results:
        summary[r["status"]] += 1
        worst = max(worst, r["severity"])
    return {
        "overall_status": _SEV_LABEL[worst],
        "check_count": len(results),
        "summary": summary,
        "checks": results,
        "generated_at": time.time(),
    }
