"""v3.43.15: Session keep-alive.

Per-(site, account) background thread keeps the login session warm so
that downloads don't get interrupted by expired cookies. Three main
responsibilities:

  1. Schedule: predict when the session will expire and trigger a
     refresh BEFORE that point (default: 10 minutes before).
  2. Refresh: visit the site with stored cookies, fire a lightweight
     authenticated request (a fetch from inside the page context),
     verify we're still logged in.
  3. Recover: if the session expired, call the existing do_login flow
     to re-authenticate. If that fails (captcha, 2FA), mark the
     account as 'needs_takeover' and the UI surfaces a button.

State per (site_id, account_idx):

    {
      "state": "connected" | "refreshing" | "reauthenticating"
              | "inconclusive" | "needs_takeover" | "disconnected"
              | "disabled",
      "last_login_ts": float,
      "last_heartbeat_ts": float,
      "next_check_ts": float,
      "predicted_expiry_ts": float | None,
      "consecutive_failures": int,
      "last_detail": str,
    }

Threads are daemonized — they exit when the main process does. Each
thread owns one persistent Chromium browser (via Playwright) for its
account. The browser lives for the thread's lifetime.

The browser is launched headless with the per-account profile dir
(profiles/<sid>/keepalive_<idx>) so cookies/storage survive restarts.
Profile dir is SEPARATE from worker profiles to avoid contention.

Public surface:

    start_all(sites_config, runner_registry)
      - Called once at app startup. Iterates s_cfg, spawns one thread
        per (site, account) that has keep_alive_enabled.

    stop_all()
      - Called on app shutdown. Signals all threads to stop, waits
        with a timeout, kills any browsers that didn't release.

    get_status() -> dict
      - Returns the current state of all keep-alive threads, keyed
        by (sid, account_idx). For the UI status endpoint.

    force_check(site_id, account_idx)
      - Triggers an immediate check, bypassing the scheduled interval.
        Used by the UI's "Reconnect" button.

    notify_takeover_started/finished(site_id, account_idx)
      - Coordination with manual takeover so two browsers don't fight
        for the profile dir.
"""
# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.
from __future__ import annotations

import json
import random
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable

# ── logout detection ────────────────────────────────────────────────
#
# Both predicates below replaced substring tests that missed the common case.
#
# The URL test was `"login" in url or "signin" in url`. "sign_in" does not
# contain "signin" -- the underscore breaks it -- so Devise's /users/sign_in,
# the most common sign-in route in Rails, read as a healthy session. /auth/ and
# /session/new were missed for the same reason.
#
# The DOM test was `password field AND body text contains "login"`. A page whose
# button says "Sign in" has the field and not the word, so it was undetectable.
#
# WORD BOUNDARIES, NOT SUBSTRINGS, in both. A bare `"sign in" in text` matches
# "design intent"; `"signin"` matches nothing useful. \b(log|sign)[\s_-]?in\b
# matches "sign in" / "sign-in" / "sign_in" / "login" and rejects "signed in"
# (the "ed" breaks it) and "design intent" (no boundary before "sign").
#
# THE CONJUNCTION IS LOAD-BEARING AND MUST STAY. A password input on its own is
# NOT a logout -- a logged-in settings page with a change-password form has one.
# Dropping the `&&` would pass every positive test and start tearing live
# sessions down into _auto_relogin. tests/test_heartbeat_detects_a_logged_out_page.py
# ::test_a_change_password_form_is_not_a_logout is the canary for exactly that.
_SIGNIN_WORD = r"\b(?:log|sign)[\s_-]?in\b"

_LOGIN_URL_RE = re.compile(
    _SIGNIN_WORD + r"|/auth(?:/|$)|/sessions?/new\b", re.I)

# The JS carries the SAME pattern, interpolated rather than retyped. When it was
# a second literal, a mutation had to edit both copies to take effect -- which is
# the same thing as saying the two could drift apart silently. Python and JS
# regex syntax agree on every construct used here (\b, non-capturing group,
# character class), so one constant serves both.
_LOGIN_FORM_JS = """() => {
  if (!document.querySelector('input[type=password]')) return false;
  const t = (document.body ? document.body.textContent : '') || '';
  return /%s/i.test(t);
}""" % _SIGNIN_WORD.replace("/", "\\/")

from . import cloak as _cloak
from . import db


# ─── Timing knobs ────────────────────────────────────────────────────
#
# v3.64.2: defaults raised from 10/5/30 → 30/30/30 minutes after the
# Chromium-persistence fix landed in the same release. With the
# persistent context, the browser's own cookie jar survives across
# heartbeats and restarts, so the keep-alive no longer needs to fire
# every 5 minutes to keep cookies fresh. All three values are now
# operator-tunable via `app_config.json`:
#
#   session_keep_alive_lead_time_min       (default 30)
#   session_keep_alive_fetch_interval_min  (default 30)
#   session_keep_alive_navigate_interval_min (default 30)
#
# The module-level constants below are the fallback used when the
# config key is unset or invalid. Reads go through `_get_*_sec()`
# helpers that read `global_config` on each call (cheap — cached by
# mtime in global_config.py). Operators can change these in Settings
# without restarting the service.

# How long before predicted expiry to do an opportunistic refresh.
REFRESH_LEAD_TIME_SEC = 30 * 60          # 30 minutes (was 10 pre-v3.64.2)

# Lightweight fetch interval (just hits an authenticated endpoint from
# the live page's context — keeps the session cookie's sliding window
# refreshed without doing a full navigation).
FETCH_HEARTBEAT_INTERVAL_SEC = 30 * 60   # 30 minutes (was 5 pre-v3.64.2)

# Full navigation interval (reload a known authenticated page — catches
# sites that don't refresh session on fetch alone).
NAVIGATE_HEARTBEAT_INTERVAL_SEC = 30 * 60  # 30 minutes (unchanged)

# Acceptable operator-tuned interval range, in minutes. The lower bound
# protects against accidental DoS-of-self (a 0/negative value would
# busy-loop the keeper); the upper bound is generous (6h) for sites
# with very long sessions. Values outside the range fall back silently
# to the module-level constant.
_INTERVAL_MIN_MINUTES = 1
_INTERVAL_MAX_MINUTES = 360


