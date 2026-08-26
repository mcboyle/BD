"""Rows 247/248: test builds cannot rewrite the deployed SPA bundle."""
from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from tests import frontend_vitest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_FRONTEND = _REPO / "frontend"
_DIST = _FRONTEND / "dist"
_MARKER = _DIST / ".bd-built-from"


def _file_manifest(root: Path) -> dict[str, str]:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    assert files, f"byte-manifest denominator is zero beneath {root}"
    manifest = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    assert len(manifest) == len(files) > 0
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in manifest.values())
    return manifest


def _assert_manifest_unchanged(
    before: dict[str, str], after: dict[str, str]
) -> None:
    assert before and after, "cannot compare an empty frontend/dist manifest"
    removed = sorted(before.keys() - after.keys())
    added = sorted(after.keys() - before.keys())
    rewritten = sorted(
        path for path in before.keys() & after.keys() if before[path] != after[path]
    )
    assert after == before, (
        "build_manifest() mutated the real frontend/dist: "
        f"removed={removed}, added={added}, rewritten={rewritten}"
    )


def _head_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"cannot identify deployed commit: {proc.stderr}"
    commit = proc.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", commit), commit
    return commit


def _require_measurable_deployed_bundle() -> str:
    if not _DIST.is_dir():
        pytest.skip(
            f"frontend/dist is unavailable at {_DIST}; build isolation is UNKNOWN"
        )
    if shutil.which("npm") is None:
        pytest.skip("npm is unavailable; build isolation is UNKNOWN")
    unavailable = [
        path
        for path in (frontend_vitest.TSC, frontend_vitest.VITE)
        if not path.is_file()
    ]
    if unavailable:
        pytest.skip(
            f"frontend build tools are unavailable ({unavailable}); "
            "build isolation is UNKNOWN"
        )
    if not _MARKER.is_file():
        pytest.skip(
            f"deployed provenance marker is unavailable at {_MARKER}; "
            "marker preservation is UNKNOWN"
        )
    marker = _MARKER.read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[0-9a-f]{40}", marker), (
        f"deployed provenance marker is malformed: {marker!r}"
    )
    expected = _head_commit()
    assert marker == expected, (
        f"deployed provenance marker {marker} does not match HEAD {expected}"
    )
    return marker


def test_build_manifest_preserves_real_dist_bytes_and_provenance(monkeypatch):
    """The real build runs once without changing any deployed bundle byte."""
    deployed_commit = _require_measurable_deployed_bundle()
    before = _file_manifest(_DIST)
    assert ".bd-built-from" in before

    calls: list[tuple[tuple[str, ...], Path | None]] = []
    real_run = frontend_vitest._run

    def recording_run(argv, **kwargs):
        cwd = kwargs.get("cwd")
        calls.append((tuple(argv), Path(cwd) if cwd is not None else None))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(frontend_vitest, "_run", recording_run)
    built_manifest = frontend_vitest.build_manifest()

    assert isinstance(built_manifest, dict) and len(built_manifest) > 0
    after = _file_manifest(_DIST)
    _assert_manifest_unchanged(before, after)
    assert _MARKER.is_file(), "build_manifest() deleted frontend/dist/.bd-built-from"
    assert _MARKER.read_text(encoding="ascii").strip() == deployed_commit
    assert len(calls) == 2, f"expected one tsc and one Vite call, got {calls}"
    assert all(cwd is not None and cwd != _FRONTEND for _, cwd in calls), (
        f"frontend command escaped the owned copy: {calls}"
    )
    vite_calls = [call for call in calls if "build" in call[0]]
    assert len(vite_calls) == 1, f"expected exactly one Vite build, got {vite_calls}"
    vite_argv, _ = vite_calls[0]
    assert vite_argv.count("--outDir") == 1
    out_dir = Path(vite_argv[vite_argv.index("--outDir") + 1])
    assert out_dir != _DIST and "--emptyOutDir" in vite_argv, vite_argv


def test_isolated_frontend_copy_excludes_dist_and_links_only_dependencies(tmp_path):
    """The copied input has source bytes, no deploy, and one dependency link."""
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "node_modules").mkdir()
    (source / "dist").mkdir()
    inputs = {
        "package.json": b'{}\n',
        "vite.config.ts": b"export default {};\n",
        "tsconfig.json": b'{}\n',
        "src/main.tsx": b"export {};\n",
        "dist/.bd-built-from": b"a" * 40 + b"\n",
    }
    for relative, content in inputs.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    assert len(inputs) == 5 and all((source / path).is_file() for path in inputs)

    destination = tmp_path / "copied"
    copied = frontend_vitest._copy_frontend_for_build(source, destination)

    assert copied == destination
    assert not (copied / "dist").exists()
    dependency_link = copied / "node_modules"
    assert dependency_link.is_symlink()
    assert dependency_link.resolve() == (source / "node_modules").resolve()
    copied_inputs = {
        path: (copied / path).read_bytes()
        for path in ("package.json", "vite.config.ts", "tsconfig.json", "src/main.tsx")
    }
    assert copied_inputs == {
        path: inputs[path]
        for path in ("package.json", "vite.config.ts", "tsconfig.json", "src/main.tsx")
    }


def test_manifest_comparison_rejects_missing_and_rewritten_bytes(tmp_path):
    """Negative control: path deletion and byte drift both make the gate RED."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    marker = dist / ".bd-built-from"
    bundle = dist / "assets" / "app.js"
    marker.write_text("a" * 40 + "\n", encoding="ascii")
    bundle.write_bytes(b"before\n")
    before = _file_manifest(dist)
    assert sorted(before) == [".bd-built-from", "assets/app.js"]

    marker.unlink()
    bundle.write_bytes(b"after\n")
    after = _file_manifest(dist)
    assert sorted(after) == ["assets/app.js"]
    with pytest.raises(
        AssertionError,
        match=(
            r"removed=\['\.bd-built-from'\], added=\[\], "
            r"rewritten=\['assets/app\.js'\]"
        ),
    ):
        _assert_manifest_unchanged(before, after)


def test_transform_control_imports_bridge_without_asserting_build_isolation():
    """Transform control: importing the bridge does not execute its build."""
    imported = importlib.import_module("tests.frontend_vitest")
    assert imported.__file__ == frontend_vitest.__file__
