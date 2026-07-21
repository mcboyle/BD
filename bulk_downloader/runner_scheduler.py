"""runner_scheduler -- auto-retry loops, subscriptions, scheduler, rl persistence

Extracted from runner.py (SiteRunner) @v3.66.401, PHASE 3 runner cut 4.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies. Cycle rule: kernel from .runner_util,
nothing from .runner.
"""
import json, os, sys, threading, time
from datetime import datetime, timedelta

from .runner_util import _ts
from .db import queue_upsert


class SchedulerMixin:
    def _start_auto_retry(self):
        """Spin up the auto-retry scanner thread if not already running."""
        if self._auto_retry_thread and self._auto_retry_thread.is_alive():
            return
        self._auto_retry_stop.clear()
        self._auto_retry_thread = threading.Thread(
            target=self._auto_retry_loop, daemon=True,
            name=f"auto-retry-{self.site_id}")
        self._auto_retry_thread.start()
    def _stop_auto_retry(self):
        """Signal the auto-retry thread to exit and wait up to 2s for it.
        Idempotent — safe to call even if the thread isn't running."""
        self._auto_retry_stop.set()
        t = self._auto_retry_thread
        if t and t.is_alive():
            t.join(timeout=2)
        self._auto_retry_thread = None
    def _parse_retry_schedule(self, schedule_str):
        """Parse '1h,4h,24h' → [3600, 14400, 86400] in seconds. Tolerates
        whitespace, mixed units, malformed entries (which are skipped)."""
        if not schedule_str: return [3600, 14400, 86400]  # default 1h, 4h, 24h
        out = []
        for tok in str(schedule_str).split(","):
            tok = tok.strip().lower()
            if not tok: continue
            try:
                if tok.endswith("d"):    out.append(int(float(tok[:-1]) * 86400))
                elif tok.endswith("h"):  out.append(int(float(tok[:-1]) * 3600))
                elif tok.endswith("m"):  out.append(int(float(tok[:-1]) * 60))
                elif tok.endswith("s"):  out.append(int(float(tok[:-1])))
                else:                    out.append(int(float(tok)))  # bare number = seconds
            except Exception:
                continue
        return out or [3600, 14400, 86400]
    def _auto_retry_loop(self):
        """Scan for retry-eligible jobs every 60s. Bumps stuck
        needs_review/failed jobs back to pending according to the
        per-site schedule. Caps via auto_retry_max_attempts so we don't
        burn cycles forever on truly broken URLs.

        Phase 63 (v3.38.x): also runs the pre-emptive relogin check here
        — it's already a once-a-minute loop, no need for another thread.

        Phase 73 (v3.41.0): same loop now also scans RSS-style URL
        subscriptions — watch a listing page on a schedule, auto-import
        new URLs into this site's queue."""
        # First sleep — gives the runner a moment to settle after start
        self._auto_retry_stop.wait(30)
        while not self._auto_retry_stop.is_set():
            try:
                self._auto_retry_scan()
            except Exception as e:
                self.log.exception("auto_retry scan failed: %s", e)
            try:
                self.maybe_preemptive_relogin()
            except Exception as e:
                self.log.exception("preemptive relogin check failed: %s", e)
            try:
                self._scan_subscriptions()
            except Exception as e:
                self.log.exception("subscription scan failed: %s", e)
            self._auto_retry_stop.wait(60)
    def _scan_subscriptions(self):
        """Phase 73 (v3.41.0): RSS-style URL subscriptions. Each subscription
        is a {name, url, interval_hours, last_run_ts} entry in the site's
        config['subscriptions'] list. When interval_hours has elapsed
        since last_run_ts, we scrape the listing URL (server-side, via
        the same heuristic as /api/scrape_listing), dedup against the
        queue's existing URLs + history, and add the new ones.

        last_run_ts is persisted back to config so the schedule survives
        restart. The subscription itself never gets added to the queue
        — only the URLs it discovers."""
        subs = self.config.get("subscriptions") or []
        if not isinstance(subs, list) or not subs:
            return
        import time as _t
        now = _t.time()
        # Snapshot existing URLs to dedup against
        with self._lock:
            existing_urls = set(self.urls)
        dirty = False
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            sub_url = (sub.get("url") or "").strip()
            interval_h = float(sub.get("interval_hours") or 24.0)
            last_run = float(sub.get("last_run_ts") or 0.0)
            name = sub.get("name") or sub_url
            if not sub_url:
                continue
            if now - last_run < interval_h * 3600.0:
                continue
            # Time to scan this subscription
            self.log_event("subscription", f"Scanning subscription: {name}")
            try:
                found = self._scrape_listing_urls(sub_url)
            except Exception as e:
                self.log.warning("subscription scrape failed for %s: %s", name, e)
                # Mark last_run even on failure so we don't hammer a broken URL
                sub["last_run_ts"] = now
                dirty = True
                continue
            new_urls = [u for u in found if u not in existing_urls]
            if new_urls:
                # Add them through the normal load_urls path so dedup
                # and folder_scan behave consistently.
                try:
                    n_added, _, _ = self.load_urls(new_urls, dedupe=True)
                    self.log_event("subscription",
                        f"Subscription {name}: added {n_added} new URLs ({len(found)} found, "
                        f"{len(found) - n_added} duplicates)")
                except Exception as e:
                    self.log.warning("subscription URL import failed for %s: %s", name, e)
            else:
                self.log_event("subscription",
                    f"Subscription {name}: no new URLs ({len(found)} found, all already known)")
            sub["last_run_ts"] = now
            dirty = True
        # Persist the updated last_run_ts back to disk so the schedule
        # survives a restart. The runner's config is a reference to the
        # in-memory s_cfg dict in app.py, so we just need to trigger
        # a save. We do this lazily — only if we modified anything.
        # AUDIT FIX (v3.42.0): the original code looked for
        # `self._persist_config_cb` which was never set anywhere, so
        # last_run_ts was never persisted and subscriptions re-scanned on
        # every loop after a restart. Call the app-level saver directly.
        if dirty:
            try:
                from .app import _save_sites_config
                _save_sites_config()
            except Exception as e:
                self.log.warning("subscription config persist failed: %s", e)
    def _auto_retry_scan(self):
        """One scan pass. Reads config flags inside the loop so toggle
        changes apply immediately without a runner restart.

        Phase 42 (v3.36.10): SQLite writes (queue_upsert) and event logging
        used to happen inside `with self._lock`. SQLite calls hit disk and
        can block tens to hundreds of ms under load — every other operation
        on this runner stalls behind them. We now collect a list of side
        effects inside the lock and execute them after releasing it.

        v3.43.37: when `auto_retry_classify=True` (default ON for new
        sites), use the retry_policy module to pick per-class backoff
        instead of the uniform schedule. Permanent failures (404/410)
        skip retries entirely; transient failures retry quickly with
        exponential backoff + jitter; rate-limited failures honor
        Retry-After when present; auth failures wait for relogin.
        Falls back to the legacy schedule when classify is off."""
        do_review = bool(self.config.get("auto_retry_review", False))
        do_failed = bool(self.config.get("auto_retry_failed", False))
        if not (do_review or do_failed): return
        max_attempts = int(self.config.get("auto_retry_max_attempts", 3) or 3)
        schedule = self._parse_retry_schedule(self.config.get("auto_retry_schedule", ""))
        # v3.43.37: classification on by default. Existing sites get
        # smart retries without any config change.
        use_classify = bool(self.config.get("auto_retry_classify", True))
        try:
            from . import retry_policy as _rp
        except Exception:
            _rp = None
            use_classify = False
        now = time.time()
        # Pending side-effects collected inside the lock, run after.
        # Each entry: (url, message, prior_status, attempt, failure_class)
        side_effects = []
        with self._job_status_writer() as mark_status_changed:
            mutated = False
            for url, j in self.jobs.items():
                st = j.get("status", "")
                if st == "needs_review":
                    if not do_review: continue
                elif st == "failed":
                    if not do_failed: continue
                else:
                    continue
                attempt = int(j.get("auto_retry_count", 0) or 0)
                # v3.43.37: per-class max_attempts overrides the
                # site-wide cap (a "permanent" class has max=0; the
                # site-wide cap might be 5 but it doesn't matter).
                fail_class = ""
                effective_max = max_attempts
                if use_classify and _rp is not None:
                    fail_class = _rp.classify_failure(
                        message=j.get("message", ""),
                        status_code=j.get("status_code"))
                    class_cfg = _rp.get_class_config(fail_class)
                    # Use the smaller of site_max and class_max
                    effective_max = min(max_attempts,
                                          class_cfg["max_attempts"])
                if attempt >= effective_max:
                    if fail_class == "permanent" and attempt == 0:
                        # First-time encounter with a permanent failure
                        # — log it ONCE so the user can see why it's
                        # not retrying, then mark to skip future scans.
                        j["next_auto_retry_at"] = -1  # sentinel: never
                        mutated = True
                    continue
                # Determine delay
                if use_classify and _rp is not None:
                    retry_after_hdr = j.get("retry_after_header")
                    delay = _rp.compute_next_delay(
                        fail_class, attempt,
                        retry_after_header=retry_after_hdr)
                else:
                    # Legacy uniform-schedule path
                    delay = schedule[min(attempt, len(schedule) - 1)]
                if delay == 0:
                    # Class says no retry (e.g. permanent) — skip
                    continue
                next_at = float(j.get("next_auto_retry_at", 0) or 0)
                if next_at == 0:
                    # First time seeing this stuck job — set the timer
                    j["next_auto_retry_at"] = now + delay
                    mutated = True
                    continue
                if next_at < 0: continue   # sentinel for "permanent, give up"
                if now < next_at: continue
                # Time to retry — bump back to pending
                j["status"] = "pending"
                # Build the message describing what we're doing AND
                # what would happen on next failure
                if use_classify and _rp is not None:
                    next_delay = _rp.compute_next_delay(
                        fail_class, attempt + 1,
                        retry_after_header=j.get("retry_after_header"))
                    if next_delay > 0:
                        msg = (f"Auto-retry {attempt+1}/{effective_max} "
                               f"[{fail_class}] (was {st}; next in "
                               f"{self._fmt_dur(next_delay)} if it fails)")
                    else:
                        msg = (f"Auto-retry {attempt+1}/{effective_max} "
                               f"[{fail_class}] (last attempt — class "
                               f"cap reached)")
                else:
                    next_delay = schedule[min(attempt + 1, len(schedule) - 1)]
                    msg = (f"Auto-retry {attempt+1}/{effective_max} "
                           f"(was {st}; next in {self._fmt_dur(next_delay)} "
                           f"if it fails again)")
                j["message"] = msg
                j["ts"] = _ts()
                j["auto_retry_count"] = attempt + 1
                j["retries"] = 0
                j["retry_after"] = 0
                # Set the next retry timer
                j["next_auto_retry_at"] = (
                    now + next_delay if next_delay > 0 else -1)
                side_effects.append(
                    (url, msg, st, attempt, fail_class))
                mutated = True
            if mutated:
                mark_status_changed()
        # Release lock before doing SQLite + logging — these can be slow
        for url, msg, prior_status, attempt, fail_class in side_effects:
            try:
                queue_upsert(self.site_id, url, status="pending",
                             message=msg, retries=0, retry_after=0)
            except Exception: pass
            class_tag = f" [{fail_class}]" if fail_class else ""
            self.log_event("auto_retry",
                           f"Bumped {prior_status} → pending "
                           f"({attempt+1}/{max_attempts}){class_tag}",
                           url=url)
        if side_effects:
            self.log.info("auto_retry: bumped %d job(s) to pending", len(side_effects))
    def _maybe_drift_recover(self):
        """If learned download selectors are missing more than they hit,
        clear them so the next URL falls through to wide-net scoring.
        That URL's needs_review (or successful auto-detect) gives the
        user another chance to re-learn fresh patterns."""
        learned=self.config.get("learned",{}) if isinstance(self.config.get("learned"),dict) else {}
        stats=learned.get("stats",{})
        hits=stats.get("download_hits",0) or 0
        misses=stats.get("download_misses",0) or 0
        # Only consider after enough samples to be meaningful
        if hits+misses < 6: return False
        if misses > hits*2 and not self._override_suppresses_persist():  # more than 2x miss rate → drift
            sys.stderr.write(f"  drift: download patterns missing {misses}/{hits+misses} times — clearing learned\n")
            learned.pop("download",None)
            stats["drift_recoveries"]=(stats.get("drift_recoveries",0) or 0)+1
            stats["download_hits"]=0
            stats["download_misses"]=0
            try:
                from . import app as _app
                if self.site_id in _app.s_cfg:
                    _app.s_cfg[self.site_id]=self.config
                    _app._save_sites_config()
            except Exception: pass
            return True
        return False
    def _load_rl(self):
        try:
            # Audit 2026-05: explicit utf-8 to match _save_rl's encoding side.
            # Default codec is cp1252 on Windows — would fail if non-ASCII
            # ever got into the human-readable timestamp field.
            with open(self._rl_file, encoding="utf-8") as f: self._rl_until=float(json.load(f).get("until",0))
        except FileNotFoundError:
            # First run — no persisted state yet, that's fine
            pass
        except (json.JSONDecodeError, ValueError, OSError) as e:
            # Corrupt or unreadable — log and start fresh
            self.log.warning("rl_file unreadable, starting with no cooldown: %s", e)
    def _save_rl(self):
        try:
            # Phase 41.7: explicit UTF-8 encoding. On Windows, text-mode open
            # defaults to the system codepage (cp1252) which mangles non-ASCII
            # site names if any leak into the message field.
            # Audit 2026-05: atomic tmp+replace so a crash mid-write doesn't
            # leave a half-written rl file that errors out at next load.
            tmp_path = self._rl_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"until":self._rl_until,"human":datetime.fromtimestamp(self._rl_until).strftime("%Y-%m-%d %H:%M:%S")},f)
            os.replace(tmp_path, self._rl_file)
        except OSError as e:
            # Disk full, permission denied, etc. — cooldown state isn't
            # persisted across restart, but the in-memory state is fine
            self.log.warning("rl_file save failed (cooldown won't survive restart): %s", e)
    def _clear_rl(self):
        self._rl_until=0.0
        try: os.remove(self._rl_file)
        except Exception: pass
    def _next_sched_dt(self):
        if not self.config.get("sched_enabled"): return None
        t=self.config.get("sched_time","")
        if not t: return None
        try: h,m=map(int,t.strip().split(":"))
        except Exception: return None
        now=datetime.now()
        candidate=now.replace(hour=h,minute=m,second=0,microsecond=0)
        if candidate<=now: candidate+=timedelta(days=1)
        return candidate
    def sched_next_str(self):
        """Render the next scheduled-run time as a short human string for
        the UI (e.g. '⏰ in 45m 12s' or '⏰ 2026-05-12 09:00'). Returns
        empty string when scheduling is disabled."""
        dt=self._next_sched_dt()
        if not dt: return ""
        delta=dt-datetime.now(); secs=int(delta.total_seconds())
        if secs<3600: return f"⏰ in {secs//60}m {secs%60}s"
        if secs<86400: return f"⏰ in {secs//3600}h {(secs%3600)//60}m"
        return f"⏰ {dt.strftime('%Y-%m-%d %H:%M')}"
    def start_scheduler(self):
        """Spawn the scheduler thread if `sched_enabled` is True. Idempotent
        — does nothing if scheduling is disabled or the thread is already
        running. The thread sleeps until the configured sched_time and then
        calls start(), optionally with a pre-login N minutes earlier when
        prelogin_minutes is set."""
        if not self.config.get("sched_enabled"): return
        if self._sched_thread and self._sched_thread.is_alive(): return
        self._sched_stop.clear()
        self._sched_thread=threading.Thread(target=self._sched_loop,daemon=True)
        self._sched_thread.start()
    def stop_scheduler(self):
        """Signal the scheduler thread to exit and wait up to 12s for it.
        Safe to call from any thread including the scheduler thread itself
        (it just won't join in that case, avoiding deadlock)."""
        self._sched_stop.set()
        # Wait for the loop to exit so a subsequent start_scheduler() doesn't
        # see is_alive()==True and skip starting a fresh thread. Skip the join
        # if we're being called from inside the loop itself (avoid deadlock).
        t=self._sched_thread
        if t and t is not threading.current_thread():
            t.join(timeout=12)
        self._sched_thread=None
    def _sched_loop(self):
        """Scheduler thread body. Waits until the configured sched_time,
        fires start(), then either exits (sched_repeat='once') or loops
        for the next day (sched_repeat='daily').

        Pre-login refresh: if prelogin_minutes is set and the site has
        credentials, fire an async login N minutes before the scheduled
        start so cookies are fresh when downloads kick off. Tracked
        per-scheduled-datetime so we don't re-pre-login on every loop
        iteration."""
        prelogin_done_for=None  # which scheduled dt we already pre-logged-in for
        while not self._sched_stop.is_set():
            dt=self._next_sched_dt()
            if dt is None: self._sched_stop.wait(30); continue
            # Pre-flight cookie refresh: fire login_async N minutes before the
            # scheduled run so cookies are fresh when the scheduler triggers.
            # Only useful with credentials; only run once per scheduled dt.
            pre=int(self.config.get("prelogin_minutes",15) or 0)
            if (pre>0 and prelogin_done_for!=dt
                    and self.config.get("username") and self.config.get("password")):
                pre_dt=dt-timedelta(minutes=pre)
                pre_wait=(pre_dt-datetime.now()).total_seconds()
                # Sleep until pre-login time (10s ticks for responsive cancel)
                while pre_wait>0 and not self._sched_stop.is_set():
                    self._sched_stop.wait(min(10,pre_wait))
                    pre_wait=(pre_dt-datetime.now()).total_seconds()
                if self._sched_stop.is_set(): break
                # Only actually pre-login if the run is still in the future
                if (dt-datetime.now()).total_seconds()>30:
                    self.login_async()
                    prelogin_done_for=dt
            # Now wait until the actual fire time
            wait=max(0,(dt-datetime.now()).total_seconds())
            while wait>0 and not self._sched_stop.is_set():
                self._sched_stop.wait(min(10,wait))
                wait=(dt-datetime.now()).total_seconds()
            if self._sched_stop.is_set(): break
            # Fire
            if self._state not in ("running",): self.start()
            repeat=self.config.get("sched_repeat","once")
            if repeat=="daily":
                # Skip past the trigger minute before looping. Use the stop
                # event so disabling the scheduler doesn't have to wait 90s.
                if self._sched_stop.wait(90): break
                prelogin_done_for=None  # reset for tomorrow's run
            else:
                with self._lock: self.config["sched_enabled"]=False
                break
