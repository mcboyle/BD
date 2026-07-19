#!/usr/bin/env python3
"""RED-first tests for bd_decomp_lib -- the per-cut decomposition dispatch table.

bd-decomp routes the per-cut invariant ritual by target (which snapshot tool, which
baseline, which surface-lock). These pin the dispatch table + arg handling
deterministically; the live `check app` against the 392 tree is demonstrated
separately (it shells out to route_map_snapshot, which imports the app)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import bd_decomp_lib as bd  # noqa: E402


def test_targets_cover_four_monoliths():
    assert set(bd.TARGETS) == {"app", "runner", "dev_suite", "deep_detect"}, set(bd.TARGETS)


def test_app_dispatch():
    t = bd.TARGETS["app"]
    assert t["invariant_tool"] == "route_map_snapshot.py", t
    assert t["baseline"] == "route_map_baseline.txt", t
    assert t["surface_lock_test"] == "test_route_map_invariant.py", t


def test_runner_dispatch():
    assert bd.TARGETS["runner"]["invariant_tool"] == "runner_api_snapshot.py"


def test_dev_suite_dispatch():
    assert bd.TARGETS["dev_suite"]["surface_lock_test"] == "test_dev_suite_surface_lock.py"


def test_deep_detect_dispatch():
    assert bd.TARGETS["deep_detect"]["invariant_tool"] == "deep_detect_surface.py"


def test_unknown_target_rejected():
    rc = bd.main(["check", "bogus"])
    assert rc != 0, "unknown target must return non-zero"


def test_targets_subcommand_runs_clean():
    rc = bd.main(["targets"])
    assert rc == 0, rc


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except Exception as e:
            fails += 1
            print(f"  FAIL {fn.__name__}: {e!r}")
    print(f"{len(fns) - fails}/{len(fns)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_run())
