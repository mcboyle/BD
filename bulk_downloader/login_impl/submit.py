"""login_impl.submit -- verbatim cluster from login.py @v447 (DECOMP-LEAF cut 3)."""

import re
import inspect
import sys
import time
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from ..constants import STEALTH_JS
from ..log import login_site, site_tag
from ..cookies import pw_to_json
from ..interstitial import _origin
from ._common import (
    _css_escape_for_id,
    _fire_login_trigger_if_needed,
    _inter_field_pause,
    _try_click,
    _try_fill,
    log_url,
)
from .manual import _MANUAL_LOGIN_BANNER_JS
from .replay import (
    LOGIN_SETTLED_NO_NAV,
    LoginOutcome,
    _looks_authenticated,
    anonymous_surface_check,
    member_state_check,
    redact_url_credentials,
    replay_saved_login_flow,
    keep_pre_submit_screenshot,
    success_url_reached,
    write_login_evidence,
)


def _brand_host(url):
    """Lower-cased hostname of an http(s) URL, or "" when unmeasurable."""
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(url or "")
        if parsed.scheme.lower() not in ("http", "https"):
            return ""
        return (parsed.hostname or "").lower()
    except (TypeError, ValueError, AttributeError):
        return ""


def _same_brand_origin(login_url, landed_url):
    """Row 722 (G17): do the login page and the page the submit landed on
    share a registrable domain (eTLD+1)?  site-ma.brazzers.com ->
    www.brazzers.com and www.blacked.com -> members.blacked.com/oidc are
    the SAME brand: recorded and judged, never refused for the origin
    alone.  accounts.google.com is a FOREIGN domain: row 774's refusal
    stands.  Fails closed on anything unmeasurable."""
    from ..registrable_domain import registrable_domain
    a, b = _brand_host(login_url), _brand_host(landed_url)
    if not a or not b:
        return False
    da, db = registrable_domain(a), registrable_domain(b)
    return bool(da) and "." in da and da == db


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
        sys.stderr.write(f"  {site_tag()}login: {info}\n")
        return True, info, cookies
    info = (f"{LOGIN_SETTLED_NO_NAV} ({why}; no navigation; {member_why}"
            f"{'; evidence ' + evidence if evidence else ''}) \u2014 NOT success")
    sys.stderr.write(f"  {site_tag()}login: {info}\n")
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
        f"  {site_tag()}login: password field absent; clicked continue [{cont_info}] — "
        f"retrying password fill (staged login)\n")
    # Give the next screen a moment to render before re-attempting. Best-effort:
    # if no password field shows up, _try_fill below fails fast and we hand off.
    try:
        page.locator("input[type=password]").first.wait_for(
            state="visible", timeout=6000)
    except Exception:
        pass
    return _try_fill(page, pf_candidates, password, "password (after continue)")


TURNSTILE_IFRAME_SEL="iframe[src*='challenges.cloudflare.com']"


def _click_turnstile_checkbox(page):
    """Row 722 (vip4k.com): a Cloudflare Turnstile widget in CHECKBOX mode
    ("Verify you are human") never populates its token until the box is
    clicked.  Operator decision: click it -- it is a browser-fingerprint
    check, not a puzzle.  Only Turnstile is ever clicked; hCaptcha and
    reCAPTCHA can open image puzzles and are left alone.  Returns True when
    a click was delivered."""
    # blacked/vixen (15:2xZ): the widget iframe is cross-origin AND inside
    # a closed shadow root -- no DOM selector reaches it, but the browser's
    # frame tree still lists it. Go by frame URL first.
    try:
        for fr in page.frames:
            if "challenges.cloudflare.com" not in (fr.url or ""):
                continue
            try:
                box=fr.locator("input[type=checkbox]").first
                if box.count():
                    box.click(timeout=3000, force=True)
                    sys.stderr.write(f"  {site_tag()}login: turnstile checkbox clicked via frame tree\n")
                    return True
            except Exception:
                pass
            try:
                body=fr.locator("body").first
                bb=body.bounding_box() or {}
                h=bb.get("height") or 65
                body.click(timeout=3000, position={"x":30,"y":min(h/2, 32)}, force=True)
                sys.stderr.write(f"  {site_tag()}login: turnstile frame body clicked via frame tree\n")
                return True
            except Exception:
                continue
    except Exception:
        pass
    try:
        frame=page.frame_locator(TURNSTILE_IFRAME_SEL).first
        box=frame.locator("input[type=checkbox]").first
        if box.count():
            box.click(timeout=3000, force=True)
            return True
    except Exception:
        pass
    # Closed shadow root or no reachable checkbox: click the widget body
    # where the box sits (left edge, vertically centred).
    # vip4k live (10:3xZ): the iframe itself can sit inside a CLOSED shadow
    # root under the widget container, invisible to every selector -- so the
    # container (div.cf-turnstile / div.g-recaptcha[data-sitekey]) is the
    # last anchor a click can be delivered to.
    # login.vixen.com/.../login/challenge (13:2xZ): the host div has a
    # RANDOM id (<div id="lVJB5" style="display: grid">) -- no selector
    # names it. Derive it from the token field: the widget is the sibling
    # box beside input[name=cf-turnstile-response] that is rendered at
    # widget size. Tag it so the positional click below can reach it.
    try:
        _how=page.evaluate(_TAG_TURNSTILE_HOST_JS)
        sys.stderr.write(f"  {site_tag()}login: turnstile host search: {_how}\n")
    except Exception as e:
        sys.stderr.write(f"  {site_tag()}login: turnstile host search failed: {e}\n")
    for sel in (TURNSTILE_IFRAME_SEL, *TURNSTILE_CONTAINER_SELS,
                "[data-bd-turnstile-host]"):
        try:
            widget=page.locator(sel).first
            if not widget.count():
                continue
            bb=widget.bounding_box() or {}
            h=bb.get("height") or 65
            widget.click(timeout=3000, position={"x":30,"y":h/2})
            return True
        except Exception:
            continue
    return False


_TAG_TURNSTILE_HOST_JS = """() => {
  const tok = document.querySelector('input[name="cf-turnstile-response"]');
  if (!tok) return 'no-token-field';
  const rect = el => el.getBoundingClientRect();
  const sized = el => { const r = rect(el); return r.width >= 200 && r.height >= 50 && r.height <= 120; };
  const tag = (el, how) => { el.setAttribute('data-bd-turnstile-host', how);
    const r = rect(el); return `${how} ${el.tagName.toLowerCase()}#${el.id||'-'} ${Math.round(r.width)}x${Math.round(r.height)}`; };
  // login.vixen.com (14:3xZ): the token input sits INSIDE the host
  // (div#<random> > div > div > input) -- walk up first.
  let el = tok.parentElement;
  for (let up = 0; el && up < 6; up++, el = el.parentElement) {
    if (sized(el)) return tag(el, 'ancestor');
  }
  // Otherwise the widget-sized box beside the token field.
  const scopes = [tok.parentElement, tok.parentElement && tok.parentElement.parentElement].filter(Boolean);
  for (const scope of scopes) {
    for (const cand of scope.querySelectorAll('div, span')) {
      if (cand.contains(tok)) continue;
      if (sized(cand) && cand.querySelectorAll('input, button, a').length === 0) return tag(cand, 'sibling');
    }
  }
  // Last resort (15:1xZ, vixen attempt 4 found nothing sized): the
  // grandparent of the token field, whatever its box says.
  const gp = tok.parentElement && tok.parentElement.parentElement;
  if (gp) return tag(gp, 'grandparent');
  return 'no-host';
}"""
TURNSTILE_CONTAINER_SELS=(".cf-turnstile","div.g-recaptcha[data-sitekey]",
                          "[data-sitekey][class*=turnstile]",
                          # login.vixen.com/.../login/challenge: the managed
                          # challenge renders an explicit widget <div id=cf-chl-widget-xxxx>
                          "div[id^=cf-chl-widget-]","#challenge-stage")
HUMAN_BUTTON_RE=re.compile(r"^\s*(?:i am human|i'm human|verify(?: you are human)?)\s*$", re.I)


