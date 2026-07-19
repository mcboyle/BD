#!/usr/bin/env bash
# uninstall_gpu_check.sh - reverse of gpu_check.sh.
#
# gpu_check.sh in its default mode is read-only -- it inspects PCI,
# runs nvidia-smi/rocminfo/etc, prints a recommendation, and exits.
# Nothing to undo for that.
#
# Its --install-drivers mode is the only path that touches the system.
# Today that path runs `sudo ubuntu-drivers autoinstall` (preferred) or
# `sudo apt-get install -y nvidia-driver-NNN-server` (fallback). To
# reverse those, this uninstaller offers --remove-nvidia, which runs:
#
#     sudo apt-get autoremove --purge 'nvidia-*' libnvidia-*
#
# That removes the proprietary driver, the kernel module, and the
# associated libraries. A reboot is required afterwards for the
# nouveau driver to take over.
#
# WHY THIS IS NOT THE DEFAULT:
#
# Removing the NVIDIA driver on a workstation is genuinely risky --
# the display server uses it. If the operator is sitting at the GUI
# when the driver gets ripped out, the next attempt to start X (or
# the current session, on reboot) can land in a black screen until
# nouveau picks up. On servers it's much safer, but the script can't
# tell which case it's in.
#
# So: --remove-nvidia is an opt-in flag, NOT covered by --purge in
# uninstall_all.sh, and the confirm prompt requires typing
# 'REMOVE-NVIDIA' rather than y/N.
#
# Flags:
#   --dry-run         print what would be done; do nothing destructive
#   --yes             skip ALL prompts (including the typed confirm).
#                     For CI on servers only. Default --remove-nvidia
#                     stance is "no" so --yes alone does nothing.
#   --remove-nvidia   actually remove the NVIDIA driver.
#   --purge           accepted for symmetry with the other uninstallers.
#                     Does NOT imply --remove-nvidia; you must pass that
#                     explicitly.
#   --keep-models     accepted for symmetry; no-op.
#
# Run:
#     chmod +x uninstall_gpu_check.sh && ./uninstall_gpu_check.sh
#     ./uninstall_gpu_check.sh --remove-nvidia
set -u
set -o pipefail

DRY_RUN="no"
ASSUME_YES="no"
REMOVE_NVIDIA="no"
for arg in "$@"; do
    case "$arg" in
        --dry-run)        DRY_RUN="yes" ;;
        --yes|-y)         ASSUME_YES="yes" ;;
        --remove-nvidia)  REMOVE_NVIDIA="yes" ;;
        --purge)          ;;  # see header: NOT implying --remove-nvidia
        --keep-models)    ;;  # no-op
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

run() {
    echo "  + $*"
    if [ "$DRY_RUN" = "no" ]; then
        "$@"
    fi
}

echo " ================================================================"
echo "  BulkDownloader - uninstall (gpu_check side effects)"
echo " ================================================================"

if [ "$REMOVE_NVIDIA" = "no" ]; then
    echo "  gpu_check.sh in detection mode is read-only -- nothing to remove."
    echo
    echo "  If you previously ran 'gpu_check.sh --install-drivers' and want"
    echo "  to remove the NVIDIA driver as well, re-run this script with:"
    echo "    $0 --remove-nvidia"
    echo
    echo "  (Removal is opt-in because pulling out an active display driver"
    echo "  can leave a workstation at a black screen until reboot completes.)"
    exit 0
fi

# --- --remove-nvidia path -----------------------------------------

if ! command -v apt-get >/dev/null 2>&1; then
    echo "  ERROR: --remove-nvidia only works on Debian/Ubuntu (apt-get not"
    echo "  found). On other distros, use your package manager to remove"
    echo "  the nvidia driver packages manually." >&2
    exit 2
fi
if ! command -v sudo >/dev/null 2>&1; then
    echo "  ERROR: sudo not found; driver removal needs root." >&2
    exit 2
fi

# List what we'll affect before asking. dpkg-query is the cheapest
# accurate way to see what's installed -- apt-cache search would list
# packages that exist in repos whether or not they're installed.
echo "  Currently installed NVIDIA-related packages:"
INSTALLED="$(dpkg-query -W -f '${Package}\n' 'nvidia-*' 'libnvidia-*' 2>/dev/null \
              | grep -v '^$' || true)"
if [ -z "$INSTALLED" ]; then
    echo "    (none)"
    echo
    echo "  Nothing to remove. Either the driver was never installed via"
    echo "  gpu_check.sh, or it's already been removed."
    exit 0
fi
echo "$INSTALLED" | sed 's/^/    /'
echo
echo " *****************************************************************"
echo " *  About to remove the NVIDIA driver and all related packages."
echo " *  After this completes, you MUST reboot. Until then:"
echo " *    - the running kernel module is still loaded (nvidia-smi works)"
echo " *    - new processes that try to use CUDA will fail"
echo " *  After reboot:"
echo " *    - the open-source 'nouveau' driver will take over the GPU"
echo " *    - 3D performance drops sharply"
echo " *    - Ollama loses GPU acceleration and falls back to CPU"
echo " *****************************************************************"
echo

if [ "$ASSUME_YES" != "yes" ] && [ "$DRY_RUN" = "no" ]; then
    printf "  Type 'REMOVE-NVIDIA' to confirm: "
    read -r CONFIRM
    if [ "$CONFIRM" != "REMOVE-NVIDIA" ]; then
        echo "  Aborted (you did not type REMOVE-NVIDIA)."
        exit 1
    fi
fi

# autoremove --purge cleans up config files too; without --purge,
# /etc/X11/xorg.conf.d/ leftovers can prevent nouveau from binding.
# We pass the patterns to autoremove, not just `apt-get remove`, so
# transitive-only deps (libnvidia-compute-XYZ, etc) also go.
run sudo apt-get update -qq
# We deliberately use a glob argument to apt-get; apt expands these
# patterns itself. Quote to keep the shell out of it.
run sudo apt-get autoremove --purge -y 'nvidia-*' 'libnvidia-*'

echo
echo " ================================================================"
echo "  NVIDIA driver removal completed."
echo "  REBOOT NOW: sudo reboot"
echo "  After reboot, nouveau will take over. nvidia-smi will no longer"
echo "  exist and 'gpu_check.sh' will report 'No NVIDIA hardware found'"
echo "  (a slight lie -- the card is still there, just unmanaged)."
echo " ================================================================"
