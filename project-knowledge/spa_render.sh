#!/usr/bin/env bash
# spa_render.sh -- boot the SPA backend (built dist on 127.0.0.1:5599) and run
# the render sweep in one call. Reconstructed v3.66.358: the original wrapper
# never shipped in the version.zip; this wraps spa_serve.py (the launcher) plus
# the sweep harnesses (spa_tabs / subtabs_cap / subtabs_click / dark_audit),
# theme-aware via BD_THEME. All renders run against an empty instance.
#
# Usage (wrap in `bd` for PATH/Flask):
#   bd bash spa_render.sh            # boot (if needed) + tabs light+dark + dark_audit
#   bd bash spa_render.sh --tabs     # tabs only (both themes)
#   bd bash spa_render.sh --full     # tabs + drill-in subtabs + filter-tabs + dark_audit
#   bd bash spa_render.sh --no-boot  # assume the backend is already up on :5599
#   bd bash spa_render.sh --stop     # tear the backend down and exit
#
# Env: BD_PORT (5599) · BD_WORK (repository root) · HARNESS_DIR (this directory)
#
# The backend is launched detached via setsid so it SURVIVES this script (and a
# `bd -c` exit) -- follow-up probes can reuse it. Kill with: pkill -f spa_serve.py
set -eu

PORT="${BD_PORT:-5599}"
HARNESS_DIR="${HARNESS_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)}"
BASE="http://127.0.0.1:${PORT}"
BOOT=1
MODE=default

for a in "$@"; do
  case "$a" in
    --no-boot) BOOT=0 ;;
    --tabs)    MODE=tabs ;;
    --full)    MODE=full ;;
    --stop)    MODE=stop ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a (try --help)" >&2; exit 2 ;;
  esac
done

up() { curl -s -o /dev/null -w '%{http_code}' "${BASE}/" 2>/dev/null || echo 000; }
pid() { pgrep -f spa_serve.py 2>/dev/null | head -1; }

if [ "$MODE" = stop ]; then
  pkill -f spa_serve.py 2>/dev/null && echo "==> SPA backend stopped" || echo "==> no SPA backend running"
  exit 0
fi

# ---- boot the backend if it isn't already serving --------------------------
if [ "$BOOT" = 1 ] && [ "$(up)" != 200 ]; then
  echo "==> booting SPA backend on ${BASE} ..."
  setsid python3 "${HARNESS_DIR}/spa_serve.py" > "${HARNESS_DIR}/spa_serve.log" 2>&1 < /dev/null &
  i=0
  while [ "$i" -lt 30 ]; do
    [ "$(up)" = 200 ] && break
    sleep 1; i=$((i + 1))
  done
fi

if [ "$(up)" != 200 ]; then
  echo "FAIL: backend not up on ${BASE} (see ${HARNESS_DIR}/spa_serve.log)" >&2
  exit 1
fi

VER=$(curl -s "${BASE}/api/health" 2>/dev/null | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')
echo "==> backend up on ${BASE}  (version ${VER:-?}, PID $(pid))"

# ---- sweep helpers ----------------------------------------------------------
run_tabs() {
  echo "######## SPA TABS -- LIGHT ########"
  BD_THEME=light BD_OUT="${HARNESS_DIR}/tabs"      python3 "${HARNESS_DIR}/spa_tabs.py"
  echo "######## SPA TABS -- DARK ########"
  BD_THEME=dark  BD_OUT="${HARNESS_DIR}/tabs_dark" python3 "${HARNESS_DIR}/spa_tabs.py"
}
run_subtabs() {
  echo "######## DRILL-IN ROUTES -- LIGHT ########"
  BD_THEME=light BD_OUT="${HARNESS_DIR}/subtabs"      python3 "${HARNESS_DIR}/subtabs_cap.py"
  echo "######## DRILL-IN ROUTES -- DARK ########"
  BD_THEME=dark  BD_OUT="${HARNESS_DIR}/subtabs_dark" python3 "${HARNESS_DIR}/subtabs_cap.py"
  echo "######## IN-PAGE FILTER TABS -- LIGHT ########"
  BD_THEME=light BD_OUT="${HARNESS_DIR}/subtabs"      python3 "${HARNESS_DIR}/subtabs_click.py"
  echo "######## IN-PAGE FILTER TABS -- DARK ########"
  BD_THEME=dark  BD_OUT="${HARNESS_DIR}/subtabs_dark" python3 "${HARNESS_DIR}/subtabs_click.py"
}
run_darkaudit() {
  echo "######## DARK CONTROL-LEAK AUDIT ########"
  python3 "${HARNESS_DIR}/dark_audit.py"
}

# ---- dispatch ---------------------------------------------------------------
case "$MODE" in
  tabs)    run_tabs ;;
  full)    run_tabs; run_subtabs; run_darkaudit ;;
  default) run_tabs; run_darkaudit ;;
esac

echo "==> done. backend left running on ${BASE} (PID $(pid)); kill: pkill -f spa_serve.py"
