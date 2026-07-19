"""T56 — fix L25/L27 + helper semantics for nonexistent systemd units.

Bug (pre-v3.63.3):
  `_systemctl()` returned `ok=True` whenever systemctl ran to
  completion, regardless of rc. `_svc_property()` returned the
  stdout whenever `_systemctl` did. The L25 and L27 checks read
  those values directly and treated systemd's default returns
  (`Restart=no` for a missing unit, `is-active=inactive` for a
  missing unit) as REAL misconfiguration. On a fresh stash deploy
  where the test suite ran BEFORE `install_service.sh` registered
  the unit, the 5 environment-coupled tests in
  test_u41_systemd_live_tests.py failed because the underlying
  checks reported FAIL on a missing unit instead of the WARN that
  the test contract requires.

  Critically, this is what T54 was MEANT to fix but didn't —
  T54 bumped subprocess timeouts but the root cause was the
  ambiguity in systemctl's defaults-for-missing-units behaviour,
  not a timeout. T54 stays in v3.63.3 as a defensible robustness
  improvement, but T56 is the real fix.

Fix (T56):
  - Added `live_tests.checks._unit_exists(name)`. Uses
    `systemctl is-enabled <name>`, which uniquely returns
    `not-found` for missing units (other queries return systemd
    defaults that LOOK like real-unit answers).
  - `_svc_property()` now gates on `_unit_exists` and returns None
    for a missing unit (was: returning the systemd default, e.g.
    'no' for Restart).
  - `l27_survives_logout` gates on `_unit_exists` BEFORE reading
    is-active state (l25_restart_on_crash is fixed transitively
    by the `_svc_property` change — it short-circuits to WARN
    on `restart is None`).
  - `_systemctl()` semantics unchanged: ok=True means "ran and
    reached the bus", NOT "the unit exists". Docstring updated
    to flag the trap.
  - The `tests/test_u41_systemd_live_tests.py` assertions are
    rewritten to a three-branch truth table: (a) no bus, (b) bus
    + no unit, (c) bus + unit — the old two-branch logic
    conflated (a) and (b).

This test file exercises the gate logic explicitly via mocked
subprocess calls so the "bus exists but unit doesn't" case is
covered regardless of where the suite is running. The sandbox
has no bus; stash had bus + no unit during step [2] of the
v3.63.2 capture; the fully-deployed stash has bus + unit. All
three must produce the right answers.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from live_tests import checks  # noqa: E402
from live_tests import harness  # noqa: E402


# ── Helpers / fake subprocess outputs ───────────────────────────────

class _Fake:
    """Stand-in for subprocess.CompletedProcess."""
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _scripted_systemctl(scripts):
    """Build a fake `subprocess.run` that returns scripted answers
    keyed by the subcommand. `scripts` is a dict mapping a tuple of
    args (after `systemctl`) to a `_Fake` instance OR an Exception
    to raise.

    Example:
        scripts = {
          ("is-enabled", "x"):  _Fake(returncode=1, stdout="not-found\\n"),
          ("show", "x", "-p", "Restart", "--no-pager", "--value"):
              _Fake(returncode=0, stdout="no\\n"),
        }
    """
    def _run(cmd, capture_output=False, text=False, timeout=None):
        assert cmd[0] == "systemctl", f"unexpected cmd: {cmd!r}"
        key = tuple(cmd[1:])
        if key not in scripts:
            raise AssertionError(
                f"unscripted systemctl call: {key!r} "
                f"(scripts had: {list(scripts.keys())})")
        outcome = scripts[key]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return _run


# ── _unit_exists ────────────────────────────────────────────────────

def test_unit_exists_present():
    """When `is-enabled` returns anything other than 'not-found',
    the unit exists (enabled, disabled, masked, etc.)."""
    for state in ("enabled", "disabled", "static", "masked"):
        sub = _scripted_systemctl({
            ("is-enabled", "x"): _Fake(returncode=0, stdout=state),
        })
        import subprocess as _sp
        _orig = _sp.run
        try:
            _sp.run = sub
            assert checks._unit_exists("x") is True, (
                f"state={state!r} should be treated as 'exists'")
        finally:
            _sp.run = _orig


def test_unit_exists_not_found():
    """The 'not-found' stdout is the unique missing-unit signal."""
    sub = _scripted_systemctl({
        ("is-enabled", "x"): _Fake(returncode=1, stdout="not-found"),
    })
    import subprocess as _sp
    _orig = _sp.run
    try:
        _sp.run = sub
        assert checks._unit_exists("x") is False
    finally:
        _sp.run = _orig


def test_unit_exists_no_bus_returns_false():
    """If systemctl can't reach the bus, _unit_exists returns
    False (can't confirm; behave as if the unit doesn't exist —
    that's the safe default for the caller's WARN-not-FAIL
    contract)."""
    sub = _scripted_systemctl({
        ("is-enabled", "x"): _Fake(
            returncode=1, stdout="",
            stderr="Failed to connect to bus: No such file or directory"),
    })
    import subprocess as _sp
    _orig = _sp.run
    try:
        _sp.run = sub
        assert checks._unit_exists("x") is False
    finally:
        _sp.run = _orig


# ── _svc_property gating ────────────────────────────────────────────

def test_svc_property_returns_none_for_missing_unit():
    """T56 fix: previously this returned 'no' (systemd default for
    `show <missing> -p Restart`); after T56 it must return None
    so callers don't mis-FAIL."""
    sub = _scripted_systemctl({
        ("is-enabled", "x"): _Fake(returncode=1, stdout="not-found"),
        # show should NOT be called once we know the unit doesn't
        # exist — the gate short-circuits.
    })
    import subprocess as _sp
    _orig = _sp.run
    try:
        _sp.run = sub
        assert checks._svc_property("x", "Restart") is None
    finally:
        _sp.run = _orig


