"""runner_accounts -- account/pool rotation + persist; rate-limit predicates

Extracted from runner.py (SiteRunner) @v3.66.401, PHASE 3 runner cut 4.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies. Cycle rule: kernel from .runner_util,
nothing from .runner.
"""
import sys, threading, time
from pathlib import Path

from .runner_util import _resolve_safe


class AccountsMixin:
    def is_rate_limited(self):
        """True if the site is currently in a 24h cooldown window from a
        previous rate-limit detection. Has a side-effect: if the cooldown
        has expired since the last call, clears the rl file as a lazy
        reset. Safe to call frequently — the file delete is a no-op once
        the rl state has been cleared."""
        if self._rl_until and time.time()<self._rl_until: return True
        if self._rl_until and time.time()>=self._rl_until:
            # v3.66.470: edge-triggered recovery -- the single point where the
            # cooldown window flips back to 0. Fire site.recovered, then clear.
            self._clear_rl()
            self._fire_site_hook("site.recovered", {"site_id": self.site_id})
        return False
    def rl_remaining(self):
        """Render the rate-limit cooldown remaining as a short human
        string (e.g. '2h 14m remaining' or '45m 30s remaining'). Returns
        empty string when no cooldown is active."""
        secs=max(0,self._rl_until-time.time())
        if not secs: return ""
        h,r=divmod(int(secs),3600); m,s=divmod(r,60)
        return (f"{h}h {m}m" if h else f"{m}m {s}s")+" remaining"
    def _fire_site_hook(self, event_name, payload):
        """v3.66.470: fire an in-process plugin hook for a site state
        transition (site.cooldown / site.recovered). These are HOOK_EVENTS
        (non-gated, like download.done); fire_hook isolates + quarantine-counts
        each callback. Wrapped so a plugin error can never affect rate-limit
        handling; no-op when no hook is registered."""
        try:
            from . import plugins as _pl
            _pl.fire_hook(event_name, payload)
        except Exception:
            pass
    def trigger_rate_limit(self,url,reason="Rate limit detected"):
        # Phase 6.5: try rotating to a fresh account before triggering
        # the 24h site-wide cooldown. If we have other usable accounts,
        # mark this one as cooled-down, switch, and resume immediately.
        if self._rotate_account_if_available(reason):
            sys.stderr.write(f"  [{self.site_id}] rotated account to recover from rate limit\n")
            # Re-queue the URL that hit the limit (don't count against retries)
            with self._job_status_writer() as mark_status_changed:
                if url in self.jobs:
                    self.jobs[url].update({"status":"pending","message":"Rotated to fresh account","ts":""})
                    mark_status_changed()
            return
        # v3.66.470: edge-triggered cooldown event. We reach here only when no
        # fresh account was available (the rotate path above recovers without
        # cooling). Fire site.cooldown once on the OFF->ON transition.
        _already_cooled = bool(self._rl_until and time.time() < self._rl_until)
        self._rl_until=time.time()+86400; self._save_rl()
        if not _already_cooled:
            self._fire_site_hook("site.cooldown", {
                "site_id": self.site_id,
                "reason": reason,
                "duration_seconds": 86400,
            })
        self._stop.set(); self._pause.set()
        # v3.36.8 audit: removed dead `if self._exec: ...` block.
        # _exec/_futures were pre-v3.0 ThreadPoolExecutor state, never
        # initialized after the worker-threads refactor — this would have
        # raised AttributeError every time a rate limit was detected.
        # Workers now exit cleanly via _stop event + queue sentinels (set
        # above and pushed in stop()); rate-limit recovery uses the same
        # mechanism.
        with self._job_status_writer() as mark_status_changed:
            changed = False
            for u,j in self.jobs.items():
                if j["status"] in ("pending","running","stopped"):
                    j.update({"status":"pending","message":"","ts":"","retry_after":0})
                    changed = True
            if url in self.jobs:
                self.jobs[url]["message"]=f"Re-queued: {reason}"
                changed = True
            if changed:
                mark_status_changed()
        self._state="rate_limited"
        self._rl_autostart=True  # P3-A: arm auto-resume for when cooldown elapses
        threading.Thread(target=self._wait_rl_autostart,daemon=True).start()
    def _get_active_account(self):
        """Return the currently-active (username, password, cookie_file)
        tuple. If `accounts` is configured, picks the entry at
        self._active_account_idx; otherwise falls back to the top-level
        username/password/cookie_file fields (backward-compat path).

        v3.43.14: passwords are now resolved through secrets_store, so
        callers get the plaintext regardless of whether storage is
        keychain-backed, master-password-encrypted, or legacy plaintext.
        The encrypted form lives in cfg["password"] as a "@cred:..."
        reference; this method does the lookup transparently."""
        accounts=self.config.get("accounts") or []
        if not accounts:
            return (self.config.get("username",""),
                    _resolve_safe(self.config.get("password","")),
                    self.config.get("cookie_file",""))
        idx=getattr(self,"_active_account_idx",0)
        if idx>=len(accounts): idx=0
        a=accounts[idx]
        return (a.get("username",""),
                _resolve_safe(a.get("password","")),
                a.get("cookie_file",""))
    def _rotate_account_if_available(self,reason=""):
        """Advance to the next account whose cooldown has elapsed.
        Returns True if a rotation happened (caller should retry the URL).
        Returns False if no rotation possible — either no accounts list,
        or all are cooling down.

        v3.43.35: now backed by the AccountPool. The pool tracks each
        account's state (available / in_use / cooling_down / dead) and
        applies the cooldown based on `account_cooldown_seconds` per
        site (default 5 min, configurable). Falls back to the legacy
        24h cfg-mutation path when the pool isn't configured for this
        site (e.g. during early startup before configure_pool ran)."""
        accounts=self.config.get("accounts") or []
        if len(accounts)<2: return False
        cur_idx=getattr(self,"_active_account_idx",0)

        # v3.43.35: prefer the pool when available
        try:
            from . import account_pool as _ap
            pool = _ap.get_pool(self.site_id)
            # If the pool has been configured, route through it
            if pool._accounts:
                # Mark current as cooling down (transient throttle —
                # the account is fine, just rate-limited)
                cooldown_s = int(self.config.get(
                    "account_cooldown_seconds", _ap.DEFAULT_COOLDOWN_S))
                pool.mark_cooldown(cur_idx, cooldown_seconds=cooldown_s,
                                     reason=reason[:200])
                # Try to lease the next available — short timeout
                # since we're in the worker hot path
                try:
                    new_idx = pool.lease(timeout=2.0)
                except _ap.AccountUnavailable as e:
                    # All accounts cooling down/dead — fall back to
                    # the legacy behavior of waiting
                    sys.stderr.write(
                        f"[{self.site_id}] pool: no account available "
                        f"({e.reason})\n")
                    return False
                # Pool gave us a new idx. Release it immediately —
                # the existing rotation contract doesn't hold the
                # lease for the duration of the work (workers don't
                # currently call release()). This way the pool's
                # cooldown tracking still works without changing the
                # worker lifecycle.
                pool.release(new_idx, success=True)
                # Update config + active idx like the legacy path
                self._active_account_idx = new_idx
                self.config["username"] = accounts[new_idx].get("username","")
                self.config["password"] = accounts[new_idx].get("password","")
                self.config["cookie_file"] = accounts[new_idx].get("cookie_file","")
                # Persist pool state into cfg so cooldowns survive restart
                self._persist_pool_state()
                # Load the new account's cookie file if present
                cf = self.config.get("cookie_file","")
                if cf and Path(cf).exists():
                    try:
                        from .cookies import load_cookies_from_file
                        self.set_cookies(load_cookies_from_file(cf))
                    except Exception: pass
                else:
                    self.set_cookies([])
                return True
        except Exception as e:
            sys.stderr.write(
                f"[{self.site_id}] pool rotation failed, falling back: "
                f"{type(e).__name__}: {e}\n")

        # ── Legacy fallback path (pre-v3.43.35 behavior) ──────────
        # Mark current account as cooling down for 24h
        accounts[cur_idx]["cooldown_until"]=time.time()+86400
        accounts[cur_idx]["last_failure"]=reason[:100]
        # Find next account whose cooldown has elapsed
        now=time.time()
        for offset in range(1,len(accounts)+1):
            cand_idx=(cur_idx+offset)%len(accounts)
            cd=float(accounts[cand_idx].get("cooldown_until",0) or 0)
            if cd<=now:
                self._active_account_idx=cand_idx
                # Update working config so login uses new credentials
                self.config["username"]=accounts[cand_idx].get("username","")
                self.config["password"]=accounts[cand_idx].get("password","")
                self.config["cookie_file"]=accounts[cand_idx].get("cookie_file","")
                # Persist the cooldown timestamps
                self._persist_account_state()
                # Load the new account's cookie file if present
                cf=self.config.get("cookie_file","")
                if cf and Path(cf).exists():
                    try:
                        from .cookies import load_cookies_from_file
                        self.set_cookies(load_cookies_from_file(cf))
                    except Exception: pass
                else:
                    self.set_cookies([])
                # Trigger fresh login on the new account
                self.login_async(allow_manual=False)
                return True
        # Nothing usable — all cooling down
        self._persist_account_state()
        return False
    def _persist_account_state(self):
        """Save the accounts array (with updated cooldown_until values)
        back to sites_config.json via app's helper."""
        try:
            from . import app as _app
            if self.site_id in _app.s_cfg:
                _app.s_cfg[self.site_id]["accounts"]=self.config.get("accounts",[])
                _app._save_sites_config()
        except Exception: pass
    def _persist_pool_state(self):
        """v3.43.35: merge the account pool's per-slot health state
        (cooldown_until, fail_count, last_used, last_error) into
        cfg["accounts"] before saving. Without this, a server restart
        would lose all the pool's tracking — accounts that were dead
        would be auto-retried, accounts on cooldown would be picked
        too early.

        Distinct from _persist_account_state because that one just
        saves the cooldown timestamps mutated directly on the accounts
        list. The pool tracks more state and needs to merge it in."""
        try:
            from . import account_pool as _ap
            from . import app as _app
            pool = _ap.get_pool(self.site_id)
            if not pool._accounts:
                return
            accounts = self.config.get("accounts") or []
            pool_state = _ap.serialize_for_config(pool)
            # Merge by idx (the pool preserves idx across configure() calls)
            for i, ps in enumerate(pool_state):
                if i < len(accounts):
                    accounts[i].update(ps)
            if self.site_id in _app.s_cfg:
                _app.s_cfg[self.site_id]["accounts"] = accounts
                _app._save_sites_config()
        except Exception as e:
            sys.stderr.write(
                f"[{self.site_id}] _persist_pool_state failed: {e}\n")
    def _wait_rl_autostart(self):
        while self._rl_until and time.time()<self._rl_until: time.sleep(60)
        # P3-A: trigger_rate_limit set _stop; clear it here so start()'s gate
        # falls through. Only resume if still armed (stop() disarms on an
        # operator stop during cooldown).
        if self._rl_autostart:
            self._rl_autostart=False
            self._stop.clear()
            self._state="idle"
            self.start()
