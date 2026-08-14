"""Two live checks read keys their producer never emits, so neither can fail.

BACKLOG ROWS 110 AND 111. Both were found by the 2026-08-13 tracker audit and
both were VERIFIED against the producing code before this file was written --
which matters, because a third finding from the same audit did NOT reproduce
(row 116: an auditing agent imported the modules it was inspecting and then
measured a registry its own probe had filled).

THE SHARED SHAPE, and it is section 0's exactly: a check asserts over a
denominator that structurally cannot contain its subject, so it reports clean --
truthfully, and uselessly. Here the denominator is a dict, and the key the check
reads is not a key the producer writes.

  L29 (vpn-kill-switch-inspectable) filtered active kills with
  `s.get("killed")`. `KillState` is a dataclass whose fields are tunnel_id,
  killed_at, reason, state ("killed"/"cycling"/"cleared") and four more, and
  `to_dict()` is `asdict(self)` -- so the emitted key is `state`, and there is
  no `killed` key at all. `killed_at` is a float timestamp and is NOT what
  `.get("killed")` reads. `active` was therefore always empty, the FAIL branch
  was dead, and the check returned PASS over a genuinely killed tunnel.

  L13 (library-extractor-fastpath) selected eligible rows with
  `m.get("installed") or m.get("library") or m.get("library_installed")`.
  `capture_diag.extractor_matrix()` emits site_id, library, host_pattern,
  adapter and library_installed -- never `installed` -- and `library` is the
  library NAME, a non-empty string on every well-formed row. So the middle
  operand was truthy regardless of whether anything was installed, `eligible`
  always equalled `matrix`, and the one fact the check exists to establish was
  the one it could not observe.

WHAT MADE L29 SURVIVE A TEST SUITE is the part worth keeping. Its own tests
built fixtures as `{"tunnel_id": ..., "killed": True}` -- the test MANUFACTURED
the key the product does not emit, so it exercised a contract nothing produces
and passed. A green battery over a fabricated shape is not evidence about the
real one, which is why every assertion below builds its input by calling the
REAL `KillState.to_dict()` rather than by writing a dict literal.

AND BOTH FIXES ADD A THIRD STATE so this class cannot recur silently. A record
that carries neither the expected key nor a recognisable shape now produces WARN
naming the record, not PASS -- section 0's "unknown is a third state, and it
fails". Without that, the next rename puts the check straight back to blind.
"""
from __future__ import annotations

import contextlib

import live_tests.checks as checks  # noqa: F401 -- import registers the checks
import live_tests.harness as h

from bulk_downloader import vpn_kill_switch as ks

BD_GATE_SCOPE = "module"


