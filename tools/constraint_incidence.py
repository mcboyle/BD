#!/usr/bin/env python3
"""constraint_incidence -- find bugs by counting, not reading.

Operationalizes the constraint-topology insight: a constraint surface touched in
N places with G guards has (N-G) holes, and you find them by SUBTRACTION. Two
views:

  topology   (from *_advanced.json constraints[])  -- the DECLARED surfaces with
             their incidence/guarded counts. GATE: any surface with holes>0 that
             is not explicitly accepted is a failure (a known-unguarded incidence
             point). Grows as batches add constraints.

  density    (from SECURITY_SURFACE.json)           -- MECHANICAL: rank files by
             count of sink sites whose guard marker is null (path_sinks
             allowlisted=null, secret_sites masked=null, sql_sites parametrized=
             null/fstring=true, subprocess shell=true). High unguarded-sink
             density = where the next hole probably is = audit-next signal.

Usage:
  constraint_incidence.py topology [--gate]
  constraint_incidence.py density  [--top N]
Stdlib only.
"""
import argparse
import glob
import json
import os
from collections import defaultdict

ROOT = os.environ.get("BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
REVIEW = os.environ.get("BD_REVIEW_ROOT", os.path.join(ROOT, "review"))
ADV_GLOB = os.path.join(REVIEW, "*_advanced.json")
SS = os.path.join(REVIEW, "artifacts", "SECURITY_SURFACE.json")


def topology(gate):
    surfaces = []
    for p in sorted(glob.glob(ADV_GLOB)):
        d = json.load(open(p))
        for k in d.get("constraints", []):
            inc = k.get("incidence", [])
            guarded = sum(1 for i in inc if i.get("guarded"))
            holes = len(inc) - guarded
            declared_holes = k.get("holes", holes)
            surfaces.append({
                "id": k["id"], "batch": d.get("batch"),
                "incidence": len(inc), "guarded": guarded,
                "holes": holes, "declared_holes": declared_holes,
                "witness": k.get("witness"),
                "unguarded_points": [i["at"] for i in inc if not i.get("guarded")]})
    surfaces.sort(key=lambda s: (-s["holes"], -s["incidence"]))
    print("CONSTRAINT TOPOLOGY (declared surfaces, ranked by holes then incidence)")
    print("=" * 70)
    bad = []
    for s in surfaces:
        flag = "  <-- HOLE" if s["holes"] > s["declared_holes"] else ""
        print(f"  {s['id']} [{s['batch']}]: incidence={s['incidence']} "
              f"guarded={s['guarded']} holes={s['holes']} "
              f"(declared {s['declared_holes']}){flag}")
        if s["unguarded_points"]:
            for up in s["unguarded_points"]:
                print(f"      UNGUARDED: {up}")
        if s["holes"] > s["declared_holes"]:
            bad.append(s["id"])
    print("=" * 70)
    print(f"{len(surfaces)} surfaces; {sum(s['holes'] for s in surfaces)} total holes "
          f"({sum(s['declared_holes'] for s in surfaces)} declared-accepted)")
    if gate:
        if bad:
            print(f"GATE FAIL: undeclared holes in {bad}")
            return 1
        print("GATE PASS: every incidence hole is declared-accepted")
    return 0


def density(top):
    ss = json.load(open(SS))
    per_file = defaultdict(lambda: defaultdict(int))

    def fileof(at):
        return at.rsplit(":", 1)[0] if ":" in at else at
    for x in ss.get("path_sinks", []):
        if x.get("allowlisted") in (None, False):
            per_file[fileof(x["at"])]["path_unallowlisted"] += 1
    for x in ss.get("secret_sites", []):
        if x.get("masked") in (None, False):
            per_file[fileof(x["at"])]["secret_unmasked"] += 1
    for x in ss.get("sql_sites", []):
        if x.get("parametrized") in (None, False) or x.get("fstring"):
            per_file[fileof(x["at"])]["sql_unparam"] += 1
    for x in ss.get("subprocess_sites", []):
        if x.get("shell"):
            per_file[fileof(x["at"])]["subprocess_shell"] += 1

    ranked = sorted(per_file.items(),
                    key=lambda kv: -sum(kv[1].values()))
    print("UNGUARDED-SINK DENSITY (mechanical; high = audit-next / likely hole)")
    print("=" * 70)
    print("  NOTE: 'unguarded' here = the scanner left the guard marker null; many")
    print("        resolve to FP on read (the parameterized dynamic-WHERE pattern,")
    print("        a caller-controlled path). The RANKING is the signal, not a verdict.")
    print("=" * 70)
    for f, cats in ranked[:top]:
        total = sum(cats.values())
        detail = " ".join(f"{k}={v}" for k, v in sorted(cats.items()))
        print(f"  {total:4d}  {f}   [{detail}]")
    print("=" * 70)
    print(f"{len(ranked)} files carry >=1 unguarded-marked sink; showing top {min(top,len(ranked))}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("topology"); t.add_argument("--gate", action="store_true")
    d = sub.add_parser("density"); d.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    if a.cmd == "topology":
        raise SystemExit(topology(a.gate))
    raise SystemExit(density(a.top))


if __name__ == "__main__":
    main()
