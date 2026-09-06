"""login_impl.submit -- verbatim cluster from login.py @v447 (DECOMP-LEAF cut 3)."""

import sys
import time
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from ..constants import STEALTH_JS
from ..cookies import pw_to_json
from ._common import (
    _css_escape_for_id,
    _fire_login_trigger_if_needed,
    _try_click,
    _try_fill,
)
from .manual import _MANUAL_LOGIN_BANNER_JS
from .replay import (
    LOGIN_SETTLED_NO_NAV,
    LoginOutcome,
    _looks_authenticated,
    member_state_check,
    replay_saved_login_flow,
)


def _no_nav_verdict(page, config, cookies, why, phase, hard_close):
    """Row 708: decide a login that fired NO navigation.

    The cookie jar no longer decides. Success requires a POSITIVE
    member-state check on the page the run actually read (a matching
    declared success_url, or the template's learned member indicator);
    the page read is kept as evidence either way. Everything else settles
    into the distinct settled-no-nav state, which is not success and is
    not the submit-failed value.

    `why` is the cookie-jar diagnostic, carried into the message so the
    operator still sees what the jar looked like -- as a description, not
    as the verdict. Returns do_login's (verdict, info, cookies) tuple.
    """
    confirmed, member_why, evidence = member_state_check(
        page, config, tag=f"login-{phase}")
    hard_close()
    if confirmed:
        info = (f"OK \u2014 {len(cookies)} cookies ({phase}; {why}; "
                f"member state confirmed: {member_why}; evidence {evidence})")
        sys.stderr.write(f"  login: {info}\n")
        return True, info, cookies
    info = (f"{LOGIN_SETTLED_NO_NAV} ({why}; no navigation; {member_why}"
            f"{'; evidence ' + evidence if evidence else ''}) \u2014 NOT success")
    sys.stderr.write(f"  login: {info}\n")
    return (LoginOutcome(LOGIN_SETTLED_NO_NAV, False, evidence, why),
            info, cookies)


def _staged_password_retry(page, sb_candidates, pf_candidates, password):
    """Two-step (staged) login recovery. Returns (ok, info).

    A comprehensive password-selector list — which includes the catch-all
    ``input[type=password]`` — failing on EVERY selector is the signature of a
    two-step login: the site asks for the email/username first and only renders
    a separate password screen after a Continue/Next click (e.g. Pexels, Auth0,
    Google-style SSO). The password field is not in the DOM yet, so no selector
    can match — adding more selectors cannot help. Click the continue/submit
    affordance, wait for a password field to appear, and re-attempt the fill
    once.

    Self-gating: a single-step form would already have matched in the first
    pass (the field would be present and visible, and the list includes the
    catch-all), so we only reach here on a genuinely absent field. If there is
    no continue affordance or the field still doesn't appear, this returns False
    and the caller falls back to manual takeover exactly as before — the only
    added cost is one continue click + a short wait on a path that was otherwise
    headed straight to manual.

    Factored out of ``login()`` so the orchestration is unit-testable with a
    fake page (the live fill itself needs a real browser).
    """
    cont_ok, cont_info = _try_click(page, sb_candidates, "continue (staged login)")
    if not cont_ok:
        return False, f"staged-login: no continue affordance ({cont_info})"
    sys.stderr.write(
        f"  login: password field absent; clicked continue [{cont_info}] — "
        f"retrying password fill (staged login)\n")
    # Give the next screen a moment to render before re-attempting. Best-effort:
    # if no password field shows up, _try_fill below fails fast and we hand off.
    try:
        page.locator("input[type=password]").first.wait_for(
            state="visible", timeout=6000)
    except Exception:
        pass
    return _try_fill(page, pf_candidates, password, "password (after continue)")


def _wait_captcha_tokens(page,deadline=30):
    """Detect and wait for any of the three major invisible captchas to
    populate their hidden token field. Returns (token_name, seconds_waited)
    if a field was present (regardless of whether it filled in time), or
    (None, 0) if no captcha is on the page."""
    for tok in ("cf-turnstile-response","h-captcha-response","g-recaptcha-response"):
        sel=f"input[name='{tok}']"
        try:
            if page.locator(sel).count()==0: continue
        except Exception: continue
        start=time.time(); end=start+deadline
        while time.time()<end:
            try:
                v=page.locator(sel).first.input_value()
                if v: return tok,time.time()-start
            except Exception: pass
            time.sleep(0.5)
        return tok,deadline
    return None,0


USER_FIELD_FALLBACKS=[
    "input[type=email]",
    "input[autocomplete='username']",
    "input[autocomplete='email']",
    "input[name='email']",
    "input[name='username']",
    "input[name='login']",
    "input[name*='email' i]",
    "input[name*='user' i]",
    "input[name*='login' i]",
    "input[id='email']",
    "input[id='username']",
    "input[id*='email' i]",
    "input[id*='user' i]",
    "input[id*='login' i]",
    "input[placeholder*='email' i]",
    "input[placeholder*='user' i]",
    "input[placeholder*='login' i]",
    "input[aria-label*='email' i]",
    "input[aria-label*='user' i]",
    "input[data-testid*='email' i]",
    "input[data-testid*='user' i]",
    "input[class*='email' i]",
    "input[class*='user' i]",
    # Last-ditch: first text-style input in any form on the page that isn't
    # a hidden, submit, button, password, checkbox, radio, or file input.
    "form input[type='text']",
    "form input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='password']):not([type='checkbox']):not([type='radio']):not([type='file'])",
]


PASS_FIELD_FALLBACKS=[
    "input[type=password]",
    "input[autocomplete='current-password']",
    "input[autocomplete='password']",
    "input[name='password']",
    "input[name='pass']",
    "input[name*='pass' i]",
    "input[id='password']",
    "input[id='pass']",
    "input[id*='pass' i]",
    "input[placeholder*='password' i]",
    "input[placeholder*='pass' i]",
    "input[aria-label*='password' i]",
    "input[aria-label*='pass' i]",
    "input[data-testid*='pass' i]",
    "input[class*='pass' i]",
]


_SUBMIT_TEXTS=["Login","Log In","Sign In","Get Inside","Get In",
               "Continue","Next","Submit","Enter","Members","Access","Go"]


