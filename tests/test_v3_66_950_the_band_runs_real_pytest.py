"""v3.66.950 -- the band tool CLAUDE.md mandates was running a pytest STUB.

MEASURED, NOT INFERRED. `bd-band` and `bd-parband` invoke `run_tests.py`, whose
`run_tests_core` installs a pytest shim into `sys.modules` whenever `pytest` is
not ALREADY imported -- which at runner startup it never is. So the stub runs
even where real pytest is installed, and test files doing `import pytest` get
the shim. Its own docstring says what it is:

    "Minimal pytest-compatible runner... Re-implements just enough of the
     pytest discovery + fixture protocol... NOT a replacement for pytest in
     production -- use `pytest tests/` there. This exists to catch authoring
     mistakes during development without internet access for pip install."

Measured at v3.66.949 across the whole suite, per-file isolation on both sides:

    stub (bd-fullsuite -> run_tests.py)  : 28 files non-PASS
    real pytest, each file in isolation  : 24 of those 28 PASS -- 24/24 verified
    -> 86% of what bd-band reports is manufactured

One file, both runners, same tree and interpreter:

    tests/test_codex_handoff_stays_retired.py
      real pytest   -> 4 passed
      run_tests.py  -> FAIL  IMPORT ERROR: No module named 'tracked_source'

The stub does not put `tests/` on `sys.path` the way pytest's rootdir handling
does, so the 22 test files importing a sibling helper (`tracked_source`,
`scan_wait`, `shell_source`, `capture_lanes`, `_env`) all fail on import. That
is the floor, not the total -- other divergence mechanisms account for the rest.

A RETRACTION, because it was stated before it was checked: this cut was first
argued on "the stub also HIDES a real failure", from
`test_t14_vpn_probe_egress` failing under pytest but passing under the stub.
It does not. That file passes in ISOLATION under both runners and fails only in
a co-batched xdist run -- the comparison was a per-file-isolated stub run
against a co-batched pytest run, and blamed the runner for an isolation
difference. The case rests on the 24, which are measured.

THE STUB BRANCH WAS ALREADY UNREACHABLE BY INTENT. `bd-band` refuses to run at
all unless the interpreter can import pytest (`sec.resolve_test_interpreter`
returning None is EXIT_CANNOT_EVALUATE), on the reasoning that a runner without
pytest "would report failures that are interpreter artifacts, not defects".
That reasoning is right and its conclusion was half-applied: having proven
pytest is importable, the tool then ran the shim anyway.

AND THE SWAP DELETES CODE RATHER THAN ADDING IT. @897 had to detect "nothing
ran" by string-matching an UNEVALUABLE banner, because the stub prints a
reassuring `Total: 0 | Failed: 0` beside it and grading on `Failed:` made a real
failure and a suite that ran nothing indistinguishable. pytest reports that
state as EXIT CODE 5. The third state survives -- it is now read from a number
that cannot drift instead of from prose that can.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PY = Path(sys.executable)
_BAND = _REPO / "toolchain" / "bin" / "bd-band"
_PARBAND = _REPO / "toolchain" / "bin" / "bd-parband"

# The file the divergence was measured on: 4 passed under pytest, IMPORT ERROR
# under the stub. Fast, and it is real repo content rather than a fixture that
# could drift away from the thing it demonstrates.
_DIVERGENT = "tests/test_codex_handoff_stays_retired.py"


def _load(path: Path):
    spec = importlib.util.spec_from_loader(
        "_bd_" + path.name.replace("-", "_"),
        importlib.machinery.SourceFileLoader("_bd_" + path.name.replace("-", "_"), str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _code_only(path: Path) -> str:
    """Source with comment lines removed.

    A COMMENT IS INSIDE THE DENOMINATOR OF EVERY GATE THAT READS SOURCE TEXT
    (CLAUDE.md section 0, four recorded instances). The tools below will keep
    explaining WHY they no longer call run_tests.py, and that explanation names
    run_tests.py -- so a raw text search would read the explanation as the
    offence and fail a correct implementation.
    """
    return "\n".join(l for l in path.read_text("utf-8").splitlines()
                     if not l.lstrip().startswith("#"))


# ── the grading, tested directly ─────────────────────────────────────────────

def test_the_exit_code_grading_actually_discriminates():
    """Positive control for grade_pytest_rc, before anything depends on it.

    Extracted into a named function on purpose. @944's battery escaped once
    because a verdict readable only through the test being mutated has no
    detector; @939's did the same. The three states pytest mints are graded
    here, in the open.
    """
    band = _load(_BAND)
    g = band.grade_pytest_rc
    assert g(0) == "pass", "exit 0 is every test passing"
    assert g(1) == "fail", "exit 1 is a real test failure"
    assert g(5) == "nothing-ran", (
        "pytest exits 5 for 'no tests collected'. NOTHING RAN must stay its own "
        "state -- test_toolchain_534's @860 guard exists because zero-collect "
        "used to grade green, and excusing it here would undo that.")
    for rc in (2, 3, 4, 137):
        assert g(rc) == "fail", (
            f"exit {rc} graded {g(rc)!r}; an interrupted, internally-broken, "
            f"misinvoked or KILLED run has proven nothing and must not read as "
            f"a pass")


def test_nothing_ran_is_not_green():
    """The state @897 had to reverse-engineer out of the stub's banner."""
    band = _load(_BAND)
    assert band.grade_pytest_rc(5) != "pass"