def _get(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    raise AssertionError(
        f"BD-GATE-UNRUNNABLE: live check {test_id} is not registered, so every "
        f"assertion in this file would be asserting over nothing")


class _Ctx(h.Context):
    """L29 reads module state directly; L13 needs one GET stubbed."""

    def __init__(self, matrix_body=None):
        super().__init__("http://localhost:1", "/tmp")
        self._matrix_body = matrix_body

    def get(self, path, timeout=15):
        if self._matrix_body is not None:
            return True, 200, self._matrix_body, 1.0
        # An unreachable endpoint, so L29 falls back to reading the in-process
        # module state -- which is the path these tests are about. Returning a
        # failed GET rather than raising, because raising would make the harness
        # report an error instead of exercising the fallback.
        return False, 0, None, 0.0


@contextlib.contextmanager
def _kill_states(states, auto_recover=True):
    """Patch the kill-switch READ API.

    The names are `list_kill_states` / `get_auto_recover` -- read out of
    bulk_downloader/vpn_kill_switch.py rather than guessed. A first draft of
    this helper patched `list_states`, which does not exist; setting an
    attribute that nothing reads patches nothing and the test then measures
    the unpatched module. Section 1: read the callee before you call it.
    """
    assert hasattr(ks, "list_kill_states") and hasattr(ks, "get_auto_recover"), (
        "BD-GATE-UNRUNNABLE: the kill-switch read API was renamed; this helper "
        "would silently patch nothing and every assertion below would measure "
        "the real module")
    real_list, real_auto = ks.list_kill_states, ks.get_auto_recover
    ks.list_kill_states = lambda: states
    ks.get_auto_recover = lambda: auto_recover
    try:
        yield
    finally:
        ks.list_kill_states, ks.get_auto_recover = real_list, real_auto


# ── the producer's real shape, built by the producer ────────────────


def _real_state(tunnel_id="tun-real", state="killed"):
    """Build the dict the PRODUCT emits, via the product's own to_dict().

    Deliberately not a literal. The defect this file closes survived years of
    green tests written as literals carrying a key the dataclass never had.
    """
    st = ks.KillState(tunnel_id=tunnel_id, killed_at=1.0, reason="leak", state=state)
    return st.to_dict()


def test_the_producer_emits_state_and_has_no_killed_key():
    """The premise, asserted before anything depends on it.

    If KillState ever grows a real `killed` key this whole file is arguing
    about a subject that moved, and it should say so rather than quietly
    testing a fix for a defect that no longer exists.
    """
    d = _real_state()
    assert "state" in d, f"KillState.to_dict() lost its `state` key: {sorted(d)}"
    assert "killed" not in d, (
        f"KillState.to_dict() now emits a `killed` key: {sorted(d)}. The row-111 "
        f"defect was that the check read a key the producer did not emit; if the "
        f"producer now emits it, re-derive the row before trusting this file")
    assert d["state"] == "killed"


# ── L29: the FAIL branch must be reachable ──────────────────────────


def test_l29_FAILS_on_a_genuinely_killed_tunnel():
    """RED before the fix: `s.get("killed")` is None for the real shape, so
    `active` was empty and this returned PASS on a killed tunnel."""
    with _kill_states([_real_state("tun-killed", "killed")]):
        level, detail = _get("L29").fn(_Ctx())
    assert level == h.FAIL, (
        f"L29 returned {level} for a tunnel whose state is 'killed'. That is the "
        f"row-111 defect: the check cannot see the only condition it exists to "
        f"report. detail={detail!r}")
    assert "tun-killed" in detail


def test_l29_FAILS_on_a_cycling_tunnel_because_the_tunnel_is_still_down():
    """`cycling` means killed and mid-recovery -- the tunnel is not carrying
    traffic. vpn_kill_switch reverts it to `killed` when the cycle does not
    clear the leak, so treating it as healthy would report OK for a box whose
    VPN is down."""
    with _kill_states([_real_state("tun-cycling", "cycling")]):
        level, detail = _get("L29").fn(_Ctx())
    assert level == h.FAIL, f"cycling read as healthy: {detail!r}"
    assert "tun-cycling" in detail


def test_l29_PASSES_on_a_cleared_tunnel():
    """The over-sensitivity control. A fix that called every state active would
    satisfy the two assertions above and destroy the check -- section 0 says
    over-sensitivity is a soundness bug, not a safe default."""
    with _kill_states([_real_state("tun-cleared", "cleared")]):
        level, detail = _get("L29").fn(_Ctx())
    assert level == h.PASS, f"a CLEARED state read as an active kill: {detail!r}"
    assert "0 active kills" in detail


def test_l29_PASSES_when_there_are_no_states_at_all():
    with _kill_states([]):
        level, detail = _get("L29").fn(_Ctx())
    assert level == h.PASS
    assert "0 active kills" in detail


def test_l29_WARNS_rather_than_passing_on_a_record_with_no_state_key():
    """Unknown is a third state, and it must not read as healthy.

    This is what stops the defect recurring: if `state` is ever renamed, the
    check reports that it cannot judge instead of silently returning PASS the
    way `s.get("killed")` did for years.
    """
    with _kill_states([{"tunnel_id": "tun-malformed", "killed_at": 1.0}]):
        level, detail = _get("L29").fn(_Ctx())
    assert level == h.WARN, (
        f"a kill-state record carrying no `state` key produced {level}, not WARN. "
        f"A check that cannot read its subject must say so: {detail!r}")
    assert "tun-malformed" in detail


# ── L13: the eligibility predicate must read installedness ──────────


def _matrix(rows):
    return {"tool": "extractor_matrix", "ok": True, "extractors": rows}


def _row(site_id, library="somelib", installed=False):
    """The real emitted shape from capture_diag.extractor_matrix()."""
    return {"site_id": site_id, "library": library,
            "host_pattern": ".*", "adapter": "a", "library_installed": installed}


def test_l13_WARNS_when_no_library_is_actually_installed():
    """RED before the fix: `library` is a non-empty name on every row, so the
    OR was constant-true, `eligible` always equalled `matrix`, and this
    reported 'N of N eligible' for a box with nothing installed."""
    body = _matrix([_row("a", installed=False), _row("b", installed=False)])
    level, detail = _get("L13").fn(_Ctx(matrix_body=body))
    assert level == h.WARN, (
        f"L13 returned {level} for a matrix in which NO library is installed. "
        f"That is the row-110 defect -- the eligibility predicate is satisfied "
        f"by the library NAME, so it cannot observe installedness. detail={detail!r}")


def test_l13_PASSES_when_a_library_is_installed():
    """The over-sensitivity control: the fix must not simply always warn."""
    body = _matrix([_row("a", installed=True), _row("b", installed=False)])
    level, detail = _get("L13").fn(_Ctx(matrix_body=body))
    assert level == h.PASS, f"an installed extractor was not counted: {detail!r}"
    assert "1 of 2" in detail, (
        f"the count must be installed-of-total, not total-of-total: {detail!r}")


def test_l13_counts_only_installed_rows_not_every_row():
    body = _matrix([_row(s, installed=(s == "c")) for s in ("a", "b", "c", "d")])
    level, detail = _get("L13").fn(_Ctx(matrix_body=body))
    assert level == h.PASS
    assert "1 of 4" in detail, (
        f"expected 1 of 4 eligible, got {detail!r} -- if this says 4 of 4 the "
        f"predicate is still counting rows rather than installed libraries")


def test_l13_WARNS_on_an_empty_matrix():
    level, detail = _get("L13").fn(_Ctx(matrix_body=_matrix([])))
    assert level == h.WARN
    assert "empty" in detail.lower()
