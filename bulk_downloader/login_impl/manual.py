"""login_impl.manual -- verbatim cluster from login.py @v447 (DECOMP-LEAF cut 3)."""

import queue
import sys
import threading
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from ..constants import STEALTH_JS
from ..cookies import pw_to_json
from ..learn import RECORDER_JS


_MANUAL_LOGIN_BANNER_JS = r"""
(() => {
  if (window.__bd_manual_banner) return;
  window.__bd_manual_banner = true;
  let dismissedAt = 0;  // unix ms; user-dismissed banner stays gone for 60s

  function install() {
    if (Date.now() - dismissedAt < 60000) return;  // user dismissed recently
    if (document.getElementById('bd-manual-banner')) return;
    if (!document.body) return;
    const bar = document.createElement('div');
    bar.id = 'bd-manual-banner';
    bar.setAttribute('style', [
      'position:fixed','top:0','left:0','right:0','z-index:2147483647',
      'background:linear-gradient(90deg,#6366f1,#8b5cf6)','color:white',
      'padding:10px 16px','font:600 13px/1.4 system-ui,sans-serif',
      'display:flex','align-items:center','gap:12px',
      'box-shadow:0 2px 8px rgba(0,0,0,0.3)',
      // Anti-removal: high specificity styles + !important on a few key props
      'pointer-events:auto !important','visibility:visible !important',
      'opacity:1 !important','transform:none !important',
    ].join(';'));
    bar.innerHTML =
      '<span style="font-size:18px;flex-shrink:0">\ud83d\udd13</span>' +
      '<div style="flex:1">' +
        '<div>Bulk Downloader: <b>finish logging in</b>, then go back to the app and click <b>"I\'m Done"</b>.</div>' +
        '<div style="font-weight:400;opacity:0.85;font-size:11px;margin-top:2px">' +
        'Your username/password may already be filled. Solve any captcha, click submit, then return to the app.</div>' +
      '</div>' +
      '<button id="bd-manual-dismiss" style="background:rgba(255,255,255,0.2);' +
      'border:0;color:white;padding:6px 12px;border-radius:4px;cursor:pointer;' +
      'font:600 12px system-ui,sans-serif">Dismiss</button>';
    document.body.appendChild(bar);
    document.getElementById('bd-manual-dismiss').onclick = () => {
      dismissedAt = Date.now();
      bar.remove();
    };
    // Push site content down so the banner doesn't overlap fixed headers
    if (!document.body.style.paddingTop || parseInt(document.body.style.paddingTop) < 50) {
      document.body.dataset.bdOldPad = document.body.style.paddingTop || '';
      document.body.style.paddingTop = '52px';
    }
  }

  function startWatching() {
    install();
    // Re-install on URL change (SPA navigation)
    let lastUrl = location.href;
    setInterval(() => {
      if (location.href !== lastUrl) { lastUrl = location.href; install(); }
      else if (!document.getElementById('bd-manual-banner')) install();
    }, 1000);
    // Re-install on DOM replacement (some SPAs swap document.body wholesale)
    try {
      const mo = new MutationObserver(() => {
        if (!document.getElementById('bd-manual-banner')) install();
      });
      mo.observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) { /* MutationObserver missing */ }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startWatching);
  } else {
    startWatching();
  }
})();
"""


def _manual_launch_kwargs(config, headless=False):
    """MOD-1 A-4: build the launch kwargs (args + headless + optional channel)
    shared by .launch() / .launch_persistent_context() for the manual/takeover
    browser. Pure + unit-testable. headless=True is the A-4 remote path (the
    solve browser is screencast to the cockpit); False is the visible default.
    The anti-automation + autofill args are identical either way."""
    launch_args = ["--no-sandbox", "--disable-notifications", "--disable-popup-blocking",
                   "--disable-infobars", "--no-default-browser-check", "--no-first-run",
                   "--password-store=basic",
                   "--enable-features=AutofillEnableAccountWalletStorage,PasswordManagerEnabled",
                   "--disable-features=PushMessaging,Translate,AutomationControlled",
                   "--disable-blink-features=AutomationControlled",
                   "--window-size=1366,800"]
    kwargs = {"headless": bool(headless), "args": launch_args}
    if (config or {}).get("use_real_chrome", True):
        kwargs["channel"] = "chrome"
    return kwargs


