"""Opt-in native Claude M24 regression and protected real append controls."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_W2_M24_CANDIDATE", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_W2_M24") != "1" or not CANDIDATE,
    reason="M24 candidate opt-in required",
)


@pytest.fixture(scope="module")
def native_result(tmp_path_factory):
    candidate = Path(CANDIDATE)
    assert candidate.is_absolute() and candidate.is_dir(), "M24 supplied candidate absent"
    scratch = tmp_path_factory.mktemp("m24-plugin") / "candidate"
    scratch.mkdir()
    for name in (".claude-plugin", "hooks", "types"):
        shutil.copytree(candidate / name, scratch / name)
    cli = os.environ.get("BD_M24_CLAUDE", "claude")
    validated = subprocess.run(
        [cli, "plugin", "validate", str(scratch)], capture_output=True, text=True, timeout=45, check=False
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr
    tested = subprocess.run(
        [cli, "plugin", "test", str(scratch)], capture_output=True, text=True, timeout=120, check=False
    )
    return tested


def test_native_behavior_and_existing_controls(native_result):
    stdout = native_result.stdout + native_result.stderr
    assert native_result.returncode == 0, stdout
    for diagnostic in (
        "M24 positive PM unarmed", "M24 positive PM CronCreate", "M24 positive QUESTION",
        "M24 negative driven", "M24 positive sentinel", "M24 OFF", "M24 warn",
        "M24 throwing append", "M24 WAIT armed", "M24 unrelated cron", "M24 subagent",
        "M24 MCP bd-say QUESTION", "M24 MCP bd-say NEED", "M24 MCP bd-ask QUESTION",
        "M24 TaskStop is not", "one-shot cannot satisfy SESSION",
    ):
        assert diagnostic in stdout, f"missing native case: {diagnostic}"
    assert re.search(r"\b0 fail(?:ed)?\b", stdout), stdout


def test_real_append_positive_and_wrong_path_negative(tmp_path):
    source = (Path(CANDIDATE) / "hooks/m24.ts").read_text()
    seams = re.findall(r"const APPEND = String.raw`([^`]+)`", source)
    assert len(seams) == 1, "M24 real append seam missing"
    script = seams[0]
    directory = tmp_path / "append"
    directory.mkdir()
    log = directory / "m24-shadow.tsv"
    row = "2026-10-03T14:00:00Z\tfixture-seat\tpm\tMISSING:SESSION"
    positive = subprocess.run(
        ["/bin/sh", "-c", script, "bd-m24", str(log), row, str(directory)],
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert positive.returncode == 0, positive.stderr
    assert log.read_bytes() == (row + "\n").encode()
    negative = subprocess.run(
        ["/bin/sh", "-c", script, "bd-m24", str(directory / "wrong.tsv"), row, str(directory)],
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert negative.returncode == 64
    assert "m24: bad path" in negative.stderr
    assert not (directory / "wrong.tsv").exists()
    assert log.read_bytes() == (row + "\n").encode()



def _append(log, directory, row, cwd=None, env=None):
    source = (Path(CANDIDATE) / "hooks/m24.ts").read_text()
    script = re.findall(r"const APPEND = String.raw`([^`]+)`", source)[0]
    return subprocess.run(
        ["/bin/sh", "-c", script, "bd-m24", str(log), row, str(directory)],
        capture_output=True, text=True, timeout=5, check=False, cwd=cwd, env=env,
    )


ROW = "2026-10-04T02:00:00Z\tfixture-seat\tpm\tMISSING:WAIT"


def test_real_append_keeps_rows(tmp_path):
    directory = tmp_path.resolve()
    log = directory / "m24-shadow.tsv"
    assert _append(log, directory, ROW).returncode == 0
    second = _append(log, directory, ROW)
    assert second.returncode == 0, second.stderr
    assert log.read_bytes() == ((ROW + "\n") * 2).encode()


def test_real_append_refuses_symlinked_log(tmp_path):
    directory = tmp_path.resolve() / "append"
    directory.mkdir()
    victim = tmp_path.resolve() / "victim.txt"
    victim.write_bytes(b"KEEP\n")
    log = directory / "m24-shadow.tsv"
    log.symlink_to(victim)
    result = _append(log, directory, ROW)
    assert result.returncode == 68
    assert "m24: unsafe log" in result.stderr
    assert victim.read_bytes() == b"KEEP\n"


def test_real_append_refuses_symlinked_directory(tmp_path):
    target = tmp_path.resolve() / "target"
    target.mkdir()
    directory = tmp_path.resolve() / "link"
    directory.symlink_to(target)
    result = _append(directory / "m24-shadow.tsv", directory, ROW)
    assert result.returncode == 65
    assert "m24: redirected directory" in result.stderr
    assert list(target.iterdir()) == []


def test_real_append_refuses_relative_directory(tmp_path):
    (tmp_path / "append").mkdir()
    result = _append("append/m24-shadow.tsv", "append", ROW, cwd=tmp_path)
    assert result.returncode == 65
    assert "m24: bad directory" in result.stderr
    assert list((tmp_path / "append").iterdir()) == []


def test_real_append_refuses_network_filesystem(tmp_path):
    shims = tmp_path / "shims"
    shims.mkdir()
    stat = shims / "stat"
    stat.write_text("#!/bin/sh\nprintf 'nfs\\n'\n")
    stat.chmod(0o755)
    directory = tmp_path.resolve() / "append"
    directory.mkdir()
    env = dict(os.environ, PATH=f"{shims}:{os.environ['PATH']}")
    result = _append(directory / "m24-shadow.tsv", directory, ROW, env=env)
    assert result.returncode == 67
    assert "m24: network filesystem" in result.stderr
    assert list(directory.iterdir()) == []


def test_wiring_preserves_all_base_hooks():
    candidate = Path(CANDIDATE)
    before = (candidate / "hooks/register.tsx.pre").read_text()
    after = (candidate / "hooks/register.tsx").read_text()
    assert after.count("import { m24 } from './m24'\n") == 1
    assert after.count("  m24(on, options)\n") == 1
    assert after.replace("import { m24 } from './m24'\n", "", 1).replace("  m24(on, options)\n", "", 1) == before
    types_before = (candidate / "types/index.d.ts.pre").read_text()
    types_after = (candidate / "types/index.d.ts").read_text()
    assert types_after.replace("; monitorErrors: number", "", 1) == types_before
