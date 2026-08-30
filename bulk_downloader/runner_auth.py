"""runner_auth -- login (async+manual), cookie/relogin, auth-required, redirect

Extracted from runner.py (SiteRunner) @v3.66.402, PHASE 3 runner cut 6.
Mixin: methods reference self.* only; NO __init__. Import block derived by AST
free-name scan of the moved bodies (matched the seams doc exactly -- no
conditional soft-import blocks in this unit). Cycle rule: nothing from .runner.
"""
import functools, math, sys, threading, time

from .db import db_log, session_event_record
from .login import do_login
from .cookies import cookies_expiry_info
from .constants import RL_RE, BLOCK_HINTS, AUTH_HINTS, AUTH_BODY_RE


def _finite_config_float(raw, default):
    """Coerce a config-sourced value to a FINITE float, falling back to
    ``default`` on a non-numeric OR non-finite (NaN/inf) value.

    A bare ``float()`` accepts ``'nan'``/``'inf'``, and the fixed-age
    pre-emptive-relogin gate that consumes ``cookie_max_age_hours``
    (``age < max_age``) then silently misbehaves: with ``inf`` the gate is
    always True so relogin is DISABLED; with ``nan`` it over-fires
    (throttled hourly) (F-RUN02-01). Rejecting non-finite here restores the
    intended gate on a hand-edited / overlaid / corrupt config value.
    """
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(v):
        return float(default)
    return v


_TAKEOVER_MODES = ("visible", "remote", "remote_vnc")


def _auth_start_guard(retired_result, *, on_retired=None):
    """Track auth/manual launch callers until publication or retirement."""
    def decorate(method):
        @functools.wraps(method)
        def guarded(self, *args, **kwargs):
            begin = getattr(self, "_begin_auxiliary_start", None)
            end = getattr(self, "_end_auxiliary_start", None)
            admitted = True if not callable(begin) else begin()
            if admitted is not True:
                if on_retired is not None:
                    on_retired(self, *args, **kwargs)
                return retired_result()
            try:
                return method(self, *args, **kwargs)
            finally:
                if callable(end):
                    end()
        return guarded
    return decorate


def _resolve_retired_login(self, on_done=None, allow_manual=True):
    """Complete the async callback contract when retirement rejects login."""
    del allow_manual
    if on_done is None:
        return
    try:
        on_done(False)
    except Exception as exc:
        sys.stderr.write(f"[{self.site_id}] login on_done raised: {exc}\n")


def _resolve_takeover_mode(config: dict) -> str:
    """MOD-1 A-4 / C-2: resolve how a captcha solve session presents. Reads
    `captcha_takeover_mode` from the merged config; anything not in
    {visible, remote, remote_vnc} falls back to 'visible' (the human/server-
    display path), so a typo or a future value can never silently disable the
    fallback. This is the REQUESTED mode; _resolve_effective_mode applies the
    self-downgrade ladder to it."""
    mode = str((config or {}).get("captcha_takeover_mode", "visible") or "visible").strip().lower()
    return mode if mode in _TAKEOVER_MODES else "visible"


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _takeover_enabled(config: dict) -> bool:
    """MOD-1 A-5a KILL-SWITCH: remote takeover is OFF unless explicitly enabled
    (fail-closed). captcha_takeover_enabled is safety-bearing; absent/false -> off."""
    return _truthy((config or {}).get("captcha_takeover_enabled", False))


def _takeover_max_concurrent(config: dict) -> int:
    """MOD-1 A-5a concurrency cap (floor 1; bad/absent -> default 2)."""
    raw = (config or {}).get("captcha_takeover_max_concurrent", 2)
    try:
        n = int(str(raw).strip() or 2)
    except (TypeError, ValueError):
        n = 2
    return max(1, n)


def _remote_admitted(config: dict, active_count: int) -> bool:
    """MOD-1 A-5a admission: remote (headless + screencast) takeover engages only
    when mode=remote AND the kill-switch is enabled AND we are under the
    concurrency cap. Any gate failing downgrades to VISIBLE -- the solve is never
    blocked, only kept off the remote path."""
    return (_resolve_takeover_mode(config) == "remote"
            and _takeover_enabled(config)
            and active_count < _takeover_max_concurrent(config))


# ── MOD-1 C-2: remote_vnc capability probe + the self-downgrade ladder ───────

_vnc_probe = None  # Optional[Callable[[dict], tuple[bool, str]]]; wired at C-4.


def register_vnc_probe(fn) -> None:
    """MOD-1 C-2: inject the DERIVED vnc-availability probe
    fn(config) -> (available: bool, reason: str). app.py wires the real probe at
    C-4 -- it must OBSERVE the Xvnc/KasmVNC endpoint and never trust a config
    flag claiming the stack is installed. Tests inject to exercise the ladder."""
    global _vnc_probe
    _vnc_probe = fn


def _vnc_available(config: dict):
    """MOD-1 C-2: (available, reason) for the vnc takeover stack. DERIVED, not
    asserted. Delegates to the wired probe; with no probe (the default until
    C-4) it is a stub returning unavailable, so remote_vnc downgrades to remote
    and behaviour is unchanged. A probe that raises or cannot determine the
    state returns available=False -- UNKNOWN DOWNGRADES, never assume vnc works."""
    if _vnc_probe is None:
        return (False, "vnc backend not provisioned")
    try:
        available, reason = _vnc_probe(config)
    except Exception:
        return (False, "vnc probe error")
    return (bool(available), str(reason or ("" if available else "vnc unavailable")))


def _resolve_effective_mode(config: dict, active_count: int):
    """MOD-1 C-2: the self-downgrade ladder. Returns (effective_mode, reason)
    where reason is "" iff effective == requested, else a non-empty operator-
    facing explanation (a silent downgrade is a lie by omission, plan 1.2):

        remote_vnc --(stack absent | probe unknown)--> remote
        remote     --(kill-switch off | over cap)-----> visible
        visible    = always available, never blocked

    The kill-switch masters BOTH remote paths (plan 4.2, fail-closed); ONE
    shared cap governs both (plan 4.3). _remote_admitted is kept intact for A --
    this composes the same gates, it does not rewrite them."""
    requested = _resolve_takeover_mode(config)
    if requested == "visible":
        return ("visible", "")
    # requested is remote or remote_vnc -- both mastered by the kill-switch + cap.
    if not _takeover_enabled(config):
        return ("visible",
                f"requested {requested}, running visible (takeover kill-switch off)")
    if active_count >= _takeover_max_concurrent(config):
        return ("visible",
                f"requested {requested}, running visible (over concurrency cap)")
    if requested == "remote":
        return ("remote", "")
    # requested == remote_vnc: needs the derived stack; unknown/absent -> remote.
    available, why = _vnc_available(config)
    if available:
        return ("remote_vnc", "")
    return ("remote",
            f"requested remote_vnc, running remote ({why or 'vnc unavailable'})")


