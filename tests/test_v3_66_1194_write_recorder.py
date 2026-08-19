"""@1194 contract for the opt-in Linux filesystem mutation recorder."""
from __future__ import annotations

import importlib.machinery
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "toolchain/bin/bd-writerec"
FIXTURES = REPO / "tests/fixtures/writerec"
mod = importlib.machinery.SourceFileLoader("bd_writerec_1194", str(TOOL)).load_module()


def _parse(name: str):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    assert len(text.splitlines()) > 1
    events, footer = mod.parse_trace(text, launch_cwd="/work", roots=("/work",), root_pid=100)
    assert footer["events"] == len(events) > 0
    return events, footer


def test_create_truncate_opens_are_one_mutation_operation_and_failures_are_attempts():
    events, footer = _parse("opens.strace")
    opens = [e for e in events if e["syscall"] in {"openat", "openat2", "creat"}]
    assert len(opens) == 11
    assert [e["operation"] for e in opens] == [
        "open_mutation", "open_mutation", "open_mutation", "open_mutation",
        "open_mutation", "open_mutation", "open_mutation", "open_mutation", "open_mutation", "open_mutation", "metadata",
    ]
    assert [e["success"] for e in opens] == [True, False, True, False, True, False, True, True, False, True, True]
    assert opens[0]["flags"] == ["O_WRONLY", "O_CREAT", "O_TRUNC"]
    assert opens[0]["operation_count"] == 1  # schema pin: one syscall is one operation
    assert footer["successful_mutations"] == 6


def test_trace_set_has_exactly_one_machine_readable_policy_and_real_oracles():
    matrix = mod.coverage_document()
    assert len(matrix["trace_set"]) > 20
    assert set(matrix["trace_set"]) == set(matrix["policies"])
    assert all(v in {"parsed_mutation", "parsed_metadata", "coverage_gap_UNKNOWN"} for v in matrix["policies"].values())
    assert all(isinstance(v, str) for v in matrix["policies"].values())
    events, _ = _parse("families.strace")
    by_call = {}
    for event in events:
        by_call.setdefault(event["syscall"], []).append(event)
    mutation_calls = {name for name, policy in matrix["policies"].items() if policy == "parsed_mutation"}
    assert mutation_calls
    for name in mutation_calls:
        assert name in by_call, f"missing oracle for {name}"
        assert any(e["success"] for e in by_call[name]), f"missing success oracle for {name}"
        assert any(not e["success"] for e in by_call[name]), f"missing failed oracle for {name}"


def test_byte_movers_use_destination_operand_and_short_result_arithmetic():
    events, footer = _parse("families.strace")
    movers = {e["syscall"]: e for e in events if e["syscall"] in {
        "pwrite64", "writev", "pwritev", "pwritev2", "sendfile", "copy_file_range", "splice"
    } and e["success"]}
    assert len(movers) == 7
    assert movers["pwrite64"]["path"] == "/dst/pwrite" and movers["pwrite64"]["requested_bytes"] == 9
    assert movers["writev"]["path"] == "/dst/writev" and movers["writev"]["requested_bytes"] == 7
    assert movers["pwritev"]["requested_bytes"] == 11 and movers["pwritev2"]["requested_bytes"] == 15
    assert movers["sendfile"]["path"] == "/dst/sendfile"
    assert movers["copy_file_range"]["path"] == "/dst/copy"
    assert movers["splice"]["path"] == "/dst/splice"
    assert {e["written_bytes"] for e in movers.values()} == {3}
    assert footer["written_bytes"] >= 21


def test_reverse_resumes_process_trees_paths_renames_and_unknown_gaps():
    events, footer = _parse("process_tree.strace")
    writes = [e for e in events if e["syscall"] == "write"]
    assert footer["pids_seen"] >= 3
    assert [(e["pid"], e["path"], e["written_bytes"], e["split"]) for e in writes] == [
        (102, "/work/fork/a file", 3, True), (101, "/work/thread/b", 2, True)
    ]
    rename = next(e for e in events if e["syscall"] == "rename")
    assert (rename["path"], rename["target_path"]) == ("/work/thread/a>b", "/work/thread/c>d")
    assert footer["coverage_gaps"] == ["io_uring", "mmap_shared_write"]
    assert footer["complete"] is False and footer["result"] == "UNKNOWN"


def test_failed_syscalls_and_unresolved_fds_are_preserved_not_counted():
    events, footer = _parse("families.strace")
    failed = [e for e in events if not e["success"]]
    assert len(failed) > 10 and all(e["errno"] for e in failed)
    assert all(e["written_bytes"] is None for e in failed)
    assert any(e["path_source"] == "unresolved" for e in events)
    assert footer["successful_mutations"] == len([e for e in events if e["success"] and e["operation"] not in {"metadata", "coverage_gap"}])


