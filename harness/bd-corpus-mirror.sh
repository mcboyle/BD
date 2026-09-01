#!/bin/bash
# Mirror every fleet host's captures/ to test5. Row 89: the corpus is gitignored
# data, deploy.sh moves code only, and a rebuilt host comes back with nothing.
#
# rsync WITHOUT --delete, deliberately. A true mirror propagates an accidental
# deletion to the backup on the very next run, which is the failure mode a backup
# exists to prevent. Restore still works; the cost is that a legitimately removed
# file lingers here.
#
# ABSENT != EMPTY. That distinction IS row 89: two hosts read as an empty store
# when the corpus was missing entirely. This records a four-valued state per host
# and treats unreachable as UNKNOWN, never as "nothing to back up".
set -u
DEST=${DEST:-/home/mboyle/corpus-mirror}
HOSTS=/home/mboyle/.config/bd/hosts
LOG=/home/mboyle/fleet-run-artifacts/2026-08-25/corpus-mirror.log
MIN_FREE_G=${MIN_FREE_G:-200}
say(){ printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
mkdir -p "$DEST"

free=$(df -BG --output=avail "$DEST" | tail -1 | tr -dc '0-9')
if [ "${free:-0}" -lt "$MIN_FREE_G" ]; then
  say "REFUSING: only ${free}G free at $DEST, need ${MIN_FREE_G}G. A backup that fills the hub is an outage."; exit 4; fi
say "== corpus mirror -> $DEST (${free}G free) =="

while read -r name ip marker; do
  [ -n "${ip:-}" ] || continue
  [ "${marker:-}" = local ] && continue
  probe=$(timeout 25 ssh -o BatchMode=yes -o ConnectTimeout=8 "$ip" \
    'd=~/BulkDownloader/captures; if [ ! -e "$d" ]; then echo ABSENT; elif [ -z "$(find "$d" -type f -print -quit 2>/dev/null)" ]; then echo EMPTY; else echo "PRESENT $(find "$d" -type f|wc -l)"; fi' 2>/dev/null)
  if [ -z "$probe" ]; then say "$name UNKNOWN -- unreachable, NOT counted as empty"; continue; fi
  case "$probe" in
    ABSENT) say "$name ABSENT -- no captures/ directory at all (row 89's exact silent case)"; continue;;
    EMPTY)  say "$name EMPTY -- directory exists, zero files"; continue;;
  esac
  n=${probe##* }
  mkdir -p "$DEST/$name"
  if rsync -a --partial --timeout=120 -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' \
       "$ip:BulkDownloader/captures/" "$DEST/$name/" >>"$LOG" 2>&1; then
    got=$(find "$DEST/$name" -type f | wc -l)
    say "$name PRESENT src=$n mirrored=$got $(du -sh "$DEST/$name" 2>/dev/null | cut -f1)"
    [ "$got" -ge "$n" ] || say "$name WARNING mirrored $got < source $n -- incomplete, investigate"
  else
    say "$name RSYNC FAILED -- prior mirror left intact (no --delete), see $LOG"
  fi
done < "$HOSTS"
say "mirror pass done"
