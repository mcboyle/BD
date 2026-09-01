#!/bin/bash
# Install ollama on bd once provisioning finishes. FRESH_HOST_BRINGUP.md step 3b:
# a migrated app_config.json with ai_enabled=true makes live test L17 a HARD
# capture FAIL when ollama is absent (L18/L19 WARN) -- measured on the first
# fresh-host run. Installing it removes the failure mode instead of explaining it.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
L="$A/ollama-50.log"; IP=10.0.70.50
say(){ printf '%s [ollama] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
while ! grep -q 'BRING-UP COMPLETE' "$A/freshhost-50/run.log" 2>/dev/null; do sleep 60; done
say "provisioning done -- checking whether the AI backend is even expected"
say "ai_enabled on bd: $(ssh -n -o BatchMode=yes $IP 'grep -o "\"ai_enabled\"[^,]*" ~/BulkDownloader/app_config.json 2>/dev/null || echo "(no app_config.json -- fresh box)"' 2>&1 | tail -1)"
say "installing ollama"
ssh -n -o BatchMode=yes "$IP" 'curl -fsSL https://ollama.com/install.sh | sh' >>"$L" 2>&1
say "install rc=$?; version: $(ssh -n -o BatchMode=yes $IP 'ollama --version 2>&1 | tail -1' 2>&1 | tail -1)"
ssh -n -o BatchMode=yes "$IP" 'systemctl is-active ollama 2>/dev/null || sudo systemctl enable --now ollama 2>&1 | tail -2' >>"$L" 2>&1
say "service: $(ssh -n -o BatchMode=yes $IP 'systemctl is-active ollama' 2>&1 | tail -1)"
# the model the live test expects, read from the tree rather than guessed
M=$(ssh -n -o BatchMode=yes "$IP" 'grep -rhoE "\"(llama[0-9.:a-z-]+|qwen[0-9.:a-z-]+|mistral[0-9.:a-z-]*)\"" ~/BulkDownloader/bulk_downloader/*.py 2>/dev/null | tr -d "\"" | sort | uniq -c | sort -rn | head -1 | awk "{print \$2}"' 2>/dev/null | tail -1)
say "model named by the tree: ${M:-none found -- leaving the pull to the operator}"
[ -n "$M" ] && { ssh -n -o BatchMode=yes "$IP" "ollama pull $M" >>"$L" 2>&1; say "pull $M rc=$?"; }
say "=== OLLAMA STEP COMPLETE ==="
