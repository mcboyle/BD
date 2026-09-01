"""Background periodic scheduler for the new modules.

Several phases shipped this release expose `run_due()` or `run_scan()`
functions that should fire periodically — saved searches (Phase 93),
bit-rot scans (Phase 105), stale-reservation sweep (Phase 98), etc.

Rather than ask each module to spin up its own thread, this module
runs a single coordinator thread that schedules every recurring task
through one place. The advantages:

  • One thread, one heartbeat — easier to observe + stop cleanly
  • Per-task interval + last-run tracking
  • Failure isolation: one task raising doesn't kill the others
  • Operator visibility via /api/bg/status

The scheduler is a polling loop (no precise timer needed):

  every 60 seconds:
      for each registered task:
          if elapsed >= task.interval:
              try: task.run()
              except: log, continue

Tasks have one of three cadences:
  • fast (60s)   — observe-circuit-breaker decay, etc.
  • mid (3600s)  — saved-searches hourly schedule, ytdlp staleness
  • slow (daily) — bit-rot scan, stale ramdisk cleanup

Tasks can be enabled/disabled via /api/bg/enable/<task> at runtime
without restart — useful when investigating misbehavior or temporarily
quieting noisy alerts.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Callable, Optional


# Per-task state. Lock'd separately so tasks added at runtime don't
# race with the loop.
_lock = threading.RLock()
_tasks: dict = {}  # name → {fn, interval, last_run, last_status, enabled}
_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_started = False

# F6 idle scaling: the coordinator polls fast while there is recent activity
# and backs off to a slower cadence while idle (fewer wakeups). note_activity()
# both refreshes the active window and wakes a sleeping loop on demand. The
# idle cadence is capped so it never exceeds the fastest registered task
# interval (300s) -- an idle back-off must not starve a due task.
ACTIVE_POLL_S = 30
IDLE_POLL_S = 300
IDLE_AFTER_S = 600
_last_activity: float = time.time()
_wake_event = threading.Event()


def idle_poll_interval(last_activity: float, now: float, *,
                       idle_after: float = IDLE_AFTER_S,
                       active_interval: int = ACTIVE_POLL_S,
                       idle_interval: int = IDLE_POLL_S) -> int:
    """The poll cadence to use: ``active_interval`` while activity is recent
    (< ``idle_after`` ago), else ``idle_interval``. Pure."""
    return active_interval if (now - last_activity) < idle_after else idle_interval


def _mark_activity() -> None:
    """Internal: refresh the activity window WITHOUT waking the loop (used by
    _run_one so a task firing keeps the cadence active but does not trigger an
    immediate re-poll)."""
    global _last_activity
    _last_activity = time.time()


def note_activity() -> None:
    """Public activity signal (wake-on-demand): refresh the active window AND
    wake a sleeping coordinator immediately so new work is picked up without
    waiting out an idle back-off."""
    _mark_activity()
    _wake_event.set()


def is_idle(now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    return (now - _last_activity) >= IDLE_AFTER_S


def _wait_next(interval: float) -> bool:
    """Interruptible inter-poll wait: return after ``interval`` seconds, or
    immediately when woken by note_activity()/stop(). Clears the wake flag.
    Returns True iff woken early."""
    woken = _wake_event.wait(interval)
    _wake_event.clear()
    return woken


def register(name: str, fn: Callable, *, interval_seconds: int,
            enabled: bool = True):
    """Register a periodic task. Idempotent — re-registering replaces
    the existing entry, preserving last_run timing so a re-registered
    task doesn't immediately fire."""
    if not name or not callable(fn):
        return
    with _lock:
        existing = _tasks.get(name) or {}
        _tasks[name] = {
            "fn": fn,
            "interval": int(interval_seconds),
            "last_run": existing.get("last_run", 0.0),
            "last_status": existing.get("last_status", "pending"),
            "last_error": existing.get("last_error", ""),
            "last_duration": existing.get("last_duration", 0.0),
            "enabled": bool(enabled),
            "run_count": existing.get("run_count", 0),
        }


def unregister(name: str):
    with _lock:
        _tasks.pop(name, None)


def set_enabled(name: str, enabled: bool) -> bool:
    """Toggle a task on/off without unregistering. Returns True on
    success, False if name unknown."""
    with _lock:
        t = _tasks.get(name)
        if not t:
            return False
        t["enabled"] = bool(enabled)
        return True


