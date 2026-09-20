"""Row 802: THREE-BD-BANDCHECK-TESTS-ALLOW-DUPLICATE-MESSAGES.

tests/test_row677_bandcheck_missing_is_unknown.py checked its findings
with `needle in output`. `in` does not care how many times the needle occurs,
so a bd-bandcheck regression that prints a named finding twice -- the same line
emitted for two reasons, or a duplicated block -- still satisfies `in`, and the
row677 tests stay green while the tool's output shape has changed underneath
them.

This file is the pin for row 802's fix (each `in` replaced by
`output.count(needle) == 1`). It drives the REAL row677 test functions, not a
rewritten copy, so there is exactly one place the assertion text lives:

  * one test per NEEDLE (ten of them) that duplicates ONLY that needle and
    requires the owning row677 test to fail naming it -- so reverting any ONE
    of the counts back to `in` is caught, which a whole-output probe
    cannot do (the first surviving count assertion masks every later one);
  * one test per row677 FUNCTION (five of them) for the whole-output-doubled
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
#  that produce it, the count row677 expects, the `_run` keyword arguments).
# One entry per `output.count(` assertion in row677 -- ten since the
# row677-cx-d rebase added two functions (measured-failure-dominates, bd-band
# caller) with five more counts, one of them `== 0`.
_MISSING_NEEDLE = f"MISSING '{_row677._MISSING}'"
_MISSING_FRAG = "output.count(f\"MISSING '{_MISSING}'\")"
_T_MISSING = (_row677._MISSING,)
_T_UNSAFE = ("tests/",)
_T_SAFE = ("tests/test_toolchain_534.py",)
_T_BOTH = (_row677._MISSING, "tests/")
_CASES = (
    ("missing-path", _MISSING_NEEDLE, _MISSING_FRAG,
     "test_missing_target_is_unknown_and_names_unmeasurable_input", _T_MISSING, 1, {}),
    ("could-not-measure", "could not be measured", 'output.count("could not be measured")',
     "test_missing_target_is_unknown_and_names_unmeasurable_input", _T_MISSING, 1, {}),
    ("unsafe", "UNSAFE", 'output.count("UNSAFE")',
     "test_unsafe_directory_remains_a_measured_finding", _T_UNSAFE, 1, {}),
    ("whole-tests-dir", "whole tests/ dir", 'output.count("whole tests/ dir")',
     "test_unsafe_directory_remains_a_measured_finding", _T_UNSAFE, 1, {}),
    ("all-safe", "all targets safe to band", 'output.count("all targets safe to band")',
     "test_present_safe_file_remains_safe", _T_SAFE, 1, {}),
    ("both-missing-path", _MISSING_NEEDLE, _MISSING_FRAG,
     "test_measured_failure_dominates_a_missing_target", _T_BOTH, 1, {}),
    ("both-unsafe", "UNSAFE", 'output.count("UNSAFE")',
     "test_measured_failure_dominates_a_missing_target", _T_BOTH, 1, {}),
    ("both-fix-flagged", "fix the flagged targets", 'output.count("fix the flagged targets")',
     "test_measured_failure_dominates_a_missing_target", _T_BOTH, 1, {}),
    ("both-no-could-not-measure", "could not be measured", 'output.count("could not be measured") == 0',
     "test_measured_failure_dominates_a_missing_target", _T_BOTH, 0, {}),
    ("bd-band-missing-path", _MISSING_NEEDLE, _MISSING_FRAG,
     "test_bd_band_caller_preserves_the_missing_target_diagnostic", _T_MISSING, 1,
     {"tool": _row677._BAND}),
)


def _key(targets, kwargs):
    return targets + tuple(sorted((k, str(v)) for k, v in kwargs.items()))


_REAL: dict[tuple, subprocess.CompletedProcess] = {}


def _real(targets: tuple[str, ...], kwargs: dict | None = None) -> subprocess.CompletedProcess:
    """Run the real bd-bandcheck (or bd-band) once per target set and cache it."""
    kwargs = kwargs or {}
    key = _key(targets, kwargs)
    if key not in _REAL:
        _REAL[key] = _row677._run(*targets, **kwargs)
    return _REAL[key]


def _text(real: subprocess.CompletedProcess) -> str:
    """What row677 asserts on: stdout + stderr (bd-band's MISSING diagnostic
    travels on stderr). The fake result carries it all as stdout."""
    return real.stdout + real.stderr


def _drive(monkeypatch, func_name: str, targets: tuple[str, ...], stdout: str, kwargs: dict | None = None):
    """Run the named row677 test with row677's own runner returning `stdout`.

    Patches row677's `_run` helper -- not the shared `subprocess` module -- so
    nothing outside this call sees a rewritten runner.
    """
    real = _real(targets, kwargs)
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
    assert found == 10, f"row677 asserts {found} counts; {len(_CASES)} are pinned here"
    assert len(_CASES) == 10
    for label, _needle, fragment, _func, _targets, _expected, _kw in _CASES:
        assert fragment in src, f"case {label}: row677 no longer asserts {fragment!r}"


@pytest.mark.parametrize("label,needle,fragment,func_name,targets,expected,kwargs", _CASES,
                         ids=[c[0] for c in _CASES])
def test_row677_assertion_catches_one_duplicated_needle(
    monkeypatch, label, needle, fragment, func_name, targets, expected, kwargs
):
    """RED on the unpatched row677 (`needle in output`): duplicating the needle
    leaves `in` True, the row677 test does not raise, and pytest.raises reports
    "DID NOT RAISE <class 'AssertionError'>". GREEN once row677 counts: 2 != 1.

    Only THIS needle is duplicated, so the failure can only come from THIS
    assertion -- that is what makes each of the five a separately pinned branch.
    """
    real = _real(targets, kwargs)
    before = _text(real).count(needle)
    assert before == expected, (
        f"fixture precondition failed: {needle!r} occurs {before}x in the real "
        f"output for {targets} {kwargs}, not {expected}x -- this probe cannot show "
        "anything about duplication starting from that shape."
    )
    # one more occurrence than row677 expects (for an `== 0` assertion that
    # is the needle appearing at all)
    doubled = _text(real) + "\n" + needle
    assert doubled.count(needle) == expected + 1, "fixture did not build a doubled needle"
    for _l, other, _f, _fn, other_targets, _e, other_kw in _CASES:
        if other == needle or other_targets != targets or other_kw != kwargs:
            continue
        assert doubled.count(other) == _text(real).count(other), (
            f"duplicating {needle!r} also changed the count of {other!r}; the "
            "probe would not isolate a single assertion"
        )

    with pytest.raises(AssertionError) as exc:
        _drive(monkeypatch, func_name, targets, doubled, kwargs)
    # Distinctive diagnostic: row677's preconditions and its returncode check
    # raise AssertionError too. The message must carry the doubled output, so
    # this proves the COUNT assertion fired and not a neighbour.
    assert needle in str(exc.value), str(exc.value)


# every row677 function that asserts counts, with the runner arguments it uses
_FUNCS = [
    ("test_missing_target_is_unknown_and_names_unmeasurable_input", _T_MISSING, {}),
    ("test_unsafe_directory_remains_a_measured_finding", _T_UNSAFE, {}),
    ("test_present_safe_file_remains_safe", _T_SAFE, {}),
    ("test_measured_failure_dominates_a_missing_target", _T_BOTH, {}),
    ("test_bd_band_caller_preserves_the_missing_target_diagnostic", _T_MISSING, {"tool": _row677._BAND}),
]


def test_row802_pins_every_row677_function_that_counts():
    src = open(_row677.__file__, encoding="utf-8").read()
    counting = {f for f, _t, _e, _kw in
                ((c[3], c[4], c[5], c[6]) for c in _CASES)}
    assert counting == {f for f, _t, _kw in _FUNCS}
    for func, _t, _kw in _FUNCS:
        assert f"def {func}(" in src


@pytest.mark.parametrize("func_name,targets,kwargs", _FUNCS, ids=[f[0] for f in _FUNCS])
def test_row677_assertion_catches_a_wholly_doubled_output(monkeypatch, func_name, targets, kwargs):
    """Row 802's named shape: the entire bd-bandcheck output emitted twice."""
    real = _real(targets, kwargs)
    doubled = _text(real) + _text(real)
    assert doubled.count("\n") > _text(real).count("\n") > 0, "fixture built no doubled output"
    with pytest.raises(AssertionError):
        _drive(monkeypatch, func_name, targets, doubled, kwargs)


@pytest.mark.parametrize("func_name,targets,kwargs", _FUNCS, ids=[f[0] for f in _FUNCS])
def test_negative_control_real_undoubled_output_still_passes(monkeypatch, func_name, targets, kwargs):
    """NEGATIVE CONTROL. Driven with the REAL output (nothing duplicated), every
    row677 test must still pass. Without this, a row677 file whose assertions
    were `== 0`, or broken outright, would satisfy every raises() test above.
    """
    real = _real(targets, kwargs)
    _drive(monkeypatch, func_name, targets, _text(real), kwargs)
