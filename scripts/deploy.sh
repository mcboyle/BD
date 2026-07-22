#!/usr/bin/env bash
#
# deploy.sh (F0.1) — one-command operator deploy for BulkDownloader on stash.
#
# Automates the exact sequence the operating instructions spell out by hand, so
# the load-bearing pycache sweep and the post-restart /api/health confirmation
# can never be skipped:
#
#   1. sha256-verify the release zip against an expected digest
#   2. unzip -o over the install dir (optionally excluding live-edited cockpit files)
#   3. sweep __pycache__ dirs AND stray *.pyc  (stale bytecode runs otherwise)
#   4. restart the service
#   5. poll /api/health until "version" == expected (loud FAIL on timeout)
#   6. confirm the venv resolves the cloakbrowser backend
#
# Operator-executed only. stdlib/coreutils + curl; no Python deps. Bash, because
# the install host runs bash; `bash -n` must parse it clean.
#
# Every external command is taken from PATH and the destructive/privileged steps
# are env-overridable, so the test harness can shim curl/systemctl and point the
# script at a scratch dir without touching a real service.
#
# Flags:
#   --zip PATH         release zip to deploy            (required)
#   --expect VERSION   version /api/health must report  (required)
#   --sha SHA256       expected sha256 of the zip       (required unless --skip-sha)
#   --dir PATH         install dir (default: $BD_DEPLOY_DIR or ~/BulkDownloader)
#   --health-url URL   (default: http://localhost:5555/api/health)
#   --timeout SECS     health-poll budget               (default 60)
#   --interval SECS    health-poll interval             (default 2)
#   --exclude-cockpit  skip tools/cockpit_console.py + ENDPOINT_CATALOG.md
#   --skip-sha         skip the sha gate (NOT recommended; prints a warning)
#   --skip-backend-check  skip the venv resolve_backend() check
#
# Env overrides (for tests / unusual hosts):
#   BD_DEPLOY_DIR      default install dir
#   BD_RESTART_CMD     restart command (default: "sudo systemctl restart bulkdownloader")
#   BD_VENV_PYTHON     venv python (default: "$DIR/venv/bin/python")
#
set -euo pipefail

die()  { printf 'deploy.sh: FAIL: %s\n' "$*" >&2; exit 1; }
note() { printf 'deploy.sh: %s\n' "$*"; }

CALLER_DIR="$(pwd -P)"
ZIP=""; EXPECT=""; SHA=""; DIR="${BD_DEPLOY_DIR:-$HOME/BulkDownloader}"
HEALTH_URL="http://localhost:5555/api/health"
TIMEOUT=60; INTERVAL=2
EXCLUDE_COCKPIT=0; SKIP_SHA=0; SKIP_BACKEND=0

while [ $# -gt 0 ]; do
  case "$1" in
    --zip)        ZIP="${2:-}"; shift 2;;
    --expect)     EXPECT="${2:-}"; shift 2;;
    --sha)        SHA="${2:-}"; shift 2;;
    --dir)        DIR="${2:-}"; shift 2;;
    --health-url) HEALTH_URL="${2:-}"; shift 2;;
    --timeout)    TIMEOUT="${2:-}"; shift 2;;
    --interval)   INTERVAL="${2:-}"; shift 2;;
    --exclude-cockpit)   EXCLUDE_COCKPIT=1; shift;;
    --skip-sha)          SKIP_SHA=1; shift;;
    --skip-backend-check) SKIP_BACKEND=1; shift;;
    *) die "unknown argument: $1";;
  esac
done

[ -n "$ZIP" ]    || die "--zip is required"
[ -n "$EXPECT" ] || die "--expect <version> is required"
[ -f "$ZIP" ]    || die "zip not found: $ZIP"
[ -d "$DIR" ]    || die "install dir not found: $DIR"
DIR="$(cd "$DIR" && pwd -P)"

RESTART_CMD="${BD_RESTART_CMD:-sudo systemctl restart bulkdownloader}"
case "${BD_VENV_PYTHON:-}" in
  "") VENV_PY="$DIR/venv/bin/python";;
  /*) VENV_PY="$BD_VENV_PYTHON";;
  *)  VENV_PY="$CALLER_DIR/$BD_VENV_PYTHON";;
esac

# ── 1. sha256 gate ────────────────────────────────────────────────
if [ "$SKIP_SHA" -eq 1 ]; then
  note "WARNING: --skip-sha set; not verifying zip integrity"
else
  [ -n "$SHA" ] || die "--sha is required (or pass --skip-sha)"
  command -v sha256sum >/dev/null 2>&1 || die "sha256sum not on PATH"
  GOT="$(sha256sum "$ZIP" | awk '{print $1}')"
  [ "$GOT" = "$SHA" ] || die "sha256 mismatch: expected $SHA got $GOT"
  note "sha256 OK ($GOT)"
fi

# ── 2. unzip overlay ──────────────────────────────────────────────
command -v unzip >/dev/null 2>&1 || die "unzip not on PATH"
UNZIP_ARGS=(-o "$ZIP" -d "$DIR")
if [ "$EXCLUDE_COCKPIT" -eq 1 ]; then
  UNZIP_ARGS+=(-x "tools/cockpit_console.py" "ENDPOINT_CATALOG.md")
  note "excluding live-edited cockpit files from overlay"
fi
unzip "${UNZIP_ARGS[@]}" >/dev/null || die "unzip failed"
note "overlay applied to $DIR"

# ── 3. pycache sweep (load-bearing) ───────────────────────────────
find "$DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$DIR" -name '*.pyc' -delete 2>/dev/null || true
note "bytecode caches cleared"

# ── 4. restart ────────────────────────────────────────────────────
note "restarting service: $RESTART_CMD"
$RESTART_CMD || die "service restart failed"

# ── 5. health poll ────────────────────────────────────────────────
command -v curl >/dev/null 2>&1 || die "curl not on PATH"
note "polling $HEALTH_URL for version==$EXPECT (timeout ${TIMEOUT}s)"
deadline=$(( $(date +%s) + TIMEOUT ))
got_version=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  body="$(curl -s --max-time 5 "$HEALTH_URL" 2>/dev/null || true)"
  # extract "version":"x.y.z" without a JSON parser (coreutils only)
  got_version="$(printf '%s' "$body" \
      | grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' \
      | head -1 | sed 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')" \
    || got_version=""
  if [ "$got_version" = "$EXPECT" ]; then
    note "/api/health version==$EXPECT confirmed"
    break
  fi
  sleep "$INTERVAL"
done
[ "$got_version" = "$EXPECT" ] \
  || die "health gate: expected version $EXPECT, last saw '${got_version:-<none>}' after ${TIMEOUT}s"

# ── 6. backend check ──────────────────────────────────────────────
if [ "$SKIP_BACKEND" -eq 1 ]; then
  note "skipping resolve_backend() check (--skip-backend-check)"
else
  [ -x "$VENV_PY" ] || die "venv python not executable: $VENV_PY (use --skip-backend-check off-host)"
  if ! backend="$(cd "$DIR" && "$VENV_PY" -c \
    'from bulk_downloader import cloak; print(cloak.resolve_backend())' 2>/dev/null)"; then
    die "resolve_backend() probe execution failed"
  fi
  [ "$backend" = "cloakbrowser" ] \
    || die "resolve_backend()=='${backend:-<none>}', expected 'cloakbrowser' (venv missing cloakbrowser?)"
  note "resolve_backend()==cloakbrowser confirmed"
fi

note "DEPLOY OK — $DIR now running $EXPECT"
