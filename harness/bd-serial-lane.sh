#!/bin/bash
# Run QA-green cuts through bd-row-chain.sh ONE AT A TIME, in the given order.
# Serial is not a style choice: bd-integrate-row.sh derives the version from
# main, so two concurrent chains both claim main+1 and collide.
#   usage: bd-serial-lane.sh "row|slug|title" "row|slug|title" ...
# Stops on the first failure -- the operator ruled GRIND UNTIL GREEN, so a
# failure wants a human decision, not the next cut started on top of it.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/SERIAL_LANE.log"
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >> "$L"; }
say "=== serial lane start: $# cut(s) ==="
for spec in "$@"; do
  IFS='|' read -r ROW SLUG TITLE <<<"$spec"
  # main must be quiet before we derive a version from it
  for _ in $(seq 1 240); do
    pgrep -f "bd-row-chain.sh " >/dev/null || break
    sleep 15
  done
  git -C /home/mboyle/BulkDownloader fetch --quiet origin 2>/dev/null
  CUR=$(git -C /home/mboyle/BulkDownloader show origin/main:bulk_downloader/__init__.py 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  NEXT=$(( ${CUR##*.} + 1 ))
  say "--- $ROW -> v3.66.$NEXT (main is $CUR) ---"
  if bash /home/mboyle/bd-row-chain.sh "$ROW" "$NEXT" "$SLUG" "$TITLE"; then
    say "OK $ROW merged"
  else
    say "STOP: $ROW did not merge. Chain log: $A/inflight/chain-$ROW.log"
    say "Remaining not attempted: the operator ruled grind-until-green, so this needs a decision."
    exit 1
  fi
done
say "=== serial lane complete ==="
