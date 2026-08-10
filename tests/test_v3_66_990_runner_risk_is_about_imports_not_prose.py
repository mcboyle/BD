"""The runner-import risk is about IMPORTS. A comment is not an import.

@990. `capture_lanes` pins a test file to the serial lane when its source
contains `import run_tests`, `from run_tests`, or the literal `run_tests.py`.
The rule is right and its stated reason is literally true -- importing the
fallback runner rewires global interpreter state:

    env_before null -> env_after "1"     (run_tests_core.py:28, setdefault)
    sys.path gains  /home/user/BD         (run_tests_core.py:33, unrestored)

measured in a fresh subprocess with the variable POPPED, which is the only way
to see it: `tests/conftest.py:196` sets the same flag unconditionally and every
band exports it, so a probe that merely refrains from setting it exercises the
flagged half and reports the import inert. A previous session's proposal to
narrow this rule rested on exactly that mistake and was refuted.

BUT THE CHECK READS PROSE. Measured over 1277 tracked test files:

    pinned serial by the rule                        143
      ...that genuinely import the runner (AST)        4
      ...that only MENTION it in a comment/docstring 139
    still pinned after stripping comments+docstrings   19

So ~124 files sit in the ~12-minute serial lane because they mention the runner
in prose. That is section 0's "a comment is inside the denominator of every gate
that reads source text", at scale, and this file's own docstring would trip it.

THIS IS NOT THE REFUTED CHANGE. That one narrowed the snippet list, which would
have freed files whose imports are real. This keeps the rule byte-identical and
applies it to CODE, so every file that actually touches the runner stays serial
-- including the dynamic forms `importlib.import_module("run_tests")` and
`__import__("run_tests")`, which are code and survive stripping.

WHAT THIS CUT DOES NOT DELIVER, stated because the obvious reading is wrong.
It frees ZERO files today. The absolute checks sit ABOVE the allowlist, so a
prose-matched file cannot be promoted by review; once the check is comment-aware
those files fall through to the allowlist, are not on it (measured: 0 of 124),
and reach the fail-closed default. The speedup needs an allowlist regen backed
by a measured all-parallel sweep on the box -- the v3.66.923 precedent -- which
is a separate cut with separate evidence. What this cut changes is that the 124
become ELIGIBLE for that review instead of structurally unreachable.

ADDENDUM -- the successor cut this file anticipated, and how it differs from
the refuted change. The absolute rule now reads LOADS, not literals: a static
import, a loader-capable call whose argument names the runner, a
monkeypatch/mock dotted path, runpy, or -- fail-closed -- any loader-capable
call fed a VARIABLE in a file that also names the runner. All of those stay
serial ABSOLUTELY; review cannot override one (pinned below). What no longer
pins is a literal the file's own interpreter can never execute: a subprocess
argv, a tmp-tree fixture path, an assertion message. Re-measured for this cut
in a fresh subprocess with BD_DISABLE_KEEPALIVE POPPED, cwd=repo root:

    run_tests_core  env_before null -> env_after null, path_added [],
                    cwd unchanged, no pytest/flask/bd module enters sys.modules
    run_tests       identical

That probe is CONTEXT for why the rule's grip needed re-measuring -- it is NOT
the licence for the promotions. The promotions rest on the process boundary: a
child interpreter mutating its own env/sys.path never touched this one, even
when the import-time mutation was real. So if run_tests_core regressed
tomorrow, no promoted file would be affected -- none of them ever brings the
runner into its own interpreter -- while every file that does load it is still
serial, absolutely. The @986 refutation (SESSION_CARRY 'DO NOT narrow')
therefore STANDS: that proposal freed files whose imports were real, on a probe
whose harness inherited the flag. This cut frees no file that loads the runner,
and was cut with the register's own prescribed sequence: harden the import
(@990), then free the population honestly. Measured over 1279 tracked test
files at eb0c00b: old rule pinned 23, precise rule pins 11 (7 in-process
loaders + 4 fail-closed indirections), 0 currently-parallel files newly pinned.
"""

import ast
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import capture_lanes as lanes                                    # noqa: E402

# An allowlisted path, so the classifier reaches the allowlist branch and the
# result turns on the source check alone. Same file the pre-existing guard test
# uses, deliberately.
ALLOWLISTED = "tests/test_validators.py"