def _build_submit_fallbacks():
    """Build the ordered list of submit-button selectors. Order matters —
    we try the most specific patterns first because the broadest patterns
    (e.g. div:has-text('Login')) match the entire page body containing
    that text, and Playwright's .first picks the OUTERMOST match.

    Sized for SPEED: under 80 selectors total. Per-selector wait timeouts
    in _try_click are ~400ms, so worst-case all-fail walk is ~30s, not
    minutes. Previous version had 350+ selectors and could hang for 15
    minutes before the manual takeover kicked in."""
    out=[]
    # Tier 1: standards-compliant submit elements. Always best.
    out.extend([
        "button[type=submit]",
        "input[type=submit]",
        "input[type=image]",
    ])
    # Tier 2: aria-label exact-ish match (no [* i] regex, just direct)
    for t in _SUBMIT_TEXTS:
        out.append(f"[aria-label='{t}' i]")
    # Tier 3: class/id patterns — framework conventions
    out.extend([
        "[class*='loginbutton' i]",
        "[class*='login-button' i]",
        "[class*='login_button' i]",
        "[class*='login-btn' i]",
        "[class*='loginbtn' i]",
        "[class*='btn-login' i]",
        "[class*='btn-signin' i]",
        "[class*='btn-submit' i]",
        "[class*='submitbutton' i]",
        "[class*='submit-button' i]",
        "[id*='login-btn' i]",
        "[id*='loginbtn' i]",
        "[id='loginsub']",
        "[id='login-submit']",
    ])
    # Tier 4: onclick handlers
    out.extend([
        "[onclick*='login' i]",
        "[onclick*='signin' i]",
        "[onclick*='submit' i]",
    ])
    # Tier 5: EXACT text match across button-like tags. :text-is is exact
    # and ignores descendant text — this only fires on the actual button.
    # Limited to button/input/role=button so we don't match navbar links.
    for t in _SUBMIT_TEXTS:
        out.append(f"button:text-is('{t}')")
        out.append(f"[role='button']:text-is('{t}')")
    # Tier 6: form-scoped buttons — last button inside the form
    out.extend([
        "form button:not([type='button']):not([type='reset'])",
        "form button:last-of-type",
        "form [role='button']:last-of-type",
    ])
    return out


SUBMIT_FALLBACKS=_build_submit_fallbacks()


