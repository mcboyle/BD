"""Test generated artifacts band noise filter (HB generated-artifacts-band-noise, O1055/O1064/O1066).

Asserts candidate bd-worker-band.sh correctly excludes train-regen gates
while preserving O1058 ratchets and contract floor.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
CANDIDATE = Path(os.environ.get(
    "BD_GENERATED_ARTIFACTS_BAND_NOISE_CANDIDATE",
    "/home/mboyle/bd-persist/harness-work/FIX/generated-artifacts-band-noise/bd-worker-band.sh",
))
DEPLOYED = Path("/home/mboyle/bd-persist/harness/bd-worker-band.sh")


def _resolve_script() -> Path:
    if CANDIDATE.is_file():
        return CANDIDATE
    if DEPLOYED.is_file():
        return DEPLOYED
    pytest.skip("Neither candidate nor deployed bd-worker-band.sh is present")


def test_candidate_filters_train_regen_gates():
    """Verify candidate excludes import_graph_no_new_edges and function_index_in_sync."""
    script = _resolve_script()
    with tempfile.TemporaryDirectory() as tmpdir:
        wt = Path(tmpdir) / "wt"
        # Create minimal git worktree
        subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--quiet", "--detach", str(wt), "HEAD"], check=True)
        try:
            (wt / "venv").symlink_to(REPO / "venv")
            mod_file = wt / "bulk_downloader" / "test_probe_mod.py"
            mod_file.write_text("# probe\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(wt), "add", str(mod_file)], check=True)

            res = subprocess.run(
                ["bash", str(script), str(wt)],
                capture_output=True,
                text=True,
                env=dict(os.environ, BD_WORKER_BAND_DRY_RUN="1"),
                check=False,
            )
            assert res.returncode == 0, f"band derive failed: rc={res.returncode}\n{res.stdout}\n{res.stderr}"
            band_line = [l for l in res.stdout.splitlines() if l.startswith("BAND ")][0]

            assert "tests/test_import_graph_no_new_edges.py" not in band_line
            assert "tests/test_function_index_in_sync.py" not in band_line
            assert "tests/test_contracts.py" in band_line
            assert "tests/test_dependency_graph_in_sync.py" in band_line
        finally:
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(wt)], check=False)
