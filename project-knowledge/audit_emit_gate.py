#!/usr/bin/env python3
"""audit_emit_gate -- the no-claim-without-a-witness rule, enforced at emit time.

The half of the KB a machine can't regenerate (judgment: constraints, findings,
invariants) can only be kept honest by making each claim SELF-FALSIFYING -- a
claim with no executable witness is the thing that rots silently. This gate
refuses an audit deliverable in which a falsifiable claim has no witness.

SCOPE (cost-tiered, deliberately): a witness is MANDATORY for
  - every constraint (advanced-kb constraints[])
  - every exception   (advanced-kb exceptions[])
  - every finding     (audit findings[])
  - every new_invariant (must have EITHER a guard_test OR a witness ref)
  - every belief whose purpose_vs_behavior is a DRIFT/FALSE (i.e. it asserts a
    bug/risk -- a claim, not a description)
A descriptive belief (purpose_vs_behavior MATCH, no finding) is EXEMPT -- not
every fact needs a probe, only every falsifiable claim.

Usage:
  audit_emit_gate.py --advanced CAP-01_advanced.json --audit AUDIT_CAP-01_*.json
rc!=0 (and a printed list) on any unwitnessed claim. Stdlib only.
"""
import argparse
import json
import sys


def _has_witness(obj):
    w = obj.get("witness")
    return bool(w) and str(w).strip().lower() not in ("", "none", "null")


def check(advanced_path, audit_path):
    violations = []
    adv = json.load(open(advanced_path)) if advanced_path else {}
    aud = json.load(open(audit_path)) if audit_path else {}

    for k in adv.get("constraints", []):
        if not _has_witness(k):
            violations.append(("constraint", k.get("id"), "no witness"))
    for x in adv.get("exceptions", []):
        if not _has_witness(x):
            violations.append(("exception", x.get("id"), "no witness"))
    for b in adv.get("beliefs", []):
        pv = (b.get("purpose_vs_behavior") or "").upper()
        is_claim = ("DRIFT" in pv) or ("FALSE" in pv) or b.get("links_finding")
        if is_claim and not _has_witness(b):
            violations.append(("belief(claim)", b.get("id"),
                               "asserts drift/bug but has no witness"))

    for f in aud.get("findings", []):
        if not _has_witness(f):
            violations.append(("finding", f.get("id"), "no witness/repro"))
    for ni in aud.get("new_invariants", []):
        gt = ni.get("guard_test")
        has_gt = bool(gt) and str(gt).lower() not in ("", "none", "null")
        if not (has_gt or _has_witness(ni)):
            violations.append(("new_invariant", ni.get("id"),
                               "UNGUARDED and no witness ref"))

    n_claims = (len(adv.get("constraints", [])) + len(adv.get("exceptions", []))
                + len(aud.get("findings", [])) + len(aud.get("new_invariants", [])))
    print(f"audit_emit_gate: falsifiable_claims_checked={n_claims} "
          f"violations={len(violations)}")
    for kind, cid, why in violations:
        print(f"  UNWITNESSED {kind} {cid}: {why}")
    if not violations:
        print("  PASS -- every constraint/exception/finding/invariant carries a witness")
    return 0 if not violations else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--advanced")
    ap.add_argument("--audit")
    a = ap.parse_args()
    if not (a.advanced or a.audit):
        ap.error("need --advanced and/or --audit")
    sys.exit(check(a.advanced, a.audit))


if __name__ == "__main__":
    main()
