"""login_impl.replay -- verbatim cluster from login.py @v447 (DECOMP-LEAF cut 3)."""

import time
from ._common import _fire_login_trigger_if_needed, _ms_since


_AUTH_COOKIE_HINTS = (
    "auth", "login", "logged", "remember", "passport", "credential",
    "jwt", "account",
    "userid", "user_id", "_user",
)


_GENERIC_SESSION_COOKIE_HINTS = ("session", "sessid", "_sess")


_NOT_AUTH_COOKIE_HINTS = ("csrf", "xsrf", "consent", "gdpr", "cookie_notice")


def _path_prefix_match(candidate, prefix):
    """True if `candidate` equals `prefix` or extends it at a path-segment
    boundary (`prefix` + '/'). Prevents a same-origin prefix sibling such as
    '/dashboard-evil' from being accepted against a configured success path
    '/dashboard' (a bare str.startswith would accept it). A `prefix` of '/'
    (root) still matches any absolute path, since '/'.rstrip('/') + '/' == '/'.
    """
    if candidate == prefix:
        return True
    return candidate.startswith(prefix.rstrip("/") + "/")


def _success_url_matches(success_url, final_url):
    """Decide whether final_url indicates we landed on the configured
    success page. v3.65.2: replaces a naive `success_url in final_url`
    substring check which produced false positives in two realistic
    paths:

      success_url=https://site.com/login
      final_url=https://site.com/login?error=invalid_password
        → substring check passed; login actually failed.

      success_url=/dashboard
      final_url=https://site.com/login?return=/dashboard
        → substring check passed; user is still on the login page.

    Compare scheme+host+path properly. A path-only success_url (starts
    with '/') matches when final_url's path starts with that path.
    A full success_url matches when scheme+netloc match exactly and
    final_url's path starts with success_url's path.

    Additionally: if final_url carries an error-indicator query
    parameter (?error=..., ?fail=..., ?invalid=..., ?login_error=...),
    treat as a non-match even when paths align. This catches the
    common "site.com/login → site.com/login?error=..." bounce that
    a path-only comparison would otherwise accept.
    """
    if not success_url or not final_url:
        return False
    try:
        from urllib.parse import urlsplit, parse_qs
        fx = urlsplit(final_url)
        # Error-query short-circuit. Sites use varied conventions; cover
        # the common ones. Presence of the KEY is the signal — the value
        # is often a code we don't want to interpret. Keep this list
        # conservative; false negatives here just mean a needless
        # manual takeover, which is the safer direction.
        if fx.query:
            qs = parse_qs(fx.query)
            _ERROR_QS_KEYS = {"error", "err", "fail", "failure",
                              "invalid", "denied", "login_error",
                              "auth_error", "failed"}
            if any(k.lower() in _ERROR_QS_KEYS for k in qs):
                return False
        if success_url.startswith("/"):
            return _path_prefix_match(fx.path, success_url)
        sx = urlsplit(success_url)
        if not sx.netloc:
            # Bare host or no scheme — fall through to a tighter
            # startswith match against scheme://host/path only,
            # ignoring query/fragment in final_url.
            stripped_final = f"{fx.scheme}://{fx.netloc}{fx.path}"
            return _path_prefix_match(stripped_final, success_url)
        if sx.scheme != fx.scheme or sx.netloc != fx.netloc:
            return False
        sx_path = sx.path or "/"
        fx_path = fx.path or "/"
        return _path_prefix_match(fx_path, sx_path)
    except Exception:
        # Conservative fallback: only treat as match if the configured
        # success_url is a strict prefix of final_url. Still safer than
        # arbitrary substring containment.
        return final_url.startswith(success_url)


