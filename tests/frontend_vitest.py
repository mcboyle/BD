"""Fail-closed bridge from repo-wide pytest gates to focused Vitest specs."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
VITEST = FRONTEND / "node_modules" / ".bin" / "vitest"
TSC = FRONTEND / "node_modules" / ".bin" / "tsc"
VITE = FRONTEND / "node_modules" / ".bin" / "vite"


def _run(argv: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, (
        f"frontend command failed ({proc.returncode}): {' '.join(argv)}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    return proc


def run_vitest(spec: str, *, expected_tests: int) -> None:
    """Run exactly one tracked spec and reconcile its executed denominator."""
    path = FRONTEND / spec
    assert path.is_file(), f"Vitest subject missing: {path}"
    assert VITEST.is_file(), (
        f"Vitest unavailable at {VITEST}; run `npm ci` in frontend/ before pytest"
    )
    proc = _run(
        [str(VITEST), "run", spec, "--reporter=verbose", "--no-color"],
        timeout=180,
    )
    files = re.search(r"Test Files\s+(\d+) passed \((\d+)\)", proc.stdout)
    tests = re.search(r"Tests\s+(\d+) passed \((\d+)\)", proc.stdout)
    assert files and tuple(map(int, files.groups())) == (1, 1), proc.stdout
    assert tests, f"Vitest emitted no parseable test denominator:\n{proc.stdout}"
    passed, collected = map(int, tests.groups())
    assert passed == collected == expected_tests, (
        f"Vitest denominator mismatch for {spec}: "
        f"expected={expected_tests}, passed={passed}, collected={collected}\n"
        f"{proc.stdout}"
    )


def build_manifest() -> dict[str, object]:
    """Typecheck and build the real SPA, returning Vite's fresh manifest."""
    assert TSC.is_file() and VITE.is_file(), "frontend build tools unavailable"
    _run([str(TSC), "-b", "--pretty", "false"], timeout=180)
    _run([str(VITE), "build", "--manifest"], timeout=180)
    manifest_path = FRONTEND / "dist" / ".vite" / "manifest.json"
    assert manifest_path.is_file(), "Vite build did not produce dist/.vite/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest, "Vite manifest is empty"
    return manifest
