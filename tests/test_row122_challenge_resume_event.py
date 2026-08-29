"""Row 122 (LEDGER ITEM 31 sub-row P3-T12-CALLSITE) -- the held-open capture
runner emits an EXPLICIT detector-cleared / resume EVENT at the
``session_capture`` seam, and never infers resume from a downstream consequence.

THE ROW'S RESTRAINT IS THE SUBJECT. Detect / pause / handoff were already
observed live and a human completed a real CAPTCHA; what was missing was a
first-class recorded event saying "the DETECTOR confirmed the challenge is gone,
therefore the run may resume". Inferring that from later authenticated traffic
asserts over a subject the instrument never saw, so the event is emitted by the
detector path itself or not at all.

Two defects this file pins, both present on the pristine base:

  1. FAIL-OPEN. ``drive_challenge_handling`` re-observed the page and fed the
     result straight to ``ChallengeHandler.observe``. An UNREADABLE page (every
     read raising -> an all-empty observation) carries no challenge markers, so
     the detector answered "absent" and the run advanced to the resumable
     ``challenge_cleared_observed``. Absence of evidence became evidence of
     absence. It must be UNKNOWN, and UNKNOWN must not resume.
  2. NO EVENT. The held-open call site discarded the returned handler, and the
     one log record that did fire on a passive self-clear shared its
     ``event`` name with detection and handoff noise, never fired at all on the
     operator-handoff path (the path the live observation actually took), and
     fired FALSELY under defect 1.

Everything here runs over synthetic observations and a duck-typed page. CLAUDE.md
A6 forbids a repository test from driving a live challenge; the live
human-completed CAPTCHA is recorded in the row, not re-run here.
"""
import io
import sys

BD_GATE_SCOPE = "module"

from bulk_downloader.session_capture import (
    drive_challenge_handling,
    observation_is_conclusive,
    challenge_resume_event,
    emit_challenge_resume_event,
    emit_resume_when_cleared,
    recorded_resume_event,
    CHALLENGE_RESUME_EVENT,
    RESUME_DECISION_RESUMED,
    RESUME_DECISION_BLOCKED,
    RESUME_DECISION_UNKNOWN,
)
from bulk_downloader.challenge_handling import (
    ChallengeHandler,
    CHALLENGE_CLEARED_OBSERVED,
    OPERATOR_ACTION_REQUIRED,
    OPERATOR_HANDOFF_COMPLETE,
)

# ── observation fixtures ─────────────────────────────────────────────────────
CHALLENGE_OBS = {
    "text": "Just a moment... Checking your browser before accessing. Ray ID: 0a1b2c",
    "title": "Just a moment...",
    "url": "https://example.com/",
    "markers": [],
}
CLEAR_OBS = {
    "text": "Welcome to the gallery. 42 albums available.",
    "title": "Gallery",
    "url": "https://example.com/",
    "markers": [],
}
# What observe_page_for_challenge returns when EVERY read raised -- the shape a
# dead / navigating / detached page produces. Carries no evidence either way.
UNREADABLE_OBS = {"text": "", "title": "", "url": "", "markers": []}


def _scripted(seq):
    """observe_fn yielding each obs in turn, repeating the last. Also counts."""
    state = {"i": 0, "n": 0}

    def _fn():
        state["n"] += 1
        i = state["i"]
        if i < len(seq) - 1:
            state["i"] = i + 1
        return seq[i]

    _fn.calls = state
    return _fn


def _clock_pair():
    t = {"v": 0.0}
    return (lambda: t["v"]), (lambda s: t.__setitem__("v", t["v"] + float(s)))


def _resume_events(events):
    return [e for e in events
            if isinstance(e, dict) and e.get("event") == CHALLENGE_RESUME_EVENT]


# ── 1. THE FAIL-OPEN: an unreadable page is not a clear ──────────────────────
def test_unreadable_observation_is_never_a_detector_clear():
    """RED on the pristine base: the passive loop reached
    ``challenge_cleared_observed`` from an all-empty observation."""
    clock, tick = _clock_pair()
    obs = _scripted([CHALLENGE_OBS, UNREADABLE_OBS])
    events = []
    h = drive_challenge_handling(obs, tick_fn=tick, passive_budget_s=3,
                                 poll_interval_s=1, clock=clock,
                                 log_fn=events.append)
    # precondition: the challenge really was detected, and the loop really ran
    assert obs.calls["n"] >= 2, "the passive loop must have re-observed the page"
    assert h.state != CHALLENGE_CLEARED_OBSERVED, (
        "FAIL-OPEN: an unreadable page (no title, no text, no markers) carries "
        "no evidence the challenge cleared; treating it as cleared resumes a "
        "run over a subject the detector never saw"
    )
    # fail-CLOSED: the budget elapses with the state unknown -> a human
    assert h.state == OPERATOR_ACTION_REQUIRED
    assert h.is_paused() is True
    assert not _resume_events(events), (
        "no detector-cleared/resume event may be emitted for a page the "
        "detector could not read"
    )


