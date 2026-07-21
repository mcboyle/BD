#!/usr/bin/env python3
"""Assign the four portability classes from the harness signals (judged rubric).

RUNS            executes AND produces correct output about THIS tree (possibly
                needing --tree .). Never assigned on exit 0 alone.
RUNS-DEGRADED   executes (often exit 0) but on an empty/wrong/sandbox denominator,
                or asserts a verdict instead of deriving it. The class the ledger
                exists to surface.
SANDBOX-BOUND   needs /home/claude, prestaged PYTHONPATH, or mock services; no
                working tree-root override.
UNKNOWN         could not be determined (mutating/heavy so not executed; or
                errored/timed out with no clean run).
"""
from __future__ import annotations
import json, re, sys

recs = [json.loads(l) for l in open("/tmp/toolchain_signals.jsonl")]

GIT_REDUNDANT = re.compile(r'-(since|snapshot|zip|diff-zip|tree-snapshot|checkpoint|restore-zip)\b')

def has(sig, *k):
    sig = sig or ""
    return all(x in sig for x in k)

def classify(r):
    name = r["tool"]
    ne, ns = r.get("noarg_exit"), r.get("noarg_sig", "")
    te, ts = r.get("tree_exit"), r.get("tree_sig")
    # mutating/heavy tools were not executed -> read the hardcode signal.
    if r["mutating_by_name"]:
        if r["hardcodes_sandbox_path"]:
            return "SANDBOX-BOUND", "mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run"
        return "UNKNOWN", "mutating/heavy; not executed against the clone (would need a real run to confirm)"
    # RUNS via --tree: pointed at this tree, clean, output about this tree.
    if te == 0 and ts and "this-tree" in ts and "error/absent" not in ts:
        return "RUNS", "clean with --tree pointed at the clone; output is about this tree"
    # RUNS no-arg: clean, this-tree, not empty/sandbox.
    if ne == 0 and "this-tree" in ns and "empty" not in ns and "error/absent" not in ns and "sandbox-path" not in ns:
        return "RUNS", "runs clean with no args; output is about this tree"
    if ne == 0 and ns == "counts":
        return "RUNS", "runs clean with no args; produced counts (non-empty denominator)"
    # RUNS-DEGRADED: exit 0 but empty or sandbox-path denominator, no rescue.
    if ne == 0 and ("empty" in ns or "sandbox-path" in ns):
        why = "scanned a sandbox path" if "sandbox-path" in ns else "empty output"
        if te == 0 and ts and "this-tree" in ts and "error/absent" not in ts:
            return "RUNS", "no-arg defaults to a sandbox path but --tree rescues it (correct output about this tree)"
        return "RUNS-DEGRADED", f"exit 0 but {why} -- reports on an absent/wrong denominator, not this tree"
    # SANDBOX-BOUND: fails referencing a sandbox path, no override rescue.
    if (isinstance(ne, int) and ne != 0 and "sandbox-path" in (ns or "")) or \
       (te is not None and isinstance(te, int) and te != 0 and "sandbox-path" in (ts or "")):
        return "SANDBOX-BOUND", "fails against the clone referencing a sandbox path; no working tree-root override"
    if r["hardcodes_sandbox_path"] and not r["has_tree_flag"]:
        return "SANDBOX-BOUND", "hardcodes a sandbox path with no --tree/--work override"
    # errored / timed out with no clean run.
    if isinstance(ne, str) and ne.startswith(("ERR", "TIMEOUT")) and not (te == 0):
        return "UNKNOWN", f"no-arg run {ne}; could not determine (needs a hand run)"
    # clean exit but ambiguous signature (output but not clearly this-tree)
    if ne == 0 or te == 0:
        return "RUNS-DEGRADED", "exit 0 but output not demonstrably about this tree (needs a denominator check)"
    return "UNKNOWN", "signals inconclusive; needs a hand classification"

from collections import Counter
out = []
for r in recs:
    cls, why = classify(r)
    r["class"], r["why"] = cls, why
    r["git_redundant"] = bool(GIT_REDUNDANT.search(r["tool"]))
    out.append(r)

counts = Counter(r["class"] for r in out)
print("== counts ==")
for k in ("RUNS", "RUNS-DEGRADED", "SANDBOX-BOUND", "UNKNOWN"):
    print(f"  {k:14s} {counts.get(k,0)}")
print(f"  TOTAL          {len(out)}")
print("\n== RUNS-DEGRADED (priority list) ==")
for r in out:
    if r["class"] == "RUNS-DEGRADED":
        print(f"  {r['tool']:30s} {r['why']}")
print("\n== git-redundant candidates ==")
for r in out:
    if r["git_redundant"]:
        print(f"  {r['tool']:30s} {r['purpose'][:60]}")
# dump the full verdict for the ledger writer
json.dump(out, open("/tmp/toolchain_verdict.json", "w"), indent=0)
print(f"\nwrote /tmp/toolchain_verdict.json ({len(out)} tools)")
