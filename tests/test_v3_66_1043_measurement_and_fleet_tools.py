"""Batch B: the session's scratch scripts become tools, and must behave better.

Five scripts carried a session's worth of measurement and fleet work from a
scratchpad that nothing tracked, nothing tested, and nothing else could use:
bd-run, bd-ladder, bd-ab, bd-fleet, bd-gc. Promoting them is not a copy -- each
one had a defect the scratch version could afford and a tracked tool cannot:

  bd-run     graded on `grep -c`, which prints 0 and exits 1 on no match.
  bd-ladder  read "the guard is not in the FAILED lines" as a CLEAN rung, so an
             internal error or a timeout sent the search the wrong way.
  bd-ab      ran four samples concurrently -- the load confound it exists to
             measure, manufactured on purpose -- and would report any two
             numbers as a difference.
  bd-fleet   counted pytest with `pgrep -f`, matching its own probe. Measured on
             its first live run: 4 on an idle host.
  bd-gc      is new, and it deletes things on three hosts, so most of what is
             tested here is what it REFUSES.

Each tool's own --selftest is run by test_toolchain_534, which is what makes it
wired rather than described. This file covers what a selftest cannot: the seam
between two implementations of one format, the promise that the read-only tools
are read-only, and the specific mutants that got through last time.
"""
import ast
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_BIN = _REPO / "toolchain" / "bin"
_TOOLS = ("bd-run", "bd-ladder", "bd-ab", "bd-fleet", "bd-gc")


