#!/usr/bin/env bash
# capture.sh — fresh-machine validation for BulkDownloader
#
# Runs a 9-step diagnostic against ~/BulkDownloader, writes all output
# under /tmp/bd_capture/, and bundles the result into a tarball you can
# upload back to Claude for analysis.
#
# Maintenance edit (capture.sh-only; NOT a release cut — no version bump):
#   - step [0] now clears __pycache__/*.pyc before any step, so the suite,
#     the CSRF import and the freshly-installed service can never validate
#     stale bytecode after a bare overlay (the v3.66.161 stale-.pyc footgun)
#   - step [7] probe `sse_smoke` corrected to `sse_status` — `sse_smoke` is
#     not a registered route, so it returned {"error":"endpoint not found"}
#     on every run; the real dev SSE diagnostic is /api/dev/sse_status
#   - --workers=N is now parsed and forwarded to step [2] run_tests.py
#     (was hardcoded --workers=4, so `./capture.sh --workers=90` was a no-op);
#     unknown args (e.g. a bare --summary) are accepted and ignored
#   - private capture fixtures remain opt-in. Callers can export the absolute
#     roots documented by capture_test_fixtures.py; step [2] inherits them.
#     No private path is autodetected.
#
# Updated for v3.63.6:
#   - capture.sh now ships in the release zip (was previously stash-local,
#     so KB-documented fixes had to be hand-applied after every deploy)
#   - step [3] CSRF diag uses module-level `app` (the create_app factory
#     does not exist; the canonical shape is `from bulk_downloader.app
#     import app`)
#   - step [6] LIVE_IDS now includes L3 (mv3-extension-service-worker);
#     the v3.63.5 extension fix (channel="chromium" + the three icon
#     PNGs) means L3 PASSes cleanly — it is no longer a known wedge
#
# Earlier change history:
#   v3.63.3:
#     - dropped step [6] live_check.py (file removed from repo)
#     - step [6] now runs live_tests with --per-check-timeout 90
#     - capture dir renamed /tmp/v3_63_1_capture -> /tmp/bd_capture
#     - explicit deactivate of the service before suite, restart after,
#       to avoid the port-5555 collision

set -u  # don't set -e: we want all 9 steps to run even if one errors

BD_HOME="${BD_HOME:-$HOME/BulkDownloader}"
OUT="/tmp/bd_capture"
ARCHIVE="/tmp/bd_capture.tar.gz"

# ── Arg parsing ──────────────────────────────────────────────────
# Before this edit capture.sh ignored ALL args, so `./capture.sh
# --workers=90` silently ran the hardcoded --workers=4. Now --workers
# is forwarded to step [2]'s run_tests.py. Unknown flags (e.g. a bare
# --summary) are accepted and ignored for backward compatibility.
WORKERS=4
while [ $# -gt 0 ]; do
  case "$1" in
    --workers=*) WORKERS="${1#*=}" ;;
    --workers)   shift; WORKERS="${1:-4}" ;;
    *)           ;;   # ignore unknown args (back-compat)
  esac
  shift
done

case "$WORKERS" in
  ''|*[!0-9]*) echo "FATAL: --workers must be a positive integer" >&2; exit 2 ;;
esac
if [ "$WORKERS" -lt 1 ]; then
  echo "FATAL: --workers must be at least 1" >&2
  exit 2
fi
if ! command -v setsid >/dev/null 2>&1; then
  echo "FATAL: setsid is required for safe worker cleanup" >&2
  exit 2
fi

_stop_process_group() {
  local child_pid="$1"
  local tick=0
  kill -TERM -- "-$child_pid" 2>/dev/null \
    || kill -TERM "$child_pid" 2>/dev/null \
    || true
  while [ "$tick" -lt 10 ] && kill -0 "$child_pid" 2>/dev/null; do
    sleep 1
    tick=$((tick + 1))
  done
  if kill -0 "$child_pid" 2>/dev/null; then
    kill -KILL -- "-$child_pid" 2>/dev/null \
      || kill -KILL "$child_pid" 2>/dev/null \
      || true
  fi
  wait "$child_pid" 2>/dev/null || true
}