def _click_human_button(page):
    """A visible button/role=button whose whole text is an 'I am human' /
    'Verify' affordance on a challenge page. Returns True when clicked."""
    try:
        btns=page.locator("button, [role=button], input[type=submit]")
        for i in range(min(btns.count(), 30)):
            b=btns.nth(i)
            try:
                if not b.is_visible():
                    continue
                label=(b.inner_text() if b.evaluate("e => e.tagName") != "INPUT"
                       else (b.get_attribute("value") or ""))
                aria=b.get_attribute("aria-label") or ""
            except Exception:
                continue
            # stepsiblingscaught live: <div role=button aria-label="Verify you
            # are human"> whose text is "✓ I am human" -- read the aria-label
            # too and drop the decoration glyphs before matching.
            for cand in (label, aria):
                cand=re.sub(r"[^A-Za-z' ]+", " ", cand or "").strip()
                if cand and HUMAN_BUTTON_RE.match(cand):
                    b.click(timeout=3000)
                    return True
    except Exception:
        pass
    return False


CF_CHALLENGE_SELS=("#challenge-running","#challenge-form",".cf-turnstile",
                   TURNSTILE_IFRAME_SEL)
SITE_CHALLENGE_SELS=("[id*=turnstile i][role=button]","[class*=turnstile-checkbox]",
                     "[role=button][aria-label='Verify you are human' i]")
CF_CHALLENGE_TITLE="just a moment"


def _is_cloudflare_challenge_page(page):
    """True when the page is a Cloudflare managed-challenge interstitial
    ("Just a moment..." / cf-chl) rather than a login form: a challenge
    marker is present AND no password field is visible.  An unreadable
    title (page gone) is never a challenge."""
    try:
        title=(page.title() or "").lower()
    except Exception:
        return False
    marked=CF_CHALLENGE_TITLE in title
    if not marked:
        for sel in CF_CHALLENGE_SELS:
            try:
                if page.locator(sel).count():
                    marked=True; break
            except Exception:
                continue
    if not marked:
        # stepsiblingscaught (14:5xZ): a SITE-DRAWN challenge -- title
        # "Security Check", path /turnstile/challenge, a
        # #turnstileCheckboxWrapper[role=button aria-label="Verify you are
        # human"] -- carries none of Cloudflare's own markers.
        try:
            cur=(page.url or "").lower()
        except Exception:
            cur=""
        if ("security check" in title or "/turnstile/challenge" in cur):
            marked=True
        else:
            for sel in SITE_CHALLENGE_SELS:
                try:
                    if page.locator(sel).count():
                        marked=True; break
                except Exception:
                    continue
    if not marked:
        return False
    try:
        if page.locator("input[type=password]:visible").count():
            return False
    except Exception:
        pass
    return True


def clear_cloudflare_challenge(page, wait=15.0, max_rounds=2):
    """Row 722 (adulttime): every login URL answers 307->403 with a
    Cloudflare managed challenge page ("Just a moment...", Turnstile
    CHECKBOX "Verify you are human" in a challenges.cloudflare.com iframe).
    The walker never saw a form and gave up with "Couldn't find username
    field".  Operator decision: the Turnstile checkbox may be clicked
    (never a puzzle).  Reuses the G11 click helper, then waits up to
    ``wait`` seconds for navigation away from the challenge or a visible
    password field.  At most ``max_rounds`` clicks.  Returns True when the
    challenge cleared, False when there was no challenge or it did not
    clear.  hCaptcha / reCAPTCHA pages are never touched."""
    if not _is_cloudflare_challenge_page(page):
        return False
    for _round in range(max_rounds):
        try:
            # A container is only trusted as Turnstile when no OTHER captcha
            # iframe (hCaptcha / reCAPTCHA: puzzle risk) is on the page.
            _foreign=page.locator(
                "iframe[src*='hcaptcha'], iframe[src*='recaptcha']").count()>0
            try:
                page.evaluate(_TAG_TURNSTILE_HOST_JS)
            except Exception:
                pass
            _frame_widget=any("challenges.cloudflare.com" in (fr.url or "")
                              for fr in page.frames)
            _has_widget=(_frame_widget or page.locator(TURNSTILE_IFRAME_SEL).count()>0 or (
                not _foreign and any(page.locator(sel).count()>0
                                     for sel in (*TURNSTILE_CONTAINER_SELS,
                                                 "[data-bd-turnstile-host]"))))
        except Exception:
            return False
        if not _has_widget:
            # stepsiblingscaught live (11:3xZ): /turnstile/challenge carries
            # only a site-drawn "I am human" button. That is the same
            # fingerprint check, not a puzzle: press it once.
            if _click_human_button(page):
                sys.stderr.write(f"  {site_tag()}login: cloudflare challenge page — clicked "
                                 "'I am human' button\n")
            else:
                # blacked live (13:1xZ): the widget is already in its
                # "Verifying..." / "Verification successful. Waiting for
                # <host> to respond" state -- nothing to click, but the page
                # WILL navigate on its own. Wait for that before giving up.
                sys.stderr.write(f"  {site_tag()}login: cloudflare challenge page — no "
                                 "Turnstile checkbox to click; waiting for "
                                 "auto-verification\n")
                # blacked (14:3xZ): Cloudflare can DOWNGRADE from auto-verify
                # to an interactive checkbox mid-wait -- re-look every poll,
                # and give the round trip twice the budget.
                end=time.time()+wait*2
                _clicked_late=False
                while time.time()<end:
                    if not _is_cloudflare_challenge_page(page):
                        try: cur=page.url
                        except Exception: cur="?"
                        sys.stderr.write(f"  {site_tag()}login: challenge cleared -> {cur}\n")
                        return True
                    if not _clicked_late:
                        try:
                            page.evaluate(_TAG_TURNSTILE_HOST_JS)
                            _late=(any("challenges.cloudflare.com" in (fr.url or "")
                                       for fr in page.frames)
                                   or page.locator(TURNSTILE_IFRAME_SEL).count()>0 or (
                                not _foreign and any(
                                    page.locator(sel).count()>0
                                    for sel in (*TURNSTILE_CONTAINER_SELS,
                                                "[data-bd-turnstile-host]"))))
                        except Exception:
                            _late=False
                        if _late and _click_turnstile_checkbox(page):
                            _clicked_late=True
                            sys.stderr.write(f"  {site_tag()}login: cloudflare challenge page — "
                                             "checkbox appeared during the wait; "
                                             "clicked Turnstile checkbox\n")
                    time.sleep(0.5)
                sys.stderr.write(f"  {site_tag()}login: challenge NOT cleared within {int(wait*2)}s "
                                 "(no checkbox, no auto-verification)\n")
                return False
        elif not _click_turnstile_checkbox(page):
            sys.stderr.write(f"  {site_tag()}login: cloudflare challenge page — Turnstile "
                             "checkbox click failed\n")
            return False
        else:
            sys.stderr.write(f"  {site_tag()}login: cloudflare challenge page — clicked "
                             "Turnstile checkbox\n")
        end=time.time()+wait
        while time.time()<end:
            if not _is_cloudflare_challenge_page(page):
                try: cur=page.url
                except Exception: cur="?"
                sys.stderr.write(f"  {site_tag()}login: challenge cleared -> {cur}\n")
                return True
            time.sleep(0.5)
    sys.stderr.write(f"  {site_tag()}login: challenge NOT cleared within {int(wait)}s "
                     "(still 'Just a moment')\n")
    return False


def _wait_captcha_tokens(page,deadline=30,turnstile_click_after=4.0):
    """Detect and wait for any of the three major invisible captchas to
    populate their hidden token field.  Returns (token_name, seconds_waited)
    when the token populated, (token_name, None) when a field was present
    but NEVER populated within ``deadline``, or (None, 0) if no captcha is
    on the page.  For cf-turnstile-response only, an empty token after
    ``turnstile_click_after`` seconds gets the Turnstile checkbox clicked
    (checkbox-mode widgets never populate on their own)."""
    for tok in ("cf-turnstile-response","h-captcha-response","g-recaptcha-response"):
        sel=f"input[name='{tok}']"
        try:
            if page.locator(sel).count()==0: continue
        except Exception: continue
        start=time.time(); end=start+deadline; clicked=False
        while time.time()<end:
            try:
                v=page.locator(sel).first.input_value()
                if v: return tok,time.time()-start
            except Exception: pass
            if (tok=="cf-turnstile-response" and not clicked
                    and time.time()-start>=turnstile_click_after):
                clicked=True
                if _click_turnstile_checkbox(page):
                    sys.stderr.write(f"  {site_tag()}login: clicked Turnstile checkbox\n")
            time.sleep(0.5)
        return tok,None
    return None,0


