#!/bin/bash
# FRESH-HOST BRING-UP TEST on 10.0.70.50 (bd), a genuinely untouched Ubuntu
# 24.04 box. Rows 343 (@v1321) and 341 (@v1322) fixed the documented bring-up
# after the install/deploy hunt found it was NOT EXECUTABLE and that READY was
# claimed without the artifact that defines it. Both are merged; this is the
# only instrument that can prove them, and it has never been run on a fresh box
# since. Follows docs/repo/FRESH_HOST_BRINGUP.md steps 1-2 EXACTLY as written --
# any deviation here would test my transcription, not the document.
set -uo pipefail
IP=10.0.70.50
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight/freshhost-50
mkdir -p "$A"; L="$A/run.log"
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
SHA=$(git -C /home/mboyle/BulkDownloader rev-parse origin/main)
say "fresh-host bring-up on $IP at $(echo $SHA|cut -c1-8)"
say "pre-state:"
ssh -n -o BatchMode=yes "$IP" 'ls -d ~/BulkDownloader 2>/dev/null && echo "TREE EXISTS -- not fresh" || echo "no tree (fresh)"; which python3 git; ls /var/lib/bulkdownloader 2>/dev/null || echo "no /var/lib/bulkdownloader"' >>"$L" 2>&1
# step 1: the checkout the provisioner cannot make. Clone from THIS host over
# ssh so no GitHub credential is needed on the new box.
say "step 1: clone"
ssh -n -o BatchMode=yes "$IP" 'rm -rf ~/BulkDownloader ~/bd.git && git init -q --bare ~/bd.git' >>"$L" 2>&1
git -C /home/mboyle/BulkDownloader push -q --force "mboyle@$IP:/home/mboyle/bd.git" "$SHA:refs/heads/main" >>"$L" 2>&1
ssh -n -o BatchMode=yes "$IP" "git clone -q ~/bd.git ~/BulkDownloader && cd ~/BulkDownloader && git checkout -q $SHA && git rev-parse --short HEAD && git status --porcelain | head -3" >>"$L" 2>&1
say "clone rc=$?"
# step 2: THE ONE COMMAND. Proceed only on VERDICT: READY.
say "step 2: provision_test_host.sh (this is the subject under test)"
timeout 5400 ssh -n -o BatchMode=yes "$IP" 'cd ~/BulkDownloader && ./scripts/provision_test_host.sh' >"$A/provision.log" 2>&1
rc=$?
say "provision rc=$rc"
say "verdict line(s):"
grep -nE 'VERDICT|READY|NOT READY|FAIL|UNKNOWN' "$A/provision.log" | tail -20 | tee -a "$L"
say "=== FRESH-HOST BRING-UP COMPLETE (rc=$rc) ==="
