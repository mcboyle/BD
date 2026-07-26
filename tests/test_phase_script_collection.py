"""Regression coverage for collision-free phase-script collection."""

from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPERS = (
    "tests/test_phase02_selector.py",
    "tests/test_phase03_live_template.py",
    "tests/test_phases04_06.py",
    "tests/test_phases07_12.py",
    "tests/test_phases13_18.py",
    "tests/test_phases19_24.py",
    "tests/test_phases25_30.py",
    "tests/test_phases31_40.py",
)


def test_phase_helpers_collect_without_shadowing_wrappers():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            *WRAPPERS,
            "tests/_phase_scripts",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output

    collected = {
        line.strip()
        for line in result.stdout.splitlines()
        if "::" in line
    }
    expected = {f"{wrapper}::test_phase_boundaries" for wrapper in WRAPPERS}
    assert collected == expected, output
