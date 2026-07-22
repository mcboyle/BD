#!/usr/bin/env bash
# uninstall_service.sh - reverse of install_service.sh.
#
# Removes:
#   - the bulkdownloader systemd service (stops, disables, removes unit)
#   - the independent AI readiness companion service
#   - $APP_DIR/tools/deployed_version.txt (PT11 marker)
#
# With --purge:
#   - $BD_HOME (or $HOME/bd_home if BD_HOME is unset) and EVERYTHING
#     inside it: bulk_downloader.db, sites_config.json, downloads/,
#     cookies, browser profile data. This is the "user data" that the
#     installer never touches; --purge is the only way to lose it.
#
# Does NOT remove:
#   - the venv (use uninstall_linux.sh)
#   - the Ollama service or models (use uninstall_ai_ollama.sh)
#   - the remote-teach stack (use uninstall_remote_teach.sh)
#
# Flags:
#   --dry-run       print what would be done; do nothing destructive
#   --yes           skip prompts; for CI
#   --purge         ALSO remove $BD_HOME and all user data
#   --keep-models   (accepted for symmetry; no-op here)
#
# Run from the project folder (same place install_service.sh ran from):
#     chmod +x uninstall_service.sh && ./uninstall_service.sh
set -u
set -o pipefail

DRY_RUN="no"
ASSUME_YES="no"
PURGE="no"
for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN="yes" ;;
        --yes|-y)      ASSUME_YES="yes" ;;
        --purge)       PURGE="yes" ;;
        --keep-models) ;;  # no-op
        -h|--help)
            awk 'NR==1 {next}
                 /^[^#]/ && !/^$/ {exit}
                 /^#/ {sub(/^# ?/, ""); print}' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown arg: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

APP_DIR="$(dirname "$(readlink -f "$0")")"
SERVICE_NAME="bulkdownloader"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
AI_SERVICE_NAME="bulkdownloader-ai-ready"
AI_UNIT_PATH="/etc/systemd/system/${AI_SERVICE_NAME}.service"
# Drop-in dir created by install_remote_teach.sh -- this uninstaller
# only removes the bulkdownloader unit; the drop-in is removed by
# uninstall_remote_teach.sh. We do mention it in the summary so the
# operator knows it's still there if remote_teach was installed.
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"

# $BD_HOME default mirrors the app's own resolution: env wins, else
# $HOME/bd_home. We do NOT use SUDO_USER here -- if the operator ran
# this via sudo, BD_HOME from the calling shell is still right.
# (The app itself runs as RUN_USER from the unit, but the data path
# is recorded relative to whatever shell ran the install.)
BD_HOME_DEFAULT="${HOME:-/root}/bd_home"
BD_HOME_RESOLVED="${BD_HOME:-$BD_HOME_DEFAULT}"

run() {
    echo "  + $*"
    if [ "$DRY_RUN" = "no" ]; then
        "$@"
    fi
}

# run_status: like run, but tolerate non-zero exit codes from idempotent
# systemctl ops (stop/disable on an already-stopped/already-disabled
# unit returns non-zero in some systemd versions). We still want to
# proceed with the rest of the uninstall.
run_ok() {
    echo "  + $*"
    if [ "$DRY_RUN" = "no" ]; then
        "$@" || true
    fi
}

echo " ================================================================"
echo "  BulkDownloader - uninstall (systemd service)"
echo " ================================================================"
echo "  Service       : $SERVICE_NAME"
echo "  Unit path     : $UNIT_PATH"
echo "  App dir       : $APP_DIR"
echo "  BD_HOME       : $BD_HOME_RESOLVED"
echo "  Purge data    : $PURGE"
echo "  Dry run       : $DRY_RUN"
echo

# Build the action list for the prompt. systemctl checks here use
# `2>/dev/null` because is-active/is-enabled return non-zero (and
# print to stderr) when the unit doesn't exist, which is a normal
# state on a fresh box and shouldn't pollute output.
HAS_SYSTEMCTL="no"
if command -v systemctl >/dev/null 2>&1; then HAS_SYSTEMCTL="yes"; fi

ACTIONS=()
SYSTEMD_CHANGE_PLANNED="no"
if [ "$HAS_SYSTEMCTL" = "yes" ]; then
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        ACTIONS+=("sudo systemctl stop $SERVICE_NAME")
        SYSTEMD_CHANGE_PLANNED="yes"
    fi
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        ACTIONS+=("sudo systemctl disable $SERVICE_NAME")
        SYSTEMD_CHANGE_PLANNED="yes"
    fi
    if systemctl is-active --quiet "$AI_SERVICE_NAME" 2>/dev/null; then
        ACTIONS+=("sudo systemctl stop $AI_SERVICE_NAME")
        SYSTEMD_CHANGE_PLANNED="yes"
    fi
    if systemctl is-enabled --quiet "$AI_SERVICE_NAME" 2>/dev/null; then
        ACTIONS+=("sudo systemctl disable $AI_SERVICE_NAME")
        SYSTEMD_CHANGE_PLANNED="yes"
    fi
fi
if [ -f "$UNIT_PATH" ]; then
    ACTIONS+=("sudo rm -f $UNIT_PATH")
    SYSTEMD_CHANGE_PLANNED="yes"
fi
if [ -f "$AI_UNIT_PATH" ]; then
    ACTIONS+=("sudo rm -f $AI_UNIT_PATH")
    SYSTEMD_CHANGE_PLANNED="yes"
fi
if [ "$HAS_SYSTEMCTL" = "yes" ] && [ "$SYSTEMD_CHANGE_PLANNED" = "yes" ]; then
    ACTIONS+=("sudo systemctl daemon-reload")
    ACTIONS+=("sudo systemctl reset-failed $SERVICE_NAME")
    ACTIONS+=("sudo systemctl reset-failed $AI_SERVICE_NAME")
fi
[ -f "$APP_DIR/tools/deployed_version.txt" ]     && ACTIONS+=("rm -f $APP_DIR/tools/deployed_version.txt")
if [ "$PURGE" = "yes" ] && [ -d "$BD_HOME_RESOLVED" ]; then
    ACTIONS+=("rm -rf $BD_HOME_RESOLVED  # ALL USER DATA")
fi

if [ "${#ACTIONS[@]}" -eq 0 ]; then
    echo "  Nothing to remove. (Already uninstalled, or never installed here.)"
    if [ -d "$DROPIN_DIR" ]; then
        echo "  Note: $DROPIN_DIR exists (remote_teach drop-in)."
        echo "  Use uninstall_remote_teach.sh to remove that separately."
    fi
    exit 0
fi

echo "  Will perform:"
for a in "${ACTIONS[@]}"; do echo "    $a"; done
echo

# Two prompts when --purge is set: the big-confirm wipe message is loud
# and separate from the "ok to remove the service" prompt, so an
# operator who just wants the service gone can't accidentally type 'y'
# and lose their downloads. --yes bypasses both (documented CI escape).
if [ "$PURGE" = "yes" ] && [ -d "$BD_HOME_RESOLVED" ] && [ "$ASSUME_YES" != "yes" ] && [ "$DRY_RUN" = "no" ]; then
    echo " *****************************************************************"
    echo " *  --purge will DELETE all user data in $BD_HOME_RESOLVED"
    echo " *  including bulk_downloader.db, downloads/, and sites_config.json."
    echo " *  This cannot be undone."
    echo " *****************************************************************"
    printf "  Type 'WIPE' to confirm full data wipe: "
    read -r WIPE_ANS
    if [ "$WIPE_ANS" != "WIPE" ]; then
        echo "  Aborted (you did not type WIPE)."
        exit 1
    fi
fi

if [ "$ASSUME_YES" != "yes" ] && [ "$DRY_RUN" = "no" ]; then
    printf "  Proceed with the actions above? [y/N]: "
    read -r ANS
    case "${ANS:-N}" in
        y|Y|yes|YES) ;;
        *) echo "  Aborted."; exit 1 ;;
    esac
