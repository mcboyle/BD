"""Row 337: ``bd-tool-smoke --run --gate`` must fail on UNKNOWN.

The runtime smoke's timeout means it did not measure whether a tool can run.
That third state is deliberately distinct from both a crash and a completed,
clean invocation.
"""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import subprocess
import sys


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-tool-smoke"

# Measured on test5 at dcd8201: 100 clean interpreter boots had a maximum
# 0.049318s runtime.  ceil(10 * 0.049318s) = 1s.  The 10x measured-startup
# allowance leaves the smallest whole-second budget accepted by the CLI.
_TOOL_TIMEOUT_SECONDS = 1


def _write_tool(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _smoke(
    bindir: Path,
    work: Path,
    *,
    json_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["BD_WORK"] = str(work)
    command = [
        sys.executable,
        str(_TOOL),
        "--bin",
        str(bindir),
        "--run",
        "--gate",
        "--timeout",
        str(_TOOL_TIMEOUT_SECONDS),
    ]
    if json_output:
        command.append("--json")
    return subprocess.run(
        command,
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
    )


def test_timeout_is_unknown_and_fails_gate_while_clean_tool_stays_clean(
    tmp_path: Path,
) -> None:
    """A real hang fires the measured bound; completed work remains CLEAN."""
    bindir = tmp_path / "bin"
    work = tmp_path / "work"
    bindir.mkdir()
    work.mkdir()
    _write_tool(bindir / "bd-clean", "print('completed cleanly')\n")

    clean = _smoke(bindir, work)
    clean_output = clean.stdout + clean.stderr
    assert clean.returncode == 0, clean_output
    assert "CLEAN" in clean_output, clean_output
    assert "DID NOT COMPLETE" not in clean_output, clean_output

    # Negative bound control: this fixture can never complete by itself.  The
    # 1s derived timeout must still kill it and publish the distinct UNKNOWN.
    _write_tool(
        bindir / "bd-genuinely-hung",
        "import threading\nthreading.Event().wait()\n",
    )
    unknown = _smoke(bindir, work)
    unknown_output = unknown.stdout + unknown.stderr
    assert unknown.returncode != 0, (
        "a timed-out tool is UNKNOWN, so --run --gate must be nonzero:\n"
        + unknown_output
    )
    assert "DID NOT COMPLETE" in unknown_output, unknown_output
    assert "UNKNOWN" in unknown_output, unknown_output
    assert "bd-genuinely-hung" in unknown_output, unknown_output
    assert "CLEAN" not in unknown_output, (
        "the gate collapsed UNKNOWN into CLEAN:\n" + unknown_output
    )


def test_json_timeout_is_unknown_and_fails_gate(tmp_path: Path) -> None:
    """The structured-output return path must fail on the same real hang."""
    bindir = tmp_path / "bin"
    work = tmp_path / "work"
    bindir.mkdir()
    work.mkdir()
    _write_tool(
        bindir / "bd-genuinely-hung-json",
        "import threading\nthreading.Event().wait()\n",
    )

    unknown = _smoke(bindir, work, json_output=True)
    output = unknown.stdout + unknown.stderr
    assert unknown.returncode != 0, output
    assert '"did_not_complete": {' in output, output
    assert '"bd-genuinely-hung-json": "did not complete in 1s"' in output, output
    assert "UNKNOWN fails --gate" in output, output


def test_tool_smoke_transform_control_loads_without_judging_timeout() -> None:
    """Mutation transform control: loading the tool makes no gate verdict."""
    namespace = runpy.run_path(str(_TOOL))
    assert callable(namespace["cmd_scan"])
