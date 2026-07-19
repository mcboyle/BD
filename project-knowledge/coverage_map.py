#!/usr/bin/env python3
"""coverage_map -- turn coverage.json into per-function coverage_gaps[], free.

The F0001 / VR bug-class is "green suite, untested path": a behavior with no test,
invisible until it bites. coverage_map makes it MECHANICAL. It ingests a coverage.py
JSON (produced on stash -- `coverage run` hangs in-sandbox, so the report comes from
the on-stash suite), intersects each function's line span (from KNOWLEDGE_GRAPH.db)
with the file's missing_lines, and emits a coverage_gap per function that is wholly
or partly untested. A wholly-untested function in an audited file is exactly the
F0001 shape and becomes a test-backlog item instead of a lucky later catch.

Usage:
  coverage_map.py --coverage coverage.json [--files f1,f2] [--merge-advanced ADV.json]
Emits COVERAGE_GAPS.json + a summary; with --merge-advanced, writes the gaps into
the matching beliefs' coverage_gaps[]. Stdlib only.
"""
import argparse
import json
import os
import sqlite3

DB = "/home/claude/review/artifacts/KNOWLEDGE_GRAPH.db"
OUT = "/home/claude/review/artifacts/COVERAGE_GAPS.json"


def _fn_spans(path):
    cx = sqlite3.connect(DB)
    rows = cx.execute(
        "SELECT qualname, span FROM nodes WHERE kind='function' AND path=?",
        (path,)).fetchall()
    spans = []
    for q, s in rows:
        if s and "-" in s:
            a, b = s.split("-", 1)
            try:
                spans.append((q, int(a), int(b)))
            except ValueError:
                pass
    return sorted(spans, key=lambda x: x[1])


def run(coverage_path, only_files, merge_adv):
    cov = json.load(open(coverage_path))
    files = cov.get("files", {})
    if only_files:
        files = {k: v for k, v in files.items() if k in only_files}
    gaps = []
    for path, fc in sorted(files.items()):
        missing = set(fc.get("missing_lines", []))
        if not missing:
            continue
        for q, a, b in _fn_spans(path):
            fn_lines = set(range(a, b + 1))
            uncov = sorted(fn_lines & missing)
            if not uncov:
                continue
            frac = len(uncov) / max(1, len(fn_lines))
            gaps.append({
                "file": path, "function": q, "span": f"{a}-{b}",
                "uncovered_lines": uncov,
                "uncovered_frac": round(frac, 2),
                "untested": "WHOLLY" if frac > 0.85 else "partial",
                "shape": "F0001/VR (green-suite-untested-path)" if frac > 0.85 else "partial-path"})
    gaps.sort(key=lambda g: (-g["uncovered_frac"], g["file"]))
    json.dump({"coverage_source": coverage_path, "gaps": gaps,
               "total": len(gaps)}, open(OUT, "w"), indent=1)
    wholly = [g for g in gaps if g["untested"] == "WHOLLY"]
    print(f"coverage_map: {len(gaps)} function-level gaps "
          f"({len(wholly)} WHOLLY-untested = F0001 shape) -> {OUT}")
    for g in gaps:
        print(f"  [{g['untested']:7s}] {g['file']}::{g['function']} "
              f"({g['span']}) uncovered={g['uncovered_frac']:.0%}")

    if merge_adv and os.path.exists(merge_adv):
        adv = json.load(open(merge_adv))
        by_file = {}
        for g in gaps:
            by_file.setdefault(g["file"], []).append(
                {"behavior": f"{g['function']} ({g['span']})", "tested": False,
                 "shape": g["shape"], "uncovered_lines": g["uncovered_lines"]})
        merged = 0
        for b in adv.get("beliefs", []):
            unit = b.get("unit", "")
            for f, gg in by_file.items():
                if f.endswith(unit) or unit.endswith(f.split("/")[-1]):
                    b.setdefault("coverage_gaps", [])
                    have = {x.get("behavior") for x in b["coverage_gaps"]}
                    for x in gg:
                        if x["behavior"] not in have:
                            b["coverage_gaps"].append(x); merged += 1
        json.dump(adv, open(merge_adv, "w"), indent=2)
        print(f"  merged {merged} gaps into {merge_adv} beliefs' coverage_gaps[]")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--files", default="")
    ap.add_argument("--merge-advanced")
    a = ap.parse_args()
    only = set(x.strip() for x in a.files.split(",") if x.strip())
    raise SystemExit(run(a.coverage, only, a.merge_advanced))


if __name__ == "__main__":
    main()