def test_conclusiveness_predicate_separates_unknown_from_clear():
    assert observation_is_conclusive(CLEAR_OBS) is True
    assert observation_is_conclusive(CHALLENGE_OBS) is True
    assert observation_is_conclusive(UNREADABLE_OBS) is False
    assert observation_is_conclusive({"text": "   ", "title": "", "markers": []}) is False
    assert observation_is_conclusive({"markers": ["cf-chl"]}) is True
    assert observation_is_conclusive(None) is False
    assert observation_is_conclusive({}) is False


# ── 2. exactly ONE resume event for one detector-confirmed clear ─────────────
def test_resume_event_fires_exactly_once_for_one_clear():
    clock, tick = _clock_pair()
    events = []
    h = drive_challenge_handling(
        _scripted([CHALLENGE_OBS, CHALLENGE_OBS, CLEAR_OBS]), tick_fn=tick,
        passive_budget_s=10, poll_interval_s=1, clock=clock, log_fn=events.append)
    assert h.state == CHALLENGE_CLEARED_OBSERVED  # precondition
    ev = _resume_events(events)
    assert len(ev) == 1, f"expected exactly 1 resume event, got {len(ev)}"
    assert ev[0]["decision"] == RESUME_DECISION_RESUMED
    assert ev[0]["detector_cleared"] is True
    assert ev[0]["resume_permitted"] is True
    assert ev[0]["solved"] is False
    # the event is DISTINCT from the surrounding challenge_handling log noise
    assert ev[0]["event"] != "challenge_handling"
    assert [e.get("event") for e in events].count(CHALLENGE_RESUME_EVENT) == 1
    # and it is readable back off the handler, not only off the log stream
    assert recorded_resume_event(h) == ev[0]
    # a second emission attempt on the same cleared handler adds nothing
    assert emit_challenge_resume_event(h, CLEAR_OBS, log_fn=events.append) is None
    assert len(_resume_events(events)) == 1


# ── 3. NEGATIVE CONTROL: no challenge -> no event, no extra work ─────────────
def test_no_challenge_emits_no_resume_event_and_is_not_slowed():
    clock, tick = _clock_pair()
    ticks = {"n": 0}

    def _tick(s):
        ticks["n"] += 1
        tick(s)

    obs = _scripted([CLEAR_OBS])
    events = []
    h = drive_challenge_handling(obs, tick_fn=_tick, passive_budget_s=30,
                                 poll_interval_s=1, clock=clock,
                                 log_fn=events.append)
    assert h.is_active() is False and h.state is None
    assert obs.calls["n"] == 1, "the zero-cost normal path observes exactly once"
    assert ticks["n"] == 0, "the new path must not make a clean site wait"
    assert events == [], "a run with no challenge emits nothing at all"
    assert _resume_events(events) == []
    # an explicit emission request on an inert handler is still a no-op
    assert emit_challenge_resume_event(h, CLEAR_OBS, log_fn=events.append) is None
    assert events == []
    assert recorded_resume_event(h) is None


# ── 4. a resume WITHOUT a cleared detector never emits "resumed" ─────────────
def test_resume_without_cleared_detector_is_blocked_not_resumed():
    clock, tick = _clock_pair()
    h = drive_challenge_handling(_scripted([CHALLENGE_OBS]), tick_fn=tick,
                                 passive_budget_s=0, poll_interval_s=1, clock=clock)
    assert h.state == OPERATOR_ACTION_REQUIRED  # precondition: paused for a human
    events = []
    ev = emit_challenge_resume_event(h, CHALLENGE_OBS, log_fn=events.append)
    assert ev is not None and ev["decision"] == RESUME_DECISION_BLOCKED
    assert ev["detector_cleared"] is False
    assert ev["resume_permitted"] is False
    assert len(_resume_events(events)) == 1
    assert not [e for e in _resume_events(events)
                if e["decision"] == RESUME_DECISION_RESUMED], (
        "a still-present challenge must never produce a resumed event"
    )


