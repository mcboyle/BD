#!/usr/bin/env bash
# Persist everything that would be lost, every 30 minutes, forever.
#
# WHAT "EVERYTHING" MEANS HERE, learned the hard way: the codex worktrees are the
# volatile part. `git diff HEAD` cannot see a worker's UNTRACKED new files, and
# for several rows the new test file IS the cut -- so capture_codex.sh tars them
# separately. A capture loop piped into `head` also dies on SIGPIPE partway
# through, which silently lost 52 of 65 patches once; it writes then reads.
#
# Runs capture -> sync -> VERIFY, and logs the verify VERDICT, not the exit code
# of the last command. A capture that "succeeded" into an unrestorable archive is
# the failure this guards against.
set -uo pipefail
R=/home/mboyle
P=$R/bd-persist
LOG=$P/continuity/persist-loop.log   # NOT a dated run directory; see bd-checkpoint-write
mkdir -p "$P/continuity"
INT="${BD_PERSIST_INTERVAL:-1800}"
ONCE=0; [ "${1:-}" = "--once" ] && ONCE=1

while true; do
  ts=$(date -u +%H:%M:%SZ)
  {
    bash "$P/capture_codex.sh" >/dev/null 2>&1
    # tools and harness: copy only what exists, never fail the loop on one gap
    # A GLOB IS A DENOMINATOR CHOICE. *.sh plus *.py silently excluded six
    # EXTENSIONLESS executables -- among them bd-checkpoint-write, the very tool
    # that writes the continuity state this loop exists to preserve.
    for f in "$R"/bd-*; do
      [ -f "$f" ] && [ -x "$f" ] || continue
      case "$f" in *.log|*.tar.gz|*.bak|*.bak-*|*.pre*) continue;; esac
      cp -f "$f" "$P/harness/" 2>/dev/null
    done
    for h in 10.0.70.249 10.0.70.51; do
      scp -q -o BatchMode=yes -o ConnectTimeout=8 "$h":'~/*.py' "$P/scripts/" 2>/dev/null
    done
    cp -f "$R/bd-night-spec.txt" "$P/" 2>/dev/null
    cp -f "$R"/bd-codex-briefs/row*.txt "$P/bd-codex-briefs/" 2>/dev/null
    cp -f "$R/BulkDownloader/project-knowledge/IMPROVEMENT_BACKLOG.md" "$P/" 2>/dev/null
    cp -f "$R/bd-resume-prompt.txt" "$P/" 2>/dev/null
  } >/dev/null 2>&1
  v=$(bash "$P/verify.sh" 2>&1 | tail -1)
  n_codex=$(ls "$P/codex"/*.patch 2>/dev/null | wc -l)
  n_scripts=$(ls "$P/scripts" 2>/dev/null | wc -l)
  n_harness=$(ls "$P/harness" 2>/dev/null | wc -l)
  sz=$(du -sh "$P" 2>/dev/null | cut -f1)
  printf '%s persist: %s | codex=%s scripts=%s harness=%s size=%s\n' \
    "$ts" "$v" "$n_codex" "$n_scripts" "$n_harness" "$sz" >> "$LOG"
  [ "$ONCE" = 1 ] && break
  sleep "$INT"
done
