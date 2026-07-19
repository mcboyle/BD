"""Macro replay engine (Phase 186 — second half).

Companion to `macro_recorder.py` (the storage layer). This module
executes a stored action sequence against a Playwright Page.

Two entry points:

  1. `replay_on_page(page, actions, *, default_timeout_ms=10000)`
     — replays into a page the caller already has. Used by the runner
       when `pre_download_macro` is configured (no nested context).

  2. `replay_standalone(site_id, name, url, *, headless=True)`
     — for operator-initiated tests via the /api. Opens a fresh
       Playwright context. RESPECTS INV-001: caller must pause site
       keepers before invoking — this module won't pause them itself
       to keep the responsibility chain clear.

Action kinds (validated by macro_recorder._validate_actions):

  click     {selector, timeout_ms?, button?}
  wait      {for, timeout_ms?}            — wait for selector to appear
  scroll    {to: 'bottom'|'top'} OR {y: int}
  type      {selector, text, delay_ms?}
  sleep     {ms}
  press     {key, selector?}              — keyboard press (page-wide
                                            if no selector)

Failure modes (replay returns ok=False with details):

  • action_timeout         — selector / wait_for never matched
  • action_invalid         — malformed action dict
  • page_error             — browser raised PlaywrightError
  • macro_not_found        — name doesn't exist in storage
  • playwright_missing     — sync_playwright import failed

Every failure path captures the action index so the operator knows
which step broke. Result is also persisted back to the macro's
metadata via `mark_replay_result`.

This module is intentionally narrow: it does NOT do recording (that
needs a different surface — the operator drives a browser, we
listen). Recording is a separate later phase.
"""
from __future__ import annotations

import time
from typing import Any, Optional


# Per-action default timeouts (ms). Operators can override per-action.
DEFAULT_TIMEOUTS = {
    "click": 10000,
    "wait": 15000,
    "type": 5000,
    "press": 5000,
    "await_url": 15000,
}

# Cap on total replay duration. Past this, we abort with a clear
# error rather than letting a misbehaving macro stall a worker.
MAX_REPLAY_SECONDS = 180.0


class ReplayError(Exception):
    """Raised when a single action fails. The replay loop catches
    this and converts to a structured result; tests use this directly."""

    def __init__(self, message: str, *, action_idx: int = -1,
                 kind: str = "?", action: Optional[dict] = None):
        super().__init__(message)
        self.action_idx = action_idx
        self.kind = kind
        self.action = action or {}


def _execute_click(page, action: dict, timeout_ms: int):
    """click {selector, button?, timeout_ms?}."""
    sel = (action.get("selector") or "").strip()
    if not sel:
        raise ReplayError("click missing selector", action=action)
    button = (action.get("button") or "left").lower()
    if button not in ("left", "right", "middle"):
        button = "left"
    page.click(sel, timeout=timeout_ms, button=button)


def _execute_wait(page, action: dict, timeout_ms: int):
    """wait {for: '<selector>', timeout_ms?}.

    The 'for' field is a CSS/XPath selector to wait for. Any element
    matching is considered satisfied; we don't try to gate on visible
    vs. attached — most macros want "exists in DOM"."""
    sel = (action.get("for") or action.get("selector") or "").strip()
    if not sel:
        raise ReplayError("wait missing 'for' selector", action=action)
    page.wait_for_selector(sel, timeout=timeout_ms,
                            state=action.get("state") or "attached")


def _execute_scroll(page, action: dict, timeout_ms: int):
    """scroll {to: 'bottom'|'top'} OR {y: int}."""
    to = (action.get("to") or "").lower()
    if to == "bottom":
        page.evaluate(
            "() => window.scrollTo(0, document.body.scrollHeight)")
    elif to == "top":
        page.evaluate("() => window.scrollTo(0, 0)")
    elif "y" in action:
        try:
            y = int(action["y"])
        except (TypeError, ValueError):
            raise ReplayError("scroll y must be int", action=action)
        page.evaluate(f"() => window.scrollTo(0, {y})")
    elif "selector" in action:
        # scroll to bring an element into view
        sel = action["selector"]
        page.locator(sel).first.scroll_into_view_if_needed(
            timeout=timeout_ms)
    else:
        raise ReplayError(
            "scroll needs 'to', 'y', or 'selector'", action=action)


def _execute_type(page, action: dict, timeout_ms: int):
    """type {selector, text, delay_ms?}.

    Note: text='(set in vault)' (macro_recorder.VAULT_MARKER) is a
    password marker. Substitution happens upstream in replay_on_page
    (which has the site_id), so by the time this runs `text` is already
    the resolved value — this just types whatever string it's given."""
    sel = (action.get("selector") or "").strip()
    text = action.get("text")
    if not sel:
        raise ReplayError("type missing selector", action=action)
    if text is None:
        raise ReplayError("type missing text", action=action)
    delay = int(action.get("delay_ms", 0) or 0)
    # fill() is faster than type() but type() simulates per-key
    # input events that some sites watch for. Prefer type() when
    # delay_ms is set (operator clearly wants the per-key behavior);
    # else fill() for speed.
    if delay > 0:
        page.type(sel, str(text), delay=delay, timeout=timeout_ms)
    else:
        page.fill(sel, str(text), timeout=timeout_ms)


