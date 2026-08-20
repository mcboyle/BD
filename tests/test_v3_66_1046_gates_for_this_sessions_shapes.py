"""Three gates for three failures that were found by luck, plus one audit.

Each of these shipped, or nearly shipped, in the v3.66.1042-1045 batches. None
was caught by review; each was caught by an unrelated accident, and that is the
argument for a gate rather than a paragraph.

  1. A test handed its own file to a subprocess pytest. Every spawned run
     spawned two more: 301 processes, killed by hand, no test result at all.
     Caught because the run hung.
  2. `pytest_terminal_summary` was defined twice in one conftest, so the second
     silently replaced the socket recorder's. Caught because a line I happened
     to be looking at went missing. No error, no warning, green suite.
  3. A test registered a job in the REAL /tmp/bd-jobs. 410 entries accumulated,
     one per run per xdist worker. Caught because bd-fleet was run against the
     fleet for an unrelated reason and its jobs column read 410 beside pytest 0.
  4. ANSI escapes made every line-anchored matcher blind. bd-ab returned an
     EMPTY failure set for a coloured log, and bd-ab reports nothing but failure
     counts. Caught because bd-run's UNKNOWN third state fired on a clean band.

The fourth is an audit rather than a behaviour: the tools are fixed, and this
requires them to stay fixed.
"""
import ast
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_TESTS = _REPO / "tests"
_BIN = _REPO / "toolchain" / "bin"


