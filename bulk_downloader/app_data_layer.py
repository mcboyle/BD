"""app_data_layer.py — additive, READ-ONLY dashboard data layer (O / consolidation).

The single analytics tier the dashboards consume. Each metric is produced by one
plain provider function; the HTTP routes wrap the providers, and the monitoring
dashboard imports the SAME providers instead of re-deriving analytics — so there is
no duplicate analytics logic between the data layer and the dashboards.

Flow:  Shared cores  ->  these providers (Analytics APIs)  ->  reports / dashboards.

    GET /api/data/template_health   -> collect_template_analytics()  (template_core)
    GET /api/data/capture_analytics -> collect_capture_analytics()
    GET /api/data/queue_analytics   -> collect_queue_analytics()
    GET /api/data/release_analytics -> collect_release_analytics()
    GET /api/data/kb_analytics      -> collect_kb_analytics()

Purely additive, observational, no mutation, no persistence. Auth piggybacks on
app.py's global hooks. Not wired into app.py (one-line register at final integration).

NEEDS OPERATOR CLICK-THROUGH VALIDATION.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from flask import Blueprint, jsonify

data_layer_bp = Blueprint("data_layer", __name__)
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _ensure_path():
    # Repo root makes ``import tools.<mod>`` resolve; tools/ itself makes the
    # tools modules' own bare sibling imports (e.g. template_core's
    # ``import template_inventory``) resolve too — those assume CLI-style
    # execution with tools/ on the path. Without the second insert, the first
    # hit to a data-layer collector in a fresh process raises
    # ``No module named 'template_inventory'`` (v3.66.321).
    for p in (str(_REPO_ROOT), str(_REPO_ROOT / "tools")):
        if p not in sys.path:
            sys.path.insert(0, p)


def _root():
    return str(_REPO_ROOT)


# providers (plain functions; the single analytics source)
def collect_template_analytics():
    _ensure_path()
    import tools.template_core as TC  # type: ignore
    out = TC.analytics(_root())
    # F3.3: additive canary status (read-only, secret-free). Fail-soft so a
    # canary problem never breaks the analytics payload.
    if isinstance(out, dict):
        try:
            from bulk_downloader import template_canary as _tc  # type: ignore
            out["canary"] = _tc.canary_status()
        except Exception:
            out["canary"] = None
    return out


def collect_capture_analytics():
    _ensure_path()
    import tools.capture_analytics as CA  # type: ignore
    # Bound + cache: analyze() opens/parses every capture_*.json under the store,
    # an unbounded single-core walk on a large store (same hang class as the 596
    # diagnostics/replay collectors). Cap to the newest _HEAVY_LIMIT and cache
    # briefly so report-page polls / route-scanning tests do not recompute.
    # v3.66.1015: all THREE bounds, matching collect_capture_diagnostics below.
    # The comment beneath names the bound set as "count + wall-time budget +
    # per-file size cap"; this caller passed only the count, because analyze()
    # did not accept the other two. On the box that left the route a coin flip
    # against L34's 8s budget -- the same commit failed one capture and passed
    # the next, the difference being this 600s cache rather than the code.
    return _cached("capture_analytics",
                   lambda: CA.analyze(_root(), limit=_HEAVY_LIMIT,
                                      budget_s=_HEAVY_BUDGET_S,
                                      max_bytes=_HEAVY_MAX_BYTES))


# Heavy-report bounds: capture diagnostics builds a FULL template per .wacz
# (open zip + player-recognition regex over the page HTML) and replay
# validation opens every wacz. On a real operator store (hundreds of wacz,
# some >100MB) an unbounded or generously-bounded pass runs for hours -- it
# wedged the release-gate suite live. Hard-bound the work (count + wall-time
# budget + per-file size cap) and cache the result briefly so repeat views
# (report page polls, route-scanning tests) do not recompute.
_HEAVY_TTL_S = 600
_HEAVY_LIMIT = 50
# v3.66.1023: 20 -> 5, because 20 could never fit inside the gate these routes
# are held to. L34 fails any operator route that does not answer within 8s
# (live_tests/checks._L34_ROUTE_BUDGET_S), so a collector permitted to spend 20
# guarantees the route TERMINATES and not that it ANSWERS -- the property the
# gate actually tests. What made the capture at e7d3b5e pass was max_bytes
# skipping an oversized capture JSON, not the wall clock.
#
# For capture_ANALYTICS the overrun past this number is ONE FILE, because
# _artifacts checks the budget BEFORE each parse: a 25.1 MB capture JSON with
# 220k network_log entries parses in 0.233s, and
# tests/test_v3_66_1023_heavy_budget_fits_the_route_gate.py pins
# budget + margin <= gate on that basis.
#
# v3.66.1026: that arithmetic was NEVER true of capture_DIAGNOSTICS, whose
# "one file" is two full zip parses + whole-dom-log HTML serialization + the
# recognizer batteries + a sha256 of the archive: measured 16.1s for the
# newest <=25MB .wacz on the operator store (37.9s for the 3rd-newest, at
# 2.0MB -- size does not predict cost), so the route answered in 17.4s with
# this budget exhausted and useless. Its collector therefore takes
# isolate=True below: each diagnose runs in a child process killed at the
# deadline, bounding the overrun by CD._KILL_GRACE_S instead of by whatever
# one file costs. tests/test_v3_66_1026_heavy_collectors_bounded_for_real.py
# pins budget + kill grace + margin <= gate for that path.
#
# THIS WILL TRUNCATE ON A LARGE STORE, DELIBERATELY. That is the right trade
# for a ROUTE and only because @1015 built the reporting: anything skipped or
# killed is still counted, still listed (unparsed_artifacts, skipped_wacz,
# killed_in_flight), and budget_exhausted says so. A bounded, LABELLED answer
# inside the gate beats a complete one that intermittently blows it. The CLI
# is unaffected -- all three bounds default to None there.
_HEAVY_BUDGET_S = 5
_HEAVY_MAX_BYTES = 25_000_000
_heavy_cache: dict = {}
# Single-flight (v3.66.1026): L34's phase-1 probe and its serial re-probe
# both hit a cold cache, and without a lock each ran the FULL collector --
# measured as two overlapping ~17s capture_diagnostics computes on the box.
# One lock per key: concurrent misses serialize, the second caller gets the
# first's cached result instead of recomputing.
_heavy_gate = threading.Lock()
_heavy_locks: dict = {}


def _cached(key, fn):
    import time as _t
    with _heavy_gate:
        lk = _heavy_locks.setdefault(key, threading.Lock())
    with lk:
        ent = _heavy_cache.get(key)
        now = _t.monotonic()
        if ent is not None and (now - ent[0]) < _HEAVY_TTL_S:
            out = dict(ent[1])
            out["cache_age_s"] = round(now - ent[0], 1)
            return out
        val = fn()
        if isinstance(val, dict):
            _heavy_cache[key] = (_t.monotonic(), val)
        return val


def collect_capture_diagnostics():
    _ensure_path()
    import tools.capture_diagnostics as CD  # type: ignore
    # isolate=True (v3.66.1026): the in-process deadline can only skip
    # BETWEEN files, and one diagnose of the newest <=25MB .wacz measured
    # 16.1s on the operator store -- so budget_s alone left this route at
    # 17.4s against L34's 8s gate. The child-process kill bounds the overrun
    # by CD._KILL_GRACE_S; anything killed is counted (killed_in_flight) and
    # budget_exhausted says so.
    return _cached("capture_diagnostics", lambda: CD.collect(
        _root(), limit=_HEAVY_LIMIT, budget_s=_HEAVY_BUDGET_S,
        max_bytes=_HEAVY_MAX_BYTES, isolate=True))


def collect_replay_validation():
    _ensure_path()
    import tools.replay_validator as RV  # type: ignore
    return _cached("replay_validation", lambda: RV.collect(
        _root(), limit=_HEAVY_LIMIT, budget_s=_HEAVY_BUDGET_S,
        max_bytes=_HEAVY_MAX_BYTES))


def collect_storage_tiers():
    """OBS-3 (v3.66.662): read-only storage-tier dashboard -- per-tier occupancy +
    headroom across every configured download dir, fleet totals, and low-headroom
    flags. Fuses storage_rebalance.inventory over the configured paths (gathered from
    the live shared s_cfg). NO writes, NO rebalance -- surfacing only. Structural
    (paths + GB + counts), no secrets. Fail-open on an odd/absent config."""
    _ensure_path()
    from . import storage_rebalance as _sr
    paths: list = []
    try:
        import importlib
        s_cfg = getattr(importlib.import_module("bulk_downloader.app_state"),
                        "s_cfg", {}) or {}
        if isinstance(s_cfg, dict):
            for v in s_cfg.values():
                if isinstance(v, dict):
                    d = (v.get("download_dir") or "").strip()
                    if d and d not in paths:
                        paths.append(d)
    except Exception:  # noqa: BLE001
        paths = []
    try:
        inv = _sr.inventory(paths) if paths else []
    except Exception:  # noqa: BLE001
        inv = []
    total_gb = round(sum(x.get("total_gb", 0) or 0 for x in inv), 1)
    free_gb = round(sum(x.get("free_gb", 0) or 0 for x in inv), 1)
    used_gb = round(sum(x.get("used_gb", 0) or 0 for x in inv), 1)
    low = []
    for x in inv:
        if x.get("error"):
            continue
        fr = x.get("free_gb") or 0
        pct = x.get("free_pct")
        if pct is None:
            tot = x.get("total_gb") or 0
            pct = (fr / tot * 100.0) if tot else 100.0
        if fr < 10.0 or pct < 10.0:
            low.append({"path": x.get("path"), "free_gb": fr,
                        "free_pct": round(pct, 1)})
    return {
        "tier_count": len(inv),
        "total_gb": total_gb,
        "free_gb": free_gb,
        "used_gb": used_gb,
        "pct_free": round(free_gb / total_gb * 100.0, 1) if total_gb else None,
        "low_headroom": low,
        "tiers": inv,
    }


def collect_queue_analytics():
    _ensure_path()
    import tools.queue_intelligence as QI  # type: ignore
    return QI.analyze()


def collect_release_analytics():
    _ensure_path()
    import tools.changelog_analyzer as CL  # type: ignore
    return CL.parse(_root())


def collect_kb_analytics():
    _ensure_path()
    import tools.kb_audit as KB  # type: ignore
    return KB.audit(_root())


def collect_browser_status():
    """G7 — read-only browser/Cloak backend status. No secrets: versions,
    booleans, resolved backend name, and a truncated import-error class only."""
    _ensure_path()
    import os
    try:
        from bulk_downloader import cloak  # type: ignore
        st = dict(cloak.get_status())
        st["resolved_backend"] = cloak.resolve_backend()
    except Exception as e:  # noqa: BLE001
        st = {"available": False, "version": "",
              "import_error": str(e)[:160], "resolved_backend": "unknown"}
    st["import_error"] = (st.get("import_error") or "")[:160]
    st["display_set"] = bool(os.environ.get("DISPLAY"))
    return st


def collect_vpn_status():
    """VPN presence/state: tunnel count, killswitch availability, provider list.
    No secrets, no config values — presence + booleans only. Not F2-write."""
    try:
        from bulk_downloader import vpn, vpn_kill_switch_system, vpn_providers  # type: ignore
        tunnels = vpn.list_tunnels()
        sys_ok, sys_why = vpn_kill_switch_system.available()
        active_ks = vpn_kill_switch_system.list_active()
        providers = [p.get("name") or p.get("id") or "?"
                     for p in (vpn_providers.list_providers() or [])]
        return {
            "available": True,
            "tunnel_count": len(tunnels),
            "provider_count": len(providers),
            "providers": providers,
            "system_killswitch_available": sys_ok,
            "system_killswitch_reason": sys_why or "",
            "active_killswitches": len(active_ks) if active_ks else 0,
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:160]}


def collect_secrets_status():
    """Secrets store presence/state: backend name, locked/unlocked, credential
    count. NEVER exposes key values or secret material — presence only."""
    try:
        from bulk_downloader import secrets_store as SS  # type: ignore
        backend = SS.get_backend()
        name = getattr(backend, "name", "unknown")
        unlocked = backend.is_unlocked()
        try:
            keys = backend.list_keys()
            cred_count = len(keys)
            enumerable = True
        except Exception:
            cred_count = None
            enumerable = False
        return {
            "available": True,
            "backend": name,
            "is_unlocked": unlocked,
            "credential_count": cred_count,
            "credentials_enumerable": enumerable,
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:160]}


def _budget_tick():
    """OBS-1 (v3.66.656): cooperative per-request budget check for the collect
    loops that iterate unbounded at the route level. Inside a request past
    dev_metrics.REQUEST_BUDGET_MS this raises RequestBudgetExceeded -- the app
    errorhandler maps it to a retryable 503 -- so a huge draft/site set can't wedge
    the route. No-op outside a request context or when the request clock is unset,
    so background/test calls (and the tool-budgeted capture/replay routes, which
    don't use this) are unaffected. Cheap: one time.time() per loop turn, dwarfed
    by the file read / DB query the loop body already does."""
    try:
        from flask import has_request_context, g, request
    except Exception:
        return
    if not has_request_context():
        return
    t0 = getattr(g, "_dev_t0", None)
    if t0 is None:
        return
    from . import dev_metrics as _dm
    _dm.check_budget(t0, method=request.method, path=request.path)


def collect_workflow_analytics():
    """G5 — read-only A6-1 workflow timeline surface: derived_steps and
    trigger_candidate from each draft/review-candidate template that has
    been processed by the 175+ builder. Structural labels only — no URLs,
    no values, no capture content."""
    _ensure_path()
    import json
    from pathlib import Path

    root = Path(_root())
    scan_dirs = [
        root / "templates" / "drafts",
        root / "templates" / "review_candidates",
    ]
    rows = []
    for tdir in scan_dirs:
        if not tdir.is_dir():
            continue
        bucket = tdir.name
        for fp in sorted(tdir.glob("*.json")):
            _budget_tick()  # OBS-1: cooperative abort on an unbounded draft set
            try:
                data = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            wf = data.get("workflow") or {}
            dl = (data.get("selectors") or {}).get("download") or {}
            rows.append({
                "host": data.get("host") or fp.stem,
                "status": data.get("status") or data.get("template_status"),
                "bucket": bucket,
                "has_workflow": bool(wf),
                "derived_steps": wf.get("derived_steps") or [],
                "trigger_candidate": dl.get("trigger_candidate"),
                "trigger_evidence": wf.get("trigger_evidence"),
                "confidence": data.get("confidence"),
            })

    total = len(rows)
    with_workflow = sum(1 for r in rows if r["has_workflow"])
    with_trigger = sum(1 for r in rows if r["trigger_candidate"])
    return {
        "total_drafts": total,
        "with_workflow_data": with_workflow,
        "with_trigger_candidate": with_trigger,
        "templates": rows,
    }


def collect_dom_recorder_status():
    """G4 — read-only DOM recorder health: vendored asset presence/sizes,
    drop counter, and arm-fail streak. No secrets, no capture content."""
    try:
        from bulk_downloader import dom_recorder as DR  # type: ignore
        st = dict(DR.get_status())
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)[:160]}
    # Derive a simple health label so the cockpit panel has an at-a-glance
    # status without extra logic on the JS side.
    vendor_ok = st.get("rrweb_present") and st.get("snapdom_present")
    drops = st.get("dom_events_dropped", 0)
    streak = st.get("arm_fail_streak", 0)
    if not vendor_ok:
        health = "error"
    elif drops > 0 or streak >= 5:
        health = "degraded"
    else:
        health = "ok"
    st["vendor_complete"] = bool(vendor_ok)
    st["health"] = health
    st["available"] = True
    return st


def collect_deploy_health():
    """G9 — read-only deploy/operator health: running version, deployed-version
    marker, and presence of the build-critical artifacts. No secrets, no paths
    beyond presence booleans."""
    _ensure_path()
    root = Path(_root())
    try:
        from bulk_downloader import __version__ as app_version  # type: ignore
    except Exception:  # noqa: BLE001
        app_version = "unknown"
    dv = ""
    dvf = root / "tools" / "deployed_version.txt"
    try:
        if dvf.is_file():
            dv = dvf.read_text(encoding="utf-8", errors="replace").strip()[:120]
    except OSError:
        dv = ""

    def _has(*parts):
        return root.joinpath(*parts).exists()

    return {
        "app_version": app_version,
        "deployed_version_marker": dv,
        "frontend_dist_present": _has("frontend", "dist", "index.html"),
        "rrweb_vendored": _has("bulk_downloader", "vendor", "rrweb"),
        "snapdom_vendored": _has("bulk_downloader", "vendor", "snapdom"),
    }


def collect_site_health(lookback_days=7):
    """F2-a (F2.1 failure clustering + F2.2 per-site health) — read-only.

    Fuses three already-existing data sources, no new state:
      - cookie_health.status_all()          -> auth_health color per site
      - db.db_session_failure_clusters()     -> F2.1 failure clusters + per-site
                                                failure/success counts (window)
      - db.session_lifetime_observations()   -> median observed session lifetime

    F2.2 per-site health is a deterministic 4-input v1 score (cockpit-only):
      (1) auth_health color   (2) failure count in window
      (3) median lifetime     (4) last-check age
    "worst site" (lowest score) should correlate with the top F2.1 cluster.
    No URLs, no values, no secrets — structural counts/labels only.
    """
    _ensure_path()
    import time as _t
    from statistics import median as _median
    from bulk_downloader import cookie_health as _ch  # type: ignore
    from bulk_downloader import db as _db  # type: ignore

    now = _t.time()
    try:
        ah = {r["site_id"]: r for r in _ch.status_all()}
    except Exception:
        ah = {}
    fc = _db.db_session_failure_clusters(lookback_days=lookback_days)
    per_site = fc.get("per_site", {})

    # union of sites seen in either source
    site_ids = set(ah.keys()) | set(per_site.keys())
    _color_penalty = {"red": 60, "yellow": 25, "green": 0}
    sites = []
    for sid in sorted(site_ids):
        _budget_tick()  # OBS-1: cooperative abort on an unbounded site set
        a = ah.get(sid) or {}
        ps = per_site.get(sid) or {"failures": 0, "successes": 0, "by_type": {},
                                   "last_failure_ts": None}
        color = a.get("status")
        failures = int(ps.get("failures", 0))
        successes = int(ps.get("successes", 0))
        denom = failures + successes
        fail_rate = (failures / denom) if denom else None
        try:
            lifetimes = _db.session_lifetime_observations(sid, lookback_days=lookback_days)
        except Exception:
            lifetimes = []
        median_lifetime = float(_median(lifetimes)) if lifetimes else None
        last_check_ts = a.get("last_check_ts")
        last_check_age = (now - last_check_ts) if last_check_ts else None

        # 4-input v1 score: 100 best, lower = worse. Deterministic, clamped.
        score = 100
        score -= _color_penalty.get(color, 10)        # (1) color
        score -= min(failures * 5, 40)                # (2) failure count
        if median_lifetime is not None and median_lifetime < 3600:
            score -= 10                               # (3) short lifetimes churny
        if last_check_age is not None and last_check_age > 7 * 86400:
            score -= 10                               # (4) stale check
        score = max(0, min(100, score))
        label = ("critical" if score < 40 else
                 "warn" if score < 75 else "ok")
        sites.append({
            "site_id": sid,
            "color": color,
            "failures": failures,
            "successes": successes,
            "fail_rate": fail_rate,
            "by_type": ps.get("by_type", {}),
            "median_lifetime_sec": median_lifetime,
            "last_check_age_sec": last_check_age,
            "health_score": score,
            "health_label": label,
        })
    # Lead with observed failures so an unchecked/no-data auth-health row cannot
    # outrank the failure cluster the F2a report is meant to correlate with.
    # Preserve the existing score/count ordering within each evidence group.
    sites.sort(key=lambda s: (
        s["failures"] == 0, s["health_score"], -s["failures"]))
    return {
        "lookback_days": int(lookback_days),
        "site_count": len(sites),
        "total_failures": fc.get("total_failures", 0),
        "clusters": fc.get("clusters", []),     # F2.1, already desc by count
        "sites": sites,                          # F2.2, worst first
    }


def _safe(fn):
    try:
        return jsonify({"ok": True, "data": fn()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


# routes (thin wrappers over the providers)
@data_layer_bp.route("/api/data/template_health", methods=["GET"])
def d_template_health():
    return _safe(collect_template_analytics)


@data_layer_bp.route("/api/data/capture_analytics", methods=["GET"])
def d_capture_analytics():
    return _safe(collect_capture_analytics)


@data_layer_bp.route("/api/data/capture_diagnostics", methods=["GET"])
def d_capture_diagnostics():
    return _safe(collect_capture_diagnostics)


@data_layer_bp.route("/api/data/replay_validation", methods=["GET"])
def d_replay_validation():
    return _safe(collect_replay_validation)


@data_layer_bp.route("/api/data/storage_tiers", methods=["GET"])
def d_storage_tiers():
    return _safe(collect_storage_tiers)


@data_layer_bp.route("/api/data/queue_analytics", methods=["GET"])
def d_queue_analytics():
    return _safe(collect_queue_analytics)


@data_layer_bp.route("/api/data/release_analytics", methods=["GET"])
def d_release_analytics():
    return _safe(collect_release_analytics)


@data_layer_bp.route("/api/data/kb_analytics", methods=["GET"])
def d_kb_analytics():
    return _safe(collect_kb_analytics)


@data_layer_bp.route("/api/data/vpn_status", methods=["GET"])
def d_vpn_status():
    return _safe(collect_vpn_status)


@data_layer_bp.route("/api/data/secrets_status", methods=["GET"])
def d_secrets_status():
    return _safe(collect_secrets_status)


@data_layer_bp.route("/api/data/workflow_analytics", methods=["GET"])
def d_workflow_analytics():
    return _safe(collect_workflow_analytics)


@data_layer_bp.route("/api/data/dom_recorder_status", methods=["GET"])
def d_dom_recorder_status():
    return _safe(collect_dom_recorder_status)


@data_layer_bp.route("/api/data/browser_status", methods=["GET"])
def d_browser_status():
    return _safe(collect_browser_status)


@data_layer_bp.route("/api/data/deploy_health", methods=["GET"])
def d_deploy_health():
    return _safe(collect_deploy_health)


@data_layer_bp.route("/api/data/site_health", methods=["GET"])
def d_site_health():
    # F2-a: optional ?lookback_days=N (default 7, clamped 1..90)
    from flask import request
    try:
        lb = int(request.args.get("lookback_days", 7))
    except (TypeError, ValueError):
        lb = 7
    lb = max(1, min(lb, 90))
    return _safe(lambda: collect_site_health(lookback_days=lb))


def register_routes(app) -> int:
    app.register_blueprint(data_layer_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("data_layer."))
