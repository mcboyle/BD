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
FLEETRUN = BIN / "bd-fleet-run"


def _run(tool: pathlib.Path, *args, cwd=None, timeout=180):
    return subprocess.run([sys.executable, str(tool), *args],
                          cwd=str(cwd or REPO), capture_output=True,
                          text=True, timeout=timeout)


def _write_transcript(path: pathlib.Path, rows) -> None:
    with path.open("w") as fh:
        for r in rows:
            fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")


# ---------------------------------------------------------------- preconditions

def test_both_tools_exist_and_parse():
    """PRECONDITION. Without it every assertion below is vacuous.

    CLAUDE.md section 6: a harness must assert that it built the shape before it
    asserts the verdict. Three tests in the v3.66.1037 cut passed because their
    fixture produced nothing to judge.
    """
    for t in (CENSUS, FLEETRUN):
        assert t.is_file(), (
            f"{t.name} is missing. It exists because a session spent 28% of its "
            "context window typing programs into the conversation, and had no "
            "instrument that could say so.")
        ast.parse(t.read_text(encoding="utf-8"))
        first = t.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("#!") and "python" in first, (
            f"{t.name} has no python shebang -- it is one of the extensionless "
            "toolchain/bin scripts a *.py glob cannot see (CLAUDE.md section 1)")


def test_both_selftests_pass_and_say_so():
    """A green exit code with no verdict behind it is the shape this repo keeps
    finding, so test_toolchain_534 requires the literal. Assert both halves."""
    for t in (CENSUS, FLEETRUN):
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


# ---------------------------------------------------------------- bd-fleet-run