def _live_capable():
    return sys.platform.startswith("linux") and shutil.which("strace") is not None


LIVE = pytest.mark.skipif(not _live_capable(), reason="live strace capability unavailable")
# An unbounded high-rate writer: it provably outruns any finite byte budget.  It rewrites
# offset 0 forever, so a mutant that lets it escape cannot grow a file, and it carries its
# own alarm so an escaped child cannot outlive the lane.
HOT_CHILD = ("import os, signal\n"
             "signal.alarm(20)\n"
             "f = open(%r, 'wb')\n"
             "while True: os.pwrite(f.fileno(), b'x', 0)\n")


def _hot(tmp_path, marker):
    return [sys.executable, "-c", HOT_CHILD % str(tmp_path / marker)]


def _recorder(tmp_path, out, *extra, command, timeout=25):
    return subprocess.run([sys.executable, str(TOOL), "--out", str(out), "--json", *extra,
                           "--", *command], cwd=tmp_path, capture_output=True, text=True,
                          timeout=timeout)


def _records(out: Path):
    return [json.loads(line) for line in out.read_text().splitlines()]


def _raw_dir(records):
    raw_dir = Path(records[-1]["raw_log_dir"])
    files = sorted(p for p in raw_dir.iterdir() if p.is_file())
    return raw_dir, files, sum(p.stat().st_size for p in files)


@LIVE
@pytest.mark.timeout(90)
def test_live_raw_capture_is_bounded_to_the_declared_byte_budget_in_memory_and_on_disk(tmp_path):
    """R1/R2/R3: --max-raw-bytes bounds the acquisition itself, not a poll threshold."""
    out = tmp_path / "bounded.jsonl"
    marker = "bd-writerec-bound-" + str(os.getpid())
    run = _recorder(tmp_path, out, "--exit-mode", "recorder", "--max-raw-bytes", "4096",
                    command=_hot(tmp_path, marker))
    assert run.returncode == 2, run.stdout + run.stderr
    summary = json.loads(run.stdout.splitlines()[0])
    records = _records(out)
    footer = records[-1]
    # precondition: the workload really did outrun the budget, so equality is a bound
    assert footer["record_type"] == "run_footer" and footer["events"] > 0
    assert summary["raw_limit_exceeded"] is True
    # R1 -- the declared bound is the bound, to the byte
    assert summary["raw_bytes"] == 4096, "acquisition overshot the declared cap"
    # R2 -- disk is bounded by the same number; no second full copy of the trace exists
    raw_dir, files, disk = _raw_dir(records)
    assert files and disk <= 4096, "raw artifact on disk exceeds the declared cap"
    assert not (raw_dir / "trace").exists(), "combined trace duplicates the per-pid capture"
    assert all(p.name.endswith(".strace") for p in files)
    assert summary["max_raw_bytes"] == 4096
    assert summary["time_limit_exceeded"] is False
    assert footer["raw_limit_exceeded"] is True and footer["time_limit_exceeded"] is False
    assert summary["result"] == "UNKNOWN" and summary["complete"] is False
    # R3 -- the retained artifact is whole lines only, and the dropped tail is declared
    text = "".join(p.read_text(encoding="utf-8") for p in files)
    assert text and text.endswith("\n") and "\n\n" not in text
    # complete byte accounting: every byte read is parsed and retained, or declared dropped
    assert footer["raw_disk_bytes"] == disk
    assert footer["parsed_bytes"] == disk
    assert footer["partial_tail_bytes"] == summary["raw_bytes"] - disk >= 0
    assert footer["parsed_bytes"] + footer["partial_tail_bytes"] == summary["raw_bytes"]
    assert footer["workload_terminated_by_recorder"] is True
    # R5 -- atomic UNKNOWN attestation, no temp residue
    assert not list(tmp_path.glob(".bounded.jsonl.*"))
    # no event is fabricated from bytes that were never retained
    parsed_pids = {r["pid"] for r in records if r["record_type"] == "event"}
    assert parsed_pids and parsed_pids <= {int(p.stem) for p in files}


