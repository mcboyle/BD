"""Challenge handling -- MANUAL HANDOFF + PASSIVE SELF-CLEAR ONLY. No solving.

This module models the lifecycle of a *detected* challenge (CAPTCHA / Cloudflare
interstitial / login wall) as an explicit, auditable state machine. Its only two
successful outcomes are:

  1. the site's OWN challenge clears on its own and the DETECTOR observes it gone
     (passive self-clear -- the normal browser just finished loading), or
  2. an authenticated human handles it in the noVNC browser and the detector then
     confirms it is gone (manual operator handoff).

What this module deliberately does NOT do, anywhere, by construction:

  * solve a CAPTCHA or any challenge;
  * click, fill, type into, or auto-submit a challenge widget;
  * call an external solver service or replay/fabricate a challenge response;
  * change browser fingerprint / evasion settings;
  * persist raw challenge-response material; or
  * claim that *automation* solved the challenge.

The single load-bearing safety invariant: **resume is gated on the DETECTOR
observing the challenge is gone -- never on an automation "solved" assertion.**
A resumable state is unreachable while the detector still classifies the page as
``challenge_present``. ``solved`` is a read-only property that is always False.

This is a framework with clear seams; wiring it into the live capture runner is a
separate, operator-gated step. See the TODOs for safe future improvements (all of
which are notification / queue / evidence ergonomics -- never solving).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .challenge_classify import route_challenge, ROUTING_LABELS  # detection/routing only
from .capture_artifact_redact import redact_artifact, scan_artifact_secrets

# ── Status labels (the operator-facing lifecycle states) ─────────────────────
CHALLENGE_PRESENT = "challenge_present"                 # detected; run paused
PASSIVE_WAITING = "passive_waiting"                     # normal browser wait for self-clear
MANUAL_HANDOFF_REQUIRED = "manual_handoff_required"     # needs a human
OPERATOR_ACTION_REQUIRED = "operator_action_required"   # handed to operator in noVNC
OPERATOR_HANDOFF_COMPLETE = "operator_handoff_complete" # operator signalled + detector confirmed
CHALLENGE_CLEARED_OBSERVED = "challenge_cleared_observed"  # detector saw it gone -> resumable
CHALLENGE_TIMEOUT = "challenge_timeout"                 # passive wait elapsed, still present

STATUS_LABELS = (
    CHALLENGE_PRESENT, PASSIVE_WAITING, MANUAL_HANDOFF_REQUIRED,
    OPERATOR_ACTION_REQUIRED, OPERATOR_HANDOFF_COMPLETE,
    CHALLENGE_CLEARED_OBSERVED, CHALLENGE_TIMEOUT,
)

# The only states from which a paused run may resume. Both are reachable ONLY
# after the detector confirms the challenge is gone (see observe / operator_complete).
_RESUMABLE = frozenset({CHALLENGE_CLEARED_OBSERVED, OPERATOR_HANDOFF_COMPLETE})

# Explicit, validated transition table. `None` is the pre-detection start.
_TRANSITIONS: Dict[Optional[str], frozenset] = {
    None: frozenset({CHALLENGE_PRESENT}),
    CHALLENGE_PRESENT: frozenset({PASSIVE_WAITING, MANUAL_HANDOFF_REQUIRED,
                                  CHALLENGE_CLEARED_OBSERVED}),
    PASSIVE_WAITING: frozenset({CHALLENGE_CLEARED_OBSERVED, CHALLENGE_TIMEOUT}),
    CHALLENGE_TIMEOUT: frozenset({MANUAL_HANDOFF_REQUIRED}),
    MANUAL_HANDOFF_REQUIRED: frozenset({OPERATOR_ACTION_REQUIRED}),
    OPERATOR_ACTION_REQUIRED: frozenset({OPERATOR_HANDOFF_COMPLETE,
                                         CHALLENGE_CLEARED_OBSERVED}),
    OPERATOR_HANDOFF_COMPLETE: frozenset({CHALLENGE_CLEARED_OBSERVED}),
    CHALLENGE_CLEARED_OBSERVED: frozenset(),  # terminal: resume
}


class ChallengeStateError(RuntimeError):
    """Raised on an invalid state transition -- keeps the lifecycle auditable."""


class ChallengeHandler:
    """Drives one detected challenge through manual handoff / passive self-clear.

    Construct with the initial page ``observation`` ({text,title,markers}). If no
    challenge is detected the handler is inert (``state is None``; not paused). If
    a challenge IS detected the handler starts paused in ``challenge_present`` and
    will not permit resume until the detector observes the challenge gone.
    """

    def __init__(self, observation: Dict[str, Any]) -> None:
        routed = route_challenge(observation)
        self._routed = routed
        self._labels: List[str] = list(routed.get("labels", []))
        self._type: str = routed.get("type", "unknown")
        self._present: bool = bool(routed.get("challenge_present"))
        self._state: Optional[str] = CHALLENGE_PRESENT if self._present else None
        self._history: List[str] = [self._state] if self._state else []

    # ── introspection ────────────────────────────────────────────────────
    @property
    def state(self) -> Optional[str]:
        return self._state

    @property
    def challenge_type(self) -> str:
        return self._type

    @property
    def history(self) -> List[str]:
        return list(self._history)

    @property
    def solved(self) -> bool:
        # Automation NEVER solves a challenge. This is intentionally a constant;
        # there is no setter and no code path that flips it to True.
        return False

    def is_active(self) -> bool:
        """True while a detected challenge is being handled (not inert)."""
        return self._state is not None

    def is_paused(self) -> bool:
        """The run stays paused until a resumable, detector-confirmed state."""
        return self._state is not None and self._state not in _RESUMABLE

    # ── transition helper (validated) ─────────────────────────────────────
    def _to(self, target: str) -> None:
        allowed = _TRANSITIONS.get(self._state, frozenset())
        if target not in allowed:
            raise ChallengeStateError(
                f"illegal transition {self._state!r} -> {target!r}")
        self._state = target
        self._history.append(target)

    # ── lifecycle transitions (all non-solving) ───────────────────────────
    def begin_passive_wait(self) -> str:
        """Enter the normal browser wait, letting a site-provided challenge
        clear on its own. This module performs NO waiting itself; the caller
        runs the real browser's page-load wait and reports the outcome back via
        ``observe`` / ``mark_passive_timeout``."""
        self._to(PASSIVE_WAITING)
        return self._state

    def observe(self, fresh_observation: Dict[str, Any]) -> bool:
        """Re-run DETECTION on a fresh observation. If the challenge is gone,
        transition to ``challenge_cleared_observed`` (resumable). If it is still
        present, stay put and remain paused. Returns True iff cleared.

        This is the ONLY way to legitimately reach a resumable state via
        self-clear: absence must be OBSERVED by the detector, never assumed."""
        still_present = bool(route_challenge(fresh_observation)["challenge_present"])
        if still_present:
            return False
        self._to(CHALLENGE_CLEARED_OBSERVED)
        return True

    def mark_passive_timeout(self) -> str:
        """The passive wait elapsed while the challenge was still present."""
        self._to(CHALLENGE_TIMEOUT)
        return self._state

    def require_manual_handoff(self) -> str:
        """Route to a human. Valid from a timed-out wait or directly from a
        freshly detected challenge that we do not want to passively wait on."""
        self._to(MANUAL_HANDOFF_REQUIRED)
        return self._state

    def hand_off_to_operator(self, artifact: Optional[Dict[str, Any]] = None
                             ) -> Dict[str, Any]:
        """Hand control to an authenticated operator in the noVNC browser and
        return a REDACTED evidence bundle plus neutral handoff instructions. No
        widget is touched; the human acts in the live browser."""
        self._to(OPERATOR_ACTION_REQUIRED)
        return {
            "state": self._state,
            "instructions": self.operator_instructions(),
            "evidence": self.evidence_bundle(artifact) if artifact is not None else None,
        }

    def operator_complete(self, fresh_observation: Dict[str, Any]) -> bool:
        """An explicit human 'manual handoff complete' signal. We DO NOT take the
        operator's word as proof: we re-run the detector. Only if the challenge
        is now gone do we advance to ``operator_handoff_complete`` (resumable).
        If it is somehow still present, we stay in ``operator_action_required``
        and remain paused. Returns True iff the completion was accepted."""
        still_present = bool(route_challenge(fresh_observation)["challenge_present"])
        if still_present:
            return False  # operator claim does not override the detector
        self._to(OPERATOR_HANDOFF_COMPLETE)
        return True

    # ── the resume gate (double-gated, detector-authoritative) ────────────
    def can_resume(self, fresh_observation: Optional[Dict[str, Any]] = None) -> bool:
        """Resume is permitted ONLY from a resumable state AND never while a
        fresh observation still detects a challenge.

        Layer 1 (hard): if ``fresh_observation`` still classifies as
        ``challenge_present``, return False regardless of state.
        Layer 2 (state): the resumable states are reachable only after the
        detector confirmed absence (see ``observe`` / ``operator_complete``).

        There is no path to True that asserts automation solved anything."""
        if fresh_observation is not None:
            if bool(route_challenge(fresh_observation)["challenge_present"]):
                return False
        return self._state in _RESUMABLE

    # ── redacted evidence + structured logging (never raw material) ───────
    def evidence_bundle(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        """Assemble the operator-facing evidence bundle. The captured artifact is
        passed through the redaction floor so NO raw challenge-response material
        (tokens, response blobs, opaque challenge fields, challenge query params)
        is preserved -- only the high-level signal and redacted context."""
        redacted = redact_artifact(artifact)
        # belt-and-suspenders: confirm the redacted bundle carries no secrets the
        # scanner recognizes; the bundle is evidence, not a payload store.
        residual = scan_artifact_secrets(redacted)
        return {
            "challenge_present": self._present,
            "challenge_type": self._type,
            "state": self._state,
            "labels": list(self._labels),
            "redacted_context": redacted,
            "residual_secret_findings": residual,  # [] == clean
            "solved": False,  # automation never solves; recorded for auditors
            "note": ("manual-handoff / passive-clear evidence; raw challenge "
                     "response material is intentionally absent (redacted)."),
        }

    def operator_instructions(self) -> str:
        """Neutral, human-facing handoff text. Contains NO solving/bypass steps;
        it simply directs an authenticated human to the live browser."""
        return ("A site challenge was detected and the automated run is paused. "
                "Open the authenticated noVNC browser session and complete the "
                "challenge yourself as a normal user. The run will resume only "
                "after the detector confirms the challenge is no longer present.")

    def to_log_event(self) -> Dict[str, Any]:
        """Structured log record for this handler's current state. Carries the
        state, routing labels, and type ONLY -- never raw challenge material."""
        return {
            "event": "challenge_handling",
            "state": self._state,
            "challenge_present": self._present,
            "challenge_type": self._type,
            "labels": list(self._labels),
            "solved": False,
            "raw_challenge_material": False,  # asserted: we never log raw payloads
        }


# TODO(safe, manual-handoff ergonomics only -- never solving):
#   * surface a desktop / cockpit notification when OPERATOR_ACTION_REQUIRED is
#     entered, so a human is paged promptly;
#   * persist the redacted evidence_bundle() into the operator review queue with
#     a stable id for later audit;
#   * add a configurable passive-wait budget (seconds) the caller can pass before
#     mark_passive_timeout(), surfaced in settings;
#   * record an audit trail of state history with timestamps.
# None of these introduce widget interaction, solver calls, or token handling.