def status() -> dict:
    """Snapshot for /api/bg/status."""
    with _lock:
        out = []
        now = time.time()
        for name, t in _tasks.items():
            out.append({
                "name": name,
                "interval_seconds": t["interval"],
                "enabled": t["enabled"],
                "last_run_ts": t["last_run"],
                "last_run_seconds_ago": (now - t["last_run"]) if t["last_run"] else None,
                "next_due_seconds": max(0, t["interval"] - (now - t["last_run"])) if t["last_run"] else 0,
                "last_status": t["last_status"],
                "last_error": t["last_error"],
                "last_duration_seconds": round(t["last_duration"], 3),
                "run_count": t["run_count"],
            })
    return {"running": _thread is not None and _thread.is_alive(),
            "tasks": out, "total": len(out),
            "idle": is_idle(),
            "poll_interval_seconds": idle_poll_interval(_last_activity, time.time()),
            "seconds_since_activity": round(time.time() - _last_activity, 1)}


def _run_one(name: str, task: dict):
    """Execute one task with full isolation. Logs duration + error."""
    started = time.time()
    try:
        task["fn"]()
        task["last_status"] = "ok"
        task["last_error"] = ""
    except Exception as e:
        task["last_status"] = "error"
        task["last_error"] = str(e)[:300]
        sys.stderr.write(f"[bg] task {name} raised: {e}\n")
    finally:
        task["last_run"] = time.time()
        task["last_duration"] = task["last_run"] - started
        task["run_count"] = task.get("run_count", 0) + 1
        _mark_activity()   # a task firing keeps the cadence active (no self-wake)


def _loop():
    """Coordinator thread body. Polls every 30s, fires due tasks."""
    while not _stop_event.is_set():
        try:
            now = time.time()
            with _lock:
                # Snapshot the task dict to avoid holding lock during run
                snapshot = [
                    (name, t) for name, t in _tasks.items()
                    if t["enabled"] and (now - t["last_run"]) >= t["interval"]
                ]
            for name, task in snapshot:
                if _stop_event.is_set():
                    break
                _run_one(name, task)
        except Exception as e:
            sys.stderr.write(f"[bg] loop error: {e}\n")
        # F6 idle scaling: poll fast while active, back off while idle. The
        # wait breaks immediately on note_activity() (new work) or stop().
        interval = idle_poll_interval(_last_activity, time.time())
        _wait_next(interval)


def start():
    """Spin up the coordinator thread. Idempotent — second call is a
    no-op. Safe to call from BD startup."""
    global _thread, _started
    if _started:
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="bd-bg-scheduler")
    _thread.start()
    _started = True


def stop():
    """Signal the coordinator to exit and wait briefly. Idempotent."""
    global _started
    if not _started:
        return
    _stop_event.set()
    _wake_event.set()   # break an idle back-off wait immediately for fast shutdown
    if _thread is not None:
        _thread.join(timeout=5)
    _started = False


