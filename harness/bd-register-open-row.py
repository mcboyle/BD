#!/usr/bin/env python3
"""Add an OPEN register row for a night-spec row, inside its codex worktree.

bd-integrate-row.sh merges the register BY ROW from the worker's tree and aborts
the whole cut with "row <n> not in the register" when the row is absent. The
briefs written on 2026-08-29 told each worker "Do NOT edit the canonical
backlog" -- correct for avoiding cross-worker conflicts, and the reason all 15
rows could not integrate. The row belongs in the worker tree, added here.

CLAUDE.md A2 requires a machine-visible row carrying status, evidence,
acceptance criteria and dependency. The evidence is taken from the row's own
brief so the register and the worker's instructions cannot drift.

  usage: bd-register-open-row.py <row> [<row> ...]
"""
import pathlib, re, sys

SPEC = pathlib.Path("/home/mboyle/bd-night-spec.txt")
BRIEFS = pathlib.Path("/home/mboyle/bd-codex-briefs")
WT = pathlib.Path("/home/mboyle/bd-codex-wt")
REG = "project-knowledge/IMPROVEMENT_BACKLOG.md"


def spec_row(row):
    for l in SPEC.read_text().splitlines():
        m = re.match(rf"^#?\s*{row}\|([^|]+)\|(.*)$", l)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None, None


def evidence(row, limit=1500):
    """One paragraph of MEASURED evidence, taken from the brief verbatim."""
    b = BRIEFS / f"row{row}.txt"
    if not b.is_file():
        return ""
    txt = b.read_text(encoding="utf-8", errors="replace")
    # drop the standing header; keep the measured body
    for marker in ("======================= THE ROW =======================",
                   "MEASURED", "THE PROBLEM", "TRACED IN"):
        i = txt.find(marker)
        if i > 0:
            txt = txt[i:]
            break
    out, seen = [], 0
    for line in txt.splitlines():
        t = " ".join(line.split())
        if not t or t.startswith(("#", "*", "-", "=")):
            continue
        if not all(ord(c) < 128 for c in t):
            continue
        out.append(t); seen += len(t)
        if seen > limit:
            break
    return " ".join(out)[:limit]


for row in sys.argv[1:]:
    w = WT / f"row{row}"
    reg = w / REG
    if not reg.is_file():
        print(f"row {row}: no register in worktree -- skipped"); continue
    txt = reg.read_text(encoding="utf-8")
    if re.search(rf"^\| {row} \|", txt, re.M):
        print(f"row {row}: already in the register -- left alone"); continue
    slug, title = spec_row(row)
    if not slug:
        print(f"row {row}: not in the night spec -- skipped"); continue
    ev = evidence(row)
    if len(ev) < 200:
        print(f"row {row}: only {len(ev)} chars of evidence -- REFUSING a stub row"); continue
    # A markdown row is pipe-delimited, and brief prose quotes the night-spec
    # line (`368|slug|title`), which silently turned a 4-pipe row into a 6- or
    # 17-pipe one and made the backlog gate parse fewer rows than exist.
    ev = ev.replace("|", " / ")
    slug = slug.replace("|", " / ")
    title = title.replace("|", " / ")
    line = (f"| {row} | OPEN | {slug.upper()} -- {title}. {ev} "
            f"ACCEPTANCE: as stated in bd-codex-briefs/row{row}.txt, including the RED-first "
            f"test on the unfixed tree, the explicit precondition assertion, the negative "
            f"control, and exact nonzero counts. DEPENDENCY: none. |")
    lines = txt.splitlines()
    # insert in numeric order among the existing row lines
    idx, last = None, None
    for i, l in enumerate(lines):
        m = re.match(r"^\| (\d{3}) \|", l)
        if m:
            last = i
            if int(m.group(1)) > int(row) and idx is None:
                idx = i
    if idx is None:
        idx = (last + 1) if last is not None else len(lines)
    lines.insert(idx, line)
    reg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"row {row}: inserted at line {idx + 1} ({len(line)} chars)")
