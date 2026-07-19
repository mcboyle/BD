#!/usr/bin/env bash
# uninstall_ai_ollama.sh - reverse of install_ai_ollama.sh.
#
# Removes:
#   - the ollama systemd service (stop + disable)
#   - /etc/systemd/system/ollama.service (unit file the official
#     installer drops)
#   - /usr/local/bin/ollama (the binary)
#   - the 'ollama' system user that the official installer creates
#   - /usr/share/ollama/.ollama (system-wide model store)
#   - ~ollama/.ollama if RUN_USER=ollama (unlikely but possible)
#
# With --keep-models:
#   - skips removal of /usr/share/ollama/.ollama and ~/.ollama; the
#     pulled qwen2.5vl:7b and qwen2.5:7b weights (30+ GB) are kept so a
#     later reinstall doesn't have to re-download them.
#
# With --purge:
#   - ALSO apt-get removes curl IF this script can verify it was
#     installed by install_ai_ollama.sh (we can't be sure curl wasn't
#     already on the box, so we ask before removing it -- never default
#     to ripping a generic tool out from under the operator).
#
# Does NOT remove:
#   - NVIDIA driver, even if gpu_check.sh --install-drivers installed
#     it. Driver removal is risky (the GUI session uses it) and the
#     driver may be used by things other than Ollama.
#
# Flags:
#   --dry-run       print what would be done; do nothing destructive
#   --yes           skip prompts; for CI
#   --keep-models   skip model store removal
#   --purge         also offer to apt remove curl
#
# Run:
#     chmod +x uninstall_ai_ollama.sh && ./uninstall_ai_ollama.sh
set -u
set -o pipefail

DRY_RUN="no"
ASSUME_YES="no"
KEEP_MODELS="no"
PURGE="no"
for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN="yes" ;;
        --yes|-y)      ASSUME_YES="yes" ;;
        --keep-models) KEEP_MODELS="yes" ;;
        --purge)       PURGE="yes" ;;
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

OLLAMA_UNIT="/etc/systemd/system/ollama.service"
# /usr/local/bin/ollama is where the official install.sh drops the
# binary on Linux. Resolve via command -v to handle distros that put
# it elsewhere (Arch packages it to /usr/bin, for instance).
OLLAMA_BIN_PATH=""
if command -v ollama >/dev/null 2>&1; then
    OLLAMA_BIN_PATH="$(command -v ollama)"
fi
# System-wide model store; the official installer puts models here
# when running as the ollama service user.
OLLAMA_SYS_HOME="/usr/share/ollama"

# If the operator ran the install as themselves (no systemd) the
# models may sit under their own $HOME instead.
RUN_USER="${SUDO_USER:-$(whoami)}"
RUN_HOME="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6)"
[ -n "$RUN_HOME" ] || RUN_HOME="${HOME:-}"
OLLAMA_USER_HOME="${RUN_HOME}/.ollama"

run() {
    echo "  + $*"
    if [ "$DRY_RUN" = "no" ]; then
        "$@"
    fi
}
run_ok() {
    echo "  + $*"
    if [ "$DRY_RUN" = "no" ]; then
        "$@" || true
    fi
}

echo " ================================================================"
echo "  BulkDownloader - uninstall (Ollama local AI)"
echo " ================================================================"
echo "  ollama binary  : ${OLLAMA_BIN_PATH:-(not on PATH)}"
echo "  systemd unit   : $OLLAMA_UNIT"
echo "  system store   : $OLLAMA_SYS_HOME"
echo "  user store     : $OLLAMA_USER_HOME"
echo "  Keep models    : $KEEP_MODELS"
echo "  Purge curl     : $PURGE"
echo "  Dry run        : $DRY_RUN"
echo

HAS_SYSTEMCTL="no"
if command -v systemctl >/dev/null 2>&1; then HAS_SYSTEMCTL="yes"; fi

ACTIONS=()
if [ "$HAS_SYSTEMCTL" = "yes" ]; then
    if systemctl is-active --quiet ollama 2>/dev/null; then
        ACTIONS+=("sudo systemctl stop ollama")
    fi
    if systemctl is-enabled --quiet ollama 2>/dev/null; then
        ACTIONS+=("sudo systemctl disable ollama")
    fi
fi
[ -f "$OLLAMA_UNIT" ]      && ACTIONS+=("sudo rm -f $OLLAMA_UNIT")
[ -n "$OLLAMA_BIN_PATH" ]  && ACTIONS+=("sudo rm -f $OLLAMA_BIN_PATH")
# The official installer creates an `ollama` system user. Remove it if
# present, but only if the model store is also going away -- otherwise
# the user owns files we're not deleting and userdel will refuse or
# orphan them.
USER_DEL_OK="no"
if getent passwd ollama >/dev/null 2>&1; then
    if [ "$KEEP_MODELS" = "no" ]; then
        ACTIONS+=("sudo userdel ollama")
        USER_DEL_OK="yes"
    else
        echo "  (note: 'ollama' system user kept; --keep-models is set"
        echo "   and the user owns the model store at $OLLAMA_SYS_HOME)"
    fi
