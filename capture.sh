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
#   - --workers=N is parsed and forwarded to real pytest via pytest-xdist
#     (was hardcoded --workers=4, so `./capture.sh --workers=90` was a no-op);
#     unknown args (e.g. a bare --summary) are accepted and ignored
#   - private capture fixtures remain opt-in. Callers can export the absolute
#     roots documented by capture_test_fixtures.py; step [2] inherits them.
#     No private path is autodetected.
#   - new step [2a] regenerates reports/gui_parity_inventory.{json,md} AFTER
#     the systemd service is stopped and confirmed inactive, and BEFORE the
#     pytest lanes. That file is gitignored yet SHIPS in the zip, so a stale
#     copy from an earlier overlay deploy outlives `git clean -fd` (ignored
#     files need -x) and the reconcile gate read it as inventory drift,
#     failing the whole suite. Regenerating makes the gate compare
#     fresh-vs-fresh. The ordering against the service stop is not cosmetic:
#     the generator does `import bulk_downloader.app`, whose module body
#     unconditionally runs db_init(), db_integrity_check() and five
#     scheduler/thread starters, so run before the stop it put a second
#     process on the live BD_HOME database. It is never fatal: it records
#     PARITY_EXIT (4 = skipped because the service was still up), warns
#     loudly, and hands the code to the verdict.
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
# is forwarded to step [2]'s pytest-xdist run. Unknown flags (e.g. a bare
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

# ── capture vault (optional, prompted once) ──────────────────────
#
# This step stops the service and step [4] starts a FRESH process, and the
# master key is in-memory only -- so the vault is necessarily LOCKED when the
# seeder runs at [5a], and an operator unlocking beforehand cannot survive the
# restart. L6/L8 were unsatisfiable here no matter what the operator did.
#
# So the capture gets its OWN vault, holding only the fixture's published test
# credential. The operator's secrets.json is never opened. The password is
# asked for ONCE, here at t=0 rather than 35 minutes in at [5a], so the run
# stays unattended after a single keystroke.
#
# It is deliberately never written down. capture.sh:739 tars the whole of
# $OUT and the operator ships that bundle to third parties -- so the vault
# lives OUTSIDE $OUT and the password reaches curl on stdin, never in argv
# (which /proc publishes to every user on the box).
#
# Blank, or no TTY, means skip: L6/L8 then WARN exactly as they do today.
# A prompt that blocked an unattended run would turn a capture into a hang.
CAPTURE_VAULT=0
CAPTURE_VAULT_PW=""
CAPTURE_VAULT_DIR="/tmp/bd_capture_vault"
CAPTURE_VAULT_FILE="$CAPTURE_VAULT_DIR/secrets.json"
CAPTURE_VAULT_DROPIN="/etc/systemd/system/bulkdownloader.service.d/20-capture-vault.conf"

if [ -t 0 ]; then
  printf 'Capture-vault password (blank = skip the L6/L8 login checks): ' >&2
  read -rs CAPTURE_VAULT_PW
  printf '\n' >&2
  if [ -n "$CAPTURE_VAULT_PW" ]; then
    CAPTURE_VAULT=1
    echo "  capture vault ENABLED -- the operator vault is not opened"
  else
    echo "  capture vault skipped -- L6/L8 will WARN as before"
  fi
else
  echo "  no TTY: capture vault skipped -- L6/L8 will WARN as before"
fi

