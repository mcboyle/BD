"""The two questions this session could not answer without a heredoc.

MEASURED 2026-08-14 at v3.66.1139. A session reached 678.9k/1.0M of context with
nothing able to say what had spent it. Answering it by hand took a 40-line
heredoc typed into the conversation -- and the answer was that typing programs
into the conversation was the second-largest consumer:

    tool_result                 143,374 tok  476 calls   39%
    tool_use:Bash (my OWN cmds) 102,910 tok  367 calls   28%
      of which heredoc/file-writes: 45 calls, 27,594 tok

So the measurement indicted the method used to take it. CLAUDE.md section 8
already says look in toolchain/bin before hand-rolling anything; these two tools
are what that rule demands exist, and this file is what makes them WIRED rather
than merely present (test_toolchain_534's distinction between the tools that are
invoked and the ones that are only described).

WHY THE ASSERTIONS BELOW ARE SHAPED THIS WAY:

  * They assert on MEASURED chars, never on the estimated token figure.
    bd-context-census converts chars to tokens through one ratio calibrated on
    one prose corpus; that number is allowed to be wrong and must never be what
    a gate depends on. CLAUDE.md section 1: say which denominator a count is
    over, in the same sentence as the count.

  * They drive the tools rather than reading them. CLAUDE.md section 0's
    strongest instrument rule -- to ask whether something touches a resource,
    monkeypatch the resource, do not read -- generalises to "to ask whether a
    tool refuses, run it and read stderr".

  * They assert the REASON of each refusal, not the exit code. Both tools use
    exit 2 for every refusal, so a test asserting the code passes when any of
    them fires; four mutants escaped exactly that way in bd-jobs and bd-ab
    (CLAUDE.md section 10).

  * They assert the OVER-SENSITIVE direction too. A tool that refuses
    everything passes every refusal test and is useless -- CLAUDE.md section 0
    counts a gate that cries wolf as a soundness bug, not a safe default.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile

# Its subject is two tools, not the tree.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
BIN = REPO / "toolchain" / "bin"
CENSUS = BIN / "bd-context-census"


def _run(tool: pathlib.Path, *args, cwd=None, timeout=180):
    return subprocess.run([sys.executable, str(tool), *args],
                          cwd=str(cwd or REPO), capture_output=True,
                          text=True, timeout=timeout)


def _write_transcript(path: pathlib.Path, rows) -> None:
    with path.open("w") as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


# ---------------------------------------------------------------- preconditions

def test_the_census_tool_exists_and_parses():
    """PRECONDITION. Without it every assertion below is vacuous.

    CLAUDE.md section 6: a harness must assert that it built the shape before it
    asserts the verdict. Three tests in the v3.66.1037 cut passed because their
    fixture produced nothing to judge.
    """
    for t in (CENSUS,):
        assert t.is_file(), (
            f"{t.name} is missing. It exists because a session spent 28% of its "
            "context window typing programs into the conversation, and had no "
            "instrument that could say so.")
        ast.parse(t.read_text(encoding="utf-8"))
        first = t.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("#!") and "python" in first, (
            f"{t.name} has no python shebang -- it is one of the extensionless "
            "toolchain/bin scripts a *.py glob cannot see (CLAUDE.md section 1)")


def test_the_census_selftest_passes_and_says_so():
    """A green exit code with no verdict behind it is the shape this repo keeps
    finding, so test_toolchain_534 requires the literal. Assert both halves."""
    for t in (CENSUS,):
        r = _run(t, "--selftest")
        out = r.stdout + r.stderr
        assert r.returncode == 0, f"{t.name} --selftest exited {r.returncode}:\n{out[-1500:]}"
        assert "SELFTEST PASS" in out, (
            f"{t.name} --selftest exited 0 without printing a verdict")


# ------------------------------------------------------------ bd-context-census

def test_census_measures_chars_exactly():
    """The one quantity that must be exact. Tokens are estimated; chars are not."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        # The census resolves HOME/.claude/projects/<mangled-cwd>. The first
        # draft of this fixture omitted the `.claude` level, so the tool
        # correctly refused and the test read it as a tool defect.
        proj = tmp / ".claude" / "projects" / "-fake-project"
        proj.mkdir(parents=True)
        _write_transcript(proj / "sess.jsonl", [
            {"message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "q" * 300}}]}},
            {"message": {"role": "user", "content": [
                {"type": "tool_result", "content": "r" * 7777}]}},
        ])
        env = dict(os.environ)
        env["HOME"] = str(tmp)
        r = subprocess.run(
            [sys.executable, str(CENSUS), "--work", "/fake/project", "--top", "0"],
            capture_output=True, text=True, env=env, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "7,777" in r.stdout, (
            "the measured tool_result char count did not reach the table:\n"
            + r.stdout)


def test_census_fails_closed_when_it_cannot_find_a_transcript():
    """UNKNOWN is a third state and it FAILS (CLAUDE.md section 0).

    Reporting '0 tokens' for a transcript that could not be found would be a
    gate that cannot see its subject reporting clean -- truthfully, uselessly.
    """
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env["HOME"] = td
        r = subprocess.run(
            [sys.executable, str(CENSUS), "--work", "/no/such/project"],
            capture_output=True, text=True, env=env, timeout=120)
        assert r.returncode == 2, (
            f"expected exit 2 (UNRUNNABLE), got {r.returncode}. A census that "
            "cannot find its subject must not report a clean zero.")
        assert "UNRUNNABLE" in (r.stdout + r.stderr), (
            "the refusal does not name itself as unrunnable")


def test_census_states_its_blind_spots_in_its_normal_output():
    """Not in a README -- in the output, where the reader cannot skip it.

    CLAUDE.md section 1: an instrument's wrong answer is inherited by everything
    downstream and arrives wearing the authority of a measurement. The socket
    recorder is the model and this is the same contract.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        # The census resolves HOME/.claude/projects/<mangled-cwd>. The first
        # draft of this fixture omitted the `.claude` level, so the tool
        # correctly refused and the test read it as a tool defect.
        proj = tmp / ".claude" / "projects" / "-fake-project"
        proj.mkdir(parents=True)
        _write_transcript(proj / "s.jsonl", [
            {"message": {"role": "user", "content": [
                {"type": "tool_result", "content": "x" * 50}]}}])
        env = dict(os.environ)
        env["HOME"] = str(tmp)
        r = subprocess.run(
            [sys.executable, str(CENSUS), "--work", "/fake/project"],
            capture_output=True, text=True, env=env, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "CANNOT SEE" in r.stdout, "the census does not state its limits"
        # The estimated-vs-measured caveat is the one that matters most, because
        # every token figure in the table depends on it.
        assert "ESTIMATED" in r.stdout and "FLOOR" in r.stdout, (
            "the output does not warn that its token figures are a floor")
        # And thinking blocks -- found only by running the tool against a real
        # transcript, where 328 of them were recorded with zero chars.
        assert "thinking blocks" in r.stdout, (
            "the census does not disclose that reasoning tokens are paid and "
            "invisible to it")


def test_census_module_scope_import_touches_no_transcript():
    """Importing the tool must not read anything. It is a script, not a probe."""
    src = CENSUS.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        assert not isinstance(node, (ast.For, ast.While)), (
            "bd-context-census does work at module scope; it must do nothing "
            "until main() runs")


# bd-fleet-run's tests LIVED HERE and were moved to
# tests/test_v3_66_1142_fleet_run_is_hermetic.py at v3.66.1142.
#
# Two of them could reach the network. `test_fleet_run_proves_it_can_record_
# before_it_launches` drove main() with a fleet file naming `alpha 192.0.2.10`
# and an unwritable `--root`; the refusal it asserted held only because
# `mkdir /proc/cannot/write/here` fails on procfs, and had it succeeded control
# reached `ssh ... 192.0.2.10`. Its own guard could not have caught that: it
# asserted a LOCAL marker path stayed absent while the touch would have run on
# the REMOTE host.
#
# The replacement suite drives an INJECTED runner and carries a
# subprocess-level egress guard that fails on any attempt to launch
# ssh/scp/sftp/rsync/bash, so "these tests cannot reach a network" is a
# property of the wiring rather than of an address being unroutable.
#
# What remains in this file is bd-context-census coverage only.
