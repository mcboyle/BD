"""Selector chains — P5-1 (v3.66.32).

Structured fallback-chain support for learned selectors. Historically
`learned.{login,download}.*` held bare ``list[str]`` selector lists,
consumed by simple iterate-and-fill / iterate-and-click loops in
``login.py`` and ``runner.py`` that advanced only on "locator missing
or threw". P5-1 adds a richer per-step shape (timeout, post-condition,
advance-on rules) WITHOUT changing the persisted JSON for the common
case.

Design contract (the load-bearing constraints):

  * **Backward compatibility is absolute.** A plain string in the
    persisted JSON parses to a ``SelectorStep`` with defaults, and a
    default ``SelectorStep`` serializes back to the plain string. So a
    site_config that only ever held ``["#id", ".cls"]`` round-trips to
    exactly ``["#id", ".cls"]`` — no schema churn on the vast majority
    of configs. Only steps that actually USE the richer fields persist
    as dicts.

  * **The promote/demote machinery (runner ``_bump_per_selector`` /
    ``_maybe_demote_selectors``) keeps operating on the persisted
    list.** Because default steps persist as strings, the existing
    "remove this string, insert at front" promotion logic is untouched
    for the common case. For dict-form steps, helpers here expose the
    selector string so promotion can match on it. A4 (promote/demote
    behavior unchanged) falls out of keeping the persisted list as the
    source of truth.

  * **The richness lives at consumption, not in the list.** A consumer
    calls :func:`parse_chain` to get ``list[SelectorStep]``, then
    dispatches each through :func:`try_step`. The list it persists back
    is still the same shape it read.

Post-conditions (what "this step worked" means):

  * ``locator_exists``    — default. The locator matched ≥1 element.
                            (The legacy behavior: count() > 0.)
  * ``input_held``        — after filling, the field still holds the
                            value (catches anti-bot JS that clears the
                            field immediately).
  * ``navigation_occurred`` — a navigation / URL change happened within
                            the step timeout (catches decoy submit
                            buttons that do nothing).
  * ``text_appeared:<pat>`` — the given text/regex appeared on the page
                            within the timeout (catches "logged in"
                            confirmation, error banners, etc.).

advance_on / abort:

  Each step lists the failure modes it will ADVANCE past (try the next
  step). A failure mode NOT in ``advance_on`` triggers the step's
  ``on_unlisted_failure`` policy, which is per-step configurable
  (operator decision, this session):

    * ``advance`` — fall through to the next step (legacy-ish; lenient).
    * ``abort``   — stop the whole chain and report. Use when blindly
                    continuing would be unsafe (e.g. a submit step that
                    REQUIRED navigation but got none — trying the next
                    submit selector might submit the form blind).

  Default ``on_unlisted_failure`` is ``advance`` for field-fill steps
  (matches today's lenient behavior) and ``abort`` for steps whose
  post_condition is ``navigation_occurred`` (the genuinely-unsafe case).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ── Failure modes a step can encounter ──────────────────────────────
# These are the canonical strings used in advance_on lists and in the
# (outcome, failure_mode) returned by try_step.
FM_NOT_FOUND = "not_found"          # locator matched nothing
FM_THREW = "threw"                  # an exception during the action
FM_INPUT_CLEARED = "input_cleared"  # filled, but value didn't stick
FM_NO_NAV = "no_navigation"         # clicked, but no navigation/URL change
FM_NO_TEXT = "no_text"              # expected text never appeared

ALL_FAILURE_MODES = frozenset({
    FM_NOT_FOUND, FM_THREW, FM_INPUT_CLEARED, FM_NO_NAV, FM_NO_TEXT,
})

# Post-condition kinds.
PC_LOCATOR_EXISTS = "locator_exists"
PC_INPUT_HELD = "input_held"
PC_NAV_OCCURRED = "navigation_occurred"
PC_TEXT_PREFIX = "text_appeared:"   # followed by a pattern

# on_unlisted_failure policies.
POL_ADVANCE = "advance"
POL_ABORT = "abort"

_DEFAULT_TIMEOUT_MS = 3000

# The legacy advance-on set: today's loops advance on "missing or threw".
# A bare-string step inherits exactly this, so behavior is unchanged.
_LEGACY_ADVANCE_ON = (FM_NOT_FOUND, FM_THREW)


@dataclass
class SelectorStep:
    """One step in a fallback chain.

    A bare string selector parses to ``SelectorStep(selector=s)`` with
    everything else defaulted, and such a step serializes back to the
    bare string ``s`` (see :meth:`to_json`). Only a step that sets a
    non-default field serializes as a dict.
    """
    selector: str
    timeout_ms: int = _DEFAULT_TIMEOUT_MS
    post_condition: str = PC_LOCATOR_EXISTS
    advance_on: tuple = _LEGACY_ADVANCE_ON
    # Per-step policy when a failure mode is NOT in advance_on. None =
    # derive a sensible default from post_condition (see _default_policy).
    on_unlisted_failure: Optional[str] = None

    def __post_init__(self):
        # Normalize advance_on to a tuple of known modes (drop unknowns
        # quietly — forward-compat with future failure modes that an
        # older reader doesn't recognize).
        if isinstance(self.advance_on, (list, tuple)):
            self.advance_on = tuple(
                m for m in self.advance_on if m in ALL_FAILURE_MODES)
        else:
            self.advance_on = _LEGACY_ADVANCE_ON

    @property
    def effective_policy(self) -> str:
        if self.on_unlisted_failure in (POL_ADVANCE, POL_ABORT):
            return self.on_unlisted_failure
        return _default_policy(self.post_condition)

    def is_default_shape(self) -> bool:
        """True iff this step carries only a selector at defaults — i.e.
        it can serialize back to a bare string with no information loss.
        """
        return (
            self.timeout_ms == _DEFAULT_TIMEOUT_MS
            and self.post_condition == PC_LOCATOR_EXISTS
            and tuple(self.advance_on) == _LEGACY_ADVANCE_ON
            and self.on_unlisted_failure is None
        )

    def to_json(self):
        """Serialize. Bare string when default-shaped (round-trip
        guarantee), else a dict carrying only the non-default fields."""
        if self.is_default_shape():
            return self.selector
        d = {"selector": self.selector}
        if self.timeout_ms != _DEFAULT_TIMEOUT_MS:
            d["timeout_ms"] = self.timeout_ms
        if self.post_condition != PC_LOCATOR_EXISTS:
            d["post_condition"] = self.post_condition
        if tuple(self.advance_on) != _LEGACY_ADVANCE_ON:
            d["advance_on"] = list(self.advance_on)
        if self.on_unlisted_failure is not None:
            d["on_unlisted_failure"] = self.on_unlisted_failure
        return d


def _default_policy(post_condition: str) -> str:
    """Steps that REQUIRE navigation default to abort-on-unlisted —
    blindly trying the next submit selector could submit the form blind.
    Everything else defaults to advance (matches legacy leniency)."""
    if post_condition == PC_NAV_OCCURRED:
        return POL_ABORT
    return POL_ADVANCE


def parse_step(raw) -> Optional[SelectorStep]:
    """Parse one persisted entry (str or dict) into a SelectorStep.
    Returns None for an unusable entry (e.g. empty/blank selector,
    non-str/dict) so callers can filter."""
    if isinstance(raw, str):
        s = raw.strip()
        return SelectorStep(selector=s) if s else None
    if isinstance(raw, dict):
        sel = (raw.get("selector") or "").strip()
        if not sel:
            return None
        kwargs = {"selector": sel}
        if "timeout_ms" in raw:
            try:
                kwargs["timeout_ms"] = int(raw["timeout_ms"])
            except (TypeError, ValueError):
                pass
        if raw.get("post_condition"):
            kwargs["post_condition"] = str(raw["post_condition"])
        if "advance_on" in raw and isinstance(raw["advance_on"], (list, tuple)):
            kwargs["advance_on"] = tuple(raw["advance_on"])
        if raw.get("on_unlisted_failure") in (POL_ADVANCE, POL_ABORT):
            kwargs["on_unlisted_failure"] = raw["on_unlisted_failure"]
        return SelectorStep(**kwargs)
    return None


def parse_chain(raw_list) -> list:
    """Parse a persisted selector list (the legacy list[str], or a mixed
    list[str|dict]) into list[SelectorStep], dropping unusable entries."""
    out = []
    for raw in (raw_list or []):
        step = parse_step(raw)
        if step is not None:
            out.append(step)
    return out


def chain_to_json(steps) -> list:
    """Serialize list[SelectorStep] back to the persisted shape. Default
    steps become bare strings (round-trip), so a chain that was all
    plain strings persists identically."""
    return [s.to_json() for s in steps]


def selectors_of(steps) -> list:
    """Just the selector strings, in order — for code paths (and the
    promote/demote machinery) that operate on the bare list."""
    return [s.selector for s in steps]


# ── try_step: the single dispatcher A2 calls for every step ─────────
# Returns (outcome, detail) where outcome is one of:
#   "ok"      — the step succeeded (post-condition satisfied)
#   "advance" — the step failed in an advance_on mode; try next step
#   "abort"   — the step failed in a NON-advance_on mode and policy is
#               abort; stop the chain
#
# `page` is a Playwright Page. `action` is one of "fill" | "click" |
# "exists". `value` is the fill text (for action="fill").
#
# This function is intentionally defensive about the Playwright surface:
# the unit tests drive it with a fake page, and the real page exposes a
# superset. Anything unexpected is treated as FM_THREW.

def try_step(step: SelectorStep, page, action: str, *, value=None,
             nav_probe=None):
    """Execute one step and classify the outcome.

    nav_probe: optional zero-arg callable returning True if a navigation
    occurred since it was armed; supplied by the caller for
    navigation_occurred post-conditions (the caller knows how to arm a
    Playwright 'framenavigated' / url-change watch). When None and the
    post_condition needs it, we fall back to a best-effort url compare.
    """
    sel = step.selector
    try:
        loc = page.locator(sel).first
        count = loc.count()
    except Exception as e:
        return _classify_failure(step, FM_THREW, f"locator error: {e}")

    if not count or count <= 0:
        return _classify_failure(step, FM_NOT_FOUND, "locator matched nothing")

    # The locator exists. Perform the action.
    url_before = _safe_url(page)
    try:
        if action == "fill":
            loc.fill(value if value is not None else "",
                     timeout=step.timeout_ms)
        elif action == "click":
            loc.click(timeout=step.timeout_ms)
        elif action == "exists":
            pass  # existence already proven by count
        else:
            return _classify_failure(step, FM_THREW, f"unknown action {action}")
    except Exception as e:
        return _classify_failure(step, FM_THREW, f"{action} error: {e}")

    # Evaluate the post-condition.
    pc = step.post_condition
    if pc == PC_LOCATOR_EXISTS:
        return ("ok", "locator_exists")

    if pc == PC_INPUT_HELD:
        try:
            held = loc.input_value(timeout=step.timeout_ms)
        except Exception as e:
            return _classify_failure(step, FM_THREW, f"input_value error: {e}")
        if value is not None and held != value:
            return _classify_failure(
                step, FM_INPUT_CLEARED,
                f"field did not hold value (got {held!r})")
        return ("ok", "input_held")

    if pc == PC_NAV_OCCURRED:
        navigated = False
        if nav_probe is not None:
            try:
                navigated = bool(nav_probe())
            except Exception:
                navigated = False
        else:
            navigated = _safe_url(page) != url_before
        if not navigated:
            return _classify_failure(
                step, FM_NO_NAV, "no navigation after action")
        return ("ok", "navigation_occurred")

    if pc.startswith(PC_TEXT_PREFIX):
        pattern = pc[len(PC_TEXT_PREFIX):]
        if _text_present(page, pattern, step.timeout_ms):
            return ("ok", f"text_appeared:{pattern}")
        return _classify_failure(
            step, FM_NO_TEXT, f"text {pattern!r} did not appear")

    # Unknown post-condition: treat as locator_exists (forward-compat).
    return ("ok", f"unknown_pc:{pc}")


def _classify_failure(step: SelectorStep, failure_mode: str, detail: str):
    """Map a failure mode to ('advance'|'abort', detail) per the step's
    advance_on list and on_unlisted_failure policy."""
    if failure_mode in step.advance_on:
        return ("advance", f"{failure_mode}: {detail}")
    # Not in advance_on → policy decides.
    if step.effective_policy == POL_ABORT:
        return ("abort", f"{failure_mode} (unlisted, abort): {detail}")
    return ("advance", f"{failure_mode} (unlisted, advance): {detail}")


def _safe_url(page):
    try:
        return page.url
    except Exception:
        return None


def _text_present(page, pattern, timeout_ms) -> bool:
    try:
        content = page.content()
    except Exception:
        return False
    if not content:
        return False
    try:
        return re.search(pattern, content) is not None
    except re.error:
        return pattern in content
