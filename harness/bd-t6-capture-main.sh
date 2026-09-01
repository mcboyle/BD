#!/bin/bash
# FULL CAPTURE ON CURRENT MAIN. Two narrower instruments have now failed to
# reproduce the six failures -- a 4-file matched run and a 6-version bisect,
# both 482-passed everywhere. The mechanism therefore needs the full 18,499-test
# co-residency, which is exactly the width lesson row 327 taught and I have now
# had to relearn twice. So stop narrowing: run the real thing.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25
OUT=$A/inflight/t6-capture-main; mkdir -p "$OUT"; L="$OUT/summary.txt"
IP=10.0.70.249; R=/home/mboyle/BulkDownloader
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
git -C "$R" fetch -q origin 2>/dev/null
SHA=$(git -C "$R" rev-parse origin/main)
V=$(git -C "$R" show origin/main:bulk_downloader/__init__.py|grep -oE '3\.66\.[0-9]+')
say "capture at $(echo $SHA|cut -c1-8) (v$V) on test6"
timeout 5400 ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no "$IP" \
  "cd ~/BulkDownloader && git fetch -q origin && git checkout -q main && git reset -q --hard origin/main && \
   bash capture.sh >/tmp/bd-cap-main.log 2>&1; echo rc=\$?" > "$OUT/run.log" 2>&1
say "rc: $(grep -o 'rc=[0-9]*' "$OUT/run.log"|head -1)"
timeout 180 ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no "$IP" '
  t=$(ls -1td /tmp/bd_capture-*.tar.gz|head -1); echo "bundle=$(basename $t)"
  tar xzOf "$t" --wildcards "*10_VERDICT.txt" 2>/dev/null|head -2
  tar xzOf "$t" --wildcards "*02_SUMMARY.txt" 2>/dev/null|sed -n "1,6p"
  echo "--- failing tests ---"
  tar xzOf "$t" --wildcards "*_pytest_*.xml" 2>/dev/null | python3 -c "
import sys,re
d=sys.stdin.read()
for m in re.finditer(r\"<testcase classname=.([^\\\"]+). name=.([^\\\"]+).[^/>]*>\s*<(failure|error)\", d):
    print(\"  \"+m.group(1).split(\".\")[-1]+\"::\"+m.group(2))
"' >> "$L" 2>&1
say "=== MAIN CAPTURE COMPLETE ==="
