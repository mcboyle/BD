#!/usr/bin/env python3
"""Generate one codex brief per backlog row: COMMON.md + the row's OWN text
verbatim + any operator ruling that changes what "done" means for that row.

The row text IS the spec -- it carries the measured evidence, the acceptance
criteria and the restraints, and paraphrasing it into a brief is how a worker
ends up solving a different problem than the one that was filed.
"""
import pathlib, re, sys

RE = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+)\|\s*(.*)$")
ROOT = pathlib.Path("/home/mboyle")
# Rows filed by an UNMERGED cut are not on main yet -- row 241 and row 242 exist
# only in the v3.66.1241 candidate. Read the candidate's register when it is
# present so a brief can be written for a row main has never seen.
_CAND = ROOT / "bd-cuts/cut/1241-owner-observation-deadline/project-knowledge/IMPROVEMENT_BACKLOG.md"
BACKLOG = _CAND if _CAND.exists() else ROOT / "BulkDownloader/project-knowledge/IMPROVEMENT_BACKLOG.md"
COMMON = (ROOT / "bd-codex-briefs/COMMON.md").read_text(encoding="utf-8")

RULINGS = {
 "26": """
OPERATOR RULING 2026-08-25 -- WHAT "DONE" MEANS FOR THIS ROW:
Ship ONE MORE DECIDABLE SLICE and LEAVE THE ROW OPEN with a measured, smaller
remainder. You may NOT close this row. You may NOT close it by argument, and you
may NOT redefine the objective around whatever you managed to build -- that is
the completion antipattern A8 names explicitly.
The remainder is variable-mediated vacuity, general unreachability, and
"assertions true of every possible implementation". THE LAST ONE IS UNDECIDABLE
IN GENERAL. Do not claim otherwise. Pick a slice that is genuinely decidable,
state its exact boundary, and give it a NONZERO ELIGIBLE FLOOR and an exact
metrics dictionary so an empty census cannot launder GREEN -- that is how the
three shipped slices did it, so read them first (@1098 and @1193).
Update the row text with the new measured remainder. Keep it OPEN.
""",
 "27": """
OPERATOR RULING 2026-08-25 -- WHAT "DONE" MEANS FOR THIS ROW:
Ship ONE MORE DECIDABLE SLICE and LEAVE THE ROW OPEN. You may NOT close it.
The remainder is "detect a cut that OMITTED the control". The executable route
CANNOT do this: it proves a control is real when written, and ABSENCE IS NOT
OBSERVABLE FROM THE ARTIFACT UNDER TEST. Do not resurrect a declarative,
derived-predicate, or mandatory-preflight route -- @1187 and @1188 already
rejected those. Find a slice that is decidable from an INDEPENDENT denominator
and ship that. Update the row with the new measured remainder. Keep it OPEN.
""",
 "241": """
OPERATOR RULING 2026-08-25 -- DIAGNOSE ONLY. DO NOT FIX.
Reproduce each of the two legs deliberately with basetemp PRESERVED, INSTRUMENT
THE RESOURCE rather than reasoning about it, and NAME THE MECHANISM. Then STOP
and update the row with that evidence. Do NOT attempt the fix and do NOT run the
5-round matched proof in this pass -- that acceptance arm monopolises the box.
The two legs: (a) under synthetic load far past the sanctioned shape, a GROUP
PROBE answered UNKNOWN about a leader in transition -- a probe returning
uncertainty, NOT a bound expiring, so it is a different mechanism from the one
v3.66.1241 fixed; (b) the zero-floor control arm flaked on the READY READER.
Captured basetemps are under /tmp/row237-artifacts/rundirs/ -- RELOCATE THEM to
fleet-run-artifacts BEFORE anything prunes /tmp, or the evidence dies.
DELIVERABLE: an updated row plus preserved evidence. No product change.
""",
}

def rows():
    out = {}
    for line in BACKLOG.read_text(encoding="utf-8").split("\n"):
        if line.startswith("|") and (m := RE.match(line)):
            out[m.group(1)] = (m.group(2).strip(), m.group(3).rstrip("| ").strip())
    return out

def main():
    table = rows()
    outdir = ROOT / "bd-codex-briefs"
    for rid in sys.argv[1:]:
        if rid not in table:
            sys.exit(f"row {rid} not in the backlog")
        status, body = table[rid]
        title = body.split(" -- ")[0].strip()
        p = outdir / f"row{rid}.md"
        if p.exists() and rid not in RULINGS and rid in {"183","184","242","121"}:
            print(f"row {rid}: hand-written brief kept"); continue
        text = COMMON + f"""

## TASK: backlog row {rid} -- {title[:90]}

Current status in the register: {status}

THE ROW'S OWN TEXT, VERBATIM. It carries the measured evidence, the acceptance
criteria and the restraints. Where it and this brief disagree, THE ROW WINS.

----------------------------------------------------------------------
{body}
----------------------------------------------------------------------
{RULINGS.get(rid, "")}
Before designing anything: locate every file the row names and READ IT. Re-derive
the row's numbers from the current tree rather than trusting them -- the row may
be stale, and if it is, say so with evidence instead of building to a number that
no longer holds. If the row turns out to be ALREADY FIXED or OBSOLETE, stop and
report that with proof; do not manufacture work to justify the dispatch.
"""
        p.write_text(text, encoding="utf-8")
        print(f"row {rid}: brief written ({len(text.splitlines())} lines) -- {title[:60]}")

main()