def test_fleet_run_does_not_define_a_second_host_parser():
    """One definition of the fleet file.

    v3.66.1136 was spent on exactly this shape in bd-run: prune and its selftest
    each decided separately what 'a log' meant, disagreed, and failed correct
    work on 2 of 6 hosts. Asserted over the AST so a docstring naming read_hosts
    cannot satisfy or break it (CLAUDE.md section 0: a comment is inside the
    denominator of every gate that reads source text).
    """
    tree = ast.parse(FLEETRUN.read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "read_hosts" not in defined, (
        "bd-fleet-run defines its own read_hosts. Two parsers over one format "
        "is a seam; it must import bd-fleet's.")
    assert "_load_bd_fleet" in defined, (
        "bd-fleet-run has no loader for bd-fleet -- it cannot be reusing the "
        "one definition of the fleet file")


def test_fleet_run_refusals_are_distinguishable_by_reason():
    """Every refusal exits 2, so the CODE cannot be what a test asserts."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        good = tmp / "hosts"
        good.write_text("alpha 192.0.2.10\n")
        empty = tmp / "empty"
        empty.write_text("# only a comment\n")

        cases = {
            "no command": ["--hosts", str(good)],
            "missing file": ["--hosts", str(tmp / "nope"), "--", "true"],
            "empty fleet": ["--hosts", str(empty), "--", "true"],
            "bad --only": ["--hosts", str(good), "--only", "zzz", "--", "true"],
        }
        texts = {}
        for name, argv in cases.items():
            r = _run(FLEETRUN, *argv)
            assert r.returncode == 2, (
                f"{name}: expected exit 2, got {r.returncode}\n{r.stdout}{r.stderr}")
            texts[name] = r.stderr.strip()

        assert len(set(texts.values())) == len(texts), (
            "two refusals are worded identically, so a test asserting the "
            f"reason cannot tell them apart: {texts}")
        for name, t in texts.items():
            assert "REFUSED" in t, f"{name} does not announce itself: {t!r}"


def test_fleet_run_keeps_every_byte_and_returns_one_line():
    """NEVER FILTER AT CAPTURE TIME -- but the caller still reads a slice.

    CLAUDE.md section 1's rule governs the ARTIFACT, not the display. This is
    the assertion that pins the distinction: 400 lines stored on disk, one line
    per host on stdout, from the same run.
    """
    import socket
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = tmp / "hosts"
        hosts.write_text(f"{socket.gethostname()} 127.0.0.1\n")
        root = tmp / "runs"
        r = _run(FLEETRUN, "--hosts", str(hosts), "--root", str(root),
                 "--timeout", "60", "--",
                 "for i in $(seq 1 400); do echo line$i; done")
        assert r.returncode == 0, r.stdout + r.stderr

        logs = list(root.rglob("*.log"))
        assert len(logs) == 1, f"expected one host log, got {logs}"
        stored = logs[0].read_text().splitlines()
        assert len(stored) == 400, (
            f"the artifact holds {len(stored)} lines, not 400 -- something "
            "filtered at capture time, which is unrecoverable without re-running")

        # ANCHOR ON A DISTINCTIVE INTERIOR LINE, not on the substring "line" --
        # that matched the header's "last line", the echoed command, and the
        # blind-spot prose, and reported 5 where the property under test was
        # never in question. CLAUDE.md section 1: a predicate over the wrong
        # part of the subject is worse than a grep, because it looks rigorous.
        assert "line399" in "\n".join(stored), "fixture precondition"
        assert "line399" not in r.stdout, (
            "an interior line of the command's output reached the caller's "
            "stdout; the whole point is that the artifact holds it and the "
            "caller is shown a summary")
        assert "line400" in r.stdout, (
            "the summary does not show the LAST line, so the caller learns "
            "nothing about how the run ended")


def test_fleet_run_strips_the_remainder_separator():
    """THE SEAM. argparse.REMAINDER KEEPS the `--`.

    bd-jobs shipped at v3.66.1040 with eleven passing tests and a green
    selftest, and failed on its first real use because `run --host X -- sleep 90`
    reached the shell as `bash -c "-- sleep 90"`. Both sides were tested; the
    JOIN was not. This asserts on what the shell actually received.
    """
    import socket
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = tmp / "hosts"
        hosts.write_text(f"{socket.gethostname()} 127.0.0.1\n")
        root = tmp / "runs"
        r = _run(FLEETRUN, "--hosts", str(hosts), "--root", str(root),
                 "--timeout", "60", "--", "echo", "SEAM_OK")
        assert r.returncode == 0, (
            "a `--`-separated command failed, which is the exact bd-jobs "
            f"defect:\n{r.stdout}{r.stderr}")
        log = next(root.rglob("*.log"))
        body = log.read_text()
        assert "SEAM_OK" in body and "--" not in body.split("SEAM_OK")[0], (
            f"the `--` reached the shell: {body!r}")


def test_fleet_run_proves_it_can_record_before_it_launches():
    """An unwritable artifact root is a refusal, not a launch.

    CLAUDE.md section 0: bd-jobs exists because work outlived its record, and on
    its own first live invocation it started a remote command and THEN failed to
    register it. The ordering is the rule.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = tmp / "hosts"
        hosts.write_text("alpha 192.0.2.10\n")
        marker = tmp / "the_command_ran"
        r = _run(FLEETRUN, "--hosts", str(hosts),
                 "--root", "/proc/cannot/write/here", "--timeout", "30",
                 "--", f"touch {marker}")
        assert r.returncode == 2, (
            f"expected a refusal, got {r.returncode}\n{r.stdout}{r.stderr}")
        # ASSERT THE REASON, NOT THE CODE -- and this is not theoretical. On the
        # RED run, with bd-fleet-run absent, CPython ITSELF exits 2 when it
        # cannot open the script it was handed, so the returncode assertion
        # above passed against a missing tool and the marker was absent because
        # nothing had run. This test was the one green in a 12-red battery and
        # proved nothing. CLAUDE.md section 10 states the rule; the rule was
        # broken here, in the test file written to encode it.
        assert "REFUSED" in r.stderr and "not writable" in r.stderr, (
            "exit 2 came from something other than bd-fleet-run's own refusal "
            f"(stderr={r.stderr!r})")
        assert not marker.exists(), (
            "the command RAN despite the run being unrecordable -- this is the "
            "bd-jobs defect exactly: the tool whose subject is tracked work "
            "produced untracked work")


def test_fleet_run_does_not_refuse_a_valid_invocation():
    """THE OVER-SENSITIVE DIRECTION.

    A tool that refuses everything passes every refusal test above and is
    useless. CLAUDE.md section 0 counts a gate that cries wolf as a soundness
    bug equal to a false clean, because it gets switched off.
    """
    import socket
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = tmp / "hosts"
        hosts.write_text(f"{socket.gethostname()} 127.0.0.1\n")
        r = _run(FLEETRUN, "--hosts", str(hosts), "--root", str(tmp / "runs"),
                 "--timeout", "60", "--", "true")
        assert r.returncode == 0, (
            f"a valid run was refused:\n{r.stdout}{r.stderr}")
        assert "REFUSED" not in r.stderr


def test_fleet_run_reports_a_failing_host_as_failure():
    """The verdict line is the least-tested output and the only one read.

    deploy_fleet.sh --dry-run printed 'all 3 host(s) deployed and verified'
    having touched nothing (CLAUDE.md section 10). A nonzero host must make the
    whole run nonzero, or a caller cannot gate on it.
    """
    import socket
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        hosts = tmp / "hosts"
        hosts.write_text(f"{socket.gethostname()} 127.0.0.1\n")
        r = _run(FLEETRUN, "--hosts", str(hosts), "--root", str(tmp / "runs"),
                 "--timeout", "60", "--", "exit 3")
        assert r.returncode == 1, (
            f"a host exiting 3 produced overall exit {r.returncode}; a caller "
            "gating on this would ship a failure as a success")
        assert "0/1" in r.stdout or "exit=3" in r.stdout, (
            f"the summary does not surface the failing host:\n{r.stdout}")