def _dispatch_cdp_input(cdp, event):
    """Translate one allowlisted takeover input event (already validated by the
    A-2 route) into a CDP Input call on the solve browser. Best-effort; a single
    dropped input is not fatal. Runs on the session's owning thread only."""
    t = (event or {}).get("type")
    if t in ("mousePressed", "mouseReleased", "mouseMoved"):
        cdp.send("Input.dispatchMouseEvent", {
            "type": t, "x": event.get("x", 0), "y": event.get("y", 0),
            "button": event.get("button", "none"),
            "clickCount": int(event.get("clickCount", 0) or 0)})
    elif t in ("keyDown", "keyUp"):
        cdp.send("Input.dispatchKeyEvent", {
            "type": t, "key": event.get("key", ""), "code": event.get("code", "")})
    elif t == "insertText":
        cdp.send("Input.insertText", {"text": event.get("text", "")})


def _drain_and_dispatch_input(cdp, sid, max_n=16):
    """Pump: drain queued operator input for `sid` and dispatch each to the CDP
    session. Called from the owning thread's idle tick while screencasting."""
    if cdp is None or not sid:
        return
    try:
        from .. import captcha_relay as _cr  # lazy: manual -> captcha_relay edge
        events = _cr.drain_takeover_inputs(sid, max_n=max_n)
    except Exception:
        return
    for ev in events:
        try:
            _dispatch_cdp_input(cdp, ev)
        except Exception:
            pass


