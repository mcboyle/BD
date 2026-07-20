#!/usr/bin/env python3
"""Emit the ledger body from the (refined, post-port) verdict, with a final
hand-correction layer for the few the output-signature refiner mis-called
(regex missed "noun : 0" / "no X found" degraded shapes)."""
from __future__ import annotations
import json, re
from collections import Counter

verdict = {r["tool"]: r for r in json.load(open("/tmp/toolchain_verdict.json"))}

# Final hand corrections (verified from real output this session).
FIX = {
 "bd-decomp": ("RUNS-DEGRADED", "exit 2 'no lens produced output for bulk_downloader/app.py' -- engaged this tree but produced nothing (missing lens dep)"),
 "bd-docstale": ("RUNS-DEGRADED", "'no verified-against markers found' -- PK/docs here carry them; scanned the wrong place"),
 "bd-since": ("RUNS-DEGRADED", "'no source zip found' -- needs the release zip; git diff replaces it (redundant)"),
 "bd-surface-census": ("RUNS-DEGRADED", "all zeros (0 env vars / 0 config / 0 modules) -- scanned an empty denominator"),
}
GITRED = {"bd-since", "bd-snapshot", "bd-checkpoint"}
for name, (cls, why) in FIX.items():
    if name in verdict:
        verdict[name]["class"], verdict[name]["why"] = cls, why
for name in GITRED:
    if name in verdict:
        verdict[name]["git_redundant"] = True

rows = sorted(verdict.values(), key=lambda r: r["tool"])
counts = Counter(r["class"] for r in rows)
order = ["RUNS", "RUNS-DEGRADED", "SANDBOX-BOUND", "UNKNOWN"]

def esc(s):
    return (s or "").replace("|", "/").replace("\n", " ")[:150]

out = ["## Counts\n", "| Class | Count |", "| --- | --- |"]
for k in order:
    out.append(f"| `{k}` | {counts.get(k,0)} |")
out.append(f"| **total** | **{len(rows)}** |\n")

out.append("## `RUNS-DEGRADED` -- the priority list (what each silently missed)\n")
out.append("| Tool | What it reports on instead of this tree |")
out.append("| --- | --- |")
for r in rows:
    if r["class"] == "RUNS-DEGRADED":
        out.append(f"| `{r['tool']}` | {esc(r['why'])} |")
out.append("")

out.append("## Redundant now that git exists (recommend, don't delete)\n")
for r in rows:
    if r.get("git_redundant"):
        out.append(f"- `{r['tool']}` -- {esc(r['purpose'])[:80]}")
out.append("")

for k in order:
    grp = [r for r in rows if r["class"] == k]
    out.append(f"## Appendix: `{k}` ({len(grp)})\n")
    out.append("| Tool | Justification |")
    out.append("| --- | --- |")
    for r in grp:
        out.append(f"| `{r['tool']}` | {esc(r['why'])} |")
    out.append("")

open("/tmp/ledger_body.md", "w").write("\n".join(out))
print("== FINAL counts ==")
for k in order:
    print(f"  {k:14s} {counts.get(k,0)}")
print(f"  TOTAL          {len(rows)}")