@LIVE
@pytest.mark.timeout(90)
def test_live_bound_reaps_the_whole_tracer_process_group_and_leaves_no_orphan(tmp_path):
    """R4: the tracer pid and its entire process group are gone, not merely the marker."""
    out = tmp_path / "reap.jsonl"
    marker = "bd-writerec-orphan-" + str(os.getpid())
    started = time.monotonic()
    run = _recorder(tmp_path, out, "--exit-mode", "recorder", "--max-raw-bytes", "4096",
                    command=_hot(tmp_path, marker))
    assert time.monotonic() - started < 25
    assert run.returncode == 2, run.stdout + run.stderr
    footer = _records(out)[-1]
    assert footer["complete"] is False and footer["result"] == "UNKNOWN"
    tracer_pid = footer["tracer_pid"]
    assert isinstance(tracer_pid, int) and tracer_pid > 0
    assert footer["tracer_pgid"] == tracer_pid  # start_new_session makes the tracer the leader
    assert footer["process_group_reaped"] is True
    with pytest.raises(ProcessLookupError):
        os.killpg(footer["tracer_pgid"], 0)
    assert not list(tmp_path.glob(".reap.jsonl.*"))
    ps = ""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ps = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True, check=True).stdout
        if marker not in ps:
            break
        time.sleep(.1)
    assert marker not in ps


@LIVE
@pytest.mark.timeout(90)
def test_live_generous_budget_neither_truncates_nor_terminates_the_workload(tmp_path):
    """R6: non-vacuity control -- an always-UNKNOWN recorder must not pass the bound tests."""
    out = tmp_path / "complete.jsonl"
    payload = tmp_path / "payload"
    finite = ("import os\n"
              "f = open(%r, 'wb')\n"
              "for _ in range(64): os.write(f.fileno(), b'x')\n"
              "os._exit(0)\n") % str(payload)
    budget = 4 << 20
    run = _recorder(tmp_path, out, "--exit-mode", "recorder", "--max-raw-bytes", str(budget),
                    command=[sys.executable, "-c", finite])
    summary = json.loads(run.stdout.splitlines()[0])
    assert run.returncode == 0, run.stdout + run.stderr
    assert summary["raw_limit_exceeded"] is False and summary["time_limit_exceeded"] is False
    assert summary["complete"] is True and summary["result"] == "PASS"
    assert 0 < summary["raw_bytes"] < budget
    footer = _records(out)[-1]
    assert footer["root_exit"] == {"how": "exited", "code": 0}
    assert footer["workload_terminated_by_recorder"] is False
    assert footer["partial_tail_bytes"] == 0
    assert footer["parsed_bytes"] == footer["raw_disk_bytes"] == summary["raw_bytes"]
    assert footer["exit_witness"] == "agree"
    assert footer["process_group_reaped"] is True
    assert footer["successful_mutations"] >= 64
    assert payload.stat().st_size == 64


@LIVE
@pytest.mark.timeout(90)
def test_live_time_limit_is_distinguishable_from_the_raw_byte_limit(tmp_path):
    """R8: the two refusals must not share one flag, and a quiet child must not wedge the drain."""
    out = tmp_path / "timed.jsonl"
    budget = 4 << 20
    started = time.monotonic()
    run = _recorder(tmp_path, out, "--exit-mode", "recorder", "--max-seconds", "1",
                    "--max-raw-bytes", str(budget),
                    command=[sys.executable, "-c", "import time; time.sleep(60)"])
    assert time.monotonic() - started < 25
    assert run.returncode == 2, run.stdout + run.stderr
    summary = json.loads(run.stdout.splitlines()[0])
    assert summary["time_limit_exceeded"] is True and summary["raw_limit_exceeded"] is False
    assert summary["raw_bytes"] < budget
    assert summary["result"] == "UNKNOWN" and summary["complete"] is False
    footer = _records(out)[-1]
    assert footer["workload_terminated_by_recorder"] is True
    assert footer["process_group_reaped"] is True


@LIVE
@pytest.mark.timeout(90)
def test_live_unspawnable_tracer_is_UNKNOWN_and_leaves_no_recorder_temp(tmp_path):
    """The recorder's own scratch state must not survive a tracer that never starts."""
    blocked = tmp_path / "strace-not-executable"
    blocked.write_text("#!/bin/sh\nexit 0\n")
    blocked.chmod(0o644)  # a real file, so the isfile probe passes, but exec must fail
    out = tmp_path / "unspawnable.jsonl"
    run = _recorder(tmp_path, out, "--exit-mode", "recorder", "--strace", str(blocked),
                    command=["/bin/true"])
    assert run.returncode == 2, run.stdout + run.stderr
    assert json.loads(run.stdout.splitlines()[0])["result"] == "UNKNOWN"
    assert not list(tmp_path.glob(".bd-writerec-tracer-stderr.*")), "recorder temp survived"
    assert not list(tmp_path.glob(".unspawnable.jsonl.*"))


