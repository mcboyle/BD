"""Cut v3.66.637 / C4 flake-registry: persist the ephemeral flake classifications.

``run_tests._retry_failures_serial`` already classifies REAL vs FLAKY per run and
reports flakes loudly, but the knowledge is EPHEMERAL -- printed, then gone.
``KNOWN_FLAKES.md`` exists as a human doc but is not machine-consumed. This adds a
persistent flake registry (accumulate per-test flake counts across runs) so a
CHRONIC flake (e.g. test_session_keeper, which recurs across sessions) is trackable
rather than re-discovered every run.

Matches the sibling C4 helper ``_files_over_budget``: pure functions, env-tunable,
informational-only (never changes pass/fail), default-OFF (an empty
``BD_FLAKE_REGISTRY`` path means no persistence -> no tracked-tree churn and no
behavior change in the release band).

RED on pristine 3.66.636: run_tests has no _update_flake_registry / _chronic_flakes
/ _FLAKE_CHRONIC_THRESHOLD / the I/O helpers.
"""
import os
import tempfile

import run_tests_core as run_tests


def test_helpers_exist():
    assert hasattr(run_tests, "_update_flake_registry")
    assert hasattr(run_tests, "_chronic_flakes")
    assert hasattr(run_tests, "_FLAKE_CHRONIC_THRESHOLD")
    assert hasattr(run_tests, "_load_flake_registry")
    assert hasattr(run_tests, "_save_flake_registry")


def test_update_adds_new_flake():
    reg = run_tests._update_flake_registry({}, ["a.py :: t1"], 100.0)
    assert reg["a.py :: t1"]["count"] == 1
    assert reg["a.py :: t1"]["first_seen"] == 100.0
    assert reg["a.py :: t1"]["last_seen"] == 100.0


def test_update_increments_existing_and_keeps_first_seen():
    reg = run_tests._update_flake_registry({}, ["a.py :: t1"], 100.0)
    reg = run_tests._update_flake_registry(reg, ["a.py :: t1"], 250.0)
    assert reg["a.py :: t1"]["count"] == 2
    assert reg["a.py :: t1"]["first_seen"] == 100.0   # preserved
    assert reg["a.py :: t1"]["last_seen"] == 250.0    # advanced


def test_update_does_not_mutate_input():
    orig = {}
    run_tests._update_flake_registry(orig, ["a.py :: t1"], 1.0)
    assert orig == {}, "must return a NEW dict, never mutate the caller's registry"


def test_update_empty_flaky_is_noop():
    reg = {"a.py :: t1": {"count": 3, "first_seen": 1.0, "last_seen": 9.0}}
    out = run_tests._update_flake_registry(reg, [], 100.0)
    assert out == reg


def test_chronic_flakes_threshold_and_sort():
    reg = {
        "a.py :: t1": {"count": 5, "first_seen": 1, "last_seen": 9},
        "b.py :: t2": {"count": 2, "first_seen": 1, "last_seen": 9},
        "c.py :: t3": {"count": 8, "first_seen": 1, "last_seen": 9},
    }
    # (id, count) tuples, count-desc; only counts >= threshold
    assert run_tests._chronic_flakes(reg, 3) == [("c.py :: t3", 8), ("a.py :: t1", 5)]


def test_chronic_empty_under_threshold():
    reg = {"a.py :: t1": {"count": 1, "first_seen": 1, "last_seen": 9}}
    assert run_tests._chronic_flakes(reg, 3) == []


def test_chronic_threshold_zero_disables():
    reg = {"a.py :: t1": {"count": 99, "first_seen": 1, "last_seen": 9}}
    assert run_tests._chronic_flakes(reg, 0) == []


def test_registry_roundtrip_io():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "sub", "flakes.json")   # nested dir must be auto-created
    reg = run_tests._update_flake_registry({}, ["x.py :: t"], 5.0)
    assert run_tests._save_flake_registry(path, reg) is True
    assert run_tests._load_flake_registry(path) == reg


def test_load_missing_registry_is_empty():
    assert run_tests._load_flake_registry("/nonexistent/definitely/nope.json") == {}
