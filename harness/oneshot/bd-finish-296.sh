#!/bin/bash
# Carry row 296 to merge, then let bd-arm-deploy fire. Watches ship->merge
# EXPLICITLY: a PR that goes green after its chain refused is merged by nobody,
# which stalled #566 for 20 minutes tonight.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"; R=/home/mboyle/BulkDownloader
say(){ printf '%s [finish-296] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
say "watching row 296 through to merge"
for _ in $(seq 1 180); do
  git -C "$R" fetch origin main -q 2>/dev/null
  V=$(git -C "$R" show origin/main:bulk_downloader/__init__.py 2>/dev/null | grep -oE '3\.66\.[0-9]+' | head -1)
  MV=${V##*.}
  [ -n "$V" ] && [ "${MV:-0}" -ge 1299 ] 2>/dev/null && { say "main reached $V -- 296 is in"; exit 0; }
  PR=$(gh pr list --repo mcboyle/BD --state open --json number,headRefName \
        -q '.[]|select(.headRefName|startswith("cut/1299"))|.number' 2>/dev/null | head -1)
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
say "GAVE UP after 90m -- row 296 never merged"
