"""Phase 9.13 -- challenge-type classification (detection only).

Classify a *detected* challenge page so the caller can route it to the owning
safe flow. Detection only: there is NO bypass/evasion content anywhere in the
output. Deterministic keyword detection first; an optional advisory LLM
refinement may summarize the observation. Output is advisory and contains only
a type tag, a neutral summary, and a review path.
"""

import re
from typing import Any, Dict, Optional

CHALLENGE_TYPES = (
    "turnstile", "hcaptcha", "recaptcha", "rate-limit", "consent",
    "interstitial", "login-wall", "custom", "unknown",
)

# words that must never appear in our structured output (no bypass guidance)
_FORBIDDEN_OUT = ("bypass", "solve", "evade", "defeat", "auto-submit", "token-harvest")

_SIGNATURES = [
    ("turnstile", re.compile(r"cf-turnstile|turnstile|challenges\.cloudflare", re.I)),
    ("hcaptcha", re.compile(r"hcaptcha|h-captcha", re.I)),
    ("recaptcha", re.compile(r"recaptcha|g-recaptcha|gstatic.*recaptcha", re.I)),
    # Strong page-gate signals precede the deliberately broad login-wall
    # vocabulary. A rate-limit or upsell page commonly carries a site-wide
    # "Sign in" header; that incidental text must not steal the classification.
    ("rate-limit", re.compile(
        r"\b(?:http(?: status)?[=: ]*)?429\b|\btoo many requests\b|"
        r"\bretry-after\b|\brate[- ]limit(?:ed|ing)?\b|"
        r"\b(?:request )?quota exceeded\b|\bthrottled\b",
        re.I,
    )),
    ("consent", re.compile(
        r"\bcookie consent\b|\b(?:accept|allow) all(?: cookies)?\b|"
        r"\bagree and continue\b|onetrust-accept-btn-handler",
        re.I,
    )),
    ("interstitial", re.compile(
        r"/interstitial(?:[/?#\s'\"]|$)|"
        r"\bno thanks[.,]?\s+continue to members(?: area)?\b|"
        r"\bcontinue to members area\b|\bskip this page\b",
        re.I,
    )),
    ("login-wall", re.compile(r"\b(sign in|log ?in|password|sign-in|authenticate)\b", re.I)),
]

_REVIEW_PATHS = {
    "rate-limit": (
        "Route to the existing rate-limit cooldown and backoff flow; do not "
        "retry the request or open a challenge handoff."
    ),
    "consent": (
        "Route to the origin-safe gate dismissal flow. Permit only a declared "
        "or exact allowlisted consent control, and refuse exit or decline controls."
    ),
    "interstitial": (
        "Route to the origin-safe interstitial dismissal flow. After a safe "
        "control clears it, re-request the exact original destination."
    ),
}

_MANUAL_REVIEW_PATH = (
    "Route to the manual operator handoff flow for an authenticated human to "
    "complete the challenge in the noVNC session."
)


def _detect(text: str) -> str:
    for name, rx in _SIGNATURES:
        if rx.search(text or ""):
            return name
    return "unknown"


def classify(observation: Dict[str, Any], *, model: Optional[str] = None,
             _call=None) -> Dict[str, Any]:
    """Classify a detected challenge. `observation` may carry `text`, `title`,
    `markers`, and `url`. Returns {type, observation_summary,
    suggested_review_path, advisory}. Detection only -- never bypass
    instructions."""
    blob = " ".join(
        str(observation.get(key, ""))
        for key in ("text", "title", "markers", "url")
    )
    ctype = _detect(blob)

    summary = f"Detected a {ctype} challenge page (detection only)."
    review_path = _REVIEW_PATHS.get(ctype, _MANUAL_REVIEW_PATH)

    # optional advisory LLM refinement of the summary only (never the type authority)
    if model is not None or _call is not None:
        try:
            from .llm_exec import LLMCallSpec, execute
            spec = LLMCallSpec(task_id="challenge_classify",
                               prompt_id="challenge_classify", prompt_version="1",
                               input=f"Summarize this detected challenge observation "
                                     f"neutrally (no bypass advice): {blob[:1000]}",
                               schema=None, model=model, review_required=True,
                               fallback=lambda: summary)
            res = execute(spec, _call=_call)
            if res.status == "success" and res.value:
                summary = str(res.value)
        except Exception:
            pass

    out = {"type": ctype, "observation_summary": summary,
           "suggested_review_path": review_path, "advisory": True}
    # safety: scrub any forbidden token that might have crept into the summary
    low = (out["observation_summary"] + " " + out["suggested_review_path"]).lower()
    out["clean"] = not any(w in low for w in _FORBIDDEN_OUT)
    if not out["clean"]:
        out["observation_summary"] = f"Detected a {ctype} challenge page (detection only)."
        out["clean"] = True
    return out


