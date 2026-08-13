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

# bd-modwatch is the instrument the suite's module-wipe measurements come from;
# this file's subject is that instrument, not a product module.
BD_GATE_SCOPE = "repo-wide"
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


def test_end_to_end_a_leaker_and_a_clean_file_are_reported_separately(tmp_path):
    """RUN IT -- the structural checks cannot see an output-format bug.

    SYNTHETIC LEAKER, DELIBERATELY. This first named
    test_v3_66_1034_guards_survive_a_module_wipe.py as the leaker, and
    v3.66.1069 fixed that file so its wipe stops at its own file -- which broke
    this test. A check that depends on a DEFECT still existing dies the moment
    the defect is fixed, and its death looks like a regression. Generate the
    leak instead: the property under test is the tool's reporting, not any
    particular file's brokenness.
    """
    leaky = tmp_path / "test_modwatch_e2e_leaky.py"
    leaky.write_text(
        "import sys\n"
        "import bulk_downloader.constants  # noqa: F401  -- bound at MODULE scope\n"
        "\n"
        "def test_wipes_and_never_restores():\n"
        "    for m in [m for m in sys.modules\n"
        "              if m == 'bulk_downloader' or m.startswith('bulk_downloader.')]:\n"
        "        del sys.modules[m]\n",
        encoding="utf-8")
    clean = tmp_path / "test_modwatch_e2e_clean.py"
    clean.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    r = subprocess.run(
        [sys.executable, str(_TOOL), str(leaky), str(clean), "--timeout", "300"],
        cwd=_REPO, capture_output=True, text=True, timeout=900)
    out = r.stdout

    # PRECONDITION: the harness built a real leak, or "not reported" below
    # would pass over a fixture that never leaked (section 6).
    assert leaky.name in out, (
        f"the synthetic leaker was not detected at all -- the fixture, not the "
        f"tool, is what failed:\n{out[:600]}\n{r.stderr[:400]}"
    )
    assert clean.name not in out, (
        f"the clean file is reported as an offender:\n{out[:600]}"
    )
    joined = "%s %s" % (leaky, clean)
    assert joined not in out, (
        f"the two files are reported as ONE space-joined label, so the verdict "
        f"cannot be attributed to either:\n{out[:600]}"
    )
    assert "[mode: per-file]" in out, out[:400]
