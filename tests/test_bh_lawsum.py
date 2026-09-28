"""BH-LAWSUM: SessionStart hook must warn and exit 0, never refuse with exit 2.

Harness candidate test (bd-harness-cut shape): opt in with
BD_BH_LAWSUM_CANDIDATE_DIR=/home/mboyle/bd-persist/harness-work/FIX/bh-lawsum-g4-bd-worker-B1-B

G4 (RULINGS-1210 R2, correctness-codex-r127c G3 R2): the 1024-byte ceiling covers
the WHOLE hook, stdout + stderr, not only the cached-verdict block (tests 006-009).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE_DIR = os.environ.get("BD_BH_LAWSUM_CANDIDATE_DIR", "")
pytestmark = pytest.mark.skipif(not CANDIDATE_DIR, reason="candidate opt-in required")


def _run_cmd(
    cmd: list[str],
    env_overrides: dict[str, str] | None = None,
    timeout: float = 15,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def _run_isolated_default_path(
    script_path: str,
    tmp_path: Path,
    timeout: float = 15,
) -> subprocess.CompletedProcess[str]:
    """Run script in an isolated namespace where default LAWSUM.status is absent.

    Never renames or touches live fleet cache. Uses bwrap mount namespace when
    available, falling back to private override.
    """
    bwrap = shutil.which("bwrap")
    if bwrap:
        target_script = tmp_path / "isolated_script.sh"
        shutil.copy2(script_path, target_script)
        target_script.chmod(0o755)

        empty_persist = tmp_path / "empty_persist"
        empty_persist.mkdir(parents=True, exist_ok=True)
        harness_dir = empty_persist / "harness"
        harness_dir.mkdir(parents=True, exist_ok=True)
        live_floor = Path("/home/mboyle/bd-persist/harness/bd-floor-check.py")
        if live_floor.is_file():
            shutil.copy2(live_floor, harness_dir / "bd-floor-check.py")

        cmd = [
            bwrap,
            "--ro-bind",
            "/",
            "/",
            "--dev-bind",
            "/dev",
            "/dev",
            "--proc",
            "/proc",
            "--bind",
            str(tmp_path),
            str(tmp_path),
            "--bind",
            str(empty_persist),
            "/home/mboyle/bd-persist",
            str(target_script),
        ]
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )

    env = {
        "BD_LAWSUM_STATUS": str(tmp_path / "missing_default.status"),
        "BD_FLOOR_CHECK": "/bin/true",
    }
    return _run_cmd([script_path], env_overrides=env, timeout=timeout)


def test_001_candidate_file_exists_and_executable() -> None:
    p = Path(CANDIDATE_DIR)
    assert p.is_dir(), f"candidate dir missing: {CANDIDATE_DIR}"
    script = p / "bd-lawsum-sessionstart.sh"
    assert script.is_file(), f"missing candidate script: {script}"
    assert os.access(script, os.X_OK), f"script not executable: {script}"
    first_line = script.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!/bin/bash"), f"unexpected shebang: {first_line}"


def test_002_finding_008_missing_status_warns_and_exits_zero(tmp_path: Path) -> None:
    """FINDING-008: Missing LAWSUM.status emits warning on stdout and exits 0 (never locks out with exit 2).

    Isolates default-path evaluation in a namespace without touching live fleet cache.
    """
    script = str(Path(CANDIDATE_DIR) / "bd-lawsum-sessionstart.sh")
    res = _run_isolated_default_path(script, tmp_path)
    assert res.returncode == 0, (
        f"script exited non-zero ({res.returncode}): stdout={res.stdout} stderr={res.stderr}"
    )
    assert "COULD NOT LOOK" in res.stdout
    assert "no cached verdict" in res.stdout


def test_003_finding_008_env_override_missing_status_exits_zero(tmp_path: Path) -> None:
    """Private override fixture for candidate: BD_LAWSUM_STATUS pointing to missing file emits warning to stdout and exits 0."""
    script = str(Path(CANDIDATE_DIR) / "bd-lawsum-sessionstart.sh")
    missing_file = tmp_path / "custom_missing.status"
    env = {
        "BD_LAWSUM_STATUS": str(missing_file),
        "BD_FLOOR_CHECK": "/bin/true",
    }
    res = _run_cmd([script], env_overrides=env)
    assert res.returncode == 0
    assert "COULD NOT LOOK" in res.stdout


def test_004_status_file_present_prints_verdict(tmp_path: Path) -> None:
    """Positive control: status file present emits verdict line on stderr and exits 0."""
    script = str(Path(CANDIDATE_DIR) / "bd-lawsum-sessionstart.sh")
    custom_status = tmp_path / "custom_LAWSUM.status"
    custom_status.write_text(
        "# bd-lawsum 2026-09-28T11:00:00Z  14 MATCH / 42 DRIFT / 12 COULD-NOT-LOOK  (denominator 68)\n"
        "test2 DRIFT /home/mboyle/CLAUDE.md\n",
        encoding="utf-8",
    )
    env = {
        "BD_LAWSUM_STATUS": str(custom_status),
        "BD_FLOOR_CHECK": "/bin/true",
    }
    res = _run_cmd([script], env_overrides=env)
    assert res.returncode == 0
    assert "bd-lawsum: bd-lawsum" in res.stderr
    assert "test2 DRIFT" in res.stderr


def test_005_floor_check_failure_warns_and_preserves_exit_zero(tmp_path: Path) -> None:
    """Negative control: floor check failure emits warning to stdout but preserves exit 0."""
    script = str(Path(CANDIDATE_DIR) / "bd-lawsum-sessionstart.sh")
    env = {
        "BD_LAWSUM_STATUS": str(tmp_path / "nonexistent.status"),
        "BD_FLOOR_CHECK": "/bin/false",
    }
    res = _run_cmd([script], env_overrides=env)
    assert res.returncode == 0
    assert "bd-floor: COULD-NOT-LOOK" in res.stdout


# ---- G4 R2: one total ceiling (stdout + stderr) for the whole hook -----------------------------

CAP = 1024


def _script() -> str:
    return str(Path(CANDIDATE_DIR) / "bd-lawsum-sessionstart.sh")


def _floor_stub(tmp_path: Path, line: str, rc: int = 1) -> str:
    stub = tmp_path / "floor_stub.py"
    stub.write_text(
        f"import sys\nsys.stdout.write({line!r} + '\\n')\nsys.exit({rc})\n",
        encoding="utf-8",
    )
    return str(stub)


def _big_status(tmp_path: Path) -> Path:
    f = tmp_path / "big_LAWSUM.status"
    f.write_text(
        "# bd-lawsum 2026-09-28T11:00:00Z "
        + "H" * 2000
        + "\n"
        + "".join(f"test2 DRIFT /x/{i}/" + "D" * 1000 + "\n" for i in range(6)),
        encoding="utf-8",
    )
    return f


def _total(res: subprocess.CompletedProcess[str]) -> int:
    return len(res.stdout.encode()) + len(res.stderr.encode())


def test_006_r2_long_missing_path_warning_is_capped(tmp_path: Path) -> None:
    # G3 R2 probe: a private missing path of 24 x 80-char components -> 2115 B stdout, uncapped.
    deep = tmp_path.joinpath(*(["p" * 80] * 24), "LAWSUM.status")
    res = _run_cmd(
        [_script()],
        {
            "BD_LAWSUM_STATUS": str(deep),
            "BD_FLOOR_CHECK": _floor_stub(tmp_path, "bd-floor: PASS x", 0),
        },
    )
    assert res.returncode == 0
    assert res.stdout.startswith("bd-lawsum: COULD NOT LOOK -- no cached verdict at ")
    assert len(res.stdout.encode()) == 257 and _total(res) <= CAP, _total(res)


def test_007_r2_long_floor_override_line_is_capped(tmp_path: Path) -> None:
    # G3 R2 probe: BD_FLOOR_CHECK returning a 64 KiB "bd-floor: DRIFT" line -> 65553 B stdout.
    stub = _floor_stub(tmp_path, "bd-floor: DRIFT " + "Z" * 65536)
    res = _run_cmd(
        [_script()],
        {"BD_LAWSUM_STATUS": str(tmp_path / "none.status"), "BD_FLOOR_CHECK": stub},
    )
    assert res.returncode == 0
    assert (
        res.stdout.startswith("bd-lawsum: COULD NOT LOOK")
        and "\nbd-floor: DRIFT ZZZ" in res.stdout
    )
    assert _total(res) <= CAP, _total(res)


def test_008_r2_stdout_warning_plus_cache_share_one_budget(tmp_path: Path) -> None:
    # G3 R2 probe: 40 B FAIL line on stdout + 1024 B cached block on stderr = 1064 B.
    line = "bd-floor: FAIL-SHA floor 1ec85ef4 src 00"
    res = _run_cmd(
        [_script()],
        {
            "BD_LAWSUM_STATUS": str(_big_status(tmp_path)),
            "BD_FLOOR_CHECK": _floor_stub(tmp_path, line),
        },
    )
    assert res.returncode == 0
    assert res.stdout == line + "\n"  # the warning is never what the cap cuts
    assert res.stderr.startswith("bd-lawsum: bd-lawsum")
    assert _total(res) == CAP  # the cache uses exactly the bytes stdout left


def test_009_r2_worst_case_both_long_stdout_lines_and_cache(tmp_path: Path) -> None:
    stub = _floor_stub(tmp_path, "bd-floor: DRIFT " + "Z" * 5000)
    res = _run_cmd(
        [_script()],
        {"BD_LAWSUM_STATUS": str(_big_status(tmp_path)), "BD_FLOOR_CHECK": stub},
    )
    assert res.returncode == 0
    assert len(res.stdout.encode()) == 257 and res.stdout.startswith(
        "bd-floor: DRIFT ZZZ"
    )
    assert _total(res) == CAP and len(res.stderr.encode()) == CAP - 257
