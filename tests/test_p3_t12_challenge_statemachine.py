"""P3-T12 -- challenge-handling STATE MACHINE (manual handoff / passive clear).

Proves the lifecycle around a detected challenge:
  * starts PAUSED in challenge_present; inert when no challenge,
  * passive self-clear path reaches a resumable state only when the DETECTOR
    observes the challenge gone,
  * passive-wait timeout routes to manual handoff,
  * manual handoff preserves REDACTED context (no raw challenge material),
  * resume is BLOCKED while challenge_present still detects true, from EVERY
    state -- the anti-bypass invariant,
  * resume is allowed only from a detector-confirmed cleared state or an
    explicit, detector-confirmed operator completion,
  * NO code path labels automation as having solved the challenge,
  * a static guard prevents auto-click / auto-submit / solver-call primitives
    from being added accidentally,
  * illegal state transitions raise.

NO SOLVING anywhere. Runner-safe: zero-arg fns, repo root from __file__.
"""
import json
import re
from pathlib import Path

from bulk_downloader import challenge_handling as ch

_REPO = Path(__file__).resolve().parent.parent
_FIX = _REPO / "tests" / "corpus" / "challenge" / "challenge_solving_synthetic.cap.json"
_RAW_MARKERS = ("TURNSTILE0RESPONSE0Ab3", "FAKE_SIG_not_real",
                "CHALLENGE0TOKEN0Xy9", "FAKE_response_xxxx")


def _load():
    return json.loads(_FIX.read_text(encoding="utf-8"))


def _challenge_obs():
    f = _load()
    return {"text": f["dom_log"][0]["html"], "title": f.get("title", ""),
            "markers": " ".join(f.get("challenge_markers", []))}


def _clear_obs():
    return {"text": "<html><body><h1>Welcome</h1><video src='ok.mp4'></video></body></html>",
            "title": "Home", "markers": ""}


# ── start / inert ─────────────────────────────────────────────────────────
def test_handler_starts_paused_in_challenge_present():
    h = ch.ChallengeHandler(_challenge_obs())
    assert h.state == ch.CHALLENGE_PRESENT
    assert h.is_active() is True
    assert h.is_paused() is True
    assert h.can_resume() is False


def test_inert_when_no_challenge():
    h = ch.ChallengeHandler(_clear_obs())
    assert h.state is None
    assert h.is_active() is False
    assert h.is_paused() is False


# ── passive self-clear path ────────────────────────────────────────────────
def test_passive_self_clear_reaches_resumable_only_when_observed_gone():
    h = ch.ChallengeHandler(_challenge_obs())
    h.begin_passive_wait()
    assert h.state == ch.PASSIVE_WAITING
    # still present -> observe returns False, stays paused
    assert h.observe(_challenge_obs()) is False
    assert h.is_paused() is True
    # now the site cleared on its own -> detector confirms -> resumable
    assert h.observe(_clear_obs()) is True
    assert h.state == ch.CHALLENGE_CLEARED_OBSERVED
    assert h.can_resume() is True


# ── passive timeout -> manual handoff ──────────────────────────────────────
def test_passive_timeout_routes_to_manual_handoff():
    h = ch.ChallengeHandler(_challenge_obs())
    h.begin_passive_wait()
    h.mark_passive_timeout()
    assert h.state == ch.CHALLENGE_TIMEOUT
    h.require_manual_handoff()
    assert h.state == ch.MANUAL_HANDOFF_REQUIRED
    assert h.is_paused() is True


# ── manual handoff preserves redacted context ──────────────────────────────
def test_manual_handoff_preserves_redacted_context():
    h = ch.ChallengeHandler(_challenge_obs())
    h.require_manual_handoff()
    pkg = h.hand_off_to_operator(_load())
    assert pkg["state"] == ch.OPERATOR_ACTION_REQUIRED
    ev = pkg["evidence"]
    # high-level signal preserved
    assert ev["challenge_present"] is True
    assert ev["challenge_type"] == "turnstile"
    assert ev["redacted_context"] is not None
    assert ev["residual_secret_findings"] == []   # bundle is clean
    assert ev["solved"] is False
    # NO raw challenge-response material anywhere in the bundle
    blob = json.dumps(pkg)
    for m in _RAW_MARKERS:
        assert m not in blob, f"raw challenge material leaked into handoff: {m!r}"
    # the neutral instructions contain no solving/bypass verbs
    low = pkg["instructions"].lower()
    for w in ("solve", "bypass", "auto-submit", "click the", "solver"):
        assert w not in low, f"handoff instructions leaked: {w!r}"


