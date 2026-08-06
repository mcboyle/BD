"""discover_and_run never registered the module it was about to execute.

Item 4, sub-cut 3 of 3 (the register's "module import path" root cause).

`discover_and_run` builds the module with `module_from_spec` and calls
`exec_module` WITHOUT first putting it in `sys.modules`. That is the one step
the importlib docs call out, and real pytest does it too. Without it, anything
that resolves a name through `sys.modules[cls.__module__]` during import gets
None -- most commonly `@dataclass` under `from __future__ import annotations`,
where the field types are strings that have to be looked up in the defining
module's globals:

    IMPORT ERROR: 'NoneType' object has no attribute '__dict__'

MINIMAL REPRODUCER, so this test is about the runner and not about the suite
that surfaced it. A dataclass with one annotated field and a future-annotations
import is enough -- proven by one-variable experiment before this test was
written:

    register_in_sys_modules=False -> AttributeError: 'NoneType' ... '__dict__'
    register_in_sys_modules=True  -> OK

THE CLEANUP HALF IS THE OVER-CORRECTION GUARD. Registering and then leaving a
FAILED module behind is worse than not registering: the next import of the same
name would find a half-executed module in `sys.modules` and skip re-running it,
so a suite could pass against a module whose import raised. The third case
below pins that a failed import leaves nothing behind.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
_PY = _REPO / "venv" / "bin" / "python"

# Reports the rows AND whether the module survived in sys.modules afterwards,
# because the leak is invisible from the rows alone.
_DRIVER = '''
import sys, json, pathlib
sys.path.insert(0, %r)
import run_tests_core as R
p = pathlib.Path(sys.argv[1])
with R.activated_pytest_stub():
    rows = R.discover_and_run(p)
left = [k for k in sys.modules if k == "test_" + p.stem]
print("BD_ROWS " + json.dumps({
    "rows": [(n, e, ok) for n, e, ok, _ in rows],
    "left_in_sys_modules": left,
}))
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
        "the runner driver produced no result -- the HARNESS failed, which is "
        "not the same as the subject failing.\nstdout=%s\nstderr=%s"
        % (r.stdout[-2000:], r.stderr[-2000:]))
    return json.loads(line[-1][len("BD_ROWS "):])


_DATACLASS = '''
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Thing:
    name: str
    count: int = 0

def test_dataclass_module_imports():
    assert Thing("a").count == 0
'''

_SELF_LOOKUP = '''
import sys

def test_module_can_find_itself_in_sys_modules():
    me = sys.modules.get(__name__)
    assert me is not None, "the running module is absent from sys.modules"
    assert getattr(me, "MARKER", None) == 42, getattr(me, "MARKER", None)

MARKER = 42
'''

_BAD_IMPORT = '''
raise RuntimeError("deliberate import failure")

def test_never_collected():
    pass
'''


def test_a_dataclass_module_under_future_annotations_imports(tmp_path):
    """RED: 'NoneType' object has no attribute '__dict__' at import."""
    out = _run_module(tmp_path, "test_dc.py", _DATACLASS)
    rows = out["rows"]
    assert len(rows) == 1, rows
    _name, err, ok = rows[0]
    assert ok, "dataclass module failed to import: %s" % (err,)


def test_the_module_is_resolvable_by_name_while_it_runs(tmp_path):
    """The mechanism itself, not just one of its symptoms.

    Asserting only the dataclass case would leave the fix free to special-case
    dataclasses. This pins the property that makes the whole class of failure
    go away: during the run, `sys.modules[__name__]` is the module itself.
    """
    out = _run_module(tmp_path, "test_selfref.py", _SELF_LOOKUP)
    rows = out["rows"]
    assert len(rows) == 1, rows
    _name, err, ok = rows[0]
    assert ok, "module could not resolve itself: %s" % (err,)


def test_a_failed_import_leaves_nothing_in_sys_modules(tmp_path):
    """OVER-CORRECTION GUARD, and the reason registering alone is not enough.

    A half-executed module left in sys.modules is worse than an unregistered
    one: a later import of the same name would find it and skip re-executing,
    so a suite could pass against a module whose import actually raised. This
    fails if the fix registers without cleaning up on the error path.

    Passes on pristine source too -- nothing is registered there, so nothing
    leaks. That is deliberate: it is the direction the FIX could break, not the
    defect being fixed.
    """
    out = _run_module(tmp_path, "test_bad.py", _BAD_IMPORT)
    rows = out["rows"]
    assert len(rows) == 1 and not rows[0][2], rows
    assert "deliberate import failure" in str(rows[0][1]), rows
    assert out["left_in_sys_modules"] == [], (
        "a failed import left the module behind: %r"
        % (out["left_in_sys_modules"],))
