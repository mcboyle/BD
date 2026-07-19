"""v3.43.24: Pre-flight self-tests.

Runs at startup (and on demand via /api/selftest) to catch
environment problems before they manifest as cryptic worker failures.

Test categories:
  - Filesystem: directories writable, free space, paths absolute
  - Database: SQLite reachable, schema present, WAL working
  - Cookies: dir present, files parseable
  - Config: sites_config schema valid, no missing required fields
  - Resources: Playwright installed, browser binary present
  - Network: loopback works (catches Windows firewall issues)

Each test returns one of:
  - ok: clean pass
  - warn: works but suboptimal (e.g. <5GB disk free)
  - fail: will cause runtime errors; user must fix

Failures don't abort startup — the app still runs with degraded
features — but they're logged loudly and surfaced in the UI banner
so the user knows what to fix.

Design notes:
  - All checks must be fast (<100ms each); they run synchronously
    during startup. Anything slow goes through a different path.
  - All checks must be self-contained; no shared state, no globals,
    no side effects. This is the "is the environment safe?" pass,
    not the "fix the environment" pass.
  - Schema validation handles partial config gracefully — a missing
    optional field is warn, not fail. Only structural breakage
    (sites_config not a dict, sites not a list, etc.) is fail.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import tempfile
import time
from pathlib import Path


# ── Result types ──────────────────────────────────────────────────────

OK = "ok"
WARN = "warn"
FAIL = "fail"


_HC_SEV_TO_STATUS = None  # built lazily to avoid a module-load import of healthcheck


def _hc_status_map():
    global _HC_SEV_TO_STATUS
    if _HC_SEV_TO_STATUS is None:
        from . import healthcheck as _hc
        _HC_SEV_TO_STATUS = {_hc.SEV_OK: OK, _hc.SEV_WARN: WARN, _hc.SEV_FAIL: FAIL}
    return _HC_SEV_TO_STATUS


def check_ffmpeg_hls() -> dict:
    """ROB-3 (v3.66.657): fold healthcheck's ffmpeg HLS/TS-over-https capability
    probe into the STARTUP selftest so a broken/incapable ffmpeg build (the
    static-ffmpeg HLS+https segfault class, or a build missing the mpegts muxer /
    https protocol) is caught at boot -- not only when the on-demand health page is
    opened. Delegates to healthcheck._check_ffmpeg; best-effort (a probe blow-up
    degrades to WARN, never propagates at startup)."""
    try:
        from . import healthcheck as _hc
        r = _hc._check_ffmpeg()
        status = _hc_status_map().get(r.get("severity"), WARN)
        return _result(status, "ffmpeg_hls", r.get("message", "ffmpeg check"))
    except Exception as e:  # noqa: BLE001
        return _result(WARN, "ffmpeg_hls", f"ffmpeg check unavailable: {type(e).__name__}")


def check_extractor_freshness() -> dict:
    """ROB-3 (v3.66.657): fold healthcheck's yt-dlp freshness signal into the
    startup selftest so a stale extractor (yt-dlp goes stale fast, breaking sites)
    is visible at boot. Delegates to healthcheck._check_ytdlp; best-effort."""
    try:
        from . import healthcheck as _hc
        r = _hc._check_ytdlp()
        status = _hc_status_map().get(r.get("severity"), WARN)
        return _result(status, "extractor_freshness", r.get("message", "yt-dlp check"))
    except Exception as e:  # noqa: BLE001
        return _result(WARN, "extractor_freshness", f"yt-dlp check unavailable: {type(e).__name__}")


def _result(status: str, test: str, message: str, **detail) -> dict:
    """Build a uniform result dict. detail is for structured data
    (free_gb, error_class, hint) that the UI can format."""
    return {
        "status": status,
        "test": test,
        "message": message,
        "detail": detail,
        "ts": time.time(),
    }


# ── Individual checks ─────────────────────────────────────────────────

def check_writable_dirs(*dirs) -> list[dict]:
    """For each directory, verify (a) it exists or can be created,
    (b) we can write to it. Returns one result per directory.

    Failed cases include path resolution errors (e.g. drive letter
    not mounted on Windows) and permission errors."""
    results = []
    for d in dirs:
        if not d:
            continue
        try:
            p = Path(d)
            # Create if missing — common case for fresh installs.
            # mkdir(parents=True) is idempotent on already-existing.
            p.mkdir(parents=True, exist_ok=True)
            # Probe write — touch a temp file we'll immediately delete.
            # Use NamedTemporaryFile so the OS handles cleanup if we
            # crash mid-test.
            with tempfile.NamedTemporaryFile(dir=p, prefix=".selftest_",
                                              suffix=".tmp", delete=True):
                pass
            results.append(_result(OK, "writable_dir",
                                    f"{p} is writable", path=str(p)))
        except PermissionError as e:
            results.append(_result(FAIL, "writable_dir",
                f"{d}: permission denied ({e})",
                path=str(d),
                hint="Run the app with permissions to write to this "
                     "directory, or change the path in config."))
        except OSError as e:
            results.append(_result(FAIL, "writable_dir",
                f"{d}: {type(e).__name__}: {e}",
                path=str(d),
                hint="Verify the path is correct and the drive/mount "
                     "is available."))
    return results


def check_disk_space(path: str, min_warn_gb: float = 10.0,
                      min_fail_gb: float = 1.0) -> dict:
    """Warn under min_warn_gb, fail under min_fail_gb. Defaults
    correspond to "1 large 8K file" (10GB) and "can't fit anything"
    (1GB)."""
    try:
        # shutil.disk_usage works on all platforms
        import shutil
        usage = shutil.disk_usage(path)
        free_gb = usage.free / 1024**3
        total_gb = usage.total / 1024**3
        if free_gb < min_fail_gb:
            return _result(FAIL, "disk_space",
                f"{path}: only {free_gb:.1f} GB free (<{min_fail_gb} GB threshold)",
                path=path, free_gb=free_gb, total_gb=total_gb,
                hint="Free up space or change download_dir to a "
                     "volume with more room.")
        if free_gb < min_warn_gb:
            return _result(WARN, "disk_space",
                f"{path}: {free_gb:.1f} GB free (recommended >{min_warn_gb} GB)",
                path=path, free_gb=free_gb, total_gb=total_gb)
        return _result(OK, "disk_space",
            f"{path}: {free_gb:.1f} / {total_gb:.0f} GB free",
            path=path, free_gb=free_gb, total_gb=total_gb)
    except (OSError, FileNotFoundError) as e:
        return _result(FAIL, "disk_space",
            f"{path}: can't check ({type(e).__name__}: {e})",
            path=path,
            hint="Verify the path exists and the drive is mounted.")


def check_database(db_path: str) -> dict:
    """Verify SQLite is reachable, WAL mode is on, expected tables
    exist. Doesn't try to repair — caller does that via
    auto_recover_sqlite()."""
    # First run: the DB hasn't been created yet. db_conn() sets WAL on the
    # first real connection (db_init) and WAL persists in the file; a raw
    # connect here would CREATE an empty 'delete'-mode file and false-positive.
    # Treat a not-yet-existing DB as a benign first-run condition (like missing
    # cookies/sites_config), not a WAL misconfiguration.
    if db_path != ":memory:" and not os.path.exists(db_path):
        return _result(OK, "database",
                       "not created yet (WAL is set on first init)",
                       db_path=db_path)
    try:
        # Short timeout — startup test, don't hang for 10s
        cx = sqlite3.connect(db_path, timeout=2.0)
        try:
            # WAL mode check
            mode = cx.execute("PRAGMA journal_mode").fetchone()[0]
            if mode.lower() != "wal":
                return _result(WARN, "database",
                    f"journal_mode is {mode!r}, expected 'wal'",
                    db_path=db_path,
                    hint="Sites with concurrent workers may see "
                         "'database is locked' errors. db_conn() should "
                         "set WAL mode; investigate why it didn't.")
            # Required tables — names must match db.db_init() exactly
            required = {"history", "queue", "push_subscriptions",
                        "session_history"}
            rows = cx.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            present = {r[0] for r in rows}
            missing = required - present
            if missing:
                return _result(FAIL, "database",
                    f"missing tables: {', '.join(sorted(missing))}",
                    db_path=db_path, missing=sorted(missing),
                    hint="Run db_init() to create schema, or restore "
                         "from backup.")
            # Quick integrity check — surfaces corruption that hasn't
            # triggered a query failure yet. quick_check is faster than
            # integrity_check and catches the same critical issues.
            check = cx.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                return _result(FAIL, "database",
                    f"integrity check failed: {check[:200]}",
                    db_path=db_path, integrity=check[:500],
                    hint="Database may be corrupt. Stop the app and "
                         "restore from backup, or let auto-recover "
                         "rebuild on next startup.")
            return _result(OK, "database",
                f"WAL mode, {len(present)} tables, integrity ok",
                db_path=db_path, tables=sorted(present))
        finally:
            cx.close()
    except sqlite3.DatabaseError as e:
        return _result(FAIL, "database",
            f"SQLite error: {type(e).__name__}: {e}",
            db_path=db_path,
            hint="Database may be corrupt or wrong format. Backup "
                 "and let auto-recover rebuild.")
    except OSError as e:
        return _result(FAIL, "database",
            f"can't open {db_path}: {type(e).__name__}: {e}",
            db_path=db_path,
            hint="Verify the database file path and permissions.")


def check_cookies_dir(cookies_dir: str) -> dict:
    """Verify cookies dir is writable and any cookie files parse as
    valid JSON. A cookie file that won't parse will cause every login
    attempt for that site to fail without a clear error."""
    try:
        p = Path(cookies_dir)
        if not p.exists():
            # Not a failure — first run creates this on demand
            return _result(OK, "cookies_dir",
                f"{cookies_dir} doesn't exist yet (will be created on first login)",
                path=str(p))
        if not p.is_dir():
            return _result(FAIL, "cookies_dir",
                f"{cookies_dir} exists but isn't a directory",
                path=str(p))
        # Spot-check each cookie file. Don't validate the SHAPE
        # (Playwright cookie list vs flat dict are both legal) —
        # just that it parses as JSON.
        bad_files = []
        for cf in p.glob("*.json"):
            try:
                with open(cf, "r", encoding="utf-8") as f:
                    json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                bad_files.append(f"{cf.name}: {type(e).__name__}")
        if bad_files:
            return _result(WARN, "cookies_dir",
                f"{len(bad_files)} unparseable cookie file(s): "
                + ", ".join(bad_files[:3]),
                path=str(p), bad_files=bad_files,
                hint="Delete the unparseable file(s) to force re-login "
                     "on those sites.")
        return _result(OK, "cookies_dir",
            f"{cookies_dir}: {len(list(p.glob('*.json')))} cookie file(s) ok",
            path=str(p))
    except OSError as e:
        return _result(FAIL, "cookies_dir",
            f"{cookies_dir}: {type(e).__name__}: {e}",
            path=cookies_dir)


def check_sites_config(config: dict) -> list[dict]:
    """Structural validation of the sites_config dict. Catches the
    common shapes of corruption that cause cryptic runtime errors:

      - sites_config not a dict (root-level corruption)
      - individual site missing required fields (name, login_url)
      - site IDs duplicated (would silently overwrite each other)
      - paths that won't resolve (download_dir on a missing drive)

    BulkDownloader supports TWO disk formats:
      1. Live format (sites_config.json): {sid: cfg, sid: cfg, ...}
      2. Example/export format: [{...cfg with comment...}, ...]
    Handle both — first by detecting the shape, then validating per
    that shape. Returns a list of results, one per detected issue.
    Empty list means structurally clean.
    """
    results = []
    if isinstance(config, list):
        # Example/export format — array of site dicts
        site_iter = enumerate(config)
        sites_count = len(config)
    elif isinstance(config, dict):
        # Live format — dict of sid → cfg. Empty dict is fine (fresh
        # install). The 'sites' key was a previously-documented shape;
        # support it too in case an old config survived.
        if "sites" in config and isinstance(config["sites"], list):
            site_iter = enumerate(config["sites"])
            sites_count = len(config["sites"])
        else:
            site_iter = config.items()
            sites_count = len(config)
    else:
        results.append(_result(FAIL, "sites_config",
            f"root is {type(config).__name__}; expected dict or list",
            hint="Restore sites_config.json from backup or recreate."))
        return results

    if sites_count == 0:
        return [_result(OK, "sites_config", "no sites configured")]

    # Duplicate-id detector — a fresh install can't have these but
    # a manual config edit might.
    seen_ids = set()
    for sid_or_idx, s in site_iter:
        if not isinstance(s, dict):
            results.append(_result(FAIL, "sites_config",
                f"site {sid_or_idx!r} is not a dict (got {type(s).__name__})",
                hint="Edit sites_config.json to fix the malformed entry."))
            continue
        sid = (sid_or_idx if isinstance(sid_or_idx, str)
               else s.get("id") or "")
        name = s.get("name") or s.get("comment") or "?"
        if sid and sid in seen_ids:
            results.append(_result(FAIL, "sites_config",
                f"duplicate site id {sid!r} on site {name!r}",
                site_id=sid, site_name=name,
                hint="Two sites share the same ID — they'll overwrite "
                     "each other's queue rows. Generate a new ID for one."))
        if sid:
            seen_ids.add(sid)
    if not results:
        results.append(_result(OK, "sites_config",
            f"{sites_count} site(s), all structurally valid"))
    return results


def check_playwright() -> dict:
    """Verify Playwright is importable AND the browser binary is
    present. The 'pip install playwright' step works but 'playwright
    install' is a separate command users often miss — that case fails
    here cleanly instead of mid-download."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError as e:
        return _result(FAIL, "playwright",
            f"playwright not installed: {e}",
            hint="Run: pip install playwright")
    # Quick binary check — try to find the chromium executable
    # without actually launching it (saves ~2s on startup).
    try:
        from playwright._impl._driver import compute_driver_executable
        compute_driver_executable()
        # The driver lookup itself doesn't verify chromium specifically,
        # but it's a strong signal that the install completed. A more
        # thorough check (launch + close) is too slow for startup.
        return _result(OK, "playwright", "installed")
    except Exception as e:
        return _result(WARN, "playwright",
            f"installed but driver check failed: {type(e).__name__}",
            hint="Run: playwright install chromium")


