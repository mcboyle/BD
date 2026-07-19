"""P3-T12-WIRE -- the v3.66.386 challenge-handling framework wired to the live
capture seam in ``bulk_downloader.session_capture``.

Boundary under test (held HARD): detect / route / manual-handoff / passive
self-clear ONLY. The wiring NEVER solves, clicks, fills, types, evaluates,
auto-submits, calls a solver, replays/persists a challenge response, or claims
automation solved anything. Resume is gated on the DETECTOR observing the
challenge gone -- never on a "solved" assertion.

These tests drive the PURE lifecycle driver (``drive_challenge_handling``) with
synthetic observation/tick/clock callbacks (no real browser), exercise the
read-only observation builder (``observe_page_for_challenge``) and the thin live
seam (``handle_challenge_on_page``) against a duck-typed fake page, and assert a
static no-solving guard over the new wiring source.
"""
import json

from bulk_downloader.session_capture import (
    observe_page_for_challenge,
    drive_challenge_handling,
    handle_challenge_on_page,
    DEFAULT_PASSIVE_BUDGET_S,
)
from bulk_downloader.challenge_handling import (
    CHALLENGE_PRESENT,
    PASSIVE_WAITING,
    OPERATOR_ACTION_REQUIRED,
    OPERATOR_HANDOFF_COMPLETE,
    CHALLENGE_CLEARED_OBSERVED,
)

# ── observation fixtures ─────────────────────────────────────────────────────
# A generic Cloudflare-style interstitial (matches _GENERIC_CHALLENGE markers).
CHALLENGE_OBS = {
    "text": "Just a moment... Checking your browser before accessing. Ray ID: 0a1b2c",
    "title": "Just a moment...",
    "url": "https://example.com/",
    "markers": [],
}
# A clean content page -- no challenge markers AND no login-wall trigger words.
CLEAR_OBS = {
    "text": "Welcome to the gallery. 42 albums available.",
    "title": "Gallery",
    "url": "https://example.com/",
    "markers": [],
}

# An artifact carrying obviously-secret-shaped values to prove redaction at the
# evidence boundary. Emails + a JWT are reliably detected by the redactor.
SECRET_ARTIFACT = {
    "summary": "challenge interstitial observed",
    "context": {
        "leak_url": "https://example.com/cdn-cgi/challenge?cf_challenge_response="
                    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.cGF5bG9hZGJsb2JibG9i."
                    "c2lnbmF0dXJlc2lnbmF0dXJl&ray=0a1b2c",
        "leak_free": "session=supersecretvalue0123456789 contact=admin@example.com",
    },
}


# ── helpers (no pytest fixtures: the custom runner injects nothing) ───────────
def _scripted(seq):
    """Return an observe_fn yielding each obs in turn, repeating the last."""
    state = {"i": 0}

    def _fn():
        i = state["i"]
        if i < len(seq) - 1:
            state["i"] = i + 1
        return seq[i]

    return _fn


def _clock_pair():
    """A monotonic fake clock + a tick that advances it. Deterministic time."""
    t = {"v": 0.0}
    clock = lambda: t["v"]
    tick = lambda s: t.__setitem__("v", t["v"] + float(s))
    return clock, tick


class _FakePage:
    """Duck-typed Playwright page. Models a sequence of (title, body) page
    STATES -- one consistent state is read per observation cycle (title() then
    inner_text()), and the next cycle advances to the following state (capped at
    the last). This mirrors a real page where the title clears together with the
    body when a challenge resolves. Records EVERY method touched so a test can
    prove the observation path is strictly read-only (no widget interaction)."""

    def __init__(self, states, url="https://example.com/", raise_reads=False):
        # `states` is a list of (title, body) tuples; a bare string element is
        # shorthand for a body with an empty title.
        self._states = [s if isinstance(s, tuple) else ("", s) for s in states]
        self._idx = 0
        self.url = url
        self._raise = raise_reads
        self.calls = []

    # read-only surface used by observe_page_for_challenge
    def title(self):
        self.calls.append("title")
        if self._raise:
            raise RuntimeError("flaky title read")
        return self._states[self._idx][0] if self._states else ""

    def inner_text(self, selector):
        self.calls.append(("inner_text", selector))
        if self._raise:
            raise RuntimeError("flaky inner_text read")
        if not self._states:
            return ""
        body = self._states[self._idx][1]
        if self._idx < len(self._states) - 1:
            self._idx += 1  # advance to the next state for the next observation
        return body

    def wait_for_timeout(self, ms):
        self.calls.append(("wait_for_timeout", ms))

    # interaction surface -- MUST NEVER be called by the observation/handoff path
    def click(self, *a, **k):
        self.calls.append("click")

    def fill(self, *a, **k):
        self.calls.append("fill")

    def type(self, *a, **k):
        self.calls.append("type")

    def press(self, *a, **k):
        self.calls.append("press")

    def evaluate(self, *a, **k):
        self.calls.append("evaluate")

    def goto(self, *a, **k):
        self.calls.append("goto")