_CHALLENGE_LANDING_MARKERS = (
    "checking your browser",
    "verify you are human",
    "just a moment...",
)


def _settled_non_success(page, config, status, why, hard_close):
    """Keep the landing that made a post-submit verdict non-successful."""
    try:
        final_url = page.url
    except Exception:
        final_url = ""
    evidence = write_login_evidence(page, config, final_url, f"login-{status}")
    hard_close()
    outcome = LoginOutcome(status, False, evidence, why)
    return outcome, f"{status}: {why} — NOT success", []


_TURNSTILE_CHECKBOX = ".cf-turnstile input[type='checkbox'], .cf-turnstile [role='checkbox']"


def _try_turnstile_one_click(page, config):
    """Perform one operator-enabled local Turnstile checkbox click.

    This deliberately has no solver, token, or cross-origin-frame path. It is
    available only when the operator opted in and no paid solver key is set.
    """
    if (not config.get("turnstile_one_click")
            or (config.get("captcha_api_key") or "").strip()):
        return False
    try:
        page_origin = urlsplit(page.url)
        if not page_origin.scheme or not page_origin.netloc:
            return False
    except Exception:
        return False
    contexts = [page]
    for frame in getattr(page, "frames", ()):
        try:
            frame_origin = urlsplit(frame.url)
            if (frame_origin.scheme, frame_origin.netloc) == (page_origin.scheme, page_origin.netloc):
                contexts.append(frame)
        except Exception:
            continue
    for context in contexts:
        try:
            checkbox = context.locator(_TURNSTILE_CHECKBOX).first
            if checkbox.count() and checkbox.is_visible(timeout=500):
                checkbox.click(timeout=1000)
                return True
        except Exception:
            continue
    return False


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


def _form_submit_is_safe(method, has_password):
    """Whether a JS form fallback may submit this form.

    Browsers default an omitted form method to GET.  A JavaScript fallback
    must never submit a password-bearing form unless it is an explicit POST;
    otherwise it serializes the credential into the URL and browser history.
    """
    if not has_password:
        return True
    return isinstance(method, str) and method.lower() == "post"


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


# Row 722 (kink.com): the login page carries a hidden duplicate form ahead of
# the visible one, so "the first password field's form" is the wrong form.
# Every JS-scoped submit method resolves the form through THIS function: the
# password field a human can see (a rendered box), preferring the one that
# already holds the typed value; the first match only when none is visible;
# the first form only when no candidate matches at all.
_LOGIN_FORM_JS = """(sels) => {
    const seen = (el) => el.getClientRects().length > 0;
    let first = null, visible = null, filled = null;
    for (const sel of sels) {
        let matches = [];
        try { matches = document.querySelectorAll(sel); } catch (e) { continue; }
        for (const pf of matches) {
            const f = pf.closest('form');
            if (!f) continue;
            if (!first) first = f;
            if (seen(pf)) {
                if (!visible) visible = f;
                if (pf.value && !filled) filled = f;
            }
        }
        if (filled) break;
    }
    return filled || visible || first || document.querySelector('form');
}"""


_SWEEP_DECLARED_ORIGINS=set()


# Row 722s (hustlerunlimited, 2026-09-15 14:1xZ): the form carried no
# method=POST, so the JS fallbacks submitted it as a GET and the credentials
# went into the URL (history, server logs, our own diagnostics). A form that
# holds a password field is never submitted by GET.
GET_FORM_REFUSED="form method is GET -- refused: submitting would put the credentials in the URL"


def _submit_login(page,sb_candidates,pf_candidates,declared_origins=None):
    """Try nine independent ways to submit the login form. Each method
    is attempted with a short timeout; we declare success the moment the
    page navigates WITHIN THE LOGIN PAGE'S ORIGIN. Returns (ok, method_used).

    Success predicate (v3.66 row 774): a URL change counts as a submit only
    when scheme+host+port of page.url equal those of the URL the sweep
    started on (bulk_downloader.interstitial._origin -- the same predicate
    dismiss_gates verifies every gate click with; default ports normalised,
    host case-folded). A bare `page.url != initial_url` is never success:
    bang.com's button:has-text("LOGIN") matches LOGIN WITH GOOGLE and lands
    on accounts.google.com, which the sweep used to report as a login. The
    first candidate that leaves the origin ends the sweep with
    (False, "cross-origin navigation refused ...") -- the remaining methods
    are never fired on the foreign page. Row 722 (G17): a hop to another
    host of the SAME registrable domain (login.example.com ->
    www.example.com; site-ma.brazzers.com -> www.brazzers.com) is a
    same-brand landing, counted as the submit and left for do_login to
    record and judge. Any other cross-origin destination is accepted only
    where the site DECLARES it: a captured multi-step flow
    (replay_saved_login_flow, v3.66.302) runs before this sweep and
    do_login honours it, or an absolute success_url naming the destination
    origin.

    Special return value: ('PAGE_CLOSED', reason). Raised when the page
    or browser context is detected as closed mid-attempt, which usually
    means the form auto-submitted on a previous fill and the browser
    is mid-navigation. Caller should NOT treat this as a hard failure
    until cookies have been checked — login may already have succeeded."""
    # Heartbeat at entry — without this, a long selector walk looks
    # identical to a silent hang, which was the visible symptom in v3.15.5.
    sys.stderr.write(f"  {site_tag()}login submit: attempting "
                     f"({len(sb_candidates)} button selector(s), 9 methods)\n")
    try: initial_url=page.url
    except Exception as e:
        # Page already closed before we even started — treat like submit
        # never happened so caller can fall back to cookie inspection.
        return "PAGE_CLOSED", f"page already closed: {str(e)[:60]}"
    initial_origin=_origin(initial_url)
    def _moved():
        # None: still on initial_url. True: navigated within the login
        # page's origin. str: the foreign origin the page is now on (or a
        # note that it cannot be measured) -- never a success.
        try: cur=page.url
        except Exception: return None
        if cur==initial_url: return None
        origin=_origin(cur)
        if origin is not None and origin==initial_origin: return True
        if origin is not None and _same_brand_origin(initial_url, cur):
            # Row 722 (G17): same brand, other host -- a submit, not a
            # refusal; do_login records the landing and judges the page.
            sys.stderr.write(f"  {site_tag()}login submit: page moved {initial_origin} -> "
                             f"{origin} (same brand), counted as submit\n")
            return True
        _declared=set(declared_origins if declared_origins is not None
                      else (_SWEEP_DECLARED_ORIGINS or ()))
        if origin is not None and origin in _declared:
            # stepsiblingscaught live (16:0xZ): the login host and the
            # members host are DIFFERENT brands (stepsiblingscaught.com ->
            # members.nubiles-porn.com); the operator declared the
            # destination as success_url, which row 774 already admits
            # after the sweep -- admit it inside the sweep too.
            sys.stderr.write(f"  {site_tag()}login submit: page moved {initial_origin} -> "
                             f"{origin} (declared success_url origin), counted as submit\n")
            return True
        return origin or f"unmeasurable origin {cur[:60]!r}"
    def _settle(moved,label):
        # A URL change settles the sweep either way: same-origin is the
        # success the caller expects; any other origin is refused outright
        # rather than retried, so no further method fires on a page that is
        # not the login form.
        if moved is True: return True,label
        sys.stderr.write(f"  {site_tag()}login submit: page left {initial_origin} for "
                         f"{moved} — cross-origin navigation refused, not a "
                         f"submit ({label})\n")
        return False,f"cross-origin navigation refused ({moved}) at {label}"
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
            result = page.evaluate("""(pf_sels) => {
                const f = ("""+_LOGIN_FORM_JS+""")(pf_sels);
                if (!f) return {submitted: false, reason: 'no form'};
                const hasPassword = Boolean(f.querySelector("input[type='password']"));
                const method = f.getAttribute('method') || 'get';
                if (hasPassword && method.toLowerCase() !== 'post') {
                    return {submitted: false, method, hasPassword,
                            reason: 'refused password form without POST'};
                }
                if (typeof f.requestSubmit === 'function') {
                    f.requestSubmit();
                    return {submitted: true, method, hasPassword};
                }
                return {submitted: false, method, hasPassword,
                        reason: 'requestSubmit unavailable'};
            }""", pf_candidates)
            if not isinstance(result, dict):
                return False, "requestSubmit produced no form result"
            if not _form_submit_is_safe(result.get("method"), result.get("hasPassword")):
                return False, GET_FORM_REFUSED
            if result.get("submitted"):
                return True, "form.requestSubmit()"
            return False, result.get("reason", "requestSubmit unavailable")
        except Exception as e: return False,f"requestSubmit error: {str(e)[:60]}"
    methods.append(("JS requestSubmit",m2))

    # Method 3: JS form.submit() — bypasses validation but always works
    # if there's a form; some sites' submit-button onclick is literally
    # `document.forms[0].submit()` so we just call it directly
    # v3.65.2: same scoping fix as Method 2.
    def m3():
        try:
            result = page.evaluate("""(pf_sels) => {
                const f = ("""+_LOGIN_FORM_JS+""")(pf_sels);
                if (!f) return {submitted: false, reason: 'no form'};
                const hasPassword = Boolean(f.querySelector("input[type='password']"));
                const method = f.getAttribute('method') || 'get';
                if (hasPassword && method.toLowerCase() !== 'post') {
                    return {submitted: false, method, hasPassword,
                            reason: 'refused password form without POST'};
                }
                f.submit();
                return {submitted: true, method, hasPassword};
            }""", pf_candidates)
            if not isinstance(result, dict):
                return False, "form.submit produced no form result"
            if not _form_submit_is_safe(result.get("method"), result.get("hasPassword")):
                return False, GET_FORM_REFUSED
            if result.get("submitted"):
                return True, "form.submit()"
            return False, result.get("reason", "form.submit unavailable")
        except Exception as e: return False,f"form.submit error: {str(e)[:60]}"
    methods.append(("JS form.submit",m3))

    # Method 4: Press Enter inside the password field
    def m4():
        for sel in pf_candidates:
            try:
                # Row 722: the visible password field, never a hidden twin.
                page.locator(f"{sel} >> visible=true").first.press("Enter")
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
                const f = ("""+_LOGIN_FORM_JS+""")(pf_sels);
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
                        // Row 722: prefer a rendered match over a hidden twin.
                        const all = document.querySelectorAll(sel);
                        let el = null;
                        for (const c of all) { if (c.getClientRects().length > 0) { el = c; break; } }
                        if (!el) el = all[0] || null;
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
            sys.stderr.write(f"  {site_tag()}login submit: page already closed — "
                             "stopping method loop\n")
            return "PAGE_CLOSED", f"page closed before {label}"
        moved=_moved()
        if moved is not None:
            if moved is True:
                sys.stderr.write(f"  {site_tag()}login submit: already navigated before "
                                 f"{label} — earlier method submitted\n")
            return _settle(moved, f"navigated before {label}")
        try: ok,info=fn()
        except Exception as e: ok,info=False,str(e)[:60]
        if not ok:
            sys.stderr.write(f"  {site_tag()}login submit: {label} → skip ({info})\n")
            if _page_closed_err(info):
                sys.stderr.write(f"  {site_tag()}login submit: page closed — bailing early; checking cookies\n")
                return "PAGE_CLOSED", f"page closed during {label}: {info}"
            if info == GET_FORM_REFUSED and label == "JS form.submit":
                # Row 722s (hustlerunlimited): the form is GET. The site's own
                # button (m1) already had its chance -- its JS may intercept --
                # and both JS fallbacks (m2, m3) refused; every method left
                # (Enter, Tab+Enter, click sweeps) would navigate natively and
                # put the password in the URL. The refusal is terminal.
                if _closed():
                    return "PAGE_CLOSED", f"page closed after {label}: {info}"
                sys.stderr.write(f"  {site_tag()}login submit: GET form -- stopping the "
                                 "method sweep (a synthetic submit would leak "
                                 "the credentials into the URL)\n")
                return False, GET_FORM_REFUSED
            continue
        sys.stderr.write(f"  {site_tag()}login submit: {label} → {info}; waiting...\n")
        # Give the page up to 8 seconds to navigate. We poll URL changes
        # because some forms don't trigger a load event (SPA logins).
        end=time.time()+8
        while time.time()<end:
            moved=_moved()
            if moved is not None: return _settle(moved,label)
            try: page.wait_for_load_state("networkidle",timeout=500)
            except Exception as e:
                if _page_closed_err(e):
                    return "PAGE_CLOSED", f"page closed waiting for {label}"
            moved=_moved()
            if moved is not None: return _settle(moved,label)
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
                sys.stderr.write(f"  {site_tag()}login: 'Remember me' already checked\n")
                return True
            loc.click(timeout=1000)
            sys.stderr.write(f"  {site_tag()}login: clicked 'Remember me' via [{sel[:60]}]\n")
            return True
        except Exception:
            continue
    return False