def _load(name):
    path = _BIN / name
    mod_name = name.replace("-", "_")
    spec = importlib.util.spec_from_loader(
        mod_name, importlib.machinery.SourceFileLoader(mod_name, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the shape every tool in the set shares ───────────────────────────────────

@pytest.mark.parametrize("tool", _TOOLS)
def test_every_tool_exists_and_is_executable(tool):
    path = _BIN / tool
    assert path.is_file(), "%s is missing" % tool
    assert os.access(path, os.X_OK), (
        "%s is not executable -- it will be run by hand from a shell before it "
        "is ever run by a test" % tool)


def _code_only(text):
    """Source with comment lines dropped.

    Needed because a gate that reads source text has the COMMENTS in its
    denominator, and the paragraph explaining why a literal was removed spells
    that literal. This file tripped that on its first run: bd-fleet's comment
    about abandoning `pgrep -fc` failed the assertion that it no longer uses
    `pgrep -fc`. CLAUDE.md section 0 records four earlier instances.
    """
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#"))


@pytest.mark.parametrize("tool", ("bd-run", "bd-ladder", "bd-ab", "bd-fleet"))
def test_only_bd_gc_and_bd_jobs_hold_a_destructive_verb(tool):
    """The set's premise: you can run these when you do not yet know what is
    wrong. A status tool that can restart a service is not a status tool.

    bd-run and bd-ab are not READ-only -- bd-run prunes its own logs, bd-ab
    rewrites the file it is A/B-ing and puts it back. What none of the four may
    do is remove a tree, signal a process, or deploy.
    """
    code = _code_only((_BIN / tool).read_text(encoding="utf-8"))
    for forbidden in ("rmtree", "rename_verify_destroy", "safe_temp_remove",
                      "os.kill", "systemctl restart", "deploy.sh"):
        assert forbidden not in code, (
            "%s can %r. Only bd-gc and bd-jobs hold destructive verbs; the "
            "rest must be safe to run blind." % (tool, forbidden))


@pytest.mark.parametrize("tool", ("bd-ladder", "bd-fleet"))
def test_the_two_pure_reporters_touch_no_file_at_all(tool):
    """bd-ladder writes rung logs and bd-fleet writes nothing; neither may
    delete. Separated from the case above so the weaker promise the other two
    make cannot be mistaken for this one."""
    code = _code_only((_BIN / tool).read_text(encoding="utf-8"))
    for forbidden in ("unlink", "rmtree", "shutil.move",
                      "rename_verify_destroy", "safe_temp_remove"):
        assert forbidden not in code, "%s can %r" % (tool, forbidden)


# ── bd-run ───────────────────────────────────────────────────────────────────

def test_bd_run_passes_the_child_exit_code_through_untouched(tmp_path):
    """The bring-up script's inverted verdict, mechanised.

    `grep -c` prints 0 and exits 1 when nothing matches, so a wrapper that
    grades itself on a match count reports FAILURE on a run where every step
    succeeded. bd-run's exit code is the child's and nothing else -- including
    the case that broke it, a success with nothing to report.
    """
    for expected in (0, 7):
        r = subprocess.run(
            [sys.executable, str(_BIN / "bd-run"), "--label", "x%d" % expected,
             "--dir", str(tmp_path), "--", "sh", "-c", "exit %d" % expected],
            capture_output=True, text=True, timeout=60)
        assert r.returncode == expected, (
            "a child exiting %d was reported as %d:\n%s"
            % (expected, r.returncode, r.stdout + r.stderr))


def test_bd_run_keeps_the_traceback_on_disk_and_off_stdout(tmp_path):
    """"Do not put it in my context" and "do not capture it" are different
    instructions. Conflating them cost two twelve-minute reruns."""
    needle = "Traceback (most recent call last): SENTINEL-3f9a"
    subprocess.run(
        [sys.executable, str(_BIN / "bd-run"), "--label", "tb", "--dir",
         str(tmp_path), "--", "sh", "-c", "echo '%s'" % needle],
        capture_output=True, text=True, timeout=60)
    log = (tmp_path / "tb.log").read_text()
    assert needle in log, "the output was filtered at capture time"


def test_bd_run_never_reports_a_pass_it_did_not_see():
    mod = _load("bd-run")
    block = "\n".join(mod.verdict("some output, no summary line at all\n"))
    assert "SUMMARY UNKNOWN" in block, (
        "a run with no pytest summary produced no UNKNOWN -- a suite killed "
        "before it could summarise would read as a clean run")
    assert "not a pass" in block


def test_bd_run_states_the_denominator_when_it_truncates():
    mod = _load("bd-run")
    many = "".join("FAILED tests/t%d.py::x - boom\n" % i for i in range(60))
    block = "\n".join(mod.verdict(many, show=10))
    assert "showing 10 of 60" in block, (
        "a truncated failure list did not say it was truncated, so it reads as "
        "the whole set and gets acted on as one")


def test_bd_run_bounds_its_own_retention(tmp_path):
    """Creating a path is a promise to remove it -- 744 leaked directories."""
    for i in range(6):
        (tmp_path / ("l%d.log" % i)).write_text("x")
        os.utime(tmp_path / ("l%d.log" % i), (1000 + i, 1000 + i))
    _load("bd-run").prune(str(tmp_path), 2)
    left = sorted(p.name for p in tmp_path.glob("*.log"))
    assert left == ["l4.log", "l5.log"], (
        "prune kept the wrong set (%s) -- it must keep the NEWEST --keep" % left)


# ── bd-ladder ────────────────────────────────────────────────────────────────

def test_bd_ladder_grades_a_guard_that_never_ran_as_UNKNOWN():
    """THE ONE THE SCRATCH VERSION GOT WRONG, and it is not a cosmetic bug.

    Grading on "the guard is absent from the FAILED lines" makes an
    INTERNALERROR, a collection failure or a timeout indistinguishable from a
    clean rung -- and a clean rung is the signal that tells you to search
    FURTHER UP the chain. A wrong ok does not just lose a rung; it points the
    whole search away from the culprit.
    """
    mod = _load("bd-ladder")
    assert mod.grade("1 passed in 0.1s\n", "tests/g.py") == mod.OK
    assert mod.grade("FAILED tests/g.py::x - boom\n1 failed in 1s\n",
                     "tests/g.py") == mod.BROKEN
    assert mod.grade("INTERNALERROR> boom\n", "tests/g.py") == mod.UNKNOWN
    assert mod.grade("TIMEOUT after 300s\n", "tests/g.py") == mod.UNKNOWN
    assert mod.grade("ERROR tests/g.py - collection failed\n1 error in 1s\n",
                     "tests/g.py") == mod.UNKNOWN


def test_bd_ladder_refuses_to_bracket_a_ladder_that_is_not_monotonic():
    mod = _load("bd-ladder")
    first, _ = mod.bracket({8: mod.OK, 16: mod.OK, 24: mod.BROKEN})
    assert first == 24

    first, detail = mod.bracket({8: mod.OK, 16: mod.BROKEN, 24: mod.OK})
    assert first is None and "NOT MONOTONIC" in detail, (
        "a ladder with a clean rung ABOVE a broken one still produced a "
        "bracket. The prefix model does not describe that effect, and the "
        "number would be acted on as though it did")

    first, detail = mod.bracket({8: mod.OK, 16: mod.UNKNOWN, 24: mod.BROKEN})
    assert first is None and "UNKNOWN" in detail, (
        "an UNKNOWN rung did not block the bracket")

    first, detail = mod.bracket({8: mod.OK, 16: mod.OK})
    assert first is None and "no rung broke" in detail


def test_bd_ladder_refuses_an_empty_chain(tmp_path):
    """A ladder over nothing reports 'no rung broke' -- a clean verdict over an
    empty denominator, which is section 0's entire subject."""
    chain = tmp_path / "c.txt"
    chain.write_text("# nothing but a comment\n")
    r = subprocess.run(
        [sys.executable, str(_BIN / "bd-ladder"), "--chain", str(chain),
         "--guard", "tests/conftest.py"],
        capture_output=True, text=True, timeout=60, cwd=str(_REPO))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "REFUSED" in r.stderr


def test_bd_ladder_rungs_end_at_the_chain_length():
    mod = _load("bd-ladder")
    assert mod.default_rungs(4) == [1, 2, 3, 4]
    r = mod.default_rungs(232, 16)
    assert r[-1] == 232, "the top rung must be the whole chain: %s" % r
    assert len(set(r)) == len(r), "a rung was repeated: %s" % r


# ── bd-ab ────────────────────────────────────────────────────────────────────

def test_bd_ab_refuses_a_single_sample(tmp_path, capsys, monkeypatch):
    """The most expensive mistake of the session that produced these tools.

    On an unchanged tree the failure count ranged 1..8 idle and 18..29 loaded,
    so one run of a full suite is not a measurement -- and a prediction made
    from one had to be retracted. The tool declines to take the sample.

    ASSERT THE REASON, NOT THE CODE. The first version of this test checked
    only `returncode == 2` against an untracked temp file, and a mutant that
    removed the guard entirely ESCAPED: `git show HEAD:<untracked>` failed, the
    tool refused for that instead, and the exit code was identical. Every other
    refusal in this tool also exits 2, so the code alone distinguishes nothing.
    The other conditions are stubbed out here so the sample guard is the only
    thing left that can fire -- and so a weakened guard runs the stub instead
    of the entire suite.
    """
    mod = _load("bd-ab")
    f = tmp_path / "x.py"
    f.write_text("working tree\n")
    monkeypatch.setattr(mod, "_git_show", lambda rev, path: "other revision\n")
    monkeypatch.setattr(mod, "sample",
                        lambda a, log: pathlib.Path(log).write_text("1 passed\n"))
    ns = type("A", (), {"samples": 1, "file": str(f), "rev": "HEAD", "jobs": 1,
                        "dir": str(tmp_path / "logs"), "pytest_args": []})()
    rc = mod.run(ns)
    err = capsys.readouterr().err
    assert rc == 2, "a single sample was accepted (rc=%s)" % rc
    assert "samples 1" in err and "not a measurement" in err, (
        "it refused, but not for the reason under test: %r" % err)


def test_bd_ab_will_not_call_a_difference_its_samples_do_not_support():
    mod = _load("bd-ab")
    ok, sentence = mod.compare([1, 2, 3], [10, 11, 12])
    assert ok and "MORE" in sentence

    ok, sentence = mod.compare([1, 30], [2, 29])
    assert not ok and "NOT DISTINGUISHED" in sentence, (
        "overlapping ranges produced a verdict. The means differ by 0.5 and "
        "the ranges cover each other -- this is exactly the pair of numbers "
        "that got read as a result last time")

    ok, _ = mod.compare([], [1, 2])
    assert not ok, "an empty condition was treated as a comparison"


def test_bd_ab_restores_the_working_tree_even_when_the_suite_fails(tmp_path):
    """A harness that leaves the tree modified after a crash turns a
    measurement into a source change nobody made on purpose."""
    mod = _load("bd-ab")
    f = tmp_path / "subject.py"
    original = "WORKING TREE CONTENT\n"
    f.write_text(original)

    calls = []

    def exploding_sample(pytest_args, log):
        calls.append(log)
        raise RuntimeError("the suite died")

    mod.sample = exploding_sample
    mod._git_show = lambda rev, path: "OTHER REVISION CONTENT\n"
    ns = type("A", (), {"samples": 2, "file": str(f), "rev": "HEAD", "jobs": 1,
                        "dir": str(tmp_path / "logs"), "pytest_args": []})()
    with pytest.raises(RuntimeError):
        mod.run(ns)
    assert calls, "the harness never got as far as running a sample"
    assert f.read_text() == original, (
        "bd-ab left %s holding the OTHER revision's content after the run "
        "died. The next command anybody types now runs against a tree they "
        "did not edit." % f)


# ── bd-fleet ─────────────────────────────────────────────────────────────────

def test_bd_fleet_and_deploy_fleet_parse_the_same_host_file_the_same_way():
    """THE SEAM. Two implementations of one format, in two languages.

    bd-fleet parses the fleet file in Python; scripts/deploy_fleet.sh parses it
    in bash. Nothing made them agree, and a format read two ways drifts in the
    direction nobody is testing -- the tool that reports on your fleet and the
    tool that deploys to it would then disagree about what the fleet IS. So
    this runs the REAL script, in dry-run, over the tracked example file, and
    requires the same labels and addresses in the same order.
    """
    example = _REPO / "docs" / "repo" / "hosts.example"
    mine = _load("bd-fleet").read_hosts(str(example))
    assert mine, "the example file parsed to nothing"

    r = subprocess.run(
        ["bash", str(_REPO / "scripts" / "deploy_fleet.sh"), "--dry-run",
         "--hosts", str(example)],
        capture_output=True, text=True, timeout=60, cwd=str(_REPO))
    assert r.returncode == 0, r.stdout + r.stderr
    theirs = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "would":
            theirs.append((parts[0], parts[1]))
    assert theirs, "the dry run listed no hosts, so this proves nothing:\n%s" % r.stdout
    assert mine == theirs, (
        "the two parsers disagree about %s.\n  bd-fleet:        %s\n"
        "  deploy_fleet.sh: %s" % (example, mine, theirs))


def test_bd_fleet_counts_pytest_without_matching_its_own_probe():
    """Measured on this tool's first live run: `pgrep -fc 'm pytest'` answered
    4 on an idle host, because the probe shell's own command line contains the
    pattern. Section 5 records the same shape for `pkill -f`, and it was
    written into a new tool anyway."""
    src = _code_only((_BIN / "bd-fleet").read_text(encoding="utf-8"))
    assert "pgrep -fc" not in src, (
        "bd-fleet is back to pgrep -f, which matches the probe itself")
    assert "comm=" in src, (
        "nothing discriminates the probe from a real run -- deploy.sh's "
        "_running_pytest uses `ps -eo comm=` for exactly this reason")

    # A HOME THE PROBE CAN FIND A TREE IN. The probe's first line is
    # `cd ~/BulkDownloader || { echo tree=ABSENT; exit 0; }`, so on a CI runner
    # -- where the checkout lives at /home/runner/work/BD/BD -- it exits before
    # reaching the line under test and reports tree=ABSENT. The first version of
    # this test then failed in CI for a reason that had nothing to do with its
    # subject. Give it a HOME where the cd succeeds and the count line runs.
    import tempfile
    with tempfile.TemporaryDirectory() as home:
        (pathlib.Path(home) / "BulkDownloader").mkdir()
        env = dict(os.environ, HOME=home)
        r = subprocess.run(["bash", "-c", _load("bd-fleet").PROBE],
                           capture_output=True, text=True, timeout=120,
                           cwd=home, env=env)
    counted = {k: v for k, v in
               (ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)}
    assert counted.get("tree") != "ABSENT", (
        "the harness failed to give the probe a tree, so the pytest-counting "
        "line never ran and this test proves nothing: %s" % r.stdout)
    assert "pytest" in counted, r.stdout
    # This test IS a pytest process, but it is not `python -m pytest` with the
    # probe's own text in its argv; what must never happen is the probe
    # inflating the count with its own shell, awk and ssh.
    assert counted["pytest"].isdigit(), counted


def test_bd_fleet_reports_an_unreachable_host_rather_than_omitting_it():
    """A fleet report that silently covers two hosts out of three reads as a
    clean bill of health for all three."""
    mod = _load("bd-fleet")
    out = "\n".join(mod.render([("ghost", "10.0.0.1", None, "timed out")]))
    assert "UNREACHABLE" in out


def test_bd_fleet_flags_the_states_that_actually_bit_us():
    mod = _load("bd-fleet")
    rows = [("a", "1", {"head": "aaa", "version": "1", "dirty": "0",
                        "service": "active", "pytest": "0", "jobs": "0",
                        "tmp_bd": "5"}, None),
            ("b", "2", {"head": "bbb", "version": "2", "dirty": "3",
                        "service": "failed", "pytest": "4", "jobs": "0",
                        "tmp_bd": "9479"}, None)]
    notes = " ".join(mod.divergences(rows))
    assert "DIVERGES" in notes, "two hosts on different commits went unremarked"
    assert "UNCOMMITTED" in notes
    assert "service is failed" in notes
    assert "NOTHING registered" in notes, (
        "a host running pytest with an empty registry is the state that broke "
        "a deploy at step 9 and left a service down; it must be called out")
    assert "9479" in notes


# ── bd-gc ────────────────────────────────────────────────────────────────────

def test_bd_gc_never_treats_the_job_registry_as_litter():
    """Deleting /tmp/bd-jobs turns tracked remote work into the untracked
    orphan the registry exists to prevent. Structural, not an age heuristic."""
    mod = _load("bd-gc")
    now = time.time()
    ok, why = mod.is_candidate("/tmp/bd-jobs", now, 60)
    assert not ok and "registry" in why
    ok, _ = mod.is_candidate("/tmp/bd-jobs/test4-140246.json", now, 60)
    assert not ok


def test_bd_gc_only_ever_considers_a_direct_child_of_an_allowed_prefix():
    """A bare startswith() also accepts /tmp/bd-run/2026/deep/thing. This tool
    deletes what the predicate accepts, so the predicate is the safety."""
    mod = _load("bd-gc")
    now = time.time()
    for path in ("/var/tmp/bd-thing", "/tmp/bd-run/nested/deeper", "/etc/passwd"):
        ok, _why = mod.is_candidate(path, now, 60)
        assert not ok, "%s was eligible for deletion" % path


def test_bd_gc_leaves_anything_recent_alone(tmp_path, monkeypatch):
    mod = _load("bd-gc")
    now = time.time()
    monkeypatch.setattr(mod, "PREFIXES", (str(tmp_path) + "/",))
    live = tmp_path / "a-run-in-progress"
    live.mkdir()
    ok, why = mod.is_candidate(str(live), now, 3600)
    assert not ok and "minute" in why, (
        "a directory touched seconds ago was eligible; a run still using it "
        "would have its tmp deleted underneath it")


def test_bd_gc_removes_nothing_without_apply_and_says_so(tmp_path, monkeypatch,
                                                         capsys):
    """deploy_fleet.sh's dry run once printed "all 3 host(s) deployed and
    verified" having touched nothing. A cleanup tool has the same trap and a
    worse consequence when read the other way round."""
    mod = _load("bd-gc")
    monkeypatch.setattr(mod, "PREFIXES", (str(tmp_path) + "/",))
    victim = tmp_path / "old-litter"
    victim.mkdir()
    os.utime(victim, (1000, 1000))

    ns = type("A", (), {"older_than": 60, "apply": False, "show": 5,
                        "verbose": False, "measure": False})()
    assert mod.run(ns) == 0
    out = capsys.readouterr().out
    assert victim.is_dir(), "a DRY RUN deleted a directory"
    assert "NOTHING REMOVED" in out, (
        "the dry run did not say it removed nothing, so its output reads as a "
        "completed cleanup")

    ns.apply = True
    assert mod.run(ns) == 0
    assert not victim.exists(), "--apply did not remove an eligible path"


def test_bd_gc_refuses_an_age_window_that_could_catch_a_live_run(tmp_path):
    r = subprocess.run(
        [sys.executable, str(_BIN / "bd-gc"), "--older-than", "1", "--apply"],
        capture_output=True, text=True, timeout=60, cwd=str(_REPO))
    assert r.returncode == 2 and "REFUSED" in r.stderr, r.stdout + r.stderr


# ── bd-jobs: the second seam bug, from the same join ─────────────────────────

_REAL_JOBS = pathlib.Path("/tmp/bd-jobs")


def _my_new_registry_entries(before_names, after_names, pid):
    """New registry entries that this process could have written.

    A concurrent process cannot share our pid on this host.  Attribute by that
    identity instead of counting a shared directory whose unrelated population
    may grow or shrink during the assertion window.
    """
    mine = "%s-%d.json" % (socket.gethostname(), pid)
    return sorted(name for name in set(after_names) - set(before_names)
                  if name == mine)

def test_bd_jobs_register_stamps_only_an_explicit_run_marker(monkeypatch,
                                                              tmp_path):
    """A shared-directory leak detector can attribute only evidence the writer
    names.  The marker is separate from BD_NESTED_PYTEST: that variable is a
    boolean re-entry guard whose literal value ``1`` is intentionally shared by
    every nested run."""
    mod = _load("bd-jobs")
    monkeypatch.setattr(mod, "JOBS_DIR", tmp_path / "bd-jobs")
    token = "row180-%d" % os.getpid()
    monkeypatch.setenv("BD_JOBS_RUN_MARKER", token)

    entry = mod.register(os.getpid(), "row180", "true")
    written = mod.JOBS_DIR / (entry["id"] + ".json")
    assert written.is_file(), "register() did not exercise the redirected writer"
    on_disk = json.loads(written.read_text(encoding="utf-8"))
    assert on_disk["run_marker"] == token


def test_bd_jobs_register_preserves_the_operator_schema_without_a_marker(
        monkeypatch, tmp_path):
    mod = _load("bd-jobs")
    monkeypatch.setattr(mod, "JOBS_DIR", tmp_path / "bd-jobs")
    monkeypatch.delenv("BD_JOBS_RUN_MARKER", raising=False)

    entry = mod.register(os.getpid(), "ordinary operator job", "true")
    on_disk = json.loads(
        (mod.JOBS_DIR / (entry["id"] + ".json")).read_text(encoding="utf-8"))
    assert "run_marker" not in on_disk, (
        "an unset test-attribution channel changed every operator entry")


def test_register_is_the_sole_bd_jobs_json_registry_writer():
    """An attribution marker is sound only while every production JSON entry
    crosses ``register()``.  Log creation is intentionally outside this floor.

    RE-EXPRESSED at v3.66.1206, and deliberately NOT weakened to a name check.
    Publication stopped being a single ``write_text``: an entry is now staged
    into a temp, fsynced, renamed over its final and the directory fsynced. The
    old denominator asked only for ``write_text`` calls mentioning ``JOBS_DIR``
    and ``.json``, so on this tree it became **empty** -- and an empty
    denominator is not a passing gate, it is a gate with no subject, through
    which the v1204 M3 bypass (a direct ``JOBS_DIR / "unmarked-bypass.json"``
    writer added outside ``register``) would walk untouched.

    So the population is every AST call that can CREATE OR REPLACE a file under
    ``JOBS_DIR`` -- ``write_text``, ``os.replace``/``os.rename``,
    ``open(..., "w")``, ``mkstemp`` -- and four independent facts are asserted
    over it, each with its own non-empty denominator:

      1. every such call lives inside the publication or exact cleanup surface;
      2. cleanup creates and renames only its non-JSON quarantine;
      3. the ATOMIC final publisher is exactly ``_publish_entry`` -- one place
             where a name becomes visible in the registry;
      4. ``_publish_entry``'s only production caller is ``register``.

    Together those still say what the v1204 battery needs: nothing reaches the
    registry except through ``register``.
    """
    source = (_BIN / "bd-jobs").read_text(encoding="utf-8")
    tree = ast.parse(source)

    def segment(node):
        return ast.get_source_segment(source, node) or ""

    # The publication surface, named exhaustively: the entry point that takes
    # the registry lock and decides the collision, the locked function that
    # stages and renames, and log creation (deliberately outside the JSON
    # floor, per this test's original docstring).
    publication_surface = {"register", "_publish_entry", "open_job_log",
                           "_stage_and_replace_under_lock"}
    cleanup_surface = {"_unlink_owned_identity"}
    writers = {}        # function name -> the kinds of registry write it does
    publishers = set()  # functions that perform the atomic rename
    cleanup_calls = set()  # the exact non-JSON quarantine operations
    callers = set()     # functions that call _publish_entry
    stagers = set()     # functions that reach the locked staging half
    for function in (n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef,
                                       ast.AsyncFunctionDef))):
        for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
            called = segment(call.func)
            arguments = " ".join(
                [segment(a) for a in call.args]
                + [segment(k.value) for k in call.keywords])
            kind = None
            if called.endswith(".write_text") and "JOBS_DIR" in called:
                kind = "write_text"
            elif called in ("os.replace", "os.rename"):
                kind = "rename"
                publishers.add(function.name)
            elif called.endswith("mkstemp") and "JOBS_DIR" in arguments:
                kind = "mkstemp"
            elif called == "open" and "JOBS_DIR" in arguments:
                kind = "open"
            if kind is not None:
                writers.setdefault(function.name, set()).add(kind)
                if function.name in cleanup_surface:
                    cleanup_calls.add((
                        called, tuple(segment(a) for a in call.args),
                        tuple(segment(k.value) for k in call.keywords)))
            if called == "_publish_entry":
                callers.add(function.name)
            if called == "_stage_and_replace_under_lock":
                stagers.add(function.name)

    assert writers, (
        "the JOBS_DIR writer denominator is EMPTY, so this gate has no "
        "subject: it would pass over any bypass at all")
    assert set(writers) <= publication_surface | cleanup_surface, (
        "production JOBS_DIR writers bypass register(): %s"
        % {name: sorted(kinds) for name, kinds in writers.items()
           if name not in publication_surface | cleanup_surface})
    assert cleanup_calls == {
        ("tempfile.mkstemp", (),
         ('".bd-jobs-cleanup-"', '".tmp"', "str(JOBS_DIR)")),
        ("os.replace", ("str(path)", "quarantine"), ()),
    }, "cleanup writes something other than its exact non-JSON quarantine: %r" % (
        cleanup_calls,)
    publishers -= cleanup_surface
    assert publishers == {"_stage_and_replace_under_lock"}, (
        "the atomic final publisher is not exactly one locked function: %s"
        % sorted(publishers))
    assert callers == {"register"}, (
        "_publish_entry is reached from something other than register(): %s"
        % sorted(callers))
    assert stagers == {"_publish_entry"}, (
        "the locked staging half is reached from something other than "
        "_publish_entry, so a publication could bypass the registry lock and "
        "the collision decision: %s" % sorted(stagers))
    assert "rename" in writers.get("_stage_and_replace_under_lock", set()), (
        "the detector no longer sees the atomic publication it is measuring")
    assert "mkstemp" in writers.get("_stage_and_replace_under_lock", set()), (
        "the detector no longer sees the staging write it is measuring")
    assert "mkstemp" in writers.get("open_job_log", set()), (
        "the detector no longer sees the log creation it deliberately allows")


