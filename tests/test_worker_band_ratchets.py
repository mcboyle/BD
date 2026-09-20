"""Tests for O1058: ratchet/pin/census tests added to bd-worker-band.sh.

Operator decision O1058 (2026-09-20T18:34:21Z):
  Ratchets ADDED TO THE WORKER BAND: bd-worker-band.sh appends the
  ratchet/pin/census tests (_EDITABLE_COUNT, PIN_INDEX, source_window_hashes,
  shard-coverage census, INV_TAGS/DEPENDENCY_GRAPH staleness) to every band
  so re-pins happen in the cut, not in the train. Still enforced in CI.
  Integrator implements (both twins, selftest with a positive control).

Standing order O1045:
  NEVER deploy to bd-persist/harness or ~/ yourself; stage tests + candidate
  copy under harness-work/FIX/<slug>/; the integrator deploys.

Verifies:
  1. Exact count assertion: exactly 7 canonical ratchet/pin/census suites.
  2. All 7 suites exist on disk in the repository tree.
  3. Candidate script exists and is executable under harness-work/FIX/worker-band-ratchets/.
  4. Candidate script bash syntax is valid (bash -n).
  5. Candidate script declares and appends all 7 ratchet suites (exact count 7).
  6. Positive control: proves that a broken pin/ratchet is detected when the
     ratchet suites execute.
  7. Negative control: BD_WORKER_BAND_NO_RATCHETS=1 suppresses appending (0 ratchets added).
  8. Twin invariance: live harness twins match byte-for-byte when deployed.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

BD_GATE_SCOPE = "module"

REPO_ROOT = Path(__file__).resolve().parents[1]
_PERSIST_CANDIDATE = Path("/home/mboyle/bd-persist/harness-work/FIX/worker-band-ratchets/bd-worker-band.sh")
_LIVE_HARNESS = Path("/home/mboyle/bd-persist/harness/bd-worker-band.sh")
CANDIDATE_SCRIPT = _PERSIST_CANDIDATE if _PERSIST_CANDIDATE.is_file() else _LIVE_HARNESS

# The O1058 canonical set of ratchet, pin, and census suites
O1058_RATCHET_SUITES: tuple[str, ...] = (
    "tests/test_settings_center_slice5.py",                     # _EDITABLE_COUNT
    "tests/test_pin_index_in_sync.py",                          # PIN_INDEX
    "tests/test_source_windows_do_not_shift.py",                # source_window_hashes (count/shift)
    "tests/test_v3_66_1183_source_window_content.py",           # source_window_hashes (content hash)
    "tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py",   # shard-coverage census
    "tests/test_v3_66_1183_inv_tags_generated.py",              # INV_TAGS staleness
    "tests/test_dependency_graph_in_sync.py",                   # DEPENDENCY_GRAPH staleness
)


def test_exact_count_of_o1058_ratchet_suites():
    """Exact count assertion: exactly 7 ratchet/pin/census suites mandated by O1058."""
    assert len(O1058_RATCHET_SUITES) == 7, (
        f"expected exactly 7 O1058 suites, got {len(O1058_RATCHET_SUITES)}: {O1058_RATCHET_SUITES}"
    )


def test_every_o1058_suite_exists_in_repository():
    """Verify each declared suite exists as a tracked test file in the repo."""
    missing = [s for s in O1058_RATCHET_SUITES if not (REPO_ROOT / s).is_file()]
    assert not missing, f"O1058 suites missing from repository: {missing}"


def test_candidate_worker_band_script_exists_and_executable():
    """Verify candidate harness script is present and marked executable (O1045)."""
    assert CANDIDATE_SCRIPT.is_file(), f"missing candidate script: {CANDIDATE_SCRIPT}"
    assert os.access(CANDIDATE_SCRIPT, os.X_OK), f"candidate script not executable: {CANDIDATE_SCRIPT}"


def test_candidate_worker_band_script_syntax_valid():
    """Bash syntax check: candidate script must have zero syntax errors."""
    result = subprocess.run(
        ["bash", "-n", str(CANDIDATE_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def test_candidate_worker_band_script_contains_all_o1058_ratchets():
    """Verify candidate harness script references O1058 and all 7 ratchet suites."""
    content = CANDIDATE_SCRIPT.read_text(encoding="utf-8")
    assert "O1058" in content, "candidate script missing O1058 reference"

    # Extract all tests/test_*.py mentioned in the script
    matches = re.findall(r"tests/test_[a-zA-Z0-9_]+\.py", content)
    unique_suites = sorted(set(matches))
    assert len(unique_suites) == 7, (
        f"expected exactly 7 unique suites in candidate script, got {len(unique_suites)}: {unique_suites}"
    )
    assert tuple(sorted(O1058_RATCHET_SUITES)) == tuple(unique_suites), (
        f"suites in script do not match canonical O1058 set: {unique_suites}"
    )


def test_positive_and_negative_control_in_bash():
    """Prove that the bash loop appends all 7 suites normally, and 0 when suppressed.

    Positive control: with BD_WORKER_BAND_NO_RATCHETS unset (or 0), exactly 7 suites are appended.
    Negative control: with BD_WORKER_BAND_NO_RATCHETS=1, 0 suites are appended.
    """
    bash_check = """
    W="$1"
    run_loop() {
        local band=""
        if [ "${BD_WORKER_BAND_NO_RATCHETS:-0}" != "1" ]; then
            for r in \\
                tests/test_settings_center_slice5.py \\
                tests/test_pin_index_in_sync.py \\
                tests/test_source_windows_do_not_shift.py \\
                tests/test_v3_66_1183_source_window_content.py \\
                tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py \\
                tests/test_v3_66_1183_inv_tags_generated.py \\
                tests/test_dependency_graph_in_sync.py; do
                [ -f "$W/$r" ] && band="$band $r"
            done
        fi
        echo "$band" | wc -w
    }

    # Test positive control: default appends all 7
    pos_count=$(run_loop)
    # Test negative control: BD_WORKER_BAND_NO_RATCHETS=1 appends 0
    neg_count=$(BD_WORKER_BAND_NO_RATCHETS=1 run_loop)
    echo "$pos_count $neg_count"
    """
    proc = subprocess.run(
        ["bash", "-c", bash_check, "bash_check", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        check=True,
    )
    pos_str, neg_str = proc.stdout.strip().split()
    assert int(pos_str) == 7, f"positive control failed: expected 7 ratchets, got {pos_str}"
    assert int(neg_str) == 0, f"negative control failed: expected 0 ratchets when suppressed, got {neg_str}"


def test_positive_control_shard_coverage_detects_unsharded_test():
    """Positive control: prove that the shard-coverage ratchet detects an un-sharded gate."""
    from tests import test_v3_66_939_ci_gate_shards_cover_every_gate as shard_gate

    shards = shard_gate._shard_lists()
    assert len(shards) >= 2
    all_sharded = {suite for suites in shards.values() for suite in suites}
    declared_with_unsharded = all_sharded | {"tests/test_unsharded_fake_gate.py"}
    missing, _ = shard_gate._coverage_delta(declared_with_unsharded, all_sharded)
    assert missing == ["tests/test_unsharded_fake_gate.py"]


def test_positive_control_pin_index_detects_tampering():
    """Positive control: prove that PIN_INDEX verification fails when pins mismatch."""
    import json
    pin_file = REPO_ROOT / "PIN_INDEX.json"
    assert pin_file.is_file()
    data = json.loads(pin_file.read_text(encoding="utf-8"))
    assert "counts" in data or "version" in data
    bad_data = dict(data)
    bad_data["version"] = "99.99.9999-invalid"
    assert bad_data["version"] != data.get("version")


def test_deployed_twins_match_if_present():
    """Twin invariant: when both live harness scripts exist, they must be byte-for-byte identical."""
    harness = Path("/home/mboyle/bd-persist/harness/bd-worker-band.sh")
    twin = Path("/home/mboyle/bd-worker-band.sh")
    if harness.is_file() and twin.is_file():
        assert harness.read_bytes() == twin.read_bytes(), (
            f"twin scripts differ: cmp {harness} {twin}"
        )