def _page_is_gone(page, exc) -> bool:
    """True only when the page itself is gone, so a body probe cannot succeed.

    Everything else -- "execution context was destroyed" during a navigation,
    a timeout, a detached frame -- is transient: the body is readable again
    once the page settles, and the caller must re-read rather than give up.
    """
    try:
        if page.is_closed():
            return True
    except Exception:
        pass
    text = str(exc).lower()
    return "has been closed" in text or "target closed" in text


# Row 722 (operator, 2026-09-15): pre-checked upsell / cross-sale checkboxes
# ("Yes! add <site> for $1", "Get a bonus site", "special offer", "trial",
# "newsletter") must be unchecked and verified before the form goes out --
# submitting with them checked is a purchase risk. "Remember me" (Phase 19,
# _try_check_remember_me above) must NOT be touched here.
_UPSELL_RE = re.compile(
    r"\$"
    r"|\d+\s*(?:usd|dollars?)\b"
    r"|\bcross[\s.-]?sales?\b"
    r"|\bupsell(?:s|ing)?\b"
    r"|\badds?\b"
    r"|\bbonus(?:es)?\b"
    r"|\bextras?\b"
    r"|\boffers?\b"
    r"|\btrials?\b"
    r"|\bspecials?\b"
    r"|\bnewsletters?\b"
    r"|\bsubscri(?:be[sd]?|ption)\b"
    r"|\bpromotions?\b"
    r"|\balso\s+join\b"
    r"|\baccess\s+to\b",
    re.I,
)
_REMEMBER_RE = re.compile(
    r"\bremember\b|\bkeep\s+me\b|\bstay\s+signed\b|\bsigned\s+in\b"
    r"|\blogged\s+in\b",
    re.I,
)

_COLLECT_CHECKED_VISIBLE_BOXES_JS = """
() => {
  const isVisible = (el) => {
    if (!el || el.hidden) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden'
        || style.opacity === '0') return false;
    if (el.offsetParent === null && style.position !== 'fixed') return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    return true;
  };
  const getLabel = (input) => {
    let text = '';
    if (input.id) {
      const lab = document.querySelector(
        `label[for="${CSS.escape(input.id)}"]`);
      if (lab) text = lab.innerText || lab.textContent || '';
    }
    if (!text.trim()) {
      const wrap = input.closest('label');
      if (wrap) text = wrap.innerText || wrap.textContent || '';
    }
    if (!text.trim()) {
      const aria = input.getAttribute('aria-label');
      if (aria) text = aria;
    }
    if (!text.trim()) {
      // Nearest text within the same container -- but a container can
      // hold OTHER checkboxes' own <label> text too (e.g. a bare
      // container is shared, as when several boxes sit directly in one
      // <form>). Strip nested label/input/select/textarea/button
      // elements from a clone first so a neighbor's label is never
      // misread as this box's own.
      const container = input.closest('div,li,td,p,span,section')
        || input.parentElement;
      if (container) {
        const clone = container.cloneNode(true);
        clone.querySelectorAll('label,input,select,textarea,button')
          .forEach((el) => el.remove());
        text = clone.innerText || clone.textContent || '';
      }
    }
    return (text || '').trim().replace(/\\s+/g, ' ');
  };
  const out = [];
  document.querySelectorAll('input[type=checkbox]').forEach((el, i) => {
    if (!el.checked) return;
    if (!isVisible(el)) return;
    el.setAttribute('data-bd-upsell-idx', String(i));
    out.push({idx: i, label: getLabel(el)});
  });
  return out;
}
"""


def _checked_upsell_boxes(page):
    """Operator (2026-09-15 13:1xZ): "no box is checked by default -- double
    verify". Re-read the page AFTER the uncheck pass: the labels of every
    VISIBLE checkbox that is still checked and reads as an upsell. Empty
    list = verified. Unreadable page = [] with a diagnostic (never a
    silent pass into a purchase: the caller logs the count either way)."""
    try:
        boxes = page.evaluate(_COLLECT_CHECKED_VISIBLE_BOXES_JS)
    except Exception as e:
        sys.stderr.write(f"  {site_tag()}login: upsell verification scan failed: {e}\n")
        return []
    still = []
    for box in boxes or []:
        label = (box.get("label") or "").strip()
        if not label or _REMEMBER_RE.search(label) or not _UPSELL_RE.search(label):
            continue
        still.append(label)
    return still