# ── the anti-bypass invariant: resume blocked while present, from EVERY state ─
def test_resume_blocked_while_challenge_present_from_every_state():
    present = _challenge_obs()
    # drive a handler to each state and assert can_resume(present) is False
    builders = []

    def _present_only():
        return ch.ChallengeHandler(_challenge_obs())
    builders.append(_present_only)

    def _passive():
        h = ch.ChallengeHandler(_challenge_obs()); h.begin_passive_wait(); return h
    builders.append(_passive)

    def _timeout():
        h = _passive(); h.mark_passive_timeout(); return h
    builders.append(_timeout)

    def _handoff_req():
        h = _timeout(); h.require_manual_handoff(); return h
    builders.append(_handoff_req)

    def _operator():
        h = _handoff_req(); h.hand_off_to_operator(); return h
    builders.append(_operator)

    def _op_complete():
        h = _operator(); h.operator_complete(_clear_obs()); return h
    builders.append(_op_complete)

    def _cleared():
        h = _passive(); h.observe(_clear_obs()); return h
    builders.append(_cleared)

    for b in builders:
        h = b()
        # hard gate: a fresh observation that STILL shows a challenge => no resume,
        # even from the two resumable states.
        assert h.can_resume(present) is False, f"resume not blocked in {h.state!r}"


# ── resume allowed only from detector-confirmed cleared / operator-complete ──
def test_resume_allowed_only_from_confirmed_states():
    # cleared-observed -> resumable
    h = ch.ChallengeHandler(_challenge_obs()); h.begin_passive_wait()
    h.observe(_clear_obs())
    assert h.can_resume() is True
    # operator handoff complete (detector-confirmed) -> resumable
    h2 = ch.ChallengeHandler(_challenge_obs()); h2.require_manual_handoff()
    h2.hand_off_to_operator()
    assert h2.operator_complete(_clear_obs()) is True
    assert h2.state == ch.OPERATOR_HANDOFF_COMPLETE
    assert h2.can_resume() is True
    # non-resumable states stay False
    h3 = ch.ChallengeHandler(_challenge_obs())
    assert h3.can_resume() is False                  # challenge_present
    h3.require_manual_handoff()
    assert h3.can_resume() is False                  # manual_handoff_required


# ── operator completion is detector-gated, not a claim ──────────────────────
def test_operator_complete_requires_detector_confirmation():
    h = ch.ChallengeHandler(_challenge_obs()); h.require_manual_handoff()
    h.hand_off_to_operator()
    # operator says done but the challenge is STILL present -> not accepted
    assert h.operator_complete(_challenge_obs()) is False
    assert h.state == ch.OPERATOR_ACTION_REQUIRED
    assert h.can_resume() is False
    # now it is actually gone -> accepted
    assert h.operator_complete(_clear_obs()) is True
    assert h.state == ch.OPERATOR_HANDOFF_COMPLETE


# ── INVARIANT: automation never claims it solved the challenge ──────────────
def test_no_code_path_labels_automation_solved():
    h = ch.ChallengeHandler(_challenge_obs())
    assert h.solved is False
    assert h.to_log_event()["solved"] is False
    assert h.to_log_event()["raw_challenge_material"] is False
    # drive to a resumed state; solved must STILL be False (resume != solved)
    h.begin_passive_wait(); h.observe(_clear_obs())
    assert h.can_resume() is True
    assert h.solved is False
    # there is no setter that flips it
    try:
        h.solved = True  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True
    assert raised, "solved must be read-only (no setter)"


# ── STATIC GUARD: no active-solving primitives may be added ─────────────────
def test_static_guard_no_active_solving_primitives():
    src = (_REPO / "bulk_downloader" / "challenge_handling.py").read_text(encoding="utf-8")
    forbidden_calls = [
        r"\.click\(", r"\.fill\(", r"\.type\(", r"\.press\(", r"\.check\(",
        r"\.select_option\(", r"\.evaluate\(", r"\.eval\(", r"\.set_input_files\(",
    ]
    for pat in forbidden_calls:
        assert not re.search(pat, src), f"active widget-interaction primitive present: {pat}"
    forbidden_tokens = ["2captcha", "anticaptcha", "anti-captcha", "capsolver",
                        "capmonster", "deathbycaptcha", "import requests",
                        "import urllib", "import httpx", "urlopen("]
    low = src.lower()
    for tok in forbidden_tokens:
        assert tok not in low, f"solver/network primitive present: {tok!r}"
    # public API method names carry no solving verbs. The `solved` audit
    # property (always False -- the anti-solving assertion) is explicitly
    # exempt; it is verified read-only/False in the solved-invariant test.
    api = [n for n in dir(ch.ChallengeHandler)
           if not n.startswith("_") and n != "solved"]
    for n in api:
        for verb in ("solve", "submit", "click", "fabricate", "replay"):
            assert verb not in n.lower(), f"method {n!r} implies active solving"
    assert ch.ChallengeHandler(_challenge_obs()).solved is False


# ── illegal transitions raise (the lifecycle is auditable) ──────────────────
def test_illegal_transition_raises():
    h = ch.ChallengeHandler(_challenge_obs())  # state = challenge_present
    # cannot jump straight to operator_handoff_complete
    try:
        h._to(ch.OPERATOR_HANDOFF_COMPLETE)
        raised = False
    except ch.ChallengeStateError:
        raised = True
    assert raised, "illegal transition should raise ChallengeStateError"
