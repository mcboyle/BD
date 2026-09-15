"""Row 803: unreadable lint input cannot mint a clean or policy verdict."""
from __future__ import annotations

import json
import os
import pwd
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain/bin/bd-tool-lint"
MODES = [
    pytest.param((), id="text"),
    pytest.param(("--json",), id="json"),
    pytest.param(("--corpus-debt",), id="debt"),
    pytest.param(("--gate",), id="gate"),
    pytest.param(("--ratchet",), id="ratchet"),
]


@pytest.fixture
def corpus():
    path = Path(tempfile.mkdtemp(prefix="row803-", dir="/tmp"))
    try:
        path.chmod(0o755)
        for name in ("bd-alpha", "bd-beta", "bd-gamma"):
            tool = path / name
            tool.write_text("#!/usr/bin/env python3\nif __name__ == '__main__': pass\n")
            tool.chmod(0o755)
        yield path
    finally:
        shutil.rmtree(path)


def _as_unprivileged():
    if os.geteuid():
        return None
    account = pwd.getpwnam("nobody")
    return lambda: (os.setgid(account.pw_gid), os.setuid(account.pw_uid))


def invoke(corpus, mode):
    return subprocess.run(
        [sys.executable, str(TOOL), "--bin", str(corpus), "--no-runtime", *mode],
        text=True, capture_output=True, timeout=30, preexec_fn=_as_unprivileged(),
    )


@pytest.mark.parametrize("mode", MODES)
def test_unreadable_tool_is_cannot_evaluate_in_every_output_mode(corpus, mode):
    bad = corpus / "bd-gamma"
    bad.chmod(0)
    result = invoke(corpus, mode)
    assert result.returncode == 2, (mode, result.stdout, result.stderr)
    assert "CANNOT-EVALUATE" in result.stderr
    assert "reason=UNREADABLE" in result.stderr
    assert "bd-gamma" in result.stderr and "Permission denied" in result.stderr
    if "--json" in mode:
        payload = json.loads(result.stdout)
        assert payload["tools"] == 3 and payload["read_tools"] == 2
        assert len(payload["unreadable"]) == 1 and payload["clean"] is False
    elif "--corpus-debt" not in mode:
        assert "linted 2 of 3 discovered tools (1 unreadable: bd-gamma)" in result.stdout


def test_restored_tool_is_measured_clean_in_json_mode(corpus):
    result = invoke(corpus, ("--json",))
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["tools"] == payload["read_tools"] == 3
    assert payload["unreadable"] == [] and payload["clean"] is True


def test_transform_control_loads_same_lint_subject():
    loaded = runpy.run_path(str(TOOL))
    assert Path(loaded["run"].__code__.co_filename).resolve() == TOOL.resolve()
    assert callable(loaded["main"])