def test_resumable_state_with_stale_clear_is_regated_by_the_detector():
    """State says resumable, but a fresh observation still shows the challenge:
    the DETECTOR wins, and the event says blocked."""
    h = ChallengeHandler(CHALLENGE_OBS)
    h.observe(CLEAR_OBS)
    assert h.state == CHALLENGE_CLEARED_OBSERVED  # precondition
    ev = challenge_resume_event(h, CHALLENGE_OBS)
    assert ev["decision"] == RESUME_DECISION_BLOCKED
    assert ev["resume_permitted"] is False


# ── 5. ABSENCE IS UNKNOWN, NOT CLEARED ───────────────────────────────────────
def test_indeterminate_detector_reports_unknown_never_resumed():
    h = ChallengeHandler(CHALLENGE_OBS)
    h.observe(CLEAR_OBS)
    assert h.state == CHALLENGE_CLEARED_OBSERVED  # precondition
    events = []
    ev = emit_challenge_resume_event(h, UNREADABLE_OBS, log_fn=events.append)
    assert ev["decision"] == RESUME_DECISION_UNKNOWN, (
        "a detector that cannot read the page reports UNKNOWN; defaulting to "
        "resumed is the fail-open this row exists to refuse"
    )
    assert ev["detector_cleared"] is None, "UNKNOWN is a third state, not False"
    assert ev["resume_permitted"] is False
    assert len(_resume_events(events)) == 1


def test_missing_handler_is_unknown_not_ok():
    """A degraded seam (no handler at all) is UNKNOWN, never a silent OK."""
    ev = challenge_resume_event(None)
    assert ev["decision"] == RESUME_DECISION_UNKNOWN
    assert ev["detector_cleared"] is None
    assert ev["resume_permitted"] is False


# ── 6. the operator path: the event is REACHABLE after a human clears it ─────
def test_operator_cleared_challenge_emits_exactly_one_resume_event():
    clock, tick = _clock_pair()
    h = drive_challenge_handling(_scripted([CHALLENGE_OBS]), tick_fn=tick,
                                 passive_budget_s=0, poll_interval_s=1, clock=clock)
    assert h.state == OPERATOR_ACTION_REQUIRED  # precondition: handed to a human
    events = []
    # the held-open loop polls: still challenged, then unreadable, then cleared
    obs = _scripted([CHALLENGE_OBS, UNREADABLE_OBS, CLEAR_OBS])
    assert emit_resume_when_cleared(h, obs, log_fn=events.append) is None
    assert _resume_events(events) == [], "still challenged -> nothing emitted"
    assert emit_resume_when_cleared(h, obs, log_fn=events.append) is None
    assert _resume_events(events) == [], "unreadable -> UNKNOWN, nothing emitted"
    assert h.state == OPERATOR_ACTION_REQUIRED, "neither poll may advance state"
    ev = emit_resume_when_cleared(h, obs, log_fn=events.append)
    assert ev is not None and ev["decision"] == RESUME_DECISION_RESUMED
    assert h.state == OPERATOR_HANDOFF_COMPLETE
    assert h.solved is False, "automation never claims to have solved anything"
    assert len(_resume_events(events)) == 1
    # further polls after the run resumed add no second event
    assert emit_resume_when_cleared(h, obs, log_fn=events.append) is None
    assert len(_resume_events(events)) == 1


def test_emit_resume_when_cleared_is_a_noop_without_a_challenge():
    h = ChallengeHandler(CLEAR_OBS)
    assert h.is_active() is False  # precondition
    obs = _scripted([CLEAR_OBS])
    events = []
    assert emit_resume_when_cleared(h, obs, log_fn=events.append) is None
    assert emit_resume_when_cleared(None, obs, log_fn=events.append) is None
    assert events == []
    assert obs.calls["n"] == 0, (
        "the inert path must not even observe the page: a clean run is not "
        "slowed by the resume-event path"
    )


# ── 7. the held-open call site no longer discards the handler ────────────────
class _FakePage:
    def title(self):
        return "Just a moment..."

    def inner_text(self, selector):
        return "Just a moment... checking your browser"

    def wait_for_timeout(self, ms):
        return None


class _FakeHandler:
    def __init__(self, state):
        self.state = state

    def is_active(self):
        return self.state is not None

    def can_resume(self, fresh=None):
        return self.state in (CHALLENGE_CLEARED_OBSERVED, OPERATOR_HANDOFF_COMPLETE)

    @property
    def challenge_type(self):
        return "turnstile"

    def operator_instructions(self):
        return "OPEN-NOVNC-AND-COMPLETE-THE-CHALLENGE-YOURSELF"

    def to_log_event(self):
        return {"event": "challenge_handling", "state": self.state}