class ManualLoginSession:
    """Phase 19.fix: dedicated-thread owner of a Playwright session.

    Playwright's sync API is THREAD-BOUND: a `sync_playwright()` instance
    and any Browser/Context/Page derived from it can only be accessed
    from the thread that created them. Cross-thread calls raise.

    The previous design returned a (pw, browser, ctx) tuple from a Flask
    request handler thread, expecting later requests on different threads
    to use them. That worked only by accident on Werkzeug versions that
    happened to reuse threads — newer Werkzeug spawns a fresh thread per
    request and every cross-thread call would raise. Symptom: the user
    logs in successfully but I'm Done fails immediately, or the cookie
    snapshot poller can't read cookies even before login.

    This class fixes that by owning the playwright instance on a single
    dedicated worker thread for the lifetime of the manual login session.
    All operations are dispatched via a command queue; results come back
    on per-call response queues. The Flask request threads only send
    commands; they never touch playwright objects directly."""

    def __init__(self, config, banner_js, manual_profile_dir=None, headless=False):
        self._config = config
        self._banner_js = banner_js
        # Phase 41.6: if set, use launch_persistent_context for password
        # manager / autofill support. The user installs their preferred
        # extension (1Password, Bitwarden, etc.) once via chrome://extensions
        # in this profile, and it persists across future manual sessions.
        self._manual_profile_dir = manual_profile_dir
        # MOD-1 A-4: when True, _launch opens the solve browser headless so it can
        # be screencast to the cockpit takeover viewer instead of the server
        # display. Default False keeps the pre-A-4 visible behavior byte-for-byte.
        self._headless = bool(headless)
        self._screencast_sid = None  # set by start_screencast(sid)
        self._cmd_q = queue.Queue()
        self._error = None
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name=f"manual-login-{config.get('name','?')[:12]}",
        )
        self._thread.start()
        # Block until the browser is fully open or we hit an error
        self._ready.wait(timeout=45)

    @property
    def ready(self):
        return self._ready.is_set() and self._error is None and not self._closed.is_set()

    @property
    def error(self):
        return self._error

    def _launch(self):
        """Open the browser and prepare the context. Called only from
        the worker thread; never call from outside.

        Phase 41.6: when self._manual_profile_dir is set, uses
        launch_persistent_context so the user's password manager
        extension (and any other extensions they install) persists
        across sessions. The profile dir is separate from worker
        profiles to avoid conflicts."""
        config = self._config
        url = config.get("login_url", "")
        # v3.43.14: enable Chromium's built-in autofill/password manager
        # for the takeover browser so the user's saved passwords can
        # fill the login form. Playwright Chromium disables this by
        # default when running under CDP; we have to opt in explicitly.
        #
        # --password-store=basic     : use local password store. On Windows
        #                              this is the Windows Credential
        #                              Manager. Without this, password
        #                              autofill silently no-ops.
        # --enable-features=AutofillEnableAccountWalletStorage
        #                            : enable saved-password autofill
        #                              and storage. Note: AutomationControlled
        #                              still disabled below (anti-fingerprint)
        #                              and that doesn't affect autofill.
        # NOTE: worker browsers (headless, no human present) do NOT get
        # these flags. Autofill only matters in the takeover context.
        # MOD-1 A-4: launch kwargs (incl. headless) now come from the module-level
        # _manual_launch_kwargs builder so the headless flag is unit-testable and
        # threaded from self._headless. args unchanged from the pre-A-4 list.
        common_kwargs = _manual_launch_kwargs(config, self._headless)
        launch_args = common_kwargs["args"]
        # Combined kwargs accepted by both .launch() and .launch_persistent_context()

        ctx_opts = {"no_viewport": True}
        fp = config.get("fingerprint") or {}
        if fp.get("user_agent"): ctx_opts["user_agent"] = fp["user_agent"]
        if fp.get("timezone"): ctx_opts["timezone_id"] = fp["timezone"]
        if fp.get("locale"): ctx_opts["locale"] = fp["locale"]

        from .. import cloak as _cloak
        browser = None
        ctx = None
        used_pw = None
        backend = None
        # Context options forwarded to whichever backend is chosen; user_agent
        # is hoisted to cloak's explicit param, the rest ride **extra.
        ua_val = ctx_opts.get("user_agent")
        ctx_extra = {k: v for k, v in ctx_opts.items() if k != "user_agent"}
        if self._manual_profile_dir:
            # Persistent profile path — password manager extensions etc. survive.
            # v3.66.141: routed through the shared cloak wrapper so manual login
            # honours the configured backend (cloakbrowser|playwright). The
            # wrapper owns the Playwright lifecycle (pw returned for the caller
            # to stop on the playwright backend; None on the cloak backend).
            extra = dict(ctx_extra)
            if common_kwargs.get("channel"):
                extra["channel"] = common_kwargs["channel"]
            try:
                ctx, used_pw, backend = _cloak.open_persistent_context(
                    user_data_dir=self._manual_profile_dir, headless=self._headless,
                    args=launch_args, user_agent=ua_val, config=config, **extra)
                _cloak.log_choice("manual login", backend, "persistent manual profile")
                sys.stderr.write(
                    f"  manual_login: using persistent profile at {self._manual_profile_dir}\n")
            except Exception as e:
                # Fall back to non-persistent if persistent fails (e.g. channel
                # not installed and Chrome can't run from the bundled binary
                # with a custom user_data_dir on this OS)
                if extra.get("channel"):
                    sys.stderr.write(
                        f"  manual_login: persistent launch (channel=chrome) failed "
                        f"({str(e)[:80]}); retrying with bundled Chromium\n")
                    extra.pop("channel", None)
                    try:
                        ctx, used_pw, backend = _cloak.open_persistent_context(
                            user_data_dir=self._manual_profile_dir, headless=self._headless,
                            args=launch_args, user_agent=ua_val, config=config, **extra)
                        _cloak.log_choice("manual login", backend,
                                          "persistent manual profile (bundled)")
                    except Exception as e2:
                        sys.stderr.write(
                            f"  manual_login: persistent fallback also failed: "
                            f"{str(e2)[:80]}; reverting to non-persistent\n")
                        ctx = None
                else:
                    sys.stderr.write(
                        f"  manual_login: persistent launch failed: {str(e)[:80]}; "
                        f"reverting to non-persistent\n")
        if ctx is None:
            # Non-persistent path — original behavior, no extensions
            extra = {}
            if common_kwargs.get("channel"):
                extra["channel"] = common_kwargs["channel"]
            try:
                browser, used_pw, backend = _cloak.launch_browser(
                    headless=self._headless, args=launch_args, config=config, **extra)
            except Exception as e:
                if extra.get("channel"):
                    sys.stderr.write(f"  manual_login: system Chrome unavailable ({str(e)[:60]}); using bundled\n")
                    extra.pop("channel", None)
                    browser, used_pw, backend = _cloak.launch_browser(
                        headless=self._headless, args=launch_args, config=config, **extra)
                else: raise
            _cloak.log_choice("manual login", backend, "non-persistent")
            ctx = browser.new_context(**ctx_opts)

        if config.get("use_stealth", True):
            try: ctx.add_init_script(STEALTH_JS)
            except Exception: pass
        try: ctx.add_init_script(RECORDER_JS)
        except Exception as e: sys.stderr.write(f"  manual_login: recorder add_init_script failed: {e}\n")
        try: ctx.add_init_script(self._banner_js)
        except Exception as e: sys.stderr.write(f"  manual_login: banner add_init_script failed: {e}\n")

        # For persistent ctx, ctx.pages already contains a blank tab from
        # the launch — reuse it if present, else create one
        page = ctx.pages[0] if (self._manual_profile_dir and ctx.pages) else ctx.new_page()
        # v3.43.56: apply playwright-stealth library if configured
        try:
            from .. import stealth as _stealth
            _stealth.apply_to_page(page, self._config)
        except Exception as e:
            sys.stderr.write(f"  manual_login: stealth library apply failed: {str(e)[:80]}\n")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except PWTimeout:
            pass
        except Exception as e:
            sys.stderr.write(f"  manual_login: navigation failed (browser still open): {e}\n")
        try: page.evaluate(RECORDER_JS)
        except Exception: pass
        try: page.evaluate(self._banner_js)
        except Exception: pass
        # v3.43.14: pre-fill the saved credentials so the user doesn't
        # have to retype them. The password is resolved through the
        # vault (so encrypted-storage backends work transparently).
        # Best-effort — failures are logged but don't block the
        # takeover. The injected JS uses common selectors and tolerates
        # missing fields gracefully.
        try:
            from ..secrets_store import resolve_password as _resolve_pw
            username = config.get("username") or ""
            password = _resolve_pw(config.get("password") or "") or ""
            if username or password:
                # Build a small autofill script that finds the most-likely
                # username/password fields and fills them. Doesn't submit
                # (user might want to review or solve captcha first).
                # Escape values for JS string embedding via json.dumps.
                import json as _json
                autofill_js = (
                    "(() => {"
                    f"const u={_json.dumps(username)},p={_json.dumps(password)};"
                    "function pick(sels){for(const s of sels){const el=document.querySelector(s);"
                    "if(el && el.offsetParent!==null && !el.disabled) return el;} return null;}"
                    "const uf=pick([" 
                    "'input[autocomplete=\"username\"]',"
                    "'input[autocomplete=\"email\"]',"
                    "'input[name=\"username\"]','input[name=\"email\"]','input[name=\"login\"]',"
                    "'input[id*=\"user\" i]','input[id*=\"email\" i]','input[id*=\"login\" i]',"
                    "'input[type=\"email\"]','input[type=\"text\"]:not([type=\"hidden\"])']);"
                    "const pf=pick([" 
                    "'input[autocomplete=\"current-password\"]',"
                    "'input[type=\"password\"]',"
                    "'input[name=\"password\"]','input[id*=\"pass\" i]']);"
                    "if(uf && u){uf.focus();uf.value=u;"
                    "uf.dispatchEvent(new Event('input',{bubbles:true}));"
                    "uf.dispatchEvent(new Event('change',{bubbles:true}));}"
                    "if(pf && p){pf.focus();pf.value=p;"
                    "pf.dispatchEvent(new Event('input',{bubbles:true}));"
                    "pf.dispatchEvent(new Event('change',{bubbles:true}));}"
                    "if(uf)uf.blur();if(pf)pf.blur();"
                    "})();"
                )
                # Run once after load; SPA sites that lazy-mount the form
                # are out of scope for this best-effort autofill.
                page.evaluate(autofill_js)
                sys.stderr.write(f"  manual_login: autofilled credentials for {config.get('name','?')}\n")
        except Exception as e:
            sys.stderr.write(f"  manual_login: autofill failed: {e}\n")
        return browser, ctx, page, used_pw

    def _run(self):
        """Worker thread main loop. Owns playwright; serves commands
        from the queue until shutdown."""
        pw = browser = ctx = None
        try:
            browser, ctx, page, pw = self._launch()
            self._ready.set()
            sys.stderr.write(f"  manual_login: session thread ready ({self._thread.name})\n")
            # Phase 41.4: tolerate transient liveness-probe failures, same as
            # _ManualDownloadSession. 3 consecutive misses before declaring death.
            liveness_misses = 0
            # MOD-1 A-4: screencast state. sc["sid"] is None in visible mode /
            # before start_screencast, and the loop below is then byte-for-byte
            # the pre-A-4 behavior (timeout=2, liveness probe). When active, the
            # loop polls fast so CDP frame events flush to the on-frame handler
            # and queued operator input drains at interactive cadence.
            sc = {"cdp": None, "sid": None, "probe_skip": 0}
            self._sc = sc
            while True:
                sc_on = sc["sid"] is not None
                try:
                    cmd, payload, response_q = self._cmd_q.get(
                        timeout=0.02 if sc_on else 2)
                except queue.Empty:
                    if sc_on:
                        # Pump frames: a short playwright call flushes pending CDP
                        # events (the on-frame handler pushes them), then dispatch
                        # any queued operator input on this owning thread.
                        try:
                            page.wait_for_timeout(15)
                        except Exception:
                            pass
                        _drain_and_dispatch_input(sc["cdp"], sc["sid"])
                        # Throttle the liveness probe to ~2s (100 * 0.02s).
                        sc["probe_skip"] += 1
                        if sc["probe_skip"] < 100:
                            continue
                        sc["probe_skip"] = 0
                    # Idle tick — gentle liveness probe
                    alive = True
                    try:
                        if browser is not None:
                            alive = browser.is_connected()
                        else:
                            _ = ctx.pages
                    except Exception as e:
                        alive = False
                        sys.stderr.write(
                            f"  manual_login: liveness probe raised ({type(e).__name__}: {str(e)[:80]}) "
                            f"[miss {liveness_misses+1}/3]\n")
                    if not alive:
                        liveness_misses += 1
                        if liveness_misses >= 3:
                            sys.stderr.write(
                                "  manual_login: browser disconnected (3 consecutive probes failed) — "
                                f"ending session\n")
                            break
                    else:
                        liveness_misses = 0
                    continue
                liveness_misses = 0
                if cmd == "snapshot":
                    try:
                        cookies = pw_to_json(ctx.cookies())
                        response_q.put(("ok", cookies))
                    except Exception as e:
                        response_q.put(("err", str(e)[:200]))
                elif cmd == "start_screencast":
                    # MOD-1 A-4: open a CDP session on the solve page, route
                    # Page.screencastFrame -> the takeover channel, and start
                    # screencasting. Runs here because Playwright is thread-bound.
                    try:
                        sid = payload
                        cdp = ctx.new_cdp_session(page)

                        def _on_frame(params, _cdp=cdp, _sid=sid):
                            try:
                                from .. import captcha_relay as _cr
                                _cr.push_takeover_frame(_sid, params.get("data", ""))
                            except Exception:
                                pass
                            try:
                                _cdp.send("Page.screencastFrameAck",
                                          {"sessionId": params.get("sessionId")})
                            except Exception:
                                pass

                        cdp.on("Page.screencastFrame", _on_frame)
                        cdp.send("Page.startScreencast", {
                            "format": "jpeg", "quality": 70,
                            "maxWidth": 1280, "maxHeight": 720, "everyNthFrame": 2})
                        sc["cdp"] = cdp
                        sc["sid"] = sid
                        response_q.put(("ok",))
                    except Exception as e:
                        response_q.put(("err", str(e)[:200]))
                elif cmd == "finalize":
                    try:
                        from ..learn import harvest_recordings
                        harvest = harvest_recordings(ctx)
                    except Exception as e:
                        sys.stderr.write(f"  manual_login: harvest failed: {e}\n")
                        harvest = {"clicks": [], "inputs": []}
                    try:
                        cookies = pw_to_json(ctx.cookies())
                        response_q.put(("ok", cookies, harvest))
                    except Exception as e:
                        response_q.put(("err", str(e)[:200], harvest))
                    break
                elif cmd == "cancel":
                    response_q.put(("ok",))
                    break
        except Exception as e:
            self._error = f"{type(e).__name__}: {str(e)[:200]}"
            sys.stderr.write(f"  manual_login: session thread error: {self._error}\n")
            self._ready.set()
        finally:
            # v3.36.8: close ctx FIRST, then browser, then pw. For persistent
            # profile sessions (Phase 41.6), `browser` is None and `ctx` IS
            # the persistent context — ctx.close() is what flushes its
            # storage state to the profile dir (cookies, extension state,
            # localStorage). Without it, the password manager extension
            # state could be lost between manual login sessions.
            try:
                if ctx is not None: ctx.close()
            except Exception: pass
            try:
                if browser: browser.close()
            except Exception: pass
            try:
                if pw: pw.stop()
            except Exception: pass
            self._closed.set()
            sys.stderr.write("  manual_login: session thread exited\n")

    def start_screencast(self, sid, timeout=15):
        """MOD-1 A-4: begin screencasting the solve browser to takeover channel
        `sid` and enable operator input drain. Safe to call from any thread --
        dispatches to the owning worker thread (Playwright is thread-bound).
        Returns True on success. No-op-safe if the session is closed."""
        if self._closed.is_set() or not self._ready.is_set() or self._error:
            return False
        self._screencast_sid = sid
        rq = queue.Queue()
        try:
            self._cmd_q.put(("start_screencast", sid, rq))
        except Exception:
            return False
        try:
            result = rq.get(timeout=timeout)
        except queue.Empty:
            return False
        return bool(result and result[0] == "ok")

    def snapshot_cookies(self, timeout=10):
        """Return cookies from the live ctx. Returns None on error or
        if the session is already closed. Safe to call from any thread."""
        if self._closed.is_set() or not self._ready.is_set(): return None
        if self._error: return None
        rq = queue.Queue()
        try:
            self._cmd_q.put(("snapshot", None, rq))
        except Exception:
            return None
        try:
            result = rq.get(timeout=timeout)
        except queue.Empty:
            return None
        if result[0] == "ok": return result[1]
        return None

    def finalize(self, timeout=30):
        """Read final cookies + harvest recordings, then close the
        session. Returns (ok, message, cookies, harvest). Safe to call
        from any thread."""
        if self._error:
            return False, f"session error: {self._error}", [], {"clicks":[],"inputs":[]}
        if self._closed.is_set():
            return False, "session already closed", [], {"clicks":[],"inputs":[]}
        if not self._ready.is_set():
            return False, "session not ready", [], {"clicks":[],"inputs":[]}
        rq = queue.Queue()
        try:
            self._cmd_q.put(("finalize", None, rq))
        except Exception as e:
            return False, f"command queue error: {e}", [], {"clicks":[],"inputs":[]}
        try:
            result = rq.get(timeout=timeout)
        except queue.Empty:
            return False, "finalize timed out", [], {"clicks":[],"inputs":[]}
        # Wait for thread cleanup so the browser actually closes
        self._closed.wait(timeout=10)
        if result[0] == "ok":
            cookies, harvest = result[1], result[2]
            return True, f"captured {len(cookies)} cookies", cookies, harvest
        else:
            harvest = result[2] if len(result) > 2 else {"clicks":[],"inputs":[]}
            return False, f"cookie read failed: {result[1]}", [], harvest

    def cancel(self, timeout=10):
        """Close the session without capturing anything. Safe to call
        from any thread."""
        if self._closed.is_set(): return
        rq = queue.Queue()
        try:
            self._cmd_q.put(("cancel", None, rq))
        except Exception:
            pass
        try: rq.get(timeout=timeout)
        except queue.Empty: pass
        self._closed.wait(timeout=10)


