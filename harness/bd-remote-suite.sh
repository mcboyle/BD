#!/bin/bash
# Run the SANCTIONED full suite on a remote fleet host, and REFUSE rather than
# report anything it could not actually measure.
#
# Written 2026-08-26 after the inline version shipped the exact defect this run
# exists to kill: ssh lands in $HOME, not the repo, so `git status --porcelain |
# wc -l` counted zero lines FROM A FAILED COMMAND and printed CLEAN=0 -- an
# unavailable measurement rendered as a clean tree. It also swallowed RC=127
# behind a trailing `head`, so the task reported success.
set -uo pipefail
H="${1:?host ip}"; TAG="${2:?tag}"
R=/home/mboyle/BulkDownloader
LOCAL=/home/mboyle/fleet-run-artifacts/2026-08-25/remote-suite
mkdir -p "$LOCAL"
REMOTE=/home/mboyle/bd-suite-$TAG.log

ssh -o BatchMode=yes -o ConnectTimeout=10 "$H" bash -s <<REMOTE_EOF > "$LOCAL/$TAG.head" 2>&1
set -uo pipefail
cd $R || { echo "FATAL: no repo at $R"; exit 90; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "FATAL: $R is not a git repo"; exit 91; }
[ -x venv/bin/python ] || { echo "FATAL: no venv/bin/python -- the repo env is 'venv', not '.venv'"; exit 92; }

C=\$(git rev-parse HEAD 2>/dev/null)
[ -n "\$C" ] || { echo "FATAL: could not read HEAD"; exit 93; }
T=\$(git rev-parse HEAD^{tree} 2>/dev/null)
[ -n "\$T" ] || { echo "FATAL: could not read tree"; exit 94; }
# Clean state must be MEASURED. A failed git status is UNKNOWN, never clean.
if ! ST=\$(git status --porcelain 2>/dev/null); then echo "FATAL: git status failed -- state UNKNOWN"; exit 95; fi
DIRTY=\$(printf '%s' "\$ST" | grep -c . || true)

echo "HOST=\$(hostname) COMMIT=\$C TREE=\$T DIRTY=\$DIRTY"
echo "PY=\$(venv/bin/python -V 2>&1) START=\$(date -u +%FT%TZ) LOAD=\$(cut -d' ' -f1-3 /proc/loadavg)"

# Nonzero denominator BEFORE trusting any verdict.
N=\$(env -u BD_INSTALL_DIR venv/bin/python -m pytest tests/ --collect-only -q -p no:randomly 2>/dev/null | tail -1)
echo "COLLECTED=\$N"

nohup env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 \
  venv/bin/python -m pytest tests/ -n 24 --dist loadfile --timeout=240 \
  --timeout-method=signal --max-worker-restart=0 -p no:randomly \
  > $REMOTE 2>&1 < /dev/null &
echo "LAUNCHED pid=\$! log=$REMOTE"
REMOTE_EOF
RC=$?
cat "$LOCAL/$TAG.head"
if [ "$RC" -ne 0 ]; then echo "REFUSED on $H (rc=$RC) -- nothing launched"; exit "$RC"; fi
grep -q '^LAUNCHED' "$LOCAL/$TAG.head" || { echo "REFUSED: no LAUNCHED marker -- UNKNOWN, not started"; exit 96; }
echo "OK: suite running on $H, remote log $REMOTE"
