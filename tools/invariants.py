#!/usr/bin/env python3
"""invariants -- the gated invariant registry (SCHEMAS §7).

Permanent, executable rules promoted from confirmed bug-classes + DANGER_MAP.
Each: statement / at / why / guard_test / status (GUARDED|UNGUARDED) / dp_class.
Seeded from the verify-pass defects (their fix is the invariant) + the 531/532
CSRF work.

Gate (--check):
  * every GUARDED invariant's guard_test file must exist in the tree (else the
    guard is a phantom -> rc!=0)
  * every UNGUARDED invariant is reported (audit must write a RED guard for it)

Usage: python3 invariants.py [--out FILE] [--root TREE]   |   --check
"""
import argparse
import json
import os
import sys

SCHEMA = 1
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
CANONICAL = os.path.join(ROOT, "INVARIANTS.json")


def load(path):
    def reject_duplicates(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON key: {key}")
            out[key] = value
        return out
    payload = json.load(open(path), object_pairs_hook=reject_duplicates)
    if payload.get("schema") != SCHEMA or not isinstance(payload.get("invariants"), dict):
        raise ValueError("invalid INVARIANTS.json authority")
    return payload


def build(out, source):
    payload = load(source)
    with open(out, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    invariants = payload["invariants"]
    guarded = sum(1 for v in invariants.values() if v["status"] == "GUARDED")
    return {"total": len(invariants), "guarded": guarded,
            "unguarded": len(invariants) - guarded}


def check(out, root):
    if not os.path.exists(out):
        print("invariants --check: INVARIANTS.json missing (run without --check first)")
        return 1
    inv = load(out)["invariants"]
    phantom = []
    unguarded = []
    for iid, v in sorted(inv.items()):
        if v["status"] == "GUARDED":
            gt = (v.get("guard_test") or "").split("::")[0]
            if gt and not os.path.exists(os.path.join(root, gt)):
                phantom.append((iid, gt))
        else:
            unguarded.append(iid)
    print(f"invariants --check: total={len(inv)} guarded={sum(1 for v in inv.values() if v['status']=='GUARDED')} "
          f"phantom_guard={len(phantom)} unguarded={len(unguarded)}")
    if phantom:
        print("  GUARDED invariants whose guard_test is absent (phantom):")
        for iid, gt in phantom:
            print(f"    {iid} -> {gt}")
    if unguarded:
        print("  UNGUARDED (audit must add a RED guard):", unguarded)
    return 0 if not phantom and not unguarded else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=CANONICAL)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--source", default=CANONICAL)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        sys.exit(check(a.out, a.root))
    print("invariants:", json.dumps(build(a.out, a.source)))


if __name__ == "__main__":
    main()