def _looks_authenticated(cookies, *, before_cookies=()):
    """Decide whether a captured cookie jar plausibly belongs to a
    logged-in session. Used when a login page closes mid-submit and
    there is no navigation signal to confirm the login.

    A single stray cookie — analytics, a CSRF token, a consent flag —
    must NOT read as "logged in": that over-accepts a failed login,
    and a login wrongly reported successful only fails later, on the
    download (see OPEN_THREADS / DANGER_MAP). So require a real
    signal, biasing toward a false negative (a needless manual
    takeover is safe; a false success is not):

      - a cookie whose NAME explicitly looks like authentication state, or
      - at least four substantial cookies with names absent before submit.

    Submit recovery supplies before_cookies; None means the snapshot could
    not be read and Signal 2 is unavailable. Single-jar diagnostic callers
    retain the historical jar-shape heuristic with the empty default; they
    do not establish that a submit succeeded.

    Generic server-side session names are deliberately not authentication
    evidence. A login page can assign PHPSESSID/sessionid before credentials
    are submitted and leave it unchanged after a failed login.

    Returns (ok, reason)."""
    non_empty = [c for c in (cookies or []) if c.get("value")]
    # Signal 1: an explicitly auth-named cookie (value need not be long —
    # `logged_in=1` is a perfectly good signal). Generic session identifiers
    # merely bind anonymous server state and are not proof of a login.
    generic_named = []
    for c in non_empty:
        name = str(c.get("name", "")).lower()
        if any(bad in name for bad in _NOT_AUTH_COOKIE_HINTS):
            continue
        if any(h in name for h in _AUTH_COOKIE_HINTS):
            return True, f"auth-looking cookie {c.get('name')!r}"
        if any(h in name for h in _GENERIC_SESSION_COOKIE_HINTS):
            generic_named.append(c.get("name"))
    # Signal 2: pre-existing cookies (even with rotated values) say nothing
    # about this submit. Count new names, not copies across domain/path scopes.
    if before_cookies is None:
        return False, "pre-submit cookie snapshot unavailable; no auth-looking cookie"
    before_names = {c.get("name") for c in before_cookies}
    substantial = [c for c in non_empty
                   if len(str(c.get("value", ""))) > 8]
    new_substantial = {c.get("name") for c in substantial
                       if c.get("name") and c.get("name") not in before_names}
    if len(new_substantial) >= 4:
        return True, f"{len(new_substantial)} new substantial cookies"
    return False, (f"{len(non_empty)} cookie(s), {len(substantial)} "
                   f"substantial, {len(new_substantial)} new substantial, "
                   f"{len(generic_named)} generic-session, "
                   "none explicitly auth-looking")


def replay_saved_login_flow(page, config):
    """Drive a saved cross-origin N-step login flow for this site, if one was
    captured (login_flow_recorder). Returns
    ``{"ran": bool, "ok": bool, "error": str, "steps": int}`` — ``{"ran": False}``
    when no usable flow exists, in which case ``do_login`` proceeds unchanged
    through its single-form selector sweep.

    The credential never sits in the plan: the username fills the credential
    field and the password stays the vault marker, which ``replay_macro``
    resolves from the secrets store at the last moment (keyed by site_id).

    LIVE browser drive — verified on stash. The pure plan
    (``login_flow_recorder.plan_login_flow``) is unit-tested offline. (v3.66.302)
    """
    try:
        from .. import login_flow_recorder as _lfr
        from .. import macro_recorder as _mr
    except Exception:
        return {"ran": False}
    sid = config.get("site_id") or config.get("sid") or ""
    if not sid:
        return {"ran": False}
    try:
        flows = _lfr.list_login_flows(site_id=sid) or []
    except Exception:
        return {"ran": False}
    if not flows:
        return {"ran": False}
    # list_macros returns summaries without `actions`; fetch the full bundle.
    name = (flows[-1].get("metadata") or {}).get("name") or "login"
    bundle = _lfr.get_login_flow(sid, name)
    actions = (bundle or {}).get("actions") or []
    if not actions:
        return {"ran": False}
    plan = _lfr.plan_login_flow(bundle, username=config.get("username") or "")
    res = _mr.replay_macro(page, {"actions": plan},
                           site_id=sid, name=name, strict=False)
    return {"ran": True, "ok": bool(res.get("ok")),
            "error": res.get("error", ""), "steps": res.get("executed", 0)}


