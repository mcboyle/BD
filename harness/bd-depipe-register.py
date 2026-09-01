#!/usr/bin/env python3
"""Remove stray pipes from the register rows this session inserted.

A markdown table row is delimited by `|`. bd-register-open-row.py copied brief
prose verbatim, and several briefs quote the night-spec line itself
(`368|admin-token-scope-matches-its-name|...`), so the inserted row carried SIX
pipes instead of four. The backlog gate then parsed fewer rows than physically
present and refused — correctly, and for a reason that had nothing to do with
the row's content. Rewrite the delimiter, never the claim.
"""
import pathlib, re, sys

WT = pathlib.Path("/home/mboyle/bd-codex-wt")
REG = "project-knowledge/IMPROVEMENT_BACKLOG.md"

for row in sys.argv[1:]:
    p = WT / f"row{row}" / REG
    if not p.is_file():
        print(f"row {row}: no register"); continue
    lines = p.read_text(encoding="utf-8").splitlines()
    hit = next((i for i, l in enumerate(lines) if re.match(rf"^\| {row} \|", l)), None)
    if hit is None:
        print(f"row {row}: own row not found"); continue
    line = lines[hit]
    n_before = line.count("|")
    if n_before <= 4:
        print(f"row {row}: {n_before} pipes, already well-formed"); continue
    # keep the three structural delimiters and the trailing one; the rest are data
    m = re.match(r"^(\| \d{3} \| [A-Z ]+?(?:@\d+)? \| )(.*?)(\s*\|)$", line)
    if not m:
        # fall back to a positional split on the first three delimiters
        parts = line.split("|")
        head, body = "|".join(parts[:3]) + "|", "|".join(parts[3:]).rstrip("|")
        fixed = head + " " + body.replace("|", " / ").strip() + " |"
    else:
        fixed = m.group(1) + m.group(2).replace("|", " / ") + m.group(3)
    fixed = re.sub(r"\s{2,}", " ", fixed)
    lines[hit] = fixed
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"row {row}: pipes {n_before} -> {fixed.count('|')}")
