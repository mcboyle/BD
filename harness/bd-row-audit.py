#!/usr/bin/env python3
"""Refuse a row whose worktree does not contain the work the row claims.

Written 2026-08-29 after an audit of the nine live worktrees found FOUR that did
not implement their own row:

  370  "the download menu is read from the page"  -> 45 KB of LOGIN code, and
       its only test is test_row370_measured_login_forms.py. Real work, wrong row.
  312  "bd-jobs reap signals an identity it still holds" -> 10 insertions: a
       backlog line and gate-count edits. No implementation. No test.
  313  "bd-job acquires its name atomically"      -> 10 insertions: a ci.yml line
       and gate-count edits. No implementation. No test.
  377  "a gate that cannot measure must not crash" -> its core deliverable in
       toolchain/bin/bd-template-verify was MISSING; an agent implemented it.

312 and 313 are worse than empty. Each RAISES _EXPECTED_DECLARED_GATE_COUNT by
one for a gate file that does not exist, and each adds SIX duplicate module-level
assignments of that constant -- so Python takes the last one and the tree would
claim 177/178 declared gates against main's 228. A gate that does not exist would
be counted, which is the exact inverse of "a gate CI does not run does not exist".
The row-377 worker hit the same duplicate-assignment defect independently, so
this is a recurring worker failure mode, not three coincidences.

All three surviving cases fall to MECHANICAL checks. No model is needed and none
is used here; C4 (does this diff implement this subject?) is the only genuinely
model-shaped question and is deliberately NOT attempted.

  C1  the diff carries at least one implementation file OR one new test
  C2  no module gains a duplicate top-level assignment of the same pin constant
  C3  a raised _EXPECTED_DECLARED_GATE_COUNT is accompanied by a new test file

2026-08-29, PRE-DISPATCH PRE-FLIGHT. Three cuts refused that day for mechanical
register/report defects rather than anything wrong with the work, each costing a
full integrate cycle and, twice, a 13-minute band:
  C4  register prose citing a row that main's register does not carry
      (v3.66.1347 refused: row 389's row cited row 388, unmerged)
  C5  a worker report without two usable changelog bullets
      (v3.66.1346 refused twice: bullets were 537/336/309 chars, cap is 300)
  C6  a generated artifact in the diff that differs from main
      (v3.66.1346 refused: FUNCTION_INDEX.md, regenerated then overtaken by a merge)
A worker cannot see main's register from inside its worktree, so C4 in
particular can only be checked here.

Exit 0 = every audited row passes. Exit 1 = at least one REFUSED. Exit 2 = a row
could not be measured, which is UNKNOWN and is NOT a pass (CLAUDE.md A7).

  usage: bd-row-audit.py <row> [<row> ...]     # or no args = every live worktree
"""
import ast, pathlib, re, subprocess, sys

WT = pathlib.Path("/home/mboyle/bd-codex-wt")
REPO = "/home/mboyle/BulkDownloader"
SPEC = pathlib.Path("/home/mboyle/bd-night-spec.txt")
IMPL_ROOTS = ("bulk_downloader/", "tools/", "toolchain/", "scripts/",
              "frontend/src/", "project-knowledge/")
# Files every row touches for bookkeeping; they prove nothing about the work.
BOOKKEEPING = {"project-knowledge/IMPROVEMENT_BACKLOG.md", ".github/workflows/ci.yml",
               "PIN_INDEX.json", "INV_TAGS.md", "FUNCTION_INDEX.md",
               "DEPENDENCY_GRAPH.json", "DEPENDENCY_GRAPH.md", "ROUTE_INDEX.json",
               "project-knowledge/STATIC_KB_MANIFEST.json"}
PIN_RE = re.compile(r"^_EXPECTED_[A-Z0-9_]+\s*=")
ROW_CITE_RE = re.compile(r"\brows?\s+(\d{2,4})\b", re.I)
REPORTS = pathlib.Path("/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts")
# every path bd-regen-order rebuilds -- the integrator re-derives these, so a
# worker copy is redundant and only ever collides
REGENERATED = {"DEPENDENCY_GRAPH.json", "DEPENDENCY_GRAPH.md", "FUNCTION_INDEX.md",
               "PIN_INDEX.json", "ROUTE_INDEX.md", "ROUTE_INDEX.json",
               "ENDPOINT_CATALOG.md", "INV_TAGS.json", "INV_TAGS.md",
               "STATIC_KB.md", "tests/source_window_hashes.json",
               "project-knowledge/STATIC_KB_MANIFEST.json"}