def test_a_DOCSTRING_mention_no_longer_pins_a_reviewed_file_to_serial():
    """RED before this cut: the substring match fires on the docstring and the
    file is serial, and no allowlist entry can override it."""
    source = ('"""This suite does not use run_tests.py at all."""\n'
              "def test_x():\n    assert True\n")
    assert lanes.classify_capture_file(ALLOWLISTED, source=source) == "parallel", (
        "a reviewed file was pinned serial for a docstring")


def test_a_COMMENT_mention_no_longer_pins_a_reviewed_file_to_serial():
    source = ("# see run_tests.py for the fallback runner\n"
              "def test_x():\n    assert True\n")
    assert lanes.classify_capture_file(ALLOWLISTED, source=source) == "parallel"


def test_a_REAL_import_still_pins_the_file_however_it_is_written():
    """The other direction, and the reason the rule exists. Every one of these
    is CODE and survives stripping. The dynamic forms are the pre-existing
    guard's own cases -- if this cut broke them it would be reintroducing the
    refuted change under a new name.

    Two changes at the successor cut, in opposite directions. The bare
    name-binding case (a runner string assigned to a variable, no loader call
    anywhere) MOVED to the contained-literal test below: with no import and no
    loader-capable call, nothing in the file can execute the runner in this
    interpreter, and it is byte-for-byte the fixture shape of the promoted
    subprocess drivers. And three load forms JOINED that the old substring
    instrument could not all see -- the monkeypatch dotted path was invisible
    to it (the quote-anchored literal regex requires the closing quote right
    after the module name, and `"run_tests_core.ATTR"` defeats it), so the
    precise rule is WIDER here, not narrower: pytest resolves that string by
    importing the module into this interpreter."""
    for source in (
        "import run_tests\n",
        "from run_tests_core import main\n",
        'import importlib\nimportlib.import_module("run_tests")\n',
        'from importlib import import_module as load\nload("run_tests_core")\n',
        '__import__("run_tests")\n',
        'import runpy\nrunpy.run_path("run_tests.py")\n',
        'def test_x(monkeypatch):\n'
        '    monkeypatch.setattr("run_tests_core._FILE_TIMEOUT_S", 1)\n',
        'import importlib\nload = importlib.import_module\n'
        'NAME = "run_tests_core"\nload(NAME)\n',
    ):
        assert lanes.classify_capture_file(ALLOWLISTED, source=source) == "serial", (
            "the runner-import risk stopped being detected in: %r" % source)


def test_a_CONTAINED_literal_no_longer_pins_a_reviewed_file():
    """RED before the successor cut: each of these named the runner only where
    this interpreter can never execute it, and was pinned anyway.

    Measured over 1279 tracked test files at eb0c00b: 12 real files carried
    the runner name ONLY in subprocess argv, tmp-tree fixture paths, heredoc
    driver strings or assertion messages -- never a load -- and sat in the
    serial lane for it (~135s of the box's 903s serial lane). The child
    process that DOES import the runner mutates its own interpreter and exits;
    the hazard the rule names cannot reach this one. Same correction as @990
    (a comment is not an import) and @998 (a heredoc loader-call is not an
    in-process load), one step further: an argv literal is not a load either.

    ONLY for a REVIEWED file: the companion test below pins that an unlisted
    file with the same shapes stays serial."""
    for source in (
        # the case that moved from the load list above, verbatim
        'RUNNER = "run_tests_core"\n',
        # the t49/796 shape: the runner as a child-process argv
        'import subprocess, sys\n'
        'def test_x(tmp_path):\n'
        '    subprocess.run([sys.executable, "run_tests.py", "--json"],\n'
        '                   timeout=120)\n',
        # the 908/909/910/911/885 shape: a heredoc driver for a fresh
        # interpreter; the import statement inside the STRING never parses
        # as an import node of this file
        '_DRIVER = "import run_tests_core as R; R.main()"\n'
        'import subprocess, sys\n'
        'def test_x():\n'
        '    subprocess.run([sys.executable, "-c", _DRIVER], timeout=120)\n',
        # the OVER-SENSITIVE direction, pinned in the same test on purpose:
        # an OBJECT-form monkeypatch (module object first, no dotted string)
        # imports nothing, so its presence beside an argv literal must not
        # trip the fail-closed indirection rule -- only LOADER-capable calls
        # join that rule, and a patcher is one only when its argument names
        # the runner
        'import subprocess, sys\n'
        'def test_x(monkeypatch):\n'
        '    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)\n'
        '    subprocess.run([sys.executable, "run_tests.py"], timeout=60)\n',
    ):
        assert lanes.classify_capture_file(ALLOWLISTED, source=source) == "parallel", (
            "a reviewed file was pinned serial for a literal nothing in it "
            "can execute: %r" % source)


