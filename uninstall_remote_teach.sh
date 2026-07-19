#!/usr/bin/env bash
# uninstall_remote_teach.sh - reverse of install_remote_teach.sh.
#
# Removes:
#   - bd-xvfb, bd-openbox, bd-x11vnc, bd-novnc systemd services
#     (stops, disables, removes unit files)
#   - /etc/systemd/system/bulkdownloader.service.d/10-display.conf
#     (the drop-in that adds DISPLAY=:99 to the main service)
#   - $RUN_HOME/.bd_vnc_passwd (the VNC credential)
#
# With --purge:
#   - ALSO apt-get removes the VNC/display stack we installed:
#     xvfb x11vnc openbox novnc websockify.
#   - iproute2 is NEVER removed. install_remote_teach.sh ensures it is
#     present (it provides `ss`, used for the noVNC port check), but it
#     is a base networking package -- on a normal system it was already
#     installed and our `apt-get install` was a no-op. Removing it (and
#     the autoremove that follows) drags out netplan/ifupdown and leaves
#     the box with no networking. It stays. A NEVER_REMOVE guard below
#     also refuses to remove it (and other base packages) even if a
#     future edit adds one back to APT_PKGS.
#
# Does NOT remove:
#   - the bulkdownloader main service (use uninstall_service.sh)
#   - the bulkdownloader.service.d/ directory itself (left in place
#     because other drop-ins might live there; we only remove our
#     own file)
#
# Flags:
#   --dry-run     print what would be done; do nothing destructive
#   --yes         skip prompts; for CI
#   --purge       also apt remove the packages we installed
#   --keep-models (accepted for symmetry; no-op here)
#
# Restart behavior: when the drop-in is removed, the bulkdownloader
# service is restarted IF it is currently active, so it picks up the
# absence of DISPLAY=:99. (Playwright's default mode is headless so
# the app keeps working; only manual-teach needs the display.)
#
# Run:
#     chmod +x uninstall_remote_teach.sh && ./uninstall_remote_teach.sh
set -u
set -o pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

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

# Locate $RUN_USER's home the same way install_remote_teach.sh did.
# Don't refuse on RUN_USER=root here: we're tearing things down, not
# installing. If somebody did install as root previously (the
# installer would have refused, but never mind), the password file
# would be in /root and we should clean it up.
RUN_USER="${SUDO_USER:-$(whoami)}"
RUN_HOME="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6)"
[ -n "$RUN_HOME" ] || RUN_HOME="${HOME:-}"
VNC_PASSWD_FILE="${RUN_HOME}/.bd_vnc_passwd"

XVFB_UNIT="/etc/systemd/system/bd-xvfb.service"
OPENBOX_UNIT="/etc/systemd/system/bd-openbox.service"
X11VNC_UNIT="/etc/systemd/system/bd-x11vnc.service"
NOVNC_UNIT="/etc/systemd/system/bd-novnc.service"
SERVICE_DROPIN_DIR="/etc/systemd/system/bulkdownloader.service.d"
SERVICE_DROPIN="${SERVICE_DROPIN_DIR}/10-display.conf"

UNITS=(bd-xvfb bd-openbox bd-x11vnc bd-novnc)
UNIT_FILES=("$XVFB_UNIT" "$OPENBOX_UNIT" "$X11VNC_UNIT" "$NOVNC_UNIT")

# apt packages safe to remove on teardown -- the VNC/display stack only.
# NB: iproute2 is deliberately NOT here. install_remote_teach.sh ensures
# iproute2 is present (for `ss`), but it is a base networking package and
# removing it takes the system's networking down with it. The
# NEVER_REMOVE guard below is a second line of defense.
APT_PKGS=(xvfb x11vnc openbox novnc websockify)

# Base packages we must NEVER apt-remove on teardown, even if one is
# mistakenly added to APT_PKGS later. iproute2 (provides ip/ss) is the
# one that took networking down before; the rest are in the same
# "remove this and the box loses its network / fails to boot" class.
NEVER_REMOVE=(iproute2 netbase netplan.io ifupdown systemd systemd-sysv udev libc6)
is_never_remove() {
    local p="$1" n
    for n in "${NEVER_REMOVE[@]}"; do
        [ "$p" = "$n" ] && return 0
    done
    return 1
}

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
echo "  BulkDownloader - uninstall (remote-teach VNC stack)"
echo " ================================================================"
echo "  Units          : ${UNITS[*]}"
echo "  Drop-in        : $SERVICE_DROPIN"
echo "  VNC passwd file: $VNC_PASSWD_FILE"
echo "  Purge packages : $PURGE"
echo "  Dry run        : $DRY_RUN"
echo

HAS_SYSTEMCTL="no"
if command -v systemctl >/dev/null 2>&1; then HAS_SYSTEMCTL="yes"; fi

ACTIONS=()
if [ "$HAS_SYSTEMCTL" = "yes" ]; then
    for u in "${UNITS[@]}"; do
        if systemctl is-active --quiet "$u" 2>/dev/null; then
            ACTIONS+=("sudo systemctl stop $u")
        fi
        if systemctl is-enabled --quiet "$u" 2>/dev/null; then
            ACTIONS+=("sudo systemctl disable $u")
        fi
    done