def _run_settle(seam):
    """Drive tools.capture_session._settle_challenge_handoff with a patched seam
    and capture the stderr the operator would read in the noVNC terminal."""
    import tools.capture_session as cs
    import bulk_downloader.session_capture as sc
    orig_seam = sc.handle_challenge_on_page
    orig_wait = cs._challenge_wait_seconds
    orig_err = sys.stderr
    buf = io.StringIO()
    try:
        sc.handle_challenge_on_page = seam
        cs._challenge_wait_seconds = lambda: 5.0
        sys.stderr = buf
        handler = cs._settle_challenge_handoff(_FakePage())
    finally:
        sys.stderr = orig_err
        sc.handle_challenge_on_page = orig_seam
        cs._challenge_wait_seconds = orig_wait
    return handler, buf.getvalue()


def test_callsite_surfaces_the_resume_decision_for_a_paused_run():
    handler, err = _run_settle(
        lambda page, **kw: _FakeHandler(OPERATOR_ACTION_REQUIRED))
    assert handler is not None, "the call site must not discard the handler"
    assert "[challenge-resume]" in err, (
        "the resume decision must reach the operator's noVNC terminal under a "
        "prefix distinct from the surrounding [challenge] log noise"
    )
    assert RESUME_DECISION_BLOCKED in err
    assert RESUME_DECISION_RESUMED not in err
    assert "OPEN-NOVNC-AND-COMPLETE-THE-CHALLENGE-YOURSELF" in err


def test_callsite_emits_one_resumed_line_for_a_cleared_challenge():
    handler, err = _run_settle(
        lambda page, **kw: _FakeHandler(CHALLENGE_CLEARED_OBSERVED))
    assert handler is not None
    lines = [ln for ln in err.splitlines() if "[challenge-resume]" in ln]
    assert len(lines) == 1, f"expected exactly one resume line, got {lines!r}"
    assert RESUME_DECISION_RESUMED in lines[0]


def test_callsite_emits_nothing_extra_when_no_challenge_occurred():
    handler, err = _run_settle(lambda page, **kw: _FakeHandler(None))
    assert "[challenge-resume]" not in err, (
        "negative control: a normal site produces no resume event"
    )
    assert err.strip() == ""


def test_callsite_reports_unknown_when_the_seam_is_unavailable():
    def _boom(page, **kw):
        raise RuntimeError("no browser")

    handler, err = _run_settle(_boom)
    assert handler is None
    assert "[challenge-resume]" in err and RESUME_DECISION_UNKNOWN in err, (
        "an unavailable detector is UNKNOWN, never a silent OK (CLAUDE.md A7)"
    )
    assert RESUME_DECISION_RESUMED not in err


# ── 8. the boundary holds: nothing here solves, clicks, or fabricates ────────
def test_resume_event_carries_no_solving_claim_and_no_raw_material():
    h = ChallengeHandler(CHALLENGE_OBS)
    h.observe(CLEAR_OBS)
    ev = challenge_resume_event(h, CLEAR_OBS)
    assert ev["solved"] is False
    assert ev["raw_challenge_material"] is False
    blob = " ".join(f"{k}={v}" for k, v in ev.items()).lower()
    for token in ("bypass", "auto-submit", "token", "cf_clearance"):
        assert token not in blob, f"resume event leaked {token!r}"


def test_local_resumable_fallback_matches_the_framework_state_machine():
    """The seam keeps a duck-typing fallback copy of the resumable states. If
    the framework ever adds one, this pin fails instead of the fallback silently
    refusing a legitimate resume."""
    import bulk_downloader.challenge_handling as ch
    import bulk_downloader.session_capture as sc
    assert set(sc._RESUMABLE_STATES) == set(ch._RESUMABLE), (
        "the seam's fallback resumable set drifted from the framework's"
    )
    assert set(sc._RESUMABLE_STATES) == {CHALLENGE_CLEARED_OBSERVED,
                                         OPERATOR_HANDOFF_COMPLETE}