def _tracked(pattern):
    r = subprocess.run(["git", "ls-files", "-z", pattern], cwd=str(_REPO),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [p for p in r.stdout.split("\0") if p]


# ── 1. a test must not hand its own file to a subprocess pytest ──────────────

def _spawns_pytest(source):
    """Does this file start pytest in a child process?

    Deliberately textual and deliberately broad. The two spellings that matter
    are `"-m", "pytest"` in an argv list and `-m pytest` in a shell string, and
    a check that missed either would report clean over the case it exists for.
    """
    return '"pytest"' in source and '"-m"' in source or "-m pytest" in source


def _string_constants(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub


def _spawn_calls(tree):
    """Calls that start pytest, and only those.

    TWO NARROWINGS, both paid for. Scanning every string constant in the file
    flagged tests/test_desandbox_tool_verifiers.py, where the filename sits in
    a DATA literal -- a set of known carriers -- and nothing was ever spawned.
    Over-sensitivity is a soundness bug, not a safe default: a gate that fires
    on correct code gets switched off. Scanning LINES instead missed the
    ordinary multi-line call, because the filename and the word "pytest" live
    on different lines. So: the constants that are arguments OF a call that
    spawns pytest.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        args = [c.value for c in _string_constants(node)]
        func = node.func
        fname = getattr(func, "attr", None) or getattr(func, "id", "") or ""
        via_argv = "pytest" in args and "-m" in args
        via_shell = any("-m pytest" in a for a in args)
        via_helper = "pytest" in fname.lower()
        if via_argv or via_shell or via_helper:
            yield node


def _own_file_offenders(rel, source):
    """Arguments to a pytest-spawning call in `rel` that name `rel` itself."""
    if not _spawns_pytest(source):
        return []
    name = os.path.basename(rel)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for call in _spawn_calls(tree):
        for const in _string_constants(call):
            text = const.value
            # NO SEPARATE `"::" in text` SKIP. It was here and a mutant removing
            # it escaped, because it can never decide anything: a node id ends
            # in `::test_name`, so it fails the equality and the endswith on its
            # own. A branch nothing can reach is dead code that reads as a
            # safety feature -- CLAUDE.md section 10. The node-id case is a
            # CONTROL below, not a special case here.
            if text == name or text == rel or text.endswith("/" + name):
                out.append("%s:%d: %r" % (rel, const.lineno, text))
    return out


def test_the_own_file_scan_can_actually_see_the_fork_bomb():
    """THE CONTROL, and it is the whole value of this gate.

    A green gate over a clean tree proves nothing about whether the gate works.
    This feeds it the exact shape that cost 301 processes, and the exact shape
    that is FINE, and requires it to tell them apart.
    """
    bomb = ('import subprocess, sys\n'
            'def test_x():\n'
            '    subprocess.run([sys.executable, "-m", "pytest",\n'
            '                    "tests/test_thing.py", "-q"])\n')
    hits = _own_file_offenders("tests/test_thing.py", bomb)
    assert hits, "the scanner did not see a file handing pytest its own path"

    fine = ('import subprocess, sys\n'
            'def test_x():\n'
            '    subprocess.run([sys.executable, "-m", "pytest",\n'
            '                    "tests/test_thing.py::test_other", "-q"])\n')
    assert not _own_file_offenders("tests/test_thing.py", fine), (
        "a NODE ID was reported as a fork bomb. It selects one test and cannot "
        "re-enter the spawner; a gate that fires on it gets switched off")

    other = ('import subprocess, sys\n'
            'def test_x():\n'
            '    subprocess.run([sys.executable, "-m", "pytest",\n'
            '                    "tests/test_elsewhere.py", "-q"])\n')
    assert not _own_file_offenders("tests/test_thing.py", other), (
        "naming a DIFFERENT file was reported as self-reentry")

    # THE FALSE POSITIVE THIS GATE ACTUALLY PRODUCED, on its first run over the
    # tree: the filename in a DATA literal, with nothing spawned from it.
    # test_desandbox_tool_verifiers.py keeps its own path in a set of known
    # carriers because the literal appears in its own assertion.
    data = ('import subprocess, sys\n'
            '_KNOWN = sorted(["tools/x.py", "tests/test_thing.py"])\n'
            'def test_x():\n'
            '    subprocess.run([sys.executable, "-m", "pytest",\n'
            '                    "tests/test_other.py::test_y", "-q"])\n')
    assert not _own_file_offenders("tests/test_thing.py", data), (
        "the file's own name in a DATA literal was reported as a fork bomb. "
        "A gate that fires on correct code gets switched off")

    # And the multi-line spawn, which the first line-based version missed.
    multi = ('import subprocess, sys\n'
             'def test_x():\n'
             '    subprocess.run([\n'
             '        sys.executable,\n'
             '        "-m",\n'
             '        "pytest",\n'
             '        "tests/test_thing.py",\n'
             '    ])\n')
    assert _own_file_offenders("tests/test_thing.py", multi), (
        "a spawn whose filename is on a different line from the word pytest "
        "was missed -- that is the ordinary formatting of this call")


def test_no_test_hands_its_own_file_to_a_subprocess_pytest():
    """THE FORK BOMB, gated.

    A test that spawns pytest and passes its own path re-enters itself, and
    every child does the same. Measured at v3.66.1044: 301 processes and no
    result. A node id (`file.py::test_name`) is fine -- it selects one test,
    and the one it selects is not the one that spawns.

    The denominator is stated because it is the whole risk here: every TRACKED
    file under tests/, not a sample.
    """
    files = _tracked("tests/*.py")
    assert len(files) > 100, (
        "git ls-files returned %d test files -- the denominator collapsed and "
        "a pass here would mean nothing" % len(files))

    offenders = []
    checked = 0
    for rel in files:
        source = (_REPO / rel).read_text(encoding="utf-8", errors="replace")
        if not _spawns_pytest(source):
            continue
        checked += 1
        offenders.extend(_own_file_offenders(rel, source))

    assert checked, (
        "no test file was found to spawn pytest at all, so this gate ran over "
        "an empty denominator")
    assert not offenders, (
        "a test spawns pytest and names its OWN file without a node id. Every "
        "child re-runs this test and spawns two more:\n  "
        + "\n  ".join(offenders))


# ── 2. one hook name, one definition ─────────────────────────────────────────

def _hook_defs(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("pytest_"):
            names.append((node.name, node.lineno))
    return names


def _duplicate_hooks(rel, defs):
    """Hook names defined more than once in one module. Pure, so it can be
    controlled -- a mutant that stopped reporting duplicates escaped while this
    logic lived inline, because the real conftest has none and the assertion
    could not fire either way."""
    problems, seen = [], {}
    for name, line in defs:
        if name in seen:
            problems.append(
                "%s: %s defined at line %d AND line %d -- the second silently "
                "replaces the first" % (rel, name, seen[name], line))
        seen[name] = line
    return problems


def test_the_duplicate_hook_detector_reports_a_duplicate_and_only_a_duplicate():
    dupes = _duplicate_hooks("c.py", [("pytest_configure", 1),
                                      ("pytest_terminal_summary", 10),
                                      ("pytest_terminal_summary", 40)])
    assert len(dupes) == 1 and "pytest_terminal_summary" in dupes[0]
    assert "10" in dupes[0] and "40" in dupes[0], (
        "the report does not name both lines, so it cannot be acted on: %s"
        % dupes)
    assert not _duplicate_hooks("c.py", [("pytest_configure", 1),
                                         ("pytest_terminal_summary", 10)]), (
        "a module with no duplicate was reported as having one")


def test_no_module_defines_the_same_pytest_hook_twice():
    """SILENT REPLACEMENT. Python keeps the LAST definition, pytest sees one
    hook, and the first one's behaviour is gone with no error and no warning.

    Measured at v3.66.1044 while writing batch E: a second
    `pytest_terminal_summary` in tests/conftest.py removed the socket
    recorder's summary from every run. The suite stayed green. The only
    evidence was a line that stopped appearing, and it was noticed by accident.
    """
    candidates = _tracked("tests/conftest.py") + _tracked("tests/*/conftest.py") \
        + _tracked("conftest.py")
    assert candidates, "no conftest.py was found; this gate proves nothing"

    problems = []
    for rel in candidates:
        problems.extend(_duplicate_hooks(rel, _hook_defs(_REPO / rel)))
    assert not problems, "\n  ".join([""] + problems)


def test_the_conftest_hook_scan_can_actually_see_a_duplicate(tmp_path):
    """The control. A gate that cannot detect its own subject reports clean."""
    fake = tmp_path / "conftest.py"
    fake.write_text("def pytest_terminal_summary(a):\n    pass\n\n\n"
                    "def pytest_terminal_summary(a):\n    pass\n")
    names = [n for n, _ in _hook_defs(fake)]
    assert names.count("pytest_terminal_summary") == 2, (
        "the scanner did not see two definitions in a file that has two, so "
        "the assertion above is vacuous: %s" % names)


def test_the_real_conftest_still_defines_the_hooks_that_matter():
    """The inverse of the gate above: proving there is no DUPLICATE is worth
    nothing if the hook was deleted instead."""
    names = [n for n, _ in _hook_defs(_TESTS / "conftest.py")]
    for required in ("pytest_configure", "pytest_terminal_summary",
                     "pytest_runtest_logstart"):
        assert required in names, (
            "tests/conftest.py no longer defines %s" % required)


# ── 3. no test may write real tool state ─────────────────────────────────────

# MUST NOT GROW AT ALL: nothing about running tests should register a job.
_REAL_STATE = ("/tmp/bd-jobs",)
# GROWS BY EXACTLY ONE PER RUN, by design: the run-context recorder creates its
# own directory keyed by the master pid, and prunes to 20. Asserting "must not
# grow" over this one was wrong, and the gate said so on its first run.
_PER_RUN_STATE = "/tmp/bd-runctx"


def _bd_jobs_offenders(jobs_dir, added_names, run_marker):
    """Added registry evidence attributable to one named inner run.

    The directory is shared with sibling workers and operators.  Only a
    well-formed JSON entry stamped by this invocation is ours.  Its log joins
    the offender set only when it is both inside the registry and newly added;
    unrelated and unparseable names remain unattributed rather than becoming a
    schedule-sensitive false red.
    """
    jobs_dir = pathlib.Path(jobs_dir)
    added = set(added_names)
    offenders = []
    for name in sorted(added):
        if not name.endswith(".json"):
            continue
        try:
            entry = json.loads((jobs_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if entry.get("run_marker") != run_marker:
            continue
        offenders.append(name)
        log = entry.get("log")
        if isinstance(log, str):
            log_path = pathlib.Path(log)
            if log_path.parent == jobs_dir and log_path.name in added:
                offenders.append(log_path.name)
    return sorted(set(offenders))


def test_the_state_diff_can_actually_see_an_added_entry(tmp_path):
    jobs = tmp_path / "bd-jobs"
    jobs.mkdir()
    token = "control"
    entry = jobs / "test5-1.json"
    entry.write_text(json.dumps({"run_marker": token}), encoding="utf-8")
    assert _bd_jobs_offenders(jobs, [entry.name], token) == [entry.name], (
        "the attribution helper cannot see an entry stamped by its own run")


def test_bd_jobs_diff_attributes_only_this_inner_run(tmp_path):
    jobs = tmp_path / "bd-jobs"
    jobs.mkdir()
    token = "row180-%d" % os.getpid()
    foreign = jobs / "test5-99999.json"
    foreign.write_text(json.dumps({"run_marker": "another-run"}),
                       encoding="utf-8")
    old = jobs / "preexisting.json"
    old.write_text(json.dumps({"run_marker": token}), encoding="utf-8")
    mine = jobs / "test5-4242.json"
    mine_log = jobs / "mine.log"
    mine_log.write_text("evidence", encoding="utf-8")
    mine.write_text(json.dumps({"run_marker": token,
                                "log": str(mine_log)}), encoding="utf-8")
    torn = jobs / "torn.json"
    torn.write_text("{not json", encoding="utf-8")
    unrelated = jobs / "operator.log"
    unrelated.write_text("foreign evidence", encoding="utf-8")

    got = _bd_jobs_offenders(
        jobs,
        [foreign.name, mine.name, mine_log.name, torn.name, unrelated.name],
        token)

    assert got == sorted([mine.name, mine_log.name])


def test_bd_jobs_diff_attributes_a_marked_entry_without_a_log(tmp_path):
    """Direct ``register(..., log=None)`` calls are legal.  Requiring a log
    would let their marked JSON escape the gate."""
    jobs = tmp_path / "bd-jobs"
    jobs.mkdir()
    token = "row180-%d" % os.getpid()
    mine = jobs / "test5-4242.json"
    mine.write_text(json.dumps({"run_marker": token, "log": None}),
                    encoding="utf-8")

    assert _bd_jobs_offenders(jobs, [mine.name], token) == [mine.name]


def test_bd_jobs_diff_does_not_trust_a_log_outside_the_registry(tmp_path):
    jobs = tmp_path / "bd-jobs"
    jobs.mkdir()
    token = "row180-%d" % os.getpid()
    entry = jobs / "test5-4242.json"
    entry.write_text(json.dumps({"run_marker": token,
                                 "log": str(tmp_path / "outside.log")}),
                     encoding="utf-8")
    assert _bd_jobs_offenders(jobs, [entry.name, "outside.log"], token) == [
        entry.name]


def test_the_tool_state_gate_calls_the_bd_jobs_attribution_helper():
    """Structural wiring floor paired with behavioral controls above.

    The unique marker must both reach the child environment and grade the
    resulting state.  A present-but-unused helper or token would make a clean
    run vacuously green.
    """
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    target = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name ==
                  "test_the_tool_suites_do_not_write_the_real_tool_state_directories")
    calls = {n.func.id for n in ast.walk(target)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_bd_jobs_offenders" in calls
    marker_keywords = [
        keyword for call in ast.walk(target) if isinstance(call, ast.Call)
        for keyword in call.keywords
        if keyword.arg == "BD_JOBS_RUN_MARKER"]
    assert len(marker_keywords) == 1
    assert isinstance(marker_keywords[0].value, ast.Name)
    assert marker_keywords[0].value.id == "run_marker"
    subprocess_envs = [
        keyword.value for call in ast.walk(target)
        if (isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "subprocess"
            and call.func.attr == "run")
        for keyword in call.keywords if keyword.arg == "env"]
    assert len(subprocess_envs) == 1
    assert isinstance(subprocess_envs[0], ast.Name)
    assert subprocess_envs[0].id == "env"


def test_the_tool_state_gate_denominator_includes_the_real_bd_jobs_writer():
    """1054 is the one in-band test that really registers and reaps a job.
    Leaving it outside this denominator would make the leak gate claim a wider
    writer surface than it executes."""
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    target = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name ==
                  "test_the_tool_suites_do_not_write_the_real_tool_state_directories")
    literals = {n.value for n in ast.walk(target)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "tests/test_v3_66_1054_launched_work_is_bounded_and_reapable.py" in literals


def test_the_tool_state_gate_rejects_a_failed_inner_run_even_with_passes(
        monkeypatch, tmp_path):
    jobs = tmp_path / "bd-jobs"
    runctx = tmp_path / "bd-runctx"
    jobs.mkdir()
    runctx.mkdir()
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_REAL_STATE", (str(jobs),))
    monkeypatch.setattr(module, "_PER_RUN_STATE", str(runctx))

    def mixed_result(argv, **kwargs):
        mine = runctx / "failed-inner"
        mine.mkdir()
        return subprocess.CompletedProcess(
            argv, 1,
            stdout="1 failed, 70 passed\n1 worker chain(s): %s\n" % mine,
            stderr="")

    monkeypatch.setattr(subprocess, "run", mixed_result)
    with pytest.raises(AssertionError, match="inner pytest failed"):
        test_the_tool_suites_do_not_write_the_real_tool_state_directories()


def test_the_tool_state_gate_propagates_and_grades_the_same_marker(
        monkeypatch, tmp_path):
    """End-to-end non-vacuity: the marker passed to the child is the marker
    whose resulting registry evidence reaches the failing assertion."""
    jobs = tmp_path / "bd-jobs"
    runctx = tmp_path / "bd-runctx"
    jobs.mkdir()
    runctx.mkdir()
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_REAL_STATE", (str(jobs),))
    monkeypatch.setattr(module, "_PER_RUN_STATE", str(runctx))

    def marked_leak(argv, **kwargs):
        marker = kwargs["env"]["BD_JOBS_RUN_MARKER"]
        (jobs / "inner-leak.json").write_text(
            json.dumps({"run_marker": marker, "log": None}),
            encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout="1 passed\n", stderr="")

    monkeypatch.setattr(subprocess, "run", marked_leak)
    with pytest.raises(AssertionError, match="added entries to real tool state"):
        test_the_tool_suites_do_not_write_the_real_tool_state_directories()


def test_the_tool_suites_do_not_write_the_real_tool_state_directories():
    """410 JUNK ENTRIES, one per run per worker, in the registry whose whole
    job is telling you what is actually running.

    Nothing was at risk -- every entry was dead and `bd-jobs reap` forgot all
    410 without killing anything -- but a list of running work is worth nothing
    when all of it is test residue, and it took an unrelated bd-fleet run to
    notice.

    DENOMINATOR: the tool suites, named here, run in one subprocess. Not the
    whole tree -- that would be a five-minute gate -- and these are the files
    that touch the tools that own those directories.  1054 is included because
    it is the one in-band suite that really registers and reaps a job.
    """
    suites = ["tests/test_v3_66_1043_measurement_and_fleet_tools.py",
              "tests/test_v3_66_1040_remote_job_registry.py",
              "tests/test_v3_66_1044_run_context_and_chains.py",
              "tests/test_v3_66_1054_launched_work_is_bounded_and_reapable.py"]
    for rel in suites:
        assert (_REPO / rel).is_file(), "%s is missing from the denominator" % rel

    def snapshot():
        out = {}
        for d in _REAL_STATE + (_PER_RUN_STATE,):
            p = pathlib.Path(d)
            out[d] = sorted(x.name for x in p.iterdir()) if p.is_dir() else []
        return out

    before = snapshot()
    run_marker = "1046-%d-%d" % (os.getpid(), time.time_ns())
    env = dict(os.environ, BD_DISABLE_KEEPALIVE="1", NO_COLOR="1",
               BD_NESTED_PYTEST="1", BD_JOBS_RUN_MARKER=run_marker)
    env.pop("FORCE_COLOR", None)
    r = subprocess.run([sys.executable, "-m", "pytest", *suites, "-q",
                        "-p", "no:randomly"],
                       capture_output=True, text=True, timeout=600,
                       cwd=str(_REPO), env=env)
    after = snapshot()

    assert r.returncode == 0, (
        "inner pytest failed, so its state evidence is not a valid completed "
        "denominator (rc=%d):\n%s" %
        (r.returncode, (r.stdout + r.stderr)[-1200:]))
    assert "passed" in (r.stdout + r.stderr), (
        "the inner run produced no summary, so it may not have run at all:\n%s"
        % (r.stdout + r.stderr)[-1200:])

    added = {d: sorted(set(after[d]) - set(before[d]))
             for d in _REAL_STATE + (_PER_RUN_STATE,)}
    attributed = _bd_jobs_offenders(
        pathlib.Path(_REAL_STATE[0]), added[_REAL_STATE[0]], run_marker)
    offenders = {_REAL_STATE[0]: attributed} if attributed else {}
    assert not offenders, (
        "running the tool suites added entries to real tool state: %s. Point "
        "the tool's directory at tmp_path in the test." % offenders)

    # ATTRIBUTE, DO NOT COUNT. The first version asserted "at most one new run
    # directory" and went red in the band at -n 28: twenty-eight sibling
    # workers were starting their own pytest runs against the same global
    # directory while this test measured it, and it reported 8. Counting a
    # shared resource from inside a parallel suite measures the suite. The
    # inner run NAMES its own directory in its output, so ask about that one.
    reported = [ln.split(":")[-1].strip() for ln in (r.stdout + r.stderr).splitlines()
                if "worker chain(s)" in ln]
    assert reported, (
        "the inner run printed no run-context line, so the recorder either did "
        "not arm or stopped reporting:\n%s" % (r.stdout + r.stderr)[-800:])
    mine = pathlib.Path(reported[0])
    assert mine.name in added[_PER_RUN_STATE] or mine.is_dir(), (
        "the inner run reported %s and it is not there" % mine)


# ── 4. the ANSI audit ────────────────────────────────────────────────────────

def _load(name):
    path = _BIN / name
    mod = name.replace("-", "_")
    spec = importlib.util.spec_from_loader(
        mod, importlib.machinery.SourceFileLoader(mod, str(path)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("tool", ["bd-run", "bd-ab", "bd-ladder"])
def test_every_tool_that_grades_pytest_output_survives_colour(tool):
    """THE BLINDNESS DEPENDS ON WHO LAUNCHED YOU, which is the worst property
    a parser can have. This box exports FORCE_COLOR=3, so pytest colourises
    even into a pipe; the same tool run over ssh from a nohup shell gets plain
    text and works. Measured pre-fix: bd-ab returned an EMPTY failure set for a
    coloured FAILED line, and bd-ab reports nothing but failure counts.
    """
    mod = _load(tool)
    coloured = "\x1b[31mFAILED tests/a.py::x\x1b[0m - boom\n"
    assert "FAILED tests/a.py::x" in mod.strip_ansi(coloured)

    env = mod.plain_env({"FORCE_COLOR": "3", "PY_COLORS": "1"})
    assert "FORCE_COLOR" not in env and "PY_COLORS" not in env, (
        "%s hands FORCE_COLOR to its child, so the log ON DISK -- the artifact "
        "somebody greps a week later -- is full of escapes whatever the parser "
        "does" % tool)
    assert env.get("NO_COLOR") == "1"


def test_bd_ab_counts_failures_in_a_coloured_log():
    """The specific measured defect, not just the helper it was fixed with."""
    mod = _load("bd-ab")
    got = mod.failures("\x1b[31mFAILED tests/a.py::x\x1b[0m - boom\n"
                       "\x1b[31mFAILED tests/b.py::y\x1b[0m\n")
    assert got == {"FAILED tests/a.py::x", "FAILED tests/b.py::y"}, (
        "bd-ab saw %s. Its entire verdict is failure counts, so a blind parser "
        "compares zeros and reports NOT DISTINGUISHED forever." % got)


def test_bd_ladder_grades_a_coloured_broken_rung_as_broken():
    mod = _load("bd-ladder")
    out = ("\x1b[31mFAILED tests/g.py::x\x1b[0m - boom\n"
           "\x1b[31m1 failed\x1b[0m in 1s\n")
    assert mod.grade(out, "tests/g.py") == mod.BROKEN, (
        "a coloured broken rung graded %s -- the ladder degrades to UNKNOWN "
        "everywhere and can never bracket anything"
        % mod.grade(out, "tests/g.py"))
