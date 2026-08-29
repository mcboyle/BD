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
from typing import Any, List, Optional
from urllib.parse import urlsplit

from .registrable_domain import registrable_domain, same_site


# The runner has used these two numbers since the loop was written; they are
# named here rather than restated at each call site so the two consumers cannot
# drift apart on them.
DEFAULT_TIMEOUT_MS = 3000
DEFAULT_SETTLE_S = 0.5
DEFAULT_NAVIGATION_TIMEOUT_MS = 30000
DEFAULT_SITE_APPEAR_MS = DEFAULT_TIMEOUT_MS

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
    ("age", re.compile(
        r"^(?:i agree[, ]+(?:enter|continue)(?: here)?|"
        r"i am (?:18|21)(?: or older)?|"
        r"yes[, ]+i(?: am|'m) (?:18|21)(?: or older)?)$", re.I)),
    ("interstitial", re.compile(
        r"^(?:no thanks(?:[., ]+continue(?: to (?:members(?: area)?|the site))?)?|"
        r"continue to (?:members(?: area)?|the site)|"
        r"skip (?:this page|for now))$", re.I)),
)

DENIED_CONTROL_TERMS = (
    "exit", "leave", "disagree", "decline", "reject", "deny",
    "opt-out", "cancel", "under-18",
)

SAFETY_UNKNOWN_OUTCOMES = frozenset({
    "label_unknown",
    "origin_unknown",
    "origin_recovery_unknown",
    "destination_re_request_unknown",
    "click_unknown",
    "measurement_unknown",
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
    return {
        **base,
        "outcome": "cleared",
        "reason": f"cleared {tier} gate via {label!r}",
        "destination_re_requested": re_requested,
    }


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

    def _site_pass(*, appear_ms: int) -> bool:
        """Try every declared selector once. False means stop the whole pass."""
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
            action = _click_gate(
                page, locator, source="site", tier="site", label=label,
                selector=selector, destination_url=destination_url,
                timeout_ms=timeout_ms,
                navigation_timeout_ms=navigation_timeout_ms,
                settle_s=settle_s, sleep=sleep)
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
            if not label or not label_pattern.fullmatch(label):
                continue
            locator = record["locator"]
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
                # measuring the tiers stacked behind it.
                actions.append(_measurement_unknown(
                    "generic", tier, GENERIC_CONTROL_SELECTOR,
                    f"generic {tier} matched control changed after snapshot; "
                    "control not used"))
                break
            attempted_labels.add(label.casefold())
            action = _click_gate(
                page, locator, source="generic", tier=tier, label=label,
                destination_url=destination_url, timeout_ms=timeout_ms,
                navigation_timeout_ms=navigation_timeout_ms,
                settle_s=settle_s, sleep=sleep)
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
    "DEFAULT_NAVIGATION_TIMEOUT_MS", "DEFAULT_SITE_APPEAR_MS",
    "DEFAULT_SETTLE_S",
]