def _uncheck_upsell_boxes(page):
    """Uncheck every VISIBLE, checked upsell/cross-sale checkbox before the
    form is submitted. "Remember me" boxes are left alone (excluded via
    ``_REMEMBER_RE`` before the upsell match is even tried). A checked box
    whose label cannot be read is left alone too -- never click blind --
    and logged as UNKNOWN. Returns the list of labels acted on (an uncheck
    was attempted, whether or not it verified)."""
    try:
        boxes = page.evaluate(_COLLECT_CHECKED_VISIBLE_BOXES_JS)
    except Exception as e:
        sys.stderr.write(f"  {site_tag()}login: upsell checkbox scan failed: {e}\n")
        return []
    acted = []
    for box in boxes or []:
        idx = box.get("idx")
        label = (box.get("label") or "").strip()
        if not label:
            sys.stderr.write(f"  {site_tag()}login: checked box with no readable label "
                             "(UNKNOWN, left alone)\n")
            continue
        if _REMEMBER_RE.search(label):
            continue
        if not _UPSELL_RE.search(label):
            continue
        loc = page.locator(f"[data-bd-upsell-idx='{idx}']")
        try:
            loc.uncheck(timeout=1000)
        except Exception:
            try:
                loc.click(timeout=1000)
            except Exception:
                pass
        try:
            still_checked = loc.is_checked()
        except Exception:
            still_checked = True
        acted.append(label)
        if still_checked:
            sys.stderr.write(
                f"  {site_tag()}login: upsell box '{label[:50]}' could NOT be "
                f"unchecked (still checked)\n")
        else:
            sys.stderr.write(
                f"  {site_tag()}login: unchecked upsell box '{label[:50]}' (verified)\n")
    return acted


def _scoped_to_the_site_being_logged_into(fn):
    """Give `do_login` its `site_id` parameter and the log scope that uses it.

    Row 769: every line the login writes goes to stderr, site lanes interleave
    into one stream, and a line that does not name its site cannot be
    attributed to one.  The id is taken from the caller, which already holds
    it beside the config (the site runner's `self.site_id`, the keeper path's
    `site_id`), and falls back to a config that carries its own id; neither
    present leaves the line untagged rather than inventing an id.

    A DECORATOR and not an edit inside the function, because the body is ~600
    lines with some twenty early returns: wrapping it in a `with` block would
    re-indent every one of them, and setting the scope without one would leak
    a finished lane's id onto whatever the thread logs next.  The wrapper's
    own signature is the one callers see -- deliberately not `functools.wraps`,
    which would leave `inspect.signature` reporting the inner function and
    denying the `site_id` parameter it just added.
    """
    def _scoped_do_login(config, allow_manual_takeover=False, site_id=""):
        sid = site_id or config.get("site_id") or config.get("sid") or ""
        with login_site(sid):
            return fn(config, allow_manual_takeover)
    # Named apart from the function it wraps on purpose: gates in this repo
    # read `do_login` out of this file's AST by name, and a second definition
    # spelled the same way would give them a four-line body to judge.
    _scoped_do_login.__name__ = fn.__name__
    _scoped_do_login.__qualname__ = fn.__qualname__
    _scoped_do_login.__doc__ = fn.__doc__
    _scoped_do_login.__module__ = fn.__module__
    # Row 722s composes with this wrapper: its gates read the login body through
    # `inspect.getsource(do_login)`, which follows `__wrapped__` to the inner
    # function.  `inspect.signature` stops at an explicit `__signature__`, so
    # the `site_id` parameter this decorator adds stays the one callers see.
    _scoped_do_login.__signature__ = inspect.signature(_scoped_do_login)
    _scoped_do_login.__wrapped__ = fn
    return _scoped_do_login


