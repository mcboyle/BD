"""health API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/health{,/checklist,/v2} views moved onto a Flask
Blueprint. Endpoint labels gain a "health." prefix; the (rule, methods,
bare-name) routing surface is byte-identical (test_route_map_invariant diffs
empty).

Shared state (runners, s_cfg, _app_boot_time) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by
reference). app_test_mode() delegates to app at call time. db_conn / healthcheck
/ __version__ are sibling-package names imported directly (identical objects).
"""
from __future__ import annotations

import json
import os
import time

from flask import Blueprint, jsonify

from .db import db_conn


def _runners_generation(mapping):
    """A stable (sid, runner) list; locked when `mapping` is the live registry.

    Row 634: walking ``app_state.runners`` bare raises ``RuntimeError:
    dictionary changed size during iteration`` the instant a site create or
    delete lands mid-walk, AFTER the loop body has already acted on a prefix of
    the fleet.  Imported lazily (importlib, per call) for the same reason the
    other shared-state accessors here are: no new static import edge.
    """
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"),
                   "runners_generation")(mapping)


health_bp = Blueprint("health", __name__)

def app_test_mode(*_a, **_k):
    """Delegate to app.app_test_mode at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "app_test_mode")(*_a, **_k)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")

def _app_s_cfg():
    """The live shared s_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "s_cfg")

