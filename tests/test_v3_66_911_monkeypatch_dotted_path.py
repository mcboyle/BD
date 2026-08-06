"""monkeypatch.setattr's dotted-path detector excluded the common case.

Item 4, sub-cut 4 of 4 -- and the register describes item 4 as three, because
this cause was found by running the suites rather than reading the item.

Real pytest has two forms:

    setattr(obj, "name", value)          3-arg: target is an OBJECT
    setattr("pkg.mod.attr", value)       2-arg: target is a STRING

The shim tried to tell them apart with

    if value is None and not callable(name):

which is wrong in both directions. In the 2-arg form `name` holds the
REPLACEMENT, and a replacement is usually a function or lambda -- so
`callable(name)` is true, the guard misses, and execution falls through to

    setattr(<str>, <function>, None)
    TypeError: attribute name must be string, not 'function'

That is CLAUDE.md section 0 in a predicate: the detector excludes the most
common instance of its own subject. And on the rare non-callable replacement
the guard DID fire, only to raise NotImplementedError -- so the form was
unreachable either way.

Measured at v3.66.910: this accounts for all 75 failures in
test_coverage_map_frontend (its autouse fixture patches
"tools.code_intelligence.coverage_service.build_snapshot" with a lambda) plus 3
in test_fuzz_harness_frontend.

THE DETECTOR IS NOW `isinstance(target, str)`, which is exactly what
distinguishes the forms and cannot be confused by what the replacement happens
to be. Resolution walks the longest importable prefix and getattrs the rest, so
"pkg.mod.Class.attr" works and not just "pkg.mod.attr".
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


# A callable replacement -- the shape 'not callable(name)' could never detect,
# and the shape the real failing fixture uses.
_CALLABLE = '''
import json
import pytest

def test_dotted_path_with_a_callable_replacement():
    mp = pytest.MonkeyPatch()
    mp.setattr("json.dumps", lambda o, **k: "PATCHED")
    assert json.dumps([1]) == "PATCHED", json.dumps([1])
    mp.undo()
    assert json.dumps([1]) == "[1]", json.dumps([1])
'''

# The other direction: a non-callable replacement DID trip the old guard, and
# was then refused outright. Both halves have to work.
_NONCALLABLE = '''
import json
import pytest

def test_dotted_path_with_a_non_callable_replacement():
    mp = pytest.MonkeyPatch()
    mp.setattr("json.__name__", "patched-name")
    assert json.__name__ == "patched-name", json.__name__
    mp.undo()
    assert json.__name__ == "json", json.__name__
'''

# Nested attribute holder: "pkg.mod.Class.attr" is not importable as a module,
# so resolution has to walk.
_NESTED = '''
import json
import pytest

def test_dotted_path_walks_to_a_nested_holder():
    mp = pytest.MonkeyPatch()
    mp.setattr("json.JSONEncoder.item_separator", "|")
    assert json.JSONEncoder.item_separator == "|"
    mp.undo()
    assert json.JSONEncoder.item_separator == ", "
'''

# OVER-CORRECTION GUARD: the 3-arg object form must keep working unchanged.
_THREE_ARG = '''
import pytest

class _T:
    v = "original"

def test_three_arg_object_form_still_works():
    mp = pytest.MonkeyPatch()
    mp.setattr(_T, "v", "patched")
    assert _T.v == "patched", _T.v
    mp.undo()
    assert _T.v == "original", _T.v
'''

# A path that cannot resolve must say so, not patch something else silently.
_BAD_PATH = '''
import pytest

def test_unresolvable_path_refuses():
    mp = pytest.MonkeyPatch()
    mp.setattr("bd_no_such_module_zzz.attr", 1)
'''


def test_dotted_path_accepts_a_callable_replacement(tmp_path):
    """RED: TypeError: attribute name must be string, not 'function'."""
    rows = _run_module(tmp_path, "test_cb.py", _CALLABLE)
    assert len(rows) == 1, rows
    _n, err, ok = rows[0]
    assert ok, "callable replacement failed: %s" % (err,)


def test_dotted_path_accepts_a_non_callable_replacement(tmp_path):
    """RED: the old guard fired here and raised NotImplementedError."""
    rows = _run_module(tmp_path, "test_nc.py", _NONCALLABLE)
    assert len(rows) == 1, rows
    _n, err, ok = rows[0]
    assert ok, "non-callable replacement failed: %s" % (err,)


def test_dotted_path_resolves_a_nested_attribute_holder(tmp_path):
    """"pkg.mod.Class.attr" is not an importable module path.

    Pins that resolution walks the longest importable prefix rather than
    assuming everything before the last dot is a module.
    """
    rows = _run_module(tmp_path, "test_nest.py", _NESTED)
    assert len(rows) == 1, rows
    _n, err, ok = rows[0]
    assert ok, "nested holder failed: %s" % (err,)


def test_the_three_arg_object_form_is_unchanged(tmp_path):
    """OVER-CORRECTION GUARD: passes before AND after.

    A detector keying on the wrong thing could route the object form down the
    dotted-path branch. This is the direction the FIX could break.
    """
    rows = _run_module(tmp_path, "test_3arg.py", _THREE_ARG)
    assert len(rows) == 1, rows
    _n, err, ok = rows[0]
    assert ok, "three-arg form regressed: %s" % (err,)


def test_an_unresolvable_dotted_path_refuses_loudly(tmp_path):
    """Silence here would patch nothing and let the test pass regardless.

    Requires the RESOLUTION-FAILURE phrase, not just the module name. The
    runner embeds the whole traceback in its error string, and the traceback
    echoes the source line -- which contains the module name verbatim. Asserting
    the name alone therefore matched TEXT THIS TEST WROTE and passed on pristine
    source, where the real message is "dotted-path form not implemented in
    shim". That is the fourth assertion in four cuts whose denominator held its
    own subject; the phrase check is what actually distinguishes "resolved and
    refused" from "never implemented".
    """
    rows = _run_module(tmp_path, "test_bad.py", _BAD_PATH)
    errs = " ".join(str(e) for _n, e, _ok in rows)
    assert not rows[0][2], "an unresolvable path must not pass silently: %r" % (rows,)
    assert "could not resolve" in errs and "bd_no_such_module_zzz" in errs, (
        "expected a resolution failure naming the path, got %r" % (rows,))
