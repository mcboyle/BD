"""P3-T12-CALLSITE -- the held-open capture runner's anti-bot *settle* step now
routes through the canonical challenge seam
(``bulk_downloader.session_capture.handle_challenge_on_page``) instead of the
retired local title-poll loop (``tools.capture_session._settle_through_challenge``).

Boundary (held HARD): detect / passive self-clear / manual-handoff ONLY. The new
wrapper NEVER solves, clicks, fills, types, evaluates, auto-submits, or calls a
solver. Resume stays the held-open operator loop's job (gated by the detector via
the seam), not a "solved" assertion.

These are pure-logic / source-structural tests: the live behaviour (a real
cloudflare/akamai interstitial clearing in a real browser) is stash-only. We patch
the seam to assert the wrapper's contract (budget parity + handoff surfacing) and
read the source for the wire proof + a no-solving guard.

Zero-arg functions; no pytest fixtures (the custom runner injects none); repo root
via __file__; module globals restored in try/finally.
"""
import sys
import inspect
from pathlib import Path

import tools.capture_session as cs
import bulk_downloader.session_capture as sc
from bulk_downloader.challenge_handling import OPERATOR_ACTION_REQUIRED

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "tools" / "capture_session.py").read_text()


class _FakePage:
    """Duck-typed stand-in. The seam is patched in the behaviour tests, so the
    page only needs to be an opaque object the wrapper passes straight through."""
    def title(self):
        return "Just a moment..."

    def wait_for_timeout(self, ms):
        return None


class _FakeHandler:
    def __init__(self, state):
        self.state = state

    def operator_instructions(self):
        return "OPEN-NOVNC-AND-COMPLETE-THE-CHALLENGE-YOURSELF"


# ── 1. budget parity: the seam gets passive_budget_s = _challenge_wait_seconds()
def test_settle_handoff_routes_through_seam_with_budget_parity():
    calls = {}

    def _fake_seam(page, *, passive_budget_s=None, **kw):
        calls["page"] = page
        calls["budget"] = passive_budget_s
        calls["kw"] = kw
        return _FakeHandler(state=None)  # inert: no challenge

    orig_seam = sc.handle_challenge_on_page
    orig_wait = cs._challenge_wait_seconds
    try:
        sc.handle_challenge_on_page = _fake_seam
        cs._challenge_wait_seconds = lambda: 7.0  # sentinel budget
        pg = _FakePage()
        cs._settle_challenge_handoff(pg)
        assert calls.get("page") is pg, "wrapper must pass the live page to the seam"
        assert calls.get("budget") == 7.0, (
            "budget parity broken: the seam must receive passive_budget_s = "
            "_challenge_wait_seconds() (store > env > default), not the module default"
        )
    finally:
        sc.handle_challenge_on_page = orig_seam
        cs._challenge_wait_seconds = orig_wait


# ── 2. manual handoff: operator_instructions() surfaced on operator_action_required
def test_operator_action_required_surfaces_instructions():
    def _fake_seam(page, *, passive_budget_s=None, **kw):
        return _FakeHandler(state=OPERATOR_ACTION_REQUIRED)

    orig_seam = sc.handle_challenge_on_page
    orig_wait = cs._challenge_wait_seconds
    orig_stderr = sys.stderr
    import io
    buf = io.StringIO()
    try:
        sc.handle_challenge_on_page = _fake_seam
        cs._challenge_wait_seconds = lambda: 20.0
        sys.stderr = buf
        cs._settle_challenge_handoff(_FakePage())
    finally:
        sys.stderr = orig_stderr
        sc.handle_challenge_on_page = orig_seam
        cs._challenge_wait_seconds = orig_wait
    out = buf.getvalue()
    assert "OPEN-NOVNC-AND-COMPLETE-THE-CHALLENGE-YOURSELF" in out, (
        "on operator_action_required the wrapper must surface "
        "handler.operator_instructions() to the noVNC capture terminal (stderr)"
    )


# ── 3. inert / resumable handler must NOT raise (normal-site + self-clear paths)
def test_inert_and_resumable_states_are_non_fatal():
    orig_seam = sc.handle_challenge_on_page
    orig_wait = cs._challenge_wait_seconds
    try:
        cs._challenge_wait_seconds = lambda: 20.0
        for st in (None, "challenge_cleared_observed"):
            sc.handle_challenge_on_page = (
                lambda page, *, passive_budget_s=None, **kw: _FakeHandler(state=st)
            )
            # must not raise; the held-open loop proceeds exactly as before
            cs._settle_challenge_handoff(_FakePage())
    finally:
        sc.handle_challenge_on_page = orig_seam
        cs._challenge_wait_seconds = orig_wait


# ── 4. wire proof: local poller retired, settle call site routes through the seam
def test_local_poller_retired_and_callsite_uses_seam():
    assert "def _settle_through_challenge(" not in SRC, (
        "the retired local title-poll loop must be GONE (superseded by the seam)"
    )
    assert "def _settle_challenge_handoff(" in SRC, "the new seam wrapper must exist"
    # the held-open settle call site must call the new wrapper, not the old poller
    assert "_settle_challenge_handoff(page)" in SRC, (
        "the held-open settle call site must route through _settle_challenge_handoff"
    )
    assert "handle_challenge_on_page" in SRC, (
        "the wrapper must delegate to the canonical session_capture seam"
    )


# ── 5. no-solving static guard over the new wrapper source
def test_no_solving_guard_over_new_wrapper():
    fn_src = inspect.getsource(cs._settle_challenge_handoff)
    forbidden = (".click(", ".fill(", ".type(", ".evaluate(", ".press(",
                 "solve", "captcha_solver", "auto_submit")
    for tok in forbidden:
        assert tok not in fn_src, (
            f"no-solving boundary violated: the wrapper must not '{tok}' -- "
            "detection + passive self-clear + manual handoff only"
        )
    # and it MUST reference the canonical seam (the whole point of the swap)
    assert "handle_challenge_on_page" in fn_src