# `systemctl restart` returns once systemd has STARTED the unit, NOT once the
# app is serving: waitress needs roughly three more seconds to bind :5555. A
# boot journal shows "Started bulkdownloader.service" at 19:00:17 and
# "[waitress] Serving on http://0.0.0.0:5555" at 19:00:20. Steps [7] and [9]
# curl that port as soon as the teardown below returns, so with no wait they
# meet a REFUSED connection -- seen on a real capture as
#   curl: (7) Failed to connect to localhost port 5555 after 0 ms
# on BOTH ("after 0 ms" is a refusal, not a timeout), which turned an otherwise
# fine run into dev-tools exit=1; http-smoke exit=1 and CAPTURE VERDICT: FAIL.
#
# BOUNDED, deliberately: an unbounded wait would convert a restart that never
# comes back into a hung capture, which is strictly worse than the two failed
# steps it replaces. 40 attempts x 0.5s is ~20s of waiting against the ~3s
# actually needed; each probe is itself capped by --max-time 2, so even if the
# port accepts and then stalls on every attempt the loop cannot run past ~100s.
#
# AND NEVER SILENT: on timeout it warns and leaves SERVICE_READY_EXIT=1, which
# the verdict reads as a stage exit. A probe that never saw the app serving
# does not know that it is, and capture.sh does not abort mid-run by design --
# so the verdict is the only place an unknown can fail (CLAUDE.md 0).
#
# The probe uses the SAME origin steps [7] and [9] use. Polling 127.0.0.1 while
# they use localhost would certify a socket they may never reach.
CAPTURE_READY_URL="http://localhost:5555/api/health"
CAPTURE_READY_TRIES=40
SERVICE_READY_EXIT=0
wait_for_service_ready() {
  local tries=0
  local started
  started=$(date +%s)
  while [ "$tries" -lt "$CAPTURE_READY_TRIES" ]; do
    if curl -sSf -o /dev/null --max-time 2 "$CAPTURE_READY_URL" 2>/dev/null
    then
      SERVICE_READY_EXIT=0
      echo "  service serving again after $(( $(date +%s) - started ))s"
      return 0
    fi
    sleep 0.5
    tries=$((tries + 1))
  done
  SERVICE_READY_EXIT=1
  echo "  WARNING: no answer from $CAPTURE_READY_URL after" \
       "$CAPTURE_READY_TRIES attempts over $(( $(date +%s) - started ))s;" \
       "steps [7]-[9] are about to probe an app that is not known to be" \
       "serving" >&2
  return 1
}

# Removing the drop-in is NOT enough: systemd passes Environment= at start, so
# the RUNNING service keeps the capture vault until something restarts it. Skip
# the restart and the operator's BD would keep reporting an empty credential
# store -- their real passwords would look like they had vanished. Idempotent,
# because it is reached both explicitly after [6] and from the EXIT trap.
#
# The readiness wait lives INSIDE this branch, not beside the call site after
# [6]: a capture with a blank password or no TTY never wrote a drop-in and
# never restarts anything, so it must not pay for a wait. Keeping the two
# together makes it impossible to restart without waiting, or to wait without
# having restarted -- reachability derived rather than asserted.
#
# It therefore also runs on the EXIT-trap path, where nothing later needs the
# app. That is wanted, not tolerated: an interrupt is exactly when the operator
# most needs to know their BD came back off the capture vault, and the wait
# ends the moment it answers.
cleanup_capture_vault() {
  if [ "$CAPTURE_VAULT" = "1" ]; then
    CAPTURE_VAULT=0
    CAPTURE_VAULT_PW=""
    sudo rm -f "$CAPTURE_VAULT_DROPIN" 2>/dev/null || true
    sudo systemctl daemon-reload 2>/dev/null || true
    sudo systemctl restart bulkdownloader 2>/dev/null || true
    rm -rf "$CAPTURE_VAULT_DIR"
    echo "  capture vault removed; service restarted on the operator vault"
    wait_for_service_ready || true
  fi
}

# Armed HERE, not down in the seed section, because the drop-in is written at
# step [4] and an EXIT trap registered after that leaves a window in which an
# interrupt strands it -- BD would restart on the CAPTURE vault and the
# operator's real credentials would look like they had vanished. That window
# was 109 lines wide and covered steps [5] and [5a]; it was found on the box
# minutes after this feature shipped, by an interrupt that happened to land
# before the drop-in was written rather than after.
#
# bash keeps ONE EXIT trap per shell, so this aggregate is also how the seed
# teardown is reached: a second `trap ... EXIT` further down would REPLACE
# this one rather than add to it. cleanup_live_seed is defined much later, so
# it is called only once it exists -- an interrupt before that point has no
# seed state to remove, but may well have a drop-in to clear. Ordering is
# load-bearing: the seed teardown talks to the app over HTTP, and
# cleanup_capture_vault restarts the service, so the seed half must go first.
cleanup_all() {
  if declare -F cleanup_live_seed >/dev/null 2>&1; then
    cleanup_live_seed
  fi
  cleanup_capture_vault
}
trap cleanup_all EXIT

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

# The Flask root and real-browser tests require the built SPA, and packaging
# declares this directory as required runtime data. A clean source checkout
# intentionally ignores frontend/dist, so fail before stopping services or
# running thousands of tests with misleading 503s. Build it explicitly from
# the checked-in lockfile first: `(cd frontend && npm ci && npm run build)`.
if [ ! -f "$BD_HOME/frontend/dist/index.html" ]; then
  echo "FATAL: frontend/dist/index.html is missing." >&2
  echo "Build the SPA before capture:" >&2
  echo "  (cd \"$BD_HOME/frontend\" && npm ci && npm run build)" >&2
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

