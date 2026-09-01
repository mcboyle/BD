#!/usr/bin/env python3
"""Renumber the row a worker filed in ITS OWN worktree to a free id.

Parallel workers allocate against the register they can SEE, so they collide --
three separate workers filed row 250 on 2026-08-26. Never overwrite the
incumbent; move the newcomer. The header is recomputed with the gate's own
parser so the count and digest cannot drift from the rows.

  usage: bd-renumber-row.py <worktree> <old-id> <new-id>
"""
import sys, re, hashlib, pathlib

wt, old, new = pathlib.Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
p = wt / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
t = p.read_text(encoding="ascii")
ROW = re.compile(r"^\|\s*(?P<id>\d+)\s*\|\s*(?P<status>[A-Z]+)(?P<ev>[^|]*)\|\s*(?P<text>.+?)\s*\|\s*$")
rows = {int(m.group("id")): l for l in t.splitlines() if (m := ROW.match(l))}
assert old in rows, f"row {old} not present in {p}"
assert new not in rows, f"row {new} already exists here"
line = rows[old]
body = line.split("|", 3)[3]
# a row that names its own id in prose would point at nothing after the move --
# row 246's whole subject. Refuse rather than ship a dangling reference.
assert not re.search(rf"\b{old}\b", body), (
    f"row {old} references its own id in prose; renumber those references by hand")
t = t.replace(line, re.sub(r"^\|\s*\d+\s*\|", f"| {new} |", line, count=1), 1)
parsed = [(int(m.group("id")), m.group("status")) for l in t.splitlines() if (m := ROW.match(l))]
ids = [i for i, _ in parsed]
assert new in ids and old not in ids, "renumber did not take"
assert len(ids) == len(set(ids)), "duplicate ids after renumber"
digest = hashlib.sha256(",".join(map(str, ids)).encode("ascii")).hexdigest()
opens = sum(s == "OPEN" for _, s in parsed)
hdr = re.compile(r"^<!-- canonical-task-register schema=1 rows=\d+ open=\d+ ids-sha256=[0-9a-f]{64} -->$", re.M)
assert len(hdr.findall(t)) == 1
t = hdr.sub(f"<!-- canonical-task-register schema=1 rows={len(ids)} open={opens} ids-sha256={digest} -->", t)
t.encode("ascii")
p.write_text(t, encoding="ascii")
print(f"{old} -> {new}; rows={len(ids)} open={opens}")
