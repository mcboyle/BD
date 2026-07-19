#!/usr/bin/env python3
"""DECOMP-R0 — import-graph regression gate.

Freezes the intended internal import-edge set of the product (bulk_downloader/ +
tools/) and asserts a decomposition cut adds no edge outside it. The complement
to the surface-lock / route_map invariants: those prove *nothing left*, this
proves *nothing new crept in* (accidental coupling, lazy-accessor sprawl — H-14).

  python3 tools/decomp/import_graph_gate.py --check    # exit 1 on any NEW edge
  python3 tools/decomp/import_graph_gate.py --update    # re-freeze the baseline
  python3 tools/decomp/import_graph_gate.py --list      # print edge counts

A NEW edge present in the live graph but not the baseline FAILS --check. An edge
removed by a cut (coupling deleted — a good thing) is reported but does not fail;
re-freeze with --update to keep the baseline tight. Declaring an intended new
edge = running --update in the same cut, the way a guard-SHA change is declared.

Design note: this tool deliberately loads tools/dependency_graph.py *by path*
(not `from tools.dependency_graph import build`) so the gate contributes no
static import edge to the very graph it measures — the baseline stays the pure
product surface, and DEPENDENCY_GRAPH.json is unperturbed by adding this file.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_BASELINE_VERSION = "1"


def _repo_root() -> Path:
    # tools/decomp/import_graph_gate.py -> repo root is two parents up.
    return Path(__file__).resolve().parents[2]


def _baseline_path(root: Path | None = None) -> Path:
    root = root or _repo_root()
    return root / "tools" / "decomp" / "import_graph_baseline.json"


def _load_dependency_graph(root: Path | None = None):
    """Load tools/dependency_graph.py by path (no static import edge)."""
    root = root or _repo_root()
    dep = root / "tools" / "dependency_graph.py"
    if not dep.exists():
        raise FileNotFoundError(f"{dep} not found")
    spec = importlib.util.spec_from_file_location("_r0_dependency_graph", dep)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def current_out_map(root: Path | None = None) -> dict:
    """The live directed internal edge map {src: [dst, ...]} (== package.out)."""
    root = root or _repo_root()
    dep = _load_dependency_graph(root)
    g = dep.build(root)
    return {k: sorted(v) for k, v in g["package"]["out"].items()}


def current_edge_set(root: Path | None = None) -> set[tuple[str, str]]:
    return {(s, d) for s, lst in current_out_map(root).items() for d in lst}


def load_baseline(root: Path | None = None) -> dict:
    p = _baseline_path(root)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} missing — run `python3 tools/decomp/import_graph_gate.py --update`."
        )
    return json.loads(p.read_text(encoding="utf-8"))


def baseline_edge_set(base: dict) -> set[tuple[str, str]]:
    return {(s, d) for s, lst in base["edges"].items() for d in lst}


def compare_edges(
    baseline: set[tuple[str, str]], current: set[tuple[str, str]]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (new, removed) — new = current\\baseline, removed = baseline\\current."""
    new = sorted(current - baseline)
    removed = sorted(baseline - current)
    return new, removed


def check(root: Path | None = None) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    root = root or _repo_root()
    base = baseline_edge_set(load_baseline(root))
    return compare_edges(base, current_edge_set(root))


def _serialize(out_map: dict) -> str:
    edges = {k: sorted(v) for k, v in out_map.items()}
    obj = {
        "baseline_version": _BASELINE_VERSION,
        "edge_count": sum(len(v) for v in edges.values()),
        "edges": edges,
    }
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def write_baseline(root: Path | None = None) -> int:
    root = root or _repo_root()
    out_map = current_out_map(root)
    content = _serialize(out_map)
    _baseline_path(root).write_text(content, encoding="utf-8")
    return sum(len(v) for v in out_map.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true",
                   help="fail (exit 1) if the live graph adds any edge outside the baseline")
    g.add_argument("--update", action="store_true",
                   help="re-freeze the baseline from the live graph (declare intended edges)")
    g.add_argument("--list", action="store_true",
                   help="print baseline + live edge counts")
    args = ap.parse_args(argv)
    root = _repo_root()

    if args.update:
        n = write_baseline(root)
        print(f"baseline re-frozen: {n} edges -> {_baseline_path(root).relative_to(root)}")
        return 0

    if args.list:
        cur = current_out_map(root)
        cur_n = sum(len(v) for v in cur.values())
        try:
            base = load_baseline(root)
            print(f"baseline: {base['edge_count']} edges | live: {cur_n} edges")
        except FileNotFoundError as e:
            print(str(e))
            return 1
        return 0

    # default: --check
    try:
        new, removed = check(root)
    except FileNotFoundError as e:
        print(str(e))
        return 1
    if removed:
        print(f"note: {len(removed)} baseline edge(s) no longer present "
              f"(coupling removed — re-freeze with --update to tighten):")
        for s, d in removed:
            print(f"  - {s} -> {d}")
    if new:
        print(f"FAIL: {len(new)} NEW import edge(s) outside the frozen baseline:")
        for s, d in new:
            print(f"  + {s} -> {d}")
        print("If intended, re-freeze in the SAME cut: "
              "`python3 tools/decomp/import_graph_gate.py --update`")
        return 1
    print(f"PASS: no new import edges (baseline holds, "
          f"{sum(len(v) for v in current_out_map(root).values())} edges).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
