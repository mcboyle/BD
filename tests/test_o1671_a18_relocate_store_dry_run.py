import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
TOOL = Path(__file__).resolve().parents[1] / "tools" / "relocate_capture_store.py"
CAPTURE_DIRS = ("captures", "offline_out", "offline_captures")


def store_fixture(root, existing):
    old = root / "old"
    for name in CAPTURE_DIRS + ("templates/drafts", "templates/review_candidates"):
        directory = old / name
        directory.mkdir(parents=True)
        (directory / "payload.bin").write_bytes(name.encode())
    new = root / "missing-parent" / "nested" / "store"
    if existing:
        (new / "captures").mkdir(parents=True)
        (new / "captures" / "previous.bin").write_bytes(b"existing capture")
    return old, new


def snapshot(root):
    paths = [root, *root.rglob("*")]
    return {str(path.relative_to(root)): (
        None if path.is_dir() else path.read_bytes(), path.stat().st_mtime_ns
    ) for path in paths}


def invoke(old, new, dry_run):
    command = [sys.executable, "-B", str(TOOL), "--from", str(old), "--to", str(new)]
    if dry_run:
        command.append("--dry-run")
    return subprocess.run(command, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("existing", [False, True])
def test_cli_dry_run_preserves_entire_fixture(tmp_path, existing):
    old, new = store_fixture(tmp_path, existing)
    before = snapshot(tmp_path)
    assert new.exists() == existing and all((old / name / "payload.bin").is_file() for name in CAPTURE_DIRS)
    result = invoke(old, new, dry_run=True)
    assert result.returncode == 0, result.stderr
    assert snapshot(tmp_path) == before, "A18 DRY-RUN mutated filesystem while planning relocation"
    assert result.stdout.count("  move   ") == 3
    assert all(str(new / name) in result.stdout for name in CAPTURE_DIRS)
    if not existing:
        assert f"mkdir {new}" in result.stdout


@pytest.mark.parametrize("existing", [False, True])
def test_cli_real_run_creates_root_and_moves_captures(tmp_path, existing):
    old, new = store_fixture(tmp_path, existing)
    template_before = snapshot(old / "templates")
    assert new.exists() == existing
    result = invoke(old, new, dry_run=False)
    assert result.returncode == 0, result.stderr
    assert new.is_dir(), "A18 POSITIVE real relocation must create its destination"
    for name in CAPTURE_DIRS:
        assert (new / name / "payload.bin").read_bytes() == name.encode()
        assert not (old / name).exists()
    assert snapshot(old / "templates") == template_before
    if existing:
        assert (new / "captures" / "previous.bin").read_bytes() == b"existing capture"
