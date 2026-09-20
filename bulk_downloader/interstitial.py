"""Shared, origin-safe dismissal of declared and generic page gates.

v3.66.1016 (item E of 15.74's A-H program). BD has always been able to click
away an overlay -- ``runner._process_one`` has carried a dismissal loop for
years and ``site_templates/_data_players.py`` hand-writes the selectors for the
Gamma brands. What did not exist is any way to say WHEN a given selector should
fire, and the two answers are genuinely different:

    a cookie / age / consent gate     can appear on ANY content page
    a post-login "No Thanks" wall     appears once, between login and content

Firing the second per URL is wrong twice over. It costs a full timeout on every
URL for an element that cannot be there (measured against a real chromium on a
page where nothing matches: 3.00s per selector LINE -- so 15.01s for the five
Gamma selectors written one per line, and 3.00s for the same five written as one
comma-joined line, which is the shape that ships today). And it does not fire
where it is actually needed: ``do_login`` dismissed nothing between
``_submit_login`` and its ``success_url`` comparison, so a wall standing between
the login POST and the members area left ``page.url`` on the wall and threw a
successful login into manual takeover.

So the scope is declared, per site, by which key the selectors live in:

    dismiss_selectors        per-page  -- every content URL (runner._process_one)
    dismiss_selectors_login  login wall -- once, in do_login, post-submit

THE LOOP ITSELF IS SHARED DELIBERATELY. Two copies of a click-with-timeout loop
drift -- one grows a settle or an early exit, the other does not, and nothing
compares them. ``tests/test_v3_66_1016_login_interstitial.py`` asserts by AST
census that the product carries exactly one, and proves the census can see a
known positive before believing it.

Row 371 adds an always-on semantic fallback after those measured selectors.
It encounters consent, age, and interstitial layers in that order; refuses
exit-like labels; checks origin after every click; and returns structured
outcomes so every runtime consumer can report what happened. ``page`` remains
duck-typed, so the safety and ordering rules are testable without a browser.
"""
from __future__ import annotations

import re
import time
from typing import Any, List, Optional, Tuple
from urllib.parse import urlsplit

from .registrable_domain import registrable_domain, same_site


# The runner has used these two numbers since the loop was written; they are
# named here rather than restated at each call site so the two consumers cannot
# drift apart on them.
DEFAULT_TIMEOUT_MS = 3000
DEFAULT_SETTLE_S = 0.5
DEFAULT_NAVIGATION_TIMEOUT_MS = 30000
DEFAULT_SITE_APPEAR_MS = DEFAULT_TIMEOUT_MS

# Row 722 (bangbros / Aylo, 2026-09-15): a consent banner rendered by React can
# legitimately re-render ONCE between the generic pass's snapshot and the
# click that was about to use it, which reads identically to real DOM drift.
# Two bounded re-measurements, spaced to let a single re-render settle, give
# that one race a second chance without widening what "changed after
# snapshot" refuses -- a control that keeps changing gets the same refusal
# it always got.
GENERIC_REMEASURE_INTERVAL_S = 0.3
GENERIC_REMEASURE_ATTEMPTS = 2

GENERIC_CONTROL_SELECTOR = (
    "button, a, [role='button'], input[type='button'], input[type='submit']"
)

_CONTROL_SNAPSHOT_JS = """els => els.map((el, index) => {
  const style = window.getComputedStyle(el);
  const rects = el.getClientRects();
  const visible = rects.length > 0 && style.display !== 'none' &&
    style.visibility !== 'hidden' && style.visibility !== 'collapse';
  const raw = [el.innerText, el.getAttribute('aria-label'),
    (typeof el.value === 'string' ? el.value : null),
    el.getAttribute('title')];
  const labels = [...new Set(raw.filter(v => typeof v === 'string')
    .map(v => v.replace(/\\s+/g, ' ').trim()).filter(Boolean))];
  return {index, visible, labels};
})"""

# Conservative, anchored phrases only. The generic pass follows measured
# selectors, so a vague word such as bare "Continue" is not enough authority
# to click a control.
_GENERIC_TIERS = (
    ("consent", re.compile(
        r"^(?:(?:accept|allow) all(?: cookies)?|"
        r"(?:accept|allow) cookies|agree and continue)$", re.I)),
    # Row 722s (hustlerunlimited.com/login/, 2026-09-15 14:33Z; bang.com):
    # "Yes, I'm Over 18" / "Yes I am 18 or over" stood in front of the login
    # form (its Turnstile under the modal, the submit click forced) and
    # matched no tier. The affirmation vocabulary now admits OVER / AT LEAST
    # and "or over" / "years old" tails; fullmatch keeps it to the bare
    # affirmation, never a sentence.
    ("age", re.compile(
        r"^(?:i agree[, ]+(?:enter|continue)(?: here)?|"
        r"(?:yes[, ]+)?i(?: am|'m) (?:over |at least )?(?:18|21)"
        r"(?: or (?:older|over))?(?: years?(?: old| of age)?)?)$", re.I)),
    ("interstitial", re.compile(
        r"^(?:no[ ,.]+thanks(?:[., ]+continue(?: to (?:members(?: area)?|the site))?)?|"
        r"continue to (?:members(?: area)?|the site)|"
        r"skip (?:this page|for now))$", re.I)),
)

DENIED_CONTROL_TERMS = (
    "exit", "leave", "disagree", "decline", "reject", "deny",
    "opt-out", "cancel", "under-18",
)

# Row 721. The Gamma Billing age wall ("ACCESS BEYOND THIS PAGE IS RESTRICTED
# TO ADULTS", EXIT HERE / ENTER) is an interstitial to click through, and the
# product handled only its safety half: EXIT is refused, ENTER had no
# affordance, so the wall was neither dismissed nor reported as dismissible.
#
# A bare "ENTER" is exactly the vague single word the generic tiers refuse on
# purpose, so it is NOT admitted on its own. It is admitted only when the PAGE
# CONTENT corroborates an age gate -- 18-plus language together with BOTH an
# ENTER and an EXIT affordance. Recognition is by content, never by URL shape:
# /login-abused is the same path whether it serves a gate or an abuse response,
# and the operator screenshot that filed this row is the proof that the URL
# cannot tell the two apart.
AGE_GATE_LANGUAGE = re.compile(
    r"(?:restricted to adults|adults only|adult content|age verification|"
    r"\b(?:18|21)\s*\+|"
    r"\b(?:you\s+)?(?:must|have to) be (?:at least )?(?:18|21)\b|"
    r"\b(?:18|21) years? (?:of age )?or older\b|"
    r"\bover (?:18|21) years?\b)", re.I)

# Row 722 (kink.com, 2026-09-15): the wall reads "ENTER KINK" beside
# "I Disagree, Exit Here". ENTER plus ONE brand-shaped token is admitted; an
# instruction word after ENTER ("your", "password", "pin", "code", ...) is not,
# and two or more tokens ("ENTER YOUR CARD DETAILS", row 792) never are. The
# content corroboration (18-plus language AND an EXIT beside it) still gates
# every admission below.
ENTER_AFFORDANCE = re.compile(
    r"^(?:enter|enter (?:site|here|the site|this site)|"
    r"enter (?!(?:your|my|a|an|the|password|passcode|pin|code|email|username|"
    r"user|login|card|number|amount|details|text|otp)$)"
    r"[a-z][a-z0-9'.&!-]{0,19})$", re.I)

