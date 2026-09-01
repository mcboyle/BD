#!/usr/bin/env python3
"""bd-edgecheck -- classify a cut's new import edges, and declare the routine ones.

WHY THIS EXISTS. tests/test_import_graph_no_new_edges.py refused four separate
cuts on 2026-08-31, and every refusal was the same shape: a NEW TEST FILE
importing the module it tests. Each cost a full CI round trip to discover, and
none was the hazard the gate was written for.

The gate's own docstring names that hazard: "a cut that quietly ADDS an
inter-module import edge it was not supposed to -- the accidental-coupling /
lazy-accessor-sprawl class". Measured over the 4,141-edge baseline at
v3.66.1371:

    test -> product     2443   59%
    product -> product  1698   41%
    test -> test           0
    product -> test        0

ALL OF THE HAZARD LIVES IN THE 41%. A test importing its subject is what a
test IS. So this tool declares test->product edges without ceremony and
REFUSES to declare a product->product edge silently -- that one is the gate
doing its job, and it wants a human.

It does not weaken the gate: the baseline still freezes every edge, and the
gate still refuses anything undeclared. This only automates the half of the
declaration that carries no signal, and makes the other half louder.

  bd-edgecheck.py [--work DIR] [--declare] [--allow-coupling REASON] [--json]

Exit 0 = no new edges, or new edges declared. 1 = new product->product edges
that were NOT declared (pass --allow-coupling with a reason to declare them).
2 = CANNOT-EVALUATE: no baseline, unreadable gate, or the gate's own refusal.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _classify(src: str, dst: str) -> str:
    s, d = src.startswith("tests/"), dst.startswith("tests/")
    if s and not d:
        return "test->product"
    if not s and not d:
        return "product->product"
    if not s and d:
        return "product->test"
    return "test->test"


def _edges(obj) -> dict:
    if isinstance(obj, dict) and "edges" in obj:
        return obj["edges"]
    return {k: v for k, v in obj.items() if isinstance(v, list)}


def _live_graph(work: Path):
    """Ask the gate itself what the live graph is -- never re-implement it."""
    gate = work / "tools/decomp/import_graph_gate.py"
    if not gate.is_file():
        return None, f"no import graph gate at {gate}"
    py = work / "venv/bin/python"
    if not py.is_file():
        return None, f"no interpreter at {py}"
    p = subprocess.run([str(py), str(gate), "--check", "--json"],
                       cwd=str(work), capture_output=True, text=True)
    # --check exits nonzero when edges are new; that is the case we care about.
    out = p.stdout.strip()
    if out:
        try:
            return json.loads(out), None
        except json.JSONDecodeError:
            pass
    return "TEXT", (p.stdout + p.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=".")
    ap.add_argument("--declare", action="store_true",
                    help="re-freeze the baseline for the new edges")
    ap.add_argument("--allow-coupling", metavar="REASON",
                    help="also declare product->product edges, with a stated reason")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    work = Path(a.work).resolve()

    baseline = work / "tools/decomp/import_graph_baseline.json"
    if not baseline.is_file():
        print(f"CANNOT-EVALUATE: no baseline at {baseline}", file=sys.stderr)
        return 2

    before = _edges(json.loads(baseline.read_text()))

    gate = work / "tools/decomp/import_graph_gate.py"
    py = work / "venv/bin/python"
    if not gate.is_file() or not py.is_file():
        print("CANNOT-EVALUATE: gate or interpreter missing", file=sys.stderr)
        return 2

    # THE GATE IS THE AUTHORITY ON WHAT THE LIVE GRAPH IS. Re-deriving it here
    # would create a second measurement that can disagree with the one that
    # actually gates the merge -- the exact defect shape this repo keeps
    # finding. So: snapshot, let the gate re-freeze into a scratch copy, and
    # diff the two baselines.
    import shutil, tempfile
    with tempfile.TemporaryDirectory(prefix="bd_edgecheck_") as td:
        saved = Path(td) / "baseline.json"
        shutil.copy2(baseline, saved)
        p = subprocess.run([str(py), str(gate), "--update"],
                           cwd=str(work), capture_output=True, text=True)
        if p.returncode != 0:
            shutil.copy2(saved, baseline)
            print(f"CANNOT-EVALUATE: the gate refused its own --update:\n"
                  f"{(p.stdout + p.stderr).strip()[:600]}", file=sys.stderr)
            return 2
        after = _edges(json.loads(baseline.read_text()))
        if not a.declare:
            shutil.copy2(saved, baseline)   # leave the tree exactly as found

    new = []
    for src, dsts in after.items():
        for dst in dsts:
            if dst not in before.get(src, []):
                new.append((src, dst, _classify(src, dst)))

    routine = [e for e in new if e[2] == "test->product"]
    coupling = [e for e in new if e[2] == "product->product"]
    weird = [e for e in new if e[2] in ("product->test", "test->test")]

    report = {"work": str(work), "new": len(new),
              "test_to_product": len(routine),
              "product_to_product": len(coupling),
              "other": [f"{s} -> {d} ({c})" for s, d, c in weird],
              "declared": bool(a.declare)}
    if a.json:
        print(json.dumps(report, indent=1))

    if not new:
        if not a.json:
            print("EDGECHECK OK -- no new import edges")
        return 0

    if not a.json:
        print(f"new edges: {len(new)}  "
              f"(test->product {len(routine)}, product->product {len(coupling)})")
        for s, d, c in routine:
            print(f"  routine   {s} -> {d}")
        for s, d, c in coupling:
            print(f"  COUPLING  {s} -> {d}")
        for s, d, c in weird:
            print(f"  UNUSUAL   {s} -> {d}  ({c})")

    if weird:
        # A product file importing from tests/ has never existed in this tree
        # (measured 0 at v3.66.1371). It is not routine and not ordinary
        # coupling; refuse regardless of flags.
        print("\nREFUSED: an edge crosses into tests/ from product code, or "
              "between test files. Neither has ever existed in this baseline. "
              "Resolve it by hand.", file=sys.stderr)
        return 1

    if coupling and not a.allow_coupling:
        if a.declare:
            print("\n(baseline left re-frozen -- revert it if you did not want "
                  "these edges: git checkout -- tools/decomp/import_graph_baseline.json)")
        print(f"\nREFUSED: {len(coupling)} product->product edge(s). This is the "
              "hazard the gate exists for -- accidental coupling between "
              "modules. If the coupling is intended, re-run with "
              "--allow-coupling '<why>' and say why in the cut.", file=sys.stderr)
        return 1

    if a.declare:
        why = f" ({a.allow_coupling})" if a.allow_coupling else ""
        print(f"\nDECLARED {len(new)} edge(s){why}. Inspect the diff before "
              f"committing:\n  git -C {work} diff tools/decomp/import_graph_baseline.json")
    else:
        print("\nnot declared (dry run). Re-run with --declare to re-freeze.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