# ── [2a/9] GUI-parity inventory regen (service down) ──────────────
#
# WHY THIS EXISTS: reports/gui_parity_inventory.json is BUILD-TIME GENERATED
# and GITIGNORED (.gitignore `reports/*`), yet it ships inside the release zip.
# The deploy path is an overlay — `unzip -o` overwrites and adds but never
# deletes — and `git clean -fd` cannot remove an ignored file (that needs -x).
# So a copy generated on a build host whose tree differed from this box can sit
# here indefinitely with nothing able to evict it. The suite's reconcile gate
# (tests/test_v3_66_302_gui_parity_reconcile.py) diffs the on-disk inventory
# against a live regen, so that stale copy reads as inventory drift and fails
# the entire capture — observed on the box as
# "only-shipped=[] only-regen=['pytest_capture_results']".
#
# Regenerating HERE puts the gate on fresh-vs-fresh. THE POSITION IS
# LOAD-BEARING ON THREE SIDES. If you came here to tidy the ordering, this is
# the paragraph you need:
#
#   * AFTER the __pycache__ purge above. The tool imports bulk_downloader.app,
#     so a regen placed earlier would read stale bytecode.
#
#   * AFTER `sudo systemctl stop bulkdownloader` AND after the is-active
#     confirmation above. THIS IS THE ONE THAT LOOKS LIKE COSMETICS AND IS NOT.
#     tools/gui_parity_inventory.py does `import bulk_downloader.app` (inside
#     _routes_from_app, the "live url_map" path this block demands below), and
#     that module's TOP LEVEL runs, unconditionally, on import:
#         db_init()                        app.py L80
#         db_integrity_check()             app.py L89
#         _start_session_keepers()         app.py L1494
#         _start_watch_folder_threads()    app.py L1569
#         _start_window_scheduler()        app.py L1638
#         _start_storage_tier_scheduler()  app.py L1658
#         _start_watcher()                 app.py L2062
#     Run while the service is live, that is a SECOND process running a SQLite
#     integrity check and starting five scheduler/thread groups against the
#     same BD_HOME database the service is serving. BD_DISABLE_KEEPALIVE=1
#     (which the tool sets for itself) suppresses ONE of those subsystems, not
#     the other six. Until this cut the block sat 24 lines ABOVE the stop and
#     did exactly that on every capture.
#
#   * BEFORE the pytest lanes below — a regen after them proves nothing.
#
# If the stop did not take, the regen is SKIPPED rather than run anyway: the
# collision above is the whole reason for this position, so "the service is
# still active" makes the inventory UNKNOWN (PARITY_EXIT=4), not
# stale-or-fresh. Unknown is a third state and it fails. That costs nothing in
# verdict terms — SERVICE_STOP_EXIT is already a --stage-exit, so a box whose
# service would not stop was failing the capture regardless.
#
# Exit 0 is NOT sufficient evidence of a good regen: the generator falls back
# to ENDPOINT_CATALOG.md when the app import raises, writes a
# catalog-derived item set, and still returns 0. The written JSON is therefore
# read back for `"route_source": "live url_map"`; missing file or any other
# source is UNKNOWN, and unknown is a third state that fails. Never fatal —
# every remaining step still runs and PARITY_EXIT goes to the verdict.
echo "=== [2a/9] GUI-parity inventory regen ==="
PARITY_JSON="$BD_HOME/reports/gui_parity_inventory.json"
PARITY_EXIT=0
if [ "$SERVICE_STOP_EXIT" -ne 0 ]; then
  echo "  SKIPPED: bulkdownloader is still active. Importing the app now would" >&2
  echo "  run a second db_integrity_check and start five scheduler groups" >&2
  echo "  against the live database. The inventory is therefore UNKNOWN, not" >&2
  echo "  stale-or-fresh." >&2
  PARITY_EXIT=4
