"""Fail-closed bridge from repo-wide pytest gates to focused Vitest specs."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
VITEST = FRONTEND / "node_modules" / ".bin" / "vitest"
TSC = FRONTEND / "node_modules" / ".bin" / "tsc"
VITE = FRONTEND / "node_modules" / ".bin" / "vite"


def _run(
    argv: list[str],
    *,
    cwd: Path = FRONTEND,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, (
        f"frontend command failed ({proc.returncode}): {' '.join(argv)}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    return proc


def _copy_frontend_for_build(source: Path, destination: Path) -> Path:
    """Copy build inputs while sharing only the installed dependency tree."""
    assert source.is_dir(), f"frontend source unavailable: {source}"
    node_modules = source / "node_modules"
    assert node_modules.is_dir(), (
        f"frontend build dependencies unavailable at {node_modules}"
    )
    required = [
        source / "package.json",
        source / "vite.config.ts",
        source / "tsconfig.json",
        source / "src",
    ]
    assert all(path.exists() for path in required), (
        f"frontend build-input denominator is incomplete: {required}"
    )
    source_files = sum(1 for path in (source / "src").rglob("*") if path.is_file())
    assert source_files > 0, "frontend/src build-input denominator is zero"
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("dist", "node_modules"),
    )
    os.symlink(node_modules, destination / "node_modules", target_is_directory=True)
    assert not (destination / "dist").exists(), (
        "frontend/dist leaked into the isolated build input"
    )
    assert (destination / "vite.config.ts").read_bytes() == (
        source / "vite.config.ts"
    ).read_bytes()
    return destination


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


@contextmanager
def isolated_spa_dist(source: Path = FRONTEND):
    """Yield a fresh Vite output owned by a temporary copied frontend."""
    node = shutil.which("node")
    assert node is not None, (
        "UNKNOWN: Node is unavailable, so the frontend build subject cannot be "
        "measured"
    )
    with tempfile.TemporaryDirectory(prefix="bd_frontend_manifest_") as raw_tmp:
        workspace = Path(raw_tmp)
        frontend = _copy_frontend_for_build(source, workspace / "frontend")
        dist = workspace / "dist"
        assert not dist.exists(), f"owned build output already exists: {dist}"
        tsc = frontend / "node_modules" / ".bin" / "tsc"
        vite = frontend / "node_modules" / ".bin" / "vite"
        assert tsc.is_file() and vite.is_file(), (
            "isolated frontend build tools unavailable"
        )
        _run(
            [str(tsc), "-b", "--pretty", "false"],
            cwd=frontend,
            timeout=180,
        )
        _run(
            [
                str(vite),
                "build",
                "--manifest",
                "--outDir",
                str(dist),
                "--emptyOutDir",
            ],
            cwd=frontend,
            timeout=180,
        )
        manifest_path = dist / ".vite" / "manifest.json"
        assert manifest_path.is_file(), (
            "Vite build did not produce owned dist/.vite/manifest.json"
        )
        index_path = dist / "index.html"
        assert index_path.is_file(), (
            "Vite build did not produce owned dist/index.html"
        )
        yield dist


def build_manifest(source: Path = FRONTEND) -> dict[str, object]:
    """Typecheck and build an isolated SPA copy, returning its fresh manifest."""
    with isolated_spa_dist(source) as dist:
        manifest_path = dist / ".vite" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest, "Vite manifest is empty"
        return manifest
