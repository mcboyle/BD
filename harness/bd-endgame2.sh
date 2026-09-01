#!/bin/bash
# ENDGAME. Order is load-bearing; do not reorder.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/ENDGAME2.log"
R=/home/mboyle/BulkDownloader
say(){ printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }

say "=== 1. quiesce: stop the pump, wait for workers, prove ZERO codex sessions ==="
for p in $(ps -eo pid=,args= | grep -E "^ *[0-9]+ bash (/home/mboyle/)?bd-codex-pump\.sh" | awk '{print $1}'); do kill "$p" 2>/dev/null; done
for _ in $(seq 1 180); do [ "$(tmux ls 2>/dev/null | grep -c '^cx-')" -eq 0 ] && break; sleep 20; done
say "   codex sessions: $(tmux ls 2>/dev/null | grep -c '^cx-')  load: $(cut -d' ' -f1 /proc/loadavg)"

say "=== 2. archive test5 worktree residue: DIFFS + untracked, not whole checkouts ==="
AR=/home/mboyle/bd-archive/2026-08-26; mkdir -p "$AR/worktree-diffs"
kept=0; arch=0
for w in /home/mboyle/bd-codex-wt/*/; do
  n=$(basename "$w")
  case "$n" in row243*|rowa-2-*|rowg-toctou-remaining|row261*) kept=$((kept+1)); continue;; esac
  git -C "$w" diff origin/main --binary -- . ':(exclude)venv' ':(exclude)frontend/node_modules' \
      > "$AR/worktree-diffs/$n.patch" 2>/dev/null
  git -C "$w" status --porcelain 2>/dev/null | grep -v 'venv\|node_modules' > "$AR/worktree-diffs/$n.status" 2>/dev/null
  # THE STATUS FILE LISTS UNTRACKED PATHS BY NAME. IT DOES NOT SAVE THEIR BYTES,
  # and `git diff` cannot see an untracked file at all -- so the rm -rf below
  # destroyed them permanently. It already cost a RED test and two mutant specs.
  # Found by audit 2026-09-01.
  git -C "$w" ls-files -o --exclude-standard 2>/dev/null \
    | grep -vE '(^|/)(venv|node_modules|__pycache__)(/|$)' > "$AR/worktree-diffs/$n.untracked.list" 2>/dev/null
  if [ -s "$AR/worktree-diffs/$n.untracked.list" ]; then
    if ! tar -czf "$AR/worktree-diffs/$n.untracked.tar.gz" -C "$w" \
           -T "$AR/worktree-diffs/$n.untracked.list" 2>/dev/null; then
      say "   UNTRACKED ARCHIVE FAILED for $n -- refusing to continue"
      exit 4
    fi
  fi
  arch=$((arch+1))
done
tar -czf "$AR/test5-worktree-diffs.tar.gz" -C "$AR" worktree-diffs 2>/dev/null && rm -rf "$AR/worktree-diffs"
say "   archived $arch worktree diffs -> $(du -h "$AR/test5-worktree-diffs.tar.gz" 2>/dev/null | cut -f1); kept $kept unmerged/held"
[ -s "$AR/test5-worktree-diffs.tar.gz" ] || { say "   ARCHIVE EMPTY -- refusing to delete worktrees"; }
if [ -s "$AR/test5-worktree-diffs.tar.gz" ]; then
  for w in /home/mboyle/bd-codex-wt/*/; do
    n=$(basename "$w"); case "$n" in row243*|rowa-2-*|rowg-toctou-remaining|row261*) continue;; esac
    git -C "$R" worktree remove --force "$w" 2>/dev/null || rm -rf "$w"
  done
  git -C "$R" worktree prune 2>/dev/null
  say "   worktrees now: $(ls -d /home/mboyle/bd-codex-wt/*/ 2>/dev/null | wc -l); git registrations: $(git -C "$R" worktree list | wc -l)"
fi