else
  venv/bin/python tools/gui_parity_inventory.py \
     > "$OUT/02a_gui_parity_inventory.log" 2>&1
  PARITY_EXIT=$?
  if [ "$PARITY_EXIT" -ne 0 ]; then
    echo "  WARNING: gui-parity inventory regen FAILED (exit=$PARITY_EXIT)." >&2
    echo "  reports/gui_parity_inventory.json was not refreshed, so the parity" >&2
    echo "  reconcile gate may report inventory drift against a stale copy and" >&2
    echo "  fail the suite. See $OUT/02a_gui_parity_inventory.log" >&2
  elif [ ! -f "$PARITY_JSON" ]; then
    echo "  WARNING: regen reported success but $PARITY_JSON is missing." >&2
    echo "  UNKNOWN state: the parity reconcile gate may report inventory drift." >&2
    PARITY_EXIT=2
  # ONE spelling of this predicate now, in capture.sh, install_linux.sh and
  # scripts/provision_test_host.sh: PARSE the JSON, never grep for the
  # substring `"route_source": "live url_map"`. The substring form is hostage
  # to how tools/gui_parity_inventory.py serialises (json.dumps(...,
  # indent=2)) — change the indent or the key separator and the grep starts
  # calling a perfectly good inventory degraded, which is a gate firing on
  # identity. install_linux.sh carried the strict literal and this file a
  # whitespace-tolerant regex; they agreed by luck, and the strict one would
  # have gone wrong first. A parse cannot be wrong about formatting. A file
  # that will not parse is NOT a pass: json.load raising exits non-zero and
  # lands in the same branch as a wrong route_source, because both mean "not
  # proven app-derived".
  elif ! venv/bin/python -c 'import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:
    print("gui_parity_inventory.json will not parse: %s" % exc, file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0 if d.get("route_source") == "live url_map" else 1)' "$PARITY_JSON"; then
    echo "  WARNING: inventory was built from the ENDPOINT_CATALOG.md fallback," >&2
    echo "  not the live url_map — the app import degraded silently and the tool" >&2
    echo "  still exited 0. The item set is catalog-derived, so the parity" >&2
    echo "  reconcile gate may report inventory drift." >&2
    PARITY_EXIT=3
  fi
fi
echo "  exit=$PARITY_EXIT"

run_with_heartbeat "parallel-safe pytest lane" "$OUT/02_pytest_parallel.log" \
   env BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest \
   -q tests --tb=short \
   -m capture_parallel \
   -n "$WORKERS" --dist loadfile \
   --junitxml="$OUT/02_pytest_parallel.xml"
PARALLEL_EXIT=$?
run_with_heartbeat "serial pytest lane" "$OUT/02_pytest_serial.log" \
   env BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest \
   -q tests --tb=short \
   -m capture_serial \
   -n 0 \
   --junitxml="$OUT/02_pytest_serial.xml"
SERIAL_EXIT=$?
{
  echo "=== parallel-safe pytest lane (exit=$PARALLEL_EXIT, workers=$WORKERS) ==="
  cat "$OUT/02_pytest_parallel.log"
  echo
  echo "=== serial pytest lane (exit=$SERIAL_EXIT, workers=0) ==="
  cat "$OUT/02_pytest_serial.log"
} > "$OUT/02_suite_run.log"
venv/bin/python tools/pytest_capture_results.py \
   --junit "$OUT/02_pytest_parallel.xml" \
   --junit "$OUT/02_pytest_serial.xml" \
   --json "$OUT/02_test_results.json" \
   --summary "$OUT/02_SUMMARY.txt" \
   >> "$OUT/02_suite_run.log" 2>&1
RESULTS_EXIT=$?
if [ "$RESULTS_EXIT" -ne 0 ]; then
  SUITE_EXIT=$RESULTS_EXIT
elif [ "$PARALLEL_EXIT" -ne 0 ]; then
  SUITE_EXIT=$PARALLEL_EXIT
else
  SUITE_EXIT=$SERIAL_EXIT
fi
echo "  --- tail of suite run ---"
tail -25 "$OUT/02_suite_run.log"
echo "  Summary written: $OUT/02_SUMMARY.txt"

