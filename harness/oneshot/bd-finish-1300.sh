#!/bin/bash
# Carry v1300 to merge, then deploy the fleet. Watches ship->merge EXPLICITLY:
# twice on 2026-08-27 a PR went green AFTER its chain had refused, and nothing
# merged it (PRs #566 and #568).
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"; R=/home/mboyle/BulkDownloader
say(){ printf '%s [finish-1300] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
say "watching v1300 through to merge, then fleet deploy"
merged=0
for _ in $(seq 1 240); do
  git -C "$R" fetch origin main -q 2>/dev/null
  V=$(git -C "$R" show origin/main:bulk_downloader/__init__.py 2>/dev/null | grep -oE '3\.66\.[0-9]+' | head -1)
  MV=${V##*.}
  if [ -n "$V" ] && [ "${MV:-0}" -ge 1300 ] 2>/dev/null; then merged=1; say "main reached $V"; break; fi
  PR=$(gh pr list --repo mcboyle/BD --state open --json number,headRefName \
        -q '.[]|select(.headRefName|startswith("cut/1300"))|.number' 2>/dev/null | head -1)
  if [ -n "$PR" ]; then
    ST=$(gh pr checks "$PR" --repo mcboyle/BD 2>/dev/null | grep -v CodeRabbit | awk -F'\t' '{print $2}')
    if [ -n "$ST" ] && ! printf '%s\n' "$ST" | grep -q pending; then
      if printf '%s\n' "$ST" | grep -q fail; then
        say "PR #$PR CI RED: $(gh pr checks "$PR" --repo mcboyle/BD 2>/dev/null | awk -F'\t' '$2=="fail"{printf "%s ", $1}')"
      else
        say "PR #$PR green and unmerged -- merging explicitly"
        gh pr merge "$PR" --repo mcboyle/BD --merge >>"$L" 2>&1 && say "merged PR #$PR"
      fi
    fi
  fi
  sleep 30
done
[ "$merged" = 1 ] || { say "GAVE UP: v1300 never merged; NO deploy"; exit 1; }
for _ in $(seq 1 60); do
  pgrep -f 'bd-verify-cut\.sh|bd-row-chain\.sh|bd-integrate-row\.sh' >/dev/null 2>&1 || break
  sleep 20
done
say "lane quiet -- deploying the fleet"
if bash /home/mboyle/bd-fleet-deploy.sh >>"$A/deploy/finish1300-$(date -u +%Y%m%dT%H%M%SZ).log" 2>&1; then
  say "FLEET DEPLOY COMPLETE"
else
  say "FLEET DEPLOY REPORTED FAILURES -- see $A/deploy/, NOT claiming health"
fi
