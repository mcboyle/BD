#!/bin/bash
# Rewrites a LIVE STATE block at the top of FLEET_RUN_CHECKPOINT.md every 30
# minutes. It owns ONLY the region between the two markers, so hand-written
# analysis below it is never clobbered. Every fact is MEASURED at write time --
# main's SHA from git, the fleet version from the deployed host, job liveness
# from /proc argv -- because a checkpoint that copies stale prose is worse than
# no checkpoint (A1).
set -u
C=/home/mboyle/FLEET_RUN_CHECKPOINT.md
IN=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
CC=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts
R=/home/mboyle/BulkDownloader
B="<!-- LIVE-STATE-BEGIN -->"; E="<!-- LIVE-STATE-END -->"
alive(){ for p in $(pgrep -f "$1" 2>/dev/null); do
  tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q -- "$1" && return 0; done; return 1; }

while true; do
  T=$(mktemp)
  {
    echo "$B"
    echo "## LIVE STATE -- rewritten automatically every 30 min"
    echo
    echo "    written    $(date -u +%Y-%m-%dT%H:%M:%SZ)  host $(hostname)  load $(cut -d' ' -f1 /proc/loadavg)"
    git -C "$R" fetch --quiet origin 2>/dev/null
    echo "    origin/main $(git -C "$R" rev-parse origin/main 2>/dev/null)"
    echo "    local main  $(git -C "$R" rev-parse HEAD 2>/dev/null)  $(git -C "$R" status --porcelain=v1 | grep -c . ) dirty path(s)"
    echo
    echo "    JOBS"
    for r in 184 183 242; do
      if alive "bd-codex-cut.sh $r"; then st="RUNNING"; else st="finished"; fi
      echo "      codex row $r   $st   $(stat -c %s "$CC/row$r.txt" 2>/dev/null || echo 0) bytes   tmux cx-row$r"
    done
    if alive bd-w1-matched.sh; then w="RUNNING"; else w="finished"; fi
    echo "      w1 matched     $w   round $(grep -c collecting "$IN/w1-matched/summary.txt" 2>/dev/null || echo 0)/6   tmux bd-w1"
    echo
    echo "    LAST HEARTBEAT"
    echo "      $(tail -1 "$IN/heartbeat.log" 2>/dev/null || echo none)"
    echo
    echo "    W1 MATCHED SO FAR (a failure on BOTH arms is pre-existing = row 241)"
    grep -E '^===|^    FAILED|passed|failed' "$IN/w1-matched/summary.txt" 2>/dev/null | tail -8 | sed 's/^/      /'
    echo
    echo "    ATTACH: see /home/mboyle/bd-attach.txt"
    echo "$E"
  } > "$T"
  if grep -q "$B" "$C"; then
    python3 - "$C" "$T" <<'PY'
import sys,pathlib
c,t=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2])
s=c.read_text(encoding="utf-8"); new=t.read_text(encoding="utf-8")
b,e="<!-- LIVE-STATE-BEGIN -->","<!-- LIVE-STATE-END -->"
i,j=s.index(b),s.index(e)+len(e)
c.write_text(s[:i]+new.rstrip("\n")+s[j:],encoding="utf-8")
PY
  else
    python3 - "$C" "$T" <<'PY'
import sys,pathlib
c,t=pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2])
s=c.read_text(encoding="utf-8"); new=t.read_text(encoding="utf-8")
h="# FLEET RUN CHECKPOINT\n"
c.write_text(h+"\n"+new+"\n"+s[len(h):] if s.startswith(h) else new+"\n"+s, encoding="utf-8")
PY
  fi
  rm -f "$T"
  sleep 1800
done