fi
for uf in "${UNIT_FILES[@]}"; do
    [ -f "$uf" ] && ACTIONS+=("sudo rm -f $uf")
done
[ -f "$SERVICE_DROPIN" ]  && ACTIONS+=("sudo rm -f $SERVICE_DROPIN")
[ -f "$VNC_PASSWD_FILE" ] && ACTIONS+=("rm -f $VNC_PASSWD_FILE")

# Track whether we'll need to restart bulkdownloader to drop DISPLAY=:99.
RESTART_APP="no"
if [ -f "$SERVICE_DROPIN" ] && [ "$HAS_SYSTEMCTL" = "yes" ] \
        && systemctl is-active --quiet bulkdownloader 2>/dev/null; then
    RESTART_APP="yes"
    ACTIONS+=("sudo systemctl restart bulkdownloader  # drop DISPLAY=:99")
fi

if [ "$PURGE" = "yes" ] && command -v apt-get >/dev/null 2>&1; then
    PURGE_PRESENT=()
    for p in "${APT_PKGS[@]}"; do
        is_never_remove "$p" && continue
        if dpkg -s "$p" >/dev/null 2>&1; then
            PURGE_PRESENT+=("$p")
        fi
    done
    if [ "${#PURGE_PRESENT[@]}" -gt 0 ]; then
        ACTIONS+=("sudo apt-get remove -y ${PURGE_PRESENT[*]}")
    fi
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

# Stop and disable each unit. Iterate units explicitly rather than
# passing all four to one systemctl invocation: a missing unit in the
# list makes systemctl fail the whole call on some versions, and we
# want each to be independent and idempotent.
if [ "$HAS_SYSTEMCTL" = "yes" ]; then
    for u in "${UNITS[@]}"; do
        if systemctl is-active --quiet "$u" 2>/dev/null; then
            run_ok sudo systemctl stop "$u"
        fi
        if systemctl is-enabled --quiet "$u" 2>/dev/null; then
            run_ok sudo systemctl disable "$u"
        fi
    done
fi

for uf in "${UNIT_FILES[@]}"; do
    if [ -f "$uf" ]; then
        run sudo rm -f "$uf"
    fi
done

if [ -f "$SERVICE_DROPIN" ]; then
    run sudo rm -f "$SERVICE_DROPIN"
fi

# Try to clean up the drop-in dir, but only if it's now empty -- other
# drop-ins might live alongside ours. `rmdir` (not `rm -rf`) is the
# right tool: it fails on non-empty, succeeds on empty, leaves nothing
# to interpret.
if [ -d "$SERVICE_DROPIN_DIR" ] && [ "$DRY_RUN" = "no" ]; then
    sudo rmdir "$SERVICE_DROPIN_DIR" 2>/dev/null || true
fi

if [ -f "$VNC_PASSWD_FILE" ]; then
    run rm -f "$VNC_PASSWD_FILE"
fi

if [ "$HAS_SYSTEMCTL" = "yes" ] && [ "$DRY_RUN" = "no" ]; then
    run_ok sudo systemctl daemon-reload
    for u in "${UNITS[@]}"; do
        run_ok sudo systemctl reset-failed "$u"
    done
fi

if [ "$RESTART_APP" = "yes" ]; then
    # The drop-in is gone; restart so bulkdownloader.service drops
    # DISPLAY=:99 from its environment. Soft-fail: if restart fails
    # the rest of the uninstall is still valid.
    run_ok sudo systemctl restart bulkdownloader
fi

# Package removal. Done last so a failure there doesn't strand the
# unit files / drop-in: those are gone by now. Re-check with dpkg
# right before running, since apt-get remove on a not-installed
# package returns 100 in some apt versions.
if [ "$PURGE" = "yes" ] && command -v apt-get >/dev/null 2>&1; then
    PURGE_PRESENT=()
    for p in "${APT_PKGS[@]}"; do
        if is_never_remove "$p"; then
            echo "  -- refusing to apt-remove base package: $p (protects networking)"
            continue
        fi
        if dpkg -s "$p" >/dev/null 2>&1; then
            PURGE_PRESENT+=("$p")
        fi
    done
    if [ "${#PURGE_PRESENT[@]}" -gt 0 ]; then
        run_ok sudo apt-get remove -y "${PURGE_PRESENT[@]}"
        # autoremove cleans up dependencies pulled in only by the above.
        # The explicit removal set excludes base/networking packages
        # (APT_PKGS + the NEVER_REMOVE guard), so autoremove only reclaims
        # leaf deps of the VNC stack -- it will NOT touch iproute2
        # (important priority + reverse-depended). -y: already confirmed
        # at the top-level prompt; asking again would be noise.
        run_ok sudo apt-get autoremove -y
    fi
fi

echo
echo " ================================================================"
echo "  Done."
if [ "$PURGE" != "yes" ]; then
    echo "  apt packages (xvfb, x11vnc, openbox, novnc, websockify)"
    echo "  were KEPT. Re-run with --purge to remove them."
fi
echo "  iproute2 is never removed (base networking package)."
echo " ================================================================"