def _execute_sleep(page, action: dict, timeout_ms: int):
    """sleep {ms}."""
    try:
        ms = int(action.get("ms", 0))
    except (TypeError, ValueError):
        raise ReplayError("sleep ms must be int", action=action)
    if ms <= 0:
        return
    # Cap individual sleeps at 30s — a longer sleep is almost always
    # an operator typo, and we don't want one bad macro to consume
    # the whole MAX_REPLAY_SECONDS budget on a single sleep.
    if ms > 30000:
        raise ReplayError(
            f"sleep ms={ms} exceeds 30s cap (likely a typo)",
            action=action)
    page.wait_for_timeout(ms)


def _execute_press(page, action: dict, timeout_ms: int):
    """press {key, selector?}. If selector given, focus it first."""
    key = (action.get("key") or "").strip()
    if not key:
        raise ReplayError("press missing key", action=action)
    sel = action.get("selector") or ""
    if sel:
        page.locator(sel).first.press(key, timeout=timeout_ms)
    else:
        page.keyboard.press(key)


def _execute_await_url(page, action: dict, timeout_ms: int):
    """await_url {url}. Wait for the page URL to match a glob/substring before
    the next step — the cross-origin hop in an N-step login flow (v3.66.302).
    The url is a structural origin/path pattern, never a credential."""
    url = (action.get("url") or "").strip()
    if not url:
        raise ReplayError("await_url missing url", action=action)
    try:
        page.wait_for_url(url, timeout=timeout_ms,
                          wait_until=action.get("wait_until", "load"))
    except TypeError:
        page.wait_for_url(url, timeout=timeout_ms)


# Dispatch table — explicit map is safer than getattr() against the
# module since malformed `kind` values would expose other helpers.
_EXECUTORS = {
    "click": _execute_click,
    "wait": _execute_wait,
    "scroll": _execute_scroll,
    "type": _execute_type,
    "sleep": _execute_sleep,
    "press": _execute_press,
    "await_url": _execute_await_url,
}


def replay_on_page(page, actions: list, *,
                   default_timeout_ms: int = 10000,
                   site_id: Optional[str] = None,
                   on_progress=None) -> dict:
    """Execute `actions` against an already-open Playwright page.

    Args:
      page: a playwright.sync_api.Page
      actions: list of action dicts (see module docstring)
      default_timeout_ms: per-action timeout if action doesn't set one
      site_id: when set, NEW-1 password markers in 'type' actions are
               resolved from the vault for this site at execution time
               (the macro itself never stores the plaintext)
      on_progress: optional callable(idx, action) called before each
                   action — useful for UI progress bars

    Returns: {
      ok: bool,
      action_count: int,
      completed: int,         # number of actions that ran successfully
      failed_idx: Optional[int],  # 0-based index of failed action
      failed_kind: Optional[str],
      error: Optional[str],
      duration_seconds: float,
    }
    """
    start = time.time()
    if not isinstance(actions, list):
        return {"ok": False, "action_count": 0, "completed": 0,
                "failed_idx": -1, "failed_kind": None,
                "error": "actions must be a list",
                "duration_seconds": 0.0}
    if not actions:
        return {"ok": True, "action_count": 0, "completed": 0,
                "failed_idx": None, "failed_kind": None,
                "error": None, "duration_seconds": 0.0}
    completed = 0
    for idx, action in enumerate(actions):
        # Global deadline check
        if (time.time() - start) > MAX_REPLAY_SECONDS:
            return _failure(idx, action,
                f"exceeded {MAX_REPLAY_SECONDS}s total replay budget",
                start, completed, len(actions))
        if not isinstance(action, dict):
            return _failure(idx, action, "action is not a dict",
                            start, completed, len(actions))
        kind = action.get("kind")
        executor = _EXECUTORS.get(kind)
        if executor is None:
            return _failure(idx, action,
                f"unknown action kind: {kind!r}",
                start, completed, len(actions))
        # NEW-1: resolve a password marker to the real vault value on a
        # copy, at the last moment — the stored macro never holds it.
        try:
            from . import macro_recorder as _mr
            action = _mr._substitute_secret(action, site_id)
        except ValueError as e:
            return _failure(idx, action, str(e),
                            start, completed, len(actions))
        timeout_ms = int(action.get(
            "timeout_ms",
            DEFAULT_TIMEOUTS.get(kind, default_timeout_ms)))
        if callable(on_progress):
            try:
                on_progress(idx, action)
            except Exception:
                pass  # progress callback must never break replay
        try:
            executor(page, action, timeout_ms)
        except ReplayError as e:
            return _failure(idx, action, str(e),
                            start, completed, len(actions))
        except Exception as e:
            # Wrap any underlying Playwright/runtime error
            return _failure(idx, action,
                f"{type(e).__name__}: {str(e)[:200]}",
                start, completed, len(actions))
        completed += 1
    return {"ok": True, "action_count": len(actions),
            "completed": completed, "failed_idx": None,
            "failed_kind": None, "error": None,
            "duration_seconds": round(time.time() - start, 3)}


