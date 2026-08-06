"""Every wired gate must REFUSE, not pass, when its denominator is empty.

Item 8 (Batch B), and it is a GATE rather than a repair. Measured at v3.66.911
by running each tool: **all five already refuse**. The register reports "one of
five done, four remain" -- the fix landed and nothing pinned it, which is the
same shape as the six items the @871/872 sweep found already closed with no
test. The register's own rule: a closed item with no test is not closed.

CLAUDE.md section 0 is the whole reason this invariant exists. A gate that
cannot see its subject reports OK -- truthfully, and uselessly. Each of these
tools has a denominator it can be handed nothing over:

    bd-opv                a check-id selection matching no check
    bd-env-report-check   a tree with no .claude-env-report.md
    bd-equiv              an empty corpus (no inputs to compare)
    bd-fullsuite          a tree where no test file is selected
    bd-docstale           a corpus with no marked doc

BOTH DIRECTIONS, because only one of them is the interesting failure. The NEG
case pins that an empty denominator exits 2 (CANNOT-EVALUATE) rather than 0.
The POS case pins that the tool is still an INSTRUMENT -- a tool that refuses
unconditionally would satisfy the NEG case perfectly and be worthless, which is
exactly the over-correction section 0 warns about in its inverse form. bd-docstale
says this in its own selftest: "a tool that can only refuse is not an instrument".

TWO MEASUREMENT TRAPS HIT WHILE WRITING THIS, both recorded because the exit
code is the entire subject here:

  * `cmd | tail` then `echo $?` reports TAIL's status, not the tool's -- the
    exact trap CLAUDE.md section 5 names. Every probe below is unpiped.
  * argparse ALSO exits 2 on an unrecognised flag. `bd-env-report-check --work`
    returned 2 for that reason and briefly read as a refusal; its flag is
    `--tree`. So the NEG assertions check the tool's own refusal WORDING, not
    just the code -- a bare exit-2 assertion cannot tell a refusal from a usage
    error.

POS invocations are chosen to be cheap (all under ~5s). bd-opv and bd-equiv use
their own --selftest for POS because a direct positive run would launch a
browser and compare real corpora respectively; each of those selftests contains
an explicit POS case, so the "not merely a refuser" property is still exercised.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_PY = _REPO / "venv" / "bin" / "python"
_BIN = _REPO / "toolchain" / "bin"

EXIT_CANNOT_EVALUATE = 2


def _run(args, timeout=300):
    """Run a bd-* tool. Returns (returncode, combined output), NEVER piped."""
    interp = _PY if _PY.exists() else pathlib.Path(sys.executable)
    cp = subprocess.run([str(interp)] + [str(a) for a in args],
                        capture_output=True, text=True, timeout=timeout,
                        cwd=str(_REPO))
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


# (tool, neg_args_factory, neg_wording, pos_args_factory)
#
# neg_wording is the tool's OWN refusal phrasing. Asserting only on exit 2
# would accept argparse's usage-error 2 as if it were a refusal.
_GATES = [
    ("bd-opv",
     lambda empty: ["--only", "bd_no_such_check_zzz"],
     "CANNOT-EVALUATE",
     lambda: ["--selftest"]),
    ("bd-env-report-check",
     lambda empty: ["--tree", empty],
     "UNKNOWN",
     lambda: ["--tree", str(_REPO)]),
    ("bd-equiv",
     lambda empty: ["--old", "echo x", "--new", "echo x", "--corpus", empty],
     "no inputs",
     lambda: ["--selftest"]),
    ("bd-fullsuite",
     lambda empty: ["--work", empty],
     "CANNOT-EVALUATE",
     lambda: ["--work", str(_REPO), "--only", "test_v3_66_908_runner_param"]),
    ("bd-docstale",
     lambda empty: ["--dir", empty, "--work", str(_REPO)],
     # Its own phrasing differs from the others: "BD-DOCSTALE UNEVALUABLE ...
     # 0 documents graded. That is UNKNOWN, not clean." Kept per-tool rather
     # than loosened to a shared substring -- a wording check that matched
     # everything would stop distinguishing a refusal from a usage error, which
     # is the only reason it exists.
     "UNEVALUABLE",
     lambda: ["--dir", str(_REPO / "project-knowledge"), "--work", str(_REPO)]),
]

_IDS = [g[0] for g in _GATES]


def test_the_gate_registry_is_not_empty():
    """This file is itself a gate, so its own denominator needs a floor.

    Without this, deleting every row from _GATES would leave a parametrized
    suite that collects nothing and reports green -- the defect the suite exists
    to forbid, committed by the suite.
    """
    assert len(_GATES) >= 5, _GATES
    for name, *_ in _GATES:
        assert (_BIN / name).exists(), f"{name} is not in {_BIN}"


@pytest.mark.parametrize("name,neg,wording,_pos", _GATES, ids=_IDS)
def test_an_empty_denominator_is_refused_not_passed(name, neg, wording, _pos):
    """NEG: nothing to examine must be CANNOT-EVALUATE, never a pass."""
    with tempfile.TemporaryDirectory() as empty:
        rc, out = _run([_BIN / name] + neg(empty))
    assert rc == EXIT_CANNOT_EVALUATE, (
        f"{name} returned {rc} on an EMPTY denominator; a gate that cannot see "
        f"its subject must refuse, not report OK.\n{out[-1500:]}")
    assert wording in out, (
        f"{name} exited 2 but without its own refusal wording {wording!r} -- "
        f"argparse also exits 2 on a usage error, so the code alone does not "
        f"establish a refusal.\n{out[-1500:]}")


@pytest.mark.parametrize("name,_neg,_wording,pos", _GATES, ids=_IDS)
def test_a_real_denominator_is_still_evaluable(name, _neg, _wording, pos):
    """POS: the over-correction guard.

    A tool rewritten to refuse unconditionally would satisfy every NEG case
    above and be useless. This fails it.
    """
    rc, out = _run([_BIN / name] + pos())
    assert rc != EXIT_CANNOT_EVALUATE, (
        f"{name} refused a REAL denominator -- a tool that can only refuse is "
        f"not an instrument.\n{out[-1500:]}")
