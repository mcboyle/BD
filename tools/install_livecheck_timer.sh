#!/usr/bin/env bash
# install_livecheck_timer.sh -- PT7, optional.
#
# Install a systemd timer that runs the live_tests/ suite against the
# running bulkdownloader service on a schedule. Default schedule is
# daily at 08:00 local time, with a 30-minute randomized delay so two
# hosts don't all fire at the same wall-clock instant.
#
# Why daily, not hourly: live_tests/ exercises real work (browser
# launches, DB integrity, optional disruptive checks). Hourly is too
# aggressive for diagnostics that change on a multi-day timescale.
# The original PT7 proposal said "hourly" -- daily is the operator-
# attentive single-host default; override with --schedule.
#
# Why a separate optional installer (not folded into install_service.sh):
#   - The single-operator default is "operator runs capture.sh when
#     they want a check." Timers are for unattended stretches.
#   - The schedule is operator-tunable without touching the main
#     service unit.
#   - Mirrors PT16 install_memory_limits.sh: opt-in, drop-in.
#
# Usage:
#     ./tools/install_livecheck_timer.sh                   # default daily 08:00
#     ./tools/install_livecheck_timer.sh --schedule daily
#     ./tools/install_livecheck_timer.sh --schedule hourly
#     ./tools/install_livecheck_timer.sh --schedule "Mon,Thu 09:00"
#     ./tools/install_livecheck_timer.sh --remove
#
# Inspect:
#     systemctl status bd-livecheck.timer
#     systemctl list-timers bd-livecheck.timer
#     journalctl -u bd-livecheck.service -n 100         # last results
#
# Manually trigger now:
#     sudo systemctl start bd-livecheck.service
#
# PT7's source proposal also mentioned "fire a notification on FAIL."
# This installer does not add notifications -- desktop/SMS plumbing is
# orthogonal and not portable across operator setups. Instead, every
# run leaves a journal entry; `journalctl -u bd-livecheck.service -p err`
# filters to FAIL output. If the operator wants a real notifier later,
# wire it on top of OnFailure= in bd-livecheck.service.

set -u

SERVICE_NAME="bd-livecheck"
SERVICE_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
TIMER_UNIT="/etc/systemd/system/${SERVICE_NAME}.timer"
DEFAULT_SCHEDULE="*-*-* 08:00:00"

die() { echo "ERROR: $*" >&2; exit 1; }

if [ "$(uname -s)" != "Linux" ]; then
    die "Linux-only (systemd timers)"
fi

if ! command -v systemctl >/dev/null 2>&1; then
    die "systemctl not found -- systemd required"
fi

# --- argument parsing ---------------------------------------------
SCHEDULE=""
REMOVE=0
URL="http://localhost:5555"
PER_CHECK_TIMEOUT="60"
while [ $# -gt 0 ]; do
    case "$1" in
        --remove|-r) REMOVE=1; shift ;;
        --schedule)  SCHEDULE="$2"; shift 2 ;;
        --url)       URL="$2"; shift 2 ;;
        --per-check-timeout) PER_CHECK_TIMEOUT="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,40p' "$0"; exit 0 ;;
        *)  die "unknown option: $1" ;;
    esac
done

# --- uninstall path -----------------------------------------------
if [ "$REMOVE" -eq 1 ]; then
    sudo systemctl stop "${SERVICE_NAME}.timer" 2>/dev/null || true
    sudo systemctl disable "${SERVICE_NAME}.timer" 2>/dev/null || true
    rm_did=0
    for u in "$TIMER_UNIT" "$SERVICE_UNIT"; do
        if [ -f "$u" ]; then
            sudo rm -f "$u"
            echo "Removed $u"
            rm_did=1
        fi
    done
    if [ "$rm_did" -eq 1 ]; then
        sudo systemctl daemon-reload
    else
        echo "Nothing to remove."
    fi
    exit 0
fi

# --- normalize schedule -------------------------------------------
case "${SCHEDULE:-}" in
    "")        SCHEDULE="$DEFAULT_SCHEDULE" ;;
    daily)     SCHEDULE="*-*-* 08:00:00" ;;
    hourly)    SCHEDULE="*-*-* *:00:00" ;;
    weekly)    SCHEDULE="Mon *-*-* 08:00:00" ;;
    *)         ;; # operator-supplied OnCalendar string, passed through