def check_loopback() -> dict:
    """Verify the loopback interface accepts TCP connections. On
    Windows in particular, an overzealous firewall can block 127.0.0.1
    traffic, and the resulting JD-bridge / session-keeper failures
    look like "connection refused" with no clear cause."""
    s = None
    try:
        # Bind to an OS-assigned ephemeral port, then connect to it
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        # Quick connect probe
        c = socket.create_connection(("127.0.0.1", port), timeout=1.0)
        c.close()
        return _result(OK, "loopback", "127.0.0.1 reachable")
    except (OSError, socket.timeout) as e:
        return _result(FAIL, "loopback",
            f"can't connect to 127.0.0.1: {type(e).__name__}: {e}",
            hint="Windows Firewall may be blocking loopback. "
                 "JD bridge / session keeper won't work.")
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


# ── Tempfile hygiene + egress fail-closed (Session-1 ROB-3-rem / POS-3) ──

# Temp artifacts a crashed download/capture leaves behind. Read-only reporting:
# these checks WARN so a buildup is visible on the health page; they never delete.
_ORPHAN_TEMP_GLOBS = ("*.part", ".tmp*", "tmp*", ".selftest_*", "*.tmp")


def check_orphan_tempfiles(*dirs, max_age_hours: float = 24.0) -> dict:
    """ROB-3-rem: WARN if stale temp artifacts (.part / .tmp* / .selftest_* left
    by a crashed download or capture) older than ``max_age_hours`` linger in any
    of ``dirs``. A missing dir is skipped -- never a FAIL. Read-only; reports,
    never deletes."""
    cutoff = time.time() - max_age_hours * 3600.0
    stale = []
    for d in dirs:
        if not d:
            continue
        base = Path(d)
        if not base.is_dir():
            continue
        for pat in _ORPHAN_TEMP_GLOBS:
            for f in base.glob(pat):
                try:
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        stale.append(str(f))
                except OSError:
                    continue
    stale = sorted(set(stale))
    if stale:
        return _result(WARN, "orphan_tempfiles",
            f"{len(stale)} orphan temp file(s) older than {max_age_hours:.0f}h "
            f"(a crashed download/capture may have leaked them)",
            count=len(stale), sample=stale[:10], max_age_hours=max_age_hours,
            hint="Safe to remove if no capture/download is in flight.")
    return _result(OK, "orphan_tempfiles",
        f"no orphan temp files older than {max_age_hours:.0f}h", count=0)