def verify_login_replay(config, profile_dir, member_url=None,
                          timeout=20.0):
    """After a successful manual takeover wizard completes, replay
    the login HEADLESSLY against the same persistent profile to
    prove that workers can do it automatically. Optionally also
    probe a member-only URL to confirm cookies grant the right
    access.

    Returns a result dict:
      {
        "replay_ok": bool,
        "replay_ms": int,
        "replay_error": str,           # populated when replay_ok=False
        "replay_method": str,          # "cookies_only" or "fresh_login"
        "member_probe_ok": bool|None,  # None if member_url was empty
        "member_probe_ms": int,
        "member_probe_error": str,
        "cookies_expire_in_days": int|None,
        "summary": str,                # one-line for the UI
      }

    Strategy: load the persistent profile (which has the cookies
    from the just-completed manual login), navigate to login_url,
    and check:

      1. If the URL transitions to success_url (or doesn't contain
         the login form anymore) → "cookies_only" pass. Worker can
         skip the credential dance entirely on next run.
      2. If the login form is still present, run the usual fill+
         submit. If it lands on success_url with valid cookies →
         "fresh_login" pass.
      3. Otherwise → fail with diagnostic detail.

    Then if member_url is set, navigate there and check for the
    presence of a login form (a heuristic for "not logged in").
    This catches sites where login.example.com returns 200 with
    cookies set but member.example.com/* still requires re-auth.

    Never closes the persistent profile in a way that loses
    cookies — uses launch_persistent_context with the same path
    as the manual flow.
    """
    from playwright.sync_api import sync_playwright
    import time

    login_url = config.get("login_url", "")
    success_url = config.get("success_url", "") or ""
    if not login_url:
        return {
            "replay_ok": False, "replay_ms": 0,
            "replay_error": "no login_url configured",
            "replay_method": "",
            "member_probe_ok": None, "member_probe_ms": 0,
            "member_probe_error": "",
            "cookies_expire_in_days": None,
            "summary": "Can't verify: login_url is empty.",
        }

    # Cookie expiry sweep (informational — surfaces "cookies will
    # work for N days" in the summary).
    cookies_expire_in_days = _compute_cookie_expiry_days(config)

    started = time.time()
    replay_ok = False
    replay_error = ""
    replay_method = ""
    member_probe_ok = None
    member_probe_ms = 0
    member_probe_error = ""

    try:
        from .. import cloak as _cloak
        launch_args = ["--no-sandbox",
                       "--disable-blink-features=AutomationControlled"]
        verify_extra = {"viewport": {"width": 1366, "height": 800}}
        if config.get("use_real_chrome", True):
            verify_extra["channel"] = "chrome"
        # Use the manual flow's profile dir → same cookies, same local
        # storage, same browser fingerprint. v3.66.141: launched via the
        # shared cloak wrapper (honours the configured backend; the
        # bundled-Chromium fallback is handled by persistent_context).
        with _cloak.persistent_context(
                user_data_dir=profile_dir, headless=True, args=launch_args,
                config=config, **verify_extra) as (ctx, backend):
            _cloak.log_choice("login verify", backend, "persistent")
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                # v3.43.56: apply playwright-stealth if configured
                try:
                    from .. import stealth as _stealth
                    _stealth.apply_to_page(page, config)
                except Exception:
                    pass  # best-effort; not load-bearing for verify
                page.set_default_timeout(int(timeout * 1000))

                # Step 1: navigate to login_url and see what happens.
                try:
                    page.goto(login_url, wait_until="domcontentloaded",
                                timeout=int(timeout * 1000))
                except Exception as e:
                    replay_error = f"navigation failed: {str(e)[:150]}"
                    return _build_verify_result(
                        replay_ok=False, replay_ms=_ms_since(started),
                        replay_error=replay_error, replay_method="",
                        member_probe_ok=None, member_probe_ms=0,
                        member_probe_error="",
                        cookies_expire_in_days=cookies_expire_in_days)

                # Wait a beat for redirects / JS-driven nav
                page.wait_for_timeout(1500)
                current_url = page.url

                # Check 1: did we redirect to success_url (cookies
                # alone were enough)?
                cookies_alone_sufficient = False
                if success_url:
                    # v3.65.2: was `success_url in current_url or
                    # current_url.startswith(success_url)`. The substring
                    # check is unsafe — see _success_url_matches docstring.
                    if _success_url_matches(success_url, current_url):
                        cookies_alone_sufficient = True
                # Check 2: is the login form GONE? (heuristic — no
                # input[type=password] visible). If so, we're either
                # already logged in or on a 404/error page.
                if not cookies_alone_sufficient:
                    try:
                        pw_count = page.locator(
                            "input[type='password']").count()
                        if pw_count == 0:
                            # No password field on the page → likely
                            # already logged in (or hit an error
                            # page; we'll catch that via member probe)
                            cookies_alone_sufficient = True
                    except Exception:
                        pass

                if cookies_alone_sufficient:
                    replay_ok = True
                    replay_method = "cookies_only"
                else:
                    # Step 2: cookies weren't enough, attempt full
                    # fill+submit using the captured credentials +
                    # learned selectors. We replicate just enough of
                    # do_login's logic to test the headless path.
                    fill_ok, fill_msg = _attempt_headless_fill_submit(
                        page, config, timeout=timeout)
                    if fill_ok:
                        # Did we land on success_url?
                        final_url = page.url
                        # v3.65.2: substring check replaced — see
                        # _success_url_matches docstring for the
                        # failure modes that motivated this change.
                        if success_url and _success_url_matches(success_url, final_url):
                            replay_ok = True
                            replay_method = "fresh_login"
                        elif not success_url:
                            # No success_url configured; trust the fill+submit msg
                            replay_ok = True
                            replay_method = "fresh_login"
                        else:
                            replay_ok = False
                            replay_error = (
                                f"login submitted but URL didn't transition to "
                                f"success_url. Final URL: {final_url[:200]}")
                    else:
                        replay_ok = False
                        replay_error = fill_msg

                replay_ms = _ms_since(started)

                # Step 3: member-only URL probe (if configured)
                if member_url and replay_ok:
                    member_started = time.time()
                    try:
                        page.goto(member_url, wait_until="domcontentloaded",
                                    timeout=int(timeout * 1000))
                        page.wait_for_timeout(1000)
                        # Probe: is there a password field? If yes,
                        # we were bounced to login → cookies not
                        # granting member access.
                        try:
                            pw_count = page.locator(
                                "input[type='password']").count()
                            if pw_count > 0:
                                member_probe_ok = False
                                member_probe_error = (
                                    "member URL bounced to a login form "
                                    "(cookies don't grant member access)")
                            else:
                                # Sanity: did the URL match what we
                                # asked for? Cross-host redirect is
                                # also a failure mode.
                                actual = page.url
                                try:
                                    from urllib.parse import urlparse
                                    if (urlparse(actual).netloc.lower()
                                          != urlparse(member_url).netloc.lower()):
                                        member_probe_ok = False
                                        member_probe_error = (
                                            f"member URL redirected to a "
                                            f"different host: {actual[:150]}")
                                    else:
                                        member_probe_ok = True
                                except Exception:
                                    member_probe_ok = True
                        except Exception as e:
                            member_probe_ok = False
                            member_probe_error = (
                                f"probe failed: {str(e)[:100]}")
                    except Exception as e:
                        member_probe_ok = False
                        member_probe_error = (
                            f"navigation to member URL failed: {str(e)[:150]}")
                    member_probe_ms = _ms_since(member_started)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    except Exception as e:
        replay_error = f"verify infra error: {type(e).__name__}: {str(e)[:200]}"

    return _build_verify_result(
        replay_ok=replay_ok, replay_ms=_ms_since(started),
        replay_error=replay_error, replay_method=replay_method,
        member_probe_ok=member_probe_ok, member_probe_ms=member_probe_ms,
        member_probe_error=member_probe_error,
        cookies_expire_in_days=cookies_expire_in_days)