def _submit_login(page,sb_candidates,pf_candidates):
    """Try nine independent ways to submit the login form. Each method
    is attempted with a short timeout; we declare success the moment the
    page navigates or the URL changes. Returns (ok, method_used).

    Special return value: ('PAGE_CLOSED', reason). Raised when the page
    or browser context is detected as closed mid-attempt, which usually
    means the form auto-submitted on a previous fill and the browser
    is mid-navigation. Caller should NOT treat this as a hard failure
    until cookies have been checked — login may already have succeeded."""
    # Heartbeat at entry — without this, a long selector walk looks
    # identical to a silent hang, which was the visible symptom in v3.15.5.
    sys.stderr.write(f"  login submit: attempting "
                     f"({len(sb_candidates)} button selector(s), 9 methods)\n")
    try: initial_url=page.url
    except Exception as e:
        # Page already closed before we even started — treat like submit
        # never happened so caller can fall back to cookie inspection.
        return "PAGE_CLOSED", f"page already closed: {str(e)[:60]}"
    def _navigated():
        try: return page.url!=initial_url
        except Exception: return False
    def _closed():
        # cheap, non-throwing closed check; a dead context counts as closed
        try: return page.is_closed()
        except Exception: return True
    def _page_closed_err(s):
        s=str(s).lower()
        return ("target page" in s and "closed" in s) or "browser has been closed" in s or "context or browser" in s

    methods=[]

    # Method 1: configured/text-matched submit button click
    def m1():
        ok,info=_try_click(page,sb_candidates,"submit button")
        return ok,f"click [{info}]"
    methods.append(("click submit selector",m1))

    # Method 2: JS form.requestSubmit() — uses the form's default submit
    # path including any submit-event handlers, no button needed
    # v3.65.2: previously called `document.querySelector('form')`, which
    # is the FIRST form in DOM order — a newsletter or search form above
    # the login form would get submitted instead. Now we walk up from the
    # password field via .closest('form'), only falling back to the first
    # form if the password field can't be located.
    def m2():
        try:
            page.evaluate("""(pf_sels) => {
                let f = null;
                for (const sel of pf_sels) {
                    try {
                        const pf = document.querySelector(sel);
                        if (pf) { f = pf.closest('form'); if (f) break; }
                    } catch (e) {}
                }
                if (!f) f = document.querySelector('form');
                if (!f) return false;
                if (typeof f.requestSubmit === 'function') { f.requestSubmit(); return true; }
                return false;
            }""", pf_candidates)
            return True,"form.requestSubmit()"
        except Exception as e: return False,f"requestSubmit error: {str(e)[:60]}"
    methods.append(("JS requestSubmit",m2))

    # Method 3: JS form.submit() — bypasses validation but always works
    # if there's a form; some sites' submit-button onclick is literally
    # `document.forms[0].submit()` so we just call it directly
    # v3.65.2: same scoping fix as Method 2.
    def m3():
        try:
            page.evaluate("""(pf_sels) => {
                let f = null;
                for (const sel of pf_sels) {
                    try {
                        const pf = document.querySelector(sel);
                        if (pf) { f = pf.closest('form'); if (f) break; }
                    } catch (e) {}
                }
                if (!f) f = document.querySelector('form');
                if (f) { f.submit(); return true; }
                return false;
            }""", pf_candidates)
            return True,"form.submit()"
        except Exception as e: return False,f"form.submit error: {str(e)[:60]}"
    methods.append(("JS form.submit",m3))

    # Method 4: Press Enter inside the password field
    def m4():
        for sel in pf_candidates:
            try:
                page.locator(sel).first.press("Enter")
                return True,f"Enter on {sel}"
            except Exception: continue
        return False,"Enter press failed"
    methods.append(("Enter on password",m4))

    # Method 5: Page-level Enter keypress
    def m5():
        try:
            page.keyboard.press("Enter")
            return True,"page Enter"
        except Exception as e: return False,str(e)[:60]
    methods.append(("page Enter",m5))

    # Method 6: Click anything with class containing 'submit'/'login' that's
    # currently visible. Broader sweep than method 1.
    def m6():
        for sel in ("[class*='submit' i]","[class*='login' i][class*='btn' i]",
                    "[onclick*='submit' i]","[onclick*='login' i]"):
            try:
                page.locator(sel).first.click(timeout=2000)
                return True,f"broad click [{sel}]"
            except Exception: continue
        return False,"broad click found nothing"
    methods.append(("broad submit-class click",m6))

    # Method 7: dispatch click event via JS on the login form's first
    # button-like child. Bypasses Playwright's actionability checks
    # entirely — useful when overlays intercept real clicks.
    # v3.65.2: same scoping fix as Methods 2 and 3.
    def m7():
        try:
            r=page.evaluate("""(pf_sels) => {
                let f = null;
                for (const sel of pf_sels) {
                    try {
                        const pf = document.querySelector(sel);
                        if (pf) { f = pf.closest('form'); if (f) break; }
                    } catch (e) {}
                }
                if (!f) f = document.querySelector('form');
                if (!f) return 'no form';
                const cands = f.querySelectorAll('button, [role=button], input[type=submit], div[onclick]');
                for (const c of cands) {
                    if (c.offsetParent !== null) {  // visible
                        c.click();
                        return 'clicked ' + (c.tagName||'?');
                    }
                }
                return 'no visible button';
            }""", pf_candidates)
            if isinstance(r,str) and r.startswith("clicked"):
                return True,f"JS click: {r}"
            return False,f"JS sweep: {r}"
        except Exception as e: return False,str(e)[:60]
    methods.append(("JS click sweep",m7))

    # Method 8: Tab+Enter (some sites require focus-then-submit)
    def m8():
        try:
            page.keyboard.press("Tab"); time.sleep(0.2); page.keyboard.press("Enter")
            return True,"Tab+Enter"
        except Exception as e: return False,str(e)[:60]
    methods.append(("Tab+Enter",m8))

    # Method 9: native JS .click() on the configured submit selector.
    # v3.65.3: Method 1 uses Playwright's loc.click() which dispatches a
    # full mousedown→mouseup→click event sequence — correct for real
    # buttons, but sites that wire onclick via certain JS frameworks
    # (or check event.isTrusted, or only listen for specific event
    # subtypes) can ignore those synthetic events. Method 7's "JS click
    # sweep" only walks tags inside the form (button, [role=button],
    # input[type=submit], div[onclick]) — it misses div submit buttons
    # whose handler is attached via addEventListener with no inline
    # onclick attribute (wowgirls' div.loginform-submit-button is one
    # such case). Method 9 fixes the gap: walk sb_candidates (the
    # learned/configured selectors) and call the DOM element's native
    # .click() — same path the browser's DevTools console uses, no
    # Playwright event synthesis involved. If a console-driven .click()
    # would have worked, this method will too.
    def m9():
        for sel in sb_candidates:
            if not sel: continue
            try:
                r=page.evaluate("""(sel) => {
                    try {
                        const el = document.querySelector(sel);
                        if (!el) return 'no match';
                        if (typeof el.click !== 'function') return 'no click()';
                        el.click();
                        return 'clicked';
                    } catch (e) { return 'error: ' + (e && e.message ? e.message : 'unknown'); }
                }""", sel)
                if r=="clicked":
                    return True,f"native .click() [{sel}]"
            except Exception: continue
        return False,"native .click() found no working selector"
    methods.append(("native JS .click()",m9))

    # Run each method, then wait briefly for navigation. If it happens,
    # we win. If not, fall through to the next method. If the page closes
    # mid-loop (form auto-submitted on fill, navigation already happening),
    # bail out immediately — the remaining methods will all fail the same
    # way and login may already have succeeded.
    for label,fn in methods:
        # Stop once an earlier method already had its effect. If the
        # page has navigated or closed, running further methods only
        # produces "Target page has been closed" noise — and on a
        # still-open page, a redundant second submit. (OPEN_THREADS:
        # the submit loop kept firing methods after the page closed.)
        if _closed():
            sys.stderr.write("  login submit: page already closed — "
                             "stopping method loop\n")
            return "PAGE_CLOSED", f"page closed before {label}"
        if _navigated():
            sys.stderr.write(f"  login submit: already navigated before "
                             f"{label} — earlier method submitted\n")
            return True, f"navigated before {label}"
        try: ok,info=fn()
        except Exception as e: ok,info=False,str(e)[:60]
        if not ok:
            sys.stderr.write(f"  login submit: {label} → skip ({info})\n")
            if _page_closed_err(info):
                sys.stderr.write("  login submit: page closed — bailing early; checking cookies\n")
                return "PAGE_CLOSED", f"page closed during {label}: {info}"
            continue
        sys.stderr.write(f"  login submit: {label} → {info}; waiting...\n")
        # Give the page up to 8 seconds to navigate. We poll URL changes
        # because some forms don't trigger a load event (SPA logins).
        end=time.time()+8
        while time.time()<end:
            if _navigated(): return True,label
            try: page.wait_for_load_state("networkidle",timeout=500)
            except Exception as e:
                if _page_closed_err(e):
                    return "PAGE_CLOSED", f"page closed waiting for {label}"
            if _navigated(): return True,label
            time.sleep(0.3)
        # No navigation? Maybe it's a SPA that just updates auth state
        # silently. Move to the next method.
    return False,"no submit method produced navigation"