# ── [2b/9] deployment-local source-graph gate ─────────────────────────
# The trust anchor is a small content pin OUTSIDE the install tree.  Rebuild the
# graph from the currently deployed source into a throwaway directory, compare
# canonical node/edge rows, and delete the SQLite database on every exit path.
# A missing pin is UNKNOWN by default and a hard failure for release/OPV runs
# that set BD_REQUIRE_GRAPH_HASH=1.  Pin generation is a separate, deliberate
# deployment-acceptance step; never write a pin immediately before this check.
# bd_graph_gate_function_begin
run_graph_hash_gate() (
  graph_pin="${BD_GRAPH_HASH_PIN:-/var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256}"
  graph_required="${BD_REQUIRE_GRAPH_HASH:-0}"

  if [ ! -f "$graph_pin" ]; then
    echo "graph content pin: MISSING -- $graph_pin"
    if [ "$graph_required" = "1" ]; then
      echo "graph content pin is required (BD_REQUIRE_GRAPH_HASH=1)"
      return 1
    fi
    echo "graph content pin: UNKNOWN -- optional check not armed"
    return 0
  fi
  if [ ! -r "$graph_pin" ]; then
    echo "graph content pin: UNREADABLE -- $graph_pin"
    if [ "$graph_required" = "1" ]; then
      echo "graph content pin must be readable (BD_REQUIRE_GRAPH_HASH=1)"
      return 1
    fi
    echo "graph content pin: UNKNOWN -- optional check not armed"
    return 0
  fi
  if [ ! -x venv/bin/python ] || [ ! -f tools/l0_extract.py ] \
      || [ ! -f tools/graph_build.py ]; then
    echo "graph content pin: CANNOT EVALUATE -- graph tools or venv missing"
    return 2
  fi

  graph_tmp=$(mktemp -d "${TMPDIR:-/tmp}/bd_graph.XXXXXX") || {
    echo "graph content pin: CANNOT EVALUATE -- mktemp failed"
    return 2
  }
  cleanup_graph_tmp() {
    if [ -n "${graph_tmp:-}" ]; then
      rm -rf -- "$graph_tmp"
    fi
  }
  trap cleanup_graph_tmp EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  graph_db="$graph_tmp/KNOWLEDGE_GRAPH.db"

  venv/bin/python tools/l0_extract.py --root "$PWD" --db "$graph_db" || return $?
  venv/bin/python tools/graph_build.py --db "$graph_db" \
      --hash-pin "$graph_pin" --check-hash
)
# bd_graph_gate_function_end

echo "=== [2b/9] graph content-hash gate (P2) ==="
GRAPH_EXIT=0
run_graph_hash_gate > "$OUT/02b_graph_checkhash.log" 2>&1
GRAPH_EXIT=$?
echo "graph-gate exit: $GRAPH_EXIT" >> "$OUT/02b_graph_checkhash.log"
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

# The drop-in must exist BEFORE install_service.sh, because that is what runs
# daemon-reload and starts the unit. Written after, it would not reach the
# running process and the seeder would meet the operator's locked vault
# exactly as before -- while the capture reported it had set one up.
#
# A drop-in rather than .env on purpose: _envfile.py records ".env, not a
# systemd drop-in" for OPERATOR configuration, which is the GUI editor's
# persistence target. This is machine-written scaffolding removed inside the
# same run. The deciding factor is the failure mode -- a stale .env line is
# invisible to the GUI editor's model, while a stale drop-in is the first
# thing `systemctl cat bulkdownloader` shows.
if [ "$CAPTURE_VAULT" = "1" ]; then
  rm -rf "$CAPTURE_VAULT_DIR"
  mkdir -p "$CAPTURE_VAULT_DIR"
  chmod 700 "$CAPTURE_VAULT_DIR"
  sudo mkdir -p "$(dirname "$CAPTURE_VAULT_DROPIN")"
  sudo tee "$CAPTURE_VAULT_DROPIN" >/dev/null <<DROPIN
[Service]
Environment=BD_SECRETS_FILE=$CAPTURE_VAULT_FILE
Environment=BD_CAPTURE_VAULT=1
DROPIN
  echo "  capture-vault drop-in written -> $CAPTURE_VAULT_DROPIN"
fi

./install_service.sh > "$OUT/04_service_install.log" 2>&1
INSTALL_EXIT=$?
sleep 3
systemctl status bulkdownloader --no-pager > "$OUT/04_service_status.log" 2>&1
journalctl -u bulkdownloader -n 50 --no-pager > "$OUT/04_service_boot.log" 2>&1
ACTIVE=$(systemctl is-active bulkdownloader 2>&1)
if [ "$ACTIVE" = "active" ]; then SERVICE_EXIT=0; else SERVICE_EXIT=1; fi
echo "  service: $ACTIVE"

# Unlock the CAPTURE vault, after the service is up and before [5a] seeds --
# the seeder refuses on a locked vault, which is the whole reason this exists.
# The password goes in on stdin (--data-binary @-), never in argv: /proc makes
# a process's command line readable by every user on the box. Only the HTTP
# code is recorded; the response body is discarded so nothing about the
# credential can reach $OUT, which is tarred into the shared bundle.
if [ "$CAPTURE_VAULT" = "1" ] && [ "$ACTIVE" = "active" ]; then
  UNLOCK_CODE=$(printf '{"password":"%s"}' "$CAPTURE_VAULT_PW" \
    | curl -sS -o /dev/null -w '%{http_code}' \
        -X POST http://127.0.0.1:5555/api/secrets/unlock \
        -H 'Content-Type: application/json' --data-binary @- 2>/dev/null)
  echo "  capture-vault unlock: HTTP ${UNLOCK_CODE:-000}" \
    | tee -a "$OUT/04_service_status.log"
  if [ "${UNLOCK_CODE:-000}" != "200" ]; then
    echo "  WARNING: capture vault did not unlock; L6/L8 will WARN and the" \
         "seeder will refuse the login half" >&2
  fi