def register_rows(text):
    """Row ids the register actually carries, whatever their status."""
    return set(re.findall(r"^\|\s*(\d+)\s*\|", text or "", re.M))


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout


def subject(row):
    for line in SPEC.read_text().splitlines():
        m = re.match(rf"^#?\s*{row}\|([^|]+)\|(.*)$", line)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return "", ""


def duplicate_pins(text):
    """Top-level constants assigned more than once in one module.

    Parsed, not grepped: a comment or a docstring mentioning the name is not an
    assignment, and this gate must not fire on prose (CLAUDE.md A7)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None                      # unparseable -> UNKNOWN, never OK
    seen, dupes = {}, set()
    for node in tree.body:               # TOP LEVEL only
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and PIN_RE.match(t.id + " ="):
                    seen[t.id] = seen.get(t.id, 0) + 1
                    if seen[t.id] > 1:
                        dupes.add(t.id)
    return dupes


def audit(row):
    """Return (verdict, [lines]) where verdict is PASS / REFUSED / UNKNOWN."""
    w = WT / f"row{row}"
    slug, title = subject(row)
    if not w.is_dir():
        return "UNKNOWN", [f"no worktree at {w}"]
    head = sh("git", "-C", str(w), "rev-parse", "HEAD").strip()
    if not head:
        return "UNKNOWN", ["worktree has no HEAD"]
    # Measure against the MERGE BASE. A stale worktree renders main's newer
    # files as deletions, and the file list then names paths it does not have.
    mb = sh("git", "-C", REPO, "merge-base", head, "origin/main").strip()
    if not mb:
        return "UNKNOWN", [f"no merge-base for {head[:8]}"]
    subprocess.run(["git", "-C", str(w), "add", "-N", "--", ".",
                    ":(exclude)venv", ":(exclude)frontend/node_modules",
                    ":(exclude)frontend/dist"], capture_output=True)
    files = [f for f in sh("git", "-C", str(w), "diff", mb, "--name-only", "--",
                           ".", ":(exclude)venv",
                           ":(exclude)frontend/node_modules").split() if f]
    if not files:
        # A MERGED row's worktree is legitimately empty against the merge base.
        # Reporting that as REFUSED reads as a defect in the row; it is not.
        slug_in_main = slug and sh("git", "-C", REPO, "log", "--oneline",
                                   "origin/main", f"--grep={slug}", "-1").strip()
        if slug_in_main:
            return "PASS", [f"already merged: {slug_in_main[:60]}"]
        return "REFUSED", ["the diff is EMPTY against the merge base"]

    out, verdict = [f"subject: {slug} -- {title}", f"{len(files)} file(s) vs {mb[:8]}"], "PASS"
    substantive = [f for f in files if f not in BOOKKEEPING]
    impl = [f for f in substantive if f.startswith(IMPL_ROOTS)
            and not f.startswith("project-knowledge/IMPROVEMENT")]
    new_tests = [f for f in files if f.startswith("tests/")
                 and pathlib.PurePath(f).name.startswith("test")
                 and not (pathlib.Path(REPO) / f).exists()]

    # C1 -- the work must be PRESENT.
    # A CONTRACT OR DOC ROW legitimately carries neither. Row 389 amends
    # CLAUDE.md A7 and nothing else, and a first draft of this check refused it
    # -- a gate whose denominator excluded a whole class of valid row, which is
    # the exact defect this file exists to catch, in this file. Doc rows are
    # real work; they are simply judged by bd-freshcheck, not by a new test.
    DOCS = (".md", ".rst", ".txt")
    doc_only = bool(substantive) and all(f.endswith(DOCS) for f in substantive)
    if doc_only:
        out.append(f"C1 ok: documentation row -- {len(substantive)} doc file(s), "
                   "judged by bd-freshcheck rather than by a new test")
    elif not impl and not new_tests:
        verdict = "REFUSED"
        out.append("C1 FAIL: no implementation file and no NEW test -- "
                   f"only {', '.join(substantive) or 'bookkeeping'}")
    else:
        out.append(f"C1 ok: {len(impl)} impl file(s), {len(new_tests)} new test(s)")

    # C2 -- one constant, one assignment.
    for f in files:
        if not f.endswith(".py"):
            continue
        p = w / f
        if not p.is_file():
            continue
        d = duplicate_pins(p.read_text(errors="replace"))
        if d is None:
            verdict = "UNKNOWN" if verdict == "PASS" else verdict
            out.append(f"C2 UNKNOWN: {f} does not parse")
        elif d:
            verdict = "REFUSED"
            out.append(f"C2 FAIL: {f} assigns {', '.join(sorted(d))} more than "
                       "once at module level -- the LAST assignment silently wins")

    # C3 -- a claimed gate must exist.
    raised = [l for l in sh("git", "-C", str(w), "diff", mb, "--", "tests/").splitlines()
              if l.startswith("+") and "_EXPECTED_DECLARED_GATE_COUNT" in l]
    if raised and not new_tests:
        verdict = "REFUSED"
        out.append(f"C3 FAIL: raises the declared-gate count ({len(raised)} line(s)) "
                   "while adding NO gate file -- a gate that does not exist "
                   "would be counted")
    elif raised:
        out.append(f"C3 ok: gate count moved alongside {len(new_tests)} new test(s)")

    # C4 -- a citation the register cannot resolve. Checked HERE because a
    # worker cannot see main's register from inside its worktree.
    reg_path = "project-knowledge/IMPROVEMENT_BACKLOG.md"
    if reg_path in files:
        main_reg = sh("git", "-C", REPO, "show", f"origin/main:{reg_path}")
        known = register_rows(main_reg) | {str(row)}
        wt_reg = (w / reg_path)
        cited = set()
        for line in (wt_reg.read_text(errors="replace").splitlines()
                     if wt_reg.is_file() else []):
            if line.startswith(f"| {row} |"):
                cited |= set(ROW_CITE_RE.findall(line))
        dangling = sorted(cited - known)
        if dangling:
            verdict = "REFUSED"
            out.append(f"C4 FAIL: row {row}'s register prose cites "
                       f"{', '.join(dangling)}, absent from main's register -- "
                       "the dangling-reference gate will refuse the cut")
        elif cited:
            out.append(f"C4 ok: {len(cited)} citation(s) all resolve")

    # C5 -- the integrator aborts the whole cut without two usable bullets.
    rp = REPORTS / f"row{row}.txt"
    if not rp.is_file():
        verdict = "REFUSED"
        out.append(f"C5 FAIL: no worker report at {rp}")
    else:
        tail = rp.read_text(errors="replace").splitlines()[-400:]
        good = [l for l in tail if l.startswith("- ") and 20 <= len(l) <= 300
                and l.isascii()]
        if len(good) < 2:
            over = [len(l) for l in tail if l.startswith("- ") and len(l) > 300]
            verdict = "REFUSED"
            out.append(f"C5 FAIL: {len(good)} usable changelog bullet(s), need 2"
                       + (f"; {len(over)} over the 300-char cap: {over}" if over else ""))
        else:
            out.append(f"C5 ok: {len(good)} usable bullet(s)")

    # C6 -- a regenerated artifact that main has already moved past.
    stale = []
    for f in files:
        if f in REGENERATED:
            # AGAINST THE MERGE BASE, NOT MAIN'S MOVING TIP. The file list
            # above was corrected to `mb`; this check was left on origin/main,
            # so once main advanced past the candidate's base C6 compared a
            # regenerated artifact against a tree the candidate was never
            # based on and could refuse a correct row.
            d = sh("git", "-C", str(w), "diff", mb, "--name-only", "--", f)
            if not d.strip():
                stale.append(f)          # identical to main, so it carries no change
    if stale:
        verdict = "REFUSED"
        out.append(f"C6 FAIL: {', '.join(stale)} is in the diff but identical to "
                   "main -- it will conflict outside the append-only set; reset it "
                   "to HEAD and let the integrator regenerate")
    return verdict, out


# Flags are NOT row ids. A stray '-v' became a phantom row returning
# UNKNOWN -- a FAILING state -- which is exactly the fail-shape this
# tool exists to catch, in the tool itself.
rows = [a for a in sys.argv[1:] if not a.startswith('-')] or sorted(
    (p.name[3:] for p in WT.iterdir() if p.is_dir() and p.name.startswith("row")),
    key=lambda s: (len(s), s))
worst, counts = 0, {"PASS": 0, "REFUSED": 0, "UNKNOWN": 0}
for row in rows:
    v, lines = audit(row)
    counts[v] += 1
    worst = max(worst, {"PASS": 0, "REFUSED": 1, "UNKNOWN": 2}[v])
    if v != "PASS" or "-v" in sys.argv:
        print(f"── row {row}: {v}")
        for l in lines:
            print(f"     {l}")
print(f"\naudited {len(rows)} row(s): {counts['PASS']} PASS, "
      f"{counts['REFUSED']} REFUSED, {counts['UNKNOWN']} UNKNOWN")
sys.exit(worst)
