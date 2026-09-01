#!/bin/bash
# After the last row merges: deploy the fleet, then run the ONE sanctioned full
# suite against the exact deployed tree. Deploy-once-at-the-end and one-suite
# were both operator rulings (2026-08-25). The suite runs on test5 against the
# merged tree -- NOT on a fleet host, because the build/install-dependent
# suites (e2e_smoke, extension_live, share_target, nuitka) assert against a
# BUILT SPA and an installed service, and a host on an older deploy fails them
# for reasons that have nothing to do with the code.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25
L=$A/ENDGAME.log
say(){ echo "$(date -u +%H:%M:%S) $*" | tee -a "$L"; }
R=/home/mboyle/BulkDownloader

say "=== waiting for row 175 to close on origin/main ==="
closed=0
for _ in $(seq 1 240); do
  git -C /home/mboyle/BulkDownloader fetch --quiet origin 2>/dev/null
  if git -C /home/mboyle/BulkDownloader show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null \
     | grep -qE '^\|[[:space:]]*175[[:space:]]*\|[[:space:]]*(CLOSED|FIXED|MOOT)'; then closed=1; break; fi
  sleep 30
done
[ "$closed" -eq 1 ] || { say "row 175 never closed on main -- endgame not starting"; exit 2; }

git -C "$R" fetch --quiet origin
say "main $(git -C "$R" rev-parse --short origin/main) version $(git -C "$R" show origin/main:bulk_downloader/__init__.py | sed -n 's/^__version__ = "\(.*\)"/\1/p')"

# CLEAN THE FLEET BEFORE DEPLOYING (operator, 2026-08-26). Agent sessions are
# ARCHIVED FIRST, then killed: a killed session's transcript is the only record
# of what it was doing, so killing before archiving destroys evidence. This also
# removes the contention that has been distorting the browser-driving tests all
# night -- the full suite that follows should run on quiet machines.
# SNAPSHOT THE SESSION'S OWN STATE BEFORE ANYTHING DESTRUCTIVE. The clean step
# kills agent sessions and the deploy resets trees; if this window is compacted
# or lost afterwards, these files are what the next session resumes from.
say "=== snapshotting checkpoint, inventory and memory ==="
SNAP=$A/session-archive/integrator-state
mkdir -p "$SNAP"
for f in /home/mboyle/FLEET_RUN_CHECKPOINT.md /home/mboyle/NEXT_RANKED.md \
         /home/mboyle/BD_TOOLING_INVENTORY.md; do
  [ -f "$f" ] && cp -p "$f" "$SNAP/" && say "  saved $(basename "$f")"
done
cp -rp /home/mboyle/.claude/projects/-home-mboyle-BulkDownloader/memory "$SNAP/memory" 2>/dev/null \
  && say "  saved memory ($(ls "$SNAP/memory" | wc -l) files)"
mkdir -p "$SNAP/tools"
for t in bd-ship.sh bd-verify-cut.sh bd-integrate-row.sh bd-queue-run.sh bd-preflight.sh \
         bd-rebase-cut.py bd-register-merge.py bd-union-resolve.py bd-remaining.sh \
         bd-heartbeat.sh bd-edit.py bd-ps.sh bd-clean-vms.sh bd-endgame.sh \
         bd-merge-lane.sh bd-qa-row.sh bd-codex-cut.sh bd-make-brief.py bd-prbody.py; do
  [ -f "/home/mboyle/$t" ] && cp -p "/home/mboyle/$t" "$SNAP/tools/"
done
cp -rp /home/mboyle/bd-codex-briefs "$SNAP/briefs" 2>/dev/null
say "  saved $(ls "$SNAP/tools" | wc -l) tools + briefs -> $SNAP"

say "=== archiving then clearing agent sessions across the fleet ==="
bash /home/mboyle/bd-clean-vms.sh 2>&1 | tail -20 | tee -a "$L"

say "=== fleet deploy ==="
bash "$R/scripts/deploy.sh" > "$A/deploy-final.log" 2>&1
say "DEPLOY_RC=$?"; tail -12 "$A/deploy-final.log" | tee -a "$L"

say "=== sanctioned full suite on test5 against the deployed tree ==="
cd "$R" || exit 1
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest tests/ \
  -n 24 --dist loadfile --timeout=240 --timeout-method=signal --max-worker-restart=0 -p no:randomly \
  > "$A/fullsuite-final.log" 2>&1
say "FULLSUITE_RC=$?"
grep -E '^FAILED' "$A/fullsuite-final.log" | head -12 | tee -a "$L"
tail -1 "$A/fullsuite-final.log" | tee -a "$L"
say "=== endgame complete ==="
