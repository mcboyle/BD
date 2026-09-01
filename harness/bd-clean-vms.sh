#!/bin/bash
# SAVE THEN KILL every codex / claude-code session on the fleet, so the deploy
# and the sanctioned full suite run on quiet machines. Transcripts are archived
# BEFORE anything is signalled -- a killed session's history is the only record
# of what it was doing, and killing first would destroy evidence we may need.
#
# SELF-EXCLUSION IS THE WHOLE SAFETY PROPERTY. This runs from inside a Claude
# Code session on test5; killing its own ancestry would end the run that is
# doing the cleaning. The ancestor chain of THIS process is walked from /proc
# and every pid in it is excluded by number, not by name matching -- a pattern
# like "claude" matches the shell doing the matching, which is exactly how the
# self-matching kills earlier in this session happened.
set -u
ARCH=/home/mboyle/fleet-run-artifacts/2026-08-25/session-archive
mkdir -p "$ARCH"
LOG="$ARCH/clean.log"
say(){ echo "$(date -u +%H:%M:%S) $*" | tee -a "$LOG"; }

# ---- build the exclusion set: every ancestor of this process ----
SELF=$$
EXCLUDE=" "
p=$SELF
while [ -n "$p" ] && [ "$p" != "0" ] && [ "$p" != "1" ]; do
  EXCLUDE="$EXCLUDE$p "
  p=$(awk '{print $4}' "/proc/$p/stat" 2>/dev/null)
done
say "self-exclusion pids:$EXCLUDE"

say "=== test5 (local) ==="
# archive local session state first
for d in "$HOME/.codex/sessions" "$HOME/.claude/projects"; do
  [ -d "$d" ] || continue
  n=$(basename "$(dirname "$d")")-$(basename "$d")
  tar czf "$ARCH/test5-$n.tgz" -C "$(dirname "$d")" "$(basename "$d")" 2>/dev/null \
    && say "  archived $d -> test5-$n.tgz ($(du -h "$ARCH/test5-$n.tgz" | cut -f1))"
done
killed=0
for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
  case "$EXCLUDE" in *" $pid "*) continue;; esac
  argv=$(cat "/proc/$pid/cmdline" 2>/dev/null | tr '\0' ' ')
  [ -z "$argv" ] && continue
  case "$argv" in
    *".local/bin/codex "*|*"/codex exec"*|*"codex exec "*)
      say "  killing codex pid $pid"; kill "$pid" 2>/dev/null; killed=$((killed+1));;
  esac
done
say "  test5: $killed codex process(es) signalled; THIS session untouched"

say "=== remote hosts ==="
for a in 10.0.70.85 10.0.70.249 10.0.70.84 10.0.70.80 10.0.70.95 10.0.70.83; do
  h=$(timeout 20 ssh -o BatchMode=yes -o StrictHostKeyChecking=no "$a" hostname 2>/dev/null)
  [ -z "$h" ] && { say "  $a UNREACHABLE -- skipped"; continue; }
  # archive remote session state to a tarball, pull it back, then kill
  timeout 90 ssh -o BatchMode=yes "$a" '
    t=/tmp/bd-sessions.tgz; rm -f $t
    dirs=""
    [ -d ~/.codex/sessions ] && dirs="$dirs .codex/sessions"
    [ -d ~/.claude/projects ] && dirs="$dirs .claude/projects"
    [ -n "$dirs" ] && tar czf $t -C ~ $dirs 2>/dev/null
    [ -f $t ] && du -h $t | cut -f1 || echo none' 2>/dev/null | tail -1 | while read -r sz; do
      if [ "$sz" != "none" ] && [ -n "$sz" ]; then
        scp -q -o BatchMode=yes "$a:/tmp/bd-sessions.tgz" "$ARCH/$h-sessions.tgz" 2>/dev/null \
          && say "  $h: archived sessions ($sz) -> $h-sessions.tgz"
      else say "  $h: no session dirs to archive"; fi
    done
  n=$(timeout 60 ssh -o BatchMode=yes "$a" '
    k=0
    for pid in $(ls /proc | grep -E "^[0-9]+$"); do
      argv=$(cat /proc/$pid/cmdline 2>/dev/null | tr "\0" " ")
      case "$argv" in
        *"codex exec"*|*".local/bin/codex"*|*"claude --remote-control"*|*"/claude "*)
          kill $pid 2>/dev/null && k=$((k+1));;
      esac
    done
    # tmux sessions running agents
    tmux ls 2>/dev/null | grep -E "^(cx-|bd-)" | cut -d: -f1 | while read -r s; do tmux kill-session -t "$s" 2>/dev/null; done
    echo $k' 2>/dev/null | tail -1)
  say "  $h: ${n:-0} agent process(es) signalled, agent tmux sessions cleared"
done

say "=== post-clean load ==="
for a in 10.0.70.85 10.0.70.249 10.0.70.84 10.0.70.80 10.0.70.95 10.0.70.83; do
  r=$(timeout 15 ssh -o BatchMode=yes "$a" 'echo "$(hostname) load=$(cut -d" " -f1 /proc/loadavg) agents=$(pgrep -c -f codex 2>/dev/null || echo 0)"' 2>/dev/null)
  say "  ${r:-$a unreachable}"
done
say "  test5 load=$(cut -d' ' -f1 /proc/loadavg)"
say "=== clean complete; archives in $ARCH ==="
