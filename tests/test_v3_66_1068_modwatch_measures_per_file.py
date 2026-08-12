"""bd-modwatch must answer a per-FILE question per file, and say which it asked.

@1068, backlog row 94. The tool dispatched two different measurements depending
on how it was invoked, and said nothing about which one produced a verdict:

    --all          -> targets = [[f] for f in tracked_tests(root)]   # per FILE
    explicit files -> targets = [list(args.files)]                   # ONE group

So naming three files ran them as a single co-batched unit, attributed the
result to a space-joined label, and reported it with the word "file(s)". A batch
of N could only ever report 0 or 1, it could never say WHICH file leaked, and a
wipe in one file offset by an import in another nets out to a clean verdict.

THAT IS THE ORIGIN OF A RETRACTED CLAIM. Backlog row 22 carried "bd-modwatch
reports 0 for those files" as evidence against a full-suite probe; the reading
came from a BATCH invocation and was compared against a PER-FILE question. The
two instruments were never measuring the same thing.

Row 94's own prescription is the fix: make the tool state which mode produced
the verdict, so a batch artifact cannot be read as a per-file answer.
"""

import importlib.machinery
import importlib.util
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-modwatch"

_LEAKER = "tests/test_v3_66_1034_guards_survive_a_module_wipe.py"
_CLEAN = "tests/test_v3_66_1052_the_backlog_is_machine_visible.py"


def _load():
    spec = importlib.util.spec_from_loader(
        "bd_modwatch", importlib.machinery.SourceFileLoader(
            "bd_modwatch", str(_TOOL)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mw():
    return _load()


def test_the_tool_loads(mw):
    assert hasattr(mw, "main"), "bd-modwatch has no main()"


def test_named_files_are_measured_one_at_a_time(mw):
    """THE DEFECT. Naming files must not silently change the question.

    Read the dispatch rather than running pytest three times: the property is
    structural, and a behavioural check here would cost minutes per run.
    """
    src = _TOOL.read_text(encoding="utf-8")
    from python_source import python_code_only
    code = python_code_only(_TOOL)
    assert "targets" in code, "dispatch not found -- has the tool been rewritten?"
    assert "targets = [list(args.files)]" not in code, (
        "explicit files are still collapsed into ONE group, so the verdict is "
        "about a co-batched run and cannot name which file leaked -- while the "
        "summary still calls the groups 'file(s)'"
    )


def test_the_summary_states_which_mode_produced_it(mw):
    """Row 94's prescription: a batch artifact must not read as a per-file one."""
    from python_source import python_code_only
    code = python_code_only(_TOOL)
    lowered = code.lower()
    assert "per-file" in lowered or "per file" in lowered or "together" in lowered, (
        "the tool never names its measurement mode in its output, so a reader "
        "cannot tell a per-file verdict from a co-batched one -- which is how "
        "a batch reading became evidence in backlog row 22"
    )


def test_a_batch_mode_still_exists_and_is_opt_in(mw):
    """The co-batched question is legitimate -- it is what a real pytest run
    does. It must remain askable, but only when asked for."""
    src = _TOOL.read_text(encoding="utf-8")
    assert "--together" in src, (
        "the co-batched measurement has been removed rather than made explicit; "
        "it is the question a real --dist loadfile run answers and must stay "
        "reachable"
    )


def test_the_selftest_covers_the_dispatch(mw):
    """A tool whose selftest never exercises its own argv dispatch reports
    green over the half that was broken."""
    src = _TOOL.read_text(encoding="utf-8")
    i = src.find("def selftest")
    assert i != -1, "no selftest"
    body = src[i:]
    assert "plan_targets" in body or "--together" in body, (
        "selftest does not touch the dispatch that produced the wrong "
        "measurement -- it would stay green through exactly this defect"
    )


def test_end_to_end_two_files_are_reported_separately():
    """RUN IT. The structural checks above cannot see an output-format bug.

    One leaker and one clean file, named together: the leaker must be named on
    its own row and the clean one must not appear.
    """
    r = subprocess.run(
        [sys.executable, str(_TOOL), _LEAKER, _CLEAN, "--timeout", "600"],
        cwd=_REPO, capture_output=True, text=True, timeout=1200)
    out = r.stdout
    assert _LEAKER in out, f"the leaker is not named in the output:\n{out[:600]}"
    joined = "%s %s" % (_LEAKER, _CLEAN)
    assert joined not in out, (
        f"the two files are reported as ONE space-joined label, so the verdict "
        f"cannot be attributed to either:\n{out[:600]}"
    )


# ── behavioural, because four mutants escaped the structural checks ──────
#
# The tests above read the tool's TEXT. A mutant that made --together a no-op,
# one that dropped the mode from the verdict, one that hardcoded the unit label
# and one that replaced the refusal with a guess ALL survived them. Source text
# is not behaviour -- the same lesson v3.66.1058 and v3.66.1066 each paid for.
# plan_targets is a pure function, so this costs milliseconds.

class _Args:
    def __init__(self, files=(), all=False, together=False):
        self.files, self.all, self.together = list(files), all, together


def _tracked(_root):
    return ["a.py", "b.py"]


def test_named_files_plan_one_group_each(mw):
    got, mode = mw.plan_targets(_Args(files=["x.py", "y.py"]), _tracked, ".")
    assert got == [["x.py"], ["y.py"]], got
    assert mode == "per-file", mode


def test_together_plans_one_co_batched_group(mw):
    got, mode = mw.plan_targets(
        _Args(files=["x.py", "y.py"], together=True), _tracked, ".")
    assert got == [["x.py", "y.py"]], (
        f"--together must co-batch the named files, got {got!r} -- if it is "
        f"silently ignored the co-batched question becomes unaskable"
    )
    assert mode == "together", mode


def test_all_plans_one_group_per_tracked_file(mw):
    got, mode = mw.plan_targets(_Args(all=True), _tracked, ".")
    assert got == [["a.py"], ["b.py"]], got
    assert mode == "per-file", mode


@pytest.mark.parametrize("args,why", [
    (_Args(), "neither files nor --all"),
    (_Args(files=["x.py"], all=True), "files AND --all together"),
])
def test_it_refuses_rather_than_guessing_a_denominator(mw, args, why):
    """A tool that guesses a denominator is the whole subject of section 0."""
    with pytest.raises(mw.Refused):
        mw.plan_targets(args, _tracked, ".")


def _tiny_repo_run(tmp_path, *flags):
    """Run the CLI against one trivial test file -- fast, and enough to read
    the verdict line, which is where the remaining defects lived."""
    t = tmp_path / "test_tiny_modwatch_probe.py"
    t.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(_TOOL), str(t), "--timeout", "120", *flags],
        cwd=_REPO, capture_output=True, text=True, timeout=600)
    return r.stdout + r.stderr


def test_the_verdict_line_names_the_mode(tmp_path):
    out = _tiny_repo_run(tmp_path)
    assert "[mode: per-file]" in out, (
        f"the verdict does not name its measurement mode, so a co-batched "
        f"artifact reads as a per-file answer:\n{out[:400]}"
    )


def test_the_verdict_unit_changes_with_the_mode(tmp_path):
    per = _tiny_repo_run(tmp_path)
    tog = _tiny_repo_run(tmp_path, "--together")
    assert "file(s) leave" in per, per[:300]
    assert "co-batched group(s) leave" in tog, (
        f"--together still counts in 'file(s)', but its unit is a GROUP -- the "
        f"same number would mean two different things:\n{tog[:400]}"
    )
    assert "[mode: together]" in tog, tog[:300]