def _admit_takeover(config: dict, active_count: int):
    """MOD-1 C-4: the runtime entry point for the C-2 ladder. Returns
    (headless, effective_mode, reason). This is what the admission path calls so
    a `remote_vnc` request is a VISIBLE downgrade instead of a silent dead toggle
    (plan 1.2/2): before this, admission used `_remote_admitted`, which only knew
    "remote", so remote_vnc fell to headless=False (visible) with no reason.

    headless is True for any non-visible effective mode: `remote` rides the proven
    Arch-A CDP screencast, and `remote_vnc` rides it too until C-5 gives it a
    dedicated Xvnc display -- it cannot promote past `remote` while `_vnc_probe`
    is the C-4 stub, so no operator is told "real X input" while on synthetic CDP.

    Equivalence: for `remote` and `visible` this yields the same headless verdict
    as the old `_remote_admitted` path; only `remote_vnc` changes."""
    effective, reason = _resolve_effective_mode(config, active_count)
    headless = effective != "visible"
    return (headless, effective, reason)


class AuthMixin:
    @_auth_start_guard(lambda: None, on_retired=_resolve_retired_login)
    def login_async(self,on_done=None,allow_manual=True):
        """Phase 4.4: by default, allow manual takeover when auto-login
        can't complete the form. The Chromium window stays open with the
        page in whatever state we got it to; the user finishes by hand
        and clicks "I'm Done" in the UI to capture cookies.

        Phase 19: if `auto_teach_first_run` is on AND we have no learned
        login selectors yet, route through start_manual_login instead of
        do_login. Saves a guaranteed-to-fail auto-attempt on a brand-new
        site and forces selector capture on the first try."""
        # v3.66.834: single-fire notifier -- every path out of login_async
        # resolves the caller's on_done exactly once, and a raising callback
        # cannot kill the login thread.
        _fired = threading.Event()
        def _fire(ok):
            if on_done is None or _fired.is_set():
                return
            _fired.set()
            try:
                on_done(bool(ok))
            except Exception as _e:
                sys.stderr.write(f"[{self.site_id}] login on_done raised: {_e}\n")
        if self._login_thread and self._login_thread.is_alive():
            # v3.66.834: the anti-orphan guard stands (Phase 19.fix -- no
            # second thread, no second browser) but the caller's callback
            # must not be dropped. Do NOT fire False here: the in-flight
            # login may succeed, and an instant False would convert a slow
            # false-failure into an instant one for concurrent workers.
            if on_done:
                self._await_in_flight_login(self._login_thread, _fire)
            return
        # Phase 19.fix: if a manual login is already pending, login_async
        # is a no-op. Without this, clicking Login while a manual session
        # is open would call start_manual_login again, which under earlier
        # versions could orphan the original browser the user was actually
        # logging in on.
        if getattr(self, "_manual_login_handle", None):
            sys.stderr.write(
                f"  login: manual login already in progress for {self.site_id} "
                f"— click I'm Done in the takeover panel\n")
            _fire(False)
            return
        # Auto-teach: skip the auto chain when nothing's learned yet
        if self.config.get("auto_teach_first_run", True):
            learned = (self.config.get("learned") or {}).get("login") or {}
            has_any = any(learned.get(k) for k in ("user_field","pass_field","submit_btn"))
            if not has_any and self.config.get("login_url","").startswith("http"):
                sys.stderr.write(
                    "  login: auto_teach_first_run on AND no learned selectors — "
                    "switching to Manual Login mode for capture\n")
                ok, msg = self.start_manual_login()
                # Mirror login_async's normal contract: set _login_status,
                # don't raise, let the manual-done flow do the rest.
                self._login_status = ("⏳ " if ok else "✗ ") + msg
                _fire(False)  # not "ok" yet — user must finish manually
                return
        self._login_status="Logging in..."
        # v3.66.834: stamp this attempt so a second caller's watcher can read
        # THIS login's real result instead of inferring it from a shared
        # timestamp any other code path can bump (an expired-jar set_cookies
        # racing a failed login would otherwise read as success).
        self._login_attempt_seq = getattr(self, "_login_attempt_seq", 0) + 1
        _attempt = self._login_attempt_seq
        def _run():
            _settled = threading.Event()
            def _settle(ok):
                if _settled.is_set():
                    return
                _settled.set()
                self._login_outcome = (_attempt, bool(ok))
                _fire(ok)
            try:
                # v3.43.78 (F2): pause session keepers BEFORE do_login spawns
                # its own sync_playwright. The v3.43.52 collision pattern
                # applies here too — a keeper heartbeat running its own
                # sync_playwright in parallel can deadlock the worker-
                # initiated re-login. The keeper detects its torn-down
                # browser on next heartbeat and reconnects automatically;
                # no explicit resume call needed (no such function exists).
                # Best-effort: if session_keeper isn't importable or
                # pause raises, we proceed anyway — that's the same
                # fail-open behavior the keeper's own relogin callback
                # uses (session_keeper.py:721).
                try:
                    from . import session_keeper as _sk
                    _sk.pause_site_keepers(self.site_id)  # INV-001
                except Exception as _e:
                    sys.stderr.write(
                        f"[{self.site_id}] login_async: pause_site_keepers "
                        f"raised (proceeding anyway): {_e}\n")
                result=do_login(self.config,allow_manual_takeover=allow_manual)
                # Manual takeover branch: store handle, set state, return
                if result and result[0]=="MANUAL_PENDING":
                    _,reason,handle=result
                    self._manual_login_handle=handle
                    self._login_status=f"⏳ Manual login required: {reason}"
                    # Don't change self._state — login isn't a worker state.
                    # The UI looks at self._login_status and a flag to render
                    # the takeover banner.
                    _settle(False)
                    return
                ok,msg,cookies=result
                if ok:
                    self.set_cookies(cookies)
                    # Phase 18.fix: signal workers that fresh cookies are available
                    self._cookies_updated_at = time.time()
                    p=self.config.get("cookie_file","")
                    if p:
                        try:
                            from .cookies import save_cookies_to_file
                            save_cookies_to_file(p,cookies)
                        except (OSError, ValueError) as e:
                            # Cookie save failures aren't fatal — session
                            # state survives in-memory; next login refreshes
                            self.log.warning("cookie save to %s failed: %s", p, e)
                    self._login_status=("✓ ")+msg
                    _settle(ok)
                    return
                # ── Phase B (v3.62.2): templated-login failure fallback ──
                # The auto-login failed (ok is False). When this site has a
                # login template applied — i.e. learned.login selectors are
                # present and the first-run manual teach was therefore
                # SKIPPED — a hard failure here would otherwise leave the
                # site dead with no path to recovery (stale template
                # selectors, a site redesign, etc.). So fall back to a
                # manual-login takeover, the exact flow the template-skip
                # bypassed. Only when allow_manual is on and a window can be
                # shown; worker-initiated relogins (allow_manual=False) keep
                # the old behaviour and just report ✗ for the worker's
                # auth-retry backoff to handle.
                # NOTE: do_login already converts most POST-page-load
                # failures into MANUAL_PENDING (handled above). This branch
                # catches the residue — page-load/network failures and any
                # other (False, ...) return — for templated sites only.
                learned_login = (self.config.get("learned") or {}).get("login") or {}
                had_template = any(learned_login.get(k) for k in
                                   ("user_field","pass_field","submit_btn"))
                if (not ok and allow_manual and had_template
                        and not getattr(self, "_manual_login_handle", None)
                        and self.config.get("login_url","").startswith("http")):
                    sys.stderr.write(
                        f"  login: templated auto-login failed for "
                        f"{self.site_id} ({msg}) — falling back to manual "
                        f"login takeover\n")
                    fallback_detail = (f"templated login failed ({msg}); "
                                       f"opened manual login")
                    self.log_event("login_template_fallback", fallback_detail)
                    # Persist the same lifecycle event that the live L7
                    # contract reads.  log_event() intentionally owns only the
                    # bounded in-memory/SSE stream, so without this write a real
                    # fallback vanished at restart and could never become durable
                    # OPV evidence.
                    try:
                        session_event_record(
                            self.site_id,
                            getattr(self, "_active_account_idx", None),
                            "login_template_fallback",
                            fallback_detail,
                        )
                    except Exception as _e:
                        sys.stderr.write(
                            f"  login: could not persist fallback event for "
                            f"{self.site_id}: {_e}\n")
                    m_ok, m_msg = self.start_manual_login()
                    self._login_status = (
                        f"⏳ Auto-login failed ({msg}) — finish login "
                        f"manually" if m_ok
                        else f"✗ {msg}; manual fallback also failed: {m_msg}")
                    _settle(False)
                    return
                self._login_status=("✗ ")+msg
                _settle(ok)
            except Exception as e:
                self._login_status = ("✗ ")+f"login crashed: {e}"
                sys.stderr.write(f"[{self.site_id}] login thread crashed: {e}\n")
            finally:
                _settle(False)
        login_thread = threading.Thread(
            target=_run, daemon=True, name=f"login-{self.site_id}")
        publish = getattr(self, "_start_owned_auxiliary_thread", None)
        if callable(publish):
            if not publish("_login_thread", login_thread):
                _fire(False)
                return
        else:
            self._login_thread = login_thread
            login_thread.start()
    def _await_in_flight_login(self, thread, fire, timeout=55.0):
        """v3.66.834: resolve a second caller's on_done against the login
        thread that is ALREADY running (the in-flight guard path in
        login_async).

        Success is read from the in-flight attempt's OWN recorded outcome
        (_login_outcome, stamped by _settle with the attempt's sequence
        number), never inferred from _cookies_updated_at. An inferred
        predicate is wrong in both directions: any other code path that
        calls set_cookies -- account switch, transport, the sites API --
        bumps that timestamp, so a FAILED login racing an expired-jar
        set_cookies reads as success and the worker downloads with a dead
        jar; and a second caller entering after the bump but before the
        thread exits captures an already-moved baseline and reads a real
        success as failure. The attempt stamp has neither failure mode.

        timeout stays strictly under the sole consumer's 60 s wait in
        _check_cookies_or_relogin, so the callback lands before that wait
        expires. The two are coupled by contract, not by construction --
        tests/test_v3_66_834_login_on_done_always_fires.py pins it."""
        attempt = getattr(self, "_login_attempt_seq", 0)
        def _watch():
            try:
                thread.join(timeout)
                rec = getattr(self, "_login_outcome", None)
                fire(bool(rec) and rec[0] == attempt and rec[1] is True)
            finally:
                finish = getattr(self, "_finish_tracked_auxiliary_thread", None)
                if callable(finish):
                    finish(threading.current_thread())
        try:
            waiter = threading.Thread(
                target=_watch, daemon=True,
                name=f"login-wait-{self.site_id}")
            publish = getattr(self, "_start_tracked_auxiliary_thread", None)
            if callable(publish):
                if not publish(waiter):
                    fire(False)
            else:
                waiter.start()
        except Exception as _e:
            # Thread exhaustion must not strand the caller: this guard path
            # was a bare `return` before v3.66.834 and could not raise.
            sys.stderr.write(
                f"[{self.site_id}] login watcher could not start: {_e}\n")
            fire(False)
    @_auth_start_guard(lambda: (False, "Site runtime is being deleted"))
    def start_manual_login(self):
        """Phase 19: skip auto-login entirely and open a browser at the
        login URL with the recorder + manual banner active. The user
        completes the login by hand; their clicks get captured and
        classified into learned selectors when they click "I'm Done"
        in the UI.

        Idempotent: if a manual login is already in progress, returns
        success without opening a second browser. This avoids the
        double-start bug where login_async's auto-teach branch and a
        manual click of Manual Login could both fire, leaving an
        orphaned first browser (where the user actually logged in) and
        a fresh second browser (with no cookies) holding the handle.

        Phase 19.fix: also spawns a background thread that polls
        ctx.cookies() every 3 seconds and stores the most recent non-
        empty snapshot to self._manual_cookie_snapshot. This way I'm
        Done uses live snapshot data even if the browser/ctx becomes
        unresponsive between login completion and the user clicking
        I'm Done."""
        # Idempotency: existing handle? Just report it as still open.
        if getattr(self, "_manual_login_handle", None):
            sys.stderr.write(
                f"  manual login already pending for {self.site_id} "
                f"— ignoring duplicate start\n")
            return True, "Manual login window already open"
        # v3.66.835: EXTERNAL callers only -- Phase B calls this ON
        # self._login_thread and the guard refused its own caller.
        if (self._login_thread and self._login_thread.is_alive()
                and self._login_thread is not threading.current_thread()):
            return False, "An auto-login is already running"
        login_url = (self.config.get("login_url") or "").strip()
        if not login_url or not login_url.startswith("http"):
            return False, "Site has no valid login_url configured"

        from .login import open_manual_login_browser
        # Phase 41.6: persistent profile for password manager extensions
        manual_profile = (self._manual_profile_dir()
                          if self.config.get("manual_use_persistent_profile", True)
                          else None)
        # v3.43.52: pause session keepers for this site before
        # launching the takeover. The keeper holds a live
        # sync_playwright on the same profile dir; without this
        # pause the manual launch fights over SingletonLock and
        # crashes with "Sync API inside asyncio loop" or browser
        # launch errors.
        try:
            from . import session_keeper as _sk
            _sk.pause_site_keepers(self.site_id)  # INV-001
        except Exception as e:
            sys.stderr.write(f"  manual_login: keeper pause failed "
                              f"({e}); continuing anyway\n")
        try:
            handle = open_manual_login_browser(self.config, manual_profile_dir=manual_profile)
        except Exception as e:
            return False, f"Couldn't open browser: {str(e)[:120]}"
        if not handle:
            return False, "Browser open returned no handle"
        self._manual_login_handle = handle
        self._manual_cookie_snapshot = []
        snapshot_stop = threading.Event()
        self._manual_snapshot_stop = snapshot_stop
        # Publish the snapshot-poller generation before start so teardown can
        # never miss a thread that outlives the manual browser handle.
        snapshot_thread = threading.Thread(
            target=self._poll_manual_cookies,
            args=(handle, snapshot_stop),
            daemon=True, name=f"manual-poll-{self.site_id}",
        )
        publish = getattr(self, "_start_owned_auxiliary_thread", None)
        try:
            if callable(publish):
                published = publish("_manual_snapshot_thread", snapshot_thread)
            else:
                self._manual_snapshot_thread = snapshot_thread
                snapshot_thread.start()
                published = True
            if not published:
                raise RuntimeError("site runtime retired during manual login")
        except BaseException:
            snapshot_stop.set()
            if self._manual_snapshot_thread is snapshot_thread:
                self._manual_snapshot_thread = None
            if self._manual_snapshot_stop is snapshot_stop:
                self._manual_snapshot_stop = None
            try:
                handle.cancel(timeout=0)
            except Exception:
                pass
            if self._manual_login_handle is handle:
                self._manual_login_handle = None
            raise
        self._login_status = "⏳ Manual login: complete in browser, then click I'm Done"
        sys.stderr.write(f"  manual login started for {self.site_id}: {login_url}\n")
        return True, "Manual login window opened"
    def _poll_manual_cookies(self, session, stop_event):
        """Background poller. Every 3 seconds, asks the manual-login
        session for a cookie snapshot via its thread-safe API. Stops
        when stop_event is set OR snapshot returns None too many
        consecutive times (indicating the session is dead).

        With the ManualLoginSession refactor, cookies are read on the
        session's owner thread (where the playwright instance lives),
        so cross-thread errors that broke the older direct-ctx-access
        version are gone."""
        consecutive_misses = 0
        while not stop_event.is_set():
            stop_event.wait(timeout=3)
            if stop_event.is_set(): break
            cookies = session.snapshot_cookies(timeout=10)
            if cookies is None:
                consecutive_misses += 1
                if consecutive_misses >= 3:
                    sys.stderr.write(
                        "  manual_poll: session not responsive after "
                        f"3 attempts, stopping poller\n")
                    break
                continue
            consecutive_misses = 0
            if cookies:
                self._manual_cookie_snapshot = cookies
    @_auth_start_guard(
        lambda: {"ok": False, "error": "site runtime is being deleted"})
    def start_captcha_solve_session(self, url: str) -> dict:
        """Open a visible browser pointed at `url` so the user can solve
        a captcha challenge by hand. Uses the same manual-login plumbing
        as start_manual_login() but navigates to the captcha URL instead
        of the login URL, and keeps recorded clicks unused (we don't want
        to pollute learned-selectors with captcha solving).

        Idempotent: if a session is already open for this URL, returns
        the existing handle. Returns {'session_id': str, 'ok': bool,
        'error': str | None}."""
        sessions = getattr(self, "_captcha_solve_sessions", None)
        if sessions is None:
            sessions = {}
            self._captcha_solve_sessions = sessions
        existing = sessions.get(url)
        if existing is not None:
            return {"session_id": existing.get("session_id"),
                    "ok": True, "url": url, "reused": True}

        if not url or not url.startswith("http"):
            return {"ok": False, "error": "invalid url"}

        from .login import open_manual_login_browser
        # Build a config copy with login_url overridden — open_manual_login_browser
        # reads login_url and opens that. Captcha takeover wants the captcha URL.
        cfg = dict(self.config)
        cfg["login_url"] = url
        profile = (self._manual_profile_dir()
                   if self.config.get("manual_use_persistent_profile", True)
                   else None)
        # Pause session keepers so they don't fight over the profile dir.
        try:
            from . import session_keeper as _sk
            _sk.pause_site_keepers(self.site_id)  # INV-001
        except Exception:
            pass
        # MOD-1 A-4/A-5a + C-4: route the decision through the self-downgrade
        # ladder. remote/remote_vnc (headless + screencast) engage only when the
        # kill-switch is enabled AND we are under the concurrency cap; any gate
        # failing falls back to VISIBLE -- the server-display path -- so a solve
        # is never blocked, only kept off remote. Unlike the old _remote_admitted
        # call this makes remote_vnc a VISIBLE downgrade (carries a reason)
        # instead of a silent dead toggle.
        from . import takeover as _tk
        headless, eff_mode, downgrade_reason = _admit_takeover(
            self.config, _tk.active_channel_count())

        # MOD-1 C-5: remote_vnc launches a DEDICATED browser on its own Xvnc
        # display, driven through KasmVNC (real X input) -- NOT the CDP
        # screencast. It only reaches here when the C-2 derived probe OBSERVED
        # the stack; a runtime launch failure falls back to remote (screencast)
        # so a solve is never blocked. takeover_vnc.launch opens the kind="vnc"
        # C-1 channel; the later start_solve open_channel(sid) reuses it.
        if eff_mode == "remote_vnc":
            session_id = f"captcha-{self.site_id}-{int(time.time())}"
            try:
                from . import takeover_vnc as _tv
                vsess = _tv.launch(self.config, session_id, url=url)
            except Exception as e:
                sys.stderr.write(
                    f"  captcha takeover: vnc launch failed ({e}); "
                    f"falling back to remote\n")
                eff_mode, headless = "remote", True
                downgrade_reason = ((downgrade_reason + "; ") if downgrade_reason
                                    else "") + "vnc launch failed -> remote"
            else:
                sessions[url] = {"session_id": session_id, "handle": vsess,
                                 "started_at": time.time(), "kind": "vnc"}
                label = eff_mode + (f" (downgraded: {downgrade_reason})"
                                    if downgrade_reason else "")
                self.log_event(
                    "captcha",
                    f"Manual solve session started ({label}) for {url[:60]}",
                    url=url)
                # MOD-1 C-6: hand the cockpit KasmVNC's own web-client URL so the
                # viewer embeds it (Arch B renders through KasmVNC, not the CDP
                # canvas).
                return {"ok": True, "session_id": session_id, "url": url,
                        "mode": eff_mode, "mode_reason": downgrade_reason,
                        "vnc_url": _tv.viewer_url(self.config)}
        try:
            handle = open_manual_login_browser(cfg, manual_profile_dir=profile,
                                               headless=headless)
        except Exception as e:
            # [SAST 3:13pm 13 may] removed dead call: resume_site_keepers does not exist in session_keeper.py.
            # [SAST 3:13pm 13 may] The keeper detects its torn-down browser on next heartbeat and reconnects
            # [SAST 3:13pm 13 may] automatically — see runner.py:4045-4051 and session_keeper.py:755-782.
            # [SAST 3:13pm 13 may] Previous code AttributeError'd here and was silently swallowed.
            return {"ok": False, "error": str(e)}

        session_id = f"captcha-{self.site_id}-{int(time.time())}"
        # In remote mode, begin screencasting the solve browser to this sid so the
        # cockpit viewer (A-3) renders it and forwards operator input (A-2). The
        # takeover channel is opened by captcha_relay.start_solve right after this
        # starter returns; early frames before it opens are harmlessly dropped.
        if headless:
            try:
                handle.start_screencast(session_id)
            except Exception as e:
                sys.stderr.write(f"  captcha takeover: screencast start failed: {e}\n")
        sessions[url] = {"session_id": session_id, "handle": handle, "started_at": time.time()}
        # eff_mode/downgrade_reason come from the ladder (_admit_takeover); surface
        # the reason so a downgrade is never silent (plan 1.2 -- a silent downgrade
        # is a lie by omission).
        label = eff_mode + (f" (downgraded: {downgrade_reason})" if downgrade_reason else "")
        self.log_event("captcha", f"Manual solve session started ({label}) for {url[:60]}", url=url)
        return {"ok": True, "session_id": session_id, "url": url,
                "mode": eff_mode, "mode_reason": downgrade_reason}
    @_auth_start_guard(lambda: False)
    def end_captcha_solve_session(self, url: str, resolution: str = "resolved") -> bool:
        """Close the visible browser for `url`. If resolution=='resolved',
        requeue the URL (status pending) so the worker picks it up on
        the next loop. If 'dismissed', leave it failed.

        Idempotent. Safe to call even if no session is open — returns False."""
        sessions = getattr(self, "_captcha_solve_sessions", None) or {}
        sess = sessions.pop(url, None)
        if sess is None:
            # Resolution can still happen — just no browser to close.
            if resolution == "resolved":
                self._update_job(url, "pending", "Captcha solved manually — retrying")
            return False
        handle = sess.get("handle")
        if sess.get("kind") == "vnc":
            # MOD-1 C-5: a vnc session's handle is a VncTakeoverSession -- close it
            # and its kind="vnc" C-1 channel through the module's teardown.
            try:
                from . import takeover_vnc as _tv
                _tv.teardown(sess.get("session_id"))
            except Exception as e:
                sys.stderr.write(
                    f"[runner] vnc teardown failed (non-fatal): {e}\n")
        elif handle is not None:
            try:
                # We don't want to capture/harvest cookies — the persistent profile
                # already holds them. Just close cleanly.
                if hasattr(handle, "cancel"):
                    handle.cancel()
                elif hasattr(handle, "finalize"):
                    handle.finalize()
            except Exception as e:
                sys.stderr.write(
                    f"[runner] captcha_solve handle close failed (non-fatal): {e}\n"
                )
        # [SAST 3:13pm 13 may] removed dead call: resume_site_keepers does not exist in session_keeper.py.
        # [SAST 3:13pm 13 may] Keeper auto-reconnects on next heartbeat — see runner.py:4045-4051.
        # [SAST 3:13pm 13 may] Previous code AttributeError'd here and was silently swallowed.
        if resolution == "resolved":
            self._update_job(url, "pending", "Captcha solved manually — retrying")
            self.log_event("captcha", f"Manual solve marked resolved for {url[:60]}", url=url)
        else:
            self.log_event("captcha", f"Manual solve dismissed for {url[:60]}", url=url)
        return True
    def finish_manual_login(self):
        """Called by /api/sites/<sid>/login_manual_done. Reads cookies
        from the live Playwright context, harvests recorded clicks for
        learning, saves cookies to the configured file, closes the
        browser. Returns (ok, message).

        Phase 19.fix: when finalize_manual_login can't read cookies from
        the live ctx (browser closed early, ctx died, etc.), fall back
        to the cookie snapshot maintained by the background poller.
        Without this, a flaky browser meant the user's successful login
        was wasted — finalize returned False and we never saw the
        cookies even though they were present 3 seconds earlier."""
        h=getattr(self,"_manual_login_handle",None)
        if not h: return False,"No pending manual login"
        # Stop the snapshot poller — we're about to close the browser
        try:
            stop_ev = getattr(self, "_manual_snapshot_stop", None)
            if stop_ev: stop_ev.set()
        except Exception: pass
        # Save the snapshot before clearing handle so we have a fallback
        snapshot = list(getattr(self, "_manual_cookie_snapshot", []) or [])
        self._manual_login_handle=None
        from .login import finalize_manual_login
        result=finalize_manual_login(h)
        # Phase 5.2: 4-tuple return — last element is the click/input harvest
        if len(result)==4:
            ok,msg,cookies,harvest=result
        else:
            ok,msg,cookies=result; harvest={"clicks":[],"inputs":[]}

        # Phase 19.fix: if live ctx didn't give us cookies, fall back to
        # the polled snapshot. If THAT'S empty too, both sources failed —
        # tell the user clearly what to do.
        if not cookies and snapshot:
            sys.stderr.write(
                f"  manual login: live ctx returned no cookies; "
                f"using snapshot ({len(snapshot)} cookie(s))\n")
            cookies = snapshot
            ok = True  # flip to success since we have a usable snapshot
            msg = f"Manual login captured {len(cookies)} cookies (from snapshot)"
        elif not ok and snapshot:
            sys.stderr.write(
                f"  manual login: live ctx error '{msg[:60]}'; "
                f"using snapshot ({len(snapshot)} cookie(s))\n")
            cookies = snapshot
            ok = True
            msg = f"Manual login captured {len(cookies)} cookies (from snapshot)"
        elif not cookies and not snapshot:
            # Neither worked. Build a clear error.
            ok = False
            msg = ("No cookies captured. Common causes: "
                   "(1) you closed the browser window before clicking I'm Done, "
                   "(2) you're not actually logged in yet — check the URL, or "
                   "(3) the site sets cookies on a domain we can't read. "
                   "Click Manual Login again to retry.")

        if ok and cookies:
            self.set_cookies(cookies)
            p=self.config.get("cookie_file","")
            if p:
                try:
                    from .cookies import save_cookies_to_file
                    save_cookies_to_file(p,cookies)
                except Exception as e:
                    msg+=f" (file save failed: {e})"

        # v3.66.140: propagate the freshly-logged-in browser session from the
        # manual profile (profiles/<sid>/manual) into the app-managed runtime
        # profiles (main / w<N> / keepalive_<N>) so downloads and keepalive
        # reuse the same login. Safe here: the manual browser is already closed
        # by finalize_manual_login above (its profile is flushed to disk) and
        # the session keepers are paused for the duration of a manual login.
        # Only login-continuity storage is copied (cookies + web/session/IDB
        # storage), never the whole profile. `main` (and keepalive_0 when the
        # keeper is enabled) are seeded even if they don't exist yet so the
        # first download after a fresh manual login is logged in.
        if ok:
            try:
                from . import profile_sync
                ensure = ["main"]
                if self.config.get("keep_alive_enabled", True):
                    ensure.append("keepalive_0")
                summ = profile_sync.sync_manual_to_runtime(
                    self.site_id, ensure=ensure)
                synced = list(summ.get("synced", {}).keys())
                if summ.get("skipped_reason"):
                    sys.stderr.write(
                        f"  manual login: profile sync skipped "
                        f"({summ['skipped_reason']})\n")
                elif synced:
                    sys.stderr.write(
                        f"  manual login: synced session into runtime "
                        f"profiles: {', '.join(sorted(synced))}\n")
                    msg += (f" (synced {len(synced)} profile"
                            f"{'s' if len(synced) != 1 else ''})")
                if summ.get("errors"):
                    sys.stderr.write(
                        f"  manual login: profile sync errors: "
                        f"{summ['errors']}\n")
            except Exception as e:
                sys.stderr.write(
                    f"  manual login: profile sync failed ({e})\n")

        # Phase 5.2/5.3: classify recorded events and merge learned
        # selectors into the site config, then persist.
        learned_count=0
        try:
            from .learn import classify_login, merge_learned
            login_url=self.config.get("login_url","") or ""
            sels=classify_login(harvest, login_url=login_url)
            learned_count=sum(1 for v in sels.values() if v)
            # v3.43.49 bug fix: the teach wizard captured the field
            # SELECTORS (user_field / pass_field / submit_btn) but
            # threw away the VALUES the user typed. Headless logins
            # then failed with "Missing credentials" until the user
            # manually re-typed everything into the site edit form.
            # Now: if classify_login recovered values, save them to
            # cfg["username"] / cfg["password"] directly so do_login()
            # can pick them up next run.
            captured_user = sels.pop("username_value", "") or ""
            captured_pass = sels.pop("password_value", "") or ""
            # v3.43.50: same bug class as credentials — the wizard
            # had the login URL and success URL in the harvest but
            # never wrote them to the site config. Caller had to
            # type them in manually. Pop these out of the selector
            # dict before merge_learned sees them.
            captured_login_url = sels.pop("login_url_value", "") or ""
            captured_success_url = sels.pop("success_url_value", "") or ""
            cred_captured = []
            url_captured = []
            # v3.43.49: always overwrite when captured. The common case
            # for re-teaching a site is "the saved credentials are wrong
            # / expired"; not overwriting forces the user to clear the
            # field by hand first. The conservative original (gate on
            # empty existing value) made the bug fix only help the
            # first teach.
            if captured_user:
                self.config["username"] = captured_user
                cred_captured.append("username")
            if captured_pass:
                # Defensive: if the existing password is a vault
                # reference (@cred:label), the user has set up the
                # encrypted store and we MUST NOT overwrite that with
                # plaintext — doing so would silently de-encrypt the
                # credential. Skip the password write in that case and
                # surface a warning so the user knows to update the
                # vault entry instead.
                existing_pw = self.config.get("password", "") or ""
                if existing_pw.startswith("@cred:"):
                    sys.stderr.write(
                        f"  learn: captured password but site uses "
                        f"vault ref ({existing_pw}); not overwriting. "
                        f"Update the vault entry directly via "
                        f"Settings → Secrets.\n")
                else:
                    self.config["password"] = captured_pass
                    cred_captured.append("password")
            # v3.43.50: persist URLs. Always overwrite when the wizard
            # captured them (same reasoning as credentials — re-teach
            # usually means the saved values are wrong). Skip when the
            # captured value is empty (don't blow away a manually-typed
            # URL with no data).
            if captured_login_url:
                old = self.config.get("login_url", "") or ""
                if old != captured_login_url:
                    self.config["login_url"] = captured_login_url
                    url_captured.append("login_url")
            if captured_success_url:
                old = self.config.get("success_url", "") or ""
                if old != captured_success_url:
                    self.config["success_url"] = captured_success_url
                    url_captured.append("success_url")
            if (learned_count or cred_captured or url_captured) and not self._override_suppresses_persist():
                if learned_count:
                    merge_learned(self.config, sels, kind="login")
                # Save back to disk via app's config persistence helper
                try:
                    from . import app as _app
                    if self.site_id in _app.s_cfg:
                        _app.s_cfg[self.site_id]=self.config
                        _app.s_meta[self.site_id]=_app._build_meta(self.config)
                        _app._save_sites_config()
                except Exception as e:
                    self.log.error("learn persist failed: %s", e)
                bits = []
                if learned_count:
                    bits.append(f"{learned_count} selector role{'s' if learned_count>1 else ''}")
                if cred_captured:
                    bits.append("+".join(cred_captured))
                if url_captured:
                    bits.append("+".join(url_captured))
                msg+=f" (learned {', '.join(bits)})"
                trailer = ""
                if cred_captured:
                    trailer += " + " + "/".join(cred_captured)
                if url_captured:
                    trailer += " + " + "/".join(url_captured)
                sys.stderr.write(f"  learn: captured "
                    f"{', '.join(k for k,v in sels.items() if v and (k.endswith('_field') or k.endswith('_btn')))}"
                    f"{trailer}\n")
        except Exception as e:
            self.log.error("learn classify failed: %s", e)

        self._login_status=("✓ " if ok else "✗ ")+msg
        return ok,msg
    def verify_login_after_wizard(self, member_url=""):
        """v3.43.51: post-wizard verification. Spawns a HEADLESS replay
        of the login against the same persistent profile the manual
        takeover used. Confirms workers will be able to log in
        unattended; optionally probes a member-only URL to confirm
        cookies grant the right access.

        Called by `/api/sites/<sid>/login_verify` after the wizard's
        finalize_manual_login() completes. The verification runs
        synchronously and returns the structured result for the UI
        to render.

        Stores the latest result at self._last_verify_result so the
        wizard's polling endpoint can read it without re-running.

        v3.43.52: pause any active session keepers for this site
        before running verify. The keeper holds a live sync_playwright
        context using the SAME profile dir we want to reuse; two sync
        contexts on the same profile fight over SingletonLock and
        cause "Sync API inside asyncio loop" errors. Tear down the
        keeper's browser; it relaunches on its next heartbeat.
        """
        from .login import verify_login_replay
        member_url = (member_url or "").strip() or None
        profile_dir = str(self._manual_profile_dir())
        # Pause keepers for this site → release their Playwright
        # contexts before we spawn ours.
        try:
            from . import session_keeper as _sk
            _sk.pause_site_keepers(self.site_id)  # INV-001
        except Exception as e:
            sys.stderr.write(f"  verify: keeper pause failed ({e}); "
                              "continuing anyway\n")
        try:
            result = verify_login_replay(
                self.config, profile_dir, member_url=member_url,
                timeout=20.0)
        except Exception as e:
            result = {
                "replay_ok": False, "replay_ms": 0,
                "replay_error": f"verify orchestrator crashed: "
                                  f"{type(e).__name__}: {str(e)[:200]}",
                "replay_method": "",
                "member_probe_ok": None, "member_probe_ms": 0,
                "member_probe_error": "",
                "cookies_expire_in_days": None,
                "summary": f"Verify failed to run: {type(e).__name__}",
            }
        self._last_verify_result = result
        return result
    def get_last_verify_result(self):
        """Return the most recent verify result, or None if no
        verification has run for this site yet. Read by the wizard's
        status-polling endpoint."""
        return getattr(self, "_last_verify_result", None)
    def cancel_manual_login_pending(self):
        """Called by /api/sites/<sid>/login_manual_cancel. Closes the
        browser without capturing cookies."""
        h=getattr(self,"_manual_login_handle",None)
        if not h: return False,"No pending manual login"
        # Stop the snapshot poller
        try:
            stop_ev = getattr(self, "_manual_snapshot_stop", None)
            if stop_ev: stop_ev.set()
        except Exception: pass
        self._manual_login_handle=None
        from .login import cancel_manual_login
        cancel_manual_login(h)
        self._login_status="✗ Manual login cancelled"
        return True,"Cancelled"
    def is_awaiting_manual_login(self):
        return getattr(self,"_manual_login_handle",None) is not None
    def _check_redirect(self,page,url):
        """Inspect the current page; return 'rl' if rate-limited, 'auth' if
        bounced to login, or None if the page looks normal. Caller decides
        what to do — auth issues should NOT trigger a 24-hour cooldown."""
        try:
            cur=page.url.lower()
            if any(h in cur for h in BLOCK_HINTS): return "rl"
            if any(h in cur for h in AUTH_HINTS): return "auth"
            try:
                body=page.locator("body").inner_text(timeout=3000)
                if RL_RE.search(body[:3000]): return "rl"
            except Exception: pass
            # In-place login wall (no redirect): check the page HTML for a
            # login-form signal. Catches a session that expired mid-process
            # where the URL didn't change. Detect-side: we recover, never
            # evade.
            try:
                html=page.content()
                if AUTH_BODY_RE.search(html[:20000]): return "auth"
            except Exception: pass
        except Exception: pass
        return None
    def _handle_auth_required(self,url):
        """Cookies/session rejected by the server.

        Phase 18.fix: this used to be fire-and-forget — it called
        login_async() and immediately returned, allowing the worker to
        pull the next URL which also failed auth. With N workers, the
        entire queue would get marked "Session expired" within seconds
        while one re-login was still in flight.

        New behavior:
          1. Clear the _session_ok event so OTHER workers stop pulling
             new URLs from the queue and wait.
          2. Trigger login_async (idempotent — if a login is already in
             flight, this is a no-op).
          3. Block this worker until the login thread finishes, with a
             generous timeout so a stuck login doesn't deadlock workers.
          4. Re-queue the URL so it gets re-tried with fresh cookies.
          5. Set _session_ok to release other workers.

        We do NOT increment the retry counter for auth-required — the URL
        was never really attempted; the cookies were just stale. (If
        login itself fails, that's handled separately and won't re-queue.)"""
        with self._lock:
            j=self.jobs.get(url,{}); retries=j.get("retries",0)
        max_ret=int(self.config.get("max_retries",2))
        if retries>=max_ret:
            # Phase 2 Cut 2.1: retry budget exhausted -> terminal dead-letter (not
            # plain 'failed', which housekeeping could re-pick). Surfaced via
            # /api/queue/dead_letter; the operator requeues explicitly.
            self._update_job(url,"dead_letter","Session expired -- re-login retries exhausted")
            try:
                from .db import db_queue_dead_letter as _dql
                _dql(self.site_id, url, "auth-required, retries exhausted")
            except Exception:
                pass
            db_log(self.site_id,self.config.get("name","?"),url,"failed","",0,"auth-required, retries exhausted")
            return
        if not (self.config.get("username") and self.config.get("password")):
            self._update_job(url,"failed","Session expired — no credentials configured for re-login")
            db_log(self.site_id,self.config.get("name","?"),url,"failed","",0,"auth-required, no creds")
            return

        # Block other workers from pulling new URLs while we recover the session
        self._session_ok.clear()
        self._update_job(url,"pending",
                         f"Session expired — re-logging in (try {retries+1}/{max_ret})",
                         retries=retries+1, retry_after=0)
        try:
            # Trigger login if not already in flight (idempotent), then wait.
            self.login_async()
            login_thread = self._login_thread
            if login_thread is not None:
                # 90s — most logins take 5-15s; the upper bound covers slow
                # CF challenges and 2captcha solves. We don't want to block
                # workers forever if the login is genuinely stuck.
                login_thread.join(timeout=90)
            login_succeeded = bool(self.cookies) and (self._cookies_updated_at > 0)
            if login_succeeded:
                # Re-queue the URL for retry. Goes to the back of the queue,
                # which is fine — by the time it's pulled, all workers have
                # refreshed their context cookies (see worker_loop refresh).
                try: self._url_queue.put(url)
                except Exception: pass
                self._update_job(url,"pending",
                                 "Session refreshed — will retry",
                                 retries=retries+1, retry_after=0)
            else:
                # Login failed (manual takeover required, captcha unsolved,
                # bad credentials, etc.). Mark URL pending with a 60s backoff
                # so _watch_done's retry path will eventually pick it up.
                from . import admission as _adm
                self._update_job(url,"pending",
                                 "Re-login did not complete — will retry later",
                                 retries=retries+1,
                                 retry_after=_adm.next_eligible_retry(
                                     time.time()+60, self.config))
        finally:
            # Release other workers regardless of success/failure
            self._session_ok.set()
    def _cookie_age_hours(self):
        """Phase 63 (v3.38.x): age of the most recent cookie refresh in
        hours. Exposed in /api/status so the UI insight strip (Phase 48)
        can render a cookie-freshness indicator. Triggers pre-emptive
        re-login when over the configured threshold."""
        if not self._cookies_updated_at:
            return None
        return max(0.0, (time.time() - self._cookies_updated_at) / 3600.0)
    def maybe_preemptive_relogin(self):
        """Phase 63: trigger a manual login BEFORE cookies expire, while
        downloads are still working. Heuristic: cookies older than
        `cookie_max_age_hours` (default 168 = 7 days), and not already
        attempted in the last hour.

        Caller responsibility: this is safe to call on every poll —
        the one-shot guard prevents spam, and the login is async (returns
        immediately while the login flow runs in a thread)."""
        # F1.4 / FINDING 7 (v3.66.267): `predictive_relogin_enabled` implies the
        # preemptive gate. Enabling predictive relogin is itself a request to
        # relogin pre-emptively (predictively), so requiring a *separate*
        # `auto_preemptive_relogin` was a silent-no-op footgun (set predictive
        # alone and nothing ever fired). Either flag now arms the feature;
        # default-off (neither set) stays byte-identical to the old behaviour.
        if not (self.config.get("auto_preemptive_relogin", False)
                or self.config.get("predictive_relogin_enabled", False)):
            return False
        if not (self.config.get("username") and self.config.get("password")):
            return False
        age = self._cookie_age_hours()
        if age is None:
            return False
        # F1.4 (v3.66.218): predictive relogin. When opt-in
        # `predictive_relogin_enabled` is set AND we have enough learned
        # session-lifetime observations, the prediction is authoritative:
        # refresh at `fraction` * median(lifetime). With too little data the
        # predictor returns None and we fall back to the fixed-age heuristic
        # below; default-off leaves behaviour byte-identical. Fail-open: any
        # error falls through to the fixed threshold.
        due = None
        if self.config.get("predictive_relogin_enabled", False):
            try:
                from . import relogin_predict as _rp
                from . import db as _db
                _obs = _db.session_lifetime_observations(
                    self.site_id, getattr(self, "_active_account_idx", 0))
                _frac = float(self.config.get(
                    "predictive_relogin_fraction", _rp.DEFAULT_FRACTION)
                    or _rp.DEFAULT_FRACTION)
                due, _reason = _rp.predictive_relogin_due(
                    age * 3600.0, _obs, fraction=_frac)
            except Exception as e:
                self.log_event("preemptive_relogin",
                               f"predictive check failed, using fixed threshold: {e}")
                due = None
        if due is False:
            return False
        if due is None:
            # Fixed-age fallback (the pre-F1.4 behaviour).
            max_age = _finite_config_float(self.config.get("cookie_max_age_hours", 168.0) or 168.0, 168.0)
            if age < max_age:
                return False
        # Throttle: don't try again within an hour of last attempt
        if time.time() - self._preemptive_login_attempted_at < 3600:
            return False
        self._preemptive_login_attempted_at = time.time()
        self.log_event("preemptive_relogin",
                       f"Cookies are {age:.0f}h old — triggering pre-emptive re-login")
        try:
            self.login_async()
        except Exception as e:
            self.log_event("preemptive_relogin", f"Failed to start re-login: {e}")
            return False
        return True
    def _check_cookies_or_relogin(self, url):
        """If all stored cookies are expired and there are no session cookies,
        kick off an automated re-login. Blocks up to 60 s waiting for the
        async login to complete.

        Returns True to continue processing the URL, False if the URL was
        already routed to failed (caller should return). Behavior identical
        to the inline block extracted from _process_one in v3.43.18."""
        if not self.cookies:
            return True
        ei = cookies_expiry_info(self.cookies)
        if ei["expired"] <= 0 or ei["session"] != 0:
            return True
        if self.config.get("username") and self.config.get("password"):
            self._update_job(url, "running", "Cookies expired — re-logging in...")
            ev = threading.Event(); result = [False]
            def _od(ok): result[0] = ok; ev.set()
            self.login_async(on_done=_od); ev.wait(timeout=60)
            if not result[0]:
                self._handle_failure(url, "Auto re-login failed"); return False
            return True
        self._handle_failure(url, "Cookies expired — re-login needed")
        return False
