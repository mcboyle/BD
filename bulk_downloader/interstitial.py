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

import time
from typing import Any, List, Optional

# The runner has used these two numbers since the loop was written; they are
# named here rather than restated at each call site so the two consumers cannot
# drift apart on them.
DEFAULT_TIMEOUT_MS = 3000
DEFAULT_SETTLE_S = 0.5


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


__all__ = ["dismiss", "selector_lines",
           "DEFAULT_TIMEOUT_MS", "DEFAULT_SETTLE_S"]
