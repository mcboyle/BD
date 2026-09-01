#!/usr/bin/env python3
"""Build a PR body from a worker's own report tail.

The first version grepped for a "Final accounting" heading that most reports do
not use, and silently produced a 2-line body for every row -- a generator whose
failure mode is an empty deliverable, not an error. This takes the report's last
prose block and filters it, then REFUSES if the result is too thin to be a real
PR body rather than shipping an empty one.
"""
import pathlib, sys, re
CC = pathlib.Path("/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts")
OUT = pathlib.Path("/home/mboyle/fleet-run-artifacts/2026-08-25/inflight")
TITLES = {"242":"the release gate sees prose above the newest header",
          "121":"derive_login_flow emits a plan that reproduces the capture",
          "241":"the two remaining W1 legs are named with preserved evidence",
          "26":"the vacuity detector gains another decidable slice",
          "176":"the reptyle fixture is identified rather than labelled",
          "174":"fleet residue owners recorded, one approved removal"}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
for row in sys.argv[1:]:
    f = CC/f"row{row}.txt"
    if not f.exists(): print(f"row {row}: no report"); continue
    lines = ANSI.sub("", f.read_text(encoding="utf-8", errors="replace")).split("\n")
    keep, seen = [], set()
    for l in reversed(lines[-260:]):
        t = l.rstrip()
        if not t or len(t) > 240: continue
        if not all(ord(c) < 128 for c in t): continue
        if t.strip().startswith(("- ", "* ", "#", "1.", "2.", "3.")) or (
                40 < len(t) < 200 and t[0].isupper() and t.endswith(".")):
            if t.strip() in seen: continue
            seen.add(t.strip()); keep.append(t)
        if len(keep) >= 26: break
    keep.reverse()
    if len(keep) < 5:
        print(f"row {row}: REFUSING -- only {len(keep)} usable line(s); write this body by hand")
        continue
    body = [f"## Row {row} -- {TITLES.get(row,'')}", "",
            "Closes backlog row " + row + ".", "",
            "The implementing worker's own account, verified by the integrator "
            "(QA re-run in its worktree, then the full affected band on the "
            "integrated cut):", ""] + keep + [
            "", "Release trio, register close and regeneration were written by the "
            "integrator; the worker is forbidden to touch them."]
    (OUT/f"pr-body-{row}.md").write_text("\n".join(body)+"\n", encoding="utf-8")
    print(f"row {row}: pr-body {len(body)} lines")