def test_a_contained_literal_in_an_UNLISTED_file_still_defaults_serial():
    """The fail-closed half of the promotion. Freeing the literal from the
    ABSOLUTE rule must not free it from review: an unlisted subprocess driver
    falls through the allowlist to the default, and the default is serial."""
    source = ('import subprocess, sys\n'
              'def test_x():\n'
              '    subprocess.run([sys.executable, "run_tests.py"], timeout=60)\n')
    assert lanes.classify_capture_file(
        "tests/test_a_file_that_is_not_on_the_allowlist_990b.py",
        source=source) == "serial"


def test_UNPARSEABLE_source_falls_back_to_the_raw_text():
    """Fail-closed. A file this module cannot read is not proven safe, and the
    strip must not become a way to hide a real import behind a syntax error.

    BOTH failure stages are driven, because they are separate fallbacks and the
    first fixture here reached only one of them. `def broken(:` fails in the
    TOKENIZER, so the AST fallback below it never ran -- a mutant that turned
    that branch into `return ""` escaped a green battery. `x = = 1` tokenizes
    cleanly and fails `ast.parse`, which is the only way to exercise it."""
    for stage, source in (
        ("tokenizer", "import run_tests\ndef broken(:\n"),
        ("ast.parse", "import run_tests\nx = = 1\n"),
        ("ast.parse", "import run_tests\n  bad_indent = 1\n"),
    ):
        assert lanes.classify_capture_file(ALLOWLISTED, source=source) == "serial", (
            "a real import was lost when the %s stage failed" % stage)
        assert "run_tests" in lanes.code_only(source), (
            "the %s fallback returned less text than it was given, which is how "
            "a strip becomes a hiding place" % stage)


def test_an_UNLISTED_file_is_still_serial_whatever_its_prose_says():
    """The property this module exists for. Making the check comment-aware must
    not promote anything the allowlist has not reviewed -- the v3.66.923
    regression, where moving the allowlist above the heuristics briefly turned
    the fail-closed default into `parallel` and promoted the whole repo."""
    source = '"""mentions run_tests.py"""\ndef test_x():\n    assert True\n'
    assert lanes.classify_capture_file(
        "tests/test_a_file_that_is_not_on_the_allowlist_990.py",
        source=source) == "serial"


# --------------------------------------------------------------------------
# The other half: remove the state mutation the rule exists to protect against.
# --------------------------------------------------------------------------

_PROBE = (
    "import os,sys,json;"
    "b=(os.environ.get('BD_DISABLE_KEEPALIVE'), list(sys.path));"
    "__import__(%r);"
    "a=(os.environ.get('BD_DISABLE_KEEPALIVE'), list(sys.path));"
    "print(json.dumps({'env_before':b[0],'env_after':a[0],"
    "'path_added':[p for p in a[1] if p not in b[1]]}))"
)


