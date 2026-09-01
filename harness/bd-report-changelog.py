#!/usr/bin/env python3
"""Append a CHANGELOG block to a codex worker report, derived from its real diff.

bd-integrate-row.sh builds the changelog by scanning the LAST 400 LINES of the
worker report for ASCII lines starting with "- " (20..300 chars) and needs at
least two, or it aborts the whole cut with "no changelog body extracted from the
worker report". Reports written without that shape refuse to integrate -- which
is what happened to all 15 rows on 2026-08-29.

The bullets here are MEASURED FROM THE DIFF, not invented: the row's own title,
the counts of source/test files touched, the new test files by name, and the
insertion count. A changelog that says something false is worse than a thin one,
so nothing is claimed that the diff does not show.

  usage: bd-report-changelog.py <row> [<row> ...]
"""
import pathlib, re, subprocess, sys

SPEC = pathlib.Path("/home/mboyle/bd-night-spec.txt")
CUTS = pathlib.Path("/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts")
WT = pathlib.Path("/home/mboyle/bd-codex-wt")
MARK = "<!-- bd-report-changelog -->"


def title_for(row):
    for l in SPEC.read_text().splitlines():
        m = re.match(rf"^#?\s*{row}\|([^|]+)\|(.*)$", l)
        if m:
            return m.group(2).strip()
    return ""


def diff_facts(row):
    w = WT / f"row{row}"
    if not w.is_dir():
        return None
    out = subprocess.run(["git", "-C", str(w), "diff", "--numstat", "HEAD"],
                         capture_output=True, text=True).stdout.splitlines()
    unt = subprocess.run(["git", "-C", str(w), "ls-files", "--others",
                          "--exclude-standard"], capture_output=True, text=True).stdout.split()
    files, ins = [], 0
    for l in out:
        p = l.split("\t")
        if len(p) == 3:
            files.append(p[2])
            if p[0].isdigit():
                ins += int(p[0])
    files += unt
    tests = [f for f in files if f.startswith("tests/") and "/test" in "/" + f]
    src = [f for f in files if f.startswith("bulk_downloader/")]
    front = [f for f in files if f.startswith("frontend/")]
    return {"files": files, "ins": ins, "tests": tests, "src": src, "front": front}


for row in sys.argv[1:]:
    rp = CUTS / f"row{row}.txt"
    if not rp.is_file():
        print(f"row {row}: no report -- skipped"); continue
    txt = rp.read_text(encoding="utf-8", errors="replace")
    if MARK in txt:
        print(f"row {row}: already has a changelog block -- left alone"); continue
    f = diff_facts(row)
    if not f:
        print(f"row {row}: no worktree -- skipped"); continue
    t = title_for(row)
    bullets = []
    if t:
        bullets.append(f"- {t[:74]}")
    if f["src"]:
        bullets.append(f"- application: {len(f['src'])} file(s) in bulk_downloader/ changed")
    if f["front"]:
        bullets.append(f"- frontend: {len(f['front'])} file(s) changed")
    for tf in f["tests"][:3]:
        bullets.append(f"- test: {pathlib.Path(tf).name[:62]}")
    bullets.append(f"- {len(f['files'])} file(s), {f['ins']} insertion(s) in the worker diff")
    # the extractor needs >= 2 usable bullets; refuse rather than emit a stub
    usable = [b for b in bullets if 20 < len(b) < 300 and all(ord(c) < 128 for c in b)]
    if len(usable) < 2:
        print(f"row {row}: only {len(usable)} usable bullet(s) -- REFUSING to write a stub")
        continue
    rp.write_text(txt.rstrip("\n") + "\n\n" + MARK + "\nCHANGELOG:\n" + "\n".join(usable) + "\n",
                  encoding="utf-8")
    print(f"row {row}: appended {len(usable)} bullet(s)")
