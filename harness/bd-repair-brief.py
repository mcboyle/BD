#!/usr/bin/env python3
"""Write a REPAIR brief for a row whose worktree already holds the work.

These rows are not new features: each has a built worktree that fails for a
NAMED, MEASURED reason. The brief hands the worker that exact reason and the
worktree, rather than restating the original row and inviting a rewrite.
"""
import pathlib, re, subprocess, sys

WT = pathlib.Path("/home/mboyle/bd-codex-wt")
OUT = pathlib.Path("/home/mboyle/bd-codex-briefs")
SPEC = pathlib.Path("/home/mboyle/bd-night-spec.txt")
CUTS = pathlib.Path("/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts")

HEAD = """PROCEED WITHOUT ASKING. This is a REPAIR task on work that ALREADY EXISTS in
this worktree. Do not rewrite the feature and do not start over: the row's
implementation is here and is mostly right. Fix the NAMED defect below, keep the
existing design, then prove it.

Do not push, merge, or deploy. Do not edit bulk_downloader/__init__.py,
CHANGELOG.md, or tests/test_settings_center_slice4.py. Read CLAUDE.md, esp. A7.

"""

TAIL = """
HOW TO PROVE IT
  * Re-run the failing test(s) named above and show them GREEN.
  * Then run the row's whole affected band: toolchain/bin/bd-band-derive, and
    run every file it lists with
      env -u BD_INSTALL_DIR bash -c 'BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest <files> -q'
  * Regenerate: venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
    and inspect every generated diff.
  * Do NOT weaken, skip, or deselect the failing assertion to get green. If the
    assertion is genuinely wrong, say so explicitly with the evidence and fix
    the assertion deliberately -- but the default assumption is that the gate is
    right and the implementation is wrong.
  * Report the exact before/after for each named failure and every file changed.
"""


def markers(row):
    w = WT / f"row{row}"
    hits = []
    for sub in ("bulk_downloader", "frontend/src", "tests", "tools"):
        d = w / sub
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if not f.is_file() or "node_modules" in str(f):
                continue
            try:
                t = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if re.search(r"^<<<<<<< |^>>>>>>> ", t, re.M):
                hits.append(str(f.relative_to(w)))
    return hits


def qa_failures(row):
    f = CUTS / f"row{row}.qa.log"
    if not f.is_file():
        return []
    return [l.strip() for l in f.read_text(errors="replace").splitlines()
            if l.startswith("FAILED ")][:8]


for row in sys.argv[1:]:
    w = WT / f"row{row}"
    if not w.is_dir():
        print(f"row {row}: no worktree"); continue
    title = ""
    for l in SPEC.read_text().splitlines():
        m = re.match(rf"^#?\s*(?:PARKED-CONFLICT-MARKERS )?{row}\|([^|]+)\|(.*)$", l)
        if m:
            title = m.group(2).strip(); break
    mk, qa = markers(row), qa_failures(row)
    body = [f"ROW {row} -- REPAIR: {title}", ""]
    if mk:
        body += [
            "DEFECT 1: GIT-STASH CONFLICT MARKERS ARE STILL IN THE SOURCE.",
            "The lane rebases with `git stash pop`, and a CONFLICTED pop leaves",
            "`<<<<<<< Updated upstream` / `=======` / `>>>>>>> Stashed changes` in the",
            "tree. Python then fails to parse and TypeScript fails to compile, so the",
            "row is refused for reasons that look like unrelated test failures.",
            "FILES CARRYING MARKERS IN THIS WORKTREE:", ]
        body += [f"    {p}" for p in mk]
        body += [
            "",
            "RESOLVE EACH ONE ON ITS MERITS. 'Updated upstream' is origin/main (work",
            "that has ALREADY MERGED); 'Stashed changes' is this row's work. Where the",
            "two add DIFFERENT things, keep BOTH. Where they are two implementations of",
            "the SAME thing, keep the upstream one and re-apply this row's intent on top",
            "of it -- do not delete merged behaviour to make your side apply.",
            "Afterwards assert ZERO markers remain and that every touched Python file",
            "parses and the frontend compiles (cd frontend && ./node_modules/.bin/tsc -b).",
            "", ]
    if qa:
        body += [f"DEFECT {2 if mk else 1}: THE ROW'S OWN QA IS RED. Failing tests:"]
        body += [f"    {l}" for l in qa]
        body += [
            "",
            "These are the row's own gates. Treat a failure as a defect in the",
            "implementation until proven otherwise.", ""]
    if not mk and not qa:
        body += ["No markers and no recorded QA failures were found; re-run the band",
                 "and report what actually fails before changing anything.", ""]
    p = OUT / f"row{row}.repair.txt"
    p.write_text(HEAD + "\n".join(body) + TAIL)
    print(f"row {row}: {p.name} ({p.stat().st_size}B) markers={len(mk)} qa_failures={len(qa)}")