# Row 722 (blacked.com, 2026-09-15): the wall reads bare "I AGREE" beside
# "I DISAGREE". ENTER_AFFORDANCE never covered agreement phrasing, so the
# affirmative had no admission and the gate stood between login and the
# members area. Admitted ONLY the bare/short forms -- "AGREE", "I AGREE",
# "YES, I AGREE" -- fullmatch keeps a ToS/checkout control such as "I AGREE
# TO THE TERMS" out on vocabulary alone, and the SAME content corroboration
# ENTER_AFFORDANCE uses (18-plus language AND a denylisted sibling control)
# still gates every admission below; it is never promoted into the
# unconditional generic "age" tier.
AGREE_AFFORDANCE = re.compile(r"^(?:i )?agree$|^yes[, ]+i agree$", re.I)

# Row 722 (nookies.com, 2026-09-15): a post-login gateway page ("OUR
# EXCLUSIVE PARTNERS & PREMIUM DEALS") carries a same-origin "ACCESS NOOKIES"
# beside foreign partner upsells ("JOIN NOW", "SAVE BIG NOW"). The walker's
# interstitial tier admitted "CONTINUE TO MEMBERS AREA"-shaped labels but not
# "ACCESS <brand>", so the run never left the gateway.  ACCESS plus ONE
# brand-shaped token is admitted, same discipline as ENTER_AFFORDANCE: an
# instruction word after ACCESS ("your", "code", "denied", "restricted", ...)
# is refused, and two or more tokens (row 792 style, e.g. "ACCESS YOUR
# ACCOUNT") never are.  "ENTER MEMBERS AREA" / "GO TO MEMBERS AREA" /
# "ACCESS MEMBERS AREA" are admitted as fixed phrases.  Corroboration is the
# control's OWN destination (same-origin href, or no href at all), never page
# content -- see the same-origin check at its call site.
ACCESS_AFFORDANCE = re.compile(
    r"^(?:access members area|enter members area|go to members area|"
    r"access (?!(?:your|my|a|an|the|password|passcode|pin|code|email|"
    r"username|user|login|card|number|amount|details|text|otp|denied|"
    r"restricted)$)"
    r"[a-z][a-z0-9'.&!-]{0,19})$", re.I)

# Read AFTER the ENTER click, never before it: the evilangel measurement shows
# the block page sitting BEHIND the gate, invisible until the gate is cleared.
# Clicking through is what makes the true state observable; reporting it as a
# block is what keeps it from being presented as members content.
BLOCK_LANGUAGE = re.compile(
    r"(?:\byour ip (?:address )?(?:was|has been|is) blocked\b|"
    r"\bip (?:address )?blocked\b|\baccess denied\b|"
    r"\btoo many (?:failed )?(?:login )?attempts\b|"
    r"\baccount (?:has been )?(?:locked|suspended)\b|"
    r"\btemporarily blocked\b)", re.I)

BODY_TEXT_SELECTOR = "body"

# Row 762. The Aylo /store post-login upsell: a PRECHECKED consent box beside
# "you agree to add <upsell> ... charged $29.97 every 30 days until you
# cancel", under CONTINUE TO MEMBERS AREA. That label is exactly what the
# interstitial tier and the measured per-site line clear, so on that page the
# dismisser's click is a purchase. Recognition is by CONTENT: recurring-charge
# language (money AND a period, or an explicit rebill phrase -- a price alone
# is a sale banner, "every 30 days" alone is a release schedule) together with
# a checked checkbox. Charge language with an unmeasurable box is UNKNOWN and
# refuses (A2): unavailable evidence is never permission to spend money.
RECURRING_CHARGE_LANGUAGE = re.compile(
    r"(?:(?:[$€£]\s?\d[\d,]*(?:\.\d{2})?|\b\d[\d,]*(?:\.\d{2})?\s?(?:usd|eur|gbp))"
    r"\s*(?:/|per|every|each|a)\s*(?:\d+\s*)?(?:days?|weeks?|months?|years?|mo|yr)\b|"
    r"\buntil you cancel\b|\bwill be (?:charged|billed)\b|\brebills?\b|"
    r"\brecurring (?:charge|billing|payment|subscription)\b)", re.I)

CHECKED_CONSENT_SELECTOR = "input[type='checkbox']:checked"

SAFETY_UNKNOWN_OUTCOMES = frozenset({
    "label_unknown",
    "origin_unknown",
    "origin_recovery_unknown",
    "destination_re_request_unknown",
    "click_unknown",
    "measurement_unknown",
})

# A block page behind a cleared gate is a KNOWN state, so it is deliberately
# NOT in SAFETY_UNKNOWN_OUTCOMES (that frozenset is pinned as the fail-closed
# set). It is reported on its own so no caller can read it as members content.
BLOCKED_AFTER_GATE_OUTCOMES = frozenset({
    "blocked_after_gate", "gate_landing_unknown",
    # Row 912: a gate measured still covering the viewport after its click is
    # a blocked page at the consumer (runner._page_gates_are_safe), not a
    # cleared one.
    "viewport_blocked",
})

# Generic controls that accept/continue, in the order a person encounters the
# layers.  These are deliberately conservative: one successful click per tier,
# never a sweep across every matching control.
CONSENT = [
    "#onetrust-accept-btn-handler",
    "[id*='cky'] button:has-text('Accept')",
    "[class*='cookie' i] button:has-text('Accept')",
    "button:has-text('Accept All')",
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
    "button:has-text('I Agree')",
    "button:has-text('Agree')",
    "button:has-text('Got it')",
    "button:has-text('Allow all')",
    "button:has-text('OK')",
]
AGE = [
    "button:has-text('I am 18')",
    "a:has-text('I am 18')",
    "button:has-text('Enter Site')",
    "a:has-text('Enter Site')",
    "button:has-text('Enter')",
    "a:has-text('Enter')",
    "button:has-text('I am over')",
    "button:has-text('Continue')",
]
INTERSTITIAL = [
    "a:has-text('No Thanks')",
    "button:has-text('No Thanks')",
    "a:has-text('No, Thanks')",
    "button:has-text('No, Thanks')",
    "a:has-text('Continue to Members Area')",
    "button:has-text('Skip')",
    "[class*='close' i]:visible",
    "button[aria-label*='close' i]",
]

# Never choose a control that declines, exits, or opts out.  This exact
# denylist is grounded in the measured kink.com failure where "I Disagree,
# Exit Here" navigated to an unrelated Google sign-in page.
FORBIDDEN = re.compile(
    r"\b(exit|leave|disagree|decline|reject|deny|opt.?out|cancel|"
    r"i am under|under 18|not 18|go back|take me (out|back))\b", re.I)

GATE_CLICK_TIMEOUT_MS = 8000
GATE_SETTLE_S = 2.5
ORIGIN_RECOVERY_TIMEOUT_MS = 30000
ORIGIN_RECOVERY_SETTLE_S = 2.0
DESTINATION_TIMEOUT_MS = 45000
DESTINATION_SETTLE_S = 4.0


def selector_lines(raw: Any) -> List[str]:
    """The usable selector lines of a dismiss block.

    One CSS selector per line; blank lines and ``#`` comments are dropped. A
    non-string (missing key, ``None``) yields no lines rather than raising --
    every caller reads this straight off operator-editable config.
    """
    if not isinstance(raw, str):
        return []
    out: List[str] = []
    for line in raw.splitlines():
        sel = line.strip()
        if not sel or sel.startswith("#"):
            continue
        out.append(sel)
    return out


