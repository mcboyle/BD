"""Three more stub gaps kept eight suites off `bd-band`.

Item 4, sub-cut 2 of 3. @908 closed `pytest.param`; these are the rest of the
API-surface root cause, each measured at v3.66.908 by running the suite:

    pytest.MonkeyPatch   AttributeError: '_PytestStub' object has no
                         attribute 'MonkeyPatch'      (test_live_seed..., 11 cases)
    pytest.importorskip  IMPORT ERROR: ... no attribute 'importorskip'
                         (4 suites, module-level, so nothing in them runs)
    pytest.mark.slow     IMPORT ERROR: type object 'mark' has no attribute
                         'slow'                        (test_desandbox_tool_verifiers)

WHY A NO-OP IS THE FAITHFUL ANSWER FOR AN UNKNOWN MARK, AND WHERE IT IS NOT.
This repo has no pytest.ini, pyproject or setup.cfg, and no `--strict-markers`;
markers are registered by `tests/conftest.py` via `addinivalue_line`. Under real
pytest an UNREGISTERED mark is therefore metadata plus a warning, not an error,
so an inert decorator matches the API the stub exists to mirror.

That reasoning does NOT extend to marks that change behaviour. `usefixtures`
silently drops setup the test declares, and `xfail` inverts the verdict -- a
no-op for either is exactly the false green CLAUDE.md section 0 is about. Those
REFUSE. The distinction is the point of this cut: faithful where real pytest is
permissive, loud where silence would change the result.

`importorskip` must raise the stub's own skip, not return None: a test that
does `mod = pytest.importorskip("x")` and then uses `mod` would get an
AttributeError on None and read as a code defect rather than a skip.
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


_MONKEYPATCH = '''
import pytest

class _Target:
    value = "original"

def test_constructed_monkeypatch_sets_and_undoes():
    mp = pytest.MonkeyPatch()
    mp.setattr(_Target, "value", "patched")
    assert _Target.value == "patched", _Target.value
    mp.undo()
    assert _Target.value == "original", _Target.value
'''

_IMPORTORSKIP = '''
import pytest

def test_present_module_is_returned():
    mod = pytest.importorskip("json")
    # Must be the MODULE, not None -- a None return turns a skip into an
    # AttributeError that reads as a defect in the test.
    assert mod.dumps([1]) == "[1]", mod

def test_absent_module_skips():
    pytest.importorskip("bd_no_such_module_zzz")
    raise AssertionError("reached the line after an absent-module importorskip")
'''

_SLOW = '''
import pytest

@pytest.mark.slow
def test_marked_slow_still_runs():
    assert True
'''

_XFAIL = '''
import pytest

@pytest.mark.xfail
def test_inverted_verdict():
    assert False
'''


def test_monkeypatch_can_be_constructed_directly(tmp_path):
    """RED: `pytest.MonkeyPatch()` raised AttributeError.

    Asserts the round trip, not just construction: a stub exposing a class
    whose `undo` did nothing would still leave the patch in place.
    """
    rows = _run_module(tmp_path, "test_mp.py", _MONKEYPATCH)
    assert len(rows) == 1, rows
    _name, err, ok = rows[0]
    assert ok, "constructed MonkeyPatch failed: %s" % (err,)


def test_importorskip_returns_the_module_and_skips_when_absent(tmp_path):
    """RED: module-level AttributeError, so all four suites imported as zero.

    Both directions in one module: a present module must come back usable, and
    an absent one must SKIP rather than fail or return None.
    """
    rows = _run_module(tmp_path, "test_ios.py", _IMPORTORSKIP)
    assert len(rows) == 2, rows
    by_name = {n: (e, ok) for n, e, ok in rows}
    present = [v for k, v in by_name.items() if "present" in k]
    absent = [v for k, v in by_name.items() if "absent" in k]
    assert present and absent, rows
    assert present[0][1], "present module should pass: %s" % (present[0][0],)
    err, ok = absent[0]
    # STRICT: the runner represents a skip as ok=None with err starting "SKIP (".
    # The first draft accepted `ok is None OR "SKIP" in err.upper()`, and a stub
    # returning None instead of skipping runs on into the AssertionError below
    # -- whose message contained the word "skip", so the substring arm matched
    # TEXT THIS TEST WROTE and the mutant escaped. Both halves are required, and
    # the message above no longer contains the word.
    assert ok is None and str(err).startswith("SKIP"), (
        "absent module must SKIP, not run on; got err=%r ok=%r" % (err, ok))


def test_an_unknown_mark_is_inert_rather_than_an_import_error(tmp_path):
    """RED: `type object 'mark' has no attribute 'slow'` killed the import.

    Real pytest without --strict-markers treats an unregistered mark as
    metadata, so running the test is the faithful behaviour.
    """
    rows = _run_module(tmp_path, "test_slow.py", _SLOW)
    assert len(rows) == 1, rows
    _name, err, ok = rows[0]
    assert ok, "an inert mark should not stop the test running: %s" % (err,)


def test_a_verdict_changing_mark_refuses_rather_than_no_opping(tmp_path):
    """The other half, and the one a blanket __getattr__ would get wrong.

    `xfail` inverts the verdict. If the stub silently no-ops it, this synthetic
    test -- which asserts False -- would be reported as a FAILURE where real
    pytest reports an expected failure, or worse, a future `usefixtures` would
    drop setup and pass. Refusing names the gap instead.

    This is the over-sensitivity guard for the mark fix: it fails both if the
    stub raises AttributeError as before AND if the stub quietly accepts.
    """
    rows = _run_module(tmp_path, "test_xf.py", _XFAIL)
    errs = " ".join(str(e) for _n, e, _ok in rows)
    # Require the REFUSAL PHRASE, not just the name. On pristine source the
    # bare "type object 'mark' has no attribute 'xfail'" already contains
    # "xfail", so asserting the name alone passes without any refusal existing
    # -- the same wrong-reason pass the @908 marks test had, and the reason a
    # missing feature and a deliberate refusal must not look alike.
    assert "xfail" in errs and "does not implement" in errs, (
        "expected a deliberate refusal naming `xfail`, not a bare "
        "AttributeError, got %r" % (rows,))