# ── 1. no challenge -> inert, zero-cost ──────────────────────────────────────
def test_no_challenge_is_inert_and_zero_cost():
    clock, tick = _clock_pair()
    ticks = {"n": 0}

    def _tick(s):
        ticks["n"] += 1
        tick(s)

    h = drive_challenge_handling(_scripted([CLEAR_OBS]), tick_fn=_tick,
                                 passive_budget_s=30, poll_interval_s=1, clock=clock)
    assert h.is_active() is False
    assert h.state is None
    assert h.is_paused() is False
    assert h.can_resume() is False  # inert is not "resumable" -- nothing to resume
    assert ticks["n"] == 0          # never entered the wait loop


# ── 2. passive self-clear -> resumable, detector-confirmed ───────────────────
def test_passive_self_clear_is_resumable():
    clock, tick = _clock_pair()
    obs = _scripted([CHALLENGE_OBS, CHALLENGE_OBS, CHALLENGE_OBS, CLEAR_OBS])
    h = drive_challenge_handling(obs, tick_fn=tick, passive_budget_s=10,
                                 poll_interval_s=1, clock=clock)
    assert h.state == CHALLENGE_CLEARED_OBSERVED
    assert h.is_paused() is False
    assert h.solved is False
    assert PASSIVE_WAITING in h.history     # it really waited before clearing
    assert h.can_resume(CLEAR_OBS) is True


# ── 3. passive timeout -> manual handoff surfaced (redacted), still paused ────
def test_passive_timeout_routes_to_manual_handoff():
    clock, tick = _clock_pair()
    events = []
    h = drive_challenge_handling(_scripted([CHALLENGE_OBS]), tick_fn=tick,
                                 passive_budget_s=3, poll_interval_s=1, clock=clock,
                                 artifact_fn=lambda: SECRET_ARTIFACT,
                                 log_fn=events.append)
    assert h.state == OPERATOR_ACTION_REQUIRED
    assert h.is_paused() is True
    assert h.solved is False
    # still present -> resume refused (Layer-1 detector gate)
    assert h.can_resume(CHALLENGE_OBS) is False
    # the driver surfaced a handoff event carrying a REDACTED evidence bundle
    handoffs = [e for e in events if e.get("handoff")]
    assert handoffs, "expected a handoff log event"
    ev = handoffs[-1]["handoff"]
    assert ev["evidence"]["residual_secret_findings"] == []
    assert ev["evidence"]["solved"] is False
    blob = json.dumps(handoffs[-1])
    assert "admin@example.com" not in blob          # email scrubbed
    assert "supersecretvalue0123456789" not in blob  # opaque secret scrubbed


# ── 4. zero passive budget -> straight to handoff, no waiting ─────────────────
def test_zero_budget_goes_straight_to_handoff():
    clock, tick = _clock_pair()
    ticks = {"n": 0}

    def _tick(s):
        ticks["n"] += 1
        tick(s)

    h = drive_challenge_handling(_scripted([CHALLENGE_OBS]), tick_fn=_tick,
                                 passive_budget_s=0, poll_interval_s=1, clock=clock)
    assert h.state == OPERATOR_ACTION_REQUIRED
    assert ticks["n"] == 0                  # never passively waited
    assert PASSIVE_WAITING not in h.history


