#!/usr/bin/env bash
# install_memory_limits.sh — PT16, optional.
#
# Add a systemd drop-in that caps the bulkdownloader service's memory
# (and optionally tasks). Without this, a leaking Playwright process or
# a Python reference cycle can grow unbounded until the kernel OOM
# killer picks something — possibly the wrong something. This drop-in
# makes the service the one that dies under memory pressure.
#
# Shipped as an OPTIONAL drop-in (not folded into install_service.sh's
# main unit) so:
#   - the operator picks the MemoryMax value based on the actual
#     working set of their host
#   - the limit can be tuned or removed without re-running
#     install_service.sh
#   - the install_service.sh unit stays focused on launch mechanics
#
# Coexists with the remote-teach drop-in (10-display.conf) via
# numeric prefix — drop-ins are merged alphabetically.
#
# Usage:
#     ./tools/install_memory_limits.sh                 # interactive
#     ./tools/install_memory_limits.sh 4G              # non-interactive
#     ./tools/install_memory_limits.sh --remove        # uninstall
#
# After install, restart the service:
#     sudo systemctl daemon-reload
#     sudo systemctl restart bulkdownloader
#
# To inspect what systemd applied:
#     systemctl cat bulkdownloader
#     systemctl show bulkdownloader -p MemoryMax
#
# To measure the typical working set before picking a value:
#     systemctl status bulkdownloader     # "Memory: ..."
#     systemd-cgtop -P                    # live per-service view
# Rule of thumb: pick ~2x the steady-state working set so a busy
# burst still fits but a leak hits the cap.

set -u

SERVICE_NAME="bulkdownloader"
DROPIN_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
DROPIN="${DROPIN_DIR}/20-memory.conf"

die() { echo "ERROR: $*" >&2; exit 1; }

if [ "$(uname -s)" != "Linux" ]; then
    die "this script is Linux-only (systemd drop-ins)"
fi

if ! command -v systemctl >/dev/null 2>&1; then
    die "systemctl not found — systemd is required"
fi

# --- uninstall path -----------------------------------------------
if [ "${1:-}" = "--remove" ] || [ "${1:-}" = "-r" ]; then
    if [ -f "$DROPIN" ]; then
        sudo rm -f "$DROPIN"
        echo "Removed $DROPIN"
        sudo systemctl daemon-reload
        echo "Restart the service: sudo systemctl restart ${SERVICE_NAME}"
    else
        echo "Nothing to remove ($DROPIN does not exist)."
    fi
    exit 0
fi

# --- install path -------------------------------------------------
MEMORY_MAX="${1:-}"

if [ -z "$MEMORY_MAX" ]; then
    # Interactive prompt — show current memory usage and a reasonable
    # default suggestion. systemctl show returns "MemoryCurrent=N" in
    # bytes; format it for the operator.
    CURRENT_BYTES="$(systemctl show "$SERVICE_NAME" -p MemoryCurrent \
        --value 2>/dev/null || true)"
    if [ -n "$CURRENT_BYTES" ] \
        && [ "$CURRENT_BYTES" != "[not set]" ] \
        && [ "$CURRENT_BYTES" -gt 0 ] 2>/dev/null; then
        CURRENT_HUMAN="$(numfmt --to=iec --suffix=B "$CURRENT_BYTES" \
            2>/dev/null || echo "${CURRENT_BYTES} bytes")"
        echo "Current ${SERVICE_NAME} memory: ${CURRENT_HUMAN}"
        # Suggest 4G as a generic safe default; operator can override.
        echo "Common choices: 2G, 4G, 6G, 8G"
        echo "(pick ~2x your steady-state working set)"
    else
        echo "Service not currently running, or memory not visible."
        echo "Common choices: 2G, 4G, 6G, 8G"
    fi
    echo
    printf "MemoryMax value (e.g. 4G), or empty to abort: "
    read -r MEMORY_MAX
    if [ -z "$MEMORY_MAX" ]; then
        die "no value entered — aborting"
    fi
fi

# Sanity-check the value. Accept K/M/G/T suffix or a bare integer
# (bytes). systemd parses "infinity" too but we won't accept that — a
# drop-in that says "no limit" is just a noisy no-op.
if ! echo "$MEMORY_MAX" \
    | grep -qE '^[0-9]+[KMGT]?$'; then
    die "MemoryMax value '$MEMORY_MAX' is not in the form NNNN[K|M|G|T]"
fi

echo " ================================================================"
echo "  BulkDownloader - systemd memory-limit drop-in"
echo " ================================================================"
echo "  MemoryMax     : ${MEMORY_MAX}"
echo "  Drop-in path  : ${DROPIN}"

echo
echo "  Writing ${DROPIN} (needs sudo)..."
sudo mkdir -p "$DROPIN_DIR"
sudo tee "$DROPIN" >/dev/null <<DROPIN
# Added by install_memory_limits.sh (PT16) -- caps memory for the
# bulkdownloader service so a leak hits this cap instead of the
# kernel OOM killer picking something unrelated.
#
# Removing this file (and running daemon-reload) reverts the change.
# Tune the value to ~2x your steady-state working set.
[Service]
MemoryMax=${MEMORY_MAX}
# MemoryHigh (soft throttle, kicks in just before MemoryMax) is
# intentionally NOT set: Playwright Chromium processes can spike
# legitimately during a heavy site and we want them to use the
# headroom rather than be throttled. Uncomment if a future workload
# benefits from soft pressure first.
# MemoryHigh=${MEMORY_MAX}
DROPIN

echo "  Reloading systemd..."
sudo systemctl daemon-reload

echo
echo " ================================================================"
echo "  Drop-in installed. Restart the service to apply:"
echo "    sudo systemctl restart ${SERVICE_NAME}"
echo
echo "  To verify:"
echo "    systemctl show ${SERVICE_NAME} -p MemoryMax"
echo "    systemctl cat  ${SERVICE_NAME}"
echo
echo "  To uninstall:"
echo "    $0 --remove"
echo " ================================================================"
