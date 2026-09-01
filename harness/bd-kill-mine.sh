#!/bin/bash
# Kill processes by argv pattern WITHOUT killing the shell doing the killing.
# Four times on 2026-08-26 a kill loop matched its own command string -- pkill -f,
# a grep case, an ancestry walk, and a bash -c wrapper. The pattern lives in MY
# argv too, so the matcher must exclude ITSELF and ITS ANCESTORS by PID.
#   usage: bd-kill-mine.sh <argv-substring> [signal]
set -u
PAT="${1:?usage: bd-kill-mine.sh <argv-substring> [signal]}"; SIG="${2:-TERM}"
SELF=$$; MINE=" $SELF "
q=$PPID; while [ -n "$q" ] && [ "$q" != "1" ]; do MINE="$MINE$q "; q=$(awk '/^PPid:/{print $2}' /proc/$q/status 2>/dev/null); done
n=0
for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
  case "$MINE" in *" $p "*) continue;; esac
  a=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null) || continue
  case "$a" in *"$PAT"*) kill "-$SIG" "$p" 2>/dev/null && { echo "killed $p: $(printf '%.70s' "$a")"; n=$((n+1)); };; esac
done
echo "$n process(es) signalled (self and ancestors excluded)"
