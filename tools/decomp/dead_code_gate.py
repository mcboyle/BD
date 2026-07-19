#!/usr/bin/env python3
"""tools/decomp/dead_code_gate.py -- MNT-1: continuous dead-code gate.

Route/nav reachability is already gated (nav_reachability + test_cockpit_appearance).
This adds the complementary FUNCTION-level signal: top-level functions defined in
the package that are never referenced anywhere in the source, tools, or tests.

It is deliberately ADVISORY (exit 0 by default): Python's dynamic dispatch,
getattr-by-string, decorator registration, and plugin hooks make a zero-false-
positive static dead-code proof impossible, so the gate REPORTS candidates for a
human to confirm rather than failing a build. `--strict` flips it to exit 1 when
any candidate remains, for a repo that has driven the list to zero and wants to
keep it there.

Heuristics that keep the signal honest (skipped, never flagged):
  * dunder methods and any name starting with `_` used as a private helper is
    still checked, but names in a module's __all__ are treated as public exports;
  * decorated functions (routes/handlers/properties/registrations) are skipped --
    they are reached dynamically, not by a textual call;
  * a name referenced ANYWHERE (import, call, attribute load) outside its own def
    is reachable.

Usage:
  python3 tools/decomp/dead_code_gate.py            # advisory report (exit 0)
  python3 tools/decomp/dead_code_gate.py --json     # machine-readable
  python3 tools/decomp/dead_code_gate.py --strict   # exit 1 if any candidate
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _py_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        parts = set(p.parts)
        if "node_modules" in parts or "__pycache__" in parts:
            continue
        yield p


def _collect_defs(def_root: Path):
    """Top-level function defs in def_root. Returns {name: [(file, lineno), ...]}.
    Skips dunders and decorated functions (dynamically reached)."""
    defs: dict = {}
    for p in _py_files(def_root):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        exported = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "__all__" for t in node.targets):
                try:
                    exported = set(ast.literal_eval(node.value))
                except Exception:
                    exported = set()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if name.startswith("__") and name.endswith("__"):
                continue
            if node.decorator_list:      # route/handler/property/registration
                continue
            if name in exported:         # public export -> reachable by contract
                continue
            defs.setdefault(name, []).append((str(p), node.lineno))
    return defs


def _collect_refs(ref_roots):
    """Every referenced NAME across ref_roots: call targets, attribute loads,
    from-imports. Returns (ref_names:set, def_sites:set[(file,lineno)]).

    def_sites lets us subtract a function's OWN definition line so a def isn't
    counted as a reference to itself."""
    refs: set = set()
    def_sites: set = set()
    for root in ref_roots:
        for p in _py_files(Path(root)):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    refs.add(node.id)
                elif isinstance(node, ast.Attribute):
                    refs.add(node.attr)
                elif isinstance(node, ast.ImportFrom):
                    for a in node.names:
                        refs.add(a.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    def_sites.add((str(p), node.lineno))
    return refs, def_sites


def scan(def_root, ref_roots=None):
    """Return {candidates, counts}. candidate = a defined function whose name is
    referenced nowhere outside its own definition."""
    def_root = Path(def_root)
    if ref_roots is None:
        root = _repo_root()
        ref_roots = [root / "bulk_downloader", root / "tools", root / "tests"]
    defs = _collect_defs(def_root)
    refs, _ = _collect_refs(ref_roots)
    candidates = []
    for name, sites in sorted(defs.items()):
        if name not in refs:
            for f, ln in sites:
                candidates.append({"name": name, "file": f, "lineno": ln})
    return {"candidates": candidates,
            "counts": {"defined": len(defs), "dead": len(candidates)}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="MNT-1 advisory dead-code gate")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any candidate remains (default: advisory)")
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    root = Path(a.root) if a.root else _repo_root()
    result = scan(root / "bulk_downloader",
                  [root / "bulk_downloader", root / "tools", root / "tests"])
    if a.json:
        print(json.dumps(result, indent=2))
    else:
        c = result["counts"]
        print(f"dead-code gate: {c['dead']} candidate(s) / {c['defined']} defs")
        for cand in result["candidates"]:
            print(f"  ? {cand['name']}  {cand['file']}:{cand['lineno']}")
        if not result["candidates"]:
            print("  (none)")
    if a.strict and result["candidates"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