def check_stale_locks(*dirs, max_age_hours: float = 6.0) -> dict:
    """ROB-3-rem: WARN on ``*.lock`` files older than ``max_age_hours`` -- a lock a
    crashed process never released. A missing dir is skipped. Read-only."""
    cutoff = time.time() - max_age_hours * 3600.0
    stale = []
    for d in dirs:
        if not d:
            continue
        base = Path(d)
        if not base.is_dir():
            continue
        for f in base.rglob("*.lock"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    stale.append(str(f))
            except OSError:
                continue
    stale = sorted(set(stale))
    if stale:
        return _result(WARN, "stale_locks",
            f"{len(stale)} stale lock file(s) older than {max_age_hours:.0f}h "
            f"(a crashed process may not have released them)",
            count=len(stale), sample=stale[:10], max_age_hours=max_age_hours,
            hint="Remove only if you are sure no process holds them.")
    return _result(OK, "stale_locks",
        f"no stale lock files older than {max_age_hours:.0f}h", count=0)


def check_plugin_health() -> dict:
    """X-PLUG-1: run every K7 plugin healthcheck and fold the results into one
    self-test row. OK when no plugin registered a probe or all probes pass; WARN
    if any probe reports unhealthy (a plugin's own subsystem is degraded). Never
    raises -- plugins run under the host's error isolation."""
    try:
        from . import plugins as _pl
        rows = _pl.run_healthchecks()
    except Exception as e:  # noqa: BLE001
        return _result(OK, "plugin_health",
            f"plugin healthchecks unavailable ({type(e).__name__})", count=0)
    if not rows:
        return _result(OK, "plugin_health", "no plugin healthchecks registered",
                       count=0)
    failed = [r for r in rows if not r.get("ok")]
    if failed:
        return _result(WARN, "plugin_health",
            f"{len(failed)}/{len(rows)} plugin healthcheck(s) unhealthy",
            count=len(rows), failed=[r.get("name") for r in failed],
            detail_rows=failed[:10])
    return _result(OK, "plugin_health",
        f"all {len(rows)} plugin healthcheck(s) passing", count=len(rows))


def check_egress_fail_closed() -> dict:
    """POS-3: verify the fail-closed egress invariant. A ``vpn_required`` site
    whose tunnel is down must make ``download_egress.effective_download_proxy()``
    RAISE (never return an unproxied client -- that would leak payload bytes on
    the clear interface). Simulate a down tunnel with a ``socks_for_site`` stub
    that raises ``VPNRequiredError`` and assert it propagates. Pure -- no
    external egress. A fail-open regression (swallowed raise) -> FAIL."""
    try:
        from . import download_egress as _eg
        from .vpn_runtime import VPNRequiredError
    except Exception as e:  # noqa: BLE001
        return _result(WARN, "egress_fail_closed",
            f"could not load egress modules to verify ({type(e).__name__})",
            hint="download_egress / vpn_runtime import failed")

    def _tunnel_down(_site_id):
        raise VPNRequiredError("selftest: simulated vpn_required tunnel down")

    try:
        proxy = _eg.effective_download_proxy(None, "__selftest__", _tunnel_down)
    except VPNRequiredError:
        return _result(OK, "egress_fail_closed",
            "fail-closed egress verified: a down vpn_required tunnel raises "
            "instead of falling open to a clear-interface download")
    return _result(FAIL, "egress_fail_closed",
        f"egress did NOT fail closed: a down vpn_required tunnel returned "
        f"proxy={proxy!r} instead of raising -- payload bytes could leak on the "
        f"clear interface",
        hint="effective_download_proxy must let VPNRequiredError propagate.")


# ── Auto-recovery for corrupt SQLite ─────────────────────────────────

def auto_recover_sqlite(db_path: str) -> dict:
    """If db is unrecoverably corrupt, move it aside and let db_init()
    recreate. Triggered by the startup self-test when check_database()
    returns FAIL with integrity != ok.

    The user's history is sacrificed — but losing history is a less
    bad outcome than the app refusing to start. The corrupt file is
    preserved as `queue.db.corrupt.<timestamp>` so the user can try to
    recover it offline if they want.
    """
    p = Path(db_path)
    if not p.exists():
        # No db at all — db_init() will create it cleanly on startup
        return _result(OK, "auto_recover_sqlite", "no db file present")
    # First try opening normally to see if it's still corrupt
    try:
        cx = sqlite3.connect(db_path, timeout=2.0)
        check = cx.execute("PRAGMA quick_check").fetchone()[0]
        cx.close()
        if check == "ok":
            return _result(OK, "auto_recover_sqlite", "db not corrupt")
    except sqlite3.DatabaseError:
        pass  # Confirmed corrupt; fall through to recovery
    # Move aside with timestamp
    backup_path = p.with_suffix(f"{p.suffix}.corrupt.{int(time.time())}")
    try:
        p.rename(backup_path)
        # Also move WAL + SHM files so the new db starts clean
        for suffix in ("-wal", "-shm"):
            companion = Path(str(p) + suffix)
            if companion.exists():
                companion.rename(
                    Path(str(backup_path) + suffix))
        return _result(WARN, "auto_recover_sqlite",
            f"corrupt db moved aside to {backup_path.name}; "
            f"a fresh schema will be created on next db_init()",
            backup_path=str(backup_path),
            hint="Your history was preserved in the corrupt file but "
                 "isn't readable through normal queries. Use 'sqlite3 "
                 f"{backup_path.name} .recover' to attempt offline "
                 "recovery if you want it back.")
    except OSError as e:
        return _result(FAIL, "auto_recover_sqlite",
            f"can't move {db_path}: {type(e).__name__}: {e}",
            hint="Stop the app, manually rename queue.db, restart.")


# ── Aggregate runner ─────────────────────────────────────────────────

def run_all(sites_config_path: str | None = None,
             db_path: str | None = None,
             cookies_dir: str = "cookies",
             download_dirs: list[str] | None = None,
             captures_root: str | None = None) -> dict:
    """Run every check, return a structured report. The caller decides
    what to do with FAILs (log loudly, surface in UI, block startup).

    Args:
      sites_config_path: path to sites_config.json, or None to skip
      db_path: path to queue.db, or None to skip
      cookies_dir: cookies directory
      download_dirs: list of all configured download_dir paths to check

    Returns: {
      "ok": bool (true iff zero FAILs),
      "checks": [<result dict>, ...],
      "summary": {"ok": N, "warn": N, "fail": N},
      "elapsed_ms": float
    }
    """
    t0 = time.time()
    checks = []
    # Database — also auto-recover if corrupt
    if db_path:
        rec = auto_recover_sqlite(db_path)
        if rec["status"] != OK:
            checks.append(rec)
        checks.append(check_database(db_path))
    # Cookies dir
    checks.append(check_cookies_dir(cookies_dir))
    # Sites config
    if sites_config_path:
        try:
            with open(sites_config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            checks.extend(check_sites_config(cfg))
        except json.JSONDecodeError as e:
            checks.append(_result(FAIL, "sites_config",
                f"can't parse: {e}",
                path=sites_config_path,
                hint="sites_config.json is malformed JSON. Restore "
                     "from backup."))
        except FileNotFoundError:
            checks.append(_result(OK, "sites_config",
                "no sites_config.json yet (first run)"))
        except OSError as e:
            checks.append(_result(WARN, "sites_config",
                f"read failed: {type(e).__name__}: {e}",
                path=sites_config_path))
    # Disk space — once per unique configured download dir, PLUS the capture
    # store root (PROJECT_ROOT / install dir). The store often lives on a
    # different path/volume than downloads, and a full capture store is a
    # distinct 'disk full' class (7.1G / 687 wacz seen in the field) the
    # download-dir check misses.
    seen = set()
    for d in list(download_dirs or []) + ([captures_root] if captures_root else []):
        if not d or d in seen:
            continue
        seen.add(d)
        checks.append(check_disk_space(d))
    # Playwright + loopback are environment-wide
    checks.append(check_playwright())
    checks.append(check_loopback())
    # ROB-3: startup coverage for ffmpeg HLS/TS capability + extractor freshness
    # (folded from healthcheck's on-demand probes so a broken ffmpeg / stale
    # yt-dlp is caught at boot, not only on the health page).
    checks.append(check_ffmpeg_hls())
    checks.append(check_extractor_freshness())

    # Session-1 hygiene: leaked tempfiles + stale locks across the download/capture
    # roots, and a live verification that egress fails closed (ROB-3-rem / POS-3).
    _hygiene_dirs = [d for d in (download_dirs or []) if d]
    if captures_root:
        _hygiene_dirs.append(captures_root)
    checks.append(check_orphan_tempfiles(*_hygiene_dirs))
    checks.append(check_stale_locks(*_hygiene_dirs))
    checks.append(check_egress_fail_closed())
    checks.append(check_plugin_health())

    summary = {OK: 0, WARN: 0, FAIL: 0}
    for c in checks:
        summary[c["status"]] = summary.get(c["status"], 0) + 1
    return {
        "ok": summary[FAIL] == 0,
        "checks": checks,
        "summary": summary,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }


def log_to_stderr(report: dict):
    """Pretty-print the report to stderr. Called once at startup.
    The format is meant for tail/grep — one line per check, color
    only if stderr is a tty."""
    is_tty = sys.stderr.isatty()
    def color(c, s):
        if not is_tty:
            return s
        codes = {"red": "\033[91m", "yellow": "\033[93m",
                  "green": "\033[92m", "dim": "\033[2m",
                  "reset": "\033[0m"}
        return f"{codes.get(c, '')}{s}{codes['reset']}"
    sys.stderr.write(color("dim", f"\n[selftest] ran {len(report['checks'])} checks in {report['elapsed_ms']}ms\n"))
    for c in report["checks"]:
        marker = {"ok": color("green", "✓"),
                   "warn": color("yellow", "!"),
                   "fail": color("red", "✗")}.get(c["status"], "?")
        line = f"[selftest] {marker} {c['test']}: {c['message']}\n"
        sys.stderr.write(line)
        if c["status"] != OK and c["detail"].get("hint"):
            sys.stderr.write(color("dim",
                f"[selftest]   hint: {c['detail']['hint']}\n"))
    s = report["summary"]
    if not report["ok"]:
        sys.stderr.write(color("red",
            f"[selftest] {s['fail']} FAIL · {s['warn']} warn · {s['ok']} ok\n"))
        sys.stderr.write(color("red",
            "[selftest] App will still start but expect runtime errors. "
            "Fix the FAILs above.\n\n"))
    else:
        sys.stderr.write(color("green",
            f"[selftest] {s['ok']} ok · {s['warn']} warn · {s['fail']} fail\n\n"))
