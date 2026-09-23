"""Tests for Row 883: Flaky Test Quarantine and Auto-Retry Statistical Classifier.

Acceptance criteria:
1. Flaky test correctly classified after single retry pass.
2. Flake traceback recorded to diagnostic ledger in bd-persist/flakes/.
3. Persistent failures recorded to diagnostic ledger and still exit nonzero.
4. Terminal output (stdout/stderr) preserved and emitted on failure (no suppression).
5. Zero sleep backoff in test execution retry loop.
"""

from __future__ import annotations

BD_GATE_SCOPE = "repo-wide"

import json
import subprocess
import sys
import time
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path

TOOLCHAIN_BIN = (
    Path(__file__).resolve().parent.parent / "toolchain" / "bin" / "bd-flake-classifier"
)


def load_classifier_module():
    loader = SourceFileLoader("bd_flake_classifier", str(TOOLCHAIN_BIN))
    mod = types.ModuleType(loader.name)
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod


class TestFlakeClassifierAcceptance:
    """Acceptance test suite for bd-flake-classifier."""

    def test_behavioural_red_unwrapped_flake_fails_build_and_writes_no_ledger(
        self, tmp_path
    ):
        """Negative control: without bd-flake-classifier, a flaky test command
        fails the build with non-zero exit and leaves zero ledger records.
        """
        flake_script = tmp_path / "flaky_cmd.sh"
        state_file = tmp_path / "state.txt"
        flake_script.write_text(
            f"""#!/bin/bash
            if [ ! -f "{state_file}" ]; then
                touch "{state_file}"
                echo "SocketTimeout: first attempt failed" >&2
                exit 1
            fi
            echo "Second attempt passed"
            exit 0
            """
        )
        flake_script.chmod(0o755)

        flakes_dir = tmp_path / "flakes"
        # Run directly without flake classifier
        res = subprocess.run(
            [str(flake_script)], capture_output=True, text=True, check=False
        )
        assert res.returncode == 1  # Unwrapped flaky test fails build
        assert not (flakes_dir / "ledger.jsonl").exists()

    def test_flaky_test_classified_after_single_retry_pass(self, tmp_path):
        """(1) Flaky test correctly classified after single retry pass (fail once, pass on retry)."""
        mod = load_classifier_module()
        classifier = mod.FlakeClassifier(flakes_dir=str(tmp_path / "flakes"))

        run_count = 0

        def flaky_runner():
            nonlocal run_count
            run_count += 1
            if run_count == 1:
                return (
                    False,
                    "ConnectionResetError: [Errno 104] Connection reset by peer",
                )
            return True, ""

        record = classifier.classify_test(
            test_id="tests/test_network.py::test_socket_timeout",
            test_func=flaky_runner,
            max_retries=1,
        )

        assert record.status == "flake"
        assert record.attempts == 2
        assert record.is_flaky is True
        assert record.passed is True
        assert "ConnectionResetError" in record.traceback
        assert run_count == 2

    def test_flake_traceback_recorded_to_diagnostic_ledger(self, tmp_path):
        """(2) Flake traceback recorded to diagnostic ledger in bd-persist/flakes/."""
        mod = load_classifier_module()
        flakes_dir = tmp_path / "flakes"
        classifier = mod.FlakeClassifier(flakes_dir=str(flakes_dir))

        traceback_msg = (
            "Traceback (most recent call last):\n"
            "  File 'test_socket.py', line 42, in test_stream\n"
            "TimeoutError: socket read timed out after 5000ms"
        )

        record = classifier.record_flake(
            test_id="tests/test_stream.py::test_socket_flake",
            traceback_text=traceback_msg,
            attempt=1,
        )

        ledger_file = flakes_dir / "ledger.jsonl"
        assert ledger_file.exists()

        with open(ledger_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        assert len(lines) == 1
        entry = lines[0]
        assert entry["test_id"] == "tests/test_stream.py::test_socket_flake"
        assert entry["attempt"] == 1
        assert entry["status"] == "flake"
        assert "TimeoutError" in entry["traceback"]

        detail_log = Path(record.log_path)
        assert detail_log.exists()
        assert "TimeoutError: socket read timed out" in detail_log.read_text(
            encoding="utf-8"
        )

    def test_persistent_failure_recorded_to_diagnostic_ledger_and_exits_nonzero(
        self, tmp_path
    ):
        """(3) Persistent failures (fail on first run and fail on retry) must record to
        diagnostic ledger with status 'fail' and return non-zero exit code (E4 fix).
        """
        mod = load_classifier_module()
        flakes_dir = tmp_path / "flakes"
        classifier = mod.FlakeClassifier(flakes_dir=str(flakes_dir))

        run_count = 0

        def persistent_fail_runner():
            nonlocal run_count
            run_count += 1
            return False, f"AssertionError: real failure on attempt {run_count}"

        record = classifier.classify_test(
            test_id="tests/test_broken.py::test_real_bug",
            test_func=persistent_fail_runner,
            max_retries=1,
        )

        assert record.status == "fail"
        assert record.attempts == 2
        assert record.is_flaky is False
        assert record.passed is False

        # Verify persistent failure was recorded to ledger (E4 requirement)
        ledger_file = flakes_dir / "ledger.jsonl"
        assert ledger_file.exists(), "Persistent failures must be recorded to ledger.jsonl"
        with open(ledger_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1
        assert lines[0]["test_id"] == "tests/test_broken.py::test_real_bug"
        assert lines[0]["status"] == "fail"
        assert "AssertionError" in lines[0]["traceback"]
        assert Path(record.log_path).exists()

    def test_failure_traceback_not_suppressed_on_persistent_failure(
        self, tmp_path
    ):
        """(4) Terminal error output must NOT be suppressed on persistent failure (E2 fix)."""
        fail_script = tmp_path / "fail_cmd.sh"
        fail_script.write_text(
            """#!/bin/bash
            echo "STDOUT_DIAGNOSTIC_MARKER_99"
            echo "STDERR_CRITICAL_FAILURE_TRACEBACK_88" >&2
            exit 1
            """
        )
        fail_script.chmod(0o755)

        flakes_dir = tmp_path / "flakes"
        cmd = [
            sys.executable,
            str(TOOLCHAIN_BIN),
            "--flakes-dir",
            str(flakes_dir),
            "--retries",
            "1",
            "--test-id",
            "tests/test_fail.py::test_diagnostic_visibility",
            "--",
            str(fail_script),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert res.returncode != 0
        # Both stdout and stderr MUST be preserved in terminal output
        assert (
            "STDOUT_DIAGNOSTIC_MARKER_99" in res.stdout
            or "STDOUT_DIAGNOSTIC_MARKER_99" in res.stderr
        ), f"stdout suppressed: stdout={res.stdout!r}, stderr={res.stderr!r}"
        assert (
            "STDERR_CRITICAL_FAILURE_TRACEBACK_88" in res.stderr
            or "STDERR_CRITICAL_FAILURE_TRACEBACK_88" in res.stdout
        ), f"stderr suppressed: stdout={res.stdout!r}, stderr={res.stderr!r}"

    def test_retry_has_no_sleep_backoff(self, tmp_path, monkeypatch):
        """(5) Test execution retry loop must not inject sleep backoff (E3 fix)."""
        mod = load_classifier_module()
        sleep_calls: list[float] = []
        monkeypatch.setattr(mod.time, "sleep", lambda s: sleep_calls.append(s))
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        fail_script = tmp_path / "quick_fail.sh"
        fail_script.write_text("#!/bin/bash\nexit 1\n")
        fail_script.chmod(0o755)

        flakes_dir = tmp_path / "flakes"
        classifier = mod.FlakeClassifier(flakes_dir=str(flakes_dir))

        # 1. run_command_with_retry executes without sleep backoff
        rc, record = classifier.run_command_with_retry(
            cmd=[str(fail_script)],
            test_id="tests/test_perf.py::test_no_sleep",
            max_retries=1,
        )
        assert rc != 0
        assert record.status == "fail"
        assert record.attempts == 2

        # 2. classify_test executes without sleep backoff
        def failing_test():
            return False, "AssertionError: immediate failure"

        rec2 = classifier.classify_test(
            test_id="tests/test_perf.py::test_no_sleep_func",
            test_func=failing_test,
            max_retries=1,
        )
        assert rec2.status == "fail"
        assert rec2.attempts == 2

        # Deterministic invariant: zero calls to time.sleep on retry path
        assert sleep_calls == [], f"Retry loop injected sleep backoff: {sleep_calls}"

    def test_cli_execution_flake_keeps_build_green(self, tmp_path):
        """CLI invocation: Flaky test that passes on retry exits 0 (keeps build green)."""
        flake_script = tmp_path / "flaky_cmd.sh"
        state_file = tmp_path / "state.txt"
        flake_script.write_text(
            f"""#!/bin/bash
            if [ ! -f "{state_file}" ]; then
                touch "{state_file}"
                echo "SocketTimeout: first attempt failed" >&2
                exit 1
            fi
            echo "Second attempt passed"
            exit 0
            """
        )
        flake_script.chmod(0o755)

        flakes_dir = tmp_path / "flakes"
        cmd = [
            sys.executable,
            str(TOOLCHAIN_BIN),
            "--flakes-dir",
            str(flakes_dir),
            "--retries",
            "1",
            "--test-id",
            "tests/test_flaky.py::test_cli",
            "--",
            str(flake_script),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert res.returncode == 0
        assert "FLAKE" in res.stdout or "FLAKE" in res.stderr

        # Check ledger was written
        ledger = flakes_dir / "ledger.jsonl"
        assert ledger.exists()
        assert "SocketTimeout" in ledger.read_text(encoding="utf-8")


def test_h685_cli_emits_current_nodeid_before_child_exits(tmp_path):
    import os
    import select
    import signal
    import socket

    nodeid = 'tests/test_probe.py::test_hanging_request'
    with socket.socket() as ready:
        ready.bind(('127.0.0.1', 0))
        ready.listen(1)
        ready.settimeout(10)
        child = (
            'import socket,sys; '
            f'sys.stdout.write({nodeid!r}); sys.stdout.flush(); '
            f'notice=socket.create_connection({ready.getsockname()!r}, timeout=10); '
            'notice.sendall(b"ready\\n"); notice.close(); '
            'sys.stdin.readline(); sys.stderr.write("controlled failure\\n"); sys.exit(7)'
        )
        proc = subprocess.Popen(
            [sys.executable, str(TOOLCHAIN_BIN), '--flakes-dir', str(tmp_path / 'flakes'),
             '--retries', '0', '--', sys.executable, '-u', '-c', child],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        observed = b''
        try:
            connection, _ = ready.accept()
            with connection:
                connection.settimeout(10)
                with connection.makefile('rb') as notice:
                    assert notice.readline() == b'ready\n', 'child never reached test body'
            assert proc.poll() is None, 'precondition: child must still be running'
            assert proc.stdout is not None
            readable, _, _ = select.select([proc.stdout], [], [], 2)
            if readable:
                observed = os.read(proc.stdout.fileno(), 4096)
            assert nodeid.encode() in observed, 'running nodeid is buffered until the child exits'
        finally:
            try:
                rest, errors = proc.communicate(input=b'finish\n', timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate(timeout=10)
                raise
    assert proc.returncode == 7, 'streaming must preserve failure status'
    assert (observed + rest).count(nodeid.encode()) == 1, 'streamed nodeid was printed twice'
    assert b'controlled failure' in errors
