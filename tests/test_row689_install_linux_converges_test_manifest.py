"""Row 689: the fresh Linux installer converges the test manifest."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_INSTALL_LINUX = _REPO / "install_linux.sh"
_START_ANCHOR = "# core requirements"
_END_ANCHOR = "# ── Playwright browsers"

_FAKE_PYTHON = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["ROW689_PYTHON_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")
if args[:4] == ["-m", "pip", "install", "-r"]:
    manifest = Path(args[4])
    if not manifest.is_file():
        raise SystemExit(24)
    if os.environ.get("ROW689_FAIL_MANIFEST") == manifest.name:
        raise SystemExit(23)
raise SystemExit(0)
'''


def _build_requirements_probe(path: Path) -> None:
    source = _INSTALL_LINUX.read_text(encoding="utf-8")
    assert source.count(_START_ANCHOR) == 1, (
        "UNKNOWN: install_linux.sh's requirements start anchor is not unique"
    )
    assert source.count(_END_ANCHOR) == 1, (
        "UNKNOWN: install_linux.sh's requirements end anchor is not unique"
    )
    start = source.index(_START_ANCHOR)
    end = source.index(_END_ANCHOR)
    assert 0 <= start < end, "UNKNOWN: the requirements tier is empty or reordered"
    body = source[start:end]
    probe = (
        "#!/usr/bin/env bash\n"
        "set -u\n"
        "set -o pipefail\n"
        'INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        'VPYTHON="$INSTALL_DIR/venv/bin/python"\n'
        f"{body}\n"
        "exit 0\n"
    )
    path.write_text(probe, encoding="utf-8")
    path.chmod(0o755)
    parsed = subprocess.run(
        ["bash", "-n", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert parsed.returncode == 0, (
        "UNKNOWN: generated requirements probe does not parse: " + parsed.stderr
    )


def _run_fresh_install(
    tmp_path: Path, *, include_test_manifest: bool, fail_manifest: str = ""
) -> tuple[Path, subprocess.CompletedProcess[str], list[list[str]]]:
    fresh = tmp_path / "fresh-clone"
    python = fresh / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(_FAKE_PYTHON, encoding="utf-8")
    python.chmod(0o755)
    (fresh / "requirements.txt").write_text(
        "core-package-fixture\n", encoding="utf-8"
    )
    if include_test_manifest:
        (fresh / "requirements-test.txt").write_text(
            "test-package-fixture\n", encoding="utf-8"
        )
    probe = fresh / "install_linux.sh"
    _build_requirements_probe(probe)

    log = fresh / "python-calls.jsonl"
    assert not log.exists(), "fresh-clone call log is not fresh"
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env["ROW689_PYTHON_LOG"] = str(log)
    env["ROW689_FAIL_MANIFEST"] = fail_manifest
    completed = subprocess.run(
        ["bash", str(probe)],
        cwd=fresh,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    calls = (
        [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        if log.is_file()
        else []
    )
    return fresh, completed, calls


def _installed_manifests(calls: list[list[str]]) -> list[str]:
    requirement_calls = [
        call for call in calls if call[:4] == ["-m", "pip", "install", "-r"]
    ]
    assert all(len(call) == 5 for call in requirement_calls), requirement_calls
    return [Path(call[4]).name for call in requirement_calls]


def test_fresh_install_converges_the_present_test_manifest(tmp_path: Path) -> None:
    fresh, completed, calls = _run_fresh_install(
        tmp_path, include_test_manifest=True
    )
    assert (fresh / "requirements.txt").is_file()
    assert (fresh / "requirements-test.txt").is_file()

    manifests = _installed_manifests(calls)
    assert manifests == ["requirements.txt", "requirements-test.txt"], (
        f"fresh install invoked {len(manifests)} requirement manifests: {manifests}; "
        f"rc={completed.returncode}; stdout={completed.stdout!r}"
    )
    assert len(manifests) == 2
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.count("Installing requirements-test.txt") == 1


def test_test_manifest_install_failure_is_fatal_and_named(tmp_path: Path) -> None:
    fresh, completed, calls = _run_fresh_install(
        tmp_path,
        include_test_manifest=True,
        fail_manifest="requirements-test.txt",
    )
    assert (fresh / "requirements-test.txt").is_file()
    manifests = _installed_manifests(calls)
    assert manifests == ["requirements.txt", "requirements-test.txt"]
    assert len(manifests) == 2
    assert completed.returncode == 1
    assert completed.stdout.count("ERROR: test dependency install failed") == 1


def test_absent_test_manifest_is_not_an_install_failure(tmp_path: Path) -> None:
    fresh, completed, calls = _run_fresh_install(
        tmp_path, include_test_manifest=False
    )
    assert not (fresh / "requirements-test.txt").exists()
    manifests = _installed_manifests(calls)
    assert manifests == ["requirements.txt"]
    assert len(manifests) == 1
    assert completed.returncode == 0, completed.stderr
    assert "Installing requirements-test.txt" not in completed.stdout


def test_row689_transform_control_only_parses_the_shell() -> None:
    parsed = subprocess.run(
        ["bash", "-n", str(_INSTALL_LINUX)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert parsed.returncode == 0, parsed.stderr
