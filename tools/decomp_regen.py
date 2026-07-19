#!/usr/bin/env python3
"""decomp_regen.py -- run a decomposition target's in-sync regenerators in the
ENFORCED order. The order is the whole point (the 381/382 on-stash-failure class):

  * build_route_index runs LAST, after gui_parity_inventory (it consumes the
    gui_parity spa_wired join);
  * build_pin_index != build_route_index -- a version/route change needs BOTH;
  * check_route_counts (G12) is the final gate.

Per-target plan is the program roadmap's regen matrix:
  dev_suite / deep_detect : DEPENDENCY_GRAPH only
  runner                  : FUNCTION_INDEX + DEPENDENCY_GRAPH
  app                     : the full route-doc set + FUNCTION_INDEX + DEPENDENCY_GRAPH

--dry-run (DEFAULT) prints the ordered plan + the *_in_sync suites to band from the
extracted zip. --apply runs each generator as a subprocess against --root, halting on
the first failure. NO Vite, ever (decomposition cuts are no-FE-churn). Stdlib-only.

Usage:
    decomp_regen.py <target>                 # dry-run: print the ordered plan
    decomp_regen.py <target> --apply         # run the generators in order (no Vite)
    decomp_regen.py <target> --root /home/claude/work
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

# tools/<name>.py, in the order they MUST run.
PLANS = {
    "dev_suite":   ["dependency_graph"],
    "deep_detect": ["dependency_graph"],
    "runner":      ["build_function_index", "dependency_graph"],
    "app":         ["build_function_index", "build_endpoint_catalog", "gui_parity_inventory",
                    "build_pin_index", "build_route_index", "check_route_counts"],
}

# the *_in_sync (and parity / G12) suites that gate each target, to band from the zip.
IN_SYNC = {
    "dev_suite":   ["tests/test_dependency_graph_in_sync.py"],
    "deep_detect": ["tests/test_dependency_graph_in_sync.py"],
    "runner":      ["tests/test_function_index_in_sync.py",
                    "tests/test_dependency_graph_in_sync.py"],
    "app":         ["tests/test_function_index_in_sync.py",
                    "tests/test_endpoint_catalog_in_sync.py",
                    "tests/test_dependency_graph_in_sync.py",
                    "tests/test_gui_parity.py",
                    "tests/test_pin_index_in_sync.py",
                    "tests/test_route_index_in_sync.py"],
}

# check_route_counts is a GATE, not a regen -- it doesn't write an artifact.
_GATES = {"check_route_counts"}


def plan(target: str) -> list[str]:
    return list(PLANS[target])  # KeyError on an unknown target (by design)


def in_sync_tests(target: str) -> list[str]:
    return list(IN_SYNC[target])


def _run_one(name: str, root: str) -> int:
    script = os.path.join(root, "tools", f"{name}.py")
    if not os.path.isfile(script):
        print(f"    MISSING {script}")
        return 127
    env = os.environ.copy()
    # ensure the package + prestaged deps resolve for the app-importing generators
    extra = [root, "/tmp/prestaged_site_packages"]
    env["PYTHONPATH"] = os.pathsep.join([p for p in extra if p] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    print(f"    $ python3 tools/{name}.py")
    return subprocess.run([sys.executable, script], cwd=root, env=env).returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="run a decomposition target's regens in the enforced order")
    ap.add_argument("target", choices=sorted(PLANS), help="which monolith's cut you just made")
    ap.add_argument("--root", default="/home/claude/work", help="work-tree root")
    ap.add_argument("--apply", action="store_true", help="actually run the generators (default: dry-run)")
    a = ap.parse_args(argv)

    steps = plan(a.target)
    print(f"== regen plan for '{a.target}' (NO Vite) ==")
    for i, name in enumerate(steps, 1):
        tag = "  [G12 gate]" if name in _GATES else ""
        print(f"  {i}. tools/{name}.py{tag}")
    print("  then band from the EXTRACTED zip:")
    for t in in_sync_tests(a.target):
        print(f"     - {t}")

    if not a.apply:
        print("\n(dry-run; pass --apply to execute. route_index runs LAST -- never transpose it.)")
        return 0

    print("\n== applying ==")
    for name in steps:
        rc = _run_one(name, a.root)
        if rc != 0:
            print(f"!! {name} exited {rc} -- halting (fix before continuing the order).")
            return rc
    print("OK  all regens ran in order. Now band the *_in_sync suites from the extracted zip.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
