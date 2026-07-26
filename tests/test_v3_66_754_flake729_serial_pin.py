"""v3.66.754c -- test_v3_66_729_body_contract_fixtures must run in the SERIAL lane.

THE DEFECT (root-caused @754 by bd_729_probe.py, not theorised):

    row name : test_v3_66_729_body_contract_fixtures.py
    level    : FILE-LEVEL (not a test function)
    ERROR    : 'TIMEOUT (>900s)'

The file replays a fixture-backed differential probe (~126 call sites, each an
`fx.ensure()` + a real test-client round-trip). Serial it takes 139s -- the 2nd-slowest
file in the suite. Under `--workers=480` on a box with far fewer cores, that 139s of
per-process work starves under oversubscription and the file crosses run_tests' 900s HARD
timeout, failing the whole file. Every prior theory (748 setup_site collision, 753 _app_cfg
contamination, OOM) is refuted: the failure is FILE-LEVEL, outside any test function.

THE REMEDY ALREADY EXISTS AND WAS SWITCHED OFF. run_tests has a chronic-flake quarantine
lane (`_quarantine_files` + `_partition_serial`) that auto-serialises a file once it flakes
`threshold` times -- but it only arms when `BD_FLAKE_REGISTRY` is set, and capture.sh never
sets it. So the runner's own fix for exactly this never ran.

The deterministic fix: pin 729 into `_PINNED_TOGETHER` (the always-serial set, alongside the
fixed-port fixture sites). It then runs first, alone, with the full 900s and zero contention
-- so its 139s completes clean. It STILL RUNS, STILL COUNTS, and a real failure in isolation
STILL FAILS. This does NOT touch the sound (and hard-won) probe logic in body_contract.py.

RED-first: on the pristine tree, 729 is in the PARALLEL partition, and this fails.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import run_tests_core as RT  # noqa: E402

_729 = "test_v3_66_729_body_contract_fixtures.py"


def test_729_is_in_the_always_serial_pinned_set():
    assert _729 in RT._PINNED_TOGETHER, (
        "%s is not pinned to the serial lane. It is the 2nd-slowest file in the suite "
        "(139s serial) and TIMES OUT at the 900s hard limit under high --workers, failing "
        "the whole file. Pin it so it runs serially, first, with no contention." % _729)


def test_partition_routes_729_to_serial_not_parallel():
    """The behavioural check: given a file list containing 729, the partitioner must place
    it in the SERIAL bucket, never the parallel one -- regardless of the quarantine
    registry (which capture.sh does not enable)."""
    files = [_729, "test_something_else.py", "test_fixture_site.py"]
    serial, parallel = RT._partition_serial(files, iso_names=set())
    assert _729 in serial, "729 was not routed to the serial lane by _partition_serial"
    assert _729 not in parallel, "729 is still in the parallel lane -- it will time out under load"


def test_pinning_729_does_not_disturb_the_existing_pins():
    """NEG guard: the fixed-port fixture sites must stay pinned. Adding 729 must be
    additive, not a replacement."""
    for keep in ("test_fixture_site.py", "test_fixture_site2.py"):
        assert keep in RT._PINNED_TOGETHER, (
            "%s fell out of _PINNED_TOGETHER -- the fixed-port sites must stay serial" % keep)