fi

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

# ── [5a/9] Seed synthetic input for the live checks ──────────────
#
# Several live checks WARN because nothing has exercised them: no queued
# URLs, no completed downloads. Those warnings are honest, and since
# v3.66.818 they no longer fail the verdict — but on a capture host they
# are permanently unactionable, because only a human queueing real work
# clears them. tools/live_seed.py supplies that work.
#
# WHAT IS AND IS NOT BEING CLAIMED: the seed is INPUT, never OUTPUT. It
# queues marked URLs pointing at the LOCAL fixture origin; BD still has to
# accept, persist, rehydrate and report every one of them, and all of that
# is BD's own work. A check that went green because the seeder handed it
# the answer would be vacuous — worse than the warning it replaced — so
# the seeder writes only through the app's HTTP API and marks everything
# it creates. See tools/live_seed.py for the full contract.
#
# The seeder refuses to run on a host that already holds real work, and
# refuses when it cannot tell, so pointing capture.sh at a live box does
# not quietly mix synthetic rows into real ones.
#
# ENTIRELY NON-FATAL: any failure here degrades to the live checks' own
# existing WARNs. Aborting the capture because an optional convenience
# failed would be strictly worse than the warning it was meant to remove.
echo "=== [5a/9] Seed synthetic live-check input ==="
FIXTURE_PID=""
SEEDED=0

# Teardown must run whether the live suite passed, failed, or the operator
# pressed Ctrl-C. Synthetic state left behind is read as REAL work by the
# next run — including by the seeder's own preflight, which refuses a host
# holding real entries — so one interrupted capture would otherwise wedge
# every later one. Idempotent: safe to call twice.
cleanup_live_seed() {
  if [ "$SEEDED" = "1" ]; then
    SEEDED=0
    venv/bin/python tools/live_seed.py --teardown \
      >> "$OUT/05a_live_seed.log" 2>&1 \
      || echo "  WARNING: seed teardown failed — synthetic rows may remain;" \
              "run: venv/bin/python tools/live_seed.py --teardown" >&2
  fi
  if [ -n "$FIXTURE_PID" ]; then
    _stop_process_group "$FIXTURE_PID"
    FIXTURE_PID=""
  fi
}
# NOTE: run_graph_hash_gate defines its own `trap cleanup_graph_tmp EXIT`,
# but it is a SUBSHELL function — `run_graph_hash_gate() (` with parens —
# so that trap is scoped to the subshell and this global one does not
# collide with it. bash keeps only one EXIT trap per shell; converting
# that function to braces would silently disable one of the two.
#
# The capture vault rides the SAME trap for exactly that reason: a second
# `trap ... EXIT` would not add a handler, it would REPLACE this one, silently
# disabling the seed teardown. Both cleanups are idempotent, so the explicit
# calls after [6] and this backstop can both fire.
#
# cleanup_all is defined and ARMED at the top of this file, next to
# cleanup_capture_vault -- it has to be, because the drop-in is written at step
# [4] and a trap armed here would leave steps [4]-[5a] unprotected. It reaches
# cleanup_live_seed by name once this definition exists.