def test_registry_leak_attribution_ignores_foreign_concurrent_churn():
    me = os.getpid()
    mine = "%s-%d.json" % (socket.gethostname(), me)
    assert _my_new_registry_entries(
        {"old-1.json"}, {"old-1.json", "test5-424242.json"}, me) == []
    assert _my_new_registry_entries(
        {"old-1.json"}, {"old-1.json", mine}, me) == [mine]
    assert _my_new_registry_entries({mine}, {mine}, me) == []
    assert _my_new_registry_entries(
        {"old-1.json", "foreign-2.json"}, {"old-1.json"}, me) == []


def test_the_real_registry_guard_calls_the_attribution_helper():
    """Wiring floor, with its evasion surface declared: AST proves the guard
    calls the pure helper, while the behavioral test above proves its meaning.
    Neither half alone would prevent a present-but-unused helper."""
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    target = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name ==
                  "test_bd_jobs_quotes_the_command_it_hands_back_to_a_shell")
    calls = {n.func.id for n in ast.walk(target)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_my_new_registry_entries" in calls

def test_bd_jobs_quotes_the_command_it_hands_back_to_a_shell(monkeypatch, tmp_path):
    """One cut after `--` reached the shell, the SAME seam bit again.

    `bd-jobs run --host X -- bash -c "a && b"` re-joined argv with bare spaces,
    so the remote saw `bash -c a && b`: it ran `bash -c a` and then `b` as a
    separate command in the wrong directory. The registry recorded a job that
    was never what the caller asked for. Both bugs live in the one line where
    a parsed argv is turned back into a shell string, and both were invisible
    to every test that passed arguments containing no spaces.
    """
    import importlib.machinery
    spec = importlib.util.spec_from_loader(
        "bd_jobs_q", importlib.machinery.SourceFileLoader(
            "bd_jobs_q", str(_BIN / "bd-jobs")))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # POINT THE REGISTRY AT tmp_path FIRST. cmd_run registers what it launches,
    # and the module's JOBS_DIR is the REAL /tmp/bd-jobs. Measured at
    # v3.66.1044 with bd-fleet: 410 entries on the master, every one of them
    # this test's `purpose="p"`, written once per run per xdist worker. A test
    # that litters the registry makes the registry useless for the thing it
    # exists for -- telling you what is actually running.
    monkeypatch.setattr(mod, "JOBS_DIR", tmp_path / "bd-jobs")
    real_before = {p.name for p in _REAL_JOBS.glob("*.json")} \
        if _REAL_JOBS.is_dir() else set()

    # RE-ANCHORED at v3.66.1206. The local launch is a release gate now, so the
    # argv `Popen` receives is the WRAPPER's, with the user's argv carried at
    # the end of it. Reading `seen["cmd"][2]` off the launcher's argv would
    # extract the release descriptor and then execute it as a shell program.
    # The subject was never the launcher's argv -- it is the command string the
    # tool hands back to a shell -- so it is read from the production helper
    # that builds it, and it is still EXECUTED, which is the half that matters.
    inner = mod._user_command(["--", "bash", "-c", "cd /tmp && echo hi"])
    assert mod._user_argv(inner)[:2] == ["bash", "-c"]
    assert inner != "bash -c cd /tmp && echo hi", (
        "argv was re-joined with bare spaces, so the shell re-splits it: %r"
        % inner)
    r = subprocess.run(["bash", "-c", inner], capture_output=True, text=True,
                       timeout=30)
    assert r.stdout.strip() == "hi", (
        "the reassembled command does not run as the caller wrote it: %r -> %r"
        % (inner, r.stdout + r.stderr))

    # AND THE LAUNCHER MUST ACTUALLY USE IT: the wrapper argv ends with exactly
    # the argv the helper builds. Without this the helper could be perfect and
    # unused -- the same "component, not seam" gap that shipped v3.66.1040.
    assert mod._gate_argv(7, 9, mod._user_argv(inner))[-3:] == ["bash", "-c",
                                                                inner]

    # The registry redirect above is still proven, with a real registration:
    # os.getpid() is a live pid, so register() publishes a complete entry.
    entry = mod.register(os.getpid(), "p", inner)
    assert list((tmp_path / "bd-jobs").glob("*.json")) == [
        tmp_path / "bd-jobs" / (entry["id"] + ".json")], (
        "nothing was registered anywhere, so the redirect above is untested "
        "and this test would keep passing if it went back to the real dir")
    real_after = {p.name for p in _REAL_JOBS.glob("*.json")} \
        if _REAL_JOBS.is_dir() else set()
    leaked = _my_new_registry_entries(real_before, real_after, os.getpid())
    assert not leaked, (
        "this test wrote entr(ies) attributed to its own pid into the REAL "
        "job registry at /tmp/bd-jobs: %s" % leaked)


# ── @1075: the version column described the tree, not the service ────────────

def test_the_probe_reads_the_running_version_not_only_the_tree(tmp_path):
    """MEASURED at v3.66.1072 on test5, and it reported a state that was false.

    `bd-fleet` derived its `version` column from `bulk_downloader/__init__.py`
    -- the working TREE. On test5 the agent's working directory IS the deployed
    tree, so immediately after a merge the column read 3.66.1072 while
    `tools/deployed_version.txt` (rewritten by ExecStartPre on every start, so
    it reflects the PROCESS) still said 3.66.1071 and the service had not been
    restarted. The fleet table showed four hosts agreeing; that was true of
    four trees and three processes.

    CLAUDE.md section 7 already records which file is which. The tool read the
    one that cannot answer the question its column header asks.

    THE FIRST VERSION OF THIS TEST WAS A GREP AND A MUTANT WALKED THROUGH IT.
    It asserted `"deployed_version.txt" in mod.PROBE` -- and the COMMENT
    explaining the fix, which sits inside the same probe string, contains that
    filename. Deleting the echo left the assertion satisfied by the prose
    describing it. CLAUDE.md section 0: a comment is inside the denominator of
    every gate that reads source text. So the probe is EXECUTED here, against a
    fake tree, and judged on what it EMITS.
    """
    import subprocess as sp
    mod = _load("bd-fleet")
    home = tmp_path / "home"
    tree = home / "BulkDownloader"
    (tree / "tools").mkdir(parents=True)
    (tree / "bulk_downloader").mkdir()
    (tree / "tools" / "deployed_version.txt").write_text(
        "9.9.9-running\nstarted: whenever\n", encoding="utf-8")
    (tree / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "9.9.9-tree"\n', encoding="utf-8")

    out = sp.run(["bash", "-c", mod.PROBE], capture_output=True, text=True,
                 env={**os.environ, "HOME": str(home)}, timeout=60).stdout
    d = mod.parse_probe(out)

    assert d.get("serving") == "9.9.9-running", (
        "the probe did not report the RUNNING version; it emitted %r" % out)
    assert d.get("version") == "9.9.9-tree", (
        "the tree reading regressed while adding the service reading: %r" % out)
    assert d["serving"] != d["version"], (
        "the fixture must make the two distinguishable or this proves nothing")


def test_a_host_serving_a_different_version_than_its_tree_is_flagged():
    """The divergence this tool exists to surface, and the one it could not see.

    Fleet-wide agreement was already checked. A single host whose SERVICE and
    TREE disagree was not -- which is the deploy-not-restarted state, and the
    one an operator most needs to be told about before running a capture.
    """
    mod = _load("bd-fleet")
    rows = [("test5", "10.0.70.164",
             {"head": "aaa", "version": "3.66.1072", "serving": "3.66.1071",
              "dirty": "0", "service": "active", "jobs": "0", "pytest": "0"}, None)]
    notes = " ".join(mod.divergences(rows))
    assert "test5" in notes and "3.66.1071" in notes and "3.66.1072" in notes, (
        "a host whose tree and service disagree was not reported: %r" % notes)
    assert "restart" in notes.lower() or "deploy" in notes.lower(), (
        "the note must say what to DO about it: %r" % notes)


def test_matching_tree_and_service_is_not_flagged():
    """The over-sensitivity control.

    A fix that flagged every host would fire on a healthy fleet and be switched
    off -- section 0 counts that as a soundness bug of equal weight.
    """
    mod = _load("bd-fleet")
    rows = [("test4", "10.0.70.85",
             {"head": "aaa", "version": "3.66.1074", "serving": "3.66.1074",
              "dirty": "0", "service": "active", "jobs": "0", "pytest": "0"}, None)]
    assert not [n for n in mod.divergences(rows) if "serving" in n.lower()], (
        "a healthy host was flagged")


def test_an_unknown_running_version_is_not_reported_as_a_mismatch():
    """UNKNOWN IS A THIRD STATE. A host whose deployed_version.txt is missing
    (never started under systemd, or a fresh checkout) must not be reported as
    serving the wrong version -- that is a gate firing on its own blindness."""
    mod = _load("bd-fleet")
    rows = [("test7", "10.0.70.84",
             {"head": "aaa", "version": "3.66.1074", "serving": "",
              "dirty": "0", "service": "active", "jobs": "0", "pytest": "0"}, None)]
    assert not [n for n in mod.divergences(rows) if "serving" in n.lower()], (
        "an absent reading was reported as a disagreement")


# ── @1078: the litter column counted two globs, not the directory ────────────

def test_the_litter_probe_counts_all_of_tmp_not_two_globs(tmp_path):
    """MEASURED on test5 at v3.66.1077: the column read 2918 while /tmp held
    15392 entries -- a 5.3x undercount, and it saw 346 of the 2373 directories
    one capture actually adds (15%).

    The globs are `bd-*` and `pytest-of-*`. The largest leak family on the fleet
    is bare `mkdtemp()` output named `tmp*`, which matches neither, so the
    column certified a denominator excluding most of its subject -- and it is
    the number anyone sizing the leak would have read.

    EXECUTED against a fake /tmp rather than grepped, because the probe is shell
    text and a comment naming a pattern is not the same as counting it.
    """
    import subprocess as sp
    mod = _load("bd-fleet")
    fake = tmp_path / "tmp"
    fake.mkdir()
    for n in ("bd-alpha", "pytest-of-mboyle", "tmpABCDEFGH", "cen_db_XYZ", "keepme"):
        (fake / n).mkdir()
    # ASSERT THE HARNESS BUILT THE SHAPE FIRST. The probe opens with
    # `cd ~/BulkDownloader || { echo tree=ABSENT; exit 0; }`, so without this
    # directory it exits before emitting anything and BOTH assertions below
    # fail for a reason that has nothing to do with the counter.
    (tmp_path / "BulkDownloader").mkdir()

    out = sp.run(["bash", "-c", mod.PROBE.replace("/tmp", str(fake))],
                 capture_output=True, text=True,
                 env={**os.environ, "HOME": str(tmp_path)}, timeout=60).stdout
    d = mod.parse_probe(out)
    assert d.get("tree") != "ABSENT" and "tmp_bd" in d, (
        "the probe never reached its litter line -- harness defect, not a "
        "counter defect: %r" % out)
    assert d.get("tmp_bd") == "5", (
        "the litter reading is %r; it must count every entry, not just the "
        "bd-* and pytest-of-* globs (the fake dir holds 5, of which only 2 "
        "match those two patterns). Emitted: %r" % (d.get("tmp_bd"), out))


def test_the_litter_reading_is_not_vacuous():
    """Over-sensitivity control: an empty directory must read 0, not 'unknown'
    or a crash -- a counter that cannot report zero is the one every verdict
    defect in section 10 had in common."""
    import subprocess as sp
    import tempfile
    mod = _load("bd-fleet")
    with tempfile.TemporaryDirectory() as empty:
        home = pathlib.Path(empty) / "home"
        (home / "BulkDownloader").mkdir(parents=True)
        scan = pathlib.Path(empty) / "scan"
        scan.mkdir()
        out = sp.run(["bash", "-c", mod.PROBE.replace("/tmp", str(scan))],
                     capture_output=True, text=True,
                     env={**os.environ, "HOME": str(home)}, timeout=60).stdout
    assert "tmp_bd" in mod.parse_probe(out), (
        "probe did not reach its litter line: %r" % out)
    assert mod.parse_probe(out).get("tmp_bd") == "0", (
        "an empty directory did not read 0: %r" % out)
