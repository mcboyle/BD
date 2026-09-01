#!/usr/bin/env python3
"""Merge a worker's OWN register rows into a cut worktree's backlog, by identity.

  usage: bd-register-merge.py <cut-worktree> <worker-worktree>

THE REGISTER IS MERGED BY ROW, NEVER BY PATCH. A worker's IMPROVEMENT_BACKLOG
edit is written against the main it was dispatched on, and every merge since has
rewritten row statuses around it, so a textual patch conflicts almost by
construction (row 241 died exactly there).

WHICH ROWS ARE "THE WORKER'S OWN" is decided against the worker's OWN MERGE-BASE,
not against whatever the cut tree happens to hold. A row the worker never touched
must not be carried across: that would launder another worker's unmerged work
into this cut, and rows 333/340/341/348/349 already lost register rows to
side-picking on 2026-08-28.
"""
import pathlib, re, subprocess, sys

ROW = re.compile(r"^\|\s*(\d+)\s*\|")
REL = "project-knowledge/IMPROVEMENT_BACKLOG.md"


def rows_of(text):
    out = {}
    for line in text.split("\n"):
        m = ROW.match(line)
        if m:
            out.setdefault(int(m.group(1)), line)
    return out


def git(wt, *args):
    return subprocess.run(["git", "-C", str(wt), *args],
                          capture_output=True, text=True)


def main():
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    nw, worker = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    nw_file, wk_file = nw / REL, worker / REL
    for p in (nw_file, wk_file):
        if not p.is_file():
            print(f"missing backlog: {p}", file=sys.stderr)
            return 3

    # the worker's base copy -- what the register looked like when it started
    mb = git(worker, "merge-base", "HEAD", "origin/main").stdout.strip()
    if not mb:
        print("cannot resolve the worker's merge-base -- UNKNOWN, refusing",
              file=sys.stderr)
        return 3
    base = git(worker, "show", f"{mb}:{REL}")
    if base.returncode != 0:
        print(f"cannot read {REL} at {mb[:8]} -- UNKNOWN, refusing", file=sys.stderr)
        return 3

    base_rows = rows_of(base.stdout)
    wk_rows = rows_of(wk_file.read_text(encoding="utf-8"))
    nw_text = nw_file.read_text(encoding="utf-8")
    nw_rows = rows_of(nw_text)

    # ADDED by this worker, and not already present in the cut tree
    added = sorted(n for n in wk_rows if n not in base_rows and n not in nw_rows)
    # CHANGED by this worker on a row that already existed: leave it alone. The
    # cut tree's copy came from main, which is newer than the worker's base.
    if not added:
        print("no rows to merge (worker added none the cut tree lacks)")
        return 0

    lines = nw_text.split("\n")
    for n in added:
        line = wk_rows[n]
        numbered = [(i, int(m.group(1)))
                    for i, L in enumerate(lines) if (m := ROW.match(L))]
        if not numbered:
            print("no numbered rows in the cut tree's register -- refusing to guess",
                  file=sys.stderr)
            return 3
        after = [i for i, v in numbered if v > n]
        idx = after[0] if after else numbered[-1][0] + 1
        lines.insert(idx, line)
        print(f"row {n}: inserted at line {idx + 1}")

    out = "\n".join(lines)
    expected = len(nw_text) + sum(len(wk_rows[n]) + 1 for n in added)
    if len(out) != expected:
        print(f"length arithmetic failed ({len(out)} != {expected}) -- refusing",
              file=sys.stderr)
        return 3
    for n in added:
        if len(re.findall(rf"^\|\s*{n}\s*\|", out, re.M)) != 1:
            print(f"row {n} would appear more than once -- refusing", file=sys.stderr)
            return 3
    nw_file.write_text(out, encoding="utf-8")
    print(f"merged {len(added)} row(s): {added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
