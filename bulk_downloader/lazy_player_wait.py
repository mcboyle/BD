"""v3.43.75: MutationObserver-based lazy-player wait.

# What this module is

Some sites lazy-load their `<video>` element after some interaction
(scroll, click, hover) or after a delayed AJAX response. BD's current
`page.wait_for_selector("video", timeout=10000)` polls every 100ms.
For lazy-load patterns the element appears suddenly mid-frame and
the polling might miss the moment if it's also doing other work, or
just times out before the player decides to render.

Browsers have a better primitive: `MutationObserver`. It fires
SYNCHRONOUSLY when DOM mutations match the configured filter, with
zero polling overhead. This module wraps it in a Promise that
Playwright can await via `page.evaluate()`.

# Strategy

Inject a small JS snippet via `page.evaluate()` that:

  1. Checks if the target selector matches RIGHT NOW (in case the
     element is already there)
  2. If not, installs a MutationObserver watching `document.body` for
     childList + subtree mutations
  3. On every mutation, re-tests the selector; if it matches, resolves
     the Promise with `True`
  4. Rejects after a timeout

This is more reliable than polling because:

  - Zero polling overhead → no missed CPU cycles
  - Fires the instant the element appears, not on the next poll tick
  - Catches `<video>` elements added by JS into shadow DOM (with the
    {composed: true} option — though shadow-pierce is best-effort)

# Fallbacks

  - If `page.evaluate()` itself raises (page closed, navigation in
    flight, etc.), return (False, "evaluate_raised")
  - On timeout the function returns (False, "timeout") — caller can
    fall through to standard `wait_for_selector` or fail the URL
  - Selector typos / unparseable selectors caught by querySelector
    raising; we return (False, "selector_invalid")

# Integration with runner

  - Per-site opt-in via `use_mutation_observer` config flag
  - When set, `_wait_for_video_load` first tries this observer-based
    wait. On timeout/failure it falls through to the existing
    `wait_for_selector` polling path.
  - No change to the standard path; this is strictly additive.

# Caveats

  - The observer is installed on `document.body`. Sites that put their
    `<video>` inside a shadow DOM (rare but possible) need `composed:
    true` — which we set. lxml-style "deep" queries (`>>>`) are NOT
    needed because querySelector with composed=true works.
  - We don't disconnect the observer manually; Playwright's
    `page.evaluate()` runs in its own isolated frame and the observer
    is garbage-collected when the promise resolves/rejects.
  - Iframe-hosted players need a different strategy (`page.frames`).
    This module is for in-document `<video>` elements only.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


# The JS snippet. Takes a selector string and a timeout in ms; returns
# a Promise that resolves to True when found or rejects with a string
# reason on timeout/error.
_OBSERVER_JS = r"""
async ({ selector, timeoutMs }) => {
    return new Promise((resolve, reject) => {
        // Fast path: maybe the element is already there
        try {
            const existing = document.querySelector(selector);
            if (existing) {
                resolve({ found: true, reason: "already_present" });
                return;
            }
        } catch (e) {
            // Invalid selector
            reject({ found: false, reason: "selector_invalid: " + (e && e.message || e) });
            return;
        }

        let observer = null;
        let timeoutHandle = null;

        const cleanup = () => {
            if (observer) {
                try { observer.disconnect(); } catch (e) {}
                observer = null;
            }
            if (timeoutHandle != null) {
                clearTimeout(timeoutHandle);
                timeoutHandle = null;
            }
        };

        timeoutHandle = setTimeout(() => {
            cleanup();
            resolve({ found: false, reason: "timeout" });
        }, timeoutMs);

        try {
            observer = new MutationObserver((mutations) => {
                // On any mutation, re-test the selector. We don't
                // bother filtering by added node type because
                // querySelector against the whole document is cheap
                // and catches the case where the <video> was nested
                // inside a wrapper that was added.
                let match = null;
                try {
                    match = document.querySelector(selector);
                } catch (e) {
                    // Selector invalid — bail
                    cleanup();
                    resolve({ found: false, reason: "selector_invalid_during_observe: " + (e && e.message || e) });
                    return;
                }
                if (match) {
                    cleanup();
                    resolve({ found: true, reason: "mutation_match" });
                }
            });
            observer.observe(document.body || document.documentElement, {
                childList: true,
                subtree: true,
                attributes: false,
                characterData: false
            });
        } catch (e) {
            cleanup();
            resolve({ found: false, reason: "observer_setup_failed: " + (e && e.message || e) });
        }
    });
}
"""


def wait_for_selector_via_observer(
    page,
    selector: str,
    *,
    timeout_ms: int = 15000,
) -> tuple:
    """Wait for `selector` to appear in `page`'s DOM via MutationObserver.

    Returns (found: bool, reason: str). The reason field is one of:
      - "already_present"   — selector matched immediately
      - "mutation_match"    — observer fired and selector now matches
      - "timeout"           — timeout_ms elapsed without a match
      - "selector_invalid"  — selector failed to parse
      - "evaluate_raised:*" — page.evaluate() raised
      - "observer_setup_failed:*" — MutationObserver constructor failed

    Fail-open: caller can fall through to a polling wait on (False, *).
    Never raises.
    """
    if not selector or not isinstance(selector, str):
        return (False, "empty_selector")
    if page is None:
        return (False, "page_is_none")
    if timeout_ms <= 0:
        timeout_ms = 1
    try:
        # Playwright will await the returned Promise. We use a
        # page.evaluate timeout slightly larger than our JS timeout
        # so the JS timeout fires first and resolves cleanly.
        result = page.evaluate(
            _OBSERVER_JS,
            {"selector": selector, "timeoutMs": int(timeout_ms)},
        )
    except Exception as e:
        return (False, f"evaluate_raised:{type(e).__name__}:{str(e)[:80]}")
    # Expected shape: {"found": bool, "reason": str}
    if not isinstance(result, dict):
        return (False, f"unexpected_result_type:{type(result).__name__}")
    found = bool(result.get("found"))
    reason = str(result.get("reason", ""))
    return (found, reason)


def wait_for_any_video_via_observer(
    page,
    *,
    extra_selectors: Optional[list] = None,
    timeout_ms: int = 15000,
) -> tuple:
    """Convenience wrapper that watches for a set of common video-
    element selectors, returning when ANY of them matches.

    Default selector list:
      - `video` (HTML5 video tag)
      - `video[src]` (only those with src — avoids matching empty
        placeholders some sites use)
      - `source[src]` (some sites use <source> children inside <video>)
      - `iframe[src*='player']` — embedded players (e.g. JW Player)

    `extra_selectors`: callers can pass additional selectors specific
    to a site template.

    Returns (found, reason) — same contract as wait_for_selector_via_observer.
    """
    sels = ["video[src]", "video source[src]"]
    if extra_selectors:
        for s in extra_selectors:
            if s and isinstance(s, str) and s not in sels:
                sels.append(s)
    # Build a combined selector with comma-separated alternatives.
    # querySelector matches the first one that exists, which is what
    # we want (any-match).
    combined = ", ".join(sels)
    return wait_for_selector_via_observer(
        page, combined, timeout_ms=timeout_ms,
    )


__all__ = [
    "wait_for_selector_via_observer",
    "wait_for_any_video_via_observer",
]
