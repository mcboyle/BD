#!/bin/bash
# The sanctioned full suite against CURRENT origin/main, on an idle fleet host.
# The operator wants one full suite at the very end; running it against main NOW
# tells us whether the BASE is clean, so a failure at the end can be attributed
# to the cuts rather than discovered as a surprise. Exact sanctioned command --
# every token is load-bearing (CLAUDE.md A5).
set -u
HOST="$1"; W=/tmp/bd-fullsuite
OUT=/home/mboyle/fleet-run-artifacts/2026-08-25/preflight/fullsuite-main.log
mkdir -p "$(dirname "$OUT")"
{
echo "== full suite vs origin/main on $HOST  $(date -u +%H:%M:%S)"
ssh -o BatchMode=yes -o StrictHostKeyChecking=no "$HOST" "
  set -e; cd ~/BulkDownloader; git fetch --quiet origin
  rm -rf $W; git worktree prune; git worktree add --quiet --detach $W origin/main
  ln -sfn ~/BulkDownloader/venv $W/venv
  ln -sfn ~/BulkDownloader/frontend/node_modules $W/frontend/node_modules 2>/dev/null || true
  echo 'base:' \$(git -C $W rev-parse --short HEAD) \$(git -C $W show HEAD:bulk_downloader/__init__.py | sed -n 's/^__version__ = \"\(.*\)\"/\1/p')
  cd $W
  env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest tests/ \
    -n 24 --dist loadfile --timeout=240 --timeout-method=signal --max-worker-restart=0 -p no:randomly 2>&1 | tail -25
  exit \${PIPESTATUS[0]}
" 2>&1
echo "FULLSUITE_RC=$?"
date -u +'   end %H:%M:%S'
} > "$OUT" 2>&1