fi

if [ "$HAS_SYSTEMCTL" = "yes" ]; then
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        run_ok sudo systemctl stop "$SERVICE_NAME"
    fi
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        run_ok sudo systemctl disable "$SERVICE_NAME"
    fi
    if systemctl is-active --quiet "$AI_SERVICE_NAME" 2>/dev/null; then
        run_ok sudo systemctl stop "$AI_SERVICE_NAME"
    fi
    if systemctl is-enabled --quiet "$AI_SERVICE_NAME" 2>/dev/null; then
        run_ok sudo systemctl disable "$AI_SERVICE_NAME"
    fi
fi

# Remove the unit file BEFORE daemon-reload so systemd notices the
# removal. Removing in the other order leaves a "loaded but missing"
# state until the next reload.
if [ -f "$UNIT_PATH" ]; then
    run sudo rm -f "$UNIT_PATH"
fi
if [ -f "$AI_UNIT_PATH" ]; then
    run sudo rm -f "$AI_UNIT_PATH"
fi

if [ "$HAS_SYSTEMCTL" = "yes" ] && [ "$SYSTEMD_CHANGE_PLANNED" = "yes" ] && [ "$DRY_RUN" = "no" ]; then
    # daemon-reload is cheap and safe even when nothing changed.
    run_ok sudo systemctl daemon-reload
    # reset-failed clears any leftover failed-unit state; otherwise
    # `systemctl status bulkdownloader` keeps showing the old failure
    # forever even after the unit is gone.
    run_ok sudo systemctl reset-failed "$SERVICE_NAME"
    run_ok sudo systemctl reset-failed "$AI_SERVICE_NAME"
fi

if [ -f "$APP_DIR/tools/deployed_version.txt" ]; then
    run rm -f "$APP_DIR/tools/deployed_version.txt"
fi

if [ "$PURGE" = "yes" ] && [ -d "$BD_HOME_RESOLVED" ]; then
    run rm -rf "$BD_HOME_RESOLVED"
fi

echo
echo " ================================================================"
echo "  Done."
if [ "$PURGE" != "yes" ]; then
    echo "  User data at $BD_HOME_RESOLVED was KEPT."
    echo "  Re-run with --purge to remove it as well."
fi
if [ -d "$DROPIN_DIR" ]; then
    echo "  Note: remote-teach drop-in still present at $DROPIN_DIR."
    echo "  Use uninstall_remote_teach.sh to remove the remote-teach stack."
fi
echo " ================================================================"