def _failure(idx: int, action, message: str,
            start: float, completed: int, total: int) -> dict:
    # `action` may be any type (including a string) when the caller is
    # complaining "this isn't a dict" — guard accordingly.
    kind = None
    if isinstance(action, dict):
        kind = action.get("kind")
    return {
        "ok": False,
        "action_count": total,
        "completed": completed,
        "failed_idx": idx,
        "failed_kind": kind,
        "error": message,
        "duration_seconds": round(time.time() - start, 3),
    }


def replay_standalone(site_id: str, name: str, *,
                     start_url: Optional[str] = None,
                     headless: bool = True,
                     persist_result: bool = True) -> dict:
    """Top-level replay: open a fresh Playwright context, navigate to
    `start_url` (if given), then replay the stored macro.

    Used by `/api/macros/replay/<sid>/<name>`. Callers MUST ensure
    no other Playwright session is active for the site (see INV-001
    in the project README) — for site-scoped flows the caller pauses
    the relevant SiteRunner's keepers before invoking us.

    Returns the same shape as `replay_on_page` plus:
      • macro_name, site_id (for caller's audit log)
      • start_url
    """
    from . import macro_recorder as _mr
    bundle = _mr.get_macro(site_id, name)
    if bundle is None:
        out = {"ok": False, "error": "macro not found",
               "site_id": site_id, "macro_name": name,
               "action_count": 0, "completed": 0,
               "failed_idx": -1, "failed_kind": None,
               "duration_seconds": 0.0}
        return out

    try:
        from . import cloak as _cloak
    except ImportError as e:
        out = {"ok": False,
               "error": f"browser backend not installed: {e}",
               "site_id": site_id, "macro_name": name,
               "action_count": len(bundle.get("actions") or []),
               "completed": 0, "failed_idx": -1, "failed_kind": None,
               "duration_seconds": 0.0}
        return out

    actions = bundle.get("actions") or []
    overall_start = time.time()
    try:
        # Canonical backend (CloakBrowser when resolved, else vanilla
        # Playwright) so a replayed login/nav macro hits the site through the
        # SAME stealth path as a real capture. cloaked_page owns teardown.
        with _cloak.cloaked_page(headless=headless) as page:
            if start_url:
                try:
                    page.goto(start_url,
                              wait_until="domcontentloaded",
                              timeout=30000)
                except Exception as e:
                    out = {"ok": False,
                           "error": f"navigate failed: {e}",
                           "site_id": site_id, "macro_name": name,
                           "start_url": start_url,
                           "action_count": len(actions), "completed": 0,
                           "failed_idx": -1, "failed_kind": None,
                           "duration_seconds": round(
                               time.time() - overall_start, 3)}
                    if persist_result:
                        _mr.mark_replay_result(site_id, name,
                                                False, out["error"])
                    return out
            result = replay_on_page(page, actions, site_id=site_id)
    except Exception as e:
        out = {"ok": False,
               "error": f"replay harness error: {e}",
               "site_id": site_id, "macro_name": name,
               "start_url": start_url,
               "action_count": len(actions), "completed": 0,
               "failed_idx": -1, "failed_kind": None,
               "duration_seconds": round(
                   time.time() - overall_start, 3)}
        if persist_result:
            _mr.mark_replay_result(site_id, name, False, out["error"])
        return out

    # Tag result with macro identity + caller-useful fields
    result["site_id"] = site_id
    result["macro_name"] = name
    result["start_url"] = start_url
    if persist_result:
        msg = result.get("error") or f"completed {result['completed']}/{result['action_count']}"
        _mr.mark_replay_result(site_id, name,
                                bool(result.get("ok")),
                                msg)
    return result


def is_playwright_available() -> bool:
    """Cheap probe for callers that want to surface "replay disabled"
    in the UI without invoking a full replay."""
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def status_dict() -> dict:
    """Health summary for diagnostics + UI."""
    return {
        "playwright_available": is_playwright_available(),
        "max_replay_seconds": MAX_REPLAY_SECONDS,
        "supported_kinds": sorted(_EXECUTORS.keys()),
        "default_timeouts_ms": dict(DEFAULT_TIMEOUTS),
    }