@_scoped_to_the_site_being_logged_into
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
            f"  {site_tag()}login: SKIPPED — site {config.get('name','?')!r}: credential "
            f"vault is LOCKED; the stored password cannot be decrypted. "
            f"Unlock it in Settings -> Secrets after every service restart.\n")
        return False, "Credential vault locked: password", []
    if password_state == "missing":
        sys.stderr.write(
            f"  {site_tag()}login: SKIPPED — site {config.get('name','?')!r}: stored "
            f"credential is MISSING for the password reference. Repair it in "
            f"Settings -> Secrets.\n")
        return False, "Stored credential missing: password", []
    if password_state in ("unavailable", "unknown"):
        sys.stderr.write(
            f"  {site_tag()}login: SKIPPED — site {config.get('name','?')!r}: credential "
            f"availability is UNKNOWN; the stored password could not be read. "
            f"Check Settings -> Secrets and the service logs.\n")
        return False, "Credential state unknown: password", []
    if not url or not username or not password:
        missing = []
        if not url: missing.append("login_url")
        if not username: missing.append("username")
        if not password: missing.append("password")
        sys.stderr.write(
            f"  {site_tag()}login: SKIPPED — site {config.get('name','?')!r} is missing "
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
        sys.stderr.write(f"  {site_tag()}login: replaying learned selectors "
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
        sys.stderr.write(f"  {site_tag()}login: handing off for manual takeover — {reason}\n")
        # Row 722: keep the page as it stood when the automation gave up
        # (HTML + screenshot) so the reason is diagnosable after the fact.
        try: _cur = page.url
        except Exception: _cur = ""
        _ev = write_login_evidence(page, config, _cur, f"manual takeover {reason[:40]}")
        if _ev: sys.stderr.write(f"  {site_tag()}login: evidence kept at {_ev}\n")
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
        # Row 722 G23: the login browser MIRRORS the stepper profile
        # (``cloak.cloaked_page``): NO hand-built args list and NO implicit
        # system-Chrome channel. Measured (cloak.py + cloakbrowser.build_args):
        # cloakbrowser merges caller args OVER its stealth defaults by flag key,
        # so the old list's --enable-features/--disable-features/
        # --disable-blink-features=AutomationControlled replaced the profile the
        # patched binary ships, and --window-size suppressed its
        # --start-maximized (window/screen coherence). channel="chrome" with the
        # implicit use_real_chrome default made the Playwright backend launch
        # stock system Chrome with none of it. Castle.io blacked that browser
        # ("functionality that was blocked by your browser") while the stepper's
        # cloaked_page(headless=False) rendered the same page clean. The
        # system-Chrome channel survives ONLY when the site config sets
        # use_real_chrome explicitly True (key present).
        launch_args=None
        from .. import cloak as _cloak
        login_extra = {}
        if "use_real_chrome" in config and config.get("use_real_chrome"):
            login_extra["channel"]="chrome"
            sys.stderr.write(f"  {site_tag()}login: browser profile = system chrome (use_real_chrome explicit)\n")
        else:
            sys.stderr.write(
                f"  {site_tag()}login: browser profile = cloak default ({_cloak.resolve_backend(config)})\n")
        try:
            browser, pw, backend = _cloak.launch_browser(
                headless=False, args=launch_args, config=config, **login_extra)
        except Exception as e:
            if login_extra.get("channel"):
                # Row 723: the retry is a degradation of THIS site's login
                # (a different browser fingerprint than it asked for), so it
                # is filed in cloak's ledger under the site that owns the
                # flow -- the stderr line alone reaches no run record.
                sys.stderr.write(f"  {site_tag()}login: system Chrome unavailable ({str(e)[:60]}); using bundled\n")
                _ch=login_extra.pop("channel",None)
                try:
                    browser, pw, backend = _cloak.launch_browser(
                        headless=False, args=launch_args, config=config, **login_extra)
                except Exception as e2:
                    _cloak.note_channel_fallback(
                        site_id=_cloak.ledger_site_id(config), flow="login",
                        channel=str(_ch), error=f"{type(e2).__name__}: {e2}",
                        recovered=False)
                    raise
                _cloak.note_channel_fallback(
                    site_id=_cloak.ledger_site_id(config), flow="login",
                    channel=str(_ch), error=f"{type(e).__name__}: {e}",
                    recovered=True)
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
                sys.stderr.write(f"  {site_tag()}login: stealth install failed: {str(e)[:80]}\n")
        page=ctx.new_page()
        # v3.43.56: apply playwright-stealth library if configured
        try:
            from .. import stealth as _stealth
            _stealth.apply_to_page(page, config)
        except Exception as e:
            sys.stderr.write(f"  {site_tag()}login: stealth library apply failed: {str(e)[:80]}\n")
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
            sys.stderr.write(f"  {site_tag()}login: recorder install failed: {e}\n")
        try: page.goto(url,wait_until="domcontentloaded",timeout=25000)
        except PWTimeout:
            _hard_close(); return False,"Login page timed out loading",[]
        time.sleep(1.5)
        # Re-install on the loaded page (in case the init script didn't apply)
        try:
            from ..learn import install_recorder
            install_recorder(page)
        except Exception: pass

        # Row 722 (adulttime): a Cloudflare managed challenge page ("Just a
        # moment...", Turnstile CHECKBOX) stands BEFORE the login form.  Click
        # the box (operator decision, never a puzzle) and wait for the real
        # page; otherwise the username search below reports its own failure.
        clear_cloudflare_challenge(page)

        # Row 371: a missing login form has several visually identical causes.
        # Clear declared per-site gates FIRST, then the conservative generic
        # consent/age/interstitial tiers, before selector probing can mislabel
        # the page as stale.  The helper verifies origin after every click and
        # returns structured outcomes; none of this is allowed to be silent.
        from ..interstitial import (
            dismiss_gates as _dismiss_page_gates,
            first_safety_unknown as _first_gate_unknown,
            safety_unknown_diagnostic as _gate_unknown_diagnostic,
            first_blocked_after_gate as _first_gate_blocked,
            blocked_after_gate_diagnostic as _gate_blocked_diagnostic,
        )

        def _report_gate_actions(actions):
            for action in actions:
                outcome = action.get("outcome", "unknown")
                tier = action.get("tier", "unknown")
                label = action.get("label", "")
                reason = action.get("reason", "")
                if outcome == "cleared":
                    sys.stderr.write(f"  {site_tag()}login: {reason}\n")
                elif outcome == "refused":
                    sys.stderr.write(
                        f"  {site_tag()}login: refused {tier} gate via {label!r} — "
                        f"{reason}\n")
                else:
                    sys.stderr.write(
                        f"  {site_tag()}login: {outcome} for {tier} gate via "
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
        # Row 721: a cleared gate can be the shell of a block page,
        # and an unreadable landing is not a clearance either.
        _pre_form_blocked = _first_gate_blocked(_pre_form_gate_actions)
        if _pre_form_blocked:
            _reason = _gate_blocked_diagnostic(_pre_form_blocked)
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
        _flow_ran = False
        try:
            _flow_res = replay_saved_login_flow(page, config)
            _flow_ran = bool(_flow_res.get("ran"))
            if _flow_ran:
                sys.stderr.write(
                    f"  {site_tag()}login: replayed saved login flow "
                    f"({_flow_res.get('steps', 0)} steps, "
                    f"ok={_flow_res.get('ok')})\n")
                time.sleep(wait)
        except Exception as _e:
            sys.stderr.write(
                f"  {site_tag()}login: login-flow replay skipped: {str(_e)[:80]}\n")

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
                            f"  {site_tag()}login: AI proposal score={grade['score']}, "
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
                sys.stderr.write(f"  {site_tag()}login: AI assist failed: {e} "
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
                f"  {site_tag()}login: login-form trigger: {trigger_detail} "
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
        sys.stderr.write(f"  {site_tag()}login: filled username via [{info}]\n")

        # Phase 15.5: brief "thinking" pause between username and password
        # fields. Real users don't tab instantly — they read the next field's
        # label, position the cursor, etc. Row 1049: the scheduler's
        # inter-field delay supplies it.
        _inter_field_pause()

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
        sys.stderr.write(f"  {site_tag()}login: filled password via [{info}]\n")

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
        # Row 721: a cleared gate can be the shell of a block page,
        # and an unreadable landing is not a clearance either.
        _fill_blocked = _first_gate_blocked(_fill_gate_actions)
        if _fill_blocked:
            _reason = _gate_blocked_diagnostic(_fill_blocked)
            _hard_close()
            return False, _reason, []
        _fill_wall_cleared = any(action.get("outcome") == "cleared"
                                 for action in _fill_gate_actions)
        if _fill_wall_cleared:
            sys.stderr.write(f"  {site_tag()}login: dismissed a post-login interstitial "
                             "reached by auto-submit-on-fill\n")
            try: page.wait_for_load_state("domcontentloaded",timeout=10000)
            except Exception: pass
        try:
            cur_after_fill=page.url
        except Exception:
            cur_after_fill=""
        if success and success_url_reached(success, cur_after_fill, url):
            if _fill_wall_cleared:
                sys.stderr.write(f"  {site_tag()}login: at success URL after dismissing the wall "
                                 f"({redact_url_credentials(cur_after_fill)[:80]})\n")
                cookies=pw_to_json(ctx.cookies()); _hard_close()
                return True,(f"OK — {len(cookies)} cookies "
                             f"(auto-submitted on fill; wall dismissed)"),cookies
            sys.stderr.write(f"  {site_tag()}login: page already at success URL after fill "
                             f"({redact_url_credentials(cur_after_fill)[:80]})\n")
            cookies=pw_to_json(ctx.cookies()); _hard_close()
            return True,f"OK — {len(cookies)} cookies (auto-submitted on fill)",cookies

        if _try_turnstile_one_click(page, config):
            sys.stderr.write(f"  {site_tag()}login: clicked one local Turnstile checkbox\n")
        tok,waited=_wait_captcha_tokens(page,deadline=30)
        if tok and waited is None:
            sys.stderr.write(f"  {site_tag()}login: {tok} NOT populated within 30s "
                             f"(submit will likely be refused as wrong captcha)\n")
        elif tok:
            sys.stderr.write(f"  {site_tag()}login: {tok} populated after {waited:.1f}s\n")

        # Phase 19: check "Remember me" / "Keep me signed in" if present.
        # Best-effort — silently no-ops if no such checkbox exists. Helps
        # extend session lifetimes so re-login storms happen less often.
        try: _try_check_remember_me(page)
        except Exception as e: sys.stderr.write(f"  {site_tag()}login: remember-me check skipped: {e}\n")

        # Row 722 (operator, 2026-09-15): uncheck any pre-checked upsell /
        # cross-sale boxes before the form goes out -- submitting with them
        # checked is a purchase risk. "Remember me" above is excluded.
        try:
            _unchecked_upsell = _uncheck_upsell_boxes(page)
            if _unchecked_upsell:
                sys.stderr.write(
                    f"  {site_tag()}login: {len(_unchecked_upsell)} upsell box(es) "
                    f"acted on before submit\n")
        except Exception as e:
            sys.stderr.write(f"  {site_tag()}login: upsell checkbox uncheck skipped: {e}\n")

        # Freeze the jar before submit can mutate it. An unreadable baseline
        # is UNKNOWN, not an empty jar that makes every later cookie new.
        try:
            cookies_before_submit = tuple(dict(c) for c in ctx.cookies())
        except Exception as e:
            cookies_before_submit = None
            sys.stderr.write(f"  {site_tag()}login: pre-submit cookie snapshot failed: {e}\n")
        # Row 722 (operator, 2026-09-15): the filled form is reviewed BEFORE
        # a second submit. Keep what is about to be submitted (HTML + PNG;
        # the password field renders masked) so that review has an object.
        _pre=keep_pre_submit_screenshot(page, config)
        if _pre: sys.stderr.write(f"  {site_tag()}login: pre-submit screenshot kept at {_pre}\n")
        # The sweep is a 3-arg seam (test harnesses stub it); the declared
        # success origin travels through a module slot instead.
        global _SWEEP_DECLARED_ORIGINS
        _SWEEP_DECLARED_ORIGINS={o for o in (_origin(success or ""),) if o}
        ok,method=_submit_login(page,sb_candidates,pf_candidates)
        # Page closed mid-submit (or before) — the form likely auto-submitted
        # on a previous step. Try to read cookies; if we got any usable session
        # cookies, login succeeded silently. Otherwise fall through to manual.
        if ok=="PAGE_CLOSED":
            sys.stderr.write(f"  {site_tag()}login: page closed during submit, checking for cookies\n")
            try:
                cookies=pw_to_json(ctx.cookies())
            except Exception as e:
                cookies=[]
                sys.stderr.write(f"  {site_tag()}login: cookie read after page-close failed: {e}\n")
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
            sys.stderr.write(f"  {site_tag()}login: page closed mid-submit but cookies "
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
                sys.stderr.write(f"  {site_tag()}login: cookie read after non-nav submit failed: {e}\n")
            authed,why=_looks_authenticated(
                cookies_after_submit, before_cookies=cookies_before_submit)
            if authed:
                # v3.66 row 708: the AJAX no-nav path is the seam the row
                # names. A convincing jar is a description of the jar, not
                # a navigation.
                return _no_nav_verdict(page, config, cookies_after_submit,
                                       why, "no nav signal", _hard_close)
            sys.stderr.write(f"  {site_tag()}login: no nav signal and cookies "
                             f"unconvincing ({why})\n")
            if allow_manual_takeover:
                return _hand_off(f"Couldn't submit form: {method}")
            _hard_close(); return False,f"Submit failed: {method}",[]
        sys.stderr.write(f"  {site_tag()}login: submitted via {method}\n")

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
        # bangbros live (11:3xZ): the post-login /store interstitial carries a
        # PRE-CHECKED paid bundle box beside "CONTINUE TO MEMBERS AREA".
        # Pressing continue with it checked is a purchase: uncheck first.
        # bangbros live (13:0xZ, operator screenshots): the /store offer block
        # (CheckboxOfferV2Block) renders ASYNC after load; a walk taken at
        # load sees no box and no CONTINUE TO MEMBERS AREA. Settle first;
        # when the first pass clears nothing on a non-success URL, settle
        # once more and walk again.
        _post_gate_actions = []
        for _pass in (1, 2):
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            if _pass == 2:
                time.sleep(2.5)
            try:
                _post_upsell=_uncheck_upsell_boxes(page)
                if _post_upsell:
                    sys.stderr.write(f"  {site_tag()}login: unchecked {len(_post_upsell)} upsell "
                                     f"box(es) on the post-login page before continuing\n")
                _still=_checked_upsell_boxes(page)
                sys.stderr.write(f"  {site_tag()}login: upsell boxes verified — {len(_still)} still "
                                 f"checked on the post-login page\n")
                if _still:
                    sys.stderr.write(f"  {site_tag()}login: REFUSING to continue past the post-login page: "
                                     f"upsell box still checked {_still[0][:60]!r}\n")
                    _hard_close()
                    return False, (f"post-login page keeps a checked upsell box "
                                   f"({_still[0][:60]!r}) — not continuing (purchase risk)"), []
            except Exception as e:
                sys.stderr.write(f"  {site_tag()}login: post-login upsell scan failed: {e}\n")
            _post_gate_actions = _dismiss_interstitials(page, _wall)
            if any(a.get("outcome") == "cleared" for a in _post_gate_actions):
                break
            try: _cur_now=page.url
            except Exception: _cur_now=""
            if not success or success_url_reached(success, _cur_now, url):
                break
            if _pass == 1:
                sys.stderr.write(f"  {site_tag()}login: post-login page is not the success URL and "
                                 "no gate cleared — settling and walking once more\n")
        _report_gate_actions(_post_gate_actions)
        _post_unknown = _first_gate_unknown(_post_gate_actions)
        if _post_unknown:
            _reason = _gate_unknown_diagnostic(_post_unknown)
            _hard_close()
            return False, _reason, []
        # Row 721: a cleared gate can be the shell of a block page,
        # and an unreadable landing is not a clearance either.
        _post_blocked = _first_gate_blocked(_post_gate_actions)
        if _post_blocked:
            _reason = _gate_blocked_diagnostic(_post_blocked)
            _hard_close()
            return False, _reason, []
        _clicked = [action for action in _post_gate_actions
                    if action.get("outcome") == "cleared"]
        if _clicked:
            sys.stderr.write(f"  {site_tag()}login: dismissed post-login interstitial "
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
        _rejected_login = cur.partition("?")[0].lower().endswith("/badlogin")
        _body_unreadable = False
        if not _rejected_login:
            try:
                _rejected_login = "wrong username or password provided" in page.content().lower()
            except Exception as exc:
                # Adjudicator ruling on PR#879: only a page that is GONE stays
                # unreadable. A transient failure during the post-submit
                # navigation ("execution context was destroyed") reads fine once
                # the page settles, and skipping the settled read there would
                # lose the rejection this block exists to find.
                _body_unreadable = _page_is_gone(page, exc)
                # Row 813 (the DP-13 hit O805 deferred). The swallow is correct --
                # the /badlogin URL check above is the primary signal and still
                # decides -- but a silent one made an unreadable body look exactly
                # like a body that said nothing.
                sys.stderr.write(
                    f"  {site_tag()}login: rejected-login body probe could not read the page "
                    f"({exc.__class__.__name__}); the landing-URL check decides\n")
        if _rejected_login:
            _hard_close()
            return False, f"Rejected login landing: {cur[:200]}", []
        # Row 722 live (blacked, 11:1xZ): the site answers a good submit with
        # a Turnstile challenge page (login.vixen.com/.../login/challenge)
        # BEFORE the members redirect. Same rule as before the form (G22):
        # click the checkbox, wait for it to clear, then read the URL again.
        # blacked live (15:5xZ): the site accepts the submit and shows a bare
        # page whose /i/blacked/wait-redirect script sends the browser to
        # members.blacked.com a few seconds later; the URL was judged before
        # that fired. A transitional page (no form, not the success page,
        # no challenge) gets up to 30s for its own redirect.
        def _wait_transitional(cur, budget=30):
            # Returns the URL after giving a transitional page (no form, not
            # the success page, no challenge) up to `budget`s to redirect.
            if not success or success_url_reached(success, cur, url) \
                    or _is_cloudflare_challenge_page(page):
                return cur
            _t0=time.time()
            def _form_is_live():
                # blacked (16:5xZ): the wait-redirect shell keeps #password in
                # the DOM (":visible" says yes) while a redirect script runs
                # and the field sits off-viewport. Only an ON-SCREEN field
                # with no redirect script pending counts as "the form is back".
                try:
                    if page.locator("script[src*='wait-redirect'], "
                                    "meta[http-equiv='refresh' i]").count():
                        return False
                    pw=page.locator("input[type=password]:visible").first
                    if not pw.count():
                        return False
                    bb=pw.bounding_box() or {}
                    vp=page.viewport_size or {}
                    if bb and vp and (bb.get("x",0) >= vp.get("width",10**9)
                                      or bb.get("y",0) >= vp.get("height",10**9)
                                      or bb.get("x",0)+bb.get("width",0) <= 0):
                        return False
                    return True
                except Exception:
                    return False
            while time.time()-_t0 < budget:
                try:
                    _now=page.url
                    _form=_form_is_live()
                except Exception:
                    return cur
                if _now!=cur:
                    sys.stderr.write(f"  {site_tag()}login: post-submit page redirected after "
                                     f"{time.time()-_t0:.1f}s -> {_now[:80]}\n")
                    try: page.wait_for_load_state("domcontentloaded",timeout=10000)
                    except Exception: pass
                    return _now
                if _form or _is_cloudflare_challenge_page(page):
                    return cur
                time.sleep(1.0)
            sys.stderr.write(f"  {site_tag()}login: post-submit page did not redirect within {budget}s\n")
            return cur
        cur=_wait_transitional(cur)
        if _is_cloudflare_challenge_page(page):
            sys.stderr.write(f"  {site_tag()}login: post-submit cloudflare challenge page\n")
            if clear_cloudflare_challenge(page):
                try: page.wait_for_load_state("domcontentloaded",timeout=10000)
                except Exception: pass
                try: cur=page.url
                except Exception: pass
                # vixen live (15:4xZ): the cleared challenge can land on a
                # dead "Not found" page at the challenge URL itself instead
                # of the site's redirect. The clearance cookie is in the jar
                # now: re-enter the declared success page (or the login
                # page) and let the surface judgment below decide.
                # blacked (16:2xZ): the cleared page can itself be the
                # site's wait-redirect shell -- let it redirect first.
                cur=_wait_transitional(cur)
                try: _t=(page.title() or "").lower()
                except Exception: _t=""
                if "/challenge" in cur or "not found" in _t:
                    _target=success if (success and success.startswith("http")) else url
                    sys.stderr.write(f"  {site_tag()}login: challenge cleared onto a dead page "
                                     f"({cur[:80]}); re-entering {_target[:80]}\n")
                    try:
                        page.goto(_target, wait_until="domcontentloaded", timeout=30000)
                        try: page.wait_for_load_state("networkidle", timeout=8000)
                        except Exception: pass
                        cur=page.url
                        # ...and the re-entered page may bounce through the
                        # same shell (login.vixen.com/i/<brand>/login?).
                        cur=_wait_transitional(cur)
                    except Exception as e:
                        sys.stderr.write(f"  {site_tag()}login: re-entry failed: {e}\n")
                # vixen (16:3xZ): the challenge swallowed the login POST --
                # the form is back, EMPTY. Fill and submit it ONCE more; the
                # clearance cookie now lets the POST through.
                try: _form_back=page.locator("input[type=password]:visible").count()>0
                except Exception: _form_back=False
                if _form_back:
                    sys.stderr.write(f"  {site_tag()}login: challenge cleared but the login form is "
                                     "back; re-submitting once\n")
                    _ok_u,_=_try_fill(page,uf_candidates,username,"username (re-submit)")
                    _ok_p,_=_try_fill(page,pf_candidates,password,"password (re-submit)")
                    if _ok_u and _ok_p:
                        _ok2,_m2=_submit_login(page,sb_candidates,pf_candidates)
                        sys.stderr.write(f"  {site_tag()}login: re-submit -> {_ok2} ({_m2})\n")
                        try: page.wait_for_load_state("domcontentloaded",timeout=10000)
                        except Exception: pass
                        try: cur=page.url
                        except Exception: pass
                        cur=_wait_transitional(cur)
                        # vixen (17:0xZ): the re-submit is answered by a SECOND
                        # challenge (new __cf_chl_rt_tk). Clear that one too,
                        # then wait for the site's redirect; never a third.
                        if _is_cloudflare_challenge_page(page):
                            sys.stderr.write(f"  {site_tag()}login: second cloudflare challenge after "
                                             "the re-submit\n")
                            if clear_cloudflare_challenge(page):
                                try: page.wait_for_load_state("domcontentloaded",timeout=10000)
                                except Exception: pass
                                try: cur=page.url
                                except Exception: pass
                                cur=_wait_transitional(cur)
                                if "/challenge" in cur:
                                    _target=success if (success and success.startswith("http")) else url
                                    sys.stderr.write(f"  {site_tag()}login: second challenge cleared; "
                                                     f"re-entering {_target[:80]}\n")
                                    try:
                                        page.goto(_target, wait_until="domcontentloaded", timeout=30000)
                                        cur=_wait_transitional(page.url)
                                    except Exception as e:
                                        sys.stderr.write(f"  {site_tag()}login: re-entry failed: {e}\n")
        # A URL move only says that the form left its original page.  It does
        # not say that the destination finished loading or that it is members
        # content: challenge pages commonly redirect first and render later.
        # Keep both cases as distinct, falsy outcomes so callers do not spend
        # another credential attempt treating them as an ordinary success.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except PWTimeout:
            return _settled_non_success(
                page, config, "settled-timeout",
                "post-submit landing did not reach DOMContentLoaded",
                _hard_close)
        # Settling can finish a redirect or render a rejection. The verdict
        # and origin check must use that final page, not the loading shell.
        cur = page.url
        _landing_text = _landing_title = ""
        # A body that could not be read above because the PAGE IS GONE will not
        # become readable here, so re-reading it would only repeat the same
        # failure silently. Any other probe failure is transient and the settled
        # page must still be judged.
        if not _body_unreadable:
            try:
                from bs4 import BeautifulSoup
                _landing_doc = BeautifulSoup(page.content(), "html.parser")
                _landing_text = " ".join(_landing_doc.get_text(" ", strip=True).split()).lower()
                _landing_title = (" ".join(_landing_doc.title.get_text(" ", strip=True).split()).lower()
                                  if _landing_doc.title else "")
            except Exception:
                _landing_text = _landing_title = ""
        if (cur.partition("?")[0].lower().endswith("/badlogin")
                or "wrong username or password provided" in _landing_text):
            _hard_close()
            return False, f"Rejected login landing: {cur[:200]}", []
        if (_landing_title == "just a moment"
                or any(marker in _landing_text for marker in _CHALLENGE_LANDING_MARKERS)):
            return _settled_non_success(
                page, config, "settled-challenge",
                "post-submit landing is a challenge page", _hard_close)
        if success and not success_url_reached(success, cur, url):
            # Row 722s: `cur` may carry the submitted fields (a GET form);
            # the reason reaches login_status, the journal and a filename.
            _why=f"Expected URL contains {success!r}, got {log_url(cur)}"
            if allow_manual_takeover:
                return _hand_off(_why)
            _hard_close(); return False,_why,[]
        # v3.66 row 774: the page we are about to read cookies from must be
        # on the login page's origin, or on one the site DECLARED -- the
        # captured multi-step flow ran (v3.66.302), or success_url is an
        # absolute URL naming this destination origin. A jar read on
        # accounts.google.com is not a login, however many cookies it holds.
        cur_origin=_origin(cur)
        declared_origins={o for o in (_origin(url), _origin(success or "")) if o}
        if not _flow_ran and (cur_origin is None or cur_origin not in declared_origins):
            if cur_origin is not None and _same_brand_origin(url, cur):
                # Row 722 (G17, operator 2026-09-15): a same-brand landing
                # (site-ma.brazzers.com -> www.brazzers.com, www.blacked.com
                # -> members.blacked.com) is not a blocker. Keep what the
                # page showed, then judge it exactly as a same-origin landing
                # below -- the origin alone never hands off.
                _ev=write_login_evidence(page, config, cur, "login-cross-origin-landing")
                sys.stderr.write(f"  {site_tag()}login: cross-origin landing recorded (same brand: "
                                 f"{_brand_host(url)} -> {_brand_host(cur)}); "
                                 f"evidence {_ev}\n")
            else:
                why=(f"cross-origin navigation refused: login page {_origin(url)} "
                     f"ended on {cur_origin or cur[:80]!r} (submit: {method})")
                if allow_manual_takeover:
                    return _hand_off(why)
                _hard_close(); return False,why,[]
        # Row 722 (nookies.com): a same-origin navigation plus a changed
        # jar is NOT proof of authentication -- anonymous visitors get a
        # session cookie too, and the page that came back still showed
        # "LOG IN / JOIN NOW". Read what the page SHOWS; a declared
        # success_url match or a present member indicator still wins.
        cookies=pw_to_json(ctx.cookies())
        anonymous,anon_why=anonymous_surface_check(page)
        if anonymous:
            confirmed,member_why,evidence=member_state_check(
                page, config, tag="login-post-submit-anonymous")
            if not confirmed:
                # The jar is carried as a description, never as the verdict.
                why=(f"{anon_why}; {member_why}; {len(cookies)} cookies"
                     f"{'; evidence ' + evidence if evidence else ''}"
                     f" (submit: {method}) \u2014 NOT success")
                sys.stderr.write(f"  {site_tag()}login: {why}\n")
                if allow_manual_takeover:
                    return _hand_off(why)
                _hard_close(); return False,why,[]
            sys.stderr.write(f"  {site_tag()}login: {anon_why} but {member_why}\n")
        # Rows 722s+772 reconcile: row 722s's base carried ONE row-772 probe,
        # which 722s moved here behind the UNKNOWN-surface guard. Main now
        # judges /badlogin and the rejection phrase on the SETTLED page
        # above (row 772 residual), with row 813's rule that a body which
        # is GONE is read exactly once. A third read here re-judged the same
        # settled URL/body and broke that one-read pin, so it is not kept.
        try:
            from ..vault_sync import get_vault_sync
            vs = get_vault_sync()
            if vs is not None:
                sid = config.get("site_id") or config.get("name") or ""
                if not sid and callable(site_tag):
                    sid = site_tag().strip("[]: ")
                if sid:
                    vs.set_session(sid, "0", {"cookies": cookies})
        except Exception as e:
            sys.stderr.write(f"  {site_tag()}vault_sync login persist failed: {e}\n")
        _hard_close()
        return True,f"OK — {len(cookies)} cookies (submit: {method})",cookies
    except Exception as e:
        # Programming error or fatal exception — never offer manual takeover
        # because the browser state is unknown.
        _hard_close()
        return False,f"login error: {str(e)[:200]}",[]