esac

# --- resolve app dir + run user -----------------------------------
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
RUN_USER="$(whoami)"

if [ -x "$APP_DIR/venv/bin/python" ]; then
    PYEXE="$APP_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYEXE="$(command -v python3)"
else
    die "no Python found"
fi

if [ ! -d "$APP_DIR/live_tests" ]; then
    die "$APP_DIR/live_tests/ not found -- wrong app dir?"
fi

echo " ================================================================"
echo "  BulkDownloader - livecheck timer install"
echo " ================================================================"
echo "  App dir   : $APP_DIR"
echo "  Run user  : $RUN_USER"
echo "  Python    : $PYEXE"
echo "  Schedule  : $SCHEDULE"
echo "  Target URL: $URL"
echo "  Timeout/c : ${PER_CHECK_TIMEOUT}s"

# --- write the service unit ---------------------------------------
echo
echo "  Writing $SERVICE_UNIT (needs sudo)..."
sudo tee "$SERVICE_UNIT" >/dev/null <<UNIT
[Unit]
Description=BulkDownloader live-tests run (PT7 timer-driven)
# Only run if the main service is up -- otherwise we're checking nothing.
Requisite=bulkdownloader.service
After=bulkdownloader.service

[Service]
Type=oneshot
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
Environment=BD_HOME=${APP_DIR}
# Type=oneshot + the timer = no Restart= needed. A FAIL stays a FAIL
# until the next scheduled run; that's the point. journalctl shows
# the exit code and the run's stdout/stderr.
ExecStart=${PYEXE} -m live_tests.run --url ${URL} --bd-home ${APP_DIR} --per-check-timeout ${PER_CHECK_TIMEOUT}
# Prevent a wedged run from blocking the next one indefinitely.
# With ~30 live tests at 60s each in the worst case, 30 min is a
# generous ceiling.
TimeoutStartSec=30min
UNIT

# --- write the timer unit -----------------------------------------
echo "  Writing $TIMER_UNIT (needs sudo)..."
sudo tee "$TIMER_UNIT" >/dev/null <<UNIT
[Unit]
Description=BulkDownloader live-tests timer (PT7)

[Timer]
OnCalendar=${SCHEDULE}
# Catch up if the host was off when the timer should have fired --
# useful for laptops or any host that's not 24/7.
Persistent=true
# Smear two hosts apart, and avoid hammering the app at the exact
# scheduled instant.
RandomizedDelaySec=30min
# Match the service unit name explicitly (defaults to bd-livecheck.service
# anyway, but being explicit guards against rename drift).
Unit=${SERVICE_NAME}.service

[Install]
WantedBy=timers.target
UNIT

echo "  Reloading systemd..."
sudo systemctl daemon-reload

echo "  Enabling ${SERVICE_NAME}.timer (start on boot)..."
sudo systemctl enable "${SERVICE_NAME}.timer" >/dev/null 2>&1

echo "  Starting ${SERVICE_NAME}.timer..."
sudo systemctl start "${SERVICE_NAME}.timer"

echo
echo " ================================================================"
if systemctl is-active --quiet "${SERVICE_NAME}.timer"; then
    echo "  ${SERVICE_NAME}.timer is RUNNING."
    echo
    echo "  Next run:"
    systemctl list-timers "${SERVICE_NAME}.timer" --no-pager \
        2>/dev/null | head -3
else
    echo "  WARNING: ${SERVICE_NAME}.timer did not start. Check:"
    echo "    sudo systemctl status ${SERVICE_NAME}.timer"
    echo "    journalctl -u ${SERVICE_NAME}.timer -n 50"
fi
echo
echo "  Run now (don't wait for the timer):"
echo "    sudo systemctl start ${SERVICE_NAME}.service"
echo "  Inspect the last result:"
echo "    journalctl -u ${SERVICE_NAME}.service -n 100 --no-pager"
echo "  Remove:"
echo "    $0 --remove"
echo " ================================================================"
