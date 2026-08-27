"""Batch E: a suite result now carries the machine it came from, and the chain.

TWO FAILURES PAID FOR THIS. Two full-suite runs of the same tree in one session
reported 1 failure and 35, and nothing in either result recorded that the second
had four other suites sharing the box; every conclusion drawn from a single run
that session had to be retracted and one prediction was wrong. Separately, item
48 was bracketed twice by replaying ONE xdist worker's real file chain, and both
times that chain had to be rebuilt by hand out of `-v` output.

So `tests/_run_context.py` records what the run ran on, each worker records what
it actually executed, and the master prints both beside the result.

WHAT IS NOT CLAIMED. This makes a run REPRODUCIBLE, not DETERMINISTIC.
`--dist loadfile` hands files to whichever worker is free and nothing here pins
that. The deliverable is the assignment that DID happen, in a form `bd-ladder`
can replay exactly -- which is what every investigation has actually needed.
"""
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _run_context as rc                                    # noqa: E402

_REPO = pathlib.Path(__file__).resolve().parent.parent


# RE-ENTRY GUARD. Two tests here spawn pytest to inspect what the terminal
# summary prints. The first version of the xdist one handed the INNER run this
# whole file, so every spawned pytest spawned two more: 301 processes before it
# was killed, and no test result at all. A node id would have been enough on its
# own; the env var is here because the next person to add a subprocess test to
# this file will not know that, and the failure mode is a fork bomb.
_NESTED = "BD_NESTED_PYTEST"


def _pytest(*args, timeout=300):
    env = dict(os.environ, BD_DISABLE_KEEPALIVE="1", **{_NESTED: "1"})
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-p", "no:randomly"],
        capture_output=True, text=True, timeout=timeout, cwd=str(_REPO), env=env)


spawns_pytest = pytest.mark.skipif(
    os.environ.get(_NESTED) == "1",
    reason="nested pytest: this test spawns pytest, and the outer run covers it")


# ── what the run ran on (item 16) ────────────────────────────────────────────

def test_the_context_names_the_machine_and_the_worker_count():
    class Opt:
        numprocesses = 12
        dist = "loadfile"

    class Cfg:
        option = Opt()

    ctx = rc.context(Cfg())
    assert ctx["workers"] == 12 and ctx["workers_from"] == "-n"
    assert ctx["dist"] == "loadfile"
    assert ctx["cores"] >= 1
    assert ctx["host"]

    Opt.numprocesses = None
    ctx = rc.context(Cfg())
    assert ctx["workers"] == 1 and ctx["workers_from"] == "serial", (
        "a serial run must say so; recording it as 0 or None makes the field "
        "unusable for the comparison it exists for")


def test_a_loaded_box_is_called_out_because_its_failure_count_is_not_comparable():
    """THE LESSON, mechanised. 1 failure and 35 from the same tree, and the
    only difference was four other suites on the box."""
    loaded = {"cores": 80, "workers": 32, "load_at_start": [60.0, 50, 40]}
    notes = " ".join(rc.advise(loaded))
    assert "not comparable" in notes, (
        "a run starting at load 60 on 80 cores was reported without comment")

    idle = {"cores": 80, "workers": 32, "load_at_start": [0.4, 0.5, 0.6]}
    assert not rc.advise(idle), (
        "an idle, sensibly sized run produced a note. A line that appears on "
        "every run is a line nobody reads: %s" % rc.advise(idle))

    assert "oversubscribes" in " ".join(rc.advise(
        {"cores": 8, "workers": 64, "load_at_start": [0.1, 0, 0]}))
    assert "shape of the machine" in " ".join(rc.advise(
        {"cores": 86, "workers": 1, "load_at_start": [0.1, 0, 0]})), (
        "serial on 86 cores went unremarked -- `-n 4` on an 86-core box is the "
        "measured mistake this note exists for")


# ── the chain (item 24) ──────────────────────────────────────────────────────

def test_a_chain_records_each_file_once_in_first_seen_order(tmp_path):
    """DEDUPED, not transition-only, and the difference is measured.

    The xdist MASTER re-emits every worker's events interleaved. Under a
    transition rule that turned three test files into a THIRTY-TWO entry chain
    on the first real xdist run. A chain is a process's file sequence, and a
    file appears in that sequence once.
    """
    d = tmp_path / "run"
    for path in ("a.py", "a.py", "b.py", "a.py", "c.py", "b.py"):
        rc.note_file(d, "gw0", path)
    assert rc.read_chains(d) == {"gw0": ["a.py", "b.py", "c.py"]}