def _try_check_remember_me(page):
    """Check the "Remember me" / "Keep me signed in" / "Stay logged in"
    checkbox if present. Best-effort — silently no-ops when not found.

    The benefit: when the cookies returned have longer expiration, the
    runner re-login storm gets less frequent. Many sites' "Remember me"
    extends sessions from session-only cookies (cleared on browser close)
    to weeks/months.

    Selector strategy: we look for inputs of type=checkbox whose label
    text or `name`/`id` attribute contains a remember-me phrase. We try
    both directly clicking the checkbox (works for unstyled native
    checkboxes) and clicking the associated <label> (works for styled
    custom checkboxes that hide the input)."""
    phrases = ["remember", "keep me", "keep logged", "keep signed",
               "stay logged", "stay signed", "rememberme", "remember-me"]
    selectors = []
    # Direct attribute matches on the checkbox itself
    for p in ("remember", "rememberme", "remember_me", "keepme", "keeploggedin"):
        selectors.append(f"input[type=checkbox][name*='{p}' i]")
        selectors.append(f"input[type=checkbox][id*='{p}' i]")
    # Label-adjacent — find a <label> whose text matches, then click it
    # (label clicks toggle the associated input via for=id or wrapping).
    for ph in phrases:
        selectors.append(f"label:has-text('{ph}'):has(input[type=checkbox])")
        selectors.append(f"label:has-text('{ph}')")
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=300)
            # v3.65.2: check the underlying checkbox state regardless of
            # whether the selector targets the input directly or a wrapping
            # label. The original guard was `if "checkbox" in sel and
            # loc.is_checked()` which never fired for bare `label:has-text(...)`
            # selectors — clicking such a label toggles the underlying
            # checkbox via for-id, so a site with Remember Me defaulted ON
            # would get it turned OFF on every login. Now: locate the
            # associated input first via the label's `for` attribute or
            # a descendant input, and skip the click if it's already checked.
            already = False
            try:
                if "checkbox" in sel:
                    already = loc.is_checked()
                elif sel.startswith("label:"):
                    # Try descendant input first (label wraps input pattern)
                    cb = loc.locator("input[type=checkbox]").first
                    if cb.count() > 0:
                        already = cb.is_checked()
                    else:
                        # Fall back to <label for=ID> → input#ID lookup.
                        for_id = loc.get_attribute("for") or ""
                        if for_id:
                            cb = page.locator(
                                f"input[type=checkbox]#{_css_escape_for_id(for_id)}"
                            ).first
                            if cb.count() > 0:
                                already = cb.is_checked()
            except Exception:
                pass
            if already:
                sys.stderr.write("  login: 'Remember me' already checked\n")
                return True
            loc.click(timeout=1000)
            sys.stderr.write(f"  login: clicked 'Remember me' via [{sel[:60]}]\n")
            return True
        except Exception:
            continue
    return False


