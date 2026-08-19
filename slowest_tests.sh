#!/usr/bin/env bash
# slowest_tests.sh -- report the slowest tests + a duration distribution.
#
# run_tests.py --json emits a schema-v2 result file with duration_seconds per
# test (the same data capture.sh collects). This runs it (under the SERVICE VENV,
# so flask/playwright/bs4 resolve) and sorts -- OR parses an existing json.
#
# Usage:
#   ./slowest_tests.sh                       # run full suite under the venv
#   ./slowest_tests.sh --workers=180         # parallel (extra args pass through)
#   ./slowest_tests.sh --from PATH.json      # just report from an existing json
#                                            #   (e.g. capture.sh's results)
# Env:
#   BD_PYTHON=/path/to/venv/bin/python       # override python (else auto-detect)
#   SLOWEST_N=30   OUT=/tmp/bd_test_durations.json
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
OUT="${OUT:-/tmp/bd_test_durations.json}"
N="${SLOWEST_N:-30}"

# --- resolve a python that has the app deps (venv), not bare python3 ---
PY="${BD_PYTHON:-}"
if [ -z "$PY" ]; then
  for c in "$ROOT/venv/bin/python" "$ROOT/.venv/bin/python" \
           "${BD_TEST_PYTHON:-}"; do
    [ -n "$c" ] || continue
    [ -x "$c" ] && PY="$c" && break
  done
fi
[ -z "$PY" ] && PY="python3"

# --- --from MODE: report from an existing durations json, no re-run ---
if [ "${1:-}" = "--from" ] && [ -n "${2:-}" ]; then
  OUT="$2"
else
  # sanity: warn if the chosen python lacks flask (would mass-fail on imports)
  if ! "$PY" -c "import flask" >/dev/null 2>&1; then
    echo "WARNING: $PY has no 'flask' -- results will be import-failures."
    echo "         set BD_PYTHON to the service venv python, or use --from <json>."
  fi
  echo "Running the full suite with per-test timing under: $PY"
  "$PY" run_tests.py tests/ --json="$OUT" "$@"
  echo
fi

"$PY" - "$OUT" "$N" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception as e:
    print("could not read durations json:", e); sys.exit(1)
n = int(sys.argv[2])
tests = [t for t in data.get("tests", []) if t.get("status") != "skip"]
if not tests:
    print("no per-test durations in the json (needs run_tests.py schema_version >= 2)")
    sys.exit(0)
tests.sort(key=lambda t: t.get("duration_seconds", 0.0), reverse=True)
tot = sum(t.get("duration_seconds", 0.0) for t in tests)
nfail = sum(1 for t in tests if t.get("status") == "fail")
print(f"================ SLOWEST {n} TESTS "
      f"({len(tests)} run, {nfail} failed, {tot:.1f}s total test-time) ================")
for t in tests[:n]:
    print(f"  {t.get('duration_seconds', 0.0):8.3f}s  "
          f"{t.get('file','?')} :: {t.get('test','?')}")
buckets = [(0.1, "< 0.1s"), (1, "0.1-1s"), (5, "1-5s"),
           (30, "5-30s"), (float("inf"), "> 30s")]
counts = {lbl: 0 for _, lbl in buckets}
for t in tests:
    d = t.get("duration_seconds", 0.0)
    for lim, lbl in buckets:
        if d < lim:
            counts[lbl] += 1
            break
print("\n  duration distribution:")
for _, lbl in buckets:
    print(f"    {lbl:8}: {counts[lbl]:5}  {'#' * min(60, counts[lbl])}")
PY
