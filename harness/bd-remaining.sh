#!/bin/bash
# How many queued task items remain UNMERGED. Measured from origin/main's
# register at call time -- never a number carried in prose (A1: do not copy
# counts, re-derive them).
set -u
R=/home/mboyle/BulkDownloader
QUEUE="237 182 185 228 26 27 121 174 175 176 183 184 221 229 235 241 242"
PARKED="120 122 124 126"
git -C "$R" fetch --quiet origin 2>/dev/null
TMP=$(mktemp); git -C "$R" show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md > "$TMP" 2>/dev/null
python3 - "$TMP" "$QUEUE" "$PARKED" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
RE = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+)\|", re.M)
st = {int(m.group(1)): m.group(2).strip() for m in RE.finditer(text)}
q = [int(x) for x in sys.argv[2].split()]
parked = [int(x) for x in sys.argv[3].split()]
closed = [r for r in q if r in st and not st[r].startswith("OPEN")]
openq  = [r for r in q if r not in closed]
print(f"QUEUE {len(q)} | MERGED {len(closed)} | UNMERGED {len(openq)} | PARKED {len(parked)}")
print("  merged:   " + (", ".join(f"{r}({st[r].split('@')[-1]})" for r in sorted(closed)) or "none"))
print("  unmerged: " + ", ".join(str(r) for r in sorted(openq)))
PY
rm -f "$TMP"
