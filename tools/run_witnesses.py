#!/usr/bin/env python3
"""run_witnesses -- discover + run every witness suite; the Tier-2 'KB announces
its own lies' gate. Wired into bd-audit-gate to run on every cut.

Discovers the repository's review/witnesses suites, imports each, runs every
@w-registered witness, and aggregates. A witness whose claim id starts with 'F-'
is a FINDING-REPRO (green = the vuln still reproduces -- informational until the
fix lands, at which point it flips red and the finding is updated). Every other
witness is a CLAIM-WITNESS and MUST be green, or a belief in the KB has gone
stale relative to behavior.

rc!=0 iff any CLAIM-WITNESS is red. Stdlib only; run under `bd` (witnesses import
bulk_downloader).
"""
import glob
import importlib.util
import os
import sys

# Witness suites ship in-tree at tools/audit/witnesses/ (promoted @533); the
# operator review/ location is a fallback for pre-promotion pack runs.
_HERE = os.path.dirname(os.path.abspath(__file__))
_INTREE_WDIR = os.path.join(_HERE, "audit", "witnesses")
_ROOT = os.environ.get("BD_WORK", os.path.dirname(_HERE))
_REVIEW_WDIR = os.path.join(
    os.environ.get("BD_REVIEW_ROOT", os.path.join(_ROOT, "review")), "witnesses"
)
WDIR = _INTREE_WDIR if os.path.isdir(_INTREE_WDIR) else _REVIEW_WDIR


def _load_suite(path):
    spec = importlib.util.spec_from_file_location(
        os.path.basename(path)[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    # reset the module-level RESULTS each import
    spec.loader.exec_module(mod)
    return mod


def main():
    suites = sorted(glob.glob(os.path.join(WDIR, "*_witnesses.py")))
    if not suites:
        print("run_witnesses: no witness suites found")
        return 0
    claim_red, claim_total, repro_green, repro_total = [], 0, 0, 0
    print("run_witnesses")
    print("=" * 60)
    for s in suites:
        mod = _load_suite(s)
        for entry in getattr(mod, "RESULTS", []):
            # normalize BOTH schemas: dict {id,kind,ok,flips_to,detail} (standard)
            # and the legacy 3-tuple (id, ok, detail).
            if isinstance(entry, dict):
                cid = entry.get("id"); ok = entry.get("ok")
                kind = entry.get("kind") or ("finding" if str(cid).startswith("F-") else "claim")
                detail = entry.get("detail", "")
            else:
                cid, ok, detail = entry
                kind = "finding" if str(cid).startswith("F-") else "claim"
            # findings/finding_repro are informational (green == still reproduces);
            # everything else (assurance/fp_confirmation/constraint/invariant/claim)
            # is a claim-witness that MUST demonstrate (green).
            is_repro = kind in ("finding", "finding_repro")
            if is_repro:
                repro_total += 1
                if ok:
                    repro_green += 1
                tag = "REPRO" if ok else "repro-cleared"
            else:
                claim_total += 1
                if not ok:
                    claim_red.append((cid, detail))
                tag = "ok" if ok else "RED"
            print(f"  [{tag:14s}] {os.path.basename(s)}::{cid} ({kind})")
    print("=" * 60)
    print(f"claim-witnesses: {claim_total - len(claim_red)}/{claim_total} green | "
          f"finding-repros: {repro_green}/{repro_total} still reproducing")
    if claim_red:
        print("  STALE CLAIMS (a KB belief no longer matches behavior):")
        for cid, detail in claim_red:
            print(f"    {cid}: {detail}")
    return 0 if not claim_red else 1


if __name__ == "__main__":
    sys.exit(main())
