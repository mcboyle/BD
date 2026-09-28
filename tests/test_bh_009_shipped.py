"""BH-009: unified CLI contract between harness/bd-shipped and toolchain/bin/bd-shipped."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH_009_SHIPPED_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def candidate_dir() -> Path:
    p = Path(CANDIDATE)
    assert p.is_dir(), f"candidate directory does not exist: {p}"
    toolchain_tool = p / "toolchain-bd-shipped"
    harness_tool = p / "bd-shipped"
    assert toolchain_tool.is_file() and os.access(toolchain_tool, os.X_OK), (
        f"toolchain-bd-shipped not executable: {toolchain_tool}"
    )
    assert harness_tool.is_file() and os.access(harness_tool, os.X_OK), (
        f"bd-shipped not executable: {harness_tool}"
    )
    return p


def test_missing_patch_dir_exits_unknown_4_toolchain(
    candidate_dir: Path, tmp_path: Path
) -> None:
    tool = candidate_dir / "toolchain-bd-shipped"
    missing = tmp_path / "no-such-dir"
    res = subprocess.run(
        [sys.executable, str(tool), "--patch", str(missing)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 4, f"expected rc 4, got {res.returncode}; {res.stderr}"
    assert f"UNKNOWN: --patch '{missing}' is not a directory" in res.stderr


def test_missing_patch_dir_exits_unknown_4_harness_wrapper(
    candidate_dir: Path, tmp_path: Path
) -> None:
    tool = candidate_dir / "bd-shipped"
    toolchain_tool = candidate_dir / "toolchain-bd-shipped"
    missing = tmp_path / "no-such-dir"
    env = os.environ.copy()
    env["BD_SHIPPED_TOOL"] = str(toolchain_tool)
    res = subprocess.run(
        [str(tool), "--patch", str(missing)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 4, f"expected rc 4, got {res.returncode}; {res.stderr}"
    assert f"UNKNOWN: --patch '{missing}' is not a directory" in res.stderr


def test_positive_control_shipped_patch(candidate_dir: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    collected = tmp_path / "collected"
    repo.mkdir()
    collected.mkdir()

    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "h009@test.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "H009"], check=True)

    (repo / "f.txt").write_text("base\n", encoding="ascii")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    base_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    (repo / "f.txt").write_text("candidate\n", encoding="ascii")
    diff_out = subprocess.run(
        ["git", "-C", str(repo), "diff", "--binary"],
        capture_output=True,
        check=True,
    ).stdout
    (collected / "patch.diff").write_bytes(diff_out)
    (collected / "transcript.txt").write_text(f"BASE={base_sha}\n", encoding="ascii")

    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "--", "f.txt"], check=True
    )
    (repo / "f.txt").write_text("candidate\n", encoding="ascii")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "landed"], check=True)

    tool = candidate_dir / "toolchain-bd-shipped"
    res = subprocess.run(
        [
            sys.executable,
            str(tool),
            "--repo",
            str(repo),
            "--patch",
            str(collected),
            "--against",
            "HEAD",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, (
        f"expected rc 0, got {res.returncode}; {res.stderr}\n{res.stdout}"
    )
    assert "RESULT     SHIPPED" in res.stdout
    assert "SHIPPED: 1 of 1 substantive candidate blobs equal" in res.stdout


def test_negative_control_not_shipped_patch(
    candidate_dir: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    collected = tmp_path / "collected"
    repo.mkdir()
    collected.mkdir()

    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "h009@test.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "H009"], check=True)

    (repo / "f.txt").write_text("base\n", encoding="ascii")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    base_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    (repo / "f.txt").write_text("candidate\n", encoding="ascii")
    diff_out = subprocess.run(
        ["git", "-C", str(repo), "diff", "--binary"],
        capture_output=True,
        check=True,
    ).stdout
    (collected / "patch.diff").write_bytes(diff_out)
    (collected / "transcript.txt").write_text(f"BASE={base_sha}\n", encoding="ascii")

    (repo / "f.txt").write_text("candidate\n", encoding="ascii")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "landed"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", base_sha, "--", "f.txt"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "reverted"], check=True)

    tool = candidate_dir / "toolchain-bd-shipped"
    res = subprocess.run(
        [
            sys.executable,
            str(tool),
            "--repo",
            str(repo),
            "--patch",
            str(collected),
            "--against",
            "HEAD",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 3, (
        f"expected rc 3, got {res.returncode}; {res.stderr}\n{res.stdout}"
    )
    assert "RESULT     NOT SHIPPED" in res.stdout
    assert "NOT SHIPPED: 0 of 1 substantive candidate blobs equal" in res.stdout
    assert "NOT SHIPPED [PATCH AHEAD (main remains at base)]: f.txt" in res.stdout
