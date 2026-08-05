"""run_tests_core resolved fixture dependencies from a table missing four of six.

TWO DEFECTS, ONE ROOT CAUSE (register items B and B2).

THE SHIM TABLE IS ASYMMETRIC. `run_test` supplies SIX shims to a TEST
function's parameters -- `clean_workdir`, `fresh_app`, `aiassist_module`,
`tmp_path`, `monkeypatch`, `capsys`. The two paths that supply a FIXTURE's
parameters -- the autouse loop and `_resolve_named` -- handle only `tmp_path`
and `monkeypatch`. So the identical name resolves on one path and is silently
dropped on the other, and the fixture call then raises TypeError. CLAUDE.md
section 0's shape exactly: two resolution paths for one name, only one
complete. `_resolve_named`'s own docstring says it supplies
"tmp_path/monkeypatch/capsys/other-named-fixture deps" -- naming a shim the
code does not pass, which is the same defect written in prose.

Consequence, measured at v3.66.883: `bd-band` manufactured 80 failing cases
across 22 suites that pass 413/413 under real pytest -- while CLAUDE.md
section 4 mandates `bd-band` as the band tool. The fix is to resolve every
shim from ONE table used by all three paths, rather than adding
`clean_workdir` as a special case and leaving the other three broken -- which
would reproduce the shape of the defect inside its own repair.

B2 IS THE MORE DANGEROUS HALF, AND IT IS SILENT. Fixture collection is gated
on `not name.startswith("_")`, for both autouse and named fixtures. An
underscore-prefixed fixture is therefore never collected and never invoked, so
a suite PASSES without the setup it declares. The RED case below only fails
loudly because its test body asserts the fixture ran; a suite that merely
depended on the setup would have gone green without it. Real pytest collects
fixtures by decorator, not by name, so this also diverges from the API the
stub exists to mirror.

WHY A SUBPROCESS. `activated_pytest_stub()` refuses to replace a non-BD
`pytest` module, and under a real pytest run `sys.modules["pytest"]` is real
pytest -- so the stub cannot be activated in-process here. Each case runs the
runner in a fresh interpreter, which is also how `bd-band` actually invokes
it, so these cases exercise the real path rather than a reconstruction.

RED IN BOTH DIRECTIONS. Five cases fail on pristine source, each with a
distinct measured signature. Three more pass before AND after: they pin that
the repair does not over-correct by collecting every underscore-prefixed
callable as a fixture, which is the obvious wrong way to fix B2.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

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
    """Run one synthetic test module through the real runner. Returns rows."""
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
        "the runner driver produced no result row -- the harness failed, which "
        "is not the same as the subject failing.\nstdout=%s\nstderr=%s"
        % (r.stdout[-2000:], r.stderr[-2000:]))
    return json.loads(line[-1][len("BD_ROWS "):])


def _one(rows):
    assert len(rows) == 1, "expected exactly one test row, got %r" % (rows,)
    return rows[0]


# --------------------------------------------------------------------------- #
# RED: item B -- a fixture may not depend on a shim the TEST path supplies     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("shim", ["clean_workdir", "capsys"])
def test_named_fixture_may_depend_on_a_test_path_shim(tmp_path, shim):
    """`_resolve_named` dropped every shim except tmp_path/monkeypatch.

    `capsys` is parametrized alongside `clean_workdir` deliberately: the
    docstring already CLAIMED capsys support, so a fix that special-cases
    clean_workdir alone leaves a defect the comment says is already fixed.
    """
    name, err, ok = _one(_run_module(tmp_path, "test_named_%s" % shim, f'''
import pytest

@pytest.fixture
def widget({shim}):
    return "ok"

def test_uses_widget(widget):
    assert widget == "ok"
'''))
    assert ok, (
        "a named fixture depending on the %s shim was not resolved; the test "
        "path supplies it and the fixture path does not. err=%s" % (shim, err))


def test_autouse_fixture_may_depend_on_clean_workdir(tmp_path):
    """Same asymmetry on the autouse path, which is a separate loop."""
    name, err, ok = _one(_run_module(tmp_path, "test_autouse_cw", '''
import pytest

@pytest.fixture(autouse=True)
def prep(clean_workdir):
    assert clean_workdir
    yield

def test_a():
    assert True
'''))
    assert ok, (
        "an autouse fixture depending on clean_workdir was not resolved. "
        "err=%s" % err)


# --------------------------------------------------------------------------- #
# RED: item B2 -- an underscore-prefixed fixture is silently never invoked     #
# --------------------------------------------------------------------------- #

def test_underscore_autouse_fixture_actually_runs(tmp_path):
    """THE SILENT ONE. Collection is gated on the NAME, so the fixture never
    runs and the suite passes without the setup it declares.

    This case is only loud because the body asserts. That is the point: a
    suite that merely RELIED on the setup would have gone green without it.
    """
    name, err, ok = _one(_run_module(tmp_path, "test_us_autouse", '''
import pytest

RAN = []

@pytest.fixture(autouse=True)
def _prep():
    RAN.append(1)
    yield

def test_c():
    assert RAN, "autouse fixture never ran"
'''))
    assert ok, (
        "an underscore-prefixed autouse fixture was never invoked, so the "
        "test ran without its declared setup. err=%s" % err)


def test_underscore_named_fixture_is_requestable(tmp_path):
    """Real pytest collects fixtures by decorator, not by leading underscore."""
    name, err, ok = _one(_run_module(tmp_path, "test_us_named", '''
import pytest

@pytest.fixture
def _thing():
    return 42

def test_d(_thing):
    assert _thing == 42
'''))
    assert ok, (
        "an underscore-prefixed named fixture could not be requested by name. "
        "err=%s" % err)


# --------------------------------------------------------------------------- #
# GREEN before AND after: the repair must not over-correct                     #
# --------------------------------------------------------------------------- #

def test_a_plain_underscore_callable_is_not_a_fixture(tmp_path):
    """The obvious wrong fix for B2 is to drop the underscore guard entirely.

    That would make every private helper in a test module injectable, so a
    test whose parameter happens to share a helper's name would silently
    receive the FUNCTION rather than failing. The marker is the discriminator,
    not the name. This case must stay RED-for-the-test (ok is False) before
    and after the repair.
    """
    name, err, ok = _one(_run_module(tmp_path, "test_plain_us", '''
def _helper():
    return 1

def test_e(_helper):
    assert _helper == 1
'''))
    assert not ok, (
        "an undecorated private helper was injected as a fixture -- the "
        "underscore guard was removed without keeping the marker check.")
    assert "_helper" in err


def test_the_test_parameter_path_still_gets_every_shim(tmp_path):
    """The path that ALREADY worked, pinned.

    ADDED TO CLOSE A MUTATION ESCAPE. Deleting `clean_workdir` from
    `_SHIM_NAMES` left the whole battery green, because `_SHIM_NAMES` drives
    only the TEST-parameter loop while every other case here exercises the
    FIXTURE path -- which reaches `_shim` directly and so still resolved it.
    A cut that repairs the fixture path is exactly the cut most likely to
    break the test path, and nothing was watching it.
    """
    name, err, ok = _one(_run_module(tmp_path, "test_param_path", '''
def test_h(clean_workdir, tmp_path, capsys):
    assert clean_workdir
    assert tmp_path.exists()
    assert capsys is not None
'''))
    assert ok, (
        "a TEST FUNCTION stopped receiving the built-in shims -- the path "
        "that worked before this cut. err=%s" % err)


def test_a_fixture_and_the_test_share_one_shim_object(tmp_path):
    """Memoisation is a behaviour, so it gets a test.

    ADDED TO CLOSE A MUTATION ESCAPE. Disabling the shim cache left the
    battery green. Without it a fixture and the test body get DIFFERENT
    monkeypatch objects, so patches applied in the fixture are not undone by
    the teardown that undoes the test's -- state leaks into the next test in
    the file. That is the property the single shim table depends on, and the
    implementation comment asserts it, so prose was the only thing holding it.
    """
    name, err, ok = _one(_run_module(tmp_path, "test_shared_shim", '''
import pytest

SEEN = []

@pytest.fixture(autouse=True)
def prep(monkeypatch, tmp_path):
    SEEN.append((id(monkeypatch), id(tmp_path)))
    yield

def test_i(monkeypatch, tmp_path):
    assert SEEN, "autouse fixture did not run"
    assert SEEN[0] == (id(monkeypatch), id(tmp_path)), (
        "fixture and test received different shim objects")
'''))
    assert ok, (
        "a fixture and the test body did not share one shim object, so a "
        "fixture's monkeypatch would outlive its teardown. err=%s" % err)


def test_ordinary_fixtures_still_work(tmp_path):
    """Baseline: the paths this cut touches must keep working unchanged."""
    name, err, ok = _one(_run_module(tmp_path, "test_ordinary", '''
import pytest

ORDER = []

@pytest.fixture(autouse=True)
def prep(tmp_path, monkeypatch):
    ORDER.append("autouse")
    yield

@pytest.fixture
def thing(tmp_path):
    ORDER.append("named")
    return str(tmp_path)

def test_f(thing):
    assert thing
    assert ORDER == ["autouse", "named"], ORDER
'''))
    assert ok, "ordinary autouse+named fixture resolution regressed. err=%s" % err


def test_a_fixture_depending_on_another_fixture_still_works(tmp_path):
    """Fixture-to-fixture deps are the one case `_resolve_named` did handle."""
    name, err, ok = _one(_run_module(tmp_path, "test_chain", '''
import pytest

@pytest.fixture
def base():
    return 7

@pytest.fixture
def derived(base):
    return base * 2

def test_g(derived):
    assert derived == 14
'''))
    assert ok, "fixture-to-fixture dependency regressed. err=%s" % err