def dismiss(page: Any, raw: Any, *,
            timeout_ms: int = DEFAULT_TIMEOUT_MS,
            settle_s: float = DEFAULT_SETTLE_S,
            sleep=time.sleep) -> List[str]:
    """Click each selector in ``raw`` that resolves to a visible element.

    Every failure is swallowed on purpose: a popup that did not show up must
    never fail a URL, and that has been the shipped behaviour of the per-URL
    loop from the start. The settle sleep happens ONLY after a real click --
    a miss costs its timeout and nothing more.

    Returns the selectors that were actually clicked, which is not the same as
    the ones that were tried. ``do_login`` uses the difference: it waits for a
    load only when something was clicked, so a site with no wall pays nothing.
    """
    clicked: List[str] = []
    for sel in selector_lines(raw):
        try:
            loc = page.locator(sel).first
            loc.wait_for(timeout=timeout_ms, state="visible")
            loc.click()
            clicked.append(sel)
            sleep(settle_s)
        except Exception:
            pass
    return clicked


def _gate_selectors(raw: Any) -> List[str]:
    """Normalise a site's stored dismiss block or an already-split list."""
    if isinstance(raw, str):
        return selector_lines(raw)
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    for candidate in raw:
        if not isinstance(candidate, str):
            continue
        selector = candidate.strip()
        if selector and not selector.startswith("# "):
            out.append(selector)
    return out


def _safe_candidate(page: Any, selector: str) -> Optional[Any]:
    """Return the first visible locator only when its label is safe to click."""
    try:
        candidates = page.locator(selector)
        if not candidates.count():
            return None
        candidate = candidates.first
        if not candidate.is_visible():
            return None
        label = candidate.inner_text() or ""
        if FORBIDDEN.search(label):
            return None
        return candidate
    except Exception:
        return None


def _clear_gates_origin(url: Any) -> str:
    """Return a comparable HTTP(S) origin, or an empty value if unprovable.

    Deliberately NOT the same helper as :func:`_origin` below, and renamed
    rather than merged.  This one answers "" for an unprovable origin, which
    ``clear_gates`` reads as "treat it as an escape"; row 371's ``_origin``
    answers ``None``, which its callers turn into a structured UNKNOWN
    outcome rather than a verdict.  Collapsing the two would silently give
    one of the two contracts the other's fail behaviour, so they stay
    separate until a cut is scoped to reconcile the two orchestrators.
    """
    try:
        parsed = urlsplit(str(url or ""))
    except Exception:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    return "%s://%s" % (parsed.scheme.lower(), parsed.netloc.lower())


def clear_gates(page: Any, *, site_gates: Any = None,
                url: Optional[str] = None, log=None,
                sleep=time.sleep) -> List[str]:
    """Clear configured gates first, then one consent/age/interstitial control.

    Every candidate is checked against :data:`FORBIDDEN` before clicking.  The
    requested origin is verified after every click; an escape is undone and is
    never described as a clearance.  A cleared interstitial is special because
    it can land on the members home page, so the original destination is
    requested again before returning.

    The returned messages name every cleared tier and are also sent to ``log``
    when supplied, giving the runner an operator-visible account of page
    changes without coupling this import-light helper to runner telemetry.
    """
    result: List[str] = []

    def note(message: str) -> None:
        result.append(message)
        if log is not None:
            log(message)

    expected_origin = _clear_gates_origin(url or getattr(page, "url", ""))
    tiers = (
        ("site", _gate_selectors(site_gates)),
        ("consent", CONSENT),
        ("age", AGE),
        ("interstitial", INTERSTITIAL),
    )
    interstitial_cleared = False

    for tier, selectors in tiers:
        for selector in selectors:
            candidate = _safe_candidate(page, selector)
            if candidate is None:
                continue
            before_url = str(getattr(page, "url", "") or "")
            try:
                candidate.click(timeout=GATE_CLICK_TIMEOUT_MS)
                sleep(GATE_SETTLE_S)
            except Exception:
                continue

            after_url = str(getattr(page, "url", "") or "")
            current_origin = _clear_gates_origin(after_url)
            if not expected_origin or current_origin != expected_origin:
                note("%s: %s LEFT THE ORIGIN (%s -> %s) -- going back, "
                     "not trusting it" % (
                         tier, selector, expected_origin, current_origin))
                try:
                    page.go_back(
                        wait_until="domcontentloaded",
                        timeout=ORIGIN_RECOVERY_TIMEOUT_MS,
                    )
                    sleep(ORIGIN_RECOVERY_SETTLE_S)
                except Exception:
                    if url:
                        page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=DESTINATION_TIMEOUT_MS,
                        )
                if (url and _clear_gates_origin(getattr(page, "url", ""))
                        != expected_origin):
                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=DESTINATION_TIMEOUT_MS,
                    )
                break

            note("%s: cleared via %s" % (tier, selector))
            # A configured selector is deliberately tried before the generic
            # tiers, so it has no stored subtype.  A same-origin navigation
            # away from the page it covered is the observable interstitial
            # signal: the destination was swallowed even though the selector
            # ran under the "site" tier.
            if tier == "interstitial" or (url and after_url != before_url):
                interstitial_cleared = True
            break

    if url and interstitial_cleared:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=DESTINATION_TIMEOUT_MS,
        )
        sleep(DESTINATION_SETTLE_S)
        note("re-requested the original url after an interstitial")

    return result


class _LabelMeasurementUnavailable(RuntimeError):
    """A label surface could not be read, so the denylist is incomplete."""

    def __init__(self, surface: str, cause: BaseException):
        super().__init__(surface, cause)
        self.surface = surface
        self.cause_type = type(cause).__name__


def _control_labels(locator: Any) -> List[str]:
    """All observable labels, or raise when any surface is unavailable.

    A partial result is unsafe: visible text can say ``Accept All`` while an
    inaccessible ARIA label says ``Exit``.  Callers turn this exception into a
    structured UNKNOWN verdict rather than treating the readable subset as the
    whole measurement.
    """
    labels: List[str] = []
    try:
        text = locator.inner_text(timeout=500)
    except Exception as exc:
        raise _LabelMeasurementUnavailable("inner-text", exc) from exc
    if isinstance(text, str) and text.strip():
        labels.append(" ".join(text.split()))
    for attr in ("aria-label", "value", "title"):
        try:
            text = locator.get_attribute(attr, timeout=500)
        except Exception as exc:
            raise _LabelMeasurementUnavailable(attr, exc) from exc
        if isinstance(text, str) and text.strip():
            normalized = " ".join(text.split())
            if normalized not in labels:
                labels.append(normalized)
    return labels


