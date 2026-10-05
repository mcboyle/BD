"""o2078 main-red (FIX-R-221, r2 -225, r3 -228): tests/test_o1567_fx_takeover_plain_browser.py WITHOUT cloakbrowser.

MAIN-RED-STALE-GATE at origin/main bfa4bc81: two tests of that file failed with ModuleNotFoundError: No module named
'cloakbrowser' -- the package is optional (requirements-cloak.txt) and the gate venv does not carry it.

Contract:
  1. Without cloakbrowser the positive control of the automation-channel test still RUNS (it is not skipped): a
     stand-in flag builder is registered at the import human_challenge.plain_browser_argv makes.
  2. That control keeps its power there: a filter that stops removing --enable-automation is caught, and a builder
     the product never called is caught (the control cannot pass on the product's fallback flags).
  3. Without cloakbrowser the session test runs every assertion up to the relaunch control on Playwright's stock
     chromium, and skips ONLY that control, with a named reason. Pinned by EXECUTION: the child run is traced, every
     assert written before the control must have run, and the skip must have been raised at the control's own line.
  4. Only a cloakbrowser that is NOT INSTALLED is stood in for, and that is decided by the import system (no spec
     for the name), never by the error an import raised. An installed one that does not import (broken package, a
     missing dependency of it, a missing or broken browser submodule, an __init__ that itself raises
     ModuleNotFoundError naming cloakbrowser) or that has no build_args errors: it is never substituted.

A venv without the package is stood in by making ``cloakbrowser`` unimportable (None in sys.modules: find_spec answers
None), so the same answer is measured on a venv that has it. An installed package is a real one on disk, ahead of any
other on sys.path, and find_spec points at its own __init__.py.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BD_GATE_SCOPE = "module"

HERE = Path(__file__).resolve().parent
SUBJECT = HERE / "test_o1567_fx_takeover_plain_browser.py"
SESSION = "test_the_login_after_a_pass_runs_inside_the_browser_that_passed"
CONTROL_REASON = "O1567_RELAUNCH_CONTROL_NEEDS_CLOAKBROWSER"
# the assertions the skip must never get ahead of: the pass, the login in the passed session, the owner's close
SESSION_ASSERTIONS = ("O1567_TAKEOVER_PASS_NOT_READ", "O1567_TAKEOVER_LOGIN_NOT_IN_PASSED_SESSION",
                      "do_login must leave the challenge browser to its owner", "rc == 0")
BLOCK = ("import sys\n"
         "for k in [k for k in sys.modules if k == 'cloakbrowser' or k.startswith('cloakbrowser.')]:\n"
         "    del sys.modules[k]\n"
         "sys.modules['cloakbrowser'] = None\n")
# the child loads THIS file for its trace plugin: argv = scope file, trace output, then the pytest arguments
CHILD = BLOCK + ("import importlib.util\nimport pytest\n"
                 "spec = importlib.util.spec_from_file_location('o2078_scope_plugin', sys.argv[1])\n"
                 "scope = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(scope)\n"
                 "sys.exit(pytest.main(sys.argv[3:], plugins=[scope.SessionTrace(sys.argv[2])]))\n")


def _subject():
    spec = importlib.util.spec_from_file_location("o2078_subject_o1567", SUBJECT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cloak_modules():
    return [k for k in sys.modules if k == "cloakbrowser" or k.startswith("cloakbrowser.")]


def _no_cloakbrowser(monkeypatch):
    for name in [k for k in sys.modules if k.startswith("cloakbrowser.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "cloakbrowser", None)
    assert importlib.util.find_spec("cloakbrowser") is None, "O2078_ABSENT_STAND_IN_STILL_HAS_A_SPEC"
    with pytest.raises(ModuleNotFoundError) as absent:      # the stand-in really is a venv without the package
        import cloakbrowser  # noqa: F401
    assert absent.value.name == "cloakbrowser", absent.value.name
    with pytest.raises(ImportError):
        import cloakbrowser.browser  # noqa: F401


def test_the_positive_control_runs_without_cloakbrowser(monkeypatch, tmp_path):
    _no_cloakbrowser(monkeypatch)
    subject = _subject()
    try:
        subject.test_the_plain_browser_carries_no_automation_channel(monkeypatch, tmp_path)
    except pytest.skip.Exception as skipped:
        pytest.fail(f"O2078_CONTROL_SKIPPED_WITHOUT_CLOAK: {skipped}")
    # it ran through the stand-in: the builder human_challenge imported is the one the control registered
    stand_in = sys.modules.get("cloakbrowser.browser")
    assert stand_in is not None and "--o1567-from-the-builder" in stand_in.build_args(), (
        f"O2078_CONTROL_DID_NOT_RUN_WITHOUT_CLOAK: {stand_in}")


def test_without_cloakbrowser_the_control_still_catches_a_filter_that_keeps_enable_automation(monkeypatch, tmp_path):
    from bulk_downloader import human_challenge as hc
    _no_cloakbrowser(monkeypatch)
    subject = _subject()
    assert "--enable-automation" in hc._FORBIDDEN_FLAGS, hc._FORBIDDEN_FLAGS
    monkeypatch.setattr(hc, "_FORBIDDEN_FLAGS", ("--remote-debugging",))
    with pytest.raises(AssertionError) as caught:
        subject.test_the_plain_browser_carries_no_automation_channel(monkeypatch, tmp_path)
    text = str(caught.value)
    assert "--enable-automation" in text and "--o1567-from-the-builder" in text, (
        f"O2078_CONTROL_LOST_ITS_POWER: {text[:400]}")


class _NeverRegisters:
    """A monkeypatch that drops every sys.modules registration: the stand-in builder never reaches the product."""

    def __init__(self, real):
        self._real = real

    def setitem(self, dic, name, value):
        if dic is not sys.modules:
            self._real.setitem(dic, name, value)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_without_cloakbrowser_a_builder_the_product_never_called_is_caught(monkeypatch, tmp_path):
    _no_cloakbrowser(monkeypatch)
    subject = _subject()
    with pytest.raises(AssertionError, match="O1567_CONTROL_BUILDER_NOT_USED"):
        subject.test_the_plain_browser_carries_no_automation_channel(_NeverRegisters(monkeypatch), tmp_path)


# --- 4. an INSTALLED cloakbrowser that does not import is never stood in for -------------------------------------

HEALTHY_BROWSER = "INSTALLED = True\n\n\ndef build_args(*a, **k):\n    return []\n"
MISSING_DEP = "import _o2078_dependency_cloakbrowser_requires\n"
BROKEN = "raise ImportError('O2078 fixture: the installed cloakbrowser failed to initialise')\n"
# the error a venv WITHOUT the package raises, raised by the __init__ of a package that IS installed (cx14 F1-r2)
ROOT_NAMED = "raise ModuleNotFoundError(\"No module named 'cloakbrowser'\", name='cloakbrowser')\n"
# case: (__init__.py, browser.py or None, the import that fails, its error type, its .name)
INSTALLED_BUT_NOT_IMPORTABLE = {
    "broken-package": (BROKEN, HEALTHY_BROWSER, "cloakbrowser", ImportError, None),
    "root-named-error": (ROOT_NAMED, HEALTHY_BROWSER, "cloakbrowser", ModuleNotFoundError, "cloakbrowser"),
    "missing-dependency": (MISSING_DEP, HEALTHY_BROWSER, "cloakbrowser", ModuleNotFoundError,
                           "_o2078_dependency_cloakbrowser_requires"),
    "broken-browser-module": ("", BROKEN, "cloakbrowser.browser", ImportError, None),
    "browser-module-missing-dependency": ("", MISSING_DEP, "cloakbrowser.browser", ModuleNotFoundError,
                                          "_o2078_dependency_cloakbrowser_requires"),
    "no-browser-module": ("", None, "cloakbrowser.browser", ModuleNotFoundError, "cloakbrowser.browser"),
}


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """Installs a real on-disk cloakbrowser package ahead of any other; the import state is put back afterwards."""
    saved = {k: sys.modules[k] for k in _cloak_modules()}

    def _install(init, browser):
        pkg = tmp_path / "site-packages" / "cloakbrowser"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(init, encoding="utf-8")
        if browser is not None:
            (pkg / "browser.py").write_text(browser, encoding="utf-8")
        for name in _cloak_modules():
            del sys.modules[name]
        monkeypatch.syspath_prepend(str(pkg.parent))
        return pkg
    try:
        yield _install
    finally:
        for name in _cloak_modules():
            del sys.modules[name]
        sys.modules.update(saved)


def _fails_as(pkg, target, exc_type, exc_name):
    """Positive control: the fixture package is the installed one (the import system finds its __init__.py) and it
    fails a plain import exactly as the case says. Leaves no import cached."""
    spec = importlib.util.find_spec("cloakbrowser")
    assert spec is not None and spec.origin and Path(spec.origin) == pkg / "__init__.py", (
        f"O2078_FIXTURE_PACKAGE_NOT_INSTALLED: find_spec -> {spec!r}")
    with pytest.raises(ImportError) as direct:
        importlib.import_module(target)
    assert type(direct.value) is exc_type and direct.value.name == exc_name, (
        f"O2078_FIXTURE_PACKAGE_NOT_AS_DECLARED: {target}: {direct.value!r} name={direct.value.name!r}")
    for name in _cloak_modules():
        del sys.modules[name]


@pytest.mark.parametrize("case", sorted(INSTALLED_BUT_NOT_IMPORTABLE))
def test_an_installed_cloakbrowser_that_does_not_import_errors_the_control(case, installed, monkeypatch, tmp_path):
    init, browser, target, exc_type, exc_name = INSTALLED_BUT_NOT_IMPORTABLE[case]
    pkg = installed(init, browser)
    _fails_as(pkg, target, exc_type, exc_name)
    subject = _subject()
    with pytest.raises(ImportError) as raised:
        subject.test_the_plain_browser_carries_no_automation_channel(monkeypatch, tmp_path)
        pytest.fail(f"O2078_BROKEN_CLOAKBROWSER_SUBSTITUTED: {case}: the control ran on a stand-in")
    assert type(raised.value) is exc_type and raised.value.name == exc_name, (
        f"O2078_WRONG_IMPORT_ERROR: {raised.value!r} name={raised.value.name!r}")
    # never substituted: whatever is registered under the name came from the installed package's own files
    for name in _cloak_modules():
        origin = getattr(sys.modules[name], "__file__", None)
        assert origin and Path(origin).parent == pkg, f"O2078_BROKEN_CLOAKBROWSER_SUBSTITUTED: {name} from {origin}"


def test_an_installed_cloakbrowser_without_build_args_errors_the_control(installed, monkeypatch, tmp_path):
    """As on main: the control replaces the installed builder, it never gives an installed module one it lacks."""
    pkg = installed("", "INSTALLED = True\n")
    with pytest.raises(AttributeError, match="build_args"):
        _subject().test_the_plain_browser_carries_no_automation_channel(monkeypatch, tmp_path)
        pytest.fail("O2078_BROKEN_CLOAKBROWSER_SUBSTITUTED: the control gave the installed module a builder of its own")
    cbb = sys.modules["cloakbrowser.browser"]
    assert Path(cbb.__file__).parent == pkg and not hasattr(cbb, "build_args"), cbb


@pytest.mark.parametrize("case", ["broken-package", "missing-dependency", "root-named-error"])
def test_an_installed_cloakbrowser_that_does_not_import_never_gets_the_stock_chromium(case, installed, monkeypatch):
    init, browser, target, exc_type, exc_name = INSTALLED_BUT_NOT_IMPORTABLE[case]
    pkg = installed(init, browser)
    _fails_as(pkg, target, exc_type, exc_name)

    def _the_installed_binary():
        return "/installed/cloak/chrome"
    hc = SimpleNamespace(plain_binary=_the_installed_binary)
    with pytest.raises(ImportError) as raised:
        got = _subject()._stock_chromium_without_cloak(monkeypatch, hc)
        pytest.fail(f"O2078_BROKEN_CLOAKBROWSER_GOT_STOCK_CHROMIUM: {case}: substituted {got}")
    assert type(raised.value) is exc_type and raised.value.name == exc_name, (
        f"O2078_WRONG_IMPORT_ERROR: {raised.value!r} name={raised.value.name!r}")
    assert hc.plain_binary is _the_installed_binary, "O2078_BROKEN_CLOAKBROWSER_GOT_STOCK_CHROMIUM"


def test_an_installed_cloakbrowser_is_used_as_it_is(installed, monkeypatch):
    """Control for the cases above: the fixture package is the one imported, and nothing is substituted for it."""
    pkg = installed("", HEALTHY_BROWSER)
    subject = _subject()
    cbb = subject._flag_builder_module(monkeypatch)
    assert getattr(cbb, "INSTALLED", False) is True and Path(cbb.__file__).parent == pkg, cbb

    def _the_installed_binary():
        return "/installed/cloak/chrome"
    hc = SimpleNamespace(plain_binary=_the_installed_binary)
    assert subject._stock_chromium_without_cloak(monkeypatch, hc) is None
    assert hc.plain_binary is _the_installed_binary


# --- 3. the skip is AT the relaunch control: pinned by what the child run executed -------------------------------

class SessionTrace:
    """pytest plugin for the child run: the lines of the session test that executed, and the line of that test its
    skip was raised from. Written to ``out`` as JSON. A tracer that was already installed keeps receiving events."""

    def __init__(self, out):
        self.out, self.calls, self.lines, self.skip_line = out, 0, set(), None

    def _tracer(self, code, outer):
        def local(inner):
            def trace_line(frame, event, arg):
                nonlocal inner
                if event == "line":
                    self.lines.add(frame.f_lineno)
                if inner is not None:
                    inner = inner(frame, event, arg)
                return trace_line
            return trace_line

        def trace_call(frame, event, arg):
            inner = outer(frame, event, arg) if outer is not None else None
            return local(inner) if frame.f_code is code else inner
        return trace_call

    @pytest.hookimpl(wrapper=True)
    def pytest_pyfunc_call(self, pyfuncitem):
        if pyfuncitem.name != SESSION:
            return (yield)
        code, outer = pyfuncitem.obj.__code__, sys.gettrace()
        self.calls += 1
        sys.settrace(self._tracer(code, outer))
        try:
            return (yield)
        except pytest.skip.Exception as skipped:
            tb = skipped.__traceback__
            while tb is not None:
                if tb.tb_frame.f_code is code:
                    self.skip_line = tb.tb_lineno
                tb = tb.tb_next
            raise
        finally:
            sys.settrace(outer)
            Path(self.out).write_text(json.dumps(
                {"calls": self.calls, "lines": sorted(self.lines), "skip_line": self.skip_line}), encoding="utf-8")


def _session_test_shape(src):
    """From the subject's source: the line of the relaunch control's importorskip, and every assert before it."""
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == SESSION)
    controls = [n.lineno for n in ast.walk(fn)
                if isinstance(n, ast.Call) and ast.unparse(n.func) == "pytest.importorskip"]
    assert len(controls) == 1, f"O2078_SESSION_TEST_SHAPE: one importorskip expected, at lines {controls}"
    asserts = {n.lineno: ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Assert) and n.lineno < controls[0]}
    return controls[0], asserts


def _session_faults(src, trace):
    """Why the traced run does NOT prove that only the relaunch control was skipped. Empty when it does."""
    control, asserts = _session_test_shape(src)
    faults = [f"no assert on {needed!r} is written before the relaunch control (line {control})"
              for needed in SESSION_ASSERTIONS if not any(needed in text for text in asserts.values())]
    if trace["calls"] != 1:
        faults.append(f"the session test was called {trace['calls']} time(s), not once")
    if trace["skip_line"] != control:
        faults.append(f"the skip was raised at line {trace['skip_line']}, not at the relaunch control (line {control})")
    ran = set(trace["lines"])
    faults += [f"the assert at line {line} never ran: {text[:90]}" for line, text in sorted(asserts.items())
               if line not in ran]
    return faults


def _run_session_child(subject_file, out_dir):
    """The session test (and one test that passes) in a child without cloakbrowser, traced. -> (output, trace)"""
    trace_file = out_dir / "session-trace.json"
    env = {k: v for k, v in os.environ.items() if k != "BD_INSTALL_DIR" and not k.startswith("PYTEST_XDIST")}
    env["BD_DISABLE_KEEPALIVE"] = "1"
    env["LC_ALL"] = "C.UTF-8"
    run = subprocess.run(
        [sys.executable, "-c", CHILD, str(Path(__file__).resolve()), str(trace_file),
         f"{subject_file}::{SESSION}", f"{subject_file}::test_the_plain_browser_carries_no_automation_channel",
         "-q", "-rs", "--no-header", "-p", "no:cacheprovider", "-p", "no:randomly"],
        cwd=str(HERE.parent), env=env, capture_output=True, text=True, timeout=200, check=False)
    out = run.stdout + run.stderr
    # what r1 pinned, and all an early skip needs to satisfy: the counts and the named reason
    assert run.returncode == 0 and "1 passed, 1 skipped" in out, f"O2078_NOCLOAK_SESSION_TEST_RED: rc={run.returncode}\n{out[-1500:]}"
    skips = [ln for ln in out.splitlines() if ln.startswith("SKIPPED")]
    assert len(skips) == 1 and CONTROL_REASON in skips[0], skips
    assert trace_file.is_file(), f"O2078_SESSION_NOT_TRACED: {out[-1500:]}"
    return out, json.loads(trace_file.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def traced_session(tmp_path_factory):
    return _run_session_child(SUBJECT, tmp_path_factory.mktemp("o2078-session"))[1]


def test_without_cloakbrowser_the_session_test_skips_only_the_relaunch_control(traced_session):
    src = SUBJECT.read_text(encoding="utf-8")
    control, asserts = _session_test_shape(src)
    assert len(asserts) >= len(SESSION_ASSERTIONS), asserts
    faults = _session_faults(src, traced_session)
    assert faults == [], "O2078_SESSION_SKIPPED_BEFORE_ITS_ASSERTIONS:\n" + "\n".join(faults)
    assert traced_session["skip_line"] == control and set(asserts) <= set(traced_session["lines"])


def test_a_skip_at_the_entry_of_the_session_test_is_rejected(tmp_path):
    """The variant r1 accepted (cx11 F2): the named reason, the same counts, and not one session assertion run."""
    src = SUBJECT.read_text(encoding="utf-8")
    anchor = f"def {SESSION}(site, tmp_path, monkeypatch, capsys):\n"
    assert src.count(anchor) == 1
    early = anchor + ('    if sys.modules.get("cloakbrowser", True) is None:\n'
                      f'        pytest.skip("{CONTROL_REASON}: an entry skip that bypasses the session")\n')
    variant = tmp_path / SUBJECT.name
    variant.write_text(src.replace(anchor, early), encoding="utf-8")
    _out, trace = _run_session_child(variant, tmp_path)      # counts and reason alone accept it
    control, asserts = _session_test_shape(variant.read_text(encoding="utf-8"))
    faults = _session_faults(variant.read_text(encoding="utf-8"), trace)
    assert any("not at the relaunch control" in f for f in faults), f"O2078_EARLY_SKIP_ACCEPTED: {faults} {trace}"
    assert sum("never ran" in f for f in faults) == len(asserts) >= len(SESSION_ASSERTIONS), (faults, asserts)
    assert trace["skip_line"] is not None and trace["skip_line"] < min(asserts) < control, (trace, control)


def test_any_session_assertion_the_skip_got_ahead_of_is_rejected(traced_session):
    """From the real traced run: each assert before the control, taken out of what ran, is a fault of its own; so is
    a skip raised from any of those lines, and a session test that was never called."""
    src = SUBJECT.read_text(encoding="utf-8")
    control, asserts = _session_test_shape(src)
    assert _session_faults(src, traced_session) == []
    for line in asserts:
        cut_short = dict(traced_session, lines=[n for n in traced_session["lines"] if n != line])
        assert any(f"line {line} never ran" in f for f in _session_faults(src, cut_short)), (
            f"O2078_UNRUN_ASSERT_ACCEPTED: line {line}: {asserts[line]}")
        skipped_there = _session_faults(src, dict(traced_session, skip_line=line))
        assert any("not at the relaunch control" in f for f in skipped_there), (
            f"O2078_EARLY_SKIP_ACCEPTED: a skip raised at line {line}")
    assert _session_faults(src, dict(traced_session, skip_line=None)), "O2078_UNLOCATED_SKIP_ACCEPTED"
    assert _session_faults(src, dict(traced_session, calls=0)), "O2078_UNCALLED_SESSION_ACCEPTED"
    # an importorskip moved ahead of the session assertions leaves none of them before the control
    start = "    session.start()\n"
    assert src.count("pytest.importorskip(") == 1 and src.count(start) == 1
    moved = src.replace("pytest.importorskip(", "print(")
    moved = moved.replace(start, '    pytest.importorskip("cloakbrowser")\n' + start)
    faults = _session_faults(moved, traced_session)
    assert sum("is written before the relaunch control" in f for f in faults) == len(SESSION_ASSERTIONS), (
        f"O2078_MOVED_CONTROL_ACCEPTED: {faults}")