# Keep long commands quiet while reporting elapsed progress once a minute.
# Polling here avoids a second monitor process and delays completion by at most
# one second; the child command's complete output still lands in its artifact.
run_with_heartbeat() {
  local label="$1"
  local logfile="$2"
  shift 2
  local started pid elapsed tick
  started=$(date +%s)
  setsid "$@" > "$logfile" 2>&1 &
  pid=$!
  trap '_stop_process_group "$pid"; trap - INT TERM HUP; exit 130' INT
  trap '_stop_process_group "$pid"; trap - INT TERM HUP; exit 143' TERM
  trap '_stop_process_group "$pid"; trap - INT TERM HUP; exit 129' HUP
  while kill -0 "$pid" 2>/dev/null; do
    tick=0
    while [ "$tick" -lt 60 ] && kill -0 "$pid" 2>/dev/null; do
      sleep 1
      tick=$((tick + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
      elapsed=$(($(date +%s) - started))
      echo "  progress: $label still running (${elapsed}s elapsed)"
    fi
  done
  wait "$pid"
  local command_exit=$?
  trap - INT TERM HUP
  return "$command_exit"
}

# Private WACZ/JSON evidence is intentionally outside the release. Validate
# opt-in roots once, before the long run, so a typo cannot silently turn the
# integration lane back into dozens of skips. The helper owns these test-only
# settings; they are not operator-facing service configuration.
cd "$BD_HOME" || { echo "FATAL: $BD_HOME not found"; exit 2; }
if ! venv/bin/python - <<'PY'
import sys
from capture_test_fixtures import validate_capture_fixture_roots

try:
    validate_capture_fixture_roots()
except ValueError as exc:
    print(f"FATAL: {exc}", file=sys.stderr)
    raise SystemExit(2)
PY
then
  exit 2
fi

rm -rf "$OUT" "$ARCHIVE"
mkdir -p "$OUT"

# Clear stale bytecode BEFORE any step runs. An overlaid .py with an mtime
# older than an existing __pycache__/*.pyc makes CPython run the STALE
# bytecode (observed at v3.66.161: on-disk __init__.py=161 but the process
# reported 160 until caches were cleared). Without this, the suite (step 2),
# the CSRF import (step 3) and the freshly-installed service (steps 4-9) can
# all load old bytecode and the capture would validate a stale tree. Cheap
# and idempotent — safe to run unconditionally.
find "$BD_HOME" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
find "$BD_HOME" -name '*.pyc' -delete 2>/dev/null

VERSION=$(grep '^__version__' bulk_downloader/__init__.py 2>/dev/null | head -1)
echo "================================================================"
echo "  BulkDownloader capture — $(date -Iseconds)"
echo "  bd_home : $BD_HOME"
echo "  version : $VERSION"
echo "  workers : $WORKERS"
echo "  output  : $OUT/  -> $ARCHIVE"
echo "================================================================"

# ── [1/9] System fingerprint ──────────────────────────────────────
echo "=== [1/9] System fingerprint ==="
{
 echo "--- date ---"; date -Iseconds
 echo "--- uname ---"; uname -a
 echo "--- os-release ---"; cat /etc/os-release 2>/dev/null
 echo "--- python ---"; venv/bin/python --version 2>&1
 echo "--- pip freeze (top 30) ---"; venv/bin/pip freeze 2>/dev/null | head -30
 echo "--- disk free ---"; df -h "$BD_HOME" /tmp /var 2>/dev/null
 echo "--- memory ---"; free -h 2>/dev/null
 echo "--- listening ports ---"; ss -tlnp 2>/dev/null | head -20
 echo "--- version we installed ---"; head -3 bulk_downloader/__init__.py
 head -10 CHANGELOG.md
} > "$OUT/01_sysinfo.log" 2>&1
echo "  done"

# ── [2/9] Full suite (service must NOT be running) ────────────────
echo "=== [2/9] Full test suite (5-15 min) ==="
sudo systemctl stop bulkdownloader 2>/dev/null
STOP_REQUEST_EXIT=$?
if systemctl is-active --quiet bulkdownloader 2>/dev/null; then
  SERVICE_STOP_EXIT=1
else
  SERVICE_STOP_EXIT=0
fi
echo "  service stop request exit=$STOP_REQUEST_EXIT; inactive=$((1 - SERVICE_STOP_EXIT))"
sleep 1
run_with_heartbeat "full test suite" "$OUT/02_suite_run.log" \
   env BD_DISABLE_KEEPALIVE=1 venv/bin/python run_tests.py \
   --workers="$WORKERS" \
   --summary="$OUT/02_SUMMARY.txt" \
   --json="$OUT/02_test_results.json"
SUITE_EXIT=$?
echo "  --- tail of suite run ---"
tail -25 "$OUT/02_suite_run.log"
echo "  Summary written: $OUT/02_SUMMARY.txt"

# ── [2b/9] graph content-hash gate (P2 @533) ─────────────────────
# Recompute-and-compare the KNOWLEDGE_GRAPH.db content pin so a re-saved /
# drifted graph is caught on-stash, not just in the sandbox. CONDITIONAL: the
# graph db is a volatile audit artifact, NOT shipped in the deploy zip, so this
# is a graceful no-op on a stash that has no graph db. It fires only when the db
# is deployed alongside for an on-stash audit refresh.
echo "=== [2b/9] graph content-hash gate (P2) ==="
GRAPH_EXIT=0
{
  GRAPH_DB=""
  for cand in ./review/artifacts/KNOWLEDGE_GRAPH.db /home/claude/review/artifacts/KNOWLEDGE_GRAPH.db; do
    if [ -f "$cand" ] && [ -f "$cand.sha256" ]; then GRAPH_DB="$cand"; break; fi
  done
  if [ -n "$GRAPH_DB" ] && [ -f tools/graph_build.py ]; then
    venv/bin/python tools/graph_build.py --db "$GRAPH_DB" \
        --hash-pin "$GRAPH_DB.sha256" --check-hash
    GRAPH_EXIT=$?
    echo "graph-gate exit: $GRAPH_EXIT"
  else
    echo "  (no graph db+pin present -- P2 check-hash skipped; db is not a deploy artifact)"
  fi
} > "$OUT/02b_graph_checkhash.log" 2>&1
tail -5 "$OUT/02b_graph_checkhash.log" | sed 's|^|  |'

# ── [3/9] CSRF diagnostic (no app running) ───────────────────────
#
# v3.63.6: imports the module-level `app` directly. The factory
# `create_app()` does not exist in this codebase — `bulk_downloader/
# app.py` builds the Flask app at module scope (`app = Flask(...)`).
# Pre-v3.63.6 capture.sh imported a nonexistent `create_app`, so this
# step ImportErrored on every run.
#
echo "=== [3/9] CSRF diagnostic ==="
BD_DISABLE_KEEPALIVE=1 venv/bin/python -c "
from bulk_downloader.app import app
with app.test_client() as c:
   r = c.get('/')
   print('status:', r.status_code)
   print('body length:', len(r.data))
   print('contains meta tag:', b'<meta name=\"csrf-token\"' in r.data)
   print('contains marker  :', b'{{ csrf_token }}' in r.data)
   print('Set-Cookie headers:', len(r.headers.getlist('Set-Cookie')))
   for h in r.headers.getlist('Set-Cookie'):
       print(' ', h[:120])
print('=' * 60)
print('END diagnostic')
print('=' * 60)
" > "$OUT/03_csrf_diag.log" 2>&1
CSRF_EXIT=$?
echo "  done"

# ── [4/9] Install + start systemd service ─────────────────────────
echo "=== [4/9] Install + start systemd service ==="
./install_service.sh > "$OUT/04_service_install.log" 2>&1
INSTALL_EXIT=$?
sleep 3
systemctl status bulkdownloader --no-pager > "$OUT/04_service_status.log" 2>&1
journalctl -u bulkdownloader -n 50 --no-pager > "$OUT/04_service_boot.log" 2>&1
ACTIVE=$(systemctl is-active bulkdownloader 2>&1)
if [ "$ACTIVE" = "active" ]; then SERVICE_EXIT=0; else SERVICE_EXIT=1; fi
echo "  service: $ACTIVE"

# ── [5/9] Ollama status ──────────────────────────────────────────
echo "=== [5/9] Ollama status ==="
{
 echo "--- systemctl status ---"
 systemctl status ollama --no-pager 2>&1 | head -10
 echo "--- journalctl tail ---"
 journalctl -u ollama -n 20 --no-pager 2>&1 | tail -20
 echo "--- ollama list ---"
 ollama list 2>&1
 echo "--- API check ---"
 curl -sS http://localhost:11434/api/tags 2>&1
} > "$OUT/05_ollama.log" 2>&1
echo "  done"

# ── [6/9] Live-test suite (running app) ──────────────────────────
#
# v3.63.6: L3 (mv3-extension-service-worker) is back in LIVE_IDS.
# The v3.63.5 extension fix means it PASSes cleanly now: the three
# placeholder PNG icons (extension/icon{16,48,128}.png) let Chromium
# load the extension at all, and channel="chromium" in checks.py
# swaps from the headless-shell binary (which cannot host MV3 SWs)
# to the full Chromium-for-Testing binary. The earlier "Chromium MV3
# known wedge" rationale was wrong — see LESSONS_LEARNED A1/A17.
#
# Per-check timeout stays at 90s (T55 contains the wedge; L34
# also benefits from the larger budget).
#
echo "=== [6/9] Live-test suite ==="
LIVE_IDS="L1,L2,L3,L4,L5,L6,L7,L8,L9,L10,L11,L12,L13,L14,L15,L16,L17,L18,L19,L20,L21,L22,L23,L24,L25,L26,L27,L28,L29,L30,L31,L32,L33,L34,L35"
EXPECTED_LIVE_TESTS=$(printf '%s\n' "$LIVE_IDS" | awk -F, '{print NF}')
run_with_heartbeat "live-test suite" "$OUT/06_live_tests.log" \
   env BD_DISABLE_KEEPALIVE=1 venv/bin/python -u -m live_tests.run \
   --only "$LIVE_IDS" \
   --per-check-timeout 90
LIVE_EXIT=$?
echo "  --- tail of live tests ---"
tail -10 "$OUT/06_live_tests.log"
echo "  exit=$LIVE_EXIT"

# ── [7/9] Dev-tool routes against the live app ───────────────────
echo "=== [7/9] Dev-tool routes ==="
DEV_EXIT=0
{
 for route in enabled test_timing dead_css storage_tier_status \
              maintenance_mode i18n_coverage model_pull_check \
              feature_flags golden_files request_replay_list \
              login_flows mem_audit threads leak_scan routes \
              sse_status; do
   echo "--- /api/dev/$route ---"
   response=$(curl -fsS --max-time 30 \
       "http://localhost:5555/api/dev/$route" 2>&1)
   route_exit=$?
   printf '%s\n' "$response" | head -40
   if [ "$route_exit" -ne 0 ]; then
     DEV_EXIT=1
     echo "ERROR: route probe exit=$route_exit"
   fi
   echo
 done
} > "$OUT/07_dev_tools.log" 2>&1
echo "  done"

# ── [8/9] T51 regenerate_goldens dry-run ─────────────────────────
echo "=== [8/9] T51 regenerate_goldens dry-run ==="
venv/bin/python tools/regenerate_goldens.py > "$OUT/08_t51_dryrun.log" 2>&1
T51_EXIT=$?
echo "  exit=$T51_EXIT"

# ── [9/9] Quick HTTP smoke ───────────────────────────────────────
echo "=== [9/9] HTTP smoke ==="
SMOKE_EXIT=0
{
 echo "--- GET / ---"
 if ! curl -fsS -o /dev/null -w "status=%{http_code}  size=%{size_download}  time=%{time_total}s\n" \
     http://localhost:5555/; then SMOKE_EXIT=1; fi
 echo "--- GET /api/health ---"
 if ! curl -fsS --max-time 5 http://localhost:5555/api/health 2>&1; then
   SMOKE_EXIT=1
 fi
 echo "--- GET /api/dev/routes (count) ---"
 if ! curl -fsS --max-time 30 http://localhost:5555/api/dev/routes 2>&1 \
     | venv/bin/python -c "import sys,json; d=json.load(sys.stdin); print('routes:', len(d.get('routes', [])))" 2>&1; then
   SMOKE_EXIT=1
 fi
} > "$OUT/09_http_smoke.log" 2>&1
echo "  done"

# Compute the certification result before bundling, but never stop here: a
# failed run is most useful when all diagnostics still make it into the archive.
echo "=== [verdict] Certification result ==="
venv/bin/python tools/capture_verdict.py \
  --tests-json "$OUT/02_test_results.json" \
  --live-log "$OUT/06_live_tests.log" \
  --suite-exit "$SUITE_EXIT" \
  --live-exit "$LIVE_EXIT" \
  --expected-live-tests "$EXPECTED_LIVE_TESTS" \
  --stage-exit "graph=$GRAPH_EXIT" \
  --stage-exit "service-stopped=$SERVICE_STOP_EXIT" \
  --stage-exit "csrf=$CSRF_EXIT" \
  --stage-exit "service-install=$INSTALL_EXIT" \
  --stage-exit "service-active=$SERVICE_EXIT" \
  --stage-exit "dev-tools=$DEV_EXIT" \
  --stage-exit "goldens=$T51_EXIT" \
  --stage-exit "http-smoke=$SMOKE_EXIT" \
  > "$OUT/10_VERDICT.txt" 2>&1
FINAL_EXIT=$?
cat "$OUT/10_VERDICT.txt"

# ── Bundle ───────────────────────────────────────────────────────
echo "================================================================"
echo "  Bundling..."
tar czf "$ARCHIVE" -C /tmp "$(basename $OUT)"
BUNDLE_EXIT=$?
if [ "$BUNDLE_EXIT" -eq 0 ]; then
  ls -la "$ARCHIVE"
  echo "  Archive: $ARCHIVE"
  echo "  Contents:"
  tar tzf "$ARCHIVE" | sed 's|^|    |'
else
  echo "  ERROR: could not create $ARCHIVE (exit=$BUNDLE_EXIT)" >&2
  FINAL_EXIT=2
fi
echo "================================================================"
if [ "$FINAL_EXIT" -eq 0 ]; then
  echo "  PASS. Upload $ARCHIVE for the complete evidence bundle."
elif [ "$BUNDLE_EXIT" -ne 0 ]; then
  echo "  FAIL (exit=$FINAL_EXIT). No archive was created."
else
  echo "  FAIL (exit=$FINAL_EXIT). Upload $ARCHIVE for diagnosis."
fi
echo "================================================================"
exit "$FINAL_EXIT"