def _origin(url: Any) -> Optional[str]:
    """Return a canonical web origin, or ``None`` when it is unmeasurable."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        host = parsed.hostname.lower()
        port = parsed.port
    except (TypeError, ValueError):
        return None
    default = 80 if parsed.scheme == "http" else 443
    suffix = "" if port in (None, default) else f":{port}"
    return f"{parsed.scheme.lower()}://{host}{suffix}"


def _page_url(page: Any) -> Any:
    """Read ``page.url`` without turning unavailable evidence into success."""
    try:
        return page.url
    except Exception:
        return None


def _control_same_origin(page: Any, href: Any) -> Optional[bool]:
    """Whether an ACCESS control's href is same-origin with the page.

    ``None`` means unmeasurable (the page url could not be read), which the
    caller turns into a fail-closed UNKNOWN rather than a click.  A control
    with no href at all (a ``<button>``) can only act on the current page, so
    it is same-origin by construction; a relative href has no host of its own
    and inherits the page's. Only an absolute href naming a different host --
    a partner upsell such as ``join.nookies.test`` -- is foreign.
    """
    if not isinstance(href, str) or not href.strip():
        return True
    try:
        parsed = urlsplit(href)
    except (TypeError, ValueError):
        return None
    if not parsed.netloc:
        return True
    page_url = _page_url(page)
    page_host = None
    if isinstance(page_url, str):
        try:
            page_host = urlsplit(page_url).hostname
        except (TypeError, ValueError):
            page_host = None
    if not page_host:
        return None
    return (parsed.hostname or "").lower() == page_host.lower()


def _same_destination(current: Any, wanted: str) -> bool:
    """Whether ``current`` is already the requested origin + path."""
    if not isinstance(current, str):
        return False
    try:
        here = urlsplit(current)
        target = urlsplit(wanted)
    except (TypeError, ValueError):
        return False
    return (_origin(current) == _origin(wanted)
            and (here.path or "/").rstrip("/")
            == (target.path or "/").rstrip("/")
            and here.query == target.query
            and here.fragment == target.fragment)


def _deny_term(label: str) -> Optional[str]:
    normalized = label.casefold()
    for dash in ("‐", "‑", "‒", "–", "—", "−"):
        normalized = normalized.replace(dash, "-")
    for term in DENIED_CONTROL_TERMS:
        variants = (term, term.replace("-", " "))
        for variant in variants:
            if re.search(r"(?<![a-z0-9])" + re.escape(variant)
                         + r"(?![a-z0-9])", normalized):
                return term
    return None


def first_safety_unknown(actions: Any) -> Optional[dict]:
    """Return the first fail-closed gate outcome, if one was observed."""
    if not isinstance(actions, list):
        return None
    return next((action for action in actions
                 if isinstance(action, dict)
                 and action.get("outcome") in SAFETY_UNKNOWN_OUTCOMES), None)


def safety_unknown_diagnostic(action: dict) -> str:
    """Operator-facing diagnostic for a fail-closed gate action."""
    label = action.get("label", "") if isinstance(action, dict) else ""
    reason = (action.get("reason", "gate measurement unavailable")
              if isinstance(action, dict) else "gate measurement unavailable")
    return f"Page gate safety UNKNOWN for {label!r}: {reason}"


def _measurement_unknown(source: str, tier: str, selector: str,
                         reason: str) -> dict:
    return {
        "source": source,
        "tier": tier,
        "label": "",
        "selector": selector,
        "outcome": "measurement_unknown",
        "reason": reason,
        "destination_re_requested": False,
    }


def _declared_selectors(raw: Any) -> List[str]:
    """Declared gate selectors, where only ``# `` starts a comment.

    ``selector_lines`` treats ANY leading ``#`` as a comment, which silently
    eats every id selector an operator writes -- and the shipped generic
    consent list opens with ``#onetrust-accept-btn-handler``. A declared gate
    block is CSS, so the comment marker has to be unambiguous. Lists are
    normalised by the shared ``_gate_selectors``.
    """
    if not isinstance(raw, str):
        return _gate_selectors(raw)
    out: List[str] = []
    for line in raw.splitlines():
        selector = line.strip()
        if not selector or selector.startswith("# "):
            continue
        out.append(selector)
    return out


def _visible_first(page: Any, selector: str) -> Any:
    """Bind a declared selector to its first VISIBLE match.

    A measured line is frequently a comma group whose first DOM match is the
    hidden alternative -- kink.com renders a display:none ``button`` before the
    live ``a`` -- so ``.first`` alone binds the wrong control and the gate
    reads as absent. Playwright narrows with ``filter(visible=True)``; the
    duck-typed fakes have no such method and fall back to ``.first``, which is
    exactly the shape they model.
    """
    locator = page.locator(selector)
    narrow = getattr(locator, "filter", None)
    if callable(narrow):
        try:
            locator = narrow(visible=True)
        except Exception:
            locator = page.locator(selector)
    return locator.first


_VIEWPORT_BLOCKED_JS = """
el => {
  if (!el.isConnected) return false;
  const r = el.getBoundingClientRect();
  if (!(r.width > 0 && r.height > 0)) return false;
  const cs = getComputedStyle(el);
  if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
  // The OVERLAY root: the outermost positioned ancestor. A fixed/sticky
  // root is an overlay wherever it sits (bottom bars, top bars, modals);
  // an absolute root only when it covers a quarter of the viewport. A
  // static control (an inline link that merely stays in the page) is not
  // an overlay at all.
  let root = null, rootPos = '';
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    const p = getComputedStyle(n).position;
    if (p === 'fixed' || p === 'sticky' || p === 'absolute') { root = n; rootPos = p; }
  }
  if (!root) return false;
  const rr = root.getBoundingClientRect();
  const vw = innerWidth, vh = innerHeight;
  const ix = Math.max(0, Math.min(rr.right, vw) - Math.max(rr.left, 0));
  const iy = Math.max(0, Math.min(rr.bottom, vh) - Math.max(rr.top, 0));
  if (ix <= 0 || iy <= 0) return false;                     // off-screen
  if (rootPos === 'absolute' && (ix * iy) < 0.25 * vw * vh) return false;
  // Is it actually on top? Sample the visible part of the root: its
  // centre plus a 3x3 grid inset from its edges.
  const x0 = Math.max(rr.left, 0), y0 = Math.max(rr.top, 0);
  const pts = [];
  for (const fx of [0.1, 0.5, 0.9]) for (const fy of [0.1, 0.5, 0.9]) pts.push([x0 + ix * fx, y0 + iy * fy]);
  for (const [x, y] of pts) {
    const hit = document.elementFromPoint(x, y);
    if (hit && (hit === root || root.contains(hit))) return true;
  }
  return false;
}
"""

_VIEWPORT_PROBE_TIMEOUT_MS = 1000


def _viewport_cleared(locator) -> Optional[bool]:
    """Row 912: True when the clicked control is gone from the document or
    its overlay root no longer covers any part of the viewport, False when
    the overlay still covers it, None when the locator cannot be asked."""
    evaluate = getattr(locator, "evaluate", None)
    if not callable(evaluate):
        return None
    # A dismissed gate is normally REMOVED from the DOM; a locator that no
    # longer resolves must not wait Playwright's 30s default -- count() is
    # an immediate read, and 0 is clearance (round 2, E1).
    count = getattr(locator, "count", None)
    if callable(count):
        try:
            if count() == 0:
                return True
        except Exception:
            return None
    try:
        try:
            blocked = evaluate(_VIEWPORT_BLOCKED_JS, timeout=_VIEWPORT_PROBE_TIMEOUT_MS)
        except TypeError:
            blocked = evaluate(_VIEWPORT_BLOCKED_JS)  # duck-typed locators without a timeout kwarg
    except Exception:
        return None
    if blocked is None:
        return None
    return not bool(blocked)


def _click_gate(page: Any, locator: Any, *, source: str, tier: str,
                label: str, selector: str = "", destination_url: str = "",
                timeout_ms: int, navigation_timeout_ms: int,
                settle_s: float, sleep) -> dict:
    """Click one already-visible gate control and verify its destination."""
    before_url = _page_url(page)
    before_origin = _origin(before_url)
    base = {
        "source": source,
        "tier": tier,
        "label": label,
        "selector": selector,
        "destination_re_requested": False,
    }
    # UNREADABLE is not the same as NON-WEB. A url property that raises is
    # unavailable evidence and refuses the control; a readable ``about:blank``
    # (or any non-http scheme) simply has no origin to protect, and the verdict
    # that matters there is whether it is still the same non-origin afterwards.
    if not isinstance(before_url, str) or not before_url.strip():
        return {
            **base,
            "outcome": "origin_unknown",
            "reason": "origin unavailable before click; control not used",
        }
    # The requested destination is the authority on WHERE a control may be
    # used. If the page has already drifted to another site, this is the
    # measured kink.com shape -- the qwen run clicked "I Disagree, Exit Here"
    # and then reported "password field present: True" about Google's SSO
    # form. Nothing on a foreign site is clicked, whatever its label says.
    if destination_url and not same_site(destination_url, before_url):
        return {
            **base,
            "outcome": "origin_unknown",
            "reason": (
                "origin mismatch before click: requested site %s, current "
                "site %s; control not used"
                % (registrable_domain(destination_url) or "UNKNOWN",
                   registrable_domain(before_url) or "UNKNOWN")
            ),
        }
    def _re_request(wanted: str) -> tuple:
        """(action_or_None, re_requested). Restore ``wanted`` if it was lost."""
        if not wanted or not same_site(wanted, before_url):
            return None, False
        if _same_destination(_page_url(page), wanted):
            return None, False
        try:
            page.goto(wanted, wait_until="domcontentloaded",
                      timeout=navigation_timeout_ms)
        except Exception as exc:
            return {
                **base,
                "outcome": "destination_re_request_unknown",
                "reason": (
                    f"destination re-request UNKNOWN: {type(exc).__name__}"
                ),
            }, False
        # A site may answer the request with its own same-site redirect (the
        # www -> members case). That is the site's answer to the destination,
        # not a lost destination; a foreign landing is still UNKNOWN.
        if not same_site(_page_url(page), wanted):
            return {
                **base,
                "outcome": "destination_re_request_unknown",
                "reason": (
                    "destination re-request UNKNOWN: target not observed"
                ),
            }, False
        return None, True

    click_error = None
    try:
        locator.click(timeout=timeout_ms)
    except Exception as exc:
        # A Playwright click can time out after the browser has already acted.
        # Origin must still be measured and recovered below.
        click_error = exc
    if click_error is None:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass

    # The click handler may schedule location=... after click() returns. Wait
    # before making the load-bearing origin verdict, then read the URL.
    sleep(settle_s)

    after_origin = _origin(_page_url(page))
    if after_origin != before_origin:
        try:
            page.go_back(wait_until="domcontentloaded",
                         timeout=navigation_timeout_ms)
        except Exception:
            pass
        recovered_origin = _origin(_page_url(page))
        if recovered_origin != before_origin:
            return {
                **base,
                "outcome": "origin_recovery_unknown",
                "reason": (
                    f"origin recovery UNKNOWN: expected {before_origin} after "
                    f"going back, got {recovered_origin or 'UNKNOWN'}"
                ),
            }
        outcome = "origin_unknown" if after_origin is None else "origin_changed"
        reason = (
            f"origin UNKNOWN after click; went back to {before_origin}"
            if after_origin is None else
            (
                (f"control click raised {type(click_error).__name__} and "
                 if click_error is not None else "control ")
                + f"left {before_origin} for {after_origin}; went back"
            )
        )
        # An escape UNDOES the page the operator was on, so the pre-click url
        # is the thing to restore when no caller named a destination. That is
        # not true on the same-origin path below, where the click is exactly
        # the navigation that was wanted.
        failure, re_requested = _re_request(destination_url or (
            before_url if isinstance(before_url, str) else ""))
        if failure is not None:
            failure["reason"] = failure["reason"].replace(
                "destination re-request UNKNOWN",
                "destination re-request UNKNOWN after origin recovery")
            return failure
        return {
            **base,
            "outcome": outcome,
            "reason": reason,
            "destination_re_requested": re_requested,
        }

    failure, re_requested = _re_request(destination_url)
    if failure is not None:
        return failure
    if click_error is not None:
        return {
            **base,
            "outcome": "click_unknown",
            "reason": (
                "control click outcome UNKNOWN: "
                f"{type(click_error).__name__}; origin unchanged"
            ),
            "destination_re_requested": re_requested,
        }
    # Row 912: "cleared" above is proven from the click's own silence (no
    # exception, origin unchanged) -- it is not proof the gate actually left
    # the viewport (an animation delay, or a backdrop the control itself
    # does not own, can leave the page blocked while every check above
    # reports success). Ask the page: is the control, or the fixed/absolute
    # overlay it belongs to, still sitting over the viewport centre? One
    # ``locator.evaluate`` on the SAME already-matched element -- never a
    # scan of the candidates row 371 counts. A control that merely stays in
    # the document (an inline link, a footer button) is not an overlay and
    # does not block. A gate measured still blocking is NOT cleared: callers
    # key off ``outcome``, and a stuck overlay reported as ``cleared`` is the
    # fake verification this row exists to remove. A locator without
    # ``evaluate`` yields ``viewport_cleared: None`` (UNKNOWN) and the
    # click's own evidence stands as before.
    viewport_cleared = _viewport_cleared(locator)
    if viewport_cleared is False:
        return {
            **base,
            "outcome": "viewport_blocked",
            "reason": (f"clicked {tier} gate via {label!r} but the control is "
                       "still visible afterwards: the overlay was not cleared"),
            "destination_re_requested": re_requested,
            "viewport_cleared": False,
        }
    return {
        **base,
        "outcome": "cleared",
        "reason": f"cleared {tier} gate via {label!r}",
        "destination_re_requested": re_requested,
        "viewport_cleared": viewport_cleared,
    }


def _body_text(page: Any) -> Optional[str]:
    """The page's visible body text, or ``None`` when it is unmeasurable.

    ``None`` is UNKNOWN, not "no language": callers must refuse the control
    rather than read an unavailable measurement as permission (A2).
    """
    reader = getattr(page, "inner_text", None)
    if not callable(reader):
        return None
    try:
        text = reader(BODY_TEXT_SELECTOR, timeout=DEFAULT_TIMEOUT_MS)
    except TypeError:
        try:
            text = reader(BODY_TEXT_SELECTOR)
        except Exception:
            return None
    except Exception:
        return None
    return text if isinstance(text, str) else None


def _prechecked_billing_consent(page: Any) -> Tuple[str, str]:
    """Row 762: ``("refuse"|"unknown"|"clear", detail)`` for the page.

    ``refuse`` is recurring-charge language beside a checked checkbox: the
    measured Aylo upsell, where any gate click accepts the charge. ``unknown``
    is charge language whose box state could not be measured -- fail closed,
    money is on the line. ``clear`` is a page with no charge language; the
    box is not even read then, because a checkbox without money behind it
    is not this hazard (the ordinary members wall keeps clearing as before).
    """
    text = _body_text(page)
    if text is None:
        return ("clear", "")
    phrases = [" ".join(found.group(0).split())
               for found in RECURRING_CHARGE_LANGUAGE.finditer(text)]
    if not phrases:
        return ("clear", "")
    # The diagnostic names the money: "$29.97 every 30 days" tells the
    # operator what was refused where "will be charged" alone does not.
    phrase = next((p for p in phrases if any(c.isdigit() for c in p)),
                  phrases[0])
    try:
        checked = page.locator(CHECKED_CONSENT_SELECTOR).count()
    except Exception as exc:
        return ("unknown", f"{phrase!r} on page; checked consent box state "
                           f"unmeasurable: {type(exc).__name__}")
    if not isinstance(checked, int):
        return ("unknown", f"{phrase!r} on page; checked consent box state "
                           "unmeasurable: invalid count")
    if checked > 0:
        return ("refuse", f"{phrase!r} with {checked} prechecked consent box")
    return ("clear", "")


def _age_gate_recognised(page: Any, records: Any) -> Optional[bool]:
    """Whether this page is an age gate. ``None`` means it could not be told.

    Both affordances must be present -- an ENTER with no EXIT beside it is a
    plain continue button, not an age wall -- and the body text must carry
    18-plus language. The affordance census is a complete measurement of the
    visible controls already enumerated for this tier, so its ``False`` is a
    verdict; only an unreadable body text is UNKNOWN.
    """
    surfaces = [surface for record in records
                for surface in record.get("labels", [])]
    has_enter = any(ENTER_AFFORDANCE.fullmatch(surface)
                     or AGREE_AFFORDANCE.fullmatch(surface)
                     for surface in surfaces)
    has_exit = any(_deny_term(surface) is not None for surface in surfaces)
    if not (has_enter and has_exit):
        return False
    text = _body_text(page)
    if text is None:
        return None
    return AGE_GATE_LANGUAGE.search(text) is not None


def _age_gate_landing(page: Any, action: dict) -> dict:
    """Re-judge a cleared age gate by what is BEHIND it.

    The evilangel measurement: "Your IP was blocked!" is only visible once the
    gate is clicked through, so the block cannot be a pre-click refusal. An
    unreadable landing is not a clearance either -- it is reported rather than
    handed on as members content.
    """
    text = _body_text(page)
    if text is None:
        return {
            **action,
            "outcome": "gate_landing_unknown",
            "reason": ("age gate cleared but the landing content is UNKNOWN; "
                       "not reported as members content"),
        }
    found = BLOCK_LANGUAGE.search(text)
    if found is None:
        return action
    return {
        **action,
        "outcome": "blocked_after_gate",
        "reason": ("age gate cleared onto a blocked page (%r); the site is "
                   "blocked, not members content" % found.group(0)),
    }


def first_blocked_after_gate(actions: Any) -> Optional[dict]:
    """Return the first gate action that landed on a block page, if any."""
    if not isinstance(actions, list):
        return None
    return next((action for action in actions
                 if isinstance(action, dict)
                 and action.get("outcome") in BLOCKED_AFTER_GATE_OUTCOMES), None)


def blocked_after_gate_diagnostic(action: dict) -> str:
    """Operator-facing diagnostic for a block page found behind a gate."""
    reason = (action.get("reason", "blocked after gate")
              if isinstance(action, dict) else "blocked after gate")
    return f"Gate cleared onto a block page, not members content: {reason}"


def dismiss_gates(page: Any, raw: Any, *,
                  destination_url: str = "",
                  timeout_ms: int = DEFAULT_TIMEOUT_MS,
                  navigation_timeout_ms: int = DEFAULT_NAVIGATION_TIMEOUT_MS,
                  site_appear_ms: int = DEFAULT_SITE_APPEAR_MS,
                  settle_s: float = DEFAULT_SETTLE_S,
                  sleep=time.sleep) -> List[dict]:
    """Clear one generic control in each human-visible gate tier.

    Per-site selectors in ``raw`` are tried first.  Each returned action is
    structured so callers can report what changed instead of silently
    presenting a different page to the operator.
    """
    # A PRECONDITION, not a measurement. An object with no ``locator`` API is
    # not a page, so it has no gate surface: there is nothing to measure and no
    # verdict to withhold. Every real Playwright page exposes ``locator``, and
    # when CALLING it fails the branches below still answer UNKNOWN -- which is
    # the case this must not hide. Without this, any minimal duck-typed page in
    # an unrelated test turns an absent API into a fail-closed safety verdict.
    if not callable(getattr(page, "locator", None)):
        return []

    actions: List[dict] = []
    attempted_labels = set()
    missing = object()
    # NOT ``selector_lines``: that helper treats any leading ``#`` as a
    # comment, which silently eats every id selector an operator writes
    # (``#onetrust-accept-btn-handler`` is the shipped example). The gate
    # orchestrators share ``_gate_selectors``, where only ``# `` comments.
    site_selectors = _declared_selectors(raw)

    # Row 762: the page is judged immediately before every click of either
    # pass, once per page state -- a click may reveal the upsell behind a
    # cookie wall, so the verdict is dropped after each click. A refusal or
    # an UNKNOWN ends the whole dismissal: every control on a
    # prechecked-charge page is the same purchase.
    billing_verdict = missing

    def _billing_guard(source: str, selector: str, label: str) -> bool:
        """Record the refusal and return True when this click must not happen."""
        nonlocal billing_verdict
        if billing_verdict is missing:
            billing_verdict = _prechecked_billing_consent(page)
        state, detail = billing_verdict
        if state == "refuse":
            actions.append({
                "source": source,
                "tier": "safety",
                "label": label,
                "selector": selector,
                "outcome": "refused",
                "reason": (f"prechecked recurring-charge consent on page: "
                           f"{detail}; control not used"),
                "destination_re_requested": False,
            })
            return True
        if state == "unknown":
            actions.append(_measurement_unknown(
                source, "safety", selector,
                f"billing consent UNKNOWN: {detail}; control not used"))
            return True
        return False

    def _site_pass(*, appear_ms: int) -> bool:
        """Try every declared selector once. False means stop the whole pass."""
        nonlocal billing_verdict
        process_site_selectors = True

        # Real Playwright locators support immediate is_visible(). If no
        # declared control is present at DOMContentLoaded, preserve SPA
        # compatibility with ONE bounded wait for their union -- not one full
        # timeout per selector line, which measured 3.00s each.
        if site_selectors and appear_ms > 0:
            supports_immediate = True
            any_visible = False
            for selector in site_selectors:
                try:
                    preflight = _visible_first(page, selector)
                    is_visible = getattr(preflight, "is_visible", missing)
                    if is_visible is missing:
                        supports_immediate = False
                        break
                    if not callable(is_visible):
                        raise TypeError("is_visible is not callable")
                    if is_visible():
                        any_visible = True
                        break
                except Exception as exc:
                    actions.append(_measurement_unknown(
                        "site", "safety", selector,
                        f"site control visibility UNKNOWN: "
                        f"{type(exc).__name__}"))
                    return False
            if supports_immediate and not any_visible:
                # ``:is(...)`` keeps a comma group's alternatives inside one
                # match, and ``:visible`` is what the wait is actually about.
                aggregate = ":is(%s):visible" % ", ".join(site_selectors)
                try:
                    page.locator(aggregate).first.wait_for(
                        state="attached", timeout=appear_ms)
                except Exception as exc:
                    if type(exc).__name__ == "TimeoutError":
                        process_site_selectors = False
                    else:
                        actions.append(_measurement_unknown(
                            "site", "safety", aggregate,
                            f"site aggregate appearance UNKNOWN: "
                            f"{type(exc).__name__}"))
                        return False

        for selector in site_selectors if process_site_selectors else ():
            try:
                locator = _visible_first(page, selector)
            except Exception as exc:
                actions.append(_measurement_unknown(
                    "site", "safety", selector,
                    f"site control lookup UNKNOWN: {type(exc).__name__}"))
                return False
            is_visible = missing
            try:
                is_visible = getattr(locator, "is_visible", missing)
                if is_visible is missing:
                    # Compatibility path for the deliberately small duck-typed
                    # tests. Real Playwright locators use the immediate branch.
                    locator.wait_for(state="visible", timeout=timeout_ms)
                elif not is_visible():
                    continue
            except Exception as exc:
                if is_visible is missing and type(exc).__name__ == "TimeoutError":
                    # An explicit timeout means the declared control is absent.
                    # Any OTHER failure means the answer was never measured.
                    continue
                actions.append(_measurement_unknown(
                    "site", "safety", selector,
                    f"site control visibility UNKNOWN: {type(exc).__name__}"))
                return False
            try:
                labels = _control_labels(locator)
            except _LabelMeasurementUnavailable as exc:
                actions.append(_measurement_unknown(
                    "site", "safety", selector,
                    f"site control label measurement UNKNOWN: "
                    f"{exc.surface}: {exc.cause_type}"))
                return False
            label = labels[0] if labels else ""
            if not label:
                actions.append({
                    "source": "site",
                    "tier": "safety",
                    "label": "",
                    "selector": selector,
                    "outcome": "label_unknown",
                    "reason": (
                        "control label UNKNOWN; denylist could not be "
                        "evaluated; control not used"
                    ),
                    "destination_re_requested": False,
                })
                return False
            # Two declared lines routinely resolve the SAME live control (an
            # id line and a class line for one button). Clicking it twice is
            # not two gates cleared; it is one gate and one stray click.
            if label.casefold() in attempted_labels:
                continue
            # Recorded on READ, not on click: a refused label must not be
            # re-offered to the generic pass either, which reads the same DOM
            # and would report the same refusal a second time.
            attempted_labels.add(label.casefold())
            denied = next((term for surface in labels
                           if (term := _deny_term(surface))), None)
            if denied:
                actions.append({
                    "source": "site",
                    "tier": "site",
                    "label": label,
                    "selector": selector,
                    "outcome": "refused",
                    "reason": (f"denylisted label matched {denied!r}; "
                               "control not used"),
                    "destination_re_requested": False,
                })
                continue
            if _billing_guard("site", selector, label):
                return False
            action = _click_gate(
                page, locator, source="site", tier="site", label=label,
                selector=selector, destination_url=destination_url,
                timeout_ms=timeout_ms,
                navigation_timeout_ms=navigation_timeout_ms,
                settle_s=settle_s, sleep=sleep)
            billing_verdict = missing
            actions.append(action)
            if action.get("outcome") in SAFETY_UNKNOWN_OUTCOMES:
                return False
        return True

    if not _site_pass(appear_ms=site_appear_ms):
        return actions

    denied_seen = set()
    generic_cleared = False
    for tier, label_pattern in _GENERIC_TIERS:
        try:
            controls = page.locator(GENERIC_CONTROL_SELECTOR)
        except Exception as exc:
            actions.append(_measurement_unknown(
                "generic", tier, GENERIC_CONTROL_SELECTOR,
                f"generic {tier} control lookup UNKNOWN: "
                f"{type(exc).__name__}"))
            return actions

        try:
            evaluate_all = getattr(controls, "evaluate_all", None)
        except Exception as exc:
            actions.append(_measurement_unknown(
                "generic", tier, GENERIC_CONTROL_SELECTOR,
                f"generic {tier} snapshot capability UNKNOWN: "
                f"{type(exc).__name__}"))
            return actions

        records = []
        if callable(evaluate_all):
            try:
                snapshot = evaluate_all(_CONTROL_SNAPSHOT_JS)
            except Exception as exc:
                actions.append(_measurement_unknown(
                    "generic", tier, GENERIC_CONTROL_SELECTOR,
                    f"generic {tier} control enumeration UNKNOWN: "
                    f"{type(exc).__name__}"))
                return actions
            if not isinstance(snapshot, list):
                actions.append(_measurement_unknown(
                    "generic", tier, GENERIC_CONTROL_SELECTOR,
                    f"generic {tier} control enumeration UNKNOWN: "
                    "invalid snapshot"))
                return actions
            for record in snapshot:
                if (not isinstance(record, dict)
                        or not isinstance(record.get("index"), int)
                        or not isinstance(record.get("visible"), bool)
                        or not isinstance(record.get("labels"), list)
                        or any(not isinstance(value, str)
                               for value in record.get("labels", []))):
                    actions.append(_measurement_unknown(
                        "generic", tier, GENERIC_CONTROL_SELECTOR,
                        f"generic {tier} control enumeration UNKNOWN: "
                        "invalid record"))
                    return actions
                if record["visible"]:
                    records.append({
                        "index": record["index"],
                        "labels": [" ".join(value.split())
                                   for value in record["labels"] if value.strip()],
                        "locator": None,
                    })
        else:
            try:
                count = controls.count()
            except Exception as exc:
                actions.append(_measurement_unknown(
                    "generic", tier, GENERIC_CONTROL_SELECTOR,
                    f"generic {tier} control enumeration UNKNOWN: "
                    f"{type(exc).__name__}"))
                return actions
            enumeration_failed = False
            for index in range(count):
                is_visible = missing
                try:
                    locator = controls.nth(index)
                    is_visible = getattr(locator, "is_visible", missing)
                    if is_visible is missing:
                        locator.wait_for(state="visible", timeout=timeout_ms)
                    elif not is_visible():
                        continue
                except Exception as exc:
                    if (is_visible is missing
                            and type(exc).__name__ == "TimeoutError"):
                        continue
                    actions.append(_measurement_unknown(
                        "generic", tier, GENERIC_CONTROL_SELECTOR,
                        f"generic {tier} control visibility UNKNOWN at "
                        f"index {index}: {type(exc).__name__}"))
                    enumeration_failed = True
                    break
                try:
                    labels = _control_labels(locator)
                except _LabelMeasurementUnavailable as exc:
                    actions.append(_measurement_unknown(
                        "generic", tier, GENERIC_CONTROL_SELECTOR,
                        f"generic {tier} control label measurement UNKNOWN at "
                        f"index {index}: {exc.surface}: {exc.cause_type}"))
                    enumeration_failed = True
                    break
                records.append({
                    "index": index,
                    "labels": labels,
                    "locator": locator,
                })
            if enumeration_failed:
                return actions

        # Row 721: judged once per tier, from the controls already enumerated
        # plus the page's own content. ``missing`` means "not yet asked".
        age_gate_verdict = missing

        for record in records:
            labels = record["labels"]
            label = labels[0] if labels else ""
            if label.casefold() in attempted_labels:
                continue
            denied = next((term for surface in labels
                           if (term := _deny_term(surface))), None)
            denied_key = label.casefold()
            if denied:
                if denied_key not in denied_seen:
                    denied_seen.add(denied_key)
                    actions.append({
                        "source": "generic",
                        "tier": "safety",
                        "label": label,
                        "selector": "",
                        "outcome": "refused",
                        "reason": (f"denylisted label matched {denied!r}; "
                                   "control not used"),
                        "destination_re_requested": False,
                    })
                continue
            if not label:
                continue
            admitted_by_age_gate = False
            if not label_pattern.fullmatch(label):
                # A bare "ENTER" is exactly the vague single word the tiers
                # refuse on purpose. It becomes authority ONLY on a page whose
                # content says age gate, which is what stops a genuine abuse
                # response at the same URL from being clicked through.
                if tier == "age" and (ENTER_AFFORDANCE.fullmatch(label)
                                       or AGREE_AFFORDANCE.fullmatch(label)):
                    if age_gate_verdict is missing:
                        age_gate_verdict = _age_gate_recognised(page, records)
                    if age_gate_verdict is None:
                        actions.append(_measurement_unknown(
                            "generic", tier, GENERIC_CONTROL_SELECTOR,
                            "age gate content UNKNOWN: body text unreadable; "
                            "control not used"))
                        return actions
                    if not age_gate_verdict:
                        continue
                    admitted_by_age_gate = True
                elif tier == "interstitial" and ACCESS_AFFORDANCE.fullmatch(label):
                    # Row 722 (nookies.com, 2026-09-15): the post-login gateway
                    # reads "OUR EXCLUSIVE PARTNERS & PREMIUM DEALS" with a
                    # same-origin "ACCESS NOOKIES" beside foreign "JOIN NOW" /
                    # "SAVE BIG NOW" partner upsells. Corroboration here is the
                    # control's OWN destination, never page content: a same-
                    # origin href (or no href at all -- a <button>) is admitted,
                    # a foreign one never is, whatever its label says.
                    try:
                        access_locator = record["locator"]
                        if access_locator is None:
                            access_locator = controls.nth(record["index"])
                        href = access_locator.get_attribute("href")
                    except Exception as exc:
                        actions.append(_measurement_unknown(
                            "generic", tier, GENERIC_CONTROL_SELECTOR,
                            f"access control origin UNKNOWN: "
                            f"{type(exc).__name__}; control not used"))
                        return actions
                    same_origin = _control_same_origin(page, href)
                    if same_origin is None:
                        actions.append(_measurement_unknown(
                            "generic", tier, GENERIC_CONTROL_SELECTOR,
                            "access control origin UNKNOWN: page url "
                            "unreadable; control not used"))
                        return actions
                    if not same_origin:
                        continue
                else:
                    continue
            locator = record["locator"]
            index_resolved = locator is None
            if locator is None:
                try:
                    locator = controls.nth(record["index"])
                except Exception as exc:
                    actions.append(_measurement_unknown(
                        "generic", tier, GENERIC_CONTROL_SELECTOR,
                        f"generic {tier} matched control lookup UNKNOWN: "
                        f"{type(exc).__name__}"))
                    return actions

            # Bind the snapshot verdict to the live control immediately before
            # clicking. Numeric locator indices can drift when a page mutates.
            try:
                live_is_visible = getattr(locator, "is_visible", None)
                if live_is_visible is not None and not live_is_visible():
                    actions.append(_measurement_unknown(
                        "generic", tier, GENERIC_CONTROL_SELECTOR,
                        f"generic {tier} matched control visibility UNKNOWN: "
                        "candidate disappeared"))
                    return actions
            except Exception as exc:
                actions.append(_measurement_unknown(
                    "generic", tier, GENERIC_CONTROL_SELECTOR,
                    f"generic {tier} matched control visibility UNKNOWN: "
                    f"{type(exc).__name__}"))
                return actions
            try:
                live_labels = _control_labels(locator)
            except _LabelMeasurementUnavailable as exc:
                actions.append(_measurement_unknown(
                    "generic", tier, GENERIC_CONTROL_SELECTOR,
                    f"generic {tier} matched control label measurement "
                    f"UNKNOWN: {exc.surface}: {exc.cause_type}"))
                return actions
            if not live_labels:
                actions.append({
                    "source": "generic",
                    "tier": "safety",
                    "label": "",
                    "selector": GENERIC_CONTROL_SELECTOR,
                    "outcome": "label_unknown",
                    "reason": (
                        "control label UNKNOWN; denylist could not be "
                        "evaluated; control not used"
                    ),
                    "destination_re_requested": False,
                })
                return actions
            live_denied = next((term for surface in live_labels
                                if (term := _deny_term(surface))), None)
            if live_denied:
                live_label = live_labels[0]
                live_key = live_label.casefold()
                if live_key not in denied_seen:
                    denied_seen.add(live_key)
                    actions.append({
                        "source": "generic",
                        "tier": "safety",
                        "label": live_label,
                        "selector": "",
                        "outcome": "refused",
                        "reason": (
                            f"denylisted label matched {live_denied!r}; "
                            "control not used"
                        ),
                        "destination_re_requested": False,
                    })
                break
            if live_labels != labels:
                # The snapshot's verdict does not describe the live control.
                # That is UNKNOWN for THIS tier, not permission to stop
                # measuring the tiers stacked behind it -- but a single
                # DOM re-render (Aylo's consent banner) is indistinguishable
                # from real drift on this one read. Re-measure the SAME
                # tier/label a bounded number of times; a control that
                # settles back to the snapshot's own labels is still the
                # control that was measured, so it is used. A control that
                # keeps changing gets the original refusal, verbatim.
                stabilised_labels = None
                if not index_resolved:
                    for attempt in range(2, GENERIC_REMEASURE_ATTEMPTS + 2):
                        sleep(GENERIC_REMEASURE_INTERVAL_S)
                        try:
                            retry_is_visible = getattr(locator, "is_visible", None)
                            if (retry_is_visible is not None
                                    and not retry_is_visible()):
                                break
                            retry_labels = _control_labels(locator)
                        except _LabelMeasurementUnavailable:
                            break
                        except Exception:
                            break
                        if retry_labels == labels:
                            stabilised_labels = retry_labels
                            actions.append({
                                "source": "generic",
                                "tier": tier,
                                "label": label,
                                "selector": "",
                                "outcome": "re_measured",
                                "reason": (
                                    "gate: re-measured %r after a DOM change "
                                    "(attempt %d/%d)"
                                    % (label, attempt,
                                       GENERIC_REMEASURE_ATTEMPTS + 1)
                                ),
                                "destination_re_requested": False,
                            })
                            break
                if stabilised_labels is None:
                    actions.append(_measurement_unknown(
                        "generic", tier, GENERIC_CONTROL_SELECTOR,
                        f"generic {tier} matched control changed after "
                        "snapshot; control not used"))
                    break
                live_labels = stabilised_labels
            attempted_labels.add(label.casefold())
            if _billing_guard("generic", GENERIC_CONTROL_SELECTOR, label):
                return actions
            action = _click_gate(
                page, locator, source="generic", tier=tier, label=label,
                destination_url=destination_url, timeout_ms=timeout_ms,
                navigation_timeout_ms=navigation_timeout_ms,
                settle_s=settle_s, sleep=sleep)
            billing_verdict = missing
            if admitted_by_age_gate and action.get("outcome") == "cleared":
                action = _age_gate_landing(page, action)
            actions.append(action)
            if action.get("outcome") in SAFETY_UNKNOWN_OUTCOMES:
                return actions
            if action.get("outcome") == "cleared":
                generic_cleared = True
            break

    # A generic layer routinely HIDES a measured one -- kink renders its login
    # control only after the consent banner is gone. The declared selectors are
    # measured evidence, so they are re-offered once the generic pass has
    # actually changed the page. Labels already used cannot be clicked twice.
    if generic_cleared and site_selectors:
        _site_pass(appear_ms=0)
    return actions


__all__ = [
    "AGE", "CONSENT", "FORBIDDEN", "INTERSTITIAL", "clear_gates",
    "dismiss", "dismiss_gates", "selector_lines",
    "first_safety_unknown", "safety_unknown_diagnostic",
    "SAFETY_UNKNOWN_OUTCOMES", "DENIED_CONTROL_TERMS",
    "GENERIC_CONTROL_SELECTOR", "DEFAULT_TIMEOUT_MS",
    "AGE_GATE_LANGUAGE", "ENTER_AFFORDANCE", "AGREE_AFFORDANCE",
    "ACCESS_AFFORDANCE", "BLOCK_LANGUAGE",
    "RECURRING_CHARGE_LANGUAGE", "CHECKED_CONSENT_SELECTOR",
    "BLOCKED_AFTER_GATE_OUTCOMES", "first_blocked_after_gate",
    "blocked_after_gate_diagnostic",
    "DEFAULT_NAVIGATION_TIMEOUT_MS", "DEFAULT_SITE_APPEAR_MS",
    "DEFAULT_SETTLE_S",
]
