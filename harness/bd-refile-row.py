#!/usr/bin/env python3
"""Re-file a worker's row onto CURRENT main, as OPEN, with a free id.

Two failures make this necessary, both seen repeatedly on 2026-08-26:
 1. A worker's worktree is several merges stale, so its register edit REVERTS
    rows other cuts closed. bd-register-merge.py would refuse (correctly), which
    blocks the cut; rebuilding on current main is the fix.
 2. Workers pre-CLOSE their own row with a GUESSED version. The merge takes the
    row by identity and the close step then never restamps it, so the register's
    evidence pointer names a release that does not contain the change. Always
    re-file OPEN and let the integrator stamp the real version.

  usage: bd-refile-row.py <worktree> <substring-identifying-the-row> <new-id>
"""
import sys, re, hashlib, pathlib, subprocess

wt, marker, new_id = pathlib.Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
p = wt / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
ROW = re.compile(r"^\|\s*(?P<id>\d+)\s*\|\s*(?P<status>[A-Z]+)(?P<ev>[^|]*)\|\s*(?P<text>.+?)\s*\|\s*$")
mine = [l for l in p.read_text(encoding="ascii").splitlines()
        if ROW.match(l) and marker in l]
assert len(mine) == 1, f"marker {marker!r} matched {len(mine)} rows, need exactly 1"
body = mine[0].split("|", 3)[3]
old_id = int(ROW.match(mine[0]).group("id"))
assert not re.search(rf"\b{old_id}\b", body), "row self-references its old id; fix by hand"

cur = subprocess.run(["git", "-C", str(wt), "show",
                      "origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md"],
                     capture_output=True, text=True, check=True).stdout

# DO NOT SYNC THE CHANGELOG HERE. I added that on 2026-08-26 to fix row 263's
# register-vs-changelog gate failing in a stale worktree's QA -- and it caused a
# WORSE problem: every refiled worktree then carried a modified CHANGELOG.md,
# which collides with the trio the INTEGRATOR writes, and bd-qa-row.sh re-stages
# it (git add -N) so merely unstaging it does not stick. Rows 268 and 261 both
# refused to integrate for exactly this reason.
# If a register-vs-changelog gate fails in a worktree's QA, that is worktree
# STALENESS, not a defect in the cut -- judge it on the integrated candidate.
rows = {int(m.group("id")): l for l in cur.splitlines() if (m := ROW.match(l))}
assert new_id not in rows, f"{new_id} already exists on main"
anchor = rows[max(rows)]
cur = cur.replace(anchor, anchor + "\n| %d | OPEN |%s" % (new_id, body.rstrip()), 1)

parsed = [(int(m.group("id")), m.group("status")) for l in cur.splitlines() if (m := ROW.match(l))]
ids = [i for i, _ in parsed]
assert new_id in ids and len(ids) == len(set(ids))
digest = hashlib.sha256(",".join(map(str, ids)).encode("ascii")).hexdigest()
opens = sum(s == "OPEN" for _, s in parsed)
hdr = re.compile(r"^<!-- canonical-task-register schema=1 rows=\d+ open=\d+ ids-sha256=[0-9a-f]{64} -->$", re.M)
assert len(hdr.findall(cur)) == 1
cur = hdr.sub(f"<!-- canonical-task-register schema=1 rows={len(ids)} open={opens} ids-sha256={digest} -->", cur)
cur.encode("ascii")
p.write_text(cur, encoding="ascii")
print(f"re-filed {old_id} -> {new_id} OPEN on current main; rows={len(ids)} open={opens}")
