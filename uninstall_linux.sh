#!/usr/bin/env bash
# uninstall_linux.sh - reverse of install_linux.sh.
#
# Removes:
#   - $INSTALL_DIR/venv/                       (Python virtualenv)
#   - $INSTALL_DIR/frontend/node_modules/      (D3 SPA deps)
#   - $INSTALL_DIR/frontend/dist/              (built SPA bundle)
#   - ~/.cache/ms-playwright/                  (Playwright browsers)
#
# Does NOT remove:
#   - the system Python that install_linux.sh tested for
#   - $BD_HOME / bulk_downloader.db / downloads/ -- those are user data;
#     uninstall_service.sh --purge handles them
#   - apt packages (python3, python3-venv, python3-pip) -- those may be
#     used by other things on the box; --purge here is a no-op for
#     symmetry with the other uninstallers
#
# Flags:
#   --dry-run        print what would be removed, do nothing
#   --yes            do not prompt; for CI
#   --purge          (accepted for symmetry; nothing to purge here)
#   --keep-models    (accepted for symmetry; no-op here)
#
# Run from the project folder (same place install_linux.sh ran from):
#     chmod +x uninstall_linux.sh && ./uninstall_linux.sh
set -u
set -o pipefail

DRY_RUN="no"
ASSUME_YES="no"
for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN="yes" ;;
        --yes|-y)      ASSUME_YES="yes" ;;
        --purge)       ;;  # no-op for this uninstaller; accepted for symmetry
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

cd "$(dirname "$(readlink -f "$0")")" || {
    echo "ERROR: could not cd to script directory" >&2
    exit 1
}
INSTALL_DIR="$(pwd)"

# run() prints the command and runs it (or just prints in dry-run mode).
# rm operations use -rf because everything we touch is expected to be
# absent on second invocation, and we want this script to be idempotent.
run() {
    echo "  + $*"
    if [ "$DRY_RUN" = "no" ]; then
        "$@"
    fi
}

# Resolve the original operator's home for the Playwright cache. Same
# logic as install_service.sh: when invoked via sudo, prefer SUDO_USER
# so we delete the cache the install actually wrote, not /root/.cache.
RUN_USER="${SUDO_USER:-$(whoami)}"
RUN_HOME="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6)"
if [ -z "$RUN_HOME" ]; then
    # Fall back to $HOME if passwd lookup fails (e.g. containerized
    # environments with no passwd entry). Better than silently writing
    # nothing.
    RUN_HOME="${HOME:-}"
fi
PLAYWRIGHT_CACHE="${RUN_HOME}/.cache/ms-playwright"

echo " ================================================================"
echo "  BulkDownloader - uninstall (Linux app deps)"
echo " ================================================================"
echo "  Install dir   : $INSTALL_DIR"
echo "  Playwright    : $PLAYWRIGHT_CACHE"
echo "  Dry run       : $DRY_RUN"
echo

# Build the action list up front so the confirm prompt is honest about
# what will actually happen on THIS run. Skip items that are already
# gone; the uninstaller is idempotent.
ACTIONS=()
[ -d "$INSTALL_DIR/venv" ]                   && ACTIONS+=("rm -rf $INSTALL_DIR/venv")
[ -d "$INSTALL_DIR/frontend/node_modules" ]  && ACTIONS+=("rm -rf $INSTALL_DIR/frontend/node_modules")
[ -d "$INSTALL_DIR/frontend/dist" ]          && ACTIONS+=("rm -rf $INSTALL_DIR/frontend/dist")
[ -d "$PLAYWRIGHT_CACHE" ]                   && ACTIONS+=("rm -rf $PLAYWRIGHT_CACHE")

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

# rm -rf is intentional: each path is either absent (handled by the
# existence check above, so we never log a phantom "removing") or a
# directory we own. We deliberately do NOT chmod -R first; if the
# operator made venv read-only on purpose, we want rm to fail loudly
# rather than silently power through.
[ -d "$INSTALL_DIR/venv" ]                  && run rm -rf "$INSTALL_DIR/venv"
[ -d "$INSTALL_DIR/frontend/node_modules" ] && run rm -rf "$INSTALL_DIR/frontend/node_modules"
[ -d "$INSTALL_DIR/frontend/dist" ]         && run rm -rf "$INSTALL_DIR/frontend/dist"
[ -d "$PLAYWRIGHT_CACHE" ]                  && run rm -rf "$PLAYWRIGHT_CACHE"

echo
echo " ================================================================"
echo "  Done."
echo "  Note: the system Python and apt packages (python3-venv etc.)"
echo "  were NOT removed -- they may be needed by other software."
echo " ================================================================"