fi
if [ "$KEEP_MODELS" = "no" ]; then
    [ -d "$OLLAMA_SYS_HOME" ]  && ACTIONS+=("sudo rm -rf $OLLAMA_SYS_HOME")
    [ -d "$OLLAMA_USER_HOME" ] && ACTIONS+=("rm -rf $OLLAMA_USER_HOME")
fi

if [ "${#ACTIONS[@]}" -eq 0 ]; then
    echo "  Nothing to remove. (Already uninstalled, or never installed here.)"
    exit 0
fi

echo "  Will perform:"
for a in "${ACTIONS[@]}"; do echo "    $a"; done
echo

if [ "$ASSUME_YES" != "yes" ] && [ "$DRY_RUN" = "no" ]; then
    printf "  Proceed? [y/N]: "
    read -r ANS
    case "${ANS:-N}" in
        y|Y|yes|YES) ;;
        *) echo "  Aborted."; exit 1 ;;
    esac
fi

# Stop/disable before removing the unit -- removing the unit while
# the process is active leaves an orphan ollama process serving on
# :11434 that systemd can no longer manage.
if [ "$HAS_SYSTEMCTL" = "yes" ]; then
    if systemctl is-active --quiet ollama 2>/dev/null; then
        run_ok sudo systemctl stop ollama
    fi
    if systemctl is-enabled --quiet ollama 2>/dev/null; then
        run_ok sudo systemctl disable ollama
    fi
fi

if [ -f "$OLLAMA_UNIT" ]; then
    run sudo rm -f "$OLLAMA_UNIT"
fi

if [ -n "$OLLAMA_BIN_PATH" ] && [ -f "$OLLAMA_BIN_PATH" ]; then
    run sudo rm -f "$OLLAMA_BIN_PATH"
fi

# Remove model store BEFORE userdel: if the user owns the model dir,
# userdel will refuse (or, worse, succeed and leave orphan-uid files).
# Order: model dirs -> user.
if [ "$KEEP_MODELS" = "no" ]; then
    if [ -d "$OLLAMA_SYS_HOME" ]; then
        run sudo rm -rf "$OLLAMA_SYS_HOME"
    fi
    if [ -d "$OLLAMA_USER_HOME" ]; then
        run rm -rf "$OLLAMA_USER_HOME"
    fi
fi

if [ "$USER_DEL_OK" = "yes" ] && getent passwd ollama >/dev/null 2>&1; then
    # userdel returns non-zero if the user is still logged in (the
    # service process), but we already stopped systemd above. Use
    # run_ok rather than die: a leftover ollama user is annoying, not
    # catastrophic.
    run_ok sudo userdel ollama
fi

if [ "$HAS_SYSTEMCTL" = "yes" ] && [ "$DRY_RUN" = "no" ]; then
    run_ok sudo systemctl daemon-reload
    run_ok sudo systemctl reset-failed ollama
fi

# --purge handling: offer to remove curl. We do NOT auto-remove because
# many systems ship curl in the base image (Ubuntu Server, for one)
# and removing it can break unrelated tooling. Surface the action and
# let the operator decide.
if [ "$PURGE" = "yes" ] && command -v curl >/dev/null 2>&1 \
        && command -v apt-get >/dev/null 2>&1; then
    echo
    echo "  --purge: remove curl?"
    echo "  Note: curl is often part of the base system. Many tools"
    echo "  depend on it (cloud-init, ansible, the snap installer, ...)."
    if [ "$ASSUME_YES" = "yes" ]; then
        echo "  --yes is set, skipping curl removal anyway (too dangerous"
        echo "  to default to in CI; remove manually with 'apt remove curl')."
    elif [ "$DRY_RUN" = "yes" ]; then
        echo "  + sudo apt-get remove -y curl  (skipped: dry run)"
    else
        printf "  Remove curl now? [y/N]: "
        read -r CURL_ANS
        case "${CURL_ANS:-N}" in
            y|Y|yes|YES) run_ok sudo apt-get remove -y curl ;;
            *)           echo "  Skipped." ;;
        esac
    fi
fi

echo
echo " ================================================================"
echo "  Done."
if [ "$KEEP_MODELS" = "yes" ]; then
    echo "  Models kept at:"
    [ -d "$OLLAMA_SYS_HOME" ]  && echo "    $OLLAMA_SYS_HOME"
    [ -d "$OLLAMA_USER_HOME" ] && echo "    $OLLAMA_USER_HOME"
    echo "  Run again WITHOUT --keep-models to remove them."
fi
echo
echo "  NOT removed: NVIDIA driver (if installed via gpu_check.sh"
echo "  --install-drivers). Driver removal is risky and the driver may"
echo "  be used by other software. Remove manually with:"
echo "    sudo apt-get autoremove --purge 'nvidia-*'"
echo "    sudo reboot"
echo " ================================================================"