@LIVE
@pytest.mark.timeout(90)
def test_live_exit_propagation_fork_per_pid_raw_logs_and_recorder_failure(tmp_path):
    """R7: the child exit is witnessed twice -- parsed terminal record and tracer status."""
    out = tmp_path / "exit.jsonl"
    run = subprocess.run([sys.executable, str(TOOL), "--out", str(out), "--", sys.executable, "-c",
                          "import os; p=os.fork(); os._exit(0 if p else 7)"], cwd=tmp_path, timeout=25)
    assert run.returncode == 0
    records = _records(out)
    footer = records[-1]
    assert footer["root_exit"] == {"how": "exited", "code": 0}
    assert footer["tracer_exit_code"] == footer["root_exit"]["code"] == 0
    assert footer["raw_limit_exceeded"] is False and footer["time_limit_exceeded"] is False
    raw_logs = list(Path(records[0]["raw_log_dir"]).glob("*.strace"))
    assert len(raw_logs) >= 2 and all(p.stat().st_size > 0 for p in raw_logs)
    bad = subprocess.run([sys.executable, str(TOOL), "--out", str(tmp_path / "bad.jsonl"), "--exit-mode", "recorder",
                          "--strace", "/nonexistent/strace", "--", "/bin/true"], cwd=tmp_path)
    assert bad.returncode == 2


def test_cli_selftest_help_and_nonvacuous_trace_argv():
    selftest = subprocess.run([sys.executable, str(TOOL), "--selftest"], capture_output=True, text=True)
    assert selftest.returncode == 0 and selftest.stdout.rstrip().endswith("SELFTEST PASS")
    argv = mod.build_strace_argv("/usr/bin/strace", "/tmp/t", ["/bin/true"])
    assert all(flag in argv for flag in ["-f", "-y", "-s", "0", "-o", "--"])
    assert "-qq" not in argv and "-ff" not in argv
    assert set(mod.TRACE_SET) == set(mod.coverage_document()["policies"])


def test_unpaired_and_eof_unfinished_calls_are_retained_and_UNKNOWN():
    text = "100 <... write resumed>) = 2\n101 write(3</x>, NULL, 4 <unfinished ...>\n"
    events, footer = mod.parse_trace(text, root_pid=100)
    assert len(events) == 2
    assert {e["status"] for e in events} == {"unpaired_resumed", "unfinished"}
    assert footer["unpaired_resumed"] == footer["unfinished_at_eof"] == 1
    assert footer["complete"] is False and footer["result"] == "UNKNOWN"


def test_empty_trace_is_UNKNOWN_and_max_events_truncates_nonvacuously():
    events, footer = mod.parse_trace("", root_pid=100)
    assert events == [] and footer["complete"] is False
    text = (FIXTURES / "families.strace").read_text()
    events, footer = mod.parse_trace(text, root_pid=100, max_events=2)
    assert len(events) == 2 and footer["truncated"] is True and footer["result"] == "UNKNOWN"


def test_root_classifies_but_never_filters_and_read_only_open_is_metadata():
    events, _ = _parse("opens.strace")
    assert len(events) == 11
    assert any(e["in_root"] for e in events)
    outside, _ = mod.parse_trace('100 write(3</outside/x>, NULL, 2) = 2\n100 +++ exited with 0 +++\n',
                                 roots=("/work",), root_pid=100)
    assert len(outside) == 1 and outside[0]["in_root"] is False
    assert events[-1]["operation"] == "metadata" and events[-1]["success"] is True


def test_symlink_content_is_not_resolved_as_a_source_path():
    events, _ = _parse("families.strace")
    symlink = next(e for e in events if e["syscall"] == "symlink" and e["success"])
    symlinkat = next(e for e in events if e["syscall"] == "symlinkat" and e["success"])
    assert (symlink["path"], symlink["target_path"]) == ("/work/sym", "content")
    assert (symlinkat["path"], symlinkat["target_path"]) == ("/dst/sym", "content")


def test_parse_cli_is_atomic_and_rejects_output_inside_a_git_worktree(tmp_path):
    trace = FIXTURES / "opens.strace"
    out = tmp_path / "parsed.jsonl"
    run = subprocess.run([sys.executable, str(TOOL), "--parse", str(trace), "--out", str(out),
                          "--exit-mode", "recorder", "--json"], capture_output=True, text=True)
    assert run.returncode == 0 and json.loads(run.stdout)["result"] == "PASS"
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert records[0]["record_type"] == "run_header" and records[-1]["record_type"] == "run_footer"
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    rejected = subprocess.run([sys.executable, str(TOOL), "--parse", str(trace), "--out", str(REPO / "no.jsonl"),
                               "--exit-mode", "recorder", "--json"], capture_output=True, text=True)
    assert rejected.returncode == 2 and json.loads(rejected.stdout)["result"] == "UNKNOWN"
    assert not (REPO / "no.jsonl").exists()
