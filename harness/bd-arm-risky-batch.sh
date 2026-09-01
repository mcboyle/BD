#!/bin/bash
# OPERATOR-AUTHORIZED 2026-08-27: batch the four risky rows together, under the
# unchanged ruleset (file-disjointness, width ladder, attempt budget).
#
# Waits until ONLY the risky four remain unmerged, then clears SOLO_ROWS. Waiting
# matters: cleared early, the grouper would pack row 295 (the largest) together
# with still-pending SAFE rows, which is not what was asked for and would put
# safe work behind the riskiest cut in the queue.
#
# The grouper decides the rest. Measured now: 261 shares 13 real files with 295
# (app.py, global_config.py, db_replication.py, bd-claim...), so the ruleset
# yields "296 295 243" + "261" alone. That is the maximum the rules permit;
# forcing 261 in would just be a guaranteed patch conflict.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"; R=/home/mboyle/BulkDownloader
RISKY="296 261 295 243"
say(){ printf '%s [arm-risky] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
say "armed: will batch the risky four once every safe row has merged"
for _ in $(seq 1 960); do
  git -C "$R" fetch origin main -q 2>/dev/null
  REG=$(git -C "$R" show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null)
  if [ -z "$REG" ]; then sleep 30; continue; fi        # unread register proves nothing
  left=""
  for r in $(grep -oE '^[0-9]+' /home/mboyle/bd-night-spec.txt); do
    case " $RISKY " in *" $r "*) continue;; esac       # risky rows are not the trigger
    printf '%s' "$REG" | grep -qE "^\| $r \|[[:space:]]*CLOSED" || left="$left $r"
  done
  if [ -z "$left" ]; then
    say "every safe row merged -- clearing SOLO_ROWS so the risky four batch"
    python3 - <<'PY'
import pathlib, re
p = pathlib.Path("/home/mboyle/bd-batch-rows.py"); s = p.read_text(encoding="utf-8")
m = re.search(r'SOLO_ROWS = set\(os\.environ\.get\("SOLO_ROWS", "[^"]*"\)\.split\(\)\)', s)
assert m, "SOLO_ROWS line not found -- refusing"
s = s.replace(m.group(0), 'SOLO_ROWS = set(os.environ.get("SOLO_ROWS", "").split())')
p.write_text(s, encoding="utf-8")
import ast; ast.parse(s); print("SOLO_ROWS cleared")
PY
    say "done -- the grouper now decides; bd-night re-reads it every pass, no restart needed"
    exit 0
  fi
  say "holding: safe row(s) still unmerged:$left"
  sleep 120
done
say "GAVE UP after 8h -- SOLO_ROWS left in place, risky four still ship alone"