# ── 5. operator-complete is detector-gated (claim never overrides detector) ──
def test_operator_complete_is_detector_gated():
    clock, tick = _clock_pair()
    h = drive_challenge_handling(_scripted([CHALLENGE_OBS]), tick_fn=tick,
                                 passive_budget_s=0, poll_interval_s=1, clock=clock)
    assert h.state == OPERATOR_ACTION_REQUIRED
    # operator says "done" but the detector still sees the challenge -> rejected
    assert h.operator_complete(CHALLENGE_OBS) is False
    assert h.can_resume(CHALLENGE_OBS) is False
    assert h.state == OPERATOR_ACTION_REQUIRED
    # operator says "done" and the detector confirms gone -> accepted, resumable
    assert h.operator_complete(CLEAR_OBS) is True
    assert h.state == OPERATOR_HANDOFF_COMPLETE
    assert h.can_resume(CLEAR_OBS) is True
    assert h.solved is False


# ── 6. observation builder is strictly read-only and degrades gracefully ─────
def test_observe_page_is_read_only():
    page = _FakePage([("Just a moment...", "Just a moment... checking your browser")],
                     url="https://example.com/x")
    obs = observe_page_for_challenge(page)
    assert obs["title"] == "Just a moment..."
    assert "checking your browser" in obs["text"]
    assert obs["url"] == "https://example.com/x"
    # ONLY read methods were touched -- no widget interaction at all
    touched = {c if isinstance(c, str) else c[0] for c in page.calls}
    assert touched <= {"title", "inner_text"}
    for forbidden in ("click", "fill", "type", "press", "evaluate", "goto"):
        assert forbidden not in touched


def test_observe_page_degrades_on_flaky_reads():
    page = _FakePage([("t", "x")], raise_reads=True)
    obs = observe_page_for_challenge(page)  # must not raise
    assert obs["title"] == ""
    assert obs["text"] == ""


# ── 7. live seam: no challenge -> inert; page only read ──────────────────────
def test_handle_challenge_on_page_no_challenge():
    page = _FakePage([("Gallery", "Welcome to the gallery. 42 albums available.")])
    h = handle_challenge_on_page(page, passive_budget_s=5)
    assert h.is_active() is False
    assert h.state is None
    touched = {c if isinstance(c, str) else c[0] for c in page.calls}
    for forbidden in ("click", "fill", "type", "press", "evaluate", "goto"):
        assert forbidden not in touched


# ── 8. live seam: passive self-clear via the page, detector-confirmed ────────
def test_handle_challenge_on_page_passive_clear():
    clock, tick = _clock_pair()
    # first body read = challenge, subsequent reads = clear (the site finished)
    page = _FakePage([("Just a moment...", "Just a moment... checking your browser. Ray ID: z"),
                      ("Gallery", "Welcome to the gallery. 42 albums.")])
    h = handle_challenge_on_page(page, passive_budget_s=10, poll_interval_s=1,
                                 clock=clock)
    assert h.state == CHALLENGE_CLEARED_OBSERVED
    assert h.can_resume() is True
    # waited via the real page wait, never interacted with a widget
    assert any(isinstance(c, tuple) and c[0] == "wait_for_timeout"
               for c in page.calls)
    touched = {c if isinstance(c, str) else c[0] for c in page.calls}
    for forbidden in ("click", "fill", "type", "press", "evaluate", "goto"):
        assert forbidden not in touched


# ── 9. default passive budget is a sane positive number ──────────────────────
def test_default_passive_budget_is_positive():
    assert isinstance(DEFAULT_PASSIVE_BUDGET_S, (int, float))
    assert DEFAULT_PASSIVE_BUDGET_S > 0


# ── 10. static guard: the new wiring source contains NO solving primitives ───
def test_no_solving_primitives_in_wiring_source():
    import bulk_downloader.session_capture as sc
    src = open(sc.__file__, "r", encoding="utf-8").read()
    anchor = "def observe_page_for_challenge"
    idx = src.find(anchor)
    assert idx != -1, "wiring functions must be present in session_capture.py"
    wiring = src[idx:]  # the new wiring region (added at end of module)

    forbidden = [
        ".click(", ".fill(", ".type(", ".press(", ".evaluate(", ".select_option(",
        ".set_input_files(", ".dispatch_event(", ".tap(", ".check(",
        "2captcha", "anticaptcha", "anti-captcha", "deathbycaptcha", "capmonster",
        "solve_captcha", "solver", "bypass", "auto_submit", "auto-submit",
        "requests.", "urllib.request", "http.client", "httpx", "aiohttp",
    ]
    for tok in forbidden:
        assert tok not in wiring, f"forbidden solving/network token in wiring: {tok!r}"