def test_svc_property_returns_value_for_existing_unit():
    sub = _scripted_systemctl({
        ("is-enabled", "x"): _Fake(returncode=0, stdout="enabled"),
        ("show", "x", "-p", "Restart", "--no-pager", "--value"):
            _Fake(returncode=0, stdout="on-failure"),
    })
    import subprocess as _sp
    _orig = _sp.run
    try:
        _sp.run = sub
        assert checks._svc_property("x", "Restart") == "on-failure"
    finally:
        _sp.run = _orig


# ── L25 outcome with mocked helpers ────────────────────────────────

def test_l25_warns_when_unit_is_missing(monkeypatch):
    """The behaviour that motivated T56: L25 must WARN, not FAIL,
    when the bulkdownloader unit is not installed.

    Pre-T56 behaviour: _svc_property returned 'no' (systemd default
    for `show <missing> -p Restart`), L25 saw 'no' and returned
    FAIL with detail 'Restart=no'. Post-T56: _svc_property returns
    None, L25 short-circuits to WARN.
    """
    monkeypatch.setattr(checks, "_unit_exists", lambda name: False)
    # If _svc_property is properly gated, L25 never reaches the
    # show subcommand. Set up _systemctl to fail loudly if it's
    # called for show — but tolerate is-active calls (L25 only
    # reads Restart so it shouldn't even call is-active).
    def _no_show(*a, **k):
        raise AssertionError("L25 should not call systemctl when "
                             "_unit_exists returns False")
    # _svc_property bails before calling _systemctl when
    # _unit_exists is False, so the spy is just defensive.
    monkeypatch.setattr(checks, "_systemctl", _no_show)

    fn = None
    for t in harness.registry():
        if t.id == "L25":
            fn = t.fn
            break
    assert fn is not None, "L25 not registered"

    ctx = harness.Context("http://localhost:1", "/tmp",
                                  disruptive=True)
    level, detail = fn(ctx)
    assert level == harness.WARN, (
        f"L25 returned {level} ({detail}) — must WARN when the "
        "unit doesn't exist, not FAIL")


# ── L27 outcome with mocked helpers ────────────────────────────────

def test_l27_warns_when_unit_is_missing(monkeypatch):
    """L27 used to call is-active on a missing unit, get 'inactive',
    and FAIL. After T56 it gates on _unit_exists FIRST."""
    monkeypatch.setattr(checks, "_unit_exists", lambda name: False)
    # is-active is called first in L27. Make it return what systemd
    # really does for a missing unit on a real bus.
    def _scripted(args, timeout=10):
        if args[:1] == ["is-active"]:
            # systemd returns 'inactive' with rc=3 for missing units
            return (True, "inactive", "")
        raise AssertionError(
            f"L27 should not call _systemctl({args!r}) once it "
            "knows the unit doesn't exist")
    monkeypatch.setattr(checks, "_systemctl", _scripted)

    fn = None
    for t in harness.registry():
        if t.id == "L27":
            fn = t.fn
            break
    assert fn is not None, "L27 not registered"

    ctx = harness.Context("http://localhost:1", "/tmp",
                                  disruptive=True)
    level, detail = fn(ctx)
    assert level == harness.WARN, (
        f"L27 returned {level} ({detail}) — must WARN when the "
        "unit doesn't exist, not FAIL")


# ── Source-level guard ─────────────────────────────────────────────

def test_l27_gates_on_unit_exists_before_reading_inactive_state():
    """Belt-and-braces: the L27 source must reference _unit_exists
    BEFORE its `if out == "active"` / `if out in ("inactive",
    "failed")` blocks, otherwise a future edit could regress."""
    src = (Path(checks.__file__)).read_text(encoding="utf-8")
    # Find the L27 function body.
    start = src.find('def l27_survives_logout')
    assert start >= 0, "l27_survives_logout not found"
    # Find the next def to bound the body.
    end = src.find('\ndef ', start + 1)
    assert end > start
    body = src[start:end]
    # _unit_exists must appear before the `if out in ("inactive",`
    # FAIL branch in the body order.
    ue_pos = body.find("_unit_exists")
    fail_pos = body.find('"inactive"')
    assert ue_pos >= 0, "L27 body does not reference _unit_exists"
    assert fail_pos >= 0
    assert ue_pos < fail_pos, (
        "L27 must gate on _unit_exists BEFORE the inactive-state "
        "FAIL branch (T56). Move the _unit_exists check up.")