def register_default_tasks(*, s_cfg_getter: Optional[Callable] = None,
                          runners_getter: Optional[Callable] = None,
                          capture_enqueue_fn: Optional[Callable] = None):
    """Register the canonical periodic tasks that the new modules
    expose. Idempotent — safe to call from BD startup.

    `s_cfg_getter` is a callable returning the current s_cfg dict.
    `runners_getter` is a callable returning the current runners dict.
    Tasks that need site config (ramdisk cleanup, etc.) call it
    lazily so the scheduler doesn't pin a stale snapshot."""

    # Saved searches: re-run due queries every 5 minutes; the
    # schedule field inside each search controls how often it
    # actually fires (hourly/daily/weekly/manual).
    def _run_saved_searches():
        from . import saved_searches as _ss
        _ss.run_due()

    register("saved_searches.run_due", _run_saved_searches,
             interval_seconds=300)

    # Bit-rot scan: nightly. 24h cadence respects the operator's
    # IO budget; the run_scan default scans 5% of the library per run.
    def _run_bitrot():
        from . import bitrot as _br
        from . import library_final as _lf
        # Read the roots LAZILY, like every other task here, so the scheduler
        # does not pin a snapshot taken before the operator configured a site.
        # Without them every relative row resolves to "unknown" and the scan
        # decides nothing -- which is what it did until v3.66.930.
        roots = _lf.download_roots(s_cfg_getter()) if s_cfg_getter else []
        _br.run_scan(scan_fraction=0.05, min_age_days=7, max_files=100,
                     download_dirs=roots)

    register("bitrot.nightly_scan", _run_bitrot,
             interval_seconds=86400)

    # RAM-disk: clean up orphaned reservations every 4 hours.
    def _run_ramdisk_cleanup():
        from . import ramdisk_stage as _rd
        _rd.stale_cleanup(max_age_seconds=86400)

    register("ramdisk.stale_cleanup", _run_ramdisk_cleanup,
             interval_seconds=14400)

    # yt-dlp staleness check (Phase 87): report status hourly. Doesn't
    # auto-update (operator-initiated only); just refreshes the cache
    # so /api/ytdlp_status is current.
    def _run_ytdlp_check():
        from . import ytdlp_updater as _yt
        _yt.current_version()  # bypasses cache only if >1h old

    register("ytdlp.refresh_status", _run_ytdlp_check,
             interval_seconds=3600)

    # A5 template-lifecycle drift sweep: daily. The task fn is TOGGLE-GATED
    # (lifecycle_drift.scheduled_sweep no-ops unless automation.drift_sweep_enabled
    # is on, default OFF), so registering it is behaviour-neutral by default —
    # it costs one global_config read per day until an operator opts in. Its
    # mutating responses (flag/quarantine) are each further gated by their own
    # default-OFF toggles, and the download-affecting ones also require the
    # backup-before-overwrite keystone.
    def _run_drift_sweep():
        from . import lifecycle_drift as _ld
        _ld.scheduled_sweep()

    register("lifecycle.drift_sweep", _run_drift_sweep,
             interval_seconds=86400)

    # F3.3 template canary: daily synthetic-fixture replay over enabled
    # templates; alerts when a site's pass-rate drops vs its last good run.
    # TOGGLE-GATED (scheduled_canary no-ops unless automation.template_canary_enabled
    # is on, default OFF), so registering it is behaviour-neutral by default —
    # one global_config read per day until an operator opts in. Read/alert only;
    # never touches an enabled template. Pure local replay (no HTTP), so it can't
    # compete with real jobs.
    def _run_template_canary():
        from . import template_canary as _tc
        _cfg = s_cfg_getter() if s_cfg_getter else None
        _tc.scheduled_canary(s_cfg=_cfg)

    register("template_canary.daily", _run_template_canary,
             interval_seconds=86400)

    # F3.2 drift -> AI repair candidate: daily sweep of selector_drift's
    # flagged-stale sites; for those with available page context it asks the
    # AI diff-repair path for replacement selectors and lands them as
    # REVIEW-ONLY drafts (never enables). TOGGLE-GATED (scheduled_drift_repair
    # no-ops unless automation.drift_repair_enabled is on, default OFF) so
    # registering it is behaviour-neutral; AI-down is inert; off the hot path.
    def _run_drift_repair():
        from . import drift_repair as _dr
        _dr.scheduled_drift_repair()

    register("drift_repair.daily", _run_drift_repair,
             interval_seconds=86400)

    # F2.4 daily ops digest: once a day, assemble a small set of ops
    # counters (timeline volume, error/warning counts, drafts-pending),
    # compare to the previous digest's snapshot, and fire ONE apprise
    # notification — but only on a non-zero delta (a quiet day stays
    # silent). TOGGLE-GATED (scheduled_digest no-ops unless
    # automation.daily_digest_enabled is on, default OFF), so registering
    # it is behaviour-neutral by default — one global_config read per day
    # until an operator opts in. Read-only + fail-soft; a missing notifier
    # is swallowed, so it can't break the loop or compete with real jobs.
    def _run_daily_digest():
        from . import daily_digest as _dd
        _dd.scheduled_digest()

    # A-PIPE / A9 (v3.66.708): the capstone. run_host_cycle -- the ONE function that
    # executes an autonomous cycle -- was called by NOTHING: the engine was built and
    # never connected. This registers the checkpointed chain (capture -> build -> lint ->
    # blocked_scan -> drift_stage -> apply) that runs THROUGH it, so the off-switch, the
    # trust gates, the audit trail and the X-AUTO-2 ceiling all apply. TOGGLE-GATED
    # (scheduled_pipeline no-ops unless automation.pipeline_enabled is on, default OFF),
    # so registering it is behaviour-neutral. Mutating stages are A0-backed and
    # fail-closed: no restorable snapshot -> the stage does not run.
    def _run_automation_pipeline():
        from . import automation_pipeline as _ap
        _ap.scheduled_pipeline()

    register("automation_pipeline.daily", _run_automation_pipeline,
             interval_seconds=86400)

    # A-DISCO (v3.66.788): level-4 enumerate -> triage -> auto-queue. TOGGLE-GATED
    # (disco_runner.scheduled_disco no-ops unless automation.disco_enabled is on,
    # default OFF), so registering it is behaviour-neutral by default -- one
    # global_config read per day until an operator opts in. The master off-switch
    # still dominates each per-site pass, enumeration is bounded (default_safe +
    # AR4 per-run enqueue cap), and discovery stays confined to the approved host.
    def _run_disco():
        from . import disco_runner as _dr
        _cfg = s_cfg_getter() if s_cfg_getter else None
        _run = runners_getter() if runners_getter else None
        _dr.scheduled_disco(s_cfg=_cfg, runners=_run)

    register("disco.scheduled_run", _run_disco,
             interval_seconds=86400)

    register("daily_digest.daily", _run_daily_digest,
             interval_seconds=86400)

    # v3.43.80 Phase 131: cookie re-login proactivity. Hourly, score
    # every site's cookie jar and flag jars dropping below threshold.
    # Doesn't actually drive the re-login — just sets needs_relogin
    # on the runner so the keeper picks it up next iteration.
    def _run_cookie_relogin():
        if not s_cfg_getter:
            return
        from . import cookie_relogin as _cr
        # runners_getter is optional; pass None if not provided
        runners = runners_getter() if runners_getter else {}
        _cr.check_and_schedule(s_cfg=s_cfg_getter(), runners=runners)

    register("cookie_relogin.hourly_check", _run_cookie_relogin,
             interval_seconds=3600)

    # v3.43.80 Phase 154: site weather probing. Every 15 minutes,
    # DNS + HTTP probe each site's homepage. Results land in
    # site_weather_log; UI shows red/yellow/green per site.
    def _run_site_weather():
        if not s_cfg_getter:
            return
        from . import site_weather as _sw
        _sw.probe_all(s_cfg_getter())

    register("site_weather.probe_all", _run_site_weather,
             interval_seconds=900)

    # v3.43.80 Phase 146: alerts engine evaluation. Every minute,
    # evaluate all rules against current metrics. Cheap (no DB
    # writes unless a rule trips). Fires plugin/webhook side effects
    # via the rule's actions list.
    # ROW 421: this wrapper DISCARDED evaluate()'s dict, so a broken
    # alert_events table produced fired=0 that was observed by no one -- the
    # last link in a silence that started at the DB boundary. The result is
    # now read, and an UNKNOWN pass is raised into _run_one's existing
    # per-task error channel, which /api/bg/status already surfaces as
    # last_status/last_error. A raise is right here: the alerting layer could
    # not do its job, and that is exactly what "error" means for a task.
    def _run_alerts():
        if not s_cfg_getter:
            return None
        from . import alerts_engine as _ae
        report = _ae.evaluate(s_cfg=s_cfg_getter())
        unknown = report.get("unknown", 0)
        if unknown:
            detail = "; ".join(
                f"{r.get('rule_id')}: {r.get('error', '')}"
                for r in report.get("results", [])
                if r.get("store_unavailable"))
            raise RuntimeError(
                f"UNKNOWN for {unknown} rule(s): {detail}")
        return report

    register("alerts_engine.evaluate", _run_alerts,
             interval_seconds=60)

    # v3.43.80 Phase 135: incremental discovery. Every 30 minutes,
    # poll RSS/sitemap for every site with discovery.enabled=true.
    # New URLs land in the queue via the configured enqueue_fn —
    # which here is None (scheduler doesn't have direct queue access);
    # operators call /api/discovery/run for actual enqueue.
    def _run_discovery():
        if not s_cfg_getter:
            return
        from . import discovery as _disc
        _disc.discover_all(s_cfg_getter())

    register("discovery.poll_feeds", _run_discovery,
             interval_seconds=1800)

    # v3.43.80 Phase 160: maintenance window transitions. Every
    # minute, detect windows that just started/ended and fire
    # webhooks. Idempotent — internal state tracks already-fired.
    def _run_maintenance():
        from . import maintenance as _mw
        _mw.detect_transitions()

    register("maintenance.detect_transitions", _run_maintenance,
             interval_seconds=60)

    # v3.43.80 Phase 139: scheduled exports. Every 5 minutes, check
    # for schedules whose next_run_ts is due and run them.
    def _run_sched_exports():
        from . import scheduled_exports as _se
        if s_cfg_getter:
            _se.run_due_exports(s_cfg=s_cfg_getter())
        else:
            _se.run_due_exports()

    register("scheduled_exports.run_due", _run_sched_exports,
             interval_seconds=300)

    # Cut 8: recurring-capture schedules. Enqueues due captures via the
    # existing run path (SiteRunner.load_urls). The enqueue_fn is provided
    # by the app's start_default_tasks wiring; if absent (e.g. headless
    # tooling), the sweep is a no-op rather than a crash.
    def _run_capture_schedules():
        from . import capture_schedules as _cs
        fn = capture_enqueue_fn
        if fn is None:
            return
        _cs.run_due(enqueue_fn=fn)

    register("capture_schedules.run_due", _run_capture_schedules,
             interval_seconds=300)

    # v3.47.0 Phase 197: cookie health monitor. Nightly check of each
    # site's stored cookies — pings the auth_check_url (or fallback)
    # and classifies the response as green/yellow/red. Operator sees
    # the result in the Review tab + can manually trigger a re-check.
    # 24h cadence + only_if_stale guards against the scheduler
    # double-firing within a single day.
    def _run_cookie_health():
        if not s_cfg_getter:
            return
        from . import cookie_health as _ch
        _ch.check_all_sites(s_cfg_getter(), only_if_stale=True)

    register("cookie_health.nightly_check", _run_cookie_health,
             interval_seconds=86400)

    # v3.43.80 Phase 142: VPN endpoint auto-blacklist. Every 5
    # minutes, sweep recent failures and blacklist (profile, site)
    # pairs that have failed too often.
    def _run_vpn_blacklist():
        from . import vpn_stats as _vs
        _vs.auto_blacklist_check(fail_threshold=5, window_minutes=30)

    register("vpn_stats.auto_blacklist", _run_vpn_blacklist,
             interval_seconds=300)

    # v3.43.80 Phase 120: federation claim expiry sweep. Every minute,
    # remove expired URL claims so peers can re-take URLs whose
    # holder crashed mid-download.
    def _run_fed_expire():
        from . import federation as _fed
        _fed.expire_old_claims()

    register("federation.expire_claims", _run_fed_expire,
             interval_seconds=60)

    # v3.43.93 Phase 173: per-site retention sweep. Once a day, run
    # the configured retention policy across all sites — DRY-RUN
    # ONLY by default. The audit log shows what WOULD be deleted;
    # operator promotes to real deletes via the UI's "Apply" button.
    # This is intentional: silent auto-deletion is a footgun, even
    # with a 'enabled' flag. We surface the candidate list and let
    # the operator confirm.
    def _run_retention_dry():
        if not s_cfg_getter:
            return
        cfg = s_cfg_getter() or {}
        # Skip the sweep entirely if no site has any retention config
        has_policy = any(
            (c.get("retention_days") or 0) > 0
            or (c.get("retention_max_gb") or 0) > 0
            for c in cfg.values())
        if not has_policy:
            return
        from . import retention as _rt
        _rt.apply_retention(s_cfg=cfg, dry_run=True)

    register("retention.daily_sweep_dry", _run_retention_dry,
             interval_seconds=86400)

    # Phase 1 Cut 1.4: capture-retention sweep (over the db `captures` index).
    # DRY-RUN only, and skipped entirely unless the operator has configured a
    # capture-retention rule -- mirrors the download sweep above. Captures are
    # keep-forever by default (RETENTION_AND_TAKEDOWN_POLICY.md), so this is a
    # no-op until opted in. Policy is read from the global app config.
    def _run_capture_retention_dry():
        try:
            from . import global_config as _gc
            from . import retention as _rt
        except Exception:
            return
        policy = {
            "capture_ttl_days": _gc.get("capture_ttl_days", 0),
            "capture_max_gb": _gc.get("capture_max_gb", 0),
            "capture_keep_n_per_host": _gc.get("capture_keep_n_per_host", 0),
        }
        if not _rt._capture_policy_active(policy):
            return  # keep-forever default: nothing configured, nothing to do
        _rt.apply_capture_retention(policy, dry_run=True)

    register("retention.capture_daily_sweep_dry", _run_capture_retention_dry,
             interval_seconds=86400)
