from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
SPARE_BOOT_SH = Path("/home/mboyle/bd-persist/harness/bd-spare-boot.sh")
PTY_GUARD_PY = Path("/home/mboyle/bd-persist/harness/bd-pty-guard.py")

BD_GATE_SCOPE = "module"


def test_spare_boot_script_exists_and_executable() -> None:
    assert SPARE_BOOT_SH.exists(), f"Missing script: {SPARE_BOOT_SH}"
    assert os.access(SPARE_BOOT_SH, os.X_OK), f"Not executable: {SPARE_BOOT_SH}"

def test_spare_boot_dry_run_spare3() -> None:
    proc = subprocess.run([str(SPARE_BOOT_SH), "--dry-run", "spare3"], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"Dry-run failed: {proc.stderr}\nStdout: {proc.stdout}"
    stdout = proc.stdout
    assert "toolsOk" in stdout, "Missing toolsOk verification in dry-run output"
    assert ".virtualMachines" in stdout or "summary.guest.ipAddress" in stdout or "DHCP" in stdout, "Missing DHCP / JSON parser verification in dry-run output"
    assert "[POOL-JOIN]" in stdout or "POOL-JOIN" in stdout, "Missing POOL-JOIN confirmation in output"

def test_spare_boot_rejects_forbidden_targets() -> None:
    forbidden = ["10.0.70.1", "10.0.70.95", "test2", "test5", "vc04", "gateway", "10.0.20.1"]
    for target in forbidden:
        proc = subprocess.run([str(SPARE_BOOT_SH), "--dry-run", target], capture_output=True, text=True, timeout=10)
        assert proc.returncode != 0, f"Forbidden target '{target}' was not rejected! Output: {proc.stdout}"
        assert "Security violation" in proc.stderr or "Disallowed" in proc.stderr or "REFUSED" in proc.stderr or ("Security violation" in proc.stdout), f"Expected refusal message for target '{target}'"

def test_spare_boot_json_schema_invariants() -> None:
    content = SPARE_BOOT_SH.read_text(encoding="utf-8")
    assert ".virtualMachines" in content, "Rule 61.3 violation: must target lowercase .virtualMachines[] root"
    assert ".summary.guest.ipAddress" in content, "Rule 61.3 violation: must target .summary.guest.ipAddress"

def test_pty_guard_script_exists_and_executable() -> None:
    assert PTY_GUARD_PY.exists(), f"Missing script: {PTY_GUARD_PY}"
    assert os.access(PTY_GUARD_PY, os.X_OK), f"Not executable: {PTY_GUARD_PY}"

def test_pty_guard_live_kernel_stats() -> None:
    proc = subprocess.run([sys.executable, str(PTY_GUARD_PY), "--once", "--json"], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, f"Live inspection failed: {proc.stderr}\nStdout: {proc.stdout}"
    data = json.loads(proc.stdout)
    assert "local" in data
    assert "nr" in data["local"]
    assert "max" in data["local"]
    assert "utilization_pct" in data["local"]
    assert data["local"]["nr"] >= 0
    assert data["local"]["max"] > 0
    assert data["status"] in ("OK", "WARNING")

def test_pty_guard_warning_trigger_at_80_pct() -> None:
    proc = subprocess.run([sys.executable, str(PTY_GUARD_PY), "--once", "--dry-run", "--mock-nr", "3300", "--mock-max", "4096"], capture_output=True, text=True, timeout=10)
    output = proc.stdout + proc.stderr
    assert "PTY-WARN" in output or "WARNING" in output, f"Expected 80% warning in output: {output}"

def test_pty_guard_backoff_trigger_at_85_pct() -> None:
    proc = subprocess.run([sys.executable, str(PTY_GUARD_PY), "--once", "--dry-run", "--mock-nr", "3500", "--mock-max", "4096"], capture_output=True, text=True, timeout=10)
    assert proc.returncode != 0, f"Expected non-zero exit code for critical PTY saturation, got {proc.returncode}"
    output = proc.stdout + proc.stderr
    assert "PTY-CRITICAL" in output or "CRITICAL" in output or "shed" in output.lower(), f"Expected critical backoff/shedding trigger in output: {output}"

def test_workload_segregation_invariants() -> None:
    proc = subprocess.run([sys.executable, str(PTY_GUARD_PY), "--check-workload-routing"], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, f"Workload routing check failed: {proc.stderr}\nStdout: {proc.stdout}"
    output = proc.stdout
    assert "10.0.10.137" in output, "Expected Node .137 in workload segregation output"
    assert "10.0.70.82" in output, "Expected Node .82 in workload segregation output"

def test_static_analysis_ruff_and_mypy() -> None:
    ruff_res = subprocess.run(["ruff", "check", str(PTY_GUARD_PY)], capture_output=True, text=True)
    assert ruff_res.returncode == 0, f"ruff check failed:\n{ruff_res.stdout}\n{ruff_res.stderr}"
    mypy_res = subprocess.run(["mypy", "--strict", str(PTY_GUARD_PY)], capture_output=True, text=True)
    assert mypy_res.returncode == 0, f"mypy check failed:\n{mypy_res.stdout}\n{mypy_res.stderr}"
