#!/bin/bash
# VERSION COLLISIONS ARE NOT FAILURES. Frozen cuts are a serial pipeline: only
# one candidate may claim a version, so every queued row behind the current one
# is refused with "ALREADY CLAIMED" and charged an attempt. Three of those and a
# perfectly good row hits the cap and is skipped for the night. This clears the
# counter for rows whose ONLY refusal was a collision, leaving real failures to
# accumulate normally.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25
while true; do
  for f in "$A"/night/att-*; do
    [ -e "$f" ] || continue
    r=$(basename "$f"|sed 's/^att-//')
    log="$A/inflight/integrate-$r.log"
    [ -f "$log" ] || continue
    last=$(tail -3 "$log")
    if printf '%s' "$last" | grep -q 'ALREADY CLAIMED'; then
      printf '%s [att-guard] row %s refused only by a version collision -- attempt not charged\n' \
        "$(date -u +%H:%M:%S)" "$r" >> "$A/FINISH.log"
      rm -f "$f"
    fi
  done
  sleep 60
done
