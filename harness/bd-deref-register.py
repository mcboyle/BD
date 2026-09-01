#!/usr/bin/env python3
"""Remove dangling row citations from the register rows this session inserted.

The backlog dangling-reference gate resolves every "row NNN" against the row
population IN THAT FILE. bd-register-open-row.py copied brief prose verbatim,
and that prose cites sibling rows that have not merged, so each citation is
dangling by construction. Rewrite the CITATION, not the claim: "row 376" becomes
"a separate row", which stays true and stops asserting an id the file cannot
resolve. Only the line this session inserted is touched; existing rows are left
byte-identical.
"""
import pathlib, re, sys

WT = pathlib.Path("/home/mboyle/bd-codex-wt")
REG = "project-knowledge/IMPROVEMENT_BACKLOG.md"

for row in sys.argv[1:]:
    p = WT / f"row{row}" / REG
    if not p.is_file():
        print(f"row {row}: no register"); continue
    lines = p.read_text(encoding="utf-8").splitlines()
    hit = None
    for i, l in enumerate(lines):
        if re.match(rf"^\| {row} \|", l):
            hit = i; break
    if hit is None:
        print(f"row {row}: own row not found"); continue
    before = lines[hit]
    # existing ids present in this file, which ARE resolvable
    present = {m.group(1) for m in re.finditer(r"^\| (\d{3}) \|", "\n".join(lines), re.M)}
    def repl(m):
        return m.group(0) if m.group(2) in present else f"{m.group(1)}a separate row"
    after = re.sub(r"(\b)rows? (\d{3})\b", repl, before)
    # bare "@1234" version stamps and file paths are not row citations; leave them
    if after == before:
        print(f"row {row}: no dangling citation")
        continue
    lines[hit] = after
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    n = len(re.findall(r"\brows? \d{3}\b", before)) - len(re.findall(r"\brows? \d{3}\b", after))
    print(f"row {row}: rewrote {n} dangling citation(s)")
