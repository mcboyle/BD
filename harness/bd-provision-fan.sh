#!/bin/bash
# Fan the provisioner out to bd1-bd4 ONLY after bd proves the documented path
# works. Provisioning four boxes against a broken document would just reproduce
# one failure four times; rows 341/343 exist because that path was NOT
# executable, and bd is the instrument that says whether they fixed it.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
L="$A/provision-fan.log"
say(){ printf '%s [fan] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
while [ ! -f "$A/freshhost-50/run.log" ] || ! grep -q 'BRING-UP COMPLETE' "$A/freshhost-50/run.log"; do sleep 60; done
if ! grep -qE 'VERDICT: READY' "$A/freshhost-50/provision.log" 2>/dev/null; then
  say "bd did NOT reach VERDICT: READY -- refusing to fan out. That is a finding about the documented path, not a reason to try three more."
  grep -nE 'VERDICT|FAIL|UNKNOWN' "$A/freshhost-50/provision.log" 2>/dev/null | tail -10 >> "$L"
  exit 1
fi
say "bd READY -- provisioning bd1..bd4 in parallel"
SHA=$(git -C /home/mboyle/BulkDownloader rev-parse origin/main)
for ip in 51 52 53 54; do
 ( ssh -n -o BatchMode=yes 10.0.70.$ip "rm -rf ~/BulkDownloader && git clone -q mboyle@10.0.70.164:/home/mboyle/BulkDownloader ~/BulkDownloader && cd ~/BulkDownloader && git fetch -q origin && git checkout -q $SHA" >"$A/provision-$ip.log" 2>&1
   timeout 5400 ssh -n -o BatchMode=yes 10.0.70.$ip 'cd ~/BulkDownloader && ./scripts/provision_test_host.sh' >>"$A/provision-$ip.log" 2>&1
   v=$(grep -oE 'VERDICT: [A-Z ]+' "$A/provision-$ip.log" | tail -1)
   say "10.0.70.$ip -> ${v:-NO VERDICT LINE}" ) &
done
wait
say "=== FAN COMPLETE ==="