def do_login(config, allow_manual_takeover=False):
    """Robust login. Tries 25 username selectors, 15 password selectors,
    50+ submit-button selectors, and 9 different submit methods (button
    click, form.requestSubmit, form.submit, Enter on password, page Enter,
    broad class-match click, JS click sweep, Tab+Enter, native JS .click()).

    Phase 4.4: When `allow_manual_takeover=True`, any failure mode that
    has the browser visible to the user returns a special tuple instead
    of closing: `("MANUAL_PENDING", reason, (pw, browser, ctx))`. The
    caller (SiteRunner.login_async) stashes the handle, sets state to
    `awaiting_manual_login`, and waits for the user to click "I'm Done"
    in the UI — at which point cookies are read from the live ctx via
    finalize_manual_login() and the browser is closed.

    On full success: returns (True, info, cookies) and the browser is
    closed cleanly inside this function. On hard failure (network,
    page-load timeout) with allow_manual_takeover=False: returns
    (False, info, []) with the browser closed. Manual takeover is only
    offered for failures *after* the page has loaded successfully —
    nothing useful to do manually if the URL itself didn't load."""
    url=config.get("login_url","")
    username=config.get("username","")
    # v3.43.14: resolve password through secrets_store. If the value is
    # a "@cred:" reference, looks up via the active backend (keychain
    # or master-password); otherwise returns it verbatim (plaintext
    # legacy mode). Transparent to the rest of the function.
    password_state = "empty"
    try:
        from ..secrets_store import resolve_password_state
        password, password_state = resolve_password_state(
            config.get("password", "")
        )
        password = password or ""
    except Exception:
        password = config.get("password","") or ""
    success=config.get("success_url","")
    wait=float(config.get("wait",4))
    # v3.43.14: missing credentials previously failed silently with the
    # bare "Missing credentials" string. With headless=True (default
    # since v3.43.11) the user never sees a window and can't tell what's
    # wrong. Log loudly so the terminal makes it obvious which field is
    # missing.
    if password_state == "locked":
        sys.stderr.write(
            f"  login: SKIPPED — site {config.get('name','?')!r}: credential "
            f"vault is LOCKED; the stored password cannot be decrypted. "
            f"Unlock it in Settings -> Secrets after every service restart.\n")
        return False, "Credential vault locked: password", []
    if password_state == "missing":
        sys.stderr.write(
            f"  login: SKIPPED — site {config.get('name','?')!r}: stored "
            f"credential is MISSING for the password reference. Repair it in "
            f"Settings -> Secrets.\n")
        return False, "Stored credential missing: password", []
    if password_state in ("unavailable", "unknown"):
        sys.stderr.write(
            f"  login: SKIPPED — site {config.get('name','?')!r}: credential "
            f"availability is UNKNOWN; the stored password could not be read. "
            f"Check Settings -> Secrets and the service logs.\n")
        return False, "Credential state unknown: password", []
    if not url or not username or not password:
        missing = []
        if not url: missing.append("login_url")
        if not username: missing.append("username")
        if not password: missing.append("password")
        sys.stderr.write(
            f"  login: SKIPPED — site {config.get('name','?')!r} is missing "
            f"{', '.join(missing)} in its configuration. Open the site's Edit "
            f"form and configure only the fields listed here.\n")
        return False, f"Missing credentials: {', '.join(missing)}", []

    user_uf=(config.get("user_field") or "").strip()
    user_pf=(config.get("pass_field") or "").strip()
    user_sb=(config.get("submit_btn") or "").strip()

    # Phase 5.3: pull learned selectors from previous manual takeovers (if
    # any). Order of priority for each role: user-configured > learned >
    # 154-selector fallback list. Learned selectors live in the `learned`
    # block of the site config, populated by classify_login() after each
    # successful manual takeover.
    learned_block=(config.get("learned") or {}).get("login",{}) if isinstance(config.get("learned"),dict) else {}
    learned_uf=learned_block.get("user_field",[]) or []
    learned_pf=learned_block.get("pass_field",[]) or []
    learned_sb=learned_block.get("submit_btn",[]) or []
    if learned_uf or learned_pf or learned_sb:
        sys.stderr.write(f"  login: replaying learned selectors "
            f"(user={len(learned_uf)}, pass={len(learned_pf)}, submit={len(learned_sb)})\n")

    # The trigger precondition must inspect the known login field, not every
    # generic fallback.  A visible site-search input matching
    # ``input[type=text]`` does not make a configured, hidden ``#username``
    # usable.  Prefer the operator selector, then learned selectors; only use
    # the full fallback chain when the site has no precise username selector.
    trigger_uf_candidates=([user_uf] if user_uf else list(learned_uf))
    uf_candidates=([user_uf] if user_uf else [])+learned_uf+USER_FIELD_FALLBACKS
    pf_candidates=([user_pf] if user_pf else [])+learned_pf+PASS_FIELD_FALLBACKS
    sb_candidates=([user_sb] if user_sb else [])+learned_sb+SUBMIT_FALLBACKS

    # P5-1b: cross-site selector reuse (opt-in BD_CROSS_SITE_SELECTORS=1).
    # No-op by default. Appends a deduped, source-excluded tail of
    # selectors proven on structurally-similar sister sites, inserted
    # before the generic fallbacks (more specific signal than the 154-list).
    from .. import cross_site_selectors as _css
    if _css.enabled():
        _own_uf=([user_uf] if user_uf else [])+learned_uf
        _own_pf=([user_pf] if user_pf else [])+learned_pf
        _own_sb=([user_sb] if user_sb else [])+learned_sb
        _aug=_css.sync_and_augment(config,{"user_field":_own_uf,
            "pass_field":_own_pf,"submit_btn":_own_sb})
        uf_candidates=_aug["user_field"]+USER_FIELD_FALLBACKS
        pf_candidates=_aug["pass_field"]+PASS_FIELD_FALLBACKS
        sb_candidates=_aug["submit_btn"]+SUBMIT_FALLBACKS
        if not trigger_uf_candidates:
            trigger_uf_candidates=list(_aug["user_field"])

    pw=None; browser=None; ctx=None
    def _hard_close():
        try:
            if browser: browser.close()
        except Exception: pass
        try:
            if pw: pw.stop()
        except Exception: pass
    def _hand_off(reason):
        """Return the live browser to the caller for manual takeover.

        Phase 19: also injects a small instructional banner at the top of
        every page in the takeover ctx, so the user sees what's expected
        of them. Without it, the chromium window just sits there with the
        login page and the user has no way to know the app is waiting for
        them to click "I'm Done" in the web UI."""
        sys.stderr.write(f"  login: handing off for manual takeover — {reason}\n")
        # Inject the banner into both: the current page (evaluate runs
        # immediately) and any future navigations (add_init_script). The
        # banner self-skips if already installed, so dual-injection is safe.
        try:
            ctx.add_init_script(_MANUAL_LOGIN_BANNER_JS)
        except Exception as e:
            sys.stderr.write(f"  banner add_init_script failed: {e}\n")
        try:
            page.evaluate(_MANUAL_LOGIN_BANNER_JS)
        except Exception as e:
            sys.stderr.write(f"  banner evaluate failed: {e}\n")
        return ("MANUAL_PENDING", reason, (pw, browser, ctx))

    try:
        pw = None  # owned by the cloak wrapper below (None on the cloak backend)
        # Phase 9: prefer system Chrome over bundled Chromium and add the
        # AutomationControlled disable flag (the second-most-checked stealth
        # tell after navigator.webdriver). Login launch deliberately doesn't
        # use the persistent profile dir — the worker owns that, and we
        # don't want to pollute it with a half-completed login session.
        # --window-size opens the browser at a sensible desktop size; without
        # it, Chrome's default headed window is ~800x600 (cramped for any
        # modern login form).
        # v3.43.14: same autofill enabling as open_manual_login_browser.
        # See that function for the full rationale. Briefly: enables
        # Chromium's password manager and autofill so the user's saved
        # credentials can fill the login form.
        launch_args=["--no-sandbox","--disable-notifications","--disable-popup-blocking",
                     "--disable-infobars","--no-default-browser-check","--no-first-run",
                     "--password-store=basic",
                     "--enable-features=AutofillEnableAccountWalletStorage,PasswordManagerEnabled",
                     "--disable-features=PushMessaging,Translate,AutomationControlled",
                     "--disable-blink-features=AutomationControlled",
                     "--window-size=1366,800"]
        from .. import cloak as _cloak
        login_extra = {}
        if config.get("use_real_chrome",True):
            login_extra["channel"]="chrome"
        try:
            browser, pw, backend = _cloak.launch_browser(
                headless=False, args=launch_args, config=config, **login_extra)
        except Exception as e:
            if login_extra.get("channel"):
                sys.stderr.write(f"  login: system Chrome unavailable ({str(e)[:60]}); using bundled\n")
                login_extra.pop("channel",None)
                browser, pw, backend = _cloak.launch_browser(
                    headless=False, args=launch_args, config=config, **login_extra)
            else:
                raise
        _cloak.log_choice("login", backend, "non-persistent")
        # Phase 7.1: build context options from fingerprint
        ctx_opts={}
        fp=config.get("fingerprint") or {}
        if fp.get("user_agent"): ctx_opts["user_agent"]=fp["user_agent"]
        # NOTE: we deliberately do NOT apply the fingerprint viewport here
        # because this launch is HEADED (the user is interacting). Setting
        # a viewport like 3840x2160 while the actual Chrome window is
        # 1280x720 causes Playwright to render the page at the fingerprint
        # size and scale-fit it into the window — visually a "huge zoom in"
        # on small text fields. We only enforce the viewport for headless
        # automated runs (anti-detection); for headed runs, Chrome's actual
        # window size is used (and looks more like a real user anyway).
        if fp.get("timezone"): ctx_opts["timezone_id"]=fp["timezone"]
        if fp.get("locale"): ctx_opts["locale"]=fp["locale"]
        # no_viewport=True tells Playwright to track Chrome's actual window
        # rather than fixing it to a virtual size. Critical for headed mode.
        ctx_opts["no_viewport"] = True
        ctx=browser.new_context(**ctx_opts)
        # Phase 9.2: install stealth init script before any navigation
        if config.get("use_stealth",True):
            try:
                from ..constants import STEALTH_JS
                ctx.add_init_script(STEALTH_JS)
            except Exception as e:
                sys.stderr.write(f"  login: stealth install failed: {str(e)[:80]}\n")
        page=ctx.new_page()
        # v3.43.56: apply playwright-stealth library if configured
        try:
            from .. import stealth as _stealth
            _stealth.apply_to_page(page, config)
        except Exception as e:
            sys.stderr.write(f"  login: stealth library apply failed: {str(e)[:80]}\n")
        # Phase 5.1: install click/input recorder. We add this BEFORE any
        # navigation so add_init_script applies to the login page itself
        # and any navigation it triggers (post-login redirects). Running
        # for every login is fine — overhead is negligible (~1ms of JS
        # event listener) and lets us learn from successful auto-logins
        # too, not just manual takeovers.
        try:
            from ..learn import install_recorder
            install_recorder(page)
        except Exception as e:
            sys.stderr.write(f"  login: recorder install failed: {e}\n")
        try: page.goto(url,wait_until="domcontentloaded",timeout=25000)
        except PWTimeout:
            _hard_close(); return False,"Login page timed out loading",[]
        time.sleep(1.5)
        # Re-install on the loaded page (in case the init script didn't apply)
        try:
            from ..learn import install_recorder
            install_recorder(page)
        except Exception: pass

        # Row 371: a missing login form has several visually identical causes.
        # Clear declared per-site gates FIRST, then the conservative generic
        # consent/age/interstitial tiers, before selector probing can mislabel
        # the page as stale.  The helper verifies origin after every click and
        # returns structured outcomes; none of this is allowed to be silent.
        from ..interstitial import (
            dismiss_gates as _dismiss_page_gates,
            first_safety_unknown as _first_gate_unknown,
            safety_unknown_diagnostic as _gate_unknown_diagnostic,
        )

        def _report_gate_actions(actions):
            for action in actions:
                outcome = action.get("outcome", "unknown")
                tier = action.get("tier", "unknown")
                label = action.get("label", "")
                reason = action.get("reason", "")
                if outcome == "cleared":
                    sys.stderr.write(f"  login: {reason}\n")
                elif outcome == "refused":
                    sys.stderr.write(
                        f"  login: refused {tier} gate via {label!r} — "
                        f"{reason}\n")
                else:
                    sys.stderr.write(
                        f"  login: {outcome} for {tier} gate via "
                        f"{label!r} — {reason}\n")

        _pre_form_gate_actions = _dismiss_page_gates(
            page,
            config.get("dismiss_selectors", ""),
            destination_url=url,
        )
        _report_gate_actions(_pre_form_gate_actions)
        _pre_form_unknown = _first_gate_unknown(_pre_form_gate_actions)
        if _pre_form_unknown:
            _reason = _gate_unknown_diagnostic(_pre_form_unknown)
            _hard_close()
            return False, _reason, []

        _gate_blocker = next((
            action for action in _pre_form_gate_actions
            if action.get("outcome") in {"refused", "label_unknown"}
        ), None)

        def _with_gate_blocker(reason):
            if not _gate_blocker:
                return reason
            outcome = _gate_blocker.get("outcome")
            label = _gate_blocker.get("label", "")
            detail = _gate_blocker.get("reason", "")
            if outcome == "refused":
                return (f"Page gate refused {label!r}: {detail}. "
                        f"{reason}")
            return (f"Page gate safety UNKNOWN for {label!r}: {detail}. "
                    f"{reason}")

        # v3.66.302: cross-origin N-step login flow. If the operator captured a
        # multi-step (possibly cross-origin) login for this site, drive it
        # (type/click/await_url across origins) before the single-form selector
        # sweep below. No-op when no flow is saved (the common case) — the sweep
        # then runs unchanged, so this is zero-regression for every existing
        # single-form site. LIVE drive; verified on stash.
        try:
            _flow_res = replay_saved_login_flow(page, config)
            if _flow_res.get("ran"):
                sys.stderr.write(
                    f"  login: replayed saved login flow "
                    f"({_flow_res.get('steps', 0)} steps, "
                    f"ok={_flow_res.get('ok')})\n")
                time.sleep(wait)
        except Exception as _e:
            sys.stderr.write(
                f"  login: login-flow replay skipped: {str(_e)[:80]}\n")

        # v3.43.33: AI-assisted login form detection. When the site has
        # ai_login_assist_enabled=True AND aiassist is configured AND
        # there are no built-in selectors that obviously match (i.e.
        # the existing fallback path is likely to struggle), ask the
        # local LLM to identify the form's selectors directly.
        #
        # We PREPEND the AI proposals to the candidate lists rather than
        # replacing them — if the AI hallucinates a bad selector, the
        # fallbacks still run. Cost: 1 extra _try_fill attempt per field
        # if the AI is wrong. Benefit: when the AI is right (common on
        # new sites), login succeeds without 25 selector probes.
        #
        # The runner stashes proposal+validation in cfg so the caller
        # can save the selectors to learned_block on success.
        ai_assist_active = (config.get("ai_login_assist_enabled")
                             and not (learned_uf and learned_pf and learned_sb))
        if ai_assist_active:
            try:
                from .. import ai_login as _ail
                from .. import aiassist as _aia
                if _aia.get_config().get("enabled"):
                    # Capture a focused DOM excerpt — entire HTML can
                    # be massive on SPAs. The login form's containing
                    # block is the relevant region.
                    try:
                        dom_excerpt = page.evaluate(
                            """() => {
                                const forms = document.querySelectorAll('form');
                                if (forms.length === 1) return forms[0].outerHTML;
                                // No form or multiple forms — return
                                // body's first ~20KB
                                return document.body
                                    ? document.body.innerHTML.slice(0, 20000)
                                    : document.documentElement.outerHTML.slice(0, 20000);
                            }""")
                    except Exception:
                        dom_excerpt = ""
                    # Screenshot (optional). Vision models do much
                    # better with the image. Skip on failure — text-only
                    # fallback works.
                    screenshot_b64 = None
                    try:
                        import base64
                        png = page.screenshot(full_page=False, type="png")
                        screenshot_b64 = base64.b64encode(png).decode("ascii")
                    except Exception:
                        pass
                    proposal = _ail.detect_login_form(
                        dom_excerpt=dom_excerpt,
                        screenshot_b64=screenshot_b64,
                        page_url=url,
                    )
                    if proposal.get("ok"):
                        # Validate the proposed selectors against the
                        # live page BEFORE blindly trusting them. The
                        # AI sometimes proposes selectors that look
                        # plausible but match 0 nodes.
                        validation = _ail.validate_proposed_selectors(
                            page, proposal)
                        grade = _ail.grade_proposal(proposal, validation)
                        sys.stderr.write(
                            f"  login: AI proposal score={grade['score']}, "
                            f"use={grade['use_proposal']}, "
                            f"reasons={grade['reasons']}\n")
                        if grade.get("use_proposal"):
                            # Prepend to candidate lists. If they work,
                            # we save them to learned on success.
                            uf_candidates = ([proposal["username_selector"]]
                                              + uf_candidates)
                            pf_candidates = ([proposal["password_selector"]]
                                              + pf_candidates)
                            sb_candidates = ([proposal["submit_selector"]]
                                              + sb_candidates)
                            if not trigger_uf_candidates:
                                trigger_uf_candidates = [
                                    proposal["username_selector"]
                                ]
                            # Stash for the success path to save as learned
                            # (the wider _save_learned flow lives outside
                            # this scope; we leave a marker on the page
                            # for the caller to pick up).
                            try:
                                page._ai_login_proposal = {
                                    "username_selector": proposal["username_selector"],
                                    "password_selector": proposal["password_selector"],
                                    "submit_selector": proposal["submit_selector"],
                                    "score": grade["score"],
                                }
                            except Exception:
                                pass
                        elif proposal.get("captcha_detected") and allow_manual_takeover:
                            # Captcha — short-circuit to manual takeover
                            # rather than burning through selectors
                            return _hand_off(
                                "AI detected a captcha on the login page — "
                                "please log in manually.")
            except Exception as e:
                sys.stderr.write(f"  login: AI assist failed: {e} "
                                  f"(falling back to enumeration)\n")

        trigger_needed, trigger_fired, trigger_detail = (
            _fire_login_trigger_if_needed(
                page,
                config.get("login_trigger"),
                trigger_uf_candidates or uf_candidates,
            )
        )
        if trigger_needed:
            sys.stderr.write(
                f"  login: login-form trigger: {trigger_detail} "
                f"(fired={trigger_fired})\n"
            )

        # ── Try to fill username. If we can't even find the username field,
        # the form might be entirely custom — but the page IS loaded, so
        # the user can drive it manually if they want.
        ok,info=_try_fill(page,uf_candidates,username,"username")
        if not ok:
            if trigger_needed:
                info=("login form is hidden behind a trigger: "
                      f"{trigger_detail}; {info}")
            if allow_manual_takeover:
                if trigger_needed:
                    return _hand_off(info)
                return _hand_off(_with_gate_blocker(
                    f"Couldn't find username field: {info}"))
            _hard_close(); return False,info,[]
        sys.stderr.write(f"  login: filled username via [{info}]\n")

        # Phase 15.5: brief "thinking" pause between username and password
        # fields. Real users don't tab instantly — they read the next field's
        # label, position the cursor, etc. 300-900ms covers most patterns.
        import random as _rnd
        time.sleep(_rnd.uniform(0.3, 0.9))

        ok,info=_try_fill(page,pf_candidates,password,"password")
        if not ok:
            # Staged-login recovery: the password field may be on a second
            # screen reached only after a Continue/Next click (two-step login).
            # Try once to advance and re-fill before giving up to manual.
            try:
                s_ok,s_info=_staged_password_retry(
                    page,sb_candidates,pf_candidates,password)
            except Exception as _stg_e:
                s_ok,s_info=False,f"staged retry errored: {_stg_e}"
            if s_ok:
                ok,info=True,s_info
            else:
                if allow_manual_takeover:
                    return _hand_off(_with_gate_blocker(
                        f"Couldn't find password field: {info}"))
                _hard_close(); return False,info,[]
        sys.stderr.write(f"  login: filled password via [{info}]\n")

        # Some forms auto-submit on password fill (Enter, blur, JS listener).
        # If the URL already changed to the success URL, we're done — no
        # need to submit anything. Skipping this check would lead us into
        # _submit_login on a closed/navigating page, which then "fails" 8
        # times against a target that's already gone.
        # v3.66.1020: the declared post-login wall, read ONCE here because the
        # auto-submit branch below needs it too. @1016 read it only after
        # _submit_login, which is too late for a form that submits on fill.
        _wall=config.get("dismiss_selectors_login","") or ""
        # A form that auto-submits on fill can land on the WALL rather than
        # on success_url -- and a wall carries no login form, so a success
        # check made first does not fire and _submit_login below then flails
        # its whole selector list against a page that can never satisfy it.
        # Measured on pristine source: a stall, not a clean failure.
        #
        # Row 371 makes the semantic fallback always-on AND moves it in front
        # of the success comparison: reaching success_url is not evidence that
        # no gate is standing on the page, and a gate left standing here is
        # the page the operator is handed.
        from ..interstitial import dismiss_gates as _dismiss_interstitials
        _fill_gate_actions = _dismiss_interstitials(page, _wall)
        _report_gate_actions(_fill_gate_actions)
        _fill_unknown = _first_gate_unknown(_fill_gate_actions)
        if _fill_unknown:
            _reason = _gate_unknown_diagnostic(_fill_unknown)
            _hard_close()
            return False, _reason, []
        _fill_wall_cleared = any(action.get("outcome") == "cleared"
                                 for action in _fill_gate_actions)
        if _fill_wall_cleared:
            sys.stderr.write("  login: dismissed a post-login interstitial "
                             "reached by auto-submit-on-fill\n")
            try: page.wait_for_load_state("domcontentloaded",timeout=10000)
            except Exception: pass
        try:
            cur_after_fill=page.url
        except Exception:
            cur_after_fill=""
        if success and success in cur_after_fill:
            if _fill_wall_cleared:
                sys.stderr.write(f"  login: at success URL after dismissing the wall "
                                 f"({cur_after_fill[:80]})\n")
                cookies=pw_to_json(ctx.cookies()); _hard_close()
                return True,(f"OK — {len(cookies)} cookies "
                             f"(auto-submitted on fill; wall dismissed)"),cookies
            sys.stderr.write(f"  login: page already at success URL after fill ({cur_after_fill[:80]})\n")
            cookies=pw_to_json(ctx.cookies()); _hard_close()
            return True,f"OK — {len(cookies)} cookies (auto-submitted on fill)",cookies

        tok,waited=_wait_captcha_tokens(page,deadline=30)
        if tok:
            sys.stderr.write(f"  login: {tok} populated after {waited:.1f}s\n")

        # Phase 19: check "Remember me" / "Keep me signed in" if present.
        # Best-effort — silently no-ops if no such checkbox exists. Helps
        # extend session lifetimes so re-login storms happen less often.
        try: _try_check_remember_me(page)
        except Exception as e: sys.stderr.write(f"  login: remember-me check skipped: {e}\n")

        # Freeze the jar before submit can mutate it. An unreadable baseline
        # is UNKNOWN, not an empty jar that makes every later cookie new.
        try:
            cookies_before_submit = tuple(dict(c) for c in ctx.cookies())
        except Exception as e:
            cookies_before_submit = None
            sys.stderr.write(f"  login: pre-submit cookie snapshot failed: {e}\n")
        ok,method=_submit_login(page,sb_candidates,pf_candidates)
        # Page closed mid-submit (or before) — the form likely auto-submitted
        # on a previous step. Try to read cookies; if we got any usable session
        # cookies, login succeeded silently. Otherwise fall through to manual.
        if ok=="PAGE_CLOSED":
            sys.stderr.write("  login: page closed during submit, checking for cookies\n")
            try:
                cookies=pw_to_json(ctx.cookies())
            except Exception as e:
                cookies=[]
                sys.stderr.write(f"  login: cookie read after page-close failed: {e}\n")
            # A login page that closes mid-submit may have succeeded
            # silently — but only count it as a success if the cookie
            # jar actually looks like an authenticated session. One
            # stray cookie is not a login (OPEN_THREADS: loose
            # success test).
            authed,why=_looks_authenticated(
                cookies, before_cookies=cookies_before_submit)
            if authed:
                # v3.66 row 708: the jar no longer decides. A page that
                # closed mid-submit fired no navigation, so success needs a
                # positive member-state check on the page actually read.
                return _no_nav_verdict(page, config, cookies, why,
                                       "page closed mid-submit", _hard_close)
            # Cookies absent or unconvincing — login almost certainly
            # did not go through. Manual takeover is the right answer.
            sys.stderr.write(f"  login: page closed mid-submit but cookies "
                             f"unconvincing ({why})\n")
            if allow_manual_takeover:
                return _hand_off(f"Form vanished during submit, no "
                                 f"convincing session cookies: {method}")
            _hard_close(); return False,f"Submit failed: {method}",[]
        if not ok:
            # v3.65.3: before handing off, check cookies. Sites whose
            # submit is an AJAX call followed by client-side navigation
            # (e.g. wowgirls' div.loginform-submit-button → XHR →
            # window.location) may set a real session cookie without
            # ever firing a Playwright navigation event. PAGE_CLOSED's
            # branch already does this; the not-ok branch should too,
            # or every such login wastes an interactive takeover.
            try: cookies_after_submit=pw_to_json(ctx.cookies())
            except Exception as e:
                cookies_after_submit=[]
                sys.stderr.write(f"  login: cookie read after non-nav submit failed: {e}\n")
            authed,why=_looks_authenticated(
                cookies_after_submit, before_cookies=cookies_before_submit)
            if authed:
                # v3.66 row 708: the AJAX no-nav path is the seam the row
                # names. A convincing jar is a description of the jar, not
                # a navigation.
                return _no_nav_verdict(page, config, cookies_after_submit,
                                       why, "no nav signal", _hard_close)
            sys.stderr.write(f"  login: no nav signal and cookies "
                             f"unconvincing ({why})\n")
            if allow_manual_takeover:
                return _hand_off(f"Couldn't submit form: {method}")
            _hard_close(); return False,f"Submit failed: {method}",[]
        sys.stderr.write(f"  login: submitted via {method}\n")

        time.sleep(wait)
        # v3.66.1016 (item E): the post-login interstitial. A "No Thanks.
        # Continue to Members Area" wall sits between the login POST and the
        # members area on the Gamma brands and others like them. Nothing
        # dismissed it here, so `cur` below read the WALL's url, `success not
        # in cur` fired, and a login that had in fact succeeded was thrown into
        # manual takeover.
        #
        # Fired ONCE, here, rather than per content URL: the wall cannot recur
        # once past it, and re-trying it in _process_one costs a full 3s
        # timeout per selector line on every URL forever. Per-page gates
        # (cookie / age / consent) are a different scope and stay in
        # `dismiss_selectors`, which _process_one still runs per URL.
        #
        # The measured per-site block remains first. The generic pass follows
        # even when that block is blank, because unknown sites encounter the
        # same wall and its absence from config is not evidence that it is safe
        # to ignore.
        from ..interstitial import dismiss_gates as _dismiss_interstitials
        _post_gate_actions = _dismiss_interstitials(page, _wall)
        _report_gate_actions(_post_gate_actions)
        _post_unknown = _first_gate_unknown(_post_gate_actions)
        if _post_unknown:
            _reason = _gate_unknown_diagnostic(_post_unknown)
            _hard_close()
            return False, _reason, []
        _clicked = [action for action in _post_gate_actions
                    if action.get("outcome") == "cleared"]
        if _clicked:
            sys.stderr.write(f"  login: dismissed post-login interstitial "
                             f"({len(_clicked)} cleared action(s))\n")
            # A dismissal is usually a navigation. Without this the url
            # read below can still be the wall's, which would make the
            # dismissal look like it had not happened.
            try: page.wait_for_load_state("domcontentloaded",timeout=10000)
            except Exception: pass
        try: cur=page.url
        except Exception:
            # Page closed AFTER reported successful submit — same recovery
            # as above. This branch is rare but defensible.
            try: cookies=pw_to_json(ctx.cookies())
            except Exception: cookies=[]
            authed,why=_looks_authenticated(
                cookies, before_cookies=cookies_before_submit)
            if authed:
                # v3.66 row 708: same rule at the third cookie-only seam.
                # The URL is unreadable here, so the member-state check
                # returns UNKNOWN and this settles rather than succeeding.
                return _no_nav_verdict(page, config, cookies, why,
                                       "page closed post-submit", _hard_close)
            _hard_close()
            return False,(f"Page closed after submit; cookies "
                          f"unconvincing ({why})"),[]
        if success and success not in cur:
            if allow_manual_takeover:
                return _hand_off(f"Expected URL contains {success!r}, got {cur}")
            _hard_close(); return False,f"Expected URL contains {success!r}, got {cur}",[]
        cookies=pw_to_json(ctx.cookies()); _hard_close()
        return True,f"OK — {len(cookies)} cookies (submit: {method})",cookies
    except Exception as e:
        # Programming error or fatal exception — never offer manual takeover
        # because the browser state is unknown.
        _hard_close()
        return False,f"login error: {str(e)[:200]}",[]