# ── Detection + ROUTING to the owning safe flow (NEVER solving) ─────────────
#
# The ONLY labels route_challenge emits. They drive safe routing; none of them
# is a solving instruction, a bypass step, or a challenge-response value.
ROUTING_LABELS = (
    "challenge_present",        # a challenge was detected on the page
    "challenge_type_unknown",   # challenge present but widget type indeterminate
    "manual_handoff_required",  # hand to an authenticated operator in noVNC
    "passive_wait_timeout",     # the site's own challenge did not self-clear in time
    "rate_limit_backoff_required",       # existing cooldown/backoff owner
    "safe_consent_dismissal_required",   # exact/declared safe gate controls only
    "safe_interstitial_dismissal_required",  # safe controls + origin checks
    "destination_re_request_required",   # an upsell swallowed the requested URL
)

CHALLENGE_ROUTES = (
    "none",
    "manual_handoff",
    "rate_limit_backoff",
    "safe_consent_dismissal",
    "safe_interstitial_dismissal",
)

# Generic "this looks like a challenge interstitial" markers -- used ONLY to set
# challenge_present / challenge_type_unknown when no specific widget signature
# matched. Detection signal only; carries no solving content.
_GENERIC_CHALLENGE = re.compile(
    r"verify you are human|checking your browser|just a moment|ddos protection|"
    r"ray id|cf-chl|challenge-platform|attention required|are you a robot|"
    r"please enable javascript and cookies|security check",
    re.I,
)


def route_challenge(observation: Dict[str, Any], *, model: Optional[str] = None,
                    _call=None, passive_wait_timed_out: bool = False) -> Dict[str, Any]:
    """Detection + ROUTING to the owning safe flow. **NEVER solving.**

    Given a (possibly) challenge ``observation`` and the PASSIVE-WAIT outcome
    from the runtime (did the site's own security challenge self-clear, or time
    out while still present?), return the exact routing action and labels. Rate
    limits go to backoff; consent and upsell interstitials go to the existing
    origin-safe gate owner; CAPTCHA/login/security challenges go to a human.

    This function performs **no** waiting, **no** widget interaction, and **no**
    solving; it never emits a challenge-response value or a bypass step. It only
    classifies the observation and routes it. ``passive_wait_timed_out`` is a
    fact the CALLER supplies from the real browser's normal page-load wait (a
    site-provided challenge that may clear on its own) -- this function never
    performs the wait and never claims a challenge was solved. Solving, when it
    happens at all, is the operator's action in the authenticated browser,
    recorded elsewhere with real browser evidence.
    """
    c = classify(observation, model=model, _call=_call)
    blob = " ".join(
        str(observation.get(key, ""))
        for key in ("text", "title", "markers", "url")
    )
    typed = c["type"] not in ("custom", "unknown")
    present = typed or bool(_GENERIC_CHALLENGE.search(blob))

    labels = []
    route = "none"
    if present:
        labels.append("challenge_present")
        if c["type"] == "rate-limit":
            route = "rate_limit_backoff"
            labels.append("rate_limit_backoff_required")
        elif c["type"] == "consent":
            route = "safe_consent_dismissal"
            labels.append("safe_consent_dismissal_required")
        elif c["type"] == "interstitial":
            route = "safe_interstitial_dismissal"
            labels.extend(("safe_interstitial_dismissal_required",
                           "destination_re_request_required"))
        else:
            route = "manual_handoff"
        if not typed:
            labels.append("challenge_type_unknown")
        if route == "manual_handoff":
            # A passive-wait timeout is the explicit "did not clear on its own"
            # case. Page gates and rate limits do not enter that wait.
            if passive_wait_timed_out:
                labels.append("passive_wait_timeout")
            labels.append("manual_handoff_required")

    out = {
        "challenge_present": present,
        "type": c["type"],
        "route": route,
        "labels": labels,
        "suggested_review_path": c["suggested_review_path"],
        "advisory": True,
        # This module NEVER asserts a challenge was solved. Detection/routing
        # only; an operator/browser solve is recorded elsewhere with evidence.
        "solved": False,
    }
    # safety: routing output is labels + a neutral review path -- never a
    # forbidden bypass/solver word, and never a challenge-response value.
    low = (" ".join(labels) + " " + out["suggested_review_path"]).lower()
    out["clean"] = not any(w in low for w in _FORBIDDEN_OUT)
    return out
