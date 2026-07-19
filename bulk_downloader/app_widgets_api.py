"""v3.43.60: Flask routes for the dashboard widget picker.

Five endpoints:

  GET   /api/widgets/all                       Full config (global + per-site)
  GET   /api/widgets/<scope>                   Resolved list for one scope
  PUT   /api/widgets/<scope>                   Replace list for one scope
  DELETE /api/widgets/<scope>                  Reset / remove override
  GET   /api/widgets/data[?site_id=<id>]       Current values for all KPIs

`<scope>` is either `_global` or a site_id.

The data endpoint aggregates the existing stats sources — dashboard_widgets
already provides rolling-window metrics, db_stats provides counts. We don't
need new collectors for v3.43.60; we just shape the existing data into the
keys the 21 KPI renderers expect.
"""
from __future__ import annotations

import sys
import time

try:
    from flask import Blueprint, request, jsonify
except ImportError:  # pragma: no cover
    Blueprint = None
    request = None
    jsonify = None


widgets_bp = Blueprint("widgets_api", __name__) if Blueprint else None


def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


# ─── Config routes ──────────────────────────────────────────────────

@widgets_bp.route("/api/widgets/all", methods=["GET"]) if widgets_bp else (lambda f: f)
def widgets_all():
    from . import widgets_config
    widgets_config.load()
    return jsonify(widgets_config.get_all())


@widgets_bp.route("/api/widgets/<scope>", methods=["GET"]) if widgets_bp else (lambda f: f)
def widgets_get_scope(scope):
    from . import widgets_config
    widgets_config.load()
    if scope == "_global":
        return jsonify({"scope": scope, "widgets": widgets_config.get_global()})
    return jsonify({"scope": scope, "widgets": widgets_config.get_for_site(scope)})


@widgets_bp.route("/api/widgets/<scope>", methods=["PUT"]) if widgets_bp else (lambda f: f)
def widgets_put_scope(scope):
    from . import widgets_config
    data = request.get_json(silent=True) or {}
    raw = data.get("widgets")
    if not isinstance(raw, list):
        return _err("body.widgets must be a list of {id, size} entries")
    try:
        if scope == "_global":
            saved = widgets_config.set_global(raw)
        else:
            saved = widgets_config.set_for_site(scope, raw)
    except ValueError as e:
        return _err(str(e))
    return jsonify({"ok": True, "scope": scope, "widgets": saved})


@widgets_bp.route("/api/widgets/<scope>", methods=["DELETE"]) if widgets_bp else (lambda f: f)
def widgets_delete_scope(scope):
    from . import widgets_config
    if scope == "_global":
        widgets_config.reset_global()
        return jsonify({"ok": True, "scope": scope, "widgets": widgets_config.get_global()})
    removed = widgets_config.reset_for_site(scope)
    return jsonify({"ok": True, "scope": scope, "removed_override": removed})


# ─── Data route ─────────────────────────────────────────────────────

@widgets_bp.route("/api/widgets/data", methods=["GET"]) if widgets_bp else (lambda f: f)
def widgets_data():
    """Aggregate current values for all 21 KPIs.

    For v3.43.60 we wire what's easily available from existing modules and
    leave placeholders (None) for KPIs that need new collectors. The frontend
    renders '—' for None values, so partial coverage is acceptable.
    """
    site_id = request.args.get("site_id") or None
    try:
        d = _collect_data(site_id)
    except Exception as e:
        sys.stderr.write(f"[widgets-api] data collection raised: {e}\n")
        d = {}
    return jsonify({"ok": True, "data": d, "site_id": site_id, "ts": time.time()})


def compute_worker_counts(runners: dict) -> dict:
    """Compute aggregate worker counts across all runners.

    Returns a dict with four keys:
      workers_active: int    — sum of active_worker_count() across runners
      workers_total:  int|None — sum of max_concurrent; None on empty fleet
      sites_running:  int    — count of runners with any active workers
      sites_total:    int    — total runner count

    The None-on-empty-fleet contract is load-bearing (DANGER_MAP:
    "workers_total is None (key absent) on empty fleet — never the
    magic 16"). v3.63.6 lesson: never synthesize a fleet size that
    doesn't reflect actual configured capacity. The widget renderer
    null-guards on this key and shows '—' when None — that is the
    honest empty-fleet UX, not "0/16".

    Used by BOTH:
      • /api/widgets/data (_collect_data above)
      • /api/dashboard/v2 (app.py — v3.65.x V2.1 redesign addition)
    Sharing this helper keeps the no-magic-16 invariant in one place;
    duplicating the loop in app.py would risk a future audit
    "fixing" one site of the bug and not the other.
    """
    total_active = 0
    total_cap = 0
    for r in runners.values():
        try:
            total_active += getattr(r, "active_worker_count", lambda: 0)()
            total_cap += getattr(r, "max_concurrent", 0)
        except Exception:
            pass
    return {
        "workers_active": total_active,
        # v3.63.6 contract: emit None when the configured fleet is
        # genuinely empty so the renderer can show '—'. NEVER substitute
        # `total_cap or <N>` here. See DANGER_MAP and
        # tests/test_u49_workers_total_honest.py.
        "workers_total": total_cap if total_cap > 0 else None,
        "sites_running": sum(
            1 for r in runners.values()
            if getattr(r, "active_worker_count", lambda: 0)() > 0
        ),
        "sites_total": len(runners),
    }