def _build_verify_result(*, replay_ok, replay_ms, replay_error,
                            replay_method, member_probe_ok,
                            member_probe_ms, member_probe_error,
                            cookies_expire_in_days):
    """Compose a user-facing summary string from the structured
    result. The wizard renders this directly in the success card."""
    parts = []
    if replay_ok:
        if replay_method == "cookies_only":
            parts.append(f"Cookies alone reach the member area "
                          f"({replay_ms}ms). Workers will skip the "
                          "credential dance.")
        else:
            parts.append(f"Headless login replayed cleanly "
                          f"({replay_ms}ms). Workers can re-login "
                          "automatically when cookies expire.")
    else:
        parts.append(f"Headless replay failed: {replay_error}")

    if member_probe_ok is True:
        parts.append(f"Member-only URL loads OK ({member_probe_ms}ms).")
    elif member_probe_ok is False:
        parts.append(f"Member-only URL check failed: {member_probe_error}")

    if cookies_expire_in_days is not None:
        if cookies_expire_in_days <= 1:
            parts.append("Warning: cookies expire in <24h.")
        elif cookies_expire_in_days < 7:
            parts.append(f"Cookies expire in ~{cookies_expire_in_days} days.")
        else:
            parts.append(f"Cookies valid for ~{cookies_expire_in_days} days.")

    return {
        "replay_ok": replay_ok,
        "replay_ms": replay_ms,
        "replay_error": replay_error,
        "replay_method": replay_method,
        "member_probe_ok": member_probe_ok,
        "member_probe_ms": member_probe_ms,
        "member_probe_error": member_probe_error,
        "cookies_expire_in_days": cookies_expire_in_days,
        "summary": " ".join(parts),
    }


