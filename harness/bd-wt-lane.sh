#!/bin/bash
# ONE WRITER PER WORKTREE. bd-integrate-row.sh reads a worker worktree and
# freezes a candidate from it; the integrator sometimes edits that same worktree
# to repair a refusal. On 2026-08-29 those two overlapped twice on row 374: the
# fix landed at 14:42, the candidate froze at 14:43 from a diff captured at
# 14:41, and the 623-file band spent 13 minutes judging a tree that never
# contained the fix. Then it happened again. 26 minutes, no CPU contention, no
# failing test -- just two writers and no interlock.
#
# Same idiom as bd-merge-lane.sh: flock a file, wrap a command, release on exit.
# Per ROW, not global, so unrelated rows still integrate in parallel.
#
#   bd-wt-lane.sh <row> <command...>     run <command> holding row <row>'s lock
#   bd-wt-lane.sh --hold <row> <seconds> hold it (for a human/agent edit window)
#   bd-wt-lane.sh --check <row>          rc 0 if free, 1 if held; prints holder
set -u
D=/home/mboyle/.bd-wt-locks; mkdir -p "$D"

case "${1:-}" in
  --check)
    R=${2:?row}; L="$D/row$R.lock"
    exec 9>"$L"
    if flock -n 9; then echo "row $R worktree: FREE"; exit 0; fi
    echo "row $R worktree: HELD by $(cat "$D/row$R.owner" 2>/dev/null || echo unknown)"; exit 1 ;;
  --hold)
    R=${2:?row}; S=${3:-1800}; L="$D/row$R.lock"
    exec 9>"$L"
    flock -w "${WT_WAIT:-1800}" 9 || { echo "row $R busy -- gave up"; exit 75; }
    echo "integrator (pid $$) since $(date -u +%H:%M:%S)" > "$D/row$R.owner"
    echo "row $R worktree HELD for ${S}s -- release with: kill $$"
    sleep "$S"; rm -f "$D/row$R.owner"; exit 0 ;;
esac

R=${1:?usage: bd-wt-lane.sh <row> <command...>}; shift
L="$D/row$R.lock"
exec 9>"$L"
if ! flock -w "${WT_WAIT:-3600}" 9; then echo "row $R worktree lane busy -- gave up"; exit 75; fi
echo "$(date -u +%H:%M:%S) wt-lane row $R acquired by $*" >&2
echo "$* (pid $$) since $(date -u +%H:%M:%S)" > "$D/row$R.owner"
"$@"; RC=$?
rm -f "$D/row$R.owner"
echo "$(date -u +%H:%M:%S) wt-lane row $R released rc=$RC" >&2
exit $RC
