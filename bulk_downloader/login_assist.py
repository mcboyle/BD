"""Phase 9.14 -- N-step login inference (assist only).

Model/deterministic assistance for multi-step logins: identify the likely next
step, classify visible fields/buttons, summarize flow state, and route uncertainty
to review. Assist only -- it can NEVER persist credentials or enable a host/template
(there is no such function here), and SSO/cross-origin/challenge cases are routed to
manual review.
"""

from typing import Any, Dict, List, Optional

_EMAIL_FIELDS = ("email", "username", "user", "login", "identifier")
_PASSWORD_FIELDS = ("password", "passwd", "pass")
_SSO_HINTS = ("sign in with google", "continue with apple", "sso", "oauth",
              "single sign-on", "okta", "microsoft")


def infer_step(observation: Dict[str, Any], *, model: Optional[str] = None,
               _call=None) -> Dict[str, Any]:
    """Infer the next login step from an observation. `observation` may carry
    `fields` (list), `buttons` (list), `cross_origin` (bool), `challenge` (bool).
    Returns {next_step, fields, buttons, requires_review, summary, advisory}."""
    fields = [str(f).lower() for f in (observation.get("fields") or [])]
    buttons = [str(b).lower() for b in (observation.get("buttons") or [])]
    cross_origin = bool(observation.get("cross_origin"))
    challenge = bool(observation.get("challenge"))

    has_email = any(any(k in f for k in _EMAIL_FIELDS) for f in fields)
    has_password = any(any(k in f for k in _PASSWORD_FIELDS) for f in fields)
    has_sso = any(any(h in b for h in _SSO_HINTS) for b in buttons)

    requires_review = False
    if challenge:
        next_step = "manual_handoff"
        requires_review = True
        summary = "Challenge detected; route to manual operator handoff."
    elif has_sso or cross_origin:
        next_step = "sso_review"
        requires_review = True
        summary = "SSO / cross-origin login; route to review (uncertain)."
    elif has_password:
        next_step = "enter_password"
        summary = "Password field present; the next step is the password entry."
    elif has_email:
        next_step = "enter_email"
        summary = "Email/username field present; the next step is identifier entry."
    else:
        next_step = "review"
        requires_review = True
        summary = "Login form not clearly recognized; route to review."

    return {
        "next_step": next_step,
        "fields": fields,
        "buttons": buttons,
        "requires_review": requires_review,
        "summary": summary,
        "advisory": True,
    }