def _compute_cookie_expiry_days(config):
    """Read cookies/<sid>.json and return the minimum days-until-
    expiry across session-meaningful cookies. Returns None if no
    cookie file or if cookies are session-only (no expires field)."""
    import json
    import time
    from pathlib import Path

    sid = config.get("site_id") or config.get("sid") or ""
    if not sid:
        # Try to infer from cookie_file path
        cf = config.get("cookie_file", "")
        if cf:
            sid = Path(cf).stem
    if not sid:
        return None

    path = Path("cookies") / f"{sid}.json"
    if not path.exists():
        return None
    try:
        cookies = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(cookies, list):
        return None

    now = time.time()
    min_expiry = None
    for c in cookies:
        if not isinstance(c, dict):
            continue
        # Playwright cookie format: expires field, -1 means session
        exp = c.get("expires")
        if exp is None or exp == -1:
            continue
        try:
            exp_f = float(exp)
        except (TypeError, ValueError):
            continue
        if exp_f <= now:
            continue  # already-expired cookies (we ignore — they
                       # got replaced by valid ones if the login worked)
        # Only consider cookies that look authentication-related.
        # A heuristic — names containing session/sess/auth/token/sid.
        # Otherwise the "minimum" is dominated by a trivial 1h
        # marketing cookie. Names are case-insensitive.
        cname = (c.get("name") or "").lower()
        if not any(needle in cname for needle in (
                "session", "sess", "auth", "token", "sid",
                "login", "user", "remember")):
            continue
        days = (exp_f - now) / 86400.0
        if min_expiry is None or days < min_expiry:
            min_expiry = days
    if min_expiry is None:
        return None
    return int(min_expiry)


