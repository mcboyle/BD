"""Interstitial dismissal: one loop, and a declaration of when it fires.

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

Import-light and pure: ``time`` only, no Flask, no I/O, no module-level work.
``page`` is duck-typed (anything with ``.locator(sel).first`` answering
``wait_for``/``click``), so the tests drive it without a browser.
"""
from __future__ import annotations

import re
import time
from typing import Any, List, Optional
from urllib.parse import urlsplit

# The runner has used these two numbers since the loop was written; they are
# named here rather than restated at each call site so the two consumers cannot
# drift apart on them.
DEFAULT_TIMEOUT_MS = 3000
DEFAULT_SETTLE_S = 0.5

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


def _origin(url: Any) -> str:
    """Return a comparable HTTP(S) origin, or an empty value if unprovable."""
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

    expected_origin = _origin(url or getattr(page, "url", ""))
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
            current_origin = _origin(after_url)
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
                if (url and _origin(getattr(page, "url", ""))
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


__all__ = [
    "AGE", "CONSENT", "FORBIDDEN", "INTERSTITIAL", "clear_gates",
    "dismiss", "selector_lines", "DEFAULT_TIMEOUT_MS", "DEFAULT_SETTLE_S",
]
