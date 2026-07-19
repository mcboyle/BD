"""Phase 9.13 -- challenge-type classification (detection only).

Classify a *detected* challenge page to route manual handoff faster. Detection
only: there is NO bypass/evasion content anywhere in the output. Deterministic
keyword detection first; an optional advisory LLM refinement may summarize the
observation. Output is advisory and contains only a type tag, a neutral summary,
and a manual-review path.
"""

import re
from typing import Any, Dict, Optional

CHALLENGE_TYPES = ("turnstile", "hcaptcha", "recaptcha", "login-wall",
                   "custom", "unknown")

# words that must never appear in our structured output (no bypass guidance)
_FORBIDDEN_OUT = ("bypass", "solve", "evade", "defeat", "auto-submit", "token-harvest")

_SIGNATURES = [
    ("turnstile", re.compile(r"cf-turnstile|turnstile|challenges\.cloudflare", re.I)),
    ("hcaptcha", re.compile(r"hcaptcha|h-captcha", re.I)),
    ("recaptcha", re.compile(r"recaptcha|g-recaptcha|gstatic.*recaptcha", re.I)),
    ("login-wall", re.compile(r"\b(sign in|log ?in|password|sign-in|authenticate)\b", re.I)),
]


def _detect(text: str) -> str:
    for name, rx in _SIGNATURES:
        if rx.search(text or ""):
            return name
    return "unknown"


def classify(observation: Dict[str, Any], *, model: Optional[str] = None,
             _call=None) -> Dict[str, Any]:
    """Classify a detected challenge. `observation` may carry `text`, `title`,
    `markers`. Returns {type, observation_summary, suggested_review_path, advisory}.
    Detection only -- never bypass instructions."""
    blob = " ".join(str(observation.get(k, "")) for k in ("text", "title", "markers"))
    ctype = _detect(blob)

    summary = f"Detected a {ctype} challenge page (detection only)."
    review_path = ("Route to the manual operator handoff flow for an authenticated "
                   "human to complete the challenge in the noVNC session.")

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


# ── Detection + ROUTING for the operator-handoff flow (NEVER solving) ─────────
#
# The ONLY labels route_challenge emits. They drive operator handoff; none of
# them is a solving instruction, a bypass step, or a challenge-response value.
ROUTING_LABELS = (
    "challenge_present",        # a challenge was detected on the page
    "challenge_type_unknown",   # challenge present but widget type indeterminate
    "manual_handoff_required",  # hand to an authenticated operator in noVNC
    "passive_wait_timeout",     # the site's own challenge did not self-clear in time
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
    """Detection + ROUTING for the manual-handoff flow. **NEVER solving.**

    Given a (possibly) challenge ``observation`` and the PASSIVE-WAIT outcome
    from the runtime (did the site's own challenge self-clear, or time out while
    still present?), return the routing labels that drive operator handoff.

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
    blob = " ".join(str(observation.get(k, "")) for k in ("text", "title", "markers"))
    typed = c["type"] in ("turnstile", "hcaptcha", "recaptcha", "login-wall")
    present = typed or bool(_GENERIC_CHALLENGE.search(blob))

    labels = []
    if present:
        labels.append("challenge_present")
        if not typed:
            labels.append("challenge_type_unknown")
        # Routing: a challenge that did not self-clear goes to a human in noVNC.
        # A passive-wait timeout is the explicit "did not clear on its own" case.
        if passive_wait_timed_out:
            labels.append("passive_wait_timeout")
        labels.append("manual_handoff_required")

    out = {
        "challenge_present": present,
        "type": c["type"],
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