if [ -f "$BD_HOME/tools/live_seed.py" ] && [ -f "$BD_HOME/tools/fixture_site.py" ]; then
  # The seeded URLs point at the local fixture origin, so it has to be
  # serving before anything is queued. setsid detaches it into its own
  # process group so _stop_process_group can take the whole tree down.
  setsid venv/bin/python tools/fixture_site.py --port 8899 \
    > "$OUT/05a_fixture_site.log" 2>&1 &
  FIXTURE_PID=$!
  _fixture_up=0
  _tries=0
  while [ "$_tries" -lt 20 ]; do
    if curl -sSf -o /dev/null "http://127.0.0.1:8899/" 2>/dev/null; then
      _fixture_up=1
      break
    fi
    sleep 0.25
    _tries=$((_tries + 1))
  done
  if [ "$_fixture_up" = "1" ]; then
    echo "  fixture site up on :8899 (pid $FIXTURE_PID)"
    # --login is best-effort on top of --seed: it needs an unlocked, encrypted
    # secrets backend to store the fixture password, and refuses cleanly when
    # that precondition is absent. SEEDED is set either way so teardown always
    # runs against whatever did get created.
    # BD_SEED_FORCE=1 passes --force through to the seeder's preflight, which
    # otherwise REFUSES when the queue already holds real work ("seeding could
    # be mistaken for the operator's own work and teardown would be
    # ambiguous"). That refusal is correct as a default and it fired on a real
    # run, leaving the seeded live checks unexercised with no way to ask for
    # them short of emptying the operator's queue.
    #
    # Overriding it is safe because teardown is MARKER-matched, not
    # queue-wide: `_is_seeded()` is `SEED_MARKER in entry["url"]`, so nothing
    # without `bdseed` in its URL can be removed. The guard is conservative
    # about a hazard teardown does not actually have.
    _seed_force=""
    [ "${BD_SEED_FORCE:-0}" = "1" ] && _seed_force="--force"
    # --start is what makes L11 / L12 / L14 exercisable AT ALL. They gate on a
    # COMPLETED download, and until now nothing anywhere started one: the
    # seeder placed URLs and stopped, so the last capture found them sitting in
    # `waiting` with `running=0` and all three checks reported "no completed
    # downloads yet" — which reads as BD failing to download, when BD was never
    # asked to. live_tests/ structurally cannot ask (Context is read-only by
    # design), so the seeder does.
    #
    # It only ever starts a site whose NAME carries the marker — the same
    # predicate teardown uses — so an operator site is never drained, and it
    # refuses outright if it cannot prove ownership.
    #
    # --start-timeout is explicit rather than defaulted because THIS is the
    # unattended caller: the number a reader needs is the one bounding the
    # capture, and it belongs where they will look for it. The fixture serves
    # 4-16 KB files, so the normal path settles in seconds; the bound is for
    # the abnormal one. A timeout exits non-zero and is reported per URL, which
    # lands in the `else` branch below as a warning, not a capture failure.
    if venv/bin/python tools/live_seed.py --seed --start --start-timeout 180 \
         --login --count 3 $_seed_force \
         > "$OUT/05a_live_seed.log" 2>&1; then
      SEEDED=1
      echo "  seeded 3 marked URLs + started the queue + fixture login — queue"
      echo "  and auth checks are exercising SYNTHETIC input;"
      echo "  see $OUT/05a_live_seed.log"
    else
      # A refusal can still have created something before it stopped, so mark
      # the run as seeded regardless -- teardown is idempotent and removing
      # nothing is cheaper than stranding marked state on the box.
      SEEDED=1
      echo "  seeding declined or failed (not a capture failure):"
      tail -3 "$OUT/05a_live_seed.log" 2>/dev/null | sed 's/^/    /'
    fi
  else
    echo "  fixture site did not come up on :8899 — skipping seed"
    _stop_process_group "$FIXTURE_PID"
    FIXTURE_PID=""
  fi
else
  echo "  tools/live_seed.py or tools/fixture_site.py absent — skipping seed"
fi

# ── [5b/9] Display for the headed-browser check ──────────────────
#
# L2 (headed-browser-launch) opens a VISIBLE Chromium — headless=False is a
# DANGER_MAP invariant, so the check exists to prove the interactive-login
# path works on this deployment. Without an X server it WARNs, which is
# honest but permanently unactionable.
#
# WHY THIS EXISTS: scripts/provision_test_host.sh already starts Xvfb and
# exports DISPLAY — but that export dies with the provisioner's process.
# capture.sh runs later, in a different shell, and had no DISPLAY of its
# own, so L2 warned even on a correctly provisioned box unless the operator
# had exported DISPLAY by hand. The capability was provisioned and then
# never handed over. This closes that handoff.
#
# This is PROVISION, not seeding: it supplies a real X server so a real
# headed browser really launches. L2's assertion is untouched — if the
# browser cannot start, L2 still fails.
#
# NON-FATAL BY DESIGN: a headless box with no Xvfb is a legitimate
# deployment. Absence degrades to L2's existing WARN (informational since
# the capture verdict stopped gating on warnings); it must never abort the
# capture, which would turn an honest warning into a broken run.
#
# bd_start_display comes from the shared fragment so the launch, the
# idempotency and the "is this display actually served" probing all live in
# one place. Re-implementing an Xvfb launch here would recreate the
# three-copies-that-drift problem the fragment was built to end.
echo "=== [5b/9] Display for headed-browser check ==="
if [ -n "${DISPLAY:-}" ]; then
  echo "  DISPLAY already set to '$DISPLAY' — leaving it alone"