def _app__app_boot_time():
    """The live shared _app_boot_time from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_app_boot_time")


def _app_sites_config_reachability():
    """The last sites-config path measurement from app.py."""
    import importlib
    app_module = importlib.import_module("bulk_downloader.app")
    state = app_module._SITES_CONFIG_REACHABILITY
    return None if state is None else dict(state)


_BUILD_IDENTITY_CACHE: dict[str, dict] = {}


def build_identity(install_dir: str | os.PathLike) -> dict:
    """What build is actually running here, and how we know.

    Returns ``{"sha", "built_at", "source"}`` where source is ``git``,
    ``build_info.json`` or ``unknown``.

    WHY GIT FIRST. ``build_info.json`` is written by exactly one thing --
    ``tools/build_release.py``, during a zip build. The box no longer builds
    zips; it deploys with ``git reset --hard`` + restart, and nothing on that
    path touches the file. So it holds whatever the last zip build left. The
    value found on the live box was ``a8881d9d471c`` from 2026-07-19, which
    ``git cat-file`` cannot resolve at all -- it is a release-zip digest, not
    a commit. Three documents tell the reader to confirm /api/health before
    trusting a post-deploy test run, so the endpoint they are told to trust was
    reporting an identity frozen in the past and not addressable in the
    present.

    A checkout knows its own commit, so derive rather than re-stamp: a derived
    answer cannot go stale, and re-stamping would only move the staleness to
    whoever forgets to run the stamper.

    ``source`` is not decoration. A fallback to a recorded file is
    indistinguishable from a live read unless it says so, and that
    indistinguishability is precisely how the stale value went unnoticed.

    Cached per install dir: the deployed commit changes only on a deploy, and
    a deploy restarts the service, which clears this.

    ROW 521 -- ONLY A SUCCESSFUL MEASUREMENT IS CACHED. That justification is
    a property of a measurement that WORKED, and the write below used to be
    unconditional, so the failure value (sha None, source unknown) was
    retained exactly like a success. Two disjoint routes reach it: an
    exception from the git subprocess (git absent, the timeout elapsing under
    deploy load, an OSError from fork), and a nonzero returncode that never
    raises at all (a work tree git refuses -- dubious ownership exits 128).
    ``api_health`` attaches the block only when a sha was obtained, so one
    transient failure made /api/health answer 200 ok true with NO build key --
    indistinguishable from a pre-B1.3 build -- until a restart, the very event
    that created the state. A measurement that failed is not evidence.

    The re-measurement cost is bounded and deliberate: a genuinely non-build
    install dir pays one refused ``git rev-parse`` per health request rather
    than caching an answer it never obtained.
    """
    root = str(install_dir)
    cached = _BUILD_IDENTITY_CACHE.get(root)
    if cached is not None:
        return cached

    result = {"sha": None, "built_at": None, "source": "unknown"}
    try:
        import subprocess
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True, timeout=10)
        if proc.returncode == 0 and proc.stdout.strip():
            sha = proc.stdout.strip()
            when = subprocess.run(["git", "log", "-1", "--format=%cI"], cwd=root,
                                  capture_output=True, text=True, timeout=10)
            result = {"sha": sha[:12],
                      "built_at": when.stdout.strip() or None,
                      "source": "git"}
    except Exception:  # why: git absent or not a work tree; fall through to the file
        pass

    if result["source"] == "unknown":
        try:
            path = os.path.join(root, "build_info.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and data.get("sha"):
                    result = {"sha": data.get("sha"),
                              "built_at": data.get("built_at"),
                              "source": "build_info.json"}
        except Exception:  # why: unreadable/malformed file is not evidence; stay unknown
            pass

    if result["source"] != "unknown":
        _BUILD_IDENTITY_CACHE[root] = result
    return result


def _runner_queue_counts(status: dict) -> tuple[int, int]:
    """Return pending/running counts from current or legacy runner status."""
    counts = status.get("counts")
    if not isinstance(counts, dict):
        counts = {}
    pending = counts.get("pending")
    running = counts.get("running")
    return (
        int((status.get("queued") if pending is None else pending) or 0),
        int((status.get("active") if running is None else running) or 0),
    )


def _unknown_credential_health(*, backend, initialized, unlocked, references):
    """A7: unavailable measurements are UNKNOWN, never zero-shaped OK."""
    return {
        "backend": backend,
        "is_initialized": initialized,
        "is_unlocked": unlocked,
        "missing_count": None,
        "ok": False,
        "reference_count": (len(references) if references is not None else None),
        "resolved_count": None,
        "state": "unknown",
        "stored_count": None,
        "unavailable_count": None,
    }


def credential_health(sites_config: dict | None) -> dict:
    """Measure password-reference availability without exposing values."""
    from . import secrets_store as ss

    backend_name = None
    initialized = None
    unlocked = None
    references = None
    try:
        references = ss.password_reference_keys(sites_config)
        backend = ss.get_backend()
        backend_name = getattr(backend, "name", "unknown")
        initialized_fn = getattr(backend, "is_initialized", None)
        initialized = bool(initialized_fn()) if callable(initialized_fn) else True
        unlocked = bool(backend.is_unlocked())
        # Row 432: an unreadable durable store is an unavailable measurement,
        # not a state. Classify it before any reference branching --
        # a damaged vault with zero references otherwise reached
        # "locked_no_references" and published ok=True over a store nothing
        # had read (CLAUDE.md A7).
        state_fn = getattr(backend, "store_state", None)
        if callable(state_fn) and state_fn() == "unreadable":
            return _unknown_credential_health(
                backend=backend_name,
                initialized=initialized,
                unlocked=unlocked,
                references=references,
            )
        # Enumeration is independently required: without it, None from get()
        # cannot be classified as missing versus unreadable.
        stored_keys = list(backend.list_keys())
    except Exception:
        return _unknown_credential_health(
            backend=backend_name,
            initialized=initialized,
            unlocked=unlocked,
            references=references,
        )

    if unlocked and not initialized:
        # No key can be trusted as "unlocked" unless durable material commits
        # the password it was derived from.  Refuse this incoherent pair as an
        # unavailable measurement rather than resolving through it.
        return _unknown_credential_health(
            backend=backend_name,
            initialized=initialized,
            unlocked=unlocked,
            references=references,
        )

    base = {
        "backend": backend_name,
        "is_initialized": initialized,
        "is_unlocked": unlocked,
        "reference_count": len(references),
        "stored_count": len(stored_keys),
    }
    if not initialized:
        # This is first-use setup, not credential deletion.  Classify it before
        # examining references so refs=1/stored=0 cannot become "missing".
        # Zero consumers do not make the absent durable password commitment
        # ready: after a restart this vault would accept a new first password.
        return {
            **base,
            "missing_count": len(references),
            "ok": False,
            "resolved_count": 0,
            "state": "uninitialized",
            "unavailable_count": 0,
        }

    stored = set(stored_keys)
    if not unlocked:
        if not references:
            # A locked vault cannot make credentials unavailable when the live
            # configuration asks for none.  Stored but unreferenced keys are
            # inventory, not a serving dependency, so this is explicitly
            # healthy instead of a misleading credential_vault_locked 503.
            return {
                **base,
                "missing_count": 0,
                "ok": True,
                "resolved_count": 0,
                "state": "locked_no_references",
                "unavailable_count": 0,
            }
        # list_keys is deliberately available while the master key is locked,
        # so missing references remain measurable even though values cannot be
        # decrypted.  Hardcoding missing_count=0 here launders an absent key
        # into the ordinary post-restart lock state that deploy.sh may accept.
        missing_count = sum(ref not in stored for ref in references)
        unavailable_count = len(references) - missing_count
        return {
            **base,
            "missing_count": missing_count,
            "ok": False,
            "resolved_count": 0,
            "state": ("missing_credentials" if missing_count else "locked"),
            "unavailable_count": unavailable_count,
        }

    resolved_count = 0
    missing_count = 0
    unavailable_count = 0
    for ref in references:
        if ref not in stored:
            missing_count += 1
            continue
        try:
            value = backend.get(ref)
        except Exception:
            return _unknown_credential_health(
                backend=backend_name,
                initialized=initialized,
                unlocked=unlocked,
                references=references,
            )
        if value is None:
            unavailable_count += 1
        else:
            resolved_count += 1

    if unavailable_count:
        state = "unknown"
        ok = False
    elif references and resolved_count == 0:
        # Initialized and unlocked, but no configured reference resolved.  It
        # is neither the ordinary restart lock nor a partially missing vault,
        # and deploy must never accept it as either one.
        state = "unlocked_zero_resolved"
        ok = False
    elif missing_count:
        state = "missing_credentials"
        ok = False
    else:
        state = "unlocked"
        ok = True
    return {
        **base,
        "missing_count": missing_count,
        "ok": ok,
        "resolved_count": resolved_count,
        "state": state,
        "unavailable_count": unavailable_count,
    }


def _attach_credential_health(payload: dict, sites_config: dict | None) -> None:
    """Row 413: vault readiness gets its OWN named field.

    ``ok``/``degraded`` are a conjunction over every subsystem, so they cannot
    answer "is the vault ready?" or "is downloading held?" separately -- an
    operator reading a stopped host could not tell a deliberate download hold
    from an uninitialised or locked vault, which is the confusion row 408
    removed from the deploy path. ``download_hold.downloads_allowed`` already
    reports the HOLD ALONE; ``vault_ready`` is the vault's own half, set on
    BOTH branches so a ready vault says so positively rather than by the
    absence of a degradation.
    """
    credentials = credential_health(sites_config)
    payload["credentials"] = credentials
    payload["vault_ready"] = credentials["ok"] is True
    if credentials["ok"]:
        return
    payload["ok"] = False
    degraded = {
        "locked": "credential_vault_locked",
        "missing_credentials": "credential_missing",
        "uninitialized": "credential_vault_uninitialized",
        "unlocked_zero_resolved": "credential_unlocked_zero_resolved",
        "unknown": "credential_state_unknown",
    }.get(credentials["state"], "credential_state_unknown")
    payload.setdefault("degraded", degraded)


def _attach_download_hold(payload: dict) -> None:
    """Row 390: make a held host SAY SO, distinctly from idle / empty queue.

    A deliberate hold keeps ``ok`` true. /api/health is what scripts/deploy.sh
    verifies after a restart, and the fleet is deliberately held right now -- a
    hold that turned every held host 503 would report the correct state as a
    failed deploy. UNKNOWN is a genuine degradation (the hold state could not be
    measured, so downloads are refused without an operator knowing why) and
    follows the credential-health precedent: ok=False plus a degraded marker.
    """
    from . import download_hold as _dh
    try:
        state = _dh.hold_state()
    except Exception:  # pragma: no cover - hold_state never raises
        state = {"state": _dh.UNKNOWN, "reason": "health_probe_failed",
                 "detail": "", "since": None, "note": "", "by": ""}
    payload["download_hold"] = _dh.health_block(state)
    if state.get("state") == _dh.UNKNOWN:
        payload["ok"] = False
        payload.setdefault("degraded", "download_hold_unknown")


def _attach_sites_config_health(payload: dict) -> None:
    """Expose an unavailable published config path as UNKNOWN, never empty OK."""
    state = _app_sites_config_reachability()
    if state is None:
        return
    payload["sites_config"] = state
    if state.get("ok") is not True:
        payload["ok"] = False
        payload.setdefault("degraded", "sites_config_unknown")


@health_bp.route("/api/health")
def api_health():
    _app_boot_time = _app__app_boot_time()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    import sqlite3 as _sqlite3
    from . import __version__ as _bd_version
    payload = {
        "ok": True,
        "version": _bd_version,
        "uptime_s": round(time.time() - _app_boot_time, 1),
        "test_mode": app_test_mode(),   # v3.66.317: advisory only (no behavior effect)
    }
    # Queue depth across all sites. Sum-then-report; never iterate twice.
    try:
        total_queued = 0
        total_active = 0
        for _sid, r in _runners_generation(runners):
            try:
                st = r.get_status(light=True)
                pending, running = _runner_queue_counts(st)
                total_queued += pending
                total_active += running
            except Exception:
                # Per-runner status failure shouldn't fail the health probe
                # itself — we report what we can and flag the discrepancy.
                payload["ok"] = False
                payload["degraded"] = "runner_status_error"
        payload["queue_depth"] = total_queued
        payload["active_downloads"] = total_active
        payload["sites_loaded"] = len(runners)
    except Exception as e:
        payload["ok"] = False
        payload["degraded"] = f"queue_query_failed: {type(e).__name__}"
    # DB liveness — single read against a known-cheap table. If the DB
    # is locked or the file is missing, this fails fast and we report ok=False.
    try:
        with db_conn() as cx:
            cx.execute("SELECT 1").fetchone()
        payload["db_ok"] = True
    except _sqlite3.Error as e:
        payload["ok"] = False
        payload["db_ok"] = False
        payload["degraded"] = f"db_error: {type(e).__name__}"
    _attach_credential_health(payload, s_cfg)
    _attach_download_hold(payload)
    _attach_sites_config_health(payload)
    # B1.3 (post-365): build identity. Read build_info.json from the install
    # dir so the Dashboard can compare the FE-loaded VITE_BUILD_STAMP against
    # the backend build sha. Absent file -> no `build` key (graceful: dev tree
    # or a pre-B1.3 build). Never fails the probe.
    try:
        _bi_dir = os.environ.get("BD_INSTALL_DIR") or os.path.dirname(os.path.dirname(__file__))
        _build = build_identity(_bi_dir)
        if _build.get("sha"):
            payload["build"] = _build
    except Exception:
        pass  # build identity is advisory — never break the health probe
    return jsonify(payload), (200 if payload["ok"] else 503)
@health_bp.route("/api/health/checklist")
def api_health_checklist():
    s_cfg = _app_s_cfg()
    try:
        from . import healthcheck as _hc
        return jsonify(_hc.run_checklist(s_cfg=s_cfg))
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
@health_bp.route("/api/health/v2")
def api_health_v2():
    """SPA-shaped full health surface. Superset of /api/health — adds
    WAL mode, disk free per download dir, Ollama reachability check
    (cached for 30s to avoid hammering the backend), VPN detection
    (best-effort), and last-suite timestamp."""
    _app_boot_time = _app__app_boot_time()
    runners = _app_runners()
    s_cfg = _app_s_cfg()
    import sqlite3 as _sqlite3
    import time as _t
    from . import __version__ as _bd_version
    payload = {
        "ok": True,
        "version": _bd_version,
        "uptime_s": round(_t.time() - _app_boot_time, 1),
    }
    # Queue + DB liveness — same logic as /api/health.
    try:
        total_queued = total_active = 0
        for _sid, r in _runners_generation(runners):
            try:
                st = r.get_status(light=True)
                pending, running = _runner_queue_counts(st)
                total_queued += pending
                total_active += running
            except Exception:
                payload["ok"] = False
                payload.setdefault("degraded", "runner_status_error")
        payload["queue_depth"] = total_queued
        payload["active_downloads"] = total_active
        payload["sites_loaded"] = len(runners)
    except Exception as e:
        payload["ok"] = False
        payload["degraded"] = f"queue_query_failed: {type(e).__name__}"
    # DB liveness + WAL mode + integrity_check result (cached on app)
    try:
        with db_conn() as cx:
            cx.execute("SELECT 1").fetchone()
            wal_row = cx.execute("PRAGMA journal_mode").fetchone()
            wal_mode = (wal_row[0] if wal_row else "unknown").lower()
        payload["db_ok"] = True
        payload["db_journal_mode"] = wal_mode
    except _sqlite3.Error as e:
        payload["ok"] = False
        payload["db_ok"] = False
        payload["degraded"] = f"db_error: {type(e).__name__}"
        payload["db_journal_mode"] = "unknown"
    _attach_credential_health(payload, s_cfg)
    _attach_download_hold(payload)
    _attach_sites_config_health(payload)
    # Disk free per download dir — first 5 only (mockup shows
    # aggregate, not per-dir; this is for the Settings → Health pane).
    disks = []
    seen_dirs = set()
    for sid, cfg in s_cfg.items():
        dl = (cfg or {}).get("download_dir") or ""
        if dl and dl not in seen_dirs:
            seen_dirs.add(dl)
            try:
                import shutil as _shutil
                u = _shutil.disk_usage(dl)
                disks.append({
                    "path": dl,
                    "free_gb": round(u.free / (1024 ** 3), 2),
                    "total_gb": round(u.total / (1024 ** 3), 2),
                    "free_pct": round((u.free / u.total) * 100, 1),
                })
            except Exception:
                pass
        if len(disks) >= 5:
            break
    payload["disks"] = disks
    # Ollama reachability — cached 30s. The cache attribute lives on
    # the function so it survives between requests within a process.
    cache = getattr(api_health_v2, "_ollama_cache", None)
    now = _t.time()
    if cache and (now - cache[0] < 30):
        payload["ollama"] = cache[1]
    else:
        ollama_status = {"reachable": False, "model": None,
                          "error": None}
        try:
            import urllib.request as _ur
            req = _ur.Request("http://127.0.0.1:11434/api/tags")
            with _ur.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    import json as _j
                    body = _j.loads(resp.read())
                    ollama_status["reachable"] = True
                    models = [m.get("name") for m in body.get("models", [])]
                    ollama_status["model"] = models[0] if models else None
        except Exception as e:
            ollama_status["error"] = f"{type(e).__name__}"
        payload["ollama"] = ollama_status
        api_health_v2._ollama_cache = (now, ollama_status)
    # Last-suite timestamp — read from SUMMARY.txt if it exists. The
    # SPA's Health pane displays "Last full suite ran at X" without
    # re-running anything.
    payload["last_suite"] = {"available": False}
    try:
        from pathlib import Path as _P
        summary = _P(__file__).parent.parent / "SUMMARY.txt"
        if summary.is_file():
            payload["last_suite"] = {
                "available": True,
                "mtime_ts": int(summary.stat().st_mtime),
                "size_bytes": summary.stat().st_size,
            }
    except Exception:
        pass
    return jsonify(payload), (200 if payload["ok"] else 503)

def register_routes(app) -> int:
    app.register_blueprint(health_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("health."))
