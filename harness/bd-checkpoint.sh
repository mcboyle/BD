#!/usr/bin/env bash
# Rewrite FLEET_RUN_CHECKPOINT.md's MEASURED block every 30 minutes.
# Everything here is re-derived at write time; nothing is copied from prose.
set -uo pipefail
R=/home/mboyle/BulkDownloader
C=/home/mboyle/FLEET_RUN_CHECKPOINT.md
while :; do
  {
    echo "<!-- AUTO BLOCK: rewritten every 30min by ~/bd-checkpoint.sh. Hand-written"
    echo "     narrative lives ABOVE the marker and is never touched. -->"
    echo "## MEASURED at $(date -u +%FT%TZ) on $(hostname)"
    echo
    echo '```'
    git -C "$R" log --oneline -1
    echo "branch:  $(git -C "$R" rev-parse --abbrev-ref HEAD)"
    echo "tree:    $(git -C "$R" rev-parse HEAD^{tree})"
    echo "version: $(grep -oP '(?<=^__version__ = ")[^"]+' "$R/bulk_downloader/__init__.py")"
    echo "dirty:   $(git -C "$R" status --porcelain | wc -l) path(s)"
    git -C "$R" status --porcelain | head -20
    echo "open rows: $(grep -cE '^\| [0-9]+ \| OPEN \|' "$R/project-knowledge/IMPROVEMENT_BACKLOG.md")"
    echo "load:    $(cut -d' ' -f1-3 /proc/loadavg)"
    echo "codex:   $(ls /proc | grep -E '^[0-9]+$' | while read p; do
                     tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null |
                     grep -q '^/home/mboyle/.local/bin/codex exec ' && echo x; done | wc -l) exec worker(s)"
    echo "queue:   $(grep -c LAUNCH /home/mboyle/fleet-run-artifacts/2026-08-25/codex-backlog/queue/dispatch.log 2>/dev/null) launched, $(ls /home/mboyle/fleet-run-artifacts/2026-08-25/codex-backlog/queue/.done.* 2>/dev/null | wc -l) done"
    echo '```'
  } > /tmp/bd-ckpt.auto
  # SPLICE, never overwrite: the narrative above the marker is the part a human wrote.
  awk '/^<!-- AUTO BLOCK:/{exit} {print}' "$C" > /tmp/bd-ckpt.new 2>/dev/null || : > /tmp/bd-ckpt.new
  cat /tmp/bd-ckpt.auto >> /tmp/bd-ckpt.new
  mv /tmp/bd-ckpt.new "$C"
  sleep 1800
done
