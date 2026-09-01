#!/bin/bash
# FRESH_HOST_BRINGUP.md step 4 on bd: "the gate (also what installs and starts
# the service)". Two things at once -- it completes the documented bring-up on a
# box that has never run it, and it gives a capture baseline from a genuinely
# clean host to compare against test6's 58 minutes.
set -uo pipefail
IP=10.0.70.50
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight/capture-bd; mkdir -p "$A"
L="$A/run.log"
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
SHA=$(ssh -n -o BatchMode=yes "$IP" 'git -C ~/BulkDownloader rev-parse --short HEAD' 2>/dev/null)
say "capture.sh on bd at $SHA (fresh host, first ever run)"
timeout 7200 ssh -n -o BatchMode=yes "$IP" \
  'cd ~/BulkDownloader && bash capture.sh >/tmp/bd-cap.log 2>&1; echo rc=$?' >"$A/rc.log" 2>&1
say "rc: $(grep -o 'rc=[0-9]*' "$A/rc.log" | head -1)"
timeout 180 ssh -n -o BatchMode=yes "$IP" '
  t=$(ls -1td /tmp/bd_capture-*.tar.gz 2>/dev/null | head -1)
  [ -n "$t" ] || { echo "NO BUNDLE"; exit 0; }
  echo "bundle=$(basename "$t")"
  tar xzOf "$t" --wildcards "*10_VERDICT.txt" 2>/dev/null | head -2
  tar xzOf "$t" --wildcards "*02_SUMMARY.txt" 2>/dev/null | sed -n "1,8p"' >>"$L" 2>&1
say "service after capture: $(ssh -n -o BatchMode=yes $IP 'systemctl is-active bulkdownloader 2>/dev/null || echo inactive' 2>&1 | tail -1)"
say "health: $(ssh -n -o BatchMode=yes $IP 'curl -s -m 5 -o /dev/null -w %{http_code} http://127.0.0.1:5555/api/health' 2>&1 | tail -1)"
say "=== CAPTURE ON BD COMPLETE ==="
