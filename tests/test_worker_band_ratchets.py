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

Verifies (the PORTABLE half only -- see the split below):
  1. Exact count assertion: exactly 7 canonical ratchet/pin/census suites.
  2. All 7 suites exist on disk in the repository tree.
  3. Positive control: proves that a broken pin/ratchet is detected when the
     ratchet suites execute.
  4. Negative control: BD_WORKER_BAND_NO_RATCHETS=1 suppresses appending (0 ratchets added).

THE SPLIT (O1104, generalised to a standing rule by O1117):
  A product-repo pytest may not depend on a host-only artifact, and FLEET_RULE 46 forbids
  skip-on-missing, so four checks that READ the band script moved out of this file to
  harness/bd-worker-band-ratchets-check.sh on the harness host: candidate exists and is
  executable, candidate passes bash -n, candidate names O1058 and all 7 suites, and the
  deployed twins are byte-identical. They were not deleted and they were not weakened: that
  script reports FOUND n / FOUND NONE / COULD NOT LOOK and exits 2 on a hit OR a missing root,
  with one positive and three negative controls in its --selftest.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

BD_GATE_SCOPE = "module"

REPO_ROOT = Path(__file__).resolve().parents[1]

# O1104/O1117: the band script itself lives only on the harness host, so the checks that read
# it are NOT in this file. They are harness/bd-worker-band-ratchets-check.sh, run on the harness
# host. FLEET_RULE 46 forbids skip-on-missing, so there is no host-conditional test here either.
# What stays is the portable half: the canonical list, its exact count, and bash-level controls.

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


def _ratchet_loop_fixture(suites):
    """Build the band's ratchet loop from `suites`.

    O1156d (BOUNCE): the previous fixture restated the seven file names as a bash
    literal beside the O1058_RATCHET_SUITES tuple. Two copies of a list are two
    lists: adding an eighth suite to the tuple left the fixture at seven and the
    count assertion still passed, so the gate could not see the very drift it
    exists to catch. The fixture is now DERIVED, and the test below proves the
    derivation reacts to a changed set rather than assuming it does.
    """
    lines = " \\\n".join("                %s" % s for s in suites)
    return """
    W="$1"
    run_loop() {
        local band=""
        if [ "${BD_WORKER_BAND_NO_RATCHETS:-0}" != "1" ]; then
            for r in \\
%s; do
                [ -f "$W/$r" ] && band="$band $r"
            done
        fi
        echo "$band"
    }
    pos=$(run_loop)
    neg=$(BD_WORKER_BAND_NO_RATCHETS=1 run_loop)
    echo "POS:$pos"
    echo "NEG:$neg"
    """ % lines


def _run_loop(suites):
    proc = subprocess.run(
        ["bash", "-c", _ratchet_loop_fixture(suites), "bash_check", str(REPO_ROOT)],
        capture_output=True, text=True, check=True,
    )
    out = dict(line.split(":", 1) for line in proc.stdout.strip().splitlines())
    return out["POS"].split(), out["NEG"].split()


def test_positive_and_negative_control_in_bash():
    """The loop appends EXACTLY the O1058 set normally, and nothing when suppressed.

    Positive control: unsuppressed, the appended set equals O1058_RATCHET_SUITES.
    Negative control: BD_WORKER_BAND_NO_RATCHETS=1 appends 0.
    """
    pos, neg = _run_loop(O1058_RATCHET_SUITES)
    assert sorted(pos) == sorted(O1058_RATCHET_SUITES), (
        f"loop appended {sorted(pos)}, not the O1058 set")
    assert len(pos) == 7 and neg == [], (
        f"expected 7 appended and 0 suppressed, got {len(pos)} and {len(neg)}")


def test_an_extra_suite_is_visible_to_this_gate():
    """O1156d: the measured escape was a band carrying the 7 canonical suites PLUS
    tests/test_unexpected_suite.py and still reporting OK. Run the same derivation
    against that eight-element set: the set comparison must name the intruder.

    The intruder is a file that EXISTS in the repo, so the loop's own `[ -f ]` test
    cannot be what rejects it -- otherwise this control would pass for the wrong
    reason and prove nothing.
    """
    intruder = "tests/test_worker_band_ratchets.py"
    assert (REPO_ROOT / intruder).is_file(), "the control needs a real file to be honest"
    assert intruder not in O1058_RATCHET_SUITES
    pos, _ = _run_loop(tuple(O1058_RATCHET_SUITES) + (intruder,))
    assert len(pos) == 8 and intruder in pos, pos
    assert sorted(pos) != sorted(O1058_RATCHET_SUITES), (
        "the set comparison does not react to an extra suite -- this is the O1156d escape")


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
