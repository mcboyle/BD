"""@994. `bd-leakprobe` measures what a test file leaves behind -- and refuses.

WHY THE TOOL EXISTS. `tests/capture_lanes.py` pins a file to the serial lane
when its SOURCE TEXT matches `os.environ[...] =`, `sys.modules` or `os.chdir(`.
Measured over the 26 files that match: 72 `os.environ` writes, 11 `sys.modules`
writes and 2 `os.chdir` calls by AST -- and TWO of the 26 perform none at all.
They match on a docstring and on a string literal holding source for a CHILD
pytest process, where the write happens in another interpreter and can reach
nobody. The regex answers "does this file contain the shape". The shape is not
the hazard; state that OUTLIVES the file is, because `--dist loadfile` keeps a
file's tests together on one worker without giving the file its own worker.

WHAT THE TOOL MEASURES INSTEAD. env / sys.modules / cwd at `pytest_sessionstart`
against `pytest_sessionfinish`, in a fresh interpreter per file. Whatever differs
is what the next file on that worker inherits. Measured over the 26: 23 sit at
the control floor, and ONE leaked for real -- `test_v3_43_60_vpn_ui.py` left
`BD_DISABLE_VPN_RUNTIME` set process-wide, which conftest's autouse guard could
not restore because its denominator is five named keys and that is not one.
Fixed in this cut with `monkeypatch.setenv`, and both directions are pinned
below.

THE TESTS THAT MATTER HERE ARE THE REFUSALS. A probe that reports "clean" while
unable to see a leak is worse than no probe -- `bd-mutation-test`'s docstring has
carried that lesson since v3.66.737, and this file is what keeps it true for this
tool. Hence: a planted canary must be DETECTED before any verdict is issued, an
empty file list is NO VERDICT rather than "nothing leaks", and exit 2 is never a
softer exit 1.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
from importlib.machinery import SourceFileLoader

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-leakprobe"


def _load():
    """The tool is extensionless (toolchain/bin house style), so a plain import
    cannot reach it -- and `git ls-files -- '*.py'` cannot see it either, which
    is why CLAUDE.md section 1 counts that population separately."""
    loader = SourceFileLoader("bd_leakprobe", str(TOOL))
    spec = importlib.util.spec_from_loader("bd_leakprobe", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return _load()


def test_the_tool_exists_and_is_executable():
    assert TOOL.exists(), "bd-leakprobe is missing"
    assert TOOL.stat().st_mode & 0o111, "bd-leakprobe is not executable"


# ---------------------------------------------------------------------------
# The refusals. Each is a state where a verdict would be unfounded.
# ---------------------------------------------------------------------------

def test_an_empty_file_list_is_NO_VERDICT_not_a_pass(tool):
    """An empty denominator reports 'nothing leaks' truthfully and uselessly.
    This is the exact shape `tools/check_requirements.py` shipped with -- exit 0
    and silent stdout over a file that parsed to zero requirements."""
    assert tool.main([]) == 2


def test_zero_controls_is_NO_VERDICT_because_there_is_then_no_floor(tool):
    """Without controls every ambient fixture reads as a leak, so the tool would
    fire on identity rather than content -- section 0's inverse defect. Refusing
    is the only sound answer; reporting all 26 as leaking is what the first
    build of this actually did."""
    assert tool.main(["--files", "tests/test_validators.py", "--controls", "0"]) == 2


def test_a_BLIND_probe_issues_no_verdict_even_with_files_to_probe(tool, monkeypatch):
    """THE LOAD-BEARING TEST. If the canary is not detected, the tool must not
    report on real files -- a clean result from a blind instrument is the defect
    this whole tool was written to avoid reproducing."""
    monkeypatch.setattr(tool, "_canary_ok", lambda _d: (False, "planted blindness"))
    assert tool.main(["--files", "tests/test_validators.py"]) == 2


def test_a_control_that_cannot_RUN_is_NO_VERDICT(tool, monkeypatch):
    """A floor built from a control that never executed is not a floor."""
    monkeypatch.setattr(tool, "_canary_ok", lambda _d: (True, ""))
    monkeypatch.setattr(tool, "_default_controls", lambda n: ["tests/test_validators.py"])
    monkeypatch.setattr(tool, "_run", lambda *a, **k: (None, subprocess.CompletedProcess(
        [], 1, "", "control blew up")))
    assert tool.main(["--files", "tests/test_validators.py"]) == 2


def test_exit_2_is_reachable_WITHOUT_any_file_leaking(tool, monkeypatch):
    """2 is not a softer 1. A caller that treats nonzero as 'something leaked'
    acts on a defect that was never measured -- the same conflation CLAUDE.md
    records for `git merge-base --is-ancestor`, where 1 means 'not an ancestor'
    and 128 means 'I cannot see it'."""
    monkeypatch.setattr(tool, "_canary_ok", lambda _d: (False, "blind"))
    rc = tool.main(["--files", "tests/test_validators.py"])
    assert rc == 2 and rc != 1


# ---------------------------------------------------------------------------
# Sensitivity, and the floor arithmetic that makes a verdict mean anything.
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_the_canary_is_actually_DETECTED_through_the_real_conftest(tool):
    """The positive direction. Proving only that the tool refuses when told it
    is blind would leave 'can it ever see?' unasked -- a test that passes in both
    directions is not a test.

    `-p tests.conftest` is load-bearing: it loads the real conftest as a plugin
    so its autouse fixtures apply to an out-of-tree canary. The obvious
    implementation -- copy the canary into tests/ -- is the wrong one, because
    `tools/build_pin_index.py` globs `tests/*.py` and residue from a killed run
    fails `test_pin_index_in_sync`."""
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp(prefix="leakprobe_t_"))
    (d / "bd_leakprobe_plugin.py").write_text(tool._PLUGIN, encoding="utf-8")
    ok, why = tool._canary_ok(d)
    assert ok, "the probe could not see a planted leak: %s" % why


def test_the_floor_hides_ambient_state_and_only_ambient_state(tool):
    """Driven directly. Everything else reaches `_excess` through a subprocess,
    and a floor that swallowed real leaks would still let those pass."""
    floor = {"env": {"BD_VPN_CONFIG_PATH"}, "mod_swapped": set(),
             "mod_removed": set(), "mod_nonmodule": {"typing.io"}}
    ambient = {"env_added": {"BD_VPN_CONFIG_PATH": "/x"}, "env_removed": [],
               "env_changed": {}, "cwd_changed": None, "mod_swapped": [],
               "mod_removed": [], "mod_became_nonmodule": [],
               "mod_new_nonmodule": ["typing.io"], "mod_nonmodule": ["typing.io"]}
    assert tool._excess(ambient, floor) == {}, "the floor is not absorbing ambient state"

    real = dict(ambient, env_added={"BD_VPN_CONFIG_PATH": "/x", "BD_REAL_LEAK": "1"})
    assert tool._excess(real, floor) == {"env_added": ["BD_REAL_LEAK"]}

    swapped = dict(ambient, mod_swapped=["bulk_downloader.db"])
    assert tool._excess(swapped, floor) == {"mod_swapped": ["bulk_downloader.db"]}

    # A real module REPLACED by a non-module is the shape a size check cannot
    # see, and it is dangerous under any name.
    mocked = dict(ambient, mod_became_nonmodule=["bulk_downloader.db"])
    assert tool._excess(mocked, floor) == {"mod_became_nonmodule": ["bulk_downloader.db"]}

    planted = dict(ambient, mod_new_nonmodule=["bd_leakprobe_canary_mod"])
    assert tool._excess(planted, floor) == {"mod_new_nonmodule": ["bd_leakprobe_canary_mod"]}


def test_a_planted_nonmodule_is_reported_UNDER_ANY_SPELLING(tool):
    """A DOTTED NAME IS NOT SAFER THAN A BARE ONE, and an earlier build of this
    file asserted the opposite.

    That build had `_excess` drop any dotted name outside a first-party prefix
    list, on the theory that importing `cryptography` parks lazy bindings nobody
    imports. Refuted by direct measurement -- `sys.modules["email.parser"] =
    object()` then `import email.parser` returns the planted object, and
    `concurrent.futures` and `urllib.request` behave identically.

    The real cause was never in this function: `_ModuleWithDeprecations` IS a
    ModuleType subclass, and the plugin's snapshot compared `type(v).__name__`
    to the literal "module", filing every subclass as a non-module. isinstance
    fixes it and no name filter is needed. Recorded here because a test that
    pins the WRONG behaviour is how a misdiagnosis becomes permanent."""
    floor = {"env": set(), "mod_swapped": set(), "mod_removed": set(),
             "mod_nonmodule": set()}
    base = {"env_added": {}, "env_removed": [], "env_changed": {},
            "cwd_changed": None, "mod_swapped": [], "mod_removed": [],
            "mod_became_nonmodule": [], "mod_new_nonmodule": [],
            "mod_nonmodule": []}
    for name in ("bd_plain_leak",                 # bare
                 "email.parser",                  # dotted STDLIB -- was dropped
                 "concurrent.futures",            # dotted stdlib, from-import form
                 "requests.adapters",             # dotted third party
                 "bulk_downloader.db"):           # dotted first party
        d = dict(base, mod_new_nonmodule=[name], mod_nonmodule=[name])
        assert tool._excess(d, floor) == {"mod_new_nonmodule": [name]}, (
            "a planted non-module at %r is not reported -- it would be handed "
            "to the next file that imports that name" % name)


def test_the_floor_still_absorbs_state_the_lane_already_tolerates(tool):
    """THE OVER-SENSITIVE DIRECTION. Removing the name filter must not turn
    every ambient object into a leak: the first build reported all 26 wave-3
    candidates as leaking, and a gate that cries wolf gets switched off. The
    floor -- not a name rule -- is what absorbs that."""
    floor = {"env": {"BD_VPN_CONFIG_PATH"}, "mod_swapped": set(),
             "mod_removed": set(), "mod_nonmodule": {"typing.io", "_openssl.lib"}}
    d = {"env_added": {"BD_VPN_CONFIG_PATH": "/x"}, "env_removed": [],
         "env_changed": {}, "cwd_changed": None, "mod_swapped": [],
         "mod_removed": [], "mod_became_nonmodule": [],
         "mod_new_nonmodule": ["typing.io", "_openssl.lib"],
         "mod_nonmodule": ["typing.io", "_openssl.lib"]}
    assert tool._excess(d, floor) == {}, "the floor stopped absorbing ambient state"


def test_a_ModuleType_SUBCLASS_counts_as_a_module(tool):
    """The root cause, pinned at the plugin rather than at its symptom.

    cryptography installs `_ModuleWithDeprecations` over its cipher submodules.
    `isinstance(o, types.ModuleType)` is True for it; `type(o).__name__` is not
    "module". The snapshot used the second form, so two files read as leaking
    for importing cryptography and TWO successive repairs were built on that
    reading before anyone printed the object's type."""
    import types
    ns = {}
    exec(compile(tool._PLUGIN, "<plugin>", "exec"), ns)
    kind = ns["_kind"]

    class _ModuleWithDeprecations(types.ModuleType):
        pass

    assert kind(_ModuleWithDeprecations("x")) == "module", (
        "a ModuleType subclass is being filed as a non-module -- the exact "
        "defect that produced two false leak reports")
    assert kind(types.ModuleType("y")) == "module"
    assert kind(object()) != "module", "a planted object must not read as a module"


