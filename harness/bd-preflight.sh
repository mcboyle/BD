#!/bin/bash
# Pre-flight a QUEUED cut on an IDLE FLEET HOST, so "will it go green" is
# measured before the serial integration lane reaches it instead of 25 minutes
# into its slot. Builds the candidate in a scratch worktree on the remote host
# from origin/main + the worker patches, then runs the derived band there.
#
# THIS IS NOT A MERGE GATE. The parent moves as cuts land, so this result is
# about the WORK, not about the exact tree that will merge (A4: evidence is
# tied to an exact tree). It answers "is this cut sound", which is the question
# worth answering early; the real gate still runs on test5 against the exact
# candidate.
set -u
HOST="$1"; LABEL="$2"; shift 2
PATCHES="$*"
OUT=/home/mboyle/fleet-run-artifacts/2026-08-25/preflight
mkdir -p "$OUT"
LOG="$OUT/$LABEL.log"
R='$HOME/BulkDownloader'
W="/tmp/bd-preflight-$LABEL"

{
echo "== preflight $LABEL on $HOST  $(date -u +%H:%M:%S)"
ssh -o BatchMode=yes -o StrictHostKeyChecking=no "$HOST" "
  set -e
  cd ~/BulkDownloader
  git fetch --quiet origin
  rm -rf $W; git worktree prune
  git worktree add --quiet --detach $W origin/main
  ln -sfn ~/BulkDownloader/venv $W/venv
  ln -sfn ~/BulkDownloader/frontend/node_modules $W/frontend/node_modules 2>/dev/null || true
  echo 'remote base:' \$(git -C $W rev-parse --short HEAD)
" 2>&1 || { echo "PREFLIGHT_RC=90 (worktree setup failed)"; exit 90; }

for p in $PATCHES; do
  # A REGISTER-ONLY ROW HAS AN EMPTY PATCH, AND THAT IS CORRECT. The integrator
  # strips IMPROVEMENT_BACKLOG out (it is merged by row id, not by text), so a
  # row whose only change was the register leaves nothing behind. git apply then
  # says "No valid patches in input" and the whole preflight failed on it --
  # a harness treating a legitimate empty delta as an error. Row 241 hit this.
  if [ ! -s "$p" ]; then echo "  skipped $(basename "$p") -- empty (register-only row)"; continue; fi
  scp -q -o BatchMode=yes -o StrictHostKeyChecking=no "$p" "$HOST:/tmp/$(basename "$p")" || { echo "PREFLIGHT_RC=91"; exit 91; }
  if ! ssh -o BatchMode=yes "$HOST" "git -C $W apply --index --3way /tmp/$(basename "$p")" 2>&1; then
    # same append-only registry union as the integrator uses
    scp -q -o BatchMode=yes "/home/mboyle/bd-union-resolve.py" "$HOST:/tmp/bd-union-resolve.py" 2>/dev/null
    if ssh -o BatchMode=yes "$HOST" "python3 /tmp/bd-union-resolve.py $W" 2>&1; then
      echo "  union-resolved after $(basename "$p")"
    else
      echo "PATCH FAILED: $(basename "$p")"; echo "PREFLIGHT_RC=92"; exit 92
    fi
  fi
  echo "  applied $(basename "$p")"
done

# REGENERATE BEFORE MEASURING, EXACTLY AS THE INTEGRATOR DOES. Without this the
# preflight applies a raw patch and then fails on DEPENDENCY_GRAPH drift and
# import-graph edges -- artefacts of NOT having regenerated, not defects in the
# cut. Row 121 came back "3 failed" for precisely that reason and the work was
# fine. A preflight that models a different pipeline than the real one answers
# a question nobody asked.
ssh -o BatchMode=yes "$HOST" "
  cd $W
  venv/bin/python toolchain/bin/bd-regen-order --work $W >/tmp/preflight-regen.log 2>&1 \
    || venv/bin/python toolchain/bin/bd-regen-order --work $W --declare-reach >/tmp/preflight-regen.log 2>&1 || true
  venv/bin/python toolchain/bin/bd-regen-order --work $W --declare-edges >/dev/null 2>&1 || true
  git add -A -- . ':(exclude)venv' ':(exclude)frontend/node_modules' 2>/dev/null || true
  echo 'regen done:' \$(tail -1 /tmp/preflight-regen.log 2>/dev/null | tr -d '\033' | cut -c1-60)
  CH=\$(git diff --name-only --cached | tr '\n' ' ')
  echo \"changed: \$(echo \$CH | wc -w) path(s)\"
  BAND=\$(venv/bin/python toolchain/bin/bd-band-derive --work $W --files \$CH --emit 2>/dev/null | sed 's/^[[:space:]]*bd-band[[:space:]]*//')
  N=\$(printf '%s\n' \$BAND | grep -c '^tests/')
  echo \"band: \$N file(s)\"
  [ \"\$N\" -eq 0 ] && { echo 'BAND EMPTY -- UNKNOWN'; exit 99; }
  # SPLIT THE BROWSER FILES OUT AND RUN THEM SERIALLY, exactly as bd-verify-cut
  # does. A real browser cannot hold its timing beside 23 other workers: on
  # 2026-08-26 row 261's preflight reported 16 failures of which EIGHT were
  # e2e_smoke/extension_live timing out under -n 24, and the same eight appeared
  # again on the repaired re-run. That is a harness manufacturing failures and
  # then being read as a verdict on the cut. Nothing is skipped -- the
  # denominator is identical, only the schedule differs, and BOTH halves must
  # be green.
  BROWSER=\"\"; FAST=\"\"
  for f in \$BAND; do
    case \"\$f\" in
      tests/test_e2e_smoke.py|tests/test_extension_live.py) BROWSER=\"\$BROWSER \$f\";;
      *) FAST=\"\$FAST \$f\";;
    esac
  done
  RC_FAST=0; RC_BROW=0
  if [ -n \"\$FAST\" ]; then
    env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 timeout 3600 \
      venv/bin/python -m pytest \$FAST -n 24 --dist loadfile --timeout=240 \
      --timeout-method=signal --max-worker-restart=0 -p no:randomly 2>&1 \
      | grep -vE '^\.|^tests/.*[.sxF]{2,}' | tail -120
    RC_FAST=\${PIPESTATUS[0]}
  fi
  if [ -n \"\$BROWSER\" ]; then
    echo \"--- browser files, SERIAL (\$(echo \$BROWSER | wc -w) file(s)) ---\"
    env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 timeout 1800 \
      venv/bin/python -m pytest \$BROWSER --timeout=240 \
      --timeout-method=signal -p no:randomly 2>&1 \
      | grep -vE '^\.|^tests/.*[.sxF]{2,}' | tail -60
    RC_BROW=\${PIPESTATUS[0]}
  fi
  [ \"\$RC_FAST\" -ne 0 ] && exit \$RC_FAST
  exit \$RC_BROW
# KEEP THE DIAGNOSIS. tail -18 preserved only the summary line, so every
# assertion was discarded and the failures had to be re-run remotely to learn
# anything -- a diagnostic that throws away the diagnosis. Progress dots are
# dropped instead, which is what was actually worth losing.
" 2>&1
echo "PREFLIGHT_RC=$?"
date -u +'   end %H:%M:%S'
} > "$LOG" 2>&1
tail -3 "$LOG"
