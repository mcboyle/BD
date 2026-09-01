#!/bin/bash
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"; R=/home/mboyle/BulkDownloader
say(){ printf '%s [finish-1305] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
say "watching v1305 to merge, then fleet deploy"
merged=0
for _ in $(seq 1 240); do
  git -C "$R" fetch origin main -q 2>/dev/null
  V=$(git -C "$R" show origin/main:bulk_downloader/__init__.py 2>/dev/null | grep -oE '3\.66\.[0-9]+' | head -1); MV=${V##*.}
  if [ -n "$V" ] && [ "${MV:-0}" -ge 1305 ] 2>/dev/null; then merged=1; say "main reached $V"; break; fi
  PR=$(gh pr list --repo mcboyle/BD --state open --json number,headRefName \
        -q '.[]|select(.headRefName|startswith("cut/1305"))|.number' 2>/dev/null | head -1)
  if [ -n "$PR" ]; then
    ST=$(gh pr checks "$PR" --repo mcboyle/BD 2>/dev/null | grep -v CodeRabbit | awk -F'\t' '{print $2}')
    if [ -n "$ST" ] && ! printf '%s\n' "$ST" | grep -q pending; then
      if printf '%s\n' "$ST" | grep -q fail; then
        say "PR #$PR CI RED: $(gh pr checks "$PR" --repo mcboyle/BD 2>/dev/null | awk -F'\t' '$2=="fail"{printf "%s ", $1}')"
      else say "PR #$PR green -- merging"; gh pr merge "$PR" --repo mcboyle/BD --merge >>"$L" 2>&1 && say "merged PR #$PR"; fi
    fi
  fi
  sleep 30
done
[ "$merged" = 1 ] || { say "GAVE UP: v1305 never merged"; exit 1; }
for _ in $(seq 1 90); do pgrep -f 'bd-verify-cut\.sh|bd-row-chain\.sh|bd-integrate-row\.sh' >/dev/null 2>&1 || break; sleep 20; done
# RESPECT THE OPERATOR HOLD EXPLICITLY. bd-fleet-deploy.sh refuses with exit 4
# while the hold file exists, and reporting that as "DEPLOY REPORTED FAILURES"
# would read as an incident when it is the operator testing by hand.
if [ -f /home/mboyle/.config/bd/DEPLOY_HOLD ]; then
  say "MERGED, DEPLOY HELD by operator -- fleet left untouched at its current version"
  exit 0
fi
say "lane quiet -- deploying the fleet"
bash /home/mboyle/bd-fleet-deploy.sh >>"$A/deploy/finish1305-$(date -u +%Y%m%dT%H%M%SZ).log" 2>&1 \
  && say "FLEET DEPLOY COMPLETE" || say "FLEET DEPLOY REPORTED FAILURES -- see $A/deploy/"
