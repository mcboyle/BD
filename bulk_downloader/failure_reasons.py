"""Operator-facing failure reasons (Cut 4).

The runner already classifies a failure into one of four classes via
``retry_policy.classify_failure`` (transient | rate_limited | auth | permanent).
That class drives the *retry machine*. This module turns the same class into an
*operator* reason — a stable code, a short human title, a suggested next action,
and a clear ``retryable`` flag — so the SPA can render a useful JobErrorModal
instead of a raw stack string.

Design notes:
  * ``category`` is passed straight through from ``classify_failure`` so the
    operator view can never disagree with what the retry machine actually did.
  * ``reason_code`` is a STABLE machine slug (persisted on ``job_runs.reason_code``
    and grouped on in ``/api/runs?status=failed``). It equals the category for
    now — finer codes can be added later without breaking the column.
  * ``retryable`` is True only for the auto-retry classes (transient,
    rate_limited). auth/permanent are deliberately NOT auto-retryable: a wrong
    "retryable" silently loops a request that can never succeed. (The operator can
    still force a manual retry via the existing retry endpoint — ``retryable``
    only describes the *automatic* posture.)
"""
from __future__ import annotations

from . import retry_policy as _rp

# category -> (title, suggested_action). retryable is derived below.
_REASONS = {
    "transient": (
        "Temporary network/CDN error",
        "Usually clears on its own — it will auto-retry. If it persists, check "
        "connectivity to the site.",
    ),
    "rate_limited": (
        "Rate limited by the site",
        "The site is throttling requests — it will back off and auto-retry. "
        "Lower this site's concurrency if it keeps happening.",
    ),
    "auth": (
        "Authentication failed",
        "Re-login or refresh this site's credentials — it will not auto-retry "
        "until the login works.",
    ),
    "permanent": (
        "Permanent error",
        "This URL is gone or blocked (e.g. 404/403) — it will not auto-retry. "
        "Remove or replace it.",
    ),
}

_RETRYABLE = {"transient", "rate_limited"}


def reason_for(message: str = "", status_code=None) -> dict:
    """Map a failure into an operator reason object.

    Returns ``{category, reason_code, title, suggested_action, retryable}``.
    Never raises — the runner calls this on its hot path, so bad input
    (non-string message, non-int status) is coerced rather than fatal.
    """
    category = _rp.classify_failure(message=message, status_code=status_code)
    if category not in _REASONS:
        category = "transient"  # mirror classify_failure's optimistic default
    title, action = _REASONS[category]
    return {
        "category": category,
        "reason_code": category,
        "title": title,
        "suggested_action": action,
        "retryable": category in _RETRYABLE,
    }


# --- 7.4: LLM triage of the uncategorized tail (advisory, never on hot path) ---
#
# `classify_failure` is deliberately optimistic: an unrecognized error and a
# matched "transient" both return "transient", so the genuinely-unclassified
# tail is invisible to `reason_for`. These helpers expose that tail and let a
# locally-hosted model *advise* a bucket for it. They do NOT touch the retry
# machine, NEVER change `reason_for`, and constrain any model output to the
# known reason vocabulary so a free-form category can never leak downstream.


def is_uncategorized(message: str = "", status_code=None) -> bool:
    """True ONLY when a failure would fall through to classify_failure's
    optimistic default (no usable status code, no positive pattern match).

    Mirrors classify_failure's precedence without re-running it: any HTTP
    status in the 4xx/5xx range is a definite signal, as is any message that
    positively matches one of the four pattern lists. Everything else is the
    uncategorized tail. Never raises — bad input coerces to "tail unknown".
    """
    sc = status_code
    if sc is not None and not isinstance(sc, int):
        try:
            sc = int(sc)
        except (TypeError, ValueError):
            sc = None
    # classify_failure resolves every 4xx/5xx to a definite class.
    if sc is not None and 400 <= sc < 600:
        return False
    msg = message.lower() if isinstance(message, str) else ""
    for plist in (_rp._PERMANENT_PATTERNS, _rp._AUTH_PATTERNS,
                  _rp._RATE_PATTERNS, _rp._TRANSIENT_PATTERNS):
        for p in plist:
            if p in msg:
                return False
    return True


def triage_unknown(message: str = "", context: str = "", *, llm=None):
    """Advisory LLM triage for an uncategorized failure.

    `llm` is an injected callable ``(prompt: str) -> dict`` (the production
    wiring is a thin adapter over the local AI-assist model; tests pass a fake).
    Returns ``{reason_code, suggested_action, advisory: True}`` ONLY when the
    model proposes a ``reason_code`` already in `_REASONS`; otherwise None
    (no model, out-of-vocabulary, or any model error). Best-effort and
    side-effect-free: it advises a human, it never changes retry/runtime
    behavior and never invents a new category.
    """
    if llm is None:
        return None
    prompt = (
        "Classify this download failure into exactly one of: "
        + ", ".join(sorted(_REASONS)) + ".\n"
        "Reply as JSON {reason_code, suggested_action}. "
        "Use only one of the listed reason_code values.\n"
        f"message: {message!r}\ncontext: {context!r}"
    )
    try:
        result = llm(prompt)
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    code = str(result.get("reason_code") or "").strip()
    if code not in _REASONS:
        return None
    action = str(result.get("suggested_action") or "").strip() or _REASONS[code][1]
    return {"reason_code": code, "suggested_action": action, "advisory": True}