def _collect_data(site_id: str | None) -> dict:
    """Shape existing stats into the keys widgets.js expects.

    Soft-imports everything so a missing/broken collector returns partial
    data rather than 500ing the entire dashboard.
    """
    out: dict = {}

    # Try dashboard_widgets — rolling-window metrics + success rate.
    try:
        from . import dashboard_widgets
        if hasattr(dashboard_widgets, "snapshot_for_site"):
            snap = dashboard_widgets.snapshot_for_site(site_id) if site_id else dashboard_widgets.snapshot()
        elif hasattr(dashboard_widgets, "snapshot"):
            snap = dashboard_widgets.snapshot()
        else:
            snap = {}
        if isinstance(snap, dict):
            # Direct mappings.
            out["done_today"] = snap.get("done_today")
            out["done_hour"] = snap.get("done_hour")
            out["bytes_today_fmt"] = snap.get("bytes_today_fmt") or snap.get("bytes_today")
            out["throughput_fmt"] = snap.get("throughput_fmt") or snap.get("throughput")
            out["success_rate"] = snap.get("success_rate_pct") or snap.get("success_rate")
            out["files_hour"] = snap.get("files_hour")
            out["avg_speed_fmt"] = snap.get("avg_speed_fmt")
            out["bandwidth_fmt"] = snap.get("bandwidth_fmt")
    except Exception as e:
        sys.stderr.write(f"[widgets-api] dashboard_widgets snapshot failed: {e}\n")

    # db_stats — queue depth, status counts.
    try:
        from .db import db_stats
        s = db_stats(site_id)
        # Shape may vary; the existing UI uses _flatten() — we just probe known keys.
        if isinstance(s, dict):
            counts = s.get("counts") if isinstance(s.get("counts"), dict) else s
            out.setdefault("queue_depth", counts.get("queued") or counts.get("pending") or 0)
            out.setdefault("action_req",
                (counts.get("login_required") or 0) + (counts.get("captcha") or 0))
            out.setdefault("stuck", counts.get("stuck") or 0)
            out.setdefault("failures_hr", counts.get("failed_recent") or 0)
            out.setdefault("retries_pending", counts.get("retry_pending") or 0)
    except Exception as e:
        sys.stderr.write(f"[widgets-api] db_stats failed: {e}\n")

    # Disk + worker counts — pull from app globals if available.
    try:
        import importlib
        app_mod = importlib.import_module("bulk_downloader.app_state")
        runners = getattr(app_mod, "runners", {}) or {}
        if site_id and site_id in runners:
            r = runners[site_id]
            out["workers_active"] = getattr(r, "active_worker_count", lambda: 0)()
            out["workers_total"] = getattr(r, "max_concurrent", 0) or len(getattr(r, "_workers", []))
        else:
            wc = compute_worker_counts(runners)
            out["workers_active"] = wc["workers_active"]
            out["workers_total"] = wc["workers_total"]
            out["sites_running"] = wc["sites_running"]
            out["sites_total"] = wc["sites_total"]
    except Exception as e:
        sys.stderr.write(f"[widgets-api] worker count failed: {e}\n")

    # CPU/RAM via psutil if installed; otherwise leave None.
    try:
        import psutil
        out["cpu_pct"] = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        out["ram_pct"] = vm.percent
        out["cpu_cores"] = psutil.cpu_count(logical=True)
        out["ram_total_fmt"] = _fmt_bytes(vm.total)
    except ImportError:
        pass
    except Exception as e:
        sys.stderr.write(f"[widgets-api] psutil failed: {e}\n")

    # v3.63.6: GPU via nvidia-smi. NVIDIA-only by design (same posture
    # as gpu_check.sh — AMD ROCm and Intel Arc compatibility matrices
    # are too narrow to automate). Fail-open on any error: missing
    # binary, no NVIDIA hardware, malformed output. The widget's
    # renderer null-guards every field, so absent keys render as '—'.
    #
    # Query: --query-gpu=utilization.gpu,memory.used,memory.total,name
    # --format=csv,noheader,nounits.
    # One row per GPU; the widget surfaces GPU 0 (matches gpu_check.sh
    # which also picks the largest-VRAM card; on a single-GPU host
    # they're the same card).
    try:
        import shutil as _shutil
        import subprocess as _sp
        _smi = _shutil.which("nvidia-smi")
        if _smi:
            _r = _sp.run(
                [_smi, "--query-gpu=utilization.gpu,memory.used,"
                       "memory.total,name",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            if _r.returncode == 0 and _r.stdout.strip():
                # First row only; comma-separated, possibly with
                # whitespace padding from nvidia-smi.
                _first = _r.stdout.strip().splitlines()[0]
                _parts = [p.strip() for p in _first.split(",")]
                if len(_parts) >= 4:
                    _util, _used_mib, _total_mib, _name = _parts[:4]
                    try:
                        out["gpu_util_pct"] = int(_util)
                        out["gpu_mem_used_mib"] = int(_used_mib)
                        out["gpu_mem_total_mib"] = int(_total_mib)
                        out["gpu_mem_pct"] = (
                            round(100.0 * int(_used_mib) / int(_total_mib), 1)
                            if int(_total_mib) > 0 else None)
                        out["gpu_name"] = _name
                        out["gpu_mem_total_fmt"] = (
                            f"{int(_total_mib) / 1024:.1f} GiB"
                            if int(_total_mib) >= 1024
                            else f"{int(_total_mib)} MiB")
                    except (ValueError, ZeroDivisionError):
                        pass
    except Exception as e:
        sys.stderr.write(f"[widgets-api] nvidia-smi failed: {e}\n")

    # Disk — re-use detect.disk_free_gb if it's available. v3.63.6 fix:
    # pre-v3.63.6 the call was `disk_free_gb()` with no argument, which
    # raised TypeError (the function requires a path positional). The
    # bare `except Exception: pass` swallowed the error silently, so
    # the widget always showed '—' instead of the real disk free value.
    # Now we pass the first configured site's download_dir if any, and
    # rely on the function's own empty-string-to-Path.home() fallback
    # otherwise.
    try:
        from .detect import disk_free_gb
        dl_path = ""
        try:
            import importlib as _il
            _app_mod = _il.import_module("bulk_downloader.app_state")
            _s_cfg = getattr(_app_mod, "s_cfg", {}) or {}
            for _sc in _s_cfg.values() if isinstance(_s_cfg, dict) else []:
                _dd = (_sc or {}).get("download_dir") if isinstance(_sc, dict) else None
                if _dd:
                    dl_path = _dd
                    break
        except Exception:
            pass
        gb = disk_free_gb(dl_path)
        if gb is not None:
            out["disk_free_fmt"] = f"{gb:.1f} GB" if gb < 1024 else f"{gb/1024:.1f} TB"
    except Exception as e:
        sys.stderr.write(f"[widgets-api] disk_free_gb failed: {e}\n")

    # v3.51 (Phase 4): collection widgets — surface the library data.
    # library_stats() is one query per axis on indexed columns, cheap
    # enough to compute on every dashboard refresh.
    try:
        from . import library as _library
        stats = _library.library_stats()
        total = stats.get("total_files", 0) or 0
        watched = stats.get("watched_files", 0) or 0
        missing = stats.get("missing_files", 0) or 0
        unrated = stats.get("unrated_files", 0) or 0
        out["lib_total"] = total
        out["lib_size_fmt"] = _fmt_bytes(stats.get("total_size", 0) or 0)
        out["lib_missing"] = missing
        out["lib_unrated"] = unrated
        # Watched as a percentage of the collection
        out["lib_watched_pct"] = (
            round(100.0 * watched / total, 1) if total else 0.0)
        out["lib_watched_extra"] = f"{watched} of {total}"
        out["lib_unrated_extra"] = (
            f"{round(100.0 * unrated / total)}% of library" if total else "")
        out["lib_missing_extra"] = (
            "files moved or deleted" if missing else "all present")
        # Top studio from the by_studio aggregate
        by_studio = stats.get("by_studio") or []
        if by_studio:
            top = by_studio[0]
            out["lib_top_studio"] = top.get("k") or "—"
            out["lib_top_studio_extra"] = f"{top.get('n', 0)} files"
        # Count library rows added in the last 7 days
        try:
            import time as _t
            from .db import db_conn
            cutoff = _t.time() - 7 * 86400
            with db_conn() as cx:
                r = cx.execute(
                    "SELECT COUNT(*) AS n FROM library WHERE added_at >= ?",
                    (cutoff,)).fetchone()
                out["lib_recent"] = (r["n"] if r else 0)
        except Exception:
            pass
    except Exception as e:
        sys.stderr.write(f"[widgets-api] library stats failed: {e}\n")

    # v3.51 (Phase 4): audit widget — config changes in the last 24h.
    try:
        import time as _t
        from .db import db_conn
        cutoff = _t.time() - 86400
        with db_conn() as cx:
            r = cx.execute(
                "SELECT COUNT(*) AS n FROM audit_log WHERE ts >= ?",
                (cutoff,)).fetchone()
            n = r["n"] if r else 0
            out["audit_recent"] = n
            out["audit_recent_extra"] = (
                "no changes" if n == 0 else "last 24h")
    except Exception:
        # audit_log table may not exist yet (no audited writes) — fine,
        # widget renders '—'
        pass

    # v3.55 (Phase 4 backlog): diagnostics widgets. Site health +
    # error clusters + captcha + cookie signals. Each soft-wrapped so a
    # missing module never 500s the dashboard.
    try:
        import importlib
        app_mod = importlib.import_module("bulk_downloader.app_state")
        cfg_map = getattr(app_mod, "s_cfg", {}) or {}
        runners = getattr(app_mod, "runners", {}) or {}

        # Success rate (24h) + sites-need-attention via site_changelog.
        try:
            from . import site_changelog as _sc
            report = _sc.for_all_sites(cfg_map)
            sites = report.get("sites", [])
            # Aggregate 24h success: weight by sample count if present
            total_ok = total_n = 0
            attention = 0
            for s in sites:
                rc = s.get("recent_24h") or {}
                if rc.get("has_data"):
                    n = rc.get("total") or rc.get("n") or 0
                    sr = rc.get("success_rate")
                    if n and sr is not None:
                        total_n += n
                        total_ok += n * sr / 100.0
                if s.get("headline_severity") in ("alert", "warn"):
                    attention += 1
            if total_n:
                out["success_rate_24h"] = round(
                    100.0 * total_ok / total_n, 1)
                out["success_rate_24h_extra"] = (
                    f"{int(total_n)} jobs across {len(sites)} sites")
            out["sites_need_attention"] = attention
            out["sites_need_attention_extra"] = (
                "all healthy" if attention == 0
                else f"of {len(sites)} sites")
        except Exception as e:
            sys.stderr.write(
                f"[widgets-api] site_changelog failed: {e}\n")

        # Top error cluster via error_fingerprint.
        try:
            from . import error_fingerprint as _ef
            clusters = _ef.explain_top_clusters(hours=24, n=1)
            if clusters:
                top = clusters[0]
                cat = top.get("category", "other")
                cnt = top.get("count", 0)
                out["error_top_cluster"] = cat
                out["error_top_cluster_extra"] = (
                    f"{cnt} occurrence(s), last 24h")
            else:
                out["error_top_cluster"] = "none"
                out["error_top_cluster_extra"] = "no failures, last 24h"
        except Exception as e:
            sys.stderr.write(
                f"[widgets-api] error_fingerprint failed: {e}\n")

        # Captcha encounters — sum per-runner _captcha_stats.
        try:
            total_captcha = 0
            for r in runners.values():
                cs = getattr(r, "_captcha_stats", {}) or {}
                total_captcha += int(cs.get("encountered", 0)
                                     or cs.get("submitted", 0) or 0)
            out["captcha_24h"] = total_captcha
            out["captcha_24h_extra"] = (
                "none" if total_captcha == 0 else "across all sites")
        except Exception:
            pass

        # Cookie warnings — reuse doctor.cookie_freshness.
        try:
            from . import doctor as _doctor
            cookie_checks = _doctor.cookie_freshness(cfg_map)
            warn_count = sum(1 for c in cookie_checks
                             if c.get("status") == "warn")
            out["cookie_warnings"] = warn_count
            out["cookie_warnings_extra"] = (
                "all fresh" if warn_count == 0
                else "stale or missing")
        except Exception as e:
            sys.stderr.write(f"[widgets-api] cookie check failed: {e}\n")

        # v3.58 (Phase 10, #133): active alerts — alert rules that
        # fired in the last 24h (disk-write rate, error spikes, etc.).
        try:
            from . import alerts_engine as _alerts
            fired = _alerts.active_alerts(lookback_hours=24)
            out["active_alerts"] = len(fired)
            out["active_alerts_extra"] = (
                "none, last 24h" if not fired
                else "fired, last 24h")
        except Exception as e:
            sys.stderr.write(f"[widgets-api] alerts check failed: {e}\n")
    except Exception as e:
        sys.stderr.write(f"[widgets-api] diagnostics collector failed: {e}\n")

    # v3.63.6: avg_size — average download size from history.
    # Query: AVG(file_size) over completed rows in the last 24h.
    # The renderer expects `avg_size_fmt` (a human-readable string).
    # If no completions in the window, leave the key absent →
    # renderer shows '—'.
    try:
        import time as _t
        from .db import db_conn
        cutoff = _t.time() - 86400
        with db_conn() as cx:
            r = cx.execute(
                "SELECT AVG(file_size) AS avg_size FROM history "
                "WHERE status = 'completed' AND ts >= ? "
                "AND file_size > 0",
                (cutoff,)).fetchone()
            if r and r["avg_size"]:
                out["avg_size_fmt"] = _fmt_bytes(int(r["avg_size"]))
    except Exception as e:
        sys.stderr.write(f"[widgets-api] avg_size failed: {e}\n")

    # v3.63.6: eta_clear — forecast how long until the queue drains.
    # eta_hours = queue_depth / files_hour, formatted as "Xh" / "X.Yd".
    # If files_hour is 0 (nothing finishing), ETA is undefined →
    # leave the key absent so the renderer shows '—'.
    try:
        qd = out.get("queue_depth") or 0
        fh = out.get("files_hour") or 0
        if qd > 0 and fh > 0:
            eta_hours = qd / fh
            if eta_hours < 1:
                out["eta_clear_fmt"] = f"{int(eta_hours * 60)}m"
            elif eta_hours < 24:
                out["eta_clear_fmt"] = f"{eta_hours:.1f}h"
            else:
                out["eta_clear_fmt"] = f"{eta_hours / 24:.1f}d"
            out["eta_clear_extra"] = (
                f"{int(qd)} pending at {int(fh)} files/hr")
        elif qd == 0:
            out["eta_clear_fmt"] = "now"
            out["eta_clear_extra"] = "queue empty"
        # else: fh == 0 and qd > 0 → can't forecast; leave absent.
    except Exception as e:
        sys.stderr.write(f"[widgets-api] eta_clear failed: {e}\n")

    # v3.63.6: cookies_oldest_days + cookies_oldest_site — surfaces the
    # site whose cookies are oldest (= most due for renewal). Reuses
    # doctor.cookie_freshness which already walks sites_config and
    # mtime's each cookie_file. The widget renderer expects:
    #   cookies_oldest_days: int days (None → renders '—')
    #   cookies_oldest_site: human-readable site name
    try:
        import importlib as _il
        _app_mod = _il.import_module("bulk_downloader.app_state")
        _s_cfg = getattr(_app_mod, "s_cfg", {}) or {}
        if isinstance(_s_cfg, dict) and _s_cfg:
            from . import doctor as _doctor
            checks = _doctor.cookie_freshness(_s_cfg)
            # Each check has either age_days (existing file) or no age
            # (missing-on-disk). Pick the max age_days.
            aged = [
                (c.get("age_days", 0), c.get("site", "?"))
                for c in checks
                if c.get("age_days") is not None
            ]
            if aged:
                top_age, top_site = max(aged, key=lambda x: x[0])
                out["cookies_oldest_days"] = int(top_age)
                out["cookies_oldest_site"] = top_site
    except Exception as e:
        sys.stderr.write(f"[widgets-api] cookies_oldest failed: {e}\n")

    return out


def _fmt_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    f = float(n)
    while f >= 1024 and i < len(units) - 1:
        f /= 1024
        i += 1
    return f"{f:.1f} {units[i]}" if i > 0 else f"{int(f)} {units[i]}"


# ─── Registration ───────────────────────────────────────────────────

def register_routes(app) -> int:
    if widgets_bp is None:
        sys.stderr.write("[widgets-api] Flask not installed; routes not registered\n")
        return 0
    try:
        app.register_blueprint(widgets_bp)
    except (ValueError, AssertionError) as e:
        sys.stderr.write(f"[widgets-api] blueprint already registered: {e}\n")
        return 0
    n = sum(1 for r in app.url_map.iter_rules() if r.rule.startswith("/api/widgets"))
    sys.stderr.write(f"[widgets-api] registered {n} widget routes\n")
    return n


__all__ = ["register_routes", "widgets_bp"]