say "=== 3. deploy all seven hosts ==="
git -C "$R" fetch --quiet origin 2>/dev/null
say "   main is v$(git -C "$R" show origin/main:bulk_downloader/__init__.py | grep -oE '3\.66\.[0-9]+' | head -1)"
bash "$R/scripts/deploy.sh" > "$A/deploy-endgame-test5.log" 2>&1; RC=$?
say "   test5 DEPLOY_RC=$RC"
# A FAILED DEPLOY IS NOT A NO-OP (CLAUDE.md A6). It can leave the service DOWN
# after the stop and cache-clear steps. Running a suite against a dead host with
# nobody watching would waste the one authoritative measurement AND leave the
# service down longer. Prove health before going on, and stop if it is not there.
HEALTH=$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/health 2>/dev/null || echo 000)
say "   test5 health after deploy: $HEALTH"
if [ "$RC" -ne 0 ] || [ "$HEALTH" != "200" ]; then
  say "   *** DEPLOY DID NOT LEAVE test5 HEALTHY (rc=$RC health=$HEALTH) ***"
  say "   *** STOPPING BEFORE THE FLEET AND BEFORE THE SUITE. Preserve the failing"
  say "       step: $A/deploy-endgame-test5.log . Do not re-run deploy blindly --"
  say "       inspect system state first. The fleet was NOT touched. ***"
  exit 1
fi
"$R/venv/bin/python" "$R/toolchain/bin/bd-fleet-run" --repo-dir "$R" --execute \
  --only test,test2,test3,test4,test6,test7 --timeout 900 --jobs 6 \
  'cd ~/BulkDownloader && bash scripts/deploy.sh 2>&1 | tail -6; echo "DEPLOY_RC=${PIPESTATUS[0]}"' \
  > "$A/deploy-endgame-fleet.log" 2>&1
say "   fleet: $(grep -cE 'DEPLOY_RC=0' "$A/deploy-endgame-fleet.log") of 6 hosts RC=0"

say "=== 4. record frontend/dist BEFORE the suite (rows 247/248 acceptance) ==="
cd "$R"; find frontend/dist -type f -printf '%p ' -exec sha256sum {} \; 2>/dev/null | awk '{print $1,$2}' | sort > "$A/dist-BEFORE-endgame.txt"
say "   $(wc -l < "$A/dist-BEFORE-endgame.txt") files; marker=$( [ -f frontend/dist/.bd-built-from ] && cut -c1-8 frontend/dist/.bd-built-from || echo ABSENT)"

say "=== 5. THE authoritative full suite -- nothing else running ==="
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest tests/ \
  -n 24 --dist loadfile --timeout=240 --timeout-method=signal --max-worker-restart=0 -p no:randomly \
  > "$A/fullsuite-endgame.log" 2>&1
say "   SUITE_RC=$?  $(tail -1 "$A/fullsuite-endgame.log")"
grep -E '^FAILED' "$A/fullsuite-endgame.log" | head -12 | tee -a "$L"

say "=== 6. rows 247/248 verdict: did the suite touch the deployed bundle? ==="
find frontend/dist -type f -printf '%p ' -exec sha256sum {} \; 2>/dev/null | awk '{print $1,$2}' | sort > "$A/dist-AFTER-endgame.txt"
if diff -q "$A/dist-BEFORE-endgame.txt" "$A/dist-AFTER-endgame.txt" >/dev/null 2>&1; then
  say "   IDENTICAL: $(wc -l < "$A/dist-AFTER-endgame.txt") files byte-for-byte. Rows 247/248 hold."
else
  say "   CHANGED -- rows 247/248 DID NOT HOLD:"; diff "$A/dist-BEFORE-endgame.txt" "$A/dist-AFTER-endgame.txt" | head -8 | tee -a "$L"
fi
say "   marker after: $( [ -f frontend/dist/.bd-built-from ] && cut -c1-8 frontend/dist/.bd-built-from || echo ABSENT)"
say "=== endgame complete -- deliverables are the integrator's job ==="
