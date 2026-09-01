#!/bin/bash
# ONE LINE EVERY 10 MINUTES (operator, 2026-08-28 12:20Z). Operator 2026-08-28: terse event lines only.
# Supersedes the 10-minute heartbeat; that cadence was noise once the lane was
# stable, and the point of a heartbeat is to be read, not to scroll past.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25
R=/home/mboyle/BulkDownloader
ROLES=/home/mboyle/.config/bd/roles
I=${PROGRESS_INTERVAL:-600}
while true; do
  git -C "$R" fetch -q origin 2>/dev/null
  V=$(git -C "$R" show origin/main:bulk_downloader/__init__.py 2>/dev/null|grep -oE '3\.66\.[0-9]+')
  CX=$(tmux ls 2>/dev/null|grep -c '^cx-row')
  # eligible = spec rows not CLOSED in main's register
  REG=$(git -C "$R" show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null)
  Q=0
  while IFS='|' read -r r _s _t; do
    case "$r" in ''|'#'*) continue;; esac
    printf '%s' "$REG"|grep -qE "^\| $r \|[[:space:]]*CLOSED" || Q=$((Q+1))
  done < /home/mboyle/bd-night-spec.txt
  # current phase from the newest chain log
  P=""   # superseded by the STAGE block below, which handles batch-named logs
  # deploy targets only -- runners are deliberately not deployed
  FL=""
  if [ -r "$ROLES" ]; then
    ok=0; n=0
    while read -r role _name ip; do
      [ "$role" = deploy ] || continue; n=$((n+1))
      v=$(timeout 8 ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no "$ip" \
            'curl -s --max-time 5 http://127.0.0.1:5555/api/health' 2>/dev/null|grep -oE '"sha":"[0-9a-f]+"'|cut -d'"' -f4)
      [ -n "$v" ] && ok=$((ok+1))
    done < "$ROLES"
    MS=$(git -C "$R" rev-parse --short=12 origin/main 2>/dev/null)
    FL="fleet${ok}/${n}"
  fi
  # NAME THE STAGE, NOT A CODE. The operator reads this line to know what is
  # happening right now; "q3 idle" told them nothing about which cut, which row,
  # or how long it had been there.
  CL=$(ls -t "$A"/inflight/chain-*.log 2>/dev/null|head -1)
  CUT=""; STAGE=""
  if [ -n "$CL" ]; then
    CUT=$(basename "$CL"); CUT=${CUT#chain-}; CUT=${CUT%.log}
    STAGE=$(tail -n 3 "$CL" 2>/dev/null|grep -oE '=== [A-Z]+|MERGED|REFUSED|BLOCKED'|tail -n 1|tr -d '= ')
  fi
  # how long the current stage has been running, from the newest artifact
  NEW=$(ls -t "$A"/inflight/*-driver.log "$A"/inflight/*-band.log 2>/dev/null|head -1)
  AGE=""
  if [ -n "$NEW" ]; then
    now=$(date +%s); m=$(stat -c %Y "$NEW" 2>/dev/null||echo "$now")
    AGE="$(( (now-m)/60 ))m"
  fi
  CW=$(ls -td /home/mboyle/bd-cuts/cut/* 2>/dev/null|head -1)
  VER=""; [ -n "$CW" ] && { VER=$(basename "$CW"); VER=${VER%%-*}; }
  # which codex agents, by row, and what each is doing
  CXR=$(tmux ls 2>/dev/null|grep -oE '^cx-row[0-9]+'|sed 's/cx-row//'|paste -sd, -)
  RED=$(grep -lE 'BAND_RC=1|PRECUT_RC=[1-9]|PREPUSH_RC=[1-9]' "$A"/inflight/*-driver.log 2>/dev/null|tail -1|xargs -r basename|cut -d- -f1-2)
  L="$(date -u +%H:%M) main v${V:-?}"
  [ -n "$CUT" ] && L="$L | cut ${VER:-?} rows ${CUT} ${STAGE:-?} ${AGE}"
  L="$L | codex[${CXR:-none}] | $Q rows left | ${FL:-fleet?}"
  [ -n "$RED" ] && L="$L | last-red $RED"
  echo "${L:0:190}"
  sleep "$I"
done