def open_manual_login_browser(config, manual_profile_dir=None, headless=False):
    """Phase 19.fix: now returns a ManualLoginSession (thread-owned)
    instead of a raw (pw, browser, ctx) tuple. Same name kept so callers
    don't need to change. The session object exposes snapshot_cookies(),
    finalize(), and cancel() — all of which are safe to call from any
    thread, because they dispatch via a command queue to the worker
    thread that actually owns the playwright instance.

    Phase 41.6: manual_profile_dir, when provided, enables a persistent
    profile (launch_persistent_context) so the user's browser extensions
    (e.g. 1Password, Bitwarden) survive across sessions. Pass None for
    the legacy fresh-context behavior."""
    url = config.get("login_url", "")
    if not url or not url.startswith("http"):
        raise ValueError("login_url must be an http(s) URL")
    session = ManualLoginSession(config, _MANUAL_LOGIN_BANNER_JS,
                                 manual_profile_dir=manual_profile_dir,
                                 headless=headless)
    if session.error:
        raise RuntimeError(f"manual login session failed to start: {session.error}")
    if not session.ready:
        raise RuntimeError("manual login session timed out before ready")
    return session


def finalize_manual_login(handle):
    """Wrapper for runner-side compatibility. `handle` may be either:
      - a ManualLoginSession (new path, recommended)
      - a legacy (pw, browser, ctx) tuple — fall through to old logic
        for backward compat during upgrade. Should not normally happen."""
    if isinstance(handle, ManualLoginSession):
        return handle.finalize()
    # Legacy tuple path — kept just in case
    pw, browser, ctx = handle
    cookies = []
    harvest = {"clicks": [], "inputs": []}
    err = None
    try:
        from ..learn import harvest_recordings
        harvest = harvest_recordings(ctx)
    except Exception as e:
        sys.stderr.write(f"  manual login: harvest failed: {e}\n")
    try:
        cookies = pw_to_json(ctx.cookies())
    except Exception as e:
        err = str(e)[:120]
    try: browser.close()
    except Exception: pass
    try: pw.stop()
    except Exception: pass
    if err: return False, f"Failed to read cookies: {err}", [], harvest
    return True, f"Manual login captured {len(cookies)} cookies", cookies, harvest


def cancel_manual_login(handle):
    """Wrapper for runner-side compatibility. Accepts session or tuple."""
    if isinstance(handle, ManualLoginSession):
        handle.cancel()
        return
    # Legacy tuple path
    pw, browser, ctx = handle
    try: browser.close()
    except Exception: pass
    try: pw.stop()
    except Exception: pass