# ── 9. wire proof: the held-open pump really polls the retained handler ──────
def test_held_open_pump_is_wired_to_the_retained_handler():
    """Source-structural, like the P3-T12-CALLSITE wire proof it extends: the
    live held-open loop is not exercisable without a real browser, so the wiring
    itself is what a repository test can assert (CLAUDE.md A6)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "tools" / "capture_session.py").read_text(encoding="utf-8")
    assert "_challenge[0] = _settle_challenge_handoff(page)" in src, (
        "the settle step's handler must be RETAINED, not discarded -- that "
        "discard is exactly what left row 122's event unemitted"
    )
    assert "_poll_challenge_resume(_challenge[0], page)" in src, (
        "the held-open pump must poll the retained handler so the operator "
        "path can reach a detector-confirmed resume"
    )
    import tools.capture_session as cs
    assert callable(cs._poll_challenge_resume)


def test_poll_is_free_when_no_challenge_was_detected():
    """Negative control at the call site: a page that would EXPLODE if read is
    never read, because an inert/absent handler short-circuits first."""
    import tools.capture_session as cs

    class _Landmine:
        def title(self):
            raise AssertionError("the clean-run path must never read the page")

        def inner_text(self, selector):
            raise AssertionError("the clean-run path must never read the page")

    page = _Landmine()
    assert cs._poll_challenge_resume(None, page) is None
    assert cs._poll_challenge_resume(_FakeHandler(None), page) is None


# ── 10. COMPOSITION: settle files "blocked", the human clears it, the pump
#        still emits the one resumed event ───────────────────────────────────
def test_a_blocked_reading_does_not_consume_the_resume_slot():
    """Only a RESUMED decision is terminal. A blocked/unknown reading describes a
    run that is still paused; if it claimed the once-per-run slot the human's
    later clear would be permanently unreportable -- the same fail-open in a new
    place (CLAUDE.md A7: every fix tends to reproduce the defect's shape)."""
    clock, tick = _clock_pair()
    h = drive_challenge_handling(_scripted([CHALLENGE_OBS]), tick_fn=tick,
                                 passive_budget_s=0, poll_interval_s=1, clock=clock)
    assert h.state == OPERATOR_ACTION_REQUIRED  # precondition
    events = []
    blocked = emit_challenge_resume_event(h, log_fn=events.append)  # settle time
    assert blocked["decision"] == RESUME_DECISION_BLOCKED
    assert recorded_resume_event(h) is None, "a blocked reading must not latch"
    # ... the human then completes the challenge in the noVNC browser
    ev = emit_resume_when_cleared(h, _scripted([CLEAR_OBS]), log_fn=events.append)
    assert ev is not None and ev["decision"] == RESUME_DECISION_RESUMED
    resumed = [e for e in _resume_events(events)
               if e["decision"] == RESUME_DECISION_RESUMED]
    assert len(resumed) == 1
    assert recorded_resume_event(h) == resumed[0]


class _ClearingPage:
    """Duck-typed page showing a challenge until ``body`` is reassigned. Read-only
    surface plus the wait; any interaction attempt is an assertion failure."""

    def __init__(self):
        self.body = "Just a moment... checking your browser. Ray ID: z"
        self.reads = 0

    def title(self):
        return "Just a moment..." if "Ray ID" in self.body else "Gallery"

    def inner_text(self, selector):
        self.reads += 1
        return self.body

    def wait_for_timeout(self, ms):
        return None

    def click(self, *a, **k):
        raise AssertionError("the challenge path must never interact")

    def fill(self, *a, **k):
        raise AssertionError("the challenge path must never interact")

    def evaluate(self, *a, **k):
        raise AssertionError("the challenge path must never interact")


def test_settle_then_pump_emits_exactly_one_resumed_line_end_to_end():
    """The whole live shape, through the REAL seam: settle hands off to a human,
    the human clears the challenge, and the held-open pump files one resumed
    event. No live site is contacted -- the page is synthetic (CLAUDE.md A6)."""
    import tools.capture_session as cs
    page = _ClearingPage()
    orig_wait, orig_err = cs._challenge_wait_seconds, sys.stderr
    buf = io.StringIO()
    try:
        cs._challenge_wait_seconds = lambda: 0.0  # straight to operator handoff
        sys.stderr = buf
        handler = cs._settle_challenge_handoff(page)
        paused_state = getattr(handler, "state", None)
        # a tick BEFORE the human acts must change nothing
        cs._poll_challenge_resume(handler, page)
        mid = buf.getvalue()
        page.body = "Welcome to the gallery. 42 albums available."
        cs._poll_challenge_resume(handler, page)
        cs._poll_challenge_resume(handler, page)  # and again: still exactly one
    finally:
        sys.stderr = orig_err
        cs._challenge_wait_seconds = orig_wait
    err = buf.getvalue()
    assert paused_state == OPERATOR_ACTION_REQUIRED  # precondition
    assert RESUME_DECISION_RESUMED not in mid, (
        "nothing may claim a resume while the challenge is still present")
    assert handler.state == OPERATOR_HANDOFF_COMPLETE
    assert handler.solved is False
    resumed = [ln for ln in err.splitlines()
               if "[challenge-resume]" in ln and RESUME_DECISION_RESUMED in ln]
    assert len(resumed) == 1, f"expected one resumed line, got {resumed!r}"
    assert page.reads >= 3, "the detector must actually have re-read the page"
