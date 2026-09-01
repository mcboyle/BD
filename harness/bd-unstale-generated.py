#!/usr/bin/env python3
"""Drop stale GENERATED artifacts from blocked worker trees.

All five rows blocked on 2026-08-29 refused for the same reason: their diff
carried a generated artifact (DEPENDENCY_GRAPH.json, FUNCTION_INDEX.md) that six
merges had since moved on main, so the patch conflicted on a file NOBODY EDITED
BY HAND. bd-regen-order rebuilds both ("AST walk; must follow any file
add/delete" / "line-number sensitive"), so the worker's copy carries no
information -- restoring it to main lets the row's REAL code apply and lets the
integrator regenerate the artifact from the merged tree.

Only files that bd-regen-order is known to rebuild are touched. A worker edit to
anything else is left exactly as the worker wrote it.
"""
import pathlib, subprocess, sys

REPO = "/home/mboyle/BulkDownloader"
WT = pathlib.Path("/home/mboyle/bd-codex-wt")
# every path bd-regen-order rebuilds, so restoring it loses nothing
GENERATED = [
    "DEPENDENCY_GRAPH.json", "DEPENDENCY_GRAPH.md",
    "FUNCTION_INDEX.md", "PIN_INDEX.json",
    "ROUTE_INDEX.md", "ENDPOINT_CATALOG.md",
    # INV_TAGS.md was MISSING while its .json sibling was listed -- measured
    # 2026-08-29 as the only real file collision between rows 371 and 378.
    "INV_TAGS.json", "INV_TAGS.md", "tests/source_window_hashes.json",
    "STATIC_KB.md",
    # 2026-08-29: rows 357 and 371 both re-froze the import graph and the
    # template snapshot, so a two-row batch conflicted "outside the
    # append-only set" and BOTH were marked BLOCKED on rebase. The
    # integrator RE-DERIVES both and names the moved edges in the
    # changelog (declare_edges / declare_surface in bd-regen-order), so a
    # worker copy is redundant and only ever collides.
    "tools/decomp/import_graph_baseline.json",
]

subprocess.run(["git", "-C", REPO, "fetch", "--quiet", "origin"], check=False)

for row in sys.argv[1:]:
    w = WT / f"row{row}"
    if not w.is_dir():
        print(f"row {row}: no worktree"); continue
    changed = subprocess.run(["git", "-C", str(w), "diff", "--name-only", "HEAD"],
                             capture_output=True, text=True).stdout.split()
    hits = [g for g in GENERATED if g in changed]
    if not hits:
        print(f"row {row}: no stale generated artifact in the diff"); continue
    for g in hits:
        # restore from origin/main, not from HEAD: HEAD is the worker's stale base
        r = subprocess.run(["git", "-C", str(w), "checkout", "origin/main", "--", g],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"row {row}: {g} restore FAILED: {r.stderr.strip()[:70]}")
    still = subprocess.run(["git", "-C", str(w), "diff", "--name-only", "HEAD"],
                           capture_output=True, text=True).stdout.split()
    left = [g for g in GENERATED if g in still]
    print(f"row {row}: restored {hits} -> still stale: {left or 'none'}; "
          f"{len(still)} file(s) remain in the diff")
