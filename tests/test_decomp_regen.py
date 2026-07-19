#!/usr/bin/env python3
"""RED-first tests for decomp_regen -- the per-target regen ORDER enforcer.

The ordering is the whole point (the 381/382 failure class): build_route_index runs
LAST (after gui_parity_inventory), and build_pin_index != build_route_index. These
tests pin the order logic deterministically -- no generators are actually run."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import decomp_regen as dr  # noqa: E402


def test_plans_per_target():
    assert dr.plan("dev_suite") == ["dependency_graph"], dr.plan("dev_suite")
    assert dr.plan("deep_detect") == ["dependency_graph"], dr.plan("deep_detect")
    assert dr.plan("runner") == ["build_function_index", "dependency_graph"], dr.plan("runner")
    assert dr.plan("app") == [
        "build_function_index", "build_endpoint_catalog", "gui_parity_inventory",
        "build_pin_index", "build_route_index", "check_route_counts",
    ], dr.plan("app")


def test_route_index_runs_last_for_app():
    p = dr.plan("app")
    assert p.index("build_route_index") > p.index("gui_parity_inventory"), "route_index must follow gui_parity"
    assert p.index("build_pin_index") < p.index("build_route_index"), "pin_index != route_index; pin precedes route"
    assert p.index("build_route_index") < p.index("check_route_counts"), "G12 check is the final step"


def test_unknown_target_errors():
    try:
        dr.plan("bogus")
    except KeyError:
        return
    raise AssertionError("expected KeyError for an unknown target")


def test_in_sync_suites_for_app():
    s = dr.in_sync_tests("app")
    assert "tests/test_route_index_in_sync.py" in s, s
    assert "tests/test_function_index_in_sync.py" in s, s


def test_dev_suite_is_dependency_graph_only():
    # the lowest-ripple target must NOT drag in route/function-index regens
    assert dr.plan("dev_suite") == ["dependency_graph"]
    assert "tests/test_route_index_in_sync.py" not in dr.in_sync_tests("dev_suite")


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
