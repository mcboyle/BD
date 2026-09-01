#!/bin/bash
# Sequential codex build runner.
#
# ONE WORKER AT A TIME, AND NEVER DURING A BAND. On 2026-08-27 three concurrent
# codex workers drove load to 23 and broke the bd-mutate timing tests in a
# verify band; the cut had to be re-run on a quiet box, and the load masked a
# REAL defect underneath two contention failures. Codex builds and verify bands
# must not overlap.
#
# When a worker lands, its row is UNPARKED in the spec so the drain can pick it
# up. A row is only unparked if its worktree actually contains work -- a green
# empty cut is worse than no cut.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"
R=/home/mboyle/BulkDownloader; S=/home/mboyle/bd-night-spec.txt
Q="$A/night/codex-build-queue.txt"; D=/home/mboyle/bd-codex-briefs
say(){ printf '%s [codex-queue] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
lane_busy(){ pgrep -f 'bd-verify-cut\.sh|bd-row-chain\.sh|bd-integrate-row\.sh' >/dev/null 2>&1; }
cx_busy(){ tmux ls 2>/dev/null | grep -q '^cx-row'; }

say "started; $(grep -c . "$Q" 2>/dev/null) build(s) queued, one at a time, never during a band"
while [ -s "$Q" ]; do
  line=$(head -1 "$Q"); row=${line%%|*}; brief=${line##*|}
  # wait for a genuinely quiet box
  for _ in $(seq 1 720); do
    lane_busy || cx_busy || break
    sleep 20
  done
  if lane_busy || cx_busy; then say "row $row: box never went quiet, giving up"; break; fi
  [ -f "$D/$brief" ] || { say "row $row: brief $brief MISSING -- skipping"; sed -i 1d "$Q"; continue; }
  say "dispatching row $row build ($brief)"
  bash /home/mboyle/bd-codex-cut.sh "$row" "$D/$brief" >/dev/null 2>&1
  # bd-codex-cut runs the session in tmux; wait for it to end
  for _ in $(seq 1 900); do tmux has-session -t "cx-row$row" 2>/dev/null || break; sleep 20; done
  W=/home/mboyle/bd-codex-wt/row$row
  B=$(git -C "$W" merge-base HEAD origin/main 2>/dev/null)
  n=$(git -C "$W" diff "$B" --name-only -- . ':(exclude)venv' ':(exclude)frontend/node_modules' 2>/dev/null | wc -l)
  # EXCLUDE THE SCAFFOLDING. bd-codex-cut.sh symlinks venv and
  # frontend/node_modules into every worktree, so they are ALWAYS untracked.
  # Counting them made an empty build look like 2 changed paths, and row 322 --
  # which produced a design proposal and no edits -- was unparked into the spec
  # as if it were ready. A green empty cut is worse than no cut.
  u=$(git -C "$W" status --porcelain 2>/dev/null | grep '^??' \
      | grep -vE ' (venv|frontend/node_modules|frontend/dist)/?$' | grep -c .)
  if [ "$((n+u))" -gt 0 ]; then
    if grep -qE "^# $row\|" "$S"; then
      python3 - "$S" "$row" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1]); row = sys.argv[2]
p.write_text("".join(l[2:] if re.match(rf'^# {row}\|', l) else l
                     for l in p.read_text(encoding="utf-8").splitlines(keepends=True)),
             encoding="utf-8")
PY
      say "row $row built ($((n+u)) path(s)) and UNPARKED into the spec"
    else
      say "row $row built ($((n+u)) path(s)); no parked spec line to unpark"
    fi
  else
    say "row $row produced NO changed paths -- left parked (refuted, or the build failed)"
  fi
  sed -i 1d "$Q"
done
say "build queue drained"