def _get_user_interval_sec(config_key: str, fallback_sec: float) -> float:
    """Read a user-tunable interval in minutes from global_config and
    return it in seconds. Falls back to `fallback_sec` if the key is
    unset, non-numeric, or outside the [1, 360] minute range.

    Reads on every call — global_config caches by mtime so this is
    cheap (no disk read unless the file changed). The intent is that
    an operator can change the value in Settings and the keeper picks
    it up on the next scheduler iteration, no restart required.
    """
    try:
        from .global_config import get_config
        cfg = get_config()
        raw = cfg.get(config_key)
        if raw is None:
            return fallback_sec
        minutes = float(raw)
        if not (_INTERVAL_MIN_MINUTES <= minutes <= _INTERVAL_MAX_MINUTES):
            return fallback_sec
        return minutes * 60.0
    except Exception:
        # Any read/parse failure → silent fallback. The keep-alive
        # subsystem must never crash because of a config edit.
        return fallback_sec


def _lead_time_sec() -> float:
    return _get_user_interval_sec(
        "session_keep_alive_lead_time_min", REFRESH_LEAD_TIME_SEC)


def _fetch_interval_sec() -> float:
    return _get_user_interval_sec(
        "session_keep_alive_fetch_interval_min", FETCH_HEARTBEAT_INTERVAL_SEC)


def _navigate_interval_sec() -> float:
    return _get_user_interval_sec(
        "session_keep_alive_navigate_interval_min",
        NAVIGATE_HEARTBEAT_INTERVAL_SEC)

# Jitter ±20% so we don't look like a metronome to server logs.
JITTER_FRACTION = 0.2

# Fallback session lifetime when we have no observations yet.
DEFAULT_SESSION_LIFETIME_SEC = 4 * 3600  # assume 4h sessions

# ── Heartbeat verdicts (row 424) ──────────────────────────────────────────
#
# All three probes used to enumerate their failure signatures and return True
# for EVERYTHING else, so a 500, 502 or 404 read as "session alive" and
# _run_one_check made four auth claims -- state connected, heartbeat_ok,
# consecutive_failures zeroed, predicted_expiry_ts advanced -- over a response
# that measured nothing about auth. That is the fail-open shape CLAUDE.md A7
# forbids, and the operator surface showed a healthy session while the site
# was down.
#
# THE FIX IS A THIRD STATE, NOT FAIL-CLOSED. Classifying 5xx as heartbeat-fail
# would fire _auto_relogin storms against a site that is merely down.
#
# WHY A VERDICT OBJECT RATHER THAN A THIRD STRING. Every existing caller does
# `ok, detail = ...` and tests truthiness. `__bool__` keeps that contract
# exactly -- ALIVE is truthy, and both DEAD and INCONCLUSIVE are falsy because
# neither is evidence that the session is usable -- while identity comparison
# lets _run_one_check tell "refused" from "did not measure", which lead to
# opposite actions.
class HeartbeatVerdict:
    """One of ALIVE / DEAD / INCONCLUSIVE. Compare by identity."""

    __slots__ = ("name", "_alive")

    def __init__(self, name: str, alive: bool):
        self.name = name
        self._alive = alive

    def __bool__(self) -> bool:
        return self._alive

    def __repr__(self) -> str:
        return f"<heartbeat {self.name}>"


ALIVE = HeartbeatVerdict("alive", True)
DEAD = HeartbeatVerdict("dead", False)
INCONCLUSIVE = HeartbeatVerdict("inconclusive", False)


def _as_verdict(value):
    """Normalise a probe's answer.

    A plain bool predates the three-state contract: True is a positive claim
    and False is a refusal. It is never promoted to INCONCLUSIVE -- inventing
    an unknown out of a boolean would hide a real dead session, which is this
    defect wearing the other face.
    """
    if isinstance(value, HeartbeatVerdict):
        return value
    return ALIVE if value else DEAD


def _classify_status(status) -> HeartbeatVerdict:
    """ALIVE only for a 2xx that already passed the logout signatures.

    Anything else is an unclassified status: the site answered, but not with
    evidence about this session's authentication.
    """
    try:
        code = int(status)
    except (TypeError, ValueError):
        return INCONCLUSIVE
    return ALIVE if 200 <= code < 300 else INCONCLUSIVE


# Cap on consecutive failures before backing off to hourly.
BACKOFF_AFTER_N_FAILURES = 3
BACKOFF_INTERVAL_SEC = 60 * 60           # 1 hour

# Per-keeper Chromium check timeout (page load + verify).
CHECK_TIMEOUT_SEC = 60
# Starting a replacement is safe only after the prior generation has left its
# target and identity-finalizer.  Refuse after a bounded wait rather than
# overlapping two browsers on the same profile/account.
_KEEPER_GENERATION_STOP_TIMEOUT_S = 5.0


def _jitter(base: float, fraction: float = JITTER_FRACTION) -> float:
    """Add ±fraction*base random variation to a wait interval."""
    delta = base * fraction
    return base + random.uniform(-delta, delta)


def _now() -> float:
    return time.time()


# ─── Per-keeper state and module-level registry ──────────────────────

_state_lock = threading.RLock()
_keeper_lifecycle_lock = threading.RLock()
_keepers: dict[tuple[str, int], "SessionKeeper"] = {}
# Process teardown/rollback closes admission before it captures generations.
# Only an explicit application/test boot may reopen it after every retained
# handle has been proved quiescent.
_keeper_retired = False

# Per-(site, account) lock that manual takeover takes before launching
# its own browser. Keep-alive must wait until released. Module-level so
# takeover (in runner.py) can import and acquire.
_takeover_locks: dict[tuple[str, int], threading.RLock] = {}


def get_takeover_lock(site_id: str, account_idx: int) -> threading.RLock:
    """Returns the lock for a (site, account). Callers (manual takeover,
    keep-alive check) acquire this before launching a browser against
    the shared profile dir."""
    key = (site_id, account_idx)
    with _state_lock:
        lock = _takeover_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _takeover_locks[key] = lock
        return lock


# ─── Predictions ─────────────────────────────────────────────────────

