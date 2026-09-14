"""Row 802: THREE-BD-BANDCHECK-TESTS-ALLOW-DUPLICATE-MESSAGES.

tests/test_row677_bandcheck_missing_is_unknown.py checked its five findings
with `needle in output`. `in` does not care how many times the needle occurs,
so a bd-bandcheck regression that prints a named finding twice -- the same line
emitted for two reasons, or a duplicated block -- still satisfies `in`, and the
row677 tests stay green while the tool's output shape has changed underneath
them.

This file is the pin for row 802's fix (each `in` replaced by
`output.count(needle) == 1`). It drives the REAL row677 test functions, not a
rewritten copy, so there is exactly one place the assertion text lives:

  * one test per NEEDLE (five of them) that duplicates ONLY that needle and
    requires the owning row677 test to fail naming it -- so reverting any ONE
    of the five counts back to `in` is caught, which a whole-output probe
    cannot do (the first surviving count assertion masks every later one);
  * one test per row677 FUNCTION (three of them) for the whole-output-doubled
    shape row 802 names;
  * a negative control: on the real, undoubled output every row677 test still
    passes, so `== 1` is not merely a stricter assertion that fails on
    everything.

BD_GATE_SCOPE is "module" for the same reason row677's own file is: this gate
judges that one test module's assertions, not a repo-wide invariant, so it is
not a shard member. See CLAUDE.md "repo-wide/safety gates must be directly
present in a shard".
"""
from __future__ import annotations

import subprocess

import pytest

import test_row677_bandcheck_missing_is_unknown as _row677


BD_GATE_SCOPE = "module"


class _FakeResult:
    """Stands in for subprocess.CompletedProcess with a rewritten stdout."""

    def __init__(self, stdout: str, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


# (needle asserted by row677, a verbatim fragment of the source line that
#  asserts it, the row677 test function that owns it, the bd-bandcheck targets
#  that produce it).
_MISSING_NEEDLE = f"MISSING '{_row677._MISSING}'"
_CASES = (
    ("missing-path", _MISSING_NEEDLE, "output.count(f\"MISSING '{_MISSING}'\")",
     "test_missing_target_is_unknown_and_names_unmeasurable_input", (_row677._MISSING,)),
    ("could-not-measure", "could not be measured", 'output.count("could not be measured")',
     "test_missing_target_is_unknown_and_names_unmeasurable_input", (_row677._MISSING,)),
    ("unsafe", "UNSAFE", 'output.count("UNSAFE")',
     "test_unsafe_directory_remains_a_measured_finding", ("tests/",)),
    ("whole-tests-dir", "whole tests/ dir", 'output.count("whole tests/ dir")',
     "test_unsafe_directory_remains_a_measured_finding", ("tests/",)),
    ("all-safe", "all targets safe to band", 'output.count("all targets safe to band")',
     "test_present_safe_file_remains_safe", ("tests/test_toolchain_534.py",)),
)

_REAL: dict[tuple[str, ...], subprocess.CompletedProcess] = {}


def _real(targets: tuple[str, ...]) -> subprocess.CompletedProcess:
    """Run the real bd-bandcheck once per target set and cache it."""
    if targets not in _REAL:
        _REAL[targets] = _row677._run(*targets)
    return _REAL[targets]


def _drive(monkeypatch, func_name: str, targets: tuple[str, ...], stdout: str):
    """Run the named row677 test with row677's own runner returning `stdout`.

    Patches row677's `_run` helper -- not the shared `subprocess` module -- so
    nothing outside this call sees a rewritten runner.
    """
    real = _real(targets)
    monkeypatch.setattr(
        _row677, "_run", lambda *a, **kw: _FakeResult(stdout, real.returncode)
    )
    getattr(_row677, func_name)()


def test_row802_pins_every_count_row677_asserts():
    """Exact-count denominator, derived from row677's SOURCE, not typed here.

    If row677 grows a sixth `output.count(...)` assertion and no case is added
    below, this fails -- an unpinned branch cannot arrive unnoticed.
    """
    src = _row677.__file__ and open(_row677.__file__, encoding="utf-8").read()
    found = src.count("output.count(")
    assert found == 5, f"row677 asserts {found} counts; {len(_CASES)} are pinned here"
    assert len(_CASES) == 5
    for label, _needle, fragment, _func, _targets in _CASES:
        assert fragment in src, f"case {label}: row677 no longer asserts {fragment!r}"


@pytest.mark.parametrize("label,needle,fragment,func_name,targets", _CASES,
                         ids=[c[0] for c in _CASES])
def test_row677_assertion_catches_one_duplicated_needle(
    monkeypatch, label, needle, fragment, func_name, targets
):
    """RED on the unpatched row677 (`needle in output`): duplicating the needle
    leaves `in` True, the row677 test does not raise, and pytest.raises reports
    "DID NOT RAISE <class 'AssertionError'>". GREEN once row677 counts: 2 != 1.

    Only THIS needle is duplicated, so the failure can only come from THIS
    assertion -- that is what makes each of the five a separately pinned branch.
    """
    real = _real(targets)
    before = real.stdout.count(needle)
    assert before == 1, (
        f"fixture precondition failed: {needle!r} occurs {before}x in the real "
        f"bd-bandcheck output for {targets}, not once -- this probe cannot show "
        "anything about duplication starting from that shape."
    )
    doubled = real.stdout + "\n" + needle
    assert doubled.count(needle) == 2, "fixture did not build a doubled needle"
    for _l, other, _f, _fn, other_targets in _CASES:
        if other == needle or other_targets != targets:
            continue
        assert doubled.count(other) == real.stdout.count(other), (
            f"duplicating {needle!r} also changed the count of {other!r}; the "
            "probe would not isolate a single assertion"
        )

    with pytest.raises(AssertionError) as exc:
        _drive(monkeypatch, func_name, targets, doubled)
    # Distinctive diagnostic: row677's preconditions and its returncode check
    # raise AssertionError too. The message must carry the doubled output, so
    # this proves the COUNT assertion fired and not a neighbour.
    assert needle in str(exc.value), str(exc.value)


@pytest.mark.parametrize(
    "func_name,targets",
    [("test_missing_target_is_unknown_and_names_unmeasurable_input", (_row677._MISSING,)),
     ("test_unsafe_directory_remains_a_measured_finding", ("tests/",)),
     ("test_present_safe_file_remains_safe", ("tests/test_toolchain_534.py",))],
)
def test_row677_assertion_catches_a_wholly_doubled_output(monkeypatch, func_name, targets):
    """Row 802's named shape: the entire bd-bandcheck output emitted twice."""
    real = _real(targets)
    doubled = real.stdout + real.stdout
    assert doubled.count("\n") > real.stdout.count("\n") > 0, "fixture built no doubled output"
    with pytest.raises(AssertionError):
        _drive(monkeypatch, func_name, targets, doubled)


@pytest.mark.parametrize(
    "func_name,targets",
    [("test_missing_target_is_unknown_and_names_unmeasurable_input", (_row677._MISSING,)),
     ("test_unsafe_directory_remains_a_measured_finding", ("tests/",)),
     ("test_present_safe_file_remains_safe", ("tests/test_toolchain_534.py",))],
)
def test_negative_control_real_undoubled_output_still_passes(monkeypatch, func_name, targets):
    """NEGATIVE CONTROL. Driven with the REAL output (nothing duplicated), every
    row677 test must still pass. Without this, a row677 file whose assertions
    were `== 0`, or broken outright, would satisfy every raises() test above.
    """
    real = _real(targets)
    _drive(monkeypatch, func_name, targets, real.stdout)