# ── the tools no longer route through the stub ───────────────────────────────

@pytest.mark.parametrize("tool", [_BAND, _PARBAND], ids=lambda p: p.name)
def test_the_band_tools_invoke_real_pytest(tool):
    """RED on pristine: both shell out to run_tests.py."""
    code = _code_only(tool)
    assert re.search(r'["\']-m["\']\s*,\s*["\']pytest["\']', code), (
        f"{tool.name} does not invoke real pytest. It runs the run_tests_core "
        f"shim, which manufactured 24 of the 28 non-PASS verdicts measured "
        f"across the suite at v3.66.949.")
    assert "run_tests.py" not in code, (
        f"{tool.name} still shells out to run_tests.py in CODE (comments are "
        f"stripped before this check, so an explanation of the change is fine "
        f"and an actual call is not).")


def test_the_interpreter_guard_survives():
    """The direction that must NOT be lost.

    Both tools refuse to band on an interpreter that cannot import pytest --
    "failures that are interpreter artifacts, not defects". Swapping the runner
    makes that guard MORE load-bearing, not less: with the stub gone there is no
    fallback at all, so a missing pytest must still refuse rather than crash.
    """
    for tool in (_BAND, _PARBAND):
        code = _code_only(tool)
        assert "resolve_test_interpreter" in code or "import pytest" in code, (
            f"{tool.name} lost its interpreter guard")


# ── behaviour, on the file the divergence was measured on ────────────────────

def test_bd_band_passes_the_file_the_stub_failed():
    """RED on pristine: bd-band reports FAIL on a file real pytest passes.

    The strongest single assertion in this file, and deliberately real repo
    content rather than a synthetic fixture -- a fixture can drift away from the
    behaviour it was written to demonstrate, and this one cannot: if the file
    ever genuinely breaks, the band SHOULD go red and this test should be
    re-derived rather than patched.
    """
    proc = subprocess.run(
        [str(_PY), str(_BAND), _DIVERGENT],
        cwd=str(_REPO), capture_output=True, text=True, timeout=600)
    blob = proc.stdout + proc.stderr
    assert "IMPORT ERROR" not in blob, (
        "bd-band still reports an IMPORT ERROR on a file that real pytest "
        "passes 4/4 -- the stub does not put tests/ on sys.path, so the 22 "
        "files importing a sibling helper all fail on import:\n" + blob[-1500:])
    assert proc.returncode == 0, (
        f"bd-band graded {_DIVERGENT} non-green; real pytest passes it 4/4 in "
        f"the same tree with the same interpreter:\n" + blob[-1500:])


def test_the_behavioural_check_is_not_vacuous():
    """The control for the test above.

    If the divergent file stopped passing under real pytest, the assertion above
    would be measuring nothing and would fail for a reason that has nothing to
    do with bd-band. Pin the premise separately so the two failures are
    distinguishable.
    """
    proc = subprocess.run(
        [str(_PY), "-m", "pytest", _DIVERGENT, "-q", "-p", "no:randomly"],
        cwd=str(_REPO), capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, (
        f"{_DIVERGENT} no longer passes under real pytest, so the divergence "
        f"test above proves nothing. Re-derive the pair before editing either:\n"
        + (proc.stdout + proc.stderr)[-1200:])
