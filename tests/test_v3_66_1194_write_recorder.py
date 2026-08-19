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
    assert len(opens) == 9
    assert [e["operation"] for e in opens] == [
        "open_mutation", "open_mutation", "open_mutation", "open_mutation",
        "open_mutation", "open_mutation", "open_mutation", "open_mutation", "metadata",
    ]
    assert [e["success"] for e in opens] == [True, False, True, False, True, False, True, False, True]
    assert opens[0]["flags"] == ["O_WRONLY", "O_CREAT", "O_TRUNC"]
    assert opens[0]["operation_count"] == 1  # schema pin: one syscall is one operation
    assert footer["successful_mutations"] == 4


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
    assert (rename["path"], rename["target_path"]) == ("/work/a>b", "/work/c>d")
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


@pytest.mark.skipif(not _live_capable(), reason="live strace capability unavailable")
def test_live_raw_bound_reaps_tracer_and_child_and_atomically_attests_UNKNOWN(tmp_path):
    out = tmp_path / "evidence.jsonl"
    marker = "bd-writerec-child-" + str(os.getpid())
    code = "import os; marker=%r; p='/tmp/'+marker; f=open(p,'wb');\nwhile True: os.write(f.fileno(),b'x')" % marker
    started = time.monotonic()
    run = subprocess.run([sys.executable, str(TOOL), "--out", str(out), "--exit-mode", "recorder",
                          "--max-raw-bytes", "4096", "--json", "--", sys.executable, "-c", code],
                         cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert time.monotonic() - started < 15
    assert run.returncode == 2, run.stdout + run.stderr
    summary = json.loads(run.stdout.splitlines()[0])
    assert summary["result"] == "UNKNOWN" and summary["raw_limit_exceeded"] is True
    assert summary["raw_bytes"] <= 8192
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert records[-1]["record_type"] == "run_footer" and records[-1]["complete"] is False
    assert not list(tmp_path.glob(".evidence.jsonl.*"))
    time.sleep(.1)
    ps = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True, check=True).stdout
    assert marker not in ps


@pytest.mark.skipif(not _live_capable(), reason="live strace capability unavailable")
def test_live_exit_propagation_fork_per_pid_raw_logs_and_recorder_failure(tmp_path):
    out = tmp_path / "exit.jsonl"
    run = subprocess.run([sys.executable, str(TOOL), "--out", str(out), "--", sys.executable, "-c",
                          "import os; p=os.fork(); os._exit(0 if p else 7)"], cwd=tmp_path, timeout=15)
    assert run.returncode == 0
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert records[-1]["root_exit"] == {"how": "exited", "code": 0}
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
