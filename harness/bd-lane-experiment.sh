#!/bin/bash
# THE WHOLE-TREE LANE EXPERIMENT, as v3.66.923 ran it and the allowlist header
# records: run the ENTIRE suite in ONE parallel lane, then retry every failure
# SERIALLY. A test that fails in parallel and passes serially is LANE PLACEMENT,
# not a defect. A test that fails both ways is a real defect and must not be
# laundered into a lane decision.
#
# Why this and not per-file leakprobe: the allowlist was regenerated at @923 for
# 1232 files; the tree now has 1422. The ~190 added since were never reviewed and
# fail-closed into serial. That is the same silent drift the floor gate's own
# docstring describes -- "86% of it drifted into the serial lane and capture went
# from ~10 minutes to ~45" -- happening again.
set -uo pipefail
HOST="${1:?host}"; TAG="${2:?tag}"
OUT=/home/mboyle/fleet-run-artifacts/2026-08-25/lane-experiment
mkdir -p "$OUT"

ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" bash -s <<'REMOTE' 2>&1 | tee "$OUT/$TAG.log"
set -uo pipefail
cd /home/mboyle/BulkDownloader || { echo "FATAL: no repo"; exit 90; }
[ -x venv/bin/python ] || { echo "FATAL: no venv/bin/python"; exit 92; }
C=$(git rev-parse HEAD); echo "HOST=$(hostname) COMMIT=$C CORES=$(nproc) START=$(date -u +%FT%TZ)"

N=$(git ls-files 'tests/test*.py' | wc -l)
echo "DENOMINATOR: $N tracked test files"
[ "$N" -gt 1000 ] || { echo "FATAL: implausible denominator $N"; exit 93; }

echo "=== PHASE 1: the WHOLE tree in ONE parallel lane, -n \$(nproc) --dist loadfile ==="
/usr/bin/time -f "PHASE1_WALL=%e s" env BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 \
  venv/bin/python -m pytest tests -n "$(nproc)" --dist loadfile -q -p no:randomly \
  --tb=no --junitxml=/tmp/lane-whole.xml 2>&1 | tail -25

echo "=== PHASE 2: every failure retried SERIALLY ==="
FAILED=$(venv/bin/python - <<'PY'
import xml.etree.ElementTree as ET
try: t=ET.parse("/tmp/lane-whole.xml")
except Exception as e: print(""); raise SystemExit
out=[]
for tc in t.iter("testcase"):
    if tc.find("failure") is not None or tc.find("error") is not None:
        cn=(tc.get("classname") or "").replace(".","/")
        out.append(f"{cn}.py::{tc.get('name')}" if not cn.endswith(".py") else f"{cn}::{tc.get('name')}")
print("\n".join(out))
PY
)
CNT=$(printf '%s\n' "$FAILED" | grep -c . || true)
echo "PARALLEL_FAILURES=$CNT"
if [ "$CNT" -eq 0 ]; then
  echo "VERDICT: zero parallel failures -- every file in the tree is parallel-safe at this commit."
else
  printf '%s\n' "$FAILED" | while read -r nid; do
    [ -z "$nid" ] && continue
    if env BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest "$nid" -q -p no:randomly --tb=no >/dev/null 2>&1; then
      echo "  LANE_PLACEMENT (passes serially): $nid"
    else
      echo "  REAL_DEFECT (fails both ways):    $nid"
    fi
  done
fi
echo "END=$(date -u +%FT%TZ)"
REMOTE