def predict_next_expiry(site_id: str, account_idx: int) -> float:
    """Predict when the current session will expire, in absolute unix
    timestamp. Returns last_login_ts + median(observed_lifetimes), or
    last_login_ts + DEFAULT_SESSION_LIFETIME_SEC if we have no data.

    If we've never seen a successful login, returns 0 (meaning 'check
    immediately')."""
    keeper = _keepers.get((site_id, account_idx))
    if keeper is None: return 0.0
    last_login = keeper.state.get("last_login_ts", 0)
    if not last_login: return 0.0

    lifetimes = db.session_lifetime_observations(site_id, account_idx)
    if not lifetimes:
        return last_login + DEFAULT_SESSION_LIFETIME_SEC
    # Median (sorted middle element) — robust to outliers
    sorted_lts = sorted(lifetimes)
    n = len(sorted_lts)
    if n % 2 == 1:
        median = sorted_lts[n // 2]
    else:
        median = (sorted_lts[n // 2 - 1] + sorted_lts[n // 2]) / 2
    return last_login + median


# ─── SessionKeeper class ─────────────────────────────────────────────

class SessionKeeper:
    """One keep-alive thread for a single (site, account) pair. Owns
    a Playwright browser for the duration of its lifetime."""

    def __init__(self, site_id: str, account_idx: int, site_config: dict,
                 do_login_callback: Callable):
        self.site_id = site_id
        self.account_idx = account_idx
        self.config = site_config  # current config (may be mutated externally)
        self.do_login_callback = do_login_callback
        self._stop = threading.Event()
        self._wake = threading.Event()  # set to force an immediate check
        self._restart_requested = threading.Event()
        self._thread = threading.Thread(
            target=self._run_and_unregister,
            name=f"keepalive-{site_id}-{account_idx}",
            daemon=True)
        # v3.43.16: persistent browser per (site, account). Owned by the
        # keeper thread for its lifetime; restarted every 24h to bound
        # Chromium's memory drift. Stays None when:
        #   - Playwright import fails (degraded to httpx-only)
        #   - We haven't done the first launch yet
        #   - We crashed and haven't relaunched
        self._pw = None              # Playwright instance
        self._browser = None         # Browser handle
        self._ctx = None             # BrowserContext (with cookies)
        self._page = None            # The parked tab
        self._browser_started_at = 0.0
        self._last_navigate_at = 0.0  # for the 30-min full-page navigate
        # Account-name override (multi-account sites push per-account
        # username into top-level config; we keep the account dict here
        # to know which one to use)
        self._account = None
        self.state: dict = {
            "state": "starting",
            "last_login_ts": 0.0,
            "last_heartbeat_ts": 0.0,
            "next_check_ts": 0.0,
            "predicted_expiry_ts": None,
            "consecutive_failures": 0,
            "last_detail": "",
        }

    def start(self):
        self._thread.start()

    def _run_and_unregister(self):
        """Run this generation and remove only its own registry entry."""
        try:
            self._run()
        finally:
            key = (self.site_id, self.account_idx)
            with _state_lock:
                if _keepers.get(key) is self:
                    _keepers.pop(key, None)
                    if (self._restart_requested.is_set()
                            and not self._stop.is_set()):
                        # Publish/start the replacement while the state lock
                        # still makes the handoff atomic with stop/start.  Do
                        # not acquire the lifecycle lock here: a starter can
                        # legitimately hold _state_lock while this finalizer
                        # runs, and the inverse order would deadlock.
                        replacement = SessionKeeper(
                            self.site_id,
                            self.account_idx,
                            self.config,
                            self.do_login_callback,
                        )
                        _keepers[key] = replacement
                        try:
                            replacement.start()
                        except BaseException as exc:
                            if _keepers.get(key) is replacement:
                                _keepers.pop(key, None)
                            sys.stderr.write(
                                "  keepalive generation handoff failed for "
                                f"{self.site_id}/{self.account_idx}: {exc}\n")

    def stop(self):
        self._stop.set()
        self._wake.set()
        # Don't join here — caller does it with a timeout

    def force_check_now(self):
        """Set the wake flag so the scheduler runs again immediately."""
        self._wake.set()

    # ── State management ────────────────────────────────────────────

    def _set_state(self, new_state: str, detail: str = ""):
        with _state_lock:
            self.state["state"] = new_state
            if detail: self.state["last_detail"] = detail

    def _record_event(self, event_type: str, detail: str = ""):
        try:
            db.session_event_record(self.site_id, self.account_idx,
                                    event_type, detail)
        except Exception as e:
            sys.stderr.write(f"  keepalive: db record failed: {e}\n")

    # ── Scheduling ──────────────────────────────────────────────────

    def _compute_next_check_time(self) -> float:
        """Decide how long to wait before the next check. Two cases:

        1. We've been failing repeatedly → back off to hourly so we
           don't hammer a site that's down or has revoked our creds.
        2. Normal: schedule at min(next_predicted_expiry - 10min,
           now + 5min). Whichever's sooner.
        """
        if self.state["consecutive_failures"] >= BACKOFF_AFTER_N_FAILURES:
            return _now() + _jitter(BACKOFF_INTERVAL_SEC)
        predicted = predict_next_expiry(self.site_id, self.account_idx)
        fetch_interval = _fetch_interval_sec()
        if predicted and predicted > _now():
            time_to_lead = predicted - _now() - _lead_time_sec()
            if time_to_lead > 0:
                # Refresh BEFORE the predicted expiry
                return _now() + min(time_to_lead,
                                    _jitter(fetch_interval))
        # No prediction yet, or predicted expiry already passed —
        # heartbeat at the default cadence
        return _now() + _jitter(fetch_interval)

    # ── Main loop ───────────────────────────────────────────────────

    def _run(self):
        sys.stderr.write(
            f"  keepalive[{self.site_id}/{self.account_idx}]: thread started\n")
        # Initial state: check immediately on startup
        self._set_state("connected", "starting up")
        try:
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    self._run_one_check()
                except Exception as e:
                    sys.stderr.write(
                        f"  keepalive[{self.site_id}/{self.account_idx}]: "
                        f"check raised {type(e).__name__}: {e}\n")
                    self.state["consecutive_failures"] += 1
                next_ts = self._compute_next_check_time()
                with _state_lock:
                    self.state["next_check_ts"] = next_ts
                wait = max(1, next_ts - _now())
                self._wake.wait(timeout=wait)
        finally:
            # v3.43.16: clean up the persistent browser on thread exit.
            # Without this, stopping the app leaves orphaned chromium
            # processes whose profile dirs are still locked.
            self._teardown_browser()
        sys.stderr.write(
            f"  keepalive[{self.site_id}/{self.account_idx}]: thread exiting\n")

    # ── Check execution ─────────────────────────────────────────────

    def _run_one_check(self):
        """Try to verify the session is still alive. If not, try to
        relogin. Records the result to session_history."""
        if not self.config.get("keep_alive_enabled", True):
            self._set_state("disabled", "keep_alive_enabled is False")
            return
        if not self.config.get("password"):
            self._set_state("disabled", "no password configured")
            return

        self._set_state("refreshing", "running heartbeat")
        verdict, detail = self._heartbeat()
        verdict = _as_verdict(verdict)

        if verdict is INCONCLUSIVE:
            # Row 424. The site answered, but with nothing about this
            # session's authentication -- a 5xx, a 404, or a redirect that
            # does not point at a sign-in page. Make NO auth claim: no
            # heartbeat_ok, no expiry advance, and no relogin (a relogin
            # storm against a site that is merely down is the failure the
            # third state exists to avoid).
            #
            # consecutive_failures is left UNTOUCHED in both directions.
            # Resetting it would launder an outage into evidence of health;
            # incrementing it would walk the keeper into
            # BACKOFF_AFTER_N_FAILURES and report `disconnected` -- an auth
            # claim built from evidence about the site being unreachable.
            self._set_state("inconclusive", detail)
            self._record_event("heartbeat_inconclusive", detail)
            return

        if verdict is ALIVE:
            now = _now()
            with _state_lock:
                self.state["last_heartbeat_ts"] = now
                self.state["consecutive_failures"] = 0
                self.state["predicted_expiry_ts"] = predict_next_expiry(
                    self.site_id, self.account_idx)
            self._set_state("connected", "heartbeat ok")
            self._record_event("heartbeat_ok", detail)
            return

        # Heartbeat failed → try auto-relogin
        self._record_event("heartbeat_fail", detail)
        with _state_lock:
            self.state["consecutive_failures"] += 1
        self._set_state("reauthenticating", "heartbeat failed; relogging in")

        relogin_ok, relogin_detail = self._auto_relogin()
        if relogin_ok:
            now = _now()
            with _state_lock:
                self.state["last_login_ts"] = now
                self.state["consecutive_failures"] = 0
                self.state["predicted_expiry_ts"] = (
                    now + DEFAULT_SESSION_LIFETIME_SEC)
            self._set_state("connected", "auto-relogin succeeded")
            self._record_event("auto_relogin_ok", relogin_detail)
        else:
            self._record_event("auto_relogin_fail", relogin_detail)
            if "captcha" in relogin_detail.lower() or "2fa" in relogin_detail.lower():
                self._set_state("needs_takeover", relogin_detail)
                self._record_event("needs_takeover", relogin_detail)
            elif self.state["consecutive_failures"] >= BACKOFF_AFTER_N_FAILURES:
                self._set_state("disconnected",
                    f"failed {self.state['consecutive_failures']} times: {relogin_detail}")
            else:
                self._set_state("disconnected", relogin_detail)

    def _heartbeat(self) -> tuple[bool, str]:
        """Verify the session is still valid using the persistent browser.

        Two cadences:
          - Every iteration: fire an authenticated fetch from inside the
            page context (cheap, refreshes session cookie's sliding window)
          - Every NAVIGATE_HEARTBEAT_INTERVAL_SEC (default 30min): full
            page navigation (catches sites that don't refresh on fetch
            alone, and lets us re-evaluate the response body for login
            forms or other auth markers)

        If the browser can't be launched (Playwright unavailable,
        crashed, etc.), degrades to a plain httpx GET — same as v3.43.15.

        Returns (verdict, detail) where verdict is ALIVE, DEAD or
        INCONCLUSIVE (row 424). ALIVE is truthy and the other two are falsy,
        so a caller that only asks "is the session usable?" reads it exactly
        as it read the old bool."""
        # Make sure we have a live browser. Restart if it's been alive
        # too long, or if it crashed, or if we haven't launched yet.
        if not self._browser_alive() or self._browser_age() > 86400:
            ok = self._launch_browser()
            if not ok:
                return self._heartbeat_httpx_fallback()

        # Decide between navigate or fetch this cycle
        now = _now()
        time_since_nav = now - self._last_navigate_at
        if time_since_nav > _navigate_interval_sec():
            return self._heartbeat_navigate()
        else:
            return self._heartbeat_in_page_fetch()

    def _browser_alive(self) -> bool:
        """True if the context/page are still usable. With
        `launch_persistent_context` (v3.64.2), `self._browser` is `None`
        — the persistent context owns the browser process directly —
        so the connection check goes through the context's `.browser`
        accessor (which returns None for persistent contexts, by
        Playwright design). We treat "page open AND context responsive"
        as alive; a quick attribute access on the context will raise
        if the underlying process died, which the outer try catches.
        """
        if self._ctx is None or self._page is None:
            return False
        try:
            if self._page.is_closed():
                return False
            # For persistent contexts, _browser is None and the proper
            # liveness check is via the context itself. `pages` is a
            # cheap accessor that touches the live process; a dead
            # process raises a TargetClosedError.
            _ = self._ctx.pages
            # If we kept a Browser object (non-persistent path, kept
            # for httpx-fallback symmetry), check it too.
            if self._browser is not None:
                return self._browser.is_connected()
            return True
        except Exception:
            return False

    def _browser_age(self) -> float:
        return _now() - self._browser_started_at if self._browser_started_at else 0.0

    def _launch_browser(self) -> bool:
        """Spin up a persistent Chromium context for this account.

        v3.64.2: Switched from `launch()` + `new_context()` to
        `launch_persistent_context(user_data_dir=profiles/<sid>/keepalive_<idx>)`.
        The previous code path used an ephemeral context and
        re-injected DB cookies on every launch — meaning anything the
        site SET between heartbeats (rotated session tokens, refreshed
        CSRF cookies) was lost when the browser restarted (every 24h,
        on crash, on takeover release). The docstring at the top of
        this module already claimed persistence; the code now matches.

        DB cookies are still seeded into the context on the **first
        launch** (when the profile dir is empty), so the keeper starts
        from the runner's last-known-good login state. After that the
        persistent profile dir is the source of truth and DB writes
        flow OUT of the keeper via `_persist_cookies()` on every
        successful navigate.

        Returns True on success, False on any failure (which triggers
        httpx fallback).
        """
        self._teardown_browser()  # ensure clean slate
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            sys.stderr.write(
                f"  keepalive[{self.site_id}/{self.account_idx}]: "
                f"Playwright import failed ({e}); using httpx fallback\n")
            return False
        # Lock: don't fight a takeover for the profile dir
        lock = get_takeover_lock(self.site_id, self.account_idx)
        if not lock.acquire(blocking=False):
            sys.stderr.write(
                f"  keepalive[{self.site_id}/{self.account_idx}]: "
                f"profile locked by takeover; deferring browser launch\n")
            return False
        try:
            # Profile dir layout matches the docstring at the top of
            # this module: profiles/<sid>/keepalive_<idx>. Distinct
            # from worker profiles (profiles/<sid>/worker_*) to avoid
            # contention with concurrent downloads.
            profile_dir = Path("profiles") / self.site_id / f"keepalive_{self.account_idx}"
            first_launch = not profile_dir.exists() or not any(
                profile_dir.iterdir()) if profile_dir.exists() else True
            profile_dir.mkdir(parents=True, exist_ok=True)

            # Headless, minimal flags. We're not pretending to be a real
            # user here — we're just refreshing a session.
            keepalive_args = [
                "--no-sandbox",
                "--disable-notifications",
                "--disable-popup-blocking",
                "--disable-features=PushMessaging,Translate,AutomationControlled",
                "--disable-blink-features=AutomationControlled",
                # Reduce idle-tab CPU. Keep-alive tabs don't need fast UI.
                "--disable-background-timer-throttling=false",
                "--lang=en-US",
            ]
            # CloakBrowser is the canonical browser backend (see cloak.py).
            # It is preferred by default and transparently degrades to
            # Playwright when cloak is unavailable, disabled, or fails to
            # launch. On the cloak path `pw` comes back None (the context
            # owns its own Playwright and stops it on close); on the
            # Playwright path `pw` is the instance _teardown_browser stops.
            self._ctx, self._pw, backend = _cloak.open_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                args=keepalive_args,
                user_agent=(self.config.get("fingerprint") or {}).get(
                    "user_agent") or None,
                config=self.config,
            )
            # launch_persistent_context owns the browser process directly.
            # There is no separate `Browser` handle — leave `self._browser`
            # as None and let `_browser_alive` check the context.
            self._browser = None
            _cloak.log_choice(
                f"keepalive[{self.site_id}/{self.account_idx}]", backend)

            # First-launch only: seed cookies from the DB-backed
            # cookies file. After this, the persistent context owns
            # the cookie state and we never re-inject. (If we DID
            # re-inject on every launch we'd lose any cookies the
            # site set between heartbeats — the exact bug this fix
            # closes.)
            if first_launch:
                cookies = self._load_cookies_as_playwright()
                if cookies:
                    try:
                        self._ctx.add_cookies(cookies)
                        sys.stderr.write(
                            f"  keepalive[{self.site_id}/{self.account_idx}]: "
                            f"seeded {len(cookies)} cookies into fresh profile\n")
                    except Exception as e:
                        sys.stderr.write(
                            f"  keepalive[{self.site_id}/{self.account_idx}]: "
                            f"first-launch cookie seed failed: {e}\n")
            # `launch_persistent_context` opens with a blank page by
            # default. Reuse it if present so we don't accumulate
            # about:blank tabs.
            if self._ctx.pages:
                self._page = self._ctx.pages[0]
            else:
                self._page = self._ctx.new_page()
            # Block heavy resources to minimize bandwidth + load time.
            # We don't care about ads/images/videos for session
            # verification.
            try:
                self._page.route(
                    "**/*",
                    lambda route: (
                        route.abort()
                        if route.request.resource_type in
                           ("image", "media", "font")
                        else route.continue_()
                    ),
                )
            except Exception:
                pass
            self._browser_started_at = _now()
            self._last_navigate_at = 0  # force a navigate on first iteration
            sys.stderr.write(
                f"  keepalive[{self.site_id}/{self.account_idx}]: "
                f"persistent context launched via {backend} "
                f"(profile={profile_dir})\n")
            return True
        except Exception as e:
            sys.stderr.write(
                f"  keepalive[{self.site_id}/{self.account_idx}]: "
                f"browser launch failed: {type(e).__name__}: {e}\n")
            self._teardown_browser()
            return False
        finally:
            lock.release()

    def _teardown_browser(self):
        """Best-effort browser cleanup. Each step is wrapped because the
        browser may already be in a half-dead state by the time we
        notice it needs restarting."""
        for attr in ("_page", "_ctx", "_browser"):
            obj = getattr(self, attr, None)
            if obj is None: continue
            try:
                obj.close()
            except Exception:
                pass
            setattr(self, attr, None)
        if self._pw is not None:
            try: self._pw.stop()
            except Exception: pass
            self._pw = None
        self._browser_started_at = 0

    def _load_cookies_as_playwright(self) -> list:
        """Read cookies/{sid}.json and convert to Playwright's expected
        shape: list of {name, value, domain, path, expires?, httpOnly?,
        secure?, sameSite?}. The runner already writes cookies in this
        Playwright format on login, so usually we just pass through."""
        try:
            cookie_path = Path("cookies") / f"{self.site_id}.json"
            if not cookie_path.exists(): return []
            import json as _json
            data = _json.loads(cookie_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # Already in Playwright shape
                out = []
                for c in data:
                    if not isinstance(c, dict): continue
                    if "name" not in c or "value" not in c: continue
                    # Playwright requires either url or (domain+path)
                    if "domain" not in c and "url" not in c: continue
                    out.append(c)
                return out
            elif isinstance(data, dict):
                # Plain {name: value} dict — need to synthesize a domain.
                # Derive from the site's login URL.
                from urllib.parse import urlparse
                login = (self.config.get("login_url") or
                         self.config.get("success_url") or "")
                if not login: return []
                host = urlparse(login).hostname or ""
                if not host: return []
                # Strip "www." to match more broadly
                domain = host[4:] if host.startswith("www.") else host
                return [{
                    "name": k, "value": v,
                    "domain": "." + domain, "path": "/",
                } for k, v in data.items() if isinstance(v, str)]
        except Exception as e:
            sys.stderr.write(
                f"  keepalive[{self.site_id}]: cookie load failed: {e}\n")
        return []

    def _persist_cookies(self):
        """After a successful navigate, save the current context's
        cookies back to disk so workers see fresh values.

        v3.43.19: atomic write via .tmp + replace. This runs after every
        successful heartbeat (~5 min cadence), so a crash mid-write
        would leave workers reading truncated/empty cookies.json on
        next launch — triggering a session-expired storm followed by
        a re-login cycle. The .tmp + rename pattern guarantees the
        cookie file is always either the old or new complete content.

        v3.43.32: routed through cookies.save_cookies_to_file so the
        round-trip validation runs. Previously this code path bypassed
        the shared writer (hand-rolled .tmp + replace). Without the
        round-trip check, a disk-full mid-heartbeat would write a
        truncated file and workers would silently start downloading
        with corrupted session state."""
        try:
            if self._ctx is None: return
            cookies = self._ctx.cookies()
            from .cookies import save_cookies_to_file, CookieRoundTripError
            cookie_path = Path("cookies") / f"{self.site_id}.json"
            try:
                save_cookies_to_file(cookie_path, cookies)
            except CookieRoundTripError as e:
                # Round-trip failed — the .tmp+replace may have left a
                # corrupted file at the destination. Log loudly so the
                # user knows; the next heartbeat will retry.
                sys.stderr.write(
                    f"  keepalive[{self.site_id}]: cookie round-trip "
                    f"validation FAILED ({e.kind}): {e}\n")
        except Exception as e:
            sys.stderr.write(
                f"  keepalive[{self.site_id}]: cookie persist failed: {e}\n")

    def _heartbeat_navigate(self) -> tuple[bool, str]:
        """Full page navigation. Slowest but most reliable. Catches:
          - JS-rendered "logged out" states
          - Cloudflare or other JS challenges
          - Server-side cookie rotation that triggers on full navigation
        """
        url = self._derive_check_url()
        if not url:
            return DEAD, "no check URL configurable for this site"
        try:
            resp = self._page.goto(url, wait_until="domcontentloaded",
                                   timeout=CHECK_TIMEOUT_SEC * 1000)
            self._last_navigate_at = _now()
            if resp is None:
                return DEAD, "navigation returned no response"
            status = resp.status
            final_url = self._page.url
            # Redirect to login → session is dead
            if _LOGIN_URL_RE.search(final_url):
                return DEAD, f"redirected to {final_url[:80]}"
            if status in (401, 403):
                return DEAD, f"server returned {status}"
            # Check for login form in the rendered DOM
            try:
                has_login_form = self._page.evaluate(_LOGIN_FORM_JS)
                if has_login_form:
                    return DEAD, "rendered page contains login form"
            except Exception:
                pass
            # Row 424: only a 2xx that survived the checks above is evidence
            # of an authenticated session. A 500/502/404 answered "navigate ok
            # (HTTP 500)" and was recorded as heartbeat_ok.
            verdict = _classify_status(status)
            if verdict is not ALIVE:
                return verdict, (f"navigate inconclusive (HTTP {status}): the "
                                 f"site answered but measured no auth")
            # Persist refreshed cookies -- only for an authenticated response;
            # a jar captured from an outage page is not a session refresh.
            self._persist_cookies()
            return ALIVE, f"navigate ok (HTTP {status})"
        except Exception as e:
            # Distinguish playwright timeout from network from other
            err_type = type(e).__name__
            return DEAD, f"navigate failed: {err_type}: {str(e)[:120]}"

    def _heartbeat_in_page_fetch(self) -> tuple[bool, str]:
        """Lightweight: ask the existing page context to fire a fetch()
        for a likely-authenticated endpoint. Doesn't navigate — the
        current page stays on whatever it last loaded. Just enough
        activity to keep the session cookie's sliding window from
        expiring.

        We hit the check URL via fetch() with credentials:'include' so
        the browser's cookies attach automatically. Read the status +
        first bytes of the response to spot a login-redirect or
        401."""
        url = self._derive_check_url()
        if not url:
            return DEAD, "no check URL configurable for this site"
        try:
            js = (
                f"async () => {{"
                f"  const r = await fetch({json.dumps(url)}, "
                f"    {{credentials:'include', redirect:'manual'}});"
                f"  let text = '';"
                f"  try {{ text = (await r.text()).substring(0, 5000); }} "
                f"  catch(e) {{ text = ''; }}"
                f"  return {{ status: r.status, url: r.url, type: r.type, body: text }};"
                f"}}"
            )
            result = self._page.evaluate(js)
            status = result.get("status", 0)
            # status 0 with type 'opaqueredirect' means the server tried
            # to redirect us (since we set redirect:'manual'). Usually
            # a redirect to login.
            if result.get("type") == "opaqueredirect" or status == 0:
                return DEAD, "fetch returned opaque redirect (likely to login)"
            if status in (401, 403):
                return DEAD, f"fetch got {status}"
            if 300 <= status < 400:
                return DEAD, f"fetch got redirect {status}"
            body = (result.get("body") or "").lower()
            if "type=\"password\"" in body and "login" in body:
                return DEAD, "fetch body contains login form"
            # Row 424: an unclassified status is not an authenticated answer.
            verdict = _classify_status(status)
            if verdict is not ALIVE:
                return verdict, (f"in-page fetch inconclusive (HTTP {status}): "
                                 f"the site answered but measured no auth")
            # Persist any updated cookies (sliding window refresh)
            self._persist_cookies()
            return ALIVE, f"in-page fetch ok (HTTP {status})"
        except Exception as e:
            return DEAD, f"in-page fetch failed: {type(e).__name__}: {str(e)[:120]}"

    def _heartbeat_httpx_fallback(self) -> tuple[bool, str]:
        """Fallback path when Playwright/Chromium is unavailable. Same
        logic as v3.43.15 (plain httpx GET with stored cookies). Less
        reliable but always available."""
        check_url = self._derive_check_url()
        if not check_url:
            return DEAD, "no check URL configurable for this site"
        try:
            import httpx
            cookies = self._load_cookies()
            if not cookies:
                return DEAD, "no cookies stored yet (never logged in)"
            # F-RUN02-03: route the heartbeat through the same fail-closed VPN
            # proxy as the payload path. A vpn_required site whose tunnel is
            # down raises VPNRequiredError -> fail closed (no client built)
            # rather than heartbeat on the clear interface. Function-local
            # imports keep this off the module-level import graph.
            from . import vpn_runtime as _vpnrt
            from .download_egress import effective_download_proxy as _eff_proxy
            try:
                _resolver = getattr(_vpnrt, "get_socks_url_for_site", None)
                proxy_url = _eff_proxy(self.config.get("proxy"), self.site_id,
                                       _resolver)
            except _vpnrt.VPNRequiredError as _pe:
                return DEAD, (f"VPN required, tunnel unavailable -- "
                              f"failing closed: {_pe}")
            with httpx.Client(timeout=20.0, follow_redirects=False,
                              cookies=cookies, proxy=proxy_url) as cli:
                resp = cli.get(check_url)
            if 300 <= resp.status_code < 400:
                loc = resp.headers.get("location", "")
                if "login" in loc.lower() or "signin" in loc.lower():
                    return DEAD, f"redirected to login: {loc[:80]}"
            if resp.status_code in (401, 403):
                return DEAD, f"server returned {resp.status_code}"
            if resp.status_code == 200:
                text = resp.text[:50000].lower()
                if 'type="password"' in text and "login" in text:
                    return DEAD, "response contains login form"
            # Row 424: this is where ANY 3xx whose Location merely lacked the
            # substrings login/signin used to fall through to True, alongside
            # every 5xx and 404.
            verdict = _classify_status(resp.status_code)
            if verdict is not ALIVE:
                return verdict, (f"httpx fallback inconclusive "
                                 f"(HTTP {resp.status_code}): the site "
                                 f"answered but measured no auth")
            return ALIVE, f"httpx fallback HTTP {resp.status_code}"
        except Exception as e:
            return DEAD, f"httpx fallback failed: {type(e).__name__}: {e}"

    def _derive_check_url(self) -> str:
        """Where to GET to verify we're logged in. Order of preference:
          1. cfg['keep_alive_check_url']  (explicit override)
          2. cfg['success_url']           (post-login destination)
          3. cfg['login_url']             (last resort — login pages
                                            usually redirect AWAY when
                                            you're already authenticated)
        """
        cfg = self.config
        return (cfg.get("keep_alive_check_url")
                or cfg.get("success_url")
                or cfg.get("login_url")
                or "")

    def _load_cookies(self) -> dict:
        """Load cookies for this site from the shared cookie file. The
        runner already maintains this file; we just read it."""
        try:
            cookie_path = Path("cookies") / f"{self.site_id}.json"
            if not cookie_path.exists(): return {}
            import json
            data = json.loads(cookie_path.read_text(encoding="utf-8"))
            # httpx wants a plain {name: value} dict
            if isinstance(data, list):
                return {c["name"]: c["value"] for c in data
                        if isinstance(c, dict) and "name" in c and "value" in c}
            elif isinstance(data, dict):
                return data
        except Exception as e:
            sys.stderr.write(
                f"  keepalive[{self.site_id}]: cookie read failed: {e}\n")
        return {}

    def _auto_relogin(self) -> tuple[bool, str]:
        """Call out to the registered do_login callback. The callback
        is responsible for running the login flow (with stored creds),
        updating the cookie file, and reporting back.

        v3.43.52 fix: do_login() in login.py uses sync_playwright()
        which CANNOT be nested inside another sync_playwright context.
        The keeper thread already has self._pw alive (the heartbeat
        browser), so calling the callback would crash with the
        "Sync API inside the asyncio loop" error. Workaround: tear
        down the keeper browser BEFORE invoking the callback. Next
        heartbeat will relaunch it fresh with the new cookies.
        """
        if not self.do_login_callback:
            return False, "no relogin callback configured"
        lock = get_takeover_lock(self.site_id, self.account_idx)
        # If someone has the lock (a takeover is in progress), defer.
        # We try acquire with no wait — if blocked, skip this cycle.
        if not lock.acquire(blocking=False):
            return False, "takeover in progress; skipping"
        # Release the keeper's Playwright context BEFORE the callback
        # starts its own. The callback's do_login() will then have a
        # clean slate. After the callback returns we leave self._pw
        # torn down — the next heartbeat will detect that and relaunch.
        try:
            self._teardown_browser()
        except Exception as e:
            sys.stderr.write(
                f"  keepalive[{self.site_id}/{self.account_idx}]: "
                f"pre-relogin teardown failed ({e}); proceeding anyway\n")
        try:
            return self.do_login_callback(self.site_id, self.account_idx,
                                          self.config)
        finally:
            lock.release()


# ─── Module API ──────────────────────────────────────────────────────

def start_keeper(site_id: str, account_idx: int, site_config: dict,
                 do_login_callback: Callable) -> SessionKeeper:
    """Spawn a keeper thread. Returns the SessionKeeper instance."""
    key = (site_id, account_idx)
    with _keeper_lifecycle_lock:
        if _keeper_retired:
            raise RuntimeError("keeper lifecycle is retired")
        with _state_lock:
            existing = _keepers.get(key)
            if (existing is not None
                    and not existing._stop.is_set()
                    and existing._thread.is_alive()):
                # If the target has already returned but is blocked in its
                # identity-finally, Thread.is_alive() is still true.  Store an
                # ensure request on that exact generation so its finalizer
                # hands off instead of losing this starter.
                existing.config = site_config
                existing.do_login_callback = do_login_callback
                existing._restart_requested.set()
                return existing
            if existing is not None:
                existing.stop()

        # Never hold _state_lock across join: the target's identity-finally
        # needs it.  The outer lifecycle lock excludes every public starter and
        # stopper while the captured generation settles.
        if existing is not None:
            old_thread = existing._thread
            if old_thread is threading.current_thread():
                raise RuntimeError(
                    "previous keeper generation is still live")
            if old_thread.is_alive():
                old_thread.join(timeout=_KEEPER_GENERATION_STOP_TIMEOUT_S)
            if old_thread.is_alive():
                raise RuntimeError(
                    "previous keeper generation is still live")

        with _state_lock:
            current = _keepers.get(key)
            if existing is not None and current is existing:
                _keepers.pop(key, None)
            elif current is not None:
                # Public lifecycle calls are serialized, so a different
                # identity here can only be an unsanctioned direct mutation.
                # Do not overwrite a generation we cannot prove quiescent.
                raise RuntimeError(
                    "keeper registry changed during generation handoff")
            keeper = SessionKeeper(site_id, account_idx, site_config,
                                   do_login_callback)
            # Reserve before Thread.start() while holding the registry lock.
            # The target-finally conditionally removes only this generation,
            # so an immediate exit cannot erase a later retry.
            _keepers[key] = keeper
            try:
                keeper.start()
            except BaseException:
                if _keepers.get(key) is keeper:
                    _keepers.pop(key, None)
                raise
        return keeper


def pause_site_keepers(site_id: str) -> int:  # INV-001
    """v3.43.52: tear down the Playwright context for every keeper
    on this site_id so a caller can spawn its own sync_playwright
    context using the same profile dir.

    Keepers DON'T stop — they keep their daemon thread running. On
    the next heartbeat each will detect `self._pw is None` and
    relaunch the browser fresh (with whatever new cookies the
    caller's flow may have produced).

    Returns the number of keepers whose browsers were torn down.
    Safe to call when no keepers exist (returns 0).
    """
    paused = 0
    with _state_lock:
        for (kid, _aidx), keeper in list(_keepers.items()):
            if kid == site_id:
                try:
                    keeper._teardown_browser()
                    paused += 1
                except Exception as e:
                    sys.stderr.write(
                        f"  keepalive[{kid}]: pause teardown failed: "
                        f"{type(e).__name__}: {e}\n")
    if paused:
        sys.stderr.write(
            f"  keepalive: paused {paused} keeper browser(s) for {site_id}\n")
    return paused


def note_worker_success(site_id: str, account_idx: int = 0) -> None:
    """v3.43.53: record that a worker just completed a download
    successfully for this site. The keeper uses this as ground-truth
    evidence that the session IS working, regardless of what its own
    heartbeat browser thinks.

    Why this matters: the keeper runs a SEPARATE Chromium for
    heartbeats. That heartbeat browser can fail for reasons (a stale
    cookie, a captcha challenge, a flaky network) while workers using
    a different profile + fresh cookies download just fine. Without
    this hook the UI displayed "disconnected" while downloads succeeded,
    confusing the user.

    The keeper's state stream still goes through its own heartbeat
    cadence — this hook just records a `last_worker_success_ts` that
    the UI can use to override a stale "disconnected" badge.
    """
    with _state_lock:
        # Find ANY keeper for this site (account_idx is a hint but
        # workers don't always know which account they used)
        for (kid, aidx), keeper in _keepers.items():
            if kid != site_id:
                continue
            if account_idx is not None and aidx != account_idx and account_idx != 0:
                continue
            try:
                keeper.state["last_worker_success_ts"] = _now()
            except Exception:
                # State dict might be partial; ignore
                pass
            # Only update the FIRST matching keeper (or all if
            # account_idx wasn't specified). Once is enough — the
            # UI checks any keeper for the site.
            if account_idx != 0:
                break


def stop_keeper(site_id: str, account_idx: int):
    """Stop the keeper for (site, account). Does not wait."""
    key = (site_id, account_idx)
    with _keeper_lifecycle_lock:
        with _state_lock:
            keeper = _keepers.get(key)
            if keeper:
                # Retain the identity until its finalizer runs.  A concurrent
                # start can then join/refuse this exact live generation rather
                # than publishing a replacement beside an untracked worker.
                keeper.stop()


def stop_site_keepers(site_id: str, timeout: float = 5.0) -> bool:
    """Stop and boundedly prove every keeper generation for one site dead.

    Survivor identities remain published so a delete retry or process teardown
    retains the only available stop/join handles.
    """
    with _keeper_lifecycle_lock:
        with _state_lock:
            keepers = [
                keeper for (keeper_site, _idx), keeper in _keepers.items()
                if keeper_site == site_id
            ]
            for keeper in keepers:
                keeper.stop()
        deadline = time.monotonic() + max(0.0, timeout)
        current_thread = threading.current_thread()
        for keeper in keepers:
            thread = keeper._thread
            if thread is current_thread:
                continue
            try:
                if thread.is_alive():
                    thread.join(timeout=max(
                        0.0, deadline - time.monotonic()))
            except Exception:
                pass
        with _state_lock:
            survivors = []
            for keeper in keepers:
                key = (keeper.site_id, keeper.account_idx)
                try:
                    alive = (keeper._thread is current_thread
                             or keeper._thread.is_alive())
                except Exception:
                    alive = True
                if alive:
                    survivors.append(keeper)
                elif _keepers.get(key) is keeper:
                    _keepers.pop(key, None)
        return not survivors


def stop_all(timeout: float = 5.0, *, retire: bool = False) -> bool:
    """Stop all keepers and optionally close admission until the next boot."""
    global _keeper_retired
    with _keeper_lifecycle_lock:
        if retire:
            # Publish the fence before capturing. A starter already inside the
            # lifecycle lock completes first and is therefore included below;
            # every later starter observes the closed admission state.
            _keeper_retired = True
        with _state_lock:
            keepers = list(_keepers.values())
            for k in keepers:
                k.stop()
        # Brief wait for thread exits; daemonized so they won't block shutdown.
        # Keep any survivor published so later teardown/start still has its
        # only stop/join handle.
        deadline = _now() + timeout
        for k in keepers:
            remaining = max(0.0, deadline - _now())
            try:
                if (k._thread is not threading.current_thread()
                        and k._thread.is_alive()):
                    k._thread.join(timeout=remaining)
            except Exception:
                pass
        with _state_lock:
            survivors = []
            for k in keepers:
                key = (k.site_id, k.account_idx)
                try:
                    alive = (k._thread is threading.current_thread()
                             or k._thread.is_alive())
                except Exception:
                    alive = True
                if alive:
                    survivors.append(k)
                elif _keepers.get(key) is k:
                    _keepers.pop(key, None)
        return not survivors


def open_lifecycle() -> bool:
    """Open a new keeper generation only when no prior handle is live."""
    global _keeper_retired
    with _keeper_lifecycle_lock:
        with _state_lock:
            current = threading.current_thread()
            for key, keeper in tuple(_keepers.items()):
                try:
                    alive = (keeper._thread is current
                             or keeper._thread.is_alive())
                except Exception:
                    alive = True
                if alive:
                    return False
                if _keepers.get(key) is keeper:
                    _keepers.pop(key, None)
        _keeper_retired = False
        return True


def get_status() -> list[dict]:
    """Return a list of all keeper states for the UI."""
    with _state_lock:
        out = []
        for (sid, idx), keeper in _keepers.items():
            s = dict(keeper.state)
            s["site_id"] = sid
            s["account_idx"] = idx
            out.append(s)
        return out


def get_status_for(site_id: str, account_idx: int = 0) -> dict | None:
    """State for a single keeper."""
    with _state_lock:
        keeper = _keepers.get((site_id, account_idx))
        if not keeper: return None
        s = dict(keeper.state)
        s["site_id"] = site_id
        s["account_idx"] = account_idx
        return s


def force_check(site_id: str, account_idx: int = 0) -> bool:
    """Wake the keeper's loop so the next check runs now instead of at
    its scheduled time. Returns True if the keeper exists."""
    with _state_lock:
        keeper = _keepers.get((site_id, account_idx))
        if not keeper: return False
        keeper.force_check_now()
        return True


def update_config(site_id: str, account_idx: int, site_config: dict):
    """Notify the keeper that its site config changed. Used when the
    user edits credentials, toggles keep_alive_enabled, etc."""
    with _state_lock:
        keeper = _keepers.get((site_id, account_idx))
        if keeper:
            keeper.config = site_config
            keeper.force_check_now()
