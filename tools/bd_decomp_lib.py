#!/usr/bin/env python3
"""bd_decomp_lib -- the per-cut decomposition ritual, dispatched by target.

One command instead of remembering which invariant tool + baseline + surface-lock +
cycle check each monolith uses. The engine behind the `bd-decomp` shim.

  bd-decomp targets                 list targets + their invariant tool/baseline/lock
  bd-decomp baseline <target>       (re)freeze the invariant baseline at phase open
  bd-decomp check <target>          live invariant diff + cross-monolith --check

Resolves the kit generators via $DECOMP_KIT (default
DECOMP_KIT or the in-repository tools/decomp directory) and the work root via
--root (default repository root). The app/runner invariant tools have snapshots that this drives
end-to-end; dev_suite/deep_detect use a surface-lock TEST, which this points you to
(the band runs it -- it's pytest-style, not a standalone snapshot).

Exit 0 = OK / invariant held; non-zero = a divergence, a missing baseline, or a bad
argument.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys

DEFAULT_ROOT = os.environ.get(
    "BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)

# target -> the per-cut handles. invariant_tool lives under <kit>/tools or
# <kit>/runner/tools; surface_lock_test ships beside tests/.
TARGETS = {
    "app": {
        "invariant_tool": "route_map_snapshot.py",
        "tool_subdir": "tools",
        "baseline": "route_map_baseline.txt",
        "surface_lock_test": "test_route_map_invariant.py",
        "imports_app": True,            # the snapshot imports the Flask app
        "cross_check": True,
    },
    "runner": {
        "invariant_tool": "runner_api_snapshot.py",
        "tool_subdir": "runner/tools",
        "baseline": "runner_api_snapshot.json",
        "surface_lock_test": None,      # runner_api_snapshot IS the gate
        "imports_app": False,           # AST-only
        "cross_check": True,
    },
    "dev_suite": {
        "invariant_tool": None,         # surface-lock test is the gate
        "tool_subdir": "tools",
        "baseline": None,
        "surface_lock_test": "test_dev_suite_surface_lock.py",
        "imports_app": False,
        "cross_check": True,
    },
    "deep_detect": {
        "invariant_tool": "deep_detect_surface.py",
        "tool_subdir": "tools",
        "baseline": None,               # --emit-lock feeds the surface-lock test
        "surface_lock_test": "test_deep_detect_surface_lock.py",
        "imports_app": False,
        "cross_check": True,
    },
}


def _kit() -> str:
    return os.environ.get("DECOMP_KIT", os.path.join(DEFAULT_ROOT, "tools", "decomp"))


def _tool_path(target: str) -> str:
    t = TARGETS[target]
    here = os.path.dirname(os.path.abspath(__file__))
    sib = os.path.join(here, t["invariant_tool"])                      # in-tree: tools/ sibling
    if os.path.isfile(sib):
        return sib
    return os.path.join(_kit(), t["tool_subdir"], t["invariant_tool"])  # staged-kit fallback


def _baseline_path(target: str, root: str) -> str | None:
    t = TARGETS[target]
    if not t["baseline"]:
        return None
    # prefer a baseline landed in the work tree's tests/, else the kit's tests/
    in_tree = os.path.join(root, "tests", t["baseline"])
    if os.path.isfile(in_tree):
        return in_tree
    return os.path.join(_kit(), "tests", t["baseline"])


def _env_for(root: str, imports_app: bool):
    env = os.environ.copy()
    if imports_app:
        extra = [root, "/tmp/prestaged_site_packages"]
        env["PYTHONPATH"] = os.pathsep.join(
            [p for p in extra if p] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _run_snapshot(target: str, root: str) -> str:
    t = TARGETS[target]
    proc = subprocess.run([sys.executable, _tool_path(target)],
                          cwd=root, env=_env_for(root, t["imports_app"]),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{t['invariant_tool']} failed (rc={proc.returncode}):\n{proc.stderr[-1500:]}")
    return proc.stdout


def _cross_check(root: str) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "cross_monolith_graph.py")             # in-tree: tools/ sibling
    if not os.path.isfile(script):
        script = os.path.join(_kit(), "tools", "cross_monolith_graph.py")  # staged-kit fallback
    if not os.path.isfile(script):
        print("  cross-monolith: (cross_monolith_graph.py not found -- skipped)")
        return 0
    proc = subprocess.run([sys.executable, script, "--root", root, "--check"],
                          cwd=root, env=_env_for(root, False),
                          capture_output=True, text=True)
    ok = proc.returncode == 0
    print(f"  cross-monolith --check: {'OK (module-level graph acyclic)' if ok else 'FAIL (a module-level inter-monolith cycle!)'}")
    if not ok:
        sys.stdout.write(proc.stdout[-800:])
    return 0 if ok else 1


def cmd_targets(_a) -> int:
    print("decomposition targets (per-cut invariant dispatch):")
    for name in sorted(TARGETS):
        t = TARGETS[name]
        inv = t["invariant_tool"] or "(surface-lock test)"
        base = t["baseline"] or "-"
        lock = t["surface_lock_test"] or "-"
        print(f"  {name:11s} invariant={inv:24s} baseline={base:26s} lock={lock}")
    print(f"\nkit: {_kit()}")
    return 0


def cmd_baseline(a) -> int:
    target = a.target
    if target not in TARGETS:
        print(f"unknown target '{target}'. known: {', '.join(sorted(TARGETS))}", file=sys.stderr)
        return 2
    t = TARGETS[target]
    if not t["invariant_tool"] or not t["baseline"]:
        print(f"{target}: no snapshot baseline -- its gate is the surface-lock test "
              f"({t['surface_lock_test']}). Freeze its EXPECTED set via the kit's surface generator.")
        return 0
    snap = _run_snapshot(target, a.root)
    dest = os.path.join(a.root, "tests", t["baseline"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(snap)
    sha = hashlib.sha256(snap.encode()).hexdigest()
    n = snap.count("\n")
    print(f"{target}: froze {n} entries -> {dest}\n  sha256 {sha}")
    print("  (update the test's _BASELINE_SHA to this; re-freeze ONLY at phase open/close.)")
    return 0


def cmd_check(a) -> int:
    target = a.target
    if target not in TARGETS:
        print(f"unknown target '{target}'. known: {', '.join(sorted(TARGETS))}", file=sys.stderr)
        return 2
    t = TARGETS[target]
    rc = 0

    if t["invariant_tool"] and t["baseline"]:
        bpath = _baseline_path(target, a.root)
        if not bpath or not os.path.isfile(bpath):
            print(f"{target}: NO frozen baseline ({t['baseline']}) -- run `bd-decomp baseline {target}` at phase open.")
            rc = 1
        else:
            live = _run_snapshot(target, a.root)
            base = open(bpath, encoding="utf-8").read()
            if live == base:
                print(f"{target}: INVARIANT HELD -- {live.count(chr(10))} entries, identical to {os.path.relpath(bpath)}")
            else:
                b, l = set(base.splitlines()), set(live.splitlines())
                removed, added = sorted(b - l), sorted(l - b)
                print(f"{target}: INVARIANT BROKEN vs {os.path.relpath(bpath)} (decomposition must be pure motion):")
                if removed:
                    print(f"  REMOVED ({len(removed)}): " + "; ".join(removed[:6]) + (" ..." if len(removed) > 6 else ""))
                if added:
                    print(f"  ADDED ({len(added)}): " + "; ".join(added[:6]) + (" ..." if len(added) > 6 else ""))
                rc = 1
    elif t["surface_lock_test"]:
        print(f"{target}: gate is the surface-lock test -> band it from the extracted zip:")
        print(f"    bd-band tests/{t['surface_lock_test']} --from-zip <built.zip>")

    if t["cross_check"]:
        rc |= _cross_check(a.root)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bd-decomp", description="per-cut decomposition invariant dispatch")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="work-tree root")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("targets")
    for name in ("baseline", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("target")
    a = ap.parse_args(argv)

    if a.cmd == "targets":
        return cmd_targets(a)
    if a.cmd == "baseline":
        return cmd_baseline(a)
    if a.cmd == "check":
        return cmd_check(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