elif [ -r "$BD_HOME/scripts/lib/system_deps.sh" ]; then
  # shellcheck source=scripts/lib/system_deps.sh
  . "$BD_HOME/scripts/lib/system_deps.sh" 2>/dev/null || true
  if declare -F bd_start_display >/dev/null 2>&1; then
    if _cap_display="$(bd_start_display :99 2>/tmp/bd_display.err)"; then
      export DISPLAY="$_cap_display"
      echo "  DISPLAY=$DISPLAY (headed-browser checks can run)"
    else
      echo "  no display available — L2 will WARN (not a failure):"
      sed 's/^/    /' /tmp/bd_display.err 2>/dev/null | tail -3
    fi
    rm -f /tmp/bd_display.err
  else
    echo "  system_deps.sh sourced but bd_start_display undefined — L2 will WARN"
  fi
else
  echo "  scripts/lib/system_deps.sh not readable — L2 will WARN"
fi

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
LIVE_IDS="L1,L2,L3,L4,L5,L6,L7,L8,L9,L10,L11,L12,L13,L14,L15,L16,L17,L18,L19,L20,L21,L22,L23,L24,L25,L26,L27,L28,L29,L30,L31,L32,L33,L34,L35,L36,L37"
# EXPECTED_LIVE_TESTS comes from the REGISTRY, never from LIVE_IDS.
#
# It used to be `awk -F, NF` over LIVE_IDS itself, which made the completeness
# gate compare the selection with itself: it caught an ID that was requested and
# did not run, and could never catch an ID that existed and was never requested.
# L36 and L37 sat in that gap and had never run in any capture -- and L36 covers
# the stale-or-half-built frontend/dist/ class that a git deploy silently does
# not deliver, which no other check sees.
#
# Reading the registry gives the verdict an independent denominator, so adding a
# check to live_tests/checks.py without adding it here now fails the capture
# instead of passing quietly. Importing `checks` is what registers them; harness
# alone yields an empty list.
EXPECTED_LIVE_TESTS=$(venv/bin/python -c \
  'from live_tests import checks, harness; print(len(harness.registry()))' \
  2>"$OUT/06_registry_count.err")
if ! printf '%s' "$EXPECTED_LIVE_TESTS" | grep -qE '^[1-9][0-9]*$'; then
  # Unknown is a third state and it fails. A blank or zero count here would
  # otherwise reach capture_verdict.py as a number that cannot be compared,
  # and the one thing this must never do is let the verdict pass unverified.
  echo "  BD-GATE-UNRUNNABLE: could not read the live-check registry"
  echo "  (got '${EXPECTED_LIVE_TESTS:-<empty>}'; see $OUT/06_registry_count.err)"
  EXPECTED_LIVE_TESTS=-1
fi
echo "  registry reports $EXPECTED_LIVE_TESTS check(s); requesting $(printf '%s\n' "$LIVE_IDS" | awk -F, '{print NF}')"
run_with_heartbeat "live-test suite" "$OUT/06_live_tests.log" \
   env BD_DISABLE_KEEPALIVE=1 venv/bin/python -u -m live_tests.run \
   --only "$LIVE_IDS" \
   --per-check-timeout 90
LIVE_EXIT=$?
echo "  --- tail of live tests ---"
tail -10 "$OUT/06_live_tests.log"
echo "  exit=$LIVE_EXIT"

# Remove the synthetic state now that the checks that needed it have run, so
# the remaining steps and the operator see the box as they found it. The EXIT
# trap is the backstop for an interrupt; this is the normal path, and running
# it here means the state is gone before steps 7-9 inspect anything.
# cleanup_live_seed is idempotent, so the trap firing later is harmless.
cleanup_live_seed
# Restore the operator vault here rather than at EXIT: steps [7]-[9] run
# against the live app, and they should see the box as the operator keeps it,
# not as the capture staged it. Idempotent, so the EXIT backstop still covers
# an interrupt before this point.
cleanup_capture_vault

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
  --stage-exit "service-stopped=$SERVICE_STOP_EXIT" \
  --stage-exit "parity-inventory=$PARITY_EXIT" \
  --stage-exit "graph=$GRAPH_EXIT" \
  --stage-exit "csrf=$CSRF_EXIT" \
  --stage-exit "service-install=$INSTALL_EXIT" \
  --stage-exit "service-active=$SERVICE_EXIT" \
  --stage-exit "service-ready=$SERVICE_READY_EXIT" \
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
