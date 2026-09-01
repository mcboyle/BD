#!/bin/bash
# bd1..bd4: provision, then install codex, then register them as build hosts.
# Gated on bd reaching VERDICT: READY -- provisioning four boxes against a
# documented path that does not work would reproduce one failure four times.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
L="$A/vm-bringup.log"
say(){ printf '%s [vm] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
while ! grep -qE "VERDICT: READY" "$A/freshhost-50/provision.log" 2>/dev/null; do sleep 60; done
if ! grep -qE 'VERDICT: READY' "$A/freshhost-50/provision.log" 2>/dev/null; then
  say "bd did NOT reach VERDICT: READY. Refusing to fan out -- that is a FINDING about the documented bring-up (rows 341/343), not a reason to try three more boxes."
  grep -nE 'VERDICT|FAIL|UNKNOWN|ERROR' "$A/freshhost-50/provision.log" 2>/dev/null | tail -15 >> "$L"
  exit 1
fi
say "bd READY -- fanning bd1..bd4"
SHA=$(git -C /home/mboyle/BulkDownloader rev-parse origin/main)
for ip in 51 52 53 54; do
 ( H=10.0.70.$ip; L2="$A/vm-$ip.log"
   ssh -n -o BatchMode=yes "$H" 'rm -rf ~/BulkDownloader ~/bd.git && git init -q --bare ~/bd.git' >"$L2" 2>&1
   git -C /home/mboyle/BulkDownloader push -q --force "mboyle@$H:/home/mboyle/bd.git" "$SHA:refs/heads/main" >>"$L2" 2>&1
   ssh -n -o BatchMode=yes "$H" "git clone -q ~/bd.git ~/BulkDownloader && cd ~/BulkDownloader && git checkout -q $SHA" >>"$L2" 2>&1
   timeout 5400 ssh -n -o BatchMode=yes "$H" 'cd ~/BulkDownloader && ./scripts/provision_test_host.sh' >>"$L2" 2>&1
   v=$(grep -oE 'VERDICT: [A-Z ]+' "$L2" | tail -1)
   say "$H provision -> ${v:-NO VERDICT}"
   case "$v" in *READY*)
     # codex: the standalone package plus config and credential. Operator
     # authorized 2026-08-28; these are throwaway boxes on the private network.
     ssh -n -o BatchMode=yes "$H" 'mkdir -p ~/.codex ~/.local/bin' >>"$L2" 2>&1
     rsync -a -e 'ssh -o BatchMode=yes' /home/mboyle/.codex/packages "mboyle@$H:/home/mboyle/.codex/" >>"$L2" 2>&1
     rsync -a -e 'ssh -o BatchMode=yes' /home/mboyle/.codex/config.toml /home/mboyle/.codex/auth.json "mboyle@$H:/home/mboyle/.codex/" >>"$L2" 2>&1
     ssh -n -o BatchMode=yes "$H" 'chmod 600 ~/.codex/auth.json; ln -sfn ~/.codex/packages/standalone/current/bin/codex ~/.local/bin/codex; ~/.local/bin/codex --version' >>"$L2" 2>&1 \
       && say "$H codex OK: $(ssh -n -o BatchMode=yes $H '~/.local/bin/codex --version' 2>/dev/null | tail -1)" \
       || say "$H codex INSTALL FAILED -- see $L2"
   ;; *) say "$H not READY -- skipping codex install" ;;
   esac ) &
done
wait
say "=== VM BRING-UP COMPLETE ==="
