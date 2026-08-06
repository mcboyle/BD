"""Batch B: a wired gate must REFUSE rather than pass when it cannot see.

ONE INVARIANT, FOUR TOOLS. `bd-opv`, `bd-equiv`, `bd-env-report-check` and
`bd-fullsuite` were each found reporting a verdict they had not earned. The
shapes differ -- a crashed check folded into a benign bucket, a dead subprocess
read as "produced nothing", a decisive field missing while an advisory one stood
in for it, an environment-looking line excusing a real failure -- but the
invariant is one: **when the denominator cannot contain the subject, say so and
fail; do not report clean.**

THREE OF THE FOUR WERE ALREADY FIXED AT v3.66.871, AND THAT IS WHY THIS FILE
EXISTS. Re-derived by running each tool rather than by reading its comments,
because a comment claiming a fix is exactly what satisfies an assertion written
to test for one. What was missing was any test pinning the three repairs, so
nothing would have caught them regressing. The parametrized cases below are that
pin; only the bd-fullsuite case was RED when this file was written.

THE RESIDUAL DEFECT IS NARROWER THAN THE ONE ORIGINALLY FILED, and the
difference matters enough to record. The register described "one env-looking line
anywhere in a file's output suppresses every genuine failure in that file".
Measured on this tree, @871's per-test segmentation already handles that: an env
line BEFORE the failing marker grades REAL correctly. What still breaks is that
the LAST failing block runs to end-of-output, so anything printed AFTER the final
FAIL row -- a teardown warning, an atexit message, a shutdown-time GTK warning --
is absorbed into that test's text and excuses it. Same consequence, different
mechanism, and a fix aimed at the filed description would have missed it.

THE OVER-SENSITIVE DIRECTION IS PINNED TOO, deliberately. run_tests_core emits
`print(f"  FAIL  {n}")` then `print(f"          {short}")` where short is the
last THREE error lines joined -- so only the FIRST excerpt line is indented and
the other two sit at column 0. Bounding a block by indentation would therefore
cut a genuine GTK traceback whose identifying line is second or third, grading a
real environment failure as a code defect and failing a healthy box. A gate that
cries wolf gets switched off, so that direction is a test, not a footnote.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "toolchain" / "bin"
PYTHON = sys.executable

_GTK = "Namespace Gtk not available"
_ASSERT = "E   AssertionError: prune should report 1 removed, got 7"


def _tool(name: str) -> Path:
    path = BIN / name
    assert path.is_file(), f"{path} does not exist -- a nonzero exit would prove nothing"
    return path


def _run(args, **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PYTHON, *[str(a) for a in args]],
                          cwd=str(REPO_ROOT), capture_output=True, text=True,
                          timeout=300, **kw)


# --------------------------------------------------------------------------
# bd-env-report-check: a report with no VERSION cannot be dated, and an
# advisory commit must not stand in for the decisive field.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("label,provenance", [
    ("version-unknown-commit-present",
     "generated_against_version=UNKNOWN\ngenerated_against_commit=3fded70208ed\n"),
    ("version-absent-commit-present",
     "generated_against_commit=deadbeefdead\n"),
])
def test_env_report_check_refuses_without_the_decisive_field(
    tmp_path, label, provenance
) -> None:
    report = tmp_path / f"{label}.md"
    report.write_text(f"# env report\n{provenance}", encoding="utf-8")

    done = _run([_tool("bd-env-report-check"), "--tree", REPO_ROOT,
                 "--report", report])
    combined = done.stdout + done.stderr

    assert done.returncode != 0, (
        f"a report that cannot be dated was graded clean: {combined!r}"
    )
    assert "UNKNOWN" in combined, (
        f"the refusal must say it cannot tell, not merely fail: {combined!r}"
    )
    assert "FRESH" not in combined, (
        "printing the TREE's version as though the report had asserted it is "
        f"how the stale value went unnoticed: {combined!r}"
    )


# --------------------------------------------------------------------------
# bd-equiv: a tool that DIED produced no tokens, and "new >= nothing" is
# vacuously true. This is the tool that authorizes deletion.
# --------------------------------------------------------------------------
def test_equiv_refuses_when_a_compared_tool_crashes(tmp_path) -> None:
    old = tmp_path / "old_t.py"
    old.write_text(
        "import sys\n"
        'if "B" in sys.argv:\n'
        '    raise RuntimeError("the old tool died")\n'
        'print("tests/test_alpha.py::test_alpha")\n',
        encoding="utf-8")
    new = tmp_path / "new_t.py"
    new.write_text(
        'print("tests/test_alpha.py::test_alpha")\n'
        'print("tests/test_beta.py::test_beta")\n',
        encoding="utf-8")

    done = _run([_tool("bd-equiv"), "--old", old, "--new", new,
                 "--inputs", "A", "B", "--work", tmp_path, "--json"])

    assert done.returncode != 0, (
        f"a crashed comparison was graded clean: {done.stdout!r}"
    )
    try:
        body = json.loads(done.stdout)
    except ValueError:
        pytest.fail(f"--json did not emit JSON: {done.stdout!r}")
    assert body.get("errored") is True, (
        f"errored=False while the old tool crashed: {body!r}"
    )
    assert body.get("verdict") != "SUPERSET", (
        "a crash must not read as agreement -- this verdict authorizes "
        f"deleting the old tool: {body!r}"
    )


# --------------------------------------------------------------------------
# bd-opv: a check that RAISED is not a check whose precondition was absent.
# --------------------------------------------------------------------------
def test_opv_refuses_when_a_check_itself_is_broken(tmp_path) -> None:
    probe = tmp_path / "opvprobe.py"
    probe.write_text(
        "import importlib.machinery, importlib.util, sys\n"
        f"p = {str(_tool('bd-opv'))!r}\n"
        "spec = importlib.util.spec_from_loader('bdopv',\n"
        "    importlib.machinery.SourceFileLoader('bdopv', p))\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['bdopv'] = m\n"
        "spec.loader.exec_module(m)\n"
        "def boom(): raise ZeroDivisionError('the check itself is broken')\n"
        "def good(): return m.PASS, 'a real pass'\n"
        "m.REGISTRY = [('OPV-BOOM', 'sandbox', boom),\n"
        "              ('OPV-GOOD', 'sandbox', good)]\n"
        "sys.argv = ['bd-opv']\n"
        "raise SystemExit(m.main())\n",
        encoding="utf-8")

    done = _run([probe])
    combined = done.stdout + done.stderr

    assert done.returncode != 0, (
        "one unrelated PASS cleared the guard while a check was broken: "
        f"{combined!r}"
    )
    assert "CANNOT-EVALUATE" in combined, (
        f"a raised check must be distinguishable from an absent precondition: "
        f"{combined!r}"
    )


# --------------------------------------------------------------------------
# bd-fullsuite: an env-looking line must not excuse a genuine failure --
# and a genuine env failure must still be excused.
# --------------------------------------------------------------------------
def _fullsuite_tree(root: Path) -> Path:
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    link = root / "venv" / "bin" / "python"
    if not link.exists():
        link.symlink_to(PYTHON)
    (root / "run_tests.py").write_text(
        "import sys\n"
        'f = sys.argv[1] if len(sys.argv) > 1 else ""\n'
        f"GTK = {_GTK!r}\n"
        f"ASSERT = {_ASSERT!r}\n"
        'if "envfirst" in f:\n'
        "    print(GTK)\n"
        '    print("  FAIL  test_prune_counts"); print("          " + ASSERT)\n'
        'elif "envtail" in f:\n'
        '    print("  FAIL  test_prune_counts"); print("          " + ASSERT)\n'
        '    print("Total: 2 | Passed: 1 | Failed: 1 | Skipped: 0")\n'
        "    print(GTK)\n"
        "    sys.exit(1)\n"
        'elif "genuineenv" in f:\n'
        '    print("  FAIL  test_imports_gtk")\n'
        '    print("          Traceback (most recent call last):")\n'
        '    print("  File \\"x.py\\", line 1, in <module>")\n'
        "    print(GTK)\n"
        '    print("Total: 1 | Passed: 0 | Failed: 1 | Skipped: 0")\n'
        "    sys.exit(1)\n"
        'elif "unattributable" in f:\n'
        "    print(GTK)\n"
        '    print("something failed, but not in a format anyone recognises")\n'
        '    print("Total: 1 | Passed: 0 | Failed: 1 | Skipped: 0")\n'
        "    sys.exit(1)\n"
        'elif "clean" in f:\n'
        '    print("  PASS  test_ok")\n'
        '    print("Total: 1 | Passed: 1 | Failed: 0 | Skipped: 0")\n'
        "    sys.exit(0)\n"
        "else:\n"
        '    print("  FAIL  test_prune_counts"); print("          " + ASSERT)\n'
        'print("Total: 2 | Passed: 1 | Failed: 1 | Skipped: 0")\n'
        "sys.exit(1)\n",
        encoding="utf-8")
    for stem in ("envfirst", "envtail", "genuineenv", "unattributable",
                 "plain", "clean"):
        (root / "tests" / f"test_{stem}.py").touch()
    return root


def _fullsuite(tmp_path: Path, only: str) -> tuple[int, str]:
    work = _fullsuite_tree(tmp_path / "fw")
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(state)
    done = subprocess.run(
        [PYTHON, str(_tool("bd-fullsuite")), "--work", str(work),
         "--only", only, "--jobs", "1"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300, env=env)
    return done.returncode, done.stdout + done.stderr


@pytest.mark.parametrize("only", ["envfirst", "plain"])
def test_fullsuite_reports_a_real_failure(tmp_path, only) -> None:
    """Already correct at @871 for envfirst; pinned so it stays correct."""
    code, out = _fullsuite(tmp_path, only)
    assert "REAL failed 1" in out, f"[{only}] genuine failure not reported:\n{out}"
    assert code != 0, f"[{only}] exited clean with a real failure:\n{out}"


def test_fullsuite_is_not_excused_by_output_after_the_last_failure(
    tmp_path,
) -> None:
    """THE RESIDUAL DEFECT. A failing block runs to end-of-output, so text
    printed after the run's summary -- teardown, atexit, a shutdown-time GTK
    warning -- is absorbed into the last test's excerpt and excuses it.
    """
    code, out = _fullsuite(tmp_path, "envtail")
    assert "REAL failed 1" in out, (
        "an environment line printed AFTER the run summary excused a genuine "
        f"AssertionError -- the operator was told GREEN:\n{out}"
    )
    assert code != 0, f"exited clean with a real failure:\n{out}"


def test_fullsuite_still_excuses_a_genuine_environment_failure(
    tmp_path,
) -> None:
    """The over-sensitive direction, and it is not optional.

    The excerpt's second and third lines are printed at column 0, so any
    indentation-based bound would cut this traceback before its GTK line and
    report a code defect on a box with no typelibs -- failing a healthy
    environment for a condition no code change can fix.
    """
    code, out = _fullsuite(tmp_path, "genuineenv")
    assert "REAL failed 0" in out, (
        f"a genuine environment failure was graded a code defect:\n{out}"
    )
    assert "env 1" in out, f"the env classification was lost entirely:\n{out}"


def test_fullsuite_refuses_when_nothing_earned_a_pass(tmp_path) -> None:
    """A run where every file classifies env has verified nothing."""
    code, out = _fullsuite(tmp_path, "genuineenv")
    assert code != 0, (
        "0 real passes and it still exited clean -- 'verified nothing' is not "
        f"a pass:\n{out}"
    )
    assert "CANNOT-EVALUATE" in out, f"the refusal is not stated:\n{out}"


def test_fullsuite_fails_closed_when_it_cannot_attribute_the_failure(
    tmp_path,
) -> None:
    """If the marker format ever drifts, the segmentation becomes its own blind
    denominator -- and it must fail CLOSED rather than excuse the whole file.

    This is the safety property the entire per-test approach rests on, and it
    escaped the first mutation battery: a mutant grading an unattributable
    failure as `env` stayed green because nothing exercised the branch. The
    output here carries an env-looking line and a non-zero exit with no
    recognisable marker, so "env" is the tempting answer and UNKNOWN is the
    correct one.
    """
    code, out = _fullsuite(tmp_path, "unattributable")

    assert "env 1" not in out, (
        "a failure nobody could attribute was excused as environmental -- the "
        f"one direction this design must never take:\n{out}"
    )
    assert code != 0, f"exited clean on an unattributable failure:\n{out}"
    assert "UNKNOWN" in out.upper(), (
        f"the refusal must name itself as unknown, not merely fail:\n{out}"
    )
