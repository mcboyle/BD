#!/usr/bin/env python3
"""Apply hand-verified overrides to the auto-classification and emit the ledger
body (counts + per-class tables). The overrides come from RUNNING each ambiguous
tool and reading its real output -- exit 0 is never trusted as RUNS."""
from __future__ import annotations
import json
from collections import Counter

verdict = {r["tool"]: r for r in json.load(open("/tmp/toolchain_verdict.json"))}

# Hand-verified reclassifications (from real output, see session transcript).
OVER = {
 "bd-precut": ("RUNS", "resolves the clone (root=/home/user/BD) and emits a real cut-readiness verdict"),
 "bd-netns-proof": ("RUNS", "PORTED this session: derives netns-create by probing; reports 'works' here (was RUNS-DEGRADED)"),
 "bd-capture-chaos": ("RUNS", "chaos simulator (3 capture failure modes); self-contained, exit 0"),
 "bd-db-chaos": ("RUNS", "chaos simulator (db failure modes); self-contained, exit 0"),
 "bd-network-chaos": ("RUNS", "chaos simulator (network failure modes); self-contained, exit 0"),
 "bd-queue-chaos": ("RUNS", "chaos simulator (queue failure modes); self-contained, exit 0"),
 "bd-plugin-chaos": ("RUNS", "chaos simulator (plugin containment); self-contained, exit 0"),
 "bd-scheduler-sim": ("RUNS", "scheduler simulation (50 jobs, cap 4); self-contained, exit 0"),
 "bd-fuzz-urlguard": ("RUNS", "fuzzes the URL guard; real verdict (no bypass), exit 0"),
 "bd-redaction-compiler": ("RUNS", "compiles this tree's redaction ruleset (5 rules); scrubber+scanner+tests agree"),
 "bd-secret-fixture": ("RUNS", "emits the fake-secret corpus (27 entries); self-contained, exit 0"),
 "bd-parallel": ("RUNS", "parallel test-runner; prints usage on no-arg (operational, needs args to act)"),
 "bd-brief": ("RUNS-DEGRADED", "reports a session brief on /home/claude/work (absent) -- wrong denominator"),
 "bd-flakes": ("RUNS-DEGRADED", "scans a sandbox path for test-discipline hazards, not this tree"),
 "bd-footguns": ("RUNS-DEGRADED", "checks 10 footguns vs /home/claude/work (absent), not the clone"),
 "bd-freshest": ("RUNS-DEGRADED", "reads /home/claude/nextsess (absent); STATE.built_version 'unknown'"),
 "bd-gui-surface": ("RUNS-DEGRADED", "censuses the GUI surface of /home/claude/work, not this tree"),
 "bd-intake": ("RUNS-DEGRADED", "looks for /mnt/user-data/uploads (sandbox mount, absent)"),
 "bd-pending": ("RUNS-DEGRADED", "reconciles pending items vs /mnt/project (materialized only in sandbox)"),
 "bd-tool-lint": ("RUNS-DEGRADED", "linted 0 tools (0 analysis + 0 operational) -- empty denominator; the toolchain is right here"),
 "bd-docstale": ("RUNS-DEGRADED", "'no verified-against markers found' -- PK docs here carry them; scanned the wrong dir"),
 "bd-versync": ("RUNS-DEGRADED", "'__init__.py __version__ NOT FOUND' -- it is 3.66.805 here; wrong path"),
 "bd-ratchet": ("RUNS-DEGRADED", "empty output on exit 0 -- no baseline read; nothing evaluated"),
 "bd-fe-dead-control": ("RUNS-DEGRADED", "'0 control surfaces' -- the FE has many; empty denominator"),
 "bd-surface-census": ("RUNS-DEGRADED", "all zeros (0 python modules of 561) -- scanned nothing in this tree"),
 "bd-pinscan": ("RUNS-DEGRADED", "exit 0 'no fragile pins' with no evidence of a non-empty scan -- denominator unconfirmed"),
 "bd-since": ("RUNS-DEGRADED", "'no source zip found' -- needs the release zip; git diff replaces it (redundant)"),
 "bd-deps": ("RUNS-DEGRADED", "fails loud: DEPENDENCY_GRAPH.json absent (needs bd-regen --write) -- no run on a bare clone"),
 "bd-imports": ("RUNS-DEGRADED", "fails loud: import_graph_gate.py 'not found' (looks in the wrong location)"),
 "bd-decomp": ("RUNS-DEGRADED", "exit 2 on bulk_downloader/app.py -- engaged this tree but no lens produced output (missing dep)"),
}
for name, (cls, why) in OVER.items():
    if name in verdict:
        verdict[name]["class"] = cls
        verdict[name]["why"] = why

rows = sorted(verdict.values(), key=lambda r: r["tool"])
counts = Counter(r["class"] for r in rows)
order = ["RUNS", "RUNS-DEGRADED", "SANDBOX-BOUND", "UNKNOWN"]

out = []
out.append("## Counts\n")
out.append("| Class | Count |")
out.append("| --- | --- |")
for k in order:
    out.append(f"| `{k}` | {counts.get(k,0)} |")
out.append(f"| **total** | **{len(rows)}** |\n")

# RUNS-DEGRADED priority list
out.append("## `RUNS-DEGRADED` -- the priority list (what each silently missed)\n")
out.append("| Tool | What it silently reported on instead of this tree |")
out.append("| --- | --- |")
for r in rows:
    if r["class"] == "RUNS-DEGRADED":
        out.append(f"| `{r['tool']}` | {r['why']} |")
out.append("")

# git-redundant
out.append("## Redundant now that git exists (recommend, don't delete)\n")
for r in rows:
    if r.get("git_redundant"):
        out.append(f"- `{r['tool']}` -- {r['purpose'][:80]}")
out.append("")

# full appendix per class
for k in order:
    grp = [r for r in rows if r["class"] == k]
    out.append(f"## Appendix: `{k}` ({len(grp)})\n")
    out.append("| Tool | Justification |")
    out.append("| --- | --- |")
    for r in grp:
        out.append(f"| `{r['tool']}` | {r['why']} |")
    out.append("")

open("/tmp/ledger_body.md", "w").write("\n".join(out))
print("== corrected counts ==")
for k in order:
    print(f"  {k:14s} {counts.get(k,0)}")
print(f"  TOTAL          {len(rows)}")
print(f"\nwrote /tmp/ledger_body.md ({sum(1 for _ in open('/tmp/ledger_body.md'))} lines)")
