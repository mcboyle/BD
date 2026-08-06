"""The runner's pytest stub had no `param`, so five suites could not import.

MEASURED at v3.66.907, over `git ls-files -- 'tests/*.py'` (1239 files):
`pytest.param` appears at 49 call sites across 5 files, and every one of those
five dies at IMPORT with

    IMPORT ERROR: '_PytestStub' object has no attribute 'param'

so `bd-band` -- which CLAUDE.md section 4 mandates on every cut -- cannot run
them at all. Among them is `test_playwright_engines_single_source.py`, an
axis-6 gate that joins any cut touching a shell script.

THE OBVIOUS IMPLEMENTATION IS WRONG, AND IT FAILS QUIETLY. Real pytest returns
a ParameterSet whose `.values` tuple is zipped against `argnames`. A stub that
returns `values[0]` -- the shape an earlier design proposed -- silently feeds a
single value to a multi-argument test. Measured over the same denominator:
**45 of the 49 sites carry between 2 and 5 values**, so that design is wrong
almost everywhere it is used, and the four single-value sites are the only ones
that would look right. The runner's injection at run_tests_core.py already
wraps a scalar and zips a tuple, so returning the tuple IS the correct
semantics and needs no change there.

THE STUB MUST STAY A STUB. `id=` is accepted and ignored, exactly as
`parametrize` already ignores `ids=`, because the runner labels cases by index.
Measured: `id` is the ONLY kwarg used, on 49 of 49 sites; `marks` appears zero
times. Rather than silently drop a `marks=` that would change which cases run,
the stub refuses -- a false green is worse than a loud refusal.

WHY A SUBPROCESS: `activated_pytest_stub()` refuses to replace a real `pytest`
module, so under a real pytest run the stub cannot be activated in-process.
Each case runs the runner in a fresh interpreter, which is also how `bd-band`
invokes it.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
_PY = _REPO / "venv" / "bin" / "python"

_DRIVER = '''
import sys, json, pathlib
sys.path.insert(0, %r)
import run_tests_core as R
with R.activated_pytest_stub():
    rows = R.discover_and_run(pathlib.Path(sys.argv[1]))
print("BD_ROWS " + json.dumps([(n, e, ok) for n, e, ok, _ in rows]))
''' % str(_REPO)


def _run_module(tmp_path: pathlib.Path, name: str, src: str):
    """Run one synthetic module through the real runner. Returns its rows."""
    interp = _PY if _PY.exists() else pathlib.Path(sys.executable)
    mod = tmp_path / (name if name.endswith(".py") else name + ".py")
    mod.write_text(src, encoding="utf-8")
    drv = tmp_path / "_drv.py"
    drv.write_text(_DRIVER, encoding="utf-8")
    r = subprocess.run([str(interp), str(drv), str(mod)],
                       capture_output=True, text=True, timeout=180,
                       cwd=str(tmp_path))
    line = [l for l in r.stdout.splitlines() if l.startswith("BD_ROWS ")]
    assert line, (
        "the runner driver produced no result row -- the HARNESS failed, which "
        "is not the same as the subject failing.\nstdout=%s\nstderr=%s"
        % (r.stdout[-2000:], r.stderr[-2000:]))
    return json.loads(line[-1][len("BD_ROWS "):])


_MULTI = '''
import pytest

@pytest.mark.parametrize("a,b", [
    pytest.param(1, 2, id="first"),
    pytest.param(3, 4, id="second"),
])
def test_pair(a, b):
    assert b == a + 1, "got a=%r b=%r" % (a, b)
'''

_SINGLE = '''
import pytest

@pytest.mark.parametrize("x", [pytest.param(7, id="seven")])
def test_one(x):
    assert x == 7, "got x=%r" % (x,)
'''

_MARKS = '''
import pytest

@pytest.mark.parametrize("x", [pytest.param(1, marks=pytest.mark.skipif(True, reason="no"))])
def test_marked(x):
    assert x == 1
'''


def test_param_carries_every_value_not_just_the_first(tmp_path):
    """RED: import fails without `param`; and a values[0] stub would fail here.

    Both cases carry two values, which is the shape 45 of 49 real sites use.
    The assertion is inside the synthetic test body (b == a + 1), so a stub
    that delivered only `a` would raise TypeError for the missing `b` rather
    than passing on a wrong value -- either way this stays RED.
    """
    rows = _run_module(tmp_path, "test_multi.py", _MULTI)
    assert len(rows) == 2, "expected one row per case, got %r" % (rows,)
    for name, err, ok in rows:
        assert ok, "case %s failed: %s" % (name, err)


def test_param_with_a_single_value_still_resolves(tmp_path):
    """The four single-value sites must keep working.

    Guards the opposite over-correction: a stub that always returned a tuple
    of tuples, or that unwrapped too eagerly, would break these.
    """
    rows = _run_module(tmp_path, "test_single.py", _SINGLE)
    assert len(rows) == 1, rows
    name, err, ok = rows[0]
    assert ok, "single-value param failed: %s" % (err,)


def test_param_refuses_marks_rather_than_dropping_it(tmp_path):
    """A kwarg that changes WHICH cases run must not be silently ignored.

    `marks=` is unused today (0 of 49 sites), so accepting and discarding it
    would be free to write and would go unnoticed until someone used it -- at
    which point a skipped case would silently run. The stub refuses instead:
    an unimplemented feature that fails loudly is a stub, one that pretends is
    a false green. This asserts the REFUSAL, so a later change that quietly
    starts ignoring `marks` turns this red.
    """
    rows = _run_module(tmp_path, "test_kwarg_refusal.py", _MARKS)
    # Assert over the ERROR TEXT ONLY. The first draft of this test named the
    # module test_marks.py and searched the whole row blob, so it matched the
    # FILENAME and passed on pristine source where no refusal existed at all --
    # a denominator containing the subject's name instead of the subject.
    errs = " ".join(err for _name, err, _ok in rows)
    assert "marks" in errs, (
        "expected a loud refusal naming `marks` in the error text, got %r"
        % (rows,))