def _attempt_headless_fill_submit(page, config, timeout=15.0, runner=None):
    """Minimal headless fill+submit using the learned selectors,
    user-configured fields, and a small fallback list. Returns
    (ok, msg). Doesn't replicate the full do_login retry matrix
    — this is a verify pass, we just need ONE working strategy.

    P5-1 (v3.66.32): the three fill/submit loops now dispatch each
    candidate through ``selector_chains.try_step``, which supports
    per-step post-conditions and advance/abort policies. Bare-string
    chains (the common case) parse to default SelectorSteps whose
    behavior is byte-for-byte the legacy "advance on missing-or-threw".
    A richer step (dict with post_condition/advance_on) only changes
    behavior for that one step. ``runner`` (optional) routes per-step
    decisions to ``runner.log_event("selector_step", ...)`` (A3);
    None preserves the prior silent behavior."""
    from .. import selector_chains as _sc

    username = config.get("username", "") or ""
    password_state = "empty"
    try:
        from ..secrets_store import resolve_password_state
        password, password_state = resolve_password_state(
            config.get("password", "")
        )
        password = password or ""
    except Exception:
        password = config.get("password", "") or ""
    if password_state == "locked":
        return False, "credential vault locked; unlock in Settings -> Secrets"
    if password_state == "missing":
        return False, "stored credential missing; repair in Settings -> Secrets"
    if password_state in ("unavailable", "unknown"):
        return False, "credential state unknown; check Settings -> Secrets"
    if not username or not password:
        return False, "no credentials in config (wizard didn't capture them?)"

    # Build selector chains: user-configured first, then learned.
    learned = ((config.get("learned") or {}).get("login") or {})
    configured_user_sels = []
    if config.get("user_field"):
        configured_user_sels.append(config["user_field"])
    learned_user_sels = list(learned.get("user_field") or [])
    trigger_user_sels = configured_user_sels or learned_user_sels
    user_sels = configured_user_sels + learned_user_sels
    user_sels.extend(["input[type='email']", "input[type='text']",
                        "input[name='username']", "input[name='email']"])

    pass_sels = []
    if config.get("pass_field"):
        pass_sels.append(config["pass_field"])
    pass_sels.extend(learned.get("pass_field") or [])
    pass_sels.append("input[type='password']")

    submit_sels = []
    if config.get("submit_btn"):
        submit_sels.append(config["submit_btn"])
    submit_sels.extend(learned.get("submit_btn") or [])
    submit_sels.extend(["button[type='submit']", "input[type='submit']",
                          "button:has-text('Login')",
                          "button:has-text('Sign in')",
                          "button:has-text('Log in')"])

    # P5-1b: cross-site selector reuse (opt-in BD_CROSS_SITE_SELECTORS=1).
    # No-op by default. Tail-appends deduped sister-site selectors.
    from .. import cross_site_selectors as _css
    if _css.enabled():
        _aug = _css.sync_and_augment(config, {"user_field": user_sels,
            "pass_field": pass_sels, "submit_btn": submit_sels})
        user_sels, pass_sels, submit_sels = (
            _aug["user_field"], _aug["pass_field"], _aug["submit_btn"])

    trigger_needed, _trigger_fired, trigger_detail = (
        _fire_login_trigger_if_needed(
            page,
            config.get("login_trigger"),
            trigger_user_sels or user_sels,
        )
    )

    # ── P5-1 chain runner ──────────────────────────────────────────
    # Run one role's chain through try_step. Returns (ok, winning_index,
    # detail). Honors per-step abort: an abort outcome stops the chain
    # and is reported, rather than silently trying the next selector.
    def _run_chain(role, raw_sels, action, *, value=None, nav_probe=None):
        steps = _sc.parse_chain(raw_sels)
        for idx, step in enumerate(steps):
            outcome, detail = _sc.try_step(
                step, page, action, value=value, nav_probe=nav_probe)
            if runner is not None:
                try:
                    runner.log_event("selector_step", (
                        f"{role}: step {idx} [{step.selector}] -> {outcome}"
                        f" ({detail})"), extra={
                            "role": role, "index": idx,
                            "selector": step.selector, "outcome": outcome,
                            "post_condition": step.post_condition,
                            "detail": detail})
                except Exception:
                    pass  # logging must never break the login path
            if outcome == "ok":
                return True, idx, detail
            if outcome == "abort":
                return False, idx, f"aborted at step {idx}: {detail}"
            # outcome == "advance": fall through to next step
        return False, -1, "no step succeeded"

    # Try to fill username
    ok_u, idx_u, det_u = _run_chain("user_field", user_sels, "fill",
                                    value=username)
    if not ok_u:
        if trigger_needed:
            return False, ("login form is hidden behind a trigger: "
                           f"{trigger_detail}; couldn't find username field "
                           f"({det_u})")
        return False, f"couldn't find username field ({det_u})"

    ok_p, idx_p, det_p = _run_chain("pass_field", pass_sels, "fill",
                                    value=password)
    if not ok_p:
        return False, f"couldn't find password field ({det_p})"

    ok_s, idx_s, det_s = _run_chain("submit_btn", submit_sels, "click")
    if not ok_s:
        # If a step aborted (e.g. required navigation and got none), do
        # NOT fall back to blind Enter — the abort was the safety signal.
        if det_s.startswith("aborted"):
            return False, f"submit {det_s}"
        # Otherwise last resort — press Enter on the password field.
        try:
            page.locator("input[type='password']").first.press(
                "Enter", timeout=2000)
        except Exception:
            return False, "couldn't submit form with any selector or Enter key"

    # Wait for any URL transition / settling
    try:
        page.wait_for_load_state("domcontentloaded",
                                    timeout=int(timeout * 1000))
        page.wait_for_timeout(1500)
    except Exception:
        pass
    return True, "fill+submit completed"
