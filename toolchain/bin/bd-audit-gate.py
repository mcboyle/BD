#!/usr/bin/env python3
"""bd-audit-gate -- the composite code-intelligence gate (stub).

Runs, in order, and fails (rc!=0) if any sub-gate fails:
  1. defect_patterns --check   (the project-native linter validates on its corpus)
  2. invariants --check        (no phantom guards; reports UNGUARDED invariants)
  3. review_state --check      (ledger staleness: reviewed files whose sha drifted
                                auto-flip to unreviewed; no finding on an absent file)

Intended to run each cut alongside the release band. Extensible: add the
fuzz_harness/differential_oracle replays here as they land. stdlib + subprocess.

Usage: python3 bd-audit-gate.py [--root TREE] [--artifacts DIR]
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(name, cmd):
    print(f"\n=== {name} ===")
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/home/claude/work")
    ap.add_argument("--artifacts", default="/home/claude/review/artifacts")
    ap.add_argument("--corpus", default="/home/claude/review/regression_corpus")
    a = ap.parse_args()
    py = sys.executable

    rc = 0
    rc |= run("defect_patterns --check",
              [py, os.path.join(HERE, "defect_patterns.py"), "--check",
               "--corpus", a.corpus]) and 1
    rc |= run("invariants --check",
              [py, os.path.join(HERE, "invariants.py"), "--check",
               "--out", os.path.join(a.artifacts, "INVARIANTS.json"),
               "--root", a.root]) and 2
    rc |= run("review_state --check",
              [py, os.path.join(HERE, "seed_review_state.py"), "--check",
               "--out", os.path.join(a.artifacts, "REVIEW_STATE.json"),
               "--db", os.path.join(a.artifacts, "KNOWLEDGE_GRAPH.db"),
               "--root", a.root]) and 4
    # Sub-gate 4: witnesses -- the KB announces its own lies (a red claim-witness
    # means a belief no longer matches behavior). Runs every cut.
    import glob as _glob
    # in-tree witnesses (promoted @533) first, sandbox fallback second
    _intree_w = os.path.join(HERE, "audit", "witnesses")
    _wdir = _intree_w if os.path.isdir(_intree_w) else "/home/claude/review/witnesses"
    if _glob.glob(os.path.join(_wdir, "*_witnesses.py")):
        rc |= run("witnesses",
                  [py, os.path.join(HERE, "run_witnesses.py")]) and 8
    # Sub-gate 5: emit gate -- no falsifiable claim without a witness (validates
    # the audit deliverables present this session). In-tree docs/audit/ first.
    _intree_docs = os.path.join(HERE, "..", "docs", "audit")
    _adv_dirs = [d for d in (_intree_docs, "/home/claude/review") if os.path.isdir(d)]
    _seen_adv = set()
    for _dir in _adv_dirs:
        for adv in sorted(_glob.glob(os.path.join(_dir, "*_advanced.json"))):
            base = os.path.basename(adv)
            if base in _seen_adv:
                continue
            _seen_adv.add(base)
            # resolve the audit deliverable next to the sidecar by convention:
            # <BATCH>_advanced.json -> AUDIT_<BATCH>_v3_66_*.json in the same dir
            batch = base.replace("_advanced.json", "")
            aud_matches = sorted(_glob.glob(
                os.path.join(_dir, f"AUDIT_{batch}_v3_66_*.json")))
            args = [py, os.path.join(HERE, "audit_emit_gate.py"), "--advanced", adv]
            if aud_matches:
                args += ["--audit", aud_matches[0]]
            rc |= run(f"emit_gate({base})", args) and 16
    # Sub-gate 6: constraint topology -- no undeclared unguarded incidence point.
    ci = os.path.join(HERE, "constraint_incidence.py")
    if os.path.exists(ci) and _seen_adv:
        rc |= run("constraint_incidence topology",
                  [py, ci, "topology", "--gate"]) and 32
    # Sub-gate 7: consumer-agreement -- every producer guards its shared symbol.
    ca = os.path.join(HERE, "consumer_agreement.py")
    cj = os.path.join(a.artifacts, "CONTRACTS.json")
    if os.path.exists(ca) and os.path.exists(cj):
        rc |= run("consumer_agreement", [py, ca, "--contracts", cj, "--gate"]) and 64

    print("\n" + "=" * 60)
    if rc == 0:
        print("bd-audit-gate: PASS — all sub-gates green")
    else:
        print(f"bd-audit-gate: FAIL — sub-gate bitmask={rc} "
              f"(1=defect_patterns 2=invariants 4=review_state)")
    sys.exit(0 if rc == 0 else 1)


def selftest():
    """DELEGATE: wraps bd-audit. v3.66.799 -- context-aware candidates so
    ONE canonical body serves both the shipped tree and the sandbox
    bdsuite: script-dir first, then PATH, then the sandbox bin. Where
    bd-audit genuinely does not exist (stash has no bdsuite), this FAILS
    honestly -- a selftest that cannot verify must not report success."""
    import os as _o
    import shutil as _sh
    _here = _o.path.dirname(_o.path.realpath(__file__))
    cands = [_o.path.join(_here, "bd-audit"),
             _sh.which("bd-audit") or "",
             "/home/claude/bin/bd-audit"]
    hit = next((p for p in cands if p and _o.path.isfile(p)), None)
    print(("PASS" if hit else "FAIL") +
          "  delegation target present: bd-audit (%s)"
          % (hit or "NOT FOUND in script dir, PATH, or sandbox bin"))
    print("SELFTEST PASS" if hit else "SELFTEST FAIL")
    return 0 if hit else 1


if __name__ == "__main__":
    import sys as _s
    if "--selftest" in _s.argv:
        raise SystemExit(selftest())
    main()