def test_the_floor_is_derived_from_files_the_lane_ALREADY_ships(tool):
    """Hand-listing controls would let the floor describe a lane the repo has
    moved on from. Derived from the classifier, so it cannot go stale silently."""
    sys.path.insert(0, str(REPO / "tests"))
    import capture_lanes as lanes
    controls = tool._default_controls(3)
    assert len(controls) == 3, "no controls derived -- there would be no floor"
    for c in controls:
        assert lanes.classify_capture_file(REPO / c) == "parallel", (
            "%s is not in the parallel lane, so its state is not known-tolerated" % c)


# ---------------------------------------------------------------------------
# The defect the tool found, pinned in both directions.
# ---------------------------------------------------------------------------

def test_the_vpn_ui_env_writes_go_through_monkeypatch_not_os_environ():
    """RED before this cut, MEASURED not assumed: probing the pristine file
    reported `env_added: ['BD_DISABLE_VPN_RUNTIME']` -- it survived the file and
    would have reached whatever xdist scheduled next on that worker.

    Asserted over COMMENT-STRIPPED source, because the docstring added in this
    cut explains the fix by naming the construct it removed, and a check that
    cannot tell prose from code would fail the repair that closed it. That
    happened four times in the v3.66.876-879 session."""
    sys.path.insert(0, str(REPO / "tests"))
    import capture_lanes as lanes
    src = (REPO / "tests" / "test_v3_43_60_vpn_ui.py").read_text(encoding="utf-8")
    code = lanes.code_only(src)
    assert 'os.environ["BD_DISABLE_VPN_RUNTIME"]' not in code, (
        "the unrestored raw write is back")
    assert "monkeypatch.setenv" in code, "the restoring form is gone"


def test_the_tool_never_writes_into_the_work_tree(tool):
    """Residue in tests/ inflates `build_pin_index`'s `test_files_scanned` and
    fails `test_pin_index_in_sync` -- section 2a's measured consequence, where
    three stray RED files from other cuts did exactly that. The canary therefore
    lives in a tmpdir and the plugin is written beside it."""
    src = TOOL.read_text(encoding="utf-8")
    assert "mkdtemp" in src, "the tool no longer stages its canary out of tree"
    before = sorted(p.name for p in (REPO / "tests").glob("*.py"))
    tool.main(["--selftest"])
    after = sorted(p.name for p in (REPO / "tests").glob("*.py"))
    assert before == after, "bd-leakprobe left residue in tests/: %s" % (
        set(after) - set(before))