def _import_clean(module):
    """Import `module` with BD_DISABLE_KEEPALIVE POPPED.

    POPPED, not merely left alone: the parent's value is part of the
    denominator, and inheriting it makes `setdefault` invisible. That is the
    trap the refuted proposal fell into."""
    env = dict(os.environ)
    env.pop("BD_DISABLE_KEEPALIVE", None)
    env.setdefault("BD_INSTALL_DIR", "/tmp")
    r = subprocess.run([sys.executable, "-c", _PROBE % module], cwd=str(REPO),
                       env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, "probe failed: %s" % r.stderr[-600:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_importing_the_runner_no_longer_rewires_the_interpreter():
    out = _import_clean("run_tests_core")
    assert out["env_before"] is None, "the harness leaked the flag it must pop"
    assert out["env_after"] is None, (
        "import set BD_DISABLE_KEEPALIVE=%r process-wide" % out["env_after"])
    assert out["path_added"] == [], (
        "import prepended %r to sys.path" % out["path_added"])


def test_importing_run_tests_is_inert_too():
    """`run_tests.py` does `from run_tests_core import ...` at module scope, so
    it inherits whatever the core module does at import time."""
    out = _import_clean("run_tests")
    assert out["env_after"] is None and out["path_added"] == [], out


def test_the_RUNNER_still_gets_the_state_when_it_RUNS():
    """The state is not wrong, its TIMING was. A fix that simply deleted it
    would pass every assertion above and break the runner."""
    env = dict(os.environ)
    env.pop("BD_DISABLE_KEEPALIVE", None)
    env.setdefault("BD_INSTALL_DIR", "/tmp")
    code = ("import os,sys,json,run_tests_core as R;R._prepare_runner_state();"
            "R._prepare_runner_state();"
            "print(json.dumps({'env':os.environ.get('BD_DISABLE_KEEPALIVE'),"
            "'n':sys.path.count(str(R.PKG_ROOT))}))")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-600:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["env"] == "1", (
        "keep-alive threads are no longer disabled for the runner; importing "
        "bulk_downloader.app then spawns daemons that call do_login() against "
        "the isolated tmpdirs individual tests use")
    assert out["n"] == 1, "preparing twice duplicated the sys.path entry"


def test_an_ALREADY_SET_flag_is_not_overwritten():
    """`setdefault`, not assignment -- an operator running with the flag
    deliberately "0" keeps it. This is the half the refuted probe accidentally
    exercised, so it is pinned rather than assumed."""
    env = dict(os.environ)
    env["BD_DISABLE_KEEPALIVE"] = "0"
    env.setdefault("BD_INSTALL_DIR", "/tmp")
    code = ("import os,run_tests_core as R;R._prepare_runner_state();"
            "print(os.environ['BD_DISABLE_KEEPALIVE'])")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-600:]
    assert r.stdout.strip().splitlines()[-1] == "0"


def test_review_still_cannot_override_an_in_process_load():
    """The invariant the retired snippet-tuple pin was standing in for.

    The old pin asserted the substring tuple verbatim, guarding against a
    session narrowing the rule "with a fresher justification" while the
    import-time mutation was real. That mutation is gone (measured for the
    successor cut, flag POPPED in a fresh subprocess: env null -> null,
    path_added []), and the successor cut re-instrumented the rule from
    substrings to AST loads -- so the thing to pin is no longer the tuple, it
    is the PROPERTY the tuple existed to protect: a file that can execute the
    runner in its own interpreter is serial even when reviewed, and the
    allowlist has not been emptied to make that vacuous.

    Every form here is an in-process load. If any of them ever classifies
    parallel for an ALLOWLISTED path, the refuted change has been reproduced,
    whatever the justification reads."""
    for source in (
        "import run_tests\n",
        "import run_tests_core as run_tests\n",
        'import importlib\nimportlib.import_module("run_tests_core")\n',
        'import importlib.util\n'
        'spec = importlib.util.spec_from_file_location('
        '"rtc", "run_tests_core.py")\n',
        'def test_x(monkeypatch):\n'
        '    monkeypatch.setattr("run_tests_core.main", lambda: None)\n',
    ):
        assert lanes.classify_capture_file(ALLOWLISTED, source=source) == "serial", (
            "an allowlist entry overrode an in-process load: %r" % source)
    n = len(lanes.parallel_allowlist())
    assert n > 1000, "allowlist unexpectedly small: %d" % n


def test_the_stripper_removes_prose_and_keeps_code():
    """Driven directly, because everything above reaches it through
    `classify_capture_file` and a stripper that returned its input unchanged
    would still let the import cases pass."""
    src = ('"""doc mentions run_tests.py"""\n'
           "# comment mentions run_tests.py\n"
           "import os\n"
           "def f():\n"
           '    """inner doc mentions run_tests.py"""\n'
           "    return os\n")
    out = lanes.code_only(src)
    assert "run_tests" not in out, "prose survived the strip: %r" % out
    assert "import os" in out, "the strip removed real code"
    ast.parse(out)