def test_chains_are_per_worker_and_survive_a_worker_that_never_finishes(tmp_path):
    d = tmp_path / "run"
    rc.note_file(d, "gw0", "a.py")
    rc.note_file(d, "gw1", "b.py")
    rc.note_file(d, "gw0", "c.py")
    # No close, no flush, no cleanup -- exactly the state a killed worker leaves.
    chains = rc.read_chains(d)
    assert chains == {"gw0": ["a.py", "c.py"], "gw1": ["b.py"]}, (
        "a chain was unreadable without an orderly shutdown, which is the run "
        "an investigation cares most about")


def test_the_assignment_file_names_the_worker_for_every_file(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    chains = {"gw0": ["a.py", "b.py"], "gw1": ["c.py"]}
    path = rc.write_assignment(d, chains, {"cores": 4})
    data = json.loads(path.read_text())
    assert data["assignment"] == {"a.py": "gw0", "b.py": "gw0", "c.py": "gw1"}
    assert data["chains"] == chains
    assert data["context"]["cores"] == 4, (
        "the assignment was written without the machine it came from, so two "
        "of them cannot be compared")


def test_the_recorder_bounds_its_own_retention(tmp_path, monkeypatch):
    """Creating a path is a promise to remove it -- 744 leaked directories,
    from a recorder in this same conftest that forgot it."""
    monkeypatch.setattr(rc, "sink_dir", lambda: tmp_path)
    for i in range(6):
        run = tmp_path / ("run%d" % i)
        run.mkdir()
        chain = run / "gw0.chain"
        chain.write_text("x\n")
        # Age the CONTENT, not just the directory: prune ranks by the newest
        # file mtime (v3.66.1199 / row 179), because an append-only chain leaves
        # the directory mtime frozen while the run is still live.
        os.utime(chain, (1000 + i, 1000 + i))
        os.utime(run, (1000 + i, 1000 + i))
    assert rc.prune(keep=2) == 4
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["run4", "run5"], (
        "prune kept the wrong runs (%s) -- it must keep the NEWEST" % left)


def test_recording_a_file_costs_almost_nothing_per_test(tmp_path):
    """Anything added to EVERY test run has to be measured, not assumed.

    The write happens once per new file, and every other call is a set lookup.
    2000 calls over 200 distinct files is the shape of a full suite on one
    worker.
    """
    d = tmp_path / "run"
    start = time.perf_counter()
    for i in range(2000):
        rc.note_file(d, "gw0", "f%d.py" % (i % 200))
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, (
        "2000 note_file calls took %.2fs -- that is per full suite per worker, "
        "and it is being paid by every run forever" % elapsed)
    assert len(rc.read_chains(d)["gw0"]) == 200


# ── the seam: both summaries survive in one conftest (items 16 + 24) ─────────

@spawns_pytest
def test_the_run_context_and_the_socket_recorder_both_still_print():
    """DEFINING `pytest_terminal_summary` TWICE IN ONE MODULE SILENTLY REPLACES
    THE FIRST. That happened while writing this cut: the socket recorder's
    summary disappeared from a clean run and the only evidence was a missing
    line -- no error, no warning, a green suite.

    Neither line is decoration. The socket line states what the recorder could
    not see; the context line states the machine the failure count came from.
    A test that only checked its own new line would have shipped the loss.
    """
    r = _pytest("tests/test_v3_66_1044_run_context_and_chains.py::"
                "test_a_chain_records_each_file_once_in_first_seen_order", "-q")
    out = r.stdout + r.stderr
    assert "socket recorder [stage 1]" in out, (
        "the socket recorder's summary is gone -- a second hook of the same "
        "name replaced it:\n%s" % out[-1500:])
    assert "run context:" in out, "the run-context line is missing:\n%s" % out[-1500:]
    assert "cores," in out and "load" in out
    assert "SigIgn=0x" in out and "SigBlk=0x" in out, (
        "the terminal record omitted the process signal identity:\n%s"
        % out[-1500:]
    )


@spawns_pytest
def test_under_xdist_the_master_writes_no_chain_of_its_own():
    """The master runs nothing and re-emits everyone's events. A chain from it
    is not a sequence any process executed, and it would be replayed as one.

    TWO NAMED NODE IDS IN TWO FILES, never a whole file: the inner run must not
    re-enter the tests that spawn pytest. Two files is also the minimum that
    makes the assertion mean anything -- with one file there is one chain, and
    "the master wrote no chain" would hold trivially.
    """
    r = _pytest("tests/test_v3_66_1044_run_context_and_chains.py::"
                "test_a_chain_records_each_file_once_in_first_seen_order",
                "tests/test_v3_66_1043_measurement_and_fleet_tools.py::"
                "test_bd_run_never_reports_a_pass_it_did_not_see",
                "-n", "2", "--dist", "loadfile", "-q", timeout=300)
    out = r.stdout + r.stderr
    assert "worker chain(s)" in out, out[-1500:]
    line = [ln for ln in out.splitlines() if "worker chain(s)" in ln][0]
    directory = line.split(":")[-1].strip()
    chains = rc.read_chains(directory)
    assert "main" not in chains, (
        "the xdist master wrote a chain (%s). Its entries are every worker's "
        "files interleaved." % sorted(chains))
    assert chains, "no worker wrote a chain at all"
    total = sum(len(v) for v in chains.values())
    assert total == 2, (
        "two test files ran and the chains hold %d entr(ies) -- %s"
        % (total, chains))


def test_bd_run_reads_a_colourised_log_exactly_like_a_plain_one():
    """MEASURED on this cut's own band, and the harmless direction is the one
    that showed: 2061 tests passed, and bd-run reported SUMMARY UNKNOWN because
    pytest had written the summary as `ESC[32mESC[1m2061 passed ...` and a
    `^\d+ passed` anchor cannot see past the escape. The dangerous direction is
    the same anchors carrying the FAILED lines -- a coloured log full of
    failures would have been summarised as having none.
    """
    mod = _bd_run()
    coloured = ("\x1b[32m\x1b[1m2061 passed\x1b[0m, \x1b[33m6 skipped\x1b[0m"
                "\x1b[32m in 175.10s (0:02:55)\x1b[0m\n")
    block = "\n".join(mod.verdict(coloured))
    assert "2061 passed" in block, block
    assert "UNKNOWN" not in block, (
        "a clean 2061-test run was reported as an unknown outcome: %s" % block)

    block = "\n".join(mod.verdict(
        "\x1b[31mFAILED tests/a.py::x\x1b[0m - boom\n"
        "\x1b[31m1 failed\x1b[0m in 1s\n"))
    assert "FAILED tests/a.py::x" in block, (
        "a colourised FAILED line was invisible to the verdict: %s" % block)


# ── auto-sizing the worker count (item 19) ───────────────────────────────────

def _bd_run():
    import importlib.machinery
    import importlib.util
    path = _REPO / "toolchain" / "bin" / "bd-run"
    spec = importlib.util.spec_from_loader(
        "bd_run_e", importlib.machinery.SourceFileLoader("bd_run_e", str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_auto_n_scales_with_the_box_and_never_overrides_a_choice():
    """`-n 4` was typed on an 86-core box and nothing said it was wrong."""
    mod = _bd_run()
    assert mod.auto_workers(86) == 28
    assert mod.auto_workers(3) == 2, "a tiny box must still get at least 2"

    parts, note = mod.inject_workers(["python", "-m", "pytest", "tests/"], cores=90)
    assert parts[-2:] == ["-n", "30"] and "added" in note

    parts, note = mod.inject_workers(["python", "-m", "pytest", "-n", "4"], cores=90)
    assert parts[-2:] == ["-n", "4"] and "left alone" in note, (
        "auto-n rewrote an explicit choice. A wrapper that silently changes the "
        "command you typed makes the log describe a run that did not happen")

    parts, note = mod.inject_workers(["ls", "-l"], cores=90)
    assert parts == ["ls", "-l"] and "not a pytest" in note


@pytest.mark.parametrize("flag", ["-n4", "--numprocesses=8"])
def test_auto_n_recognises_the_other_spellings_of_a_worker_count(flag):
    """`-n 4`, `-n4` and `--numprocesses=8` are the same instruction, and a
    check that sees only the spaced form appends a SECOND -n."""
    mod = _bd_run()
    parts, note = mod.inject_workers(["python", "-m", "pytest", flag], cores=90)
    assert parts == ["python", "-m", "pytest", flag], (
        "auto-n appended a worker count to a command that already had one "
        "(%s): %s" % (flag, parts))
    assert "left alone" in note
