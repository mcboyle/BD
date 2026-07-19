#!/usr/bin/env bash
# install_remote_teach.sh - add a virtual display + browser-based VNC
# so manual-teach (which needs a headed Chromium) is usable from any
# device on the LAN, including phones.
#
# What this installs:
#   - Xvfb on :99 (virtual framebuffer, 1920x1080)
#   - openbox as a minimal window manager on :99
#   - x11vnc exporting :99 with a password
#   - noVNC + websockify serving the VNC stream as a web page on :6080
#   - a systemd drop-in for bulkdownloader.service that sets
#     DISPLAY=:99 but does NOT make bulkdownloader fail if Xvfb is
#     down -- Playwright still works headless for normal downloads;
#     only manual-teach needs the display.
#
# Auth model: VNC password only. The endpoint listens on the LAN
# (0.0.0.0:6080) and trusts anyone with the password -- same trust
# shape as BulkDownloader's port 5555. Generate a strong password
# at install time; rotate with `--reset-password`.
#
# Usage:
#   ./install_remote_teach.sh                  # install/idempotent
#   ./install_remote_teach.sh --reset-password # rotate VNC password
#   ./install_remote_teach.sh --status         # show what's running
#
# noVNC host (the host part of the BD_NOVNC_URL cockpit embed URL):
#   By default this is auto-detected from `hostname -I` (first IP).
#   Override it when the first listed IP is not the LAN-reachable one,
#   or to pin a stable address. Whatever you set is written verbatim
#   into the BD_NOVNC_URL service environment (host part only -- the
#   :PORT and ?resize=scale&autoconnect=true query are still appended):
#     BD_NOVNC_IP=10.0.70.20 ./install_remote_teach.sh   # via env var
#     ./install_remote_teach.sh --novnc-ip=10.0.70.20    # via flag ('=')
#   Accepts an IPv4, a hostname, or a bracketed IPv6 ([2001:db8::1]).
#   Set the HOST only -- the port comes from NOVNC_PORT, not from here.
#
# Uninstall:
#   ./uninstall_remote_teach.sh                # ships alongside this
#
# Hits: phone browser -> http://<stash-ip>:6080/vnc.html
#       (no HTTPS -- LAN only; do NOT expose 6080 to the internet)

set -u
set -o pipefail

# -- helpers (defined early so they're available to arg parsing + sanity) --
die() { echo "ERROR: $*" >&2; exit 1; }

require_root_via_sudo() {
    if ! sudo -n true 2>/dev/null; then
        echo "  (you will be prompted for your sudo password)"
    fi
}

# Return 0 if a listening socket is bound to the given port; 1 otherwise.
# The previous inline check used `grep ":$port\b"` which (a) relies on
# GNU grep's `\b` word-boundary and (b) false-positives -- ":6080\b"
# matches ":16080" too. ss's structured filter avoids both pitfalls.
# Returns 2 (distinct from in-use=0 / free=1) if ss is unavailable so
# callers can distinguish "definitely free" from "couldn't probe".
port_in_use() {
    local port="$1"
    command -v ss >/dev/null 2>&1 || return 2
    [ -n "$(ss -ltnH "sport = :${port}" 2>/dev/null)" ]
}

# -- arg parsing ---------------------------------------------------
MODE="install"
# Operator override for the noVNC host (the host part of BD_NOVNC_URL).
# Empty => auto-detect from `hostname -I`. Picked up here from the
# environment (so `BD_NOVNC_IP=... ./install_remote_teach.sh` works) and
# may also be set by the --novnc-ip=<ip> flag below. Validated once,
# after the loop, in the main shell.
BD_NOVNC_IP="${BD_NOVNC_IP:-}"
for arg in "$@"; do
    case "$arg" in
        --reset-password) MODE="reset" ;;
        --status)         MODE="status" ;;
        --novnc-ip=*)     BD_NOVNC_IP="${arg#*=}" ;;
        --novnc-ip)
            die "use --novnc-ip=<ip> (with '='), e.g. --novnc-ip=10.0.70.20"
            ;;
        -h|--help)
            # Print the header comment block. The earlier `sed -n '2,30p'`
            # truncated the "Hits:" footer; this awk variant walks until
            # the first non-comment line, so the help output stays in
            # sync if the header grows or shrinks.
            awk 'NR==1 {next}
                 /^[^#]/ && !/^$/ {exit}
                 /^#/ {sub(/^# ?/, ""); print}' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown arg: $arg" >&2
            exit 2
            ;;
    esac
done

# -- noVNC host override: validate once, in the MAIN shell ---------
# A bad value must abort HERE, not deep inside a $(resolve_novnc_ip)
# subshell where a die/exit would only kill the subshell and leave the
# script running with an empty/garbled host. Trim surrounding
# whitespace, then allow only characters legal in a URL host + a systemd
# Environment= value: letters, digits, '.', '-', '_', and ':' '[' ']'
# for a bracketed IPv6. This rejects spaces, quotes, slashes, and a
# pasted "http://..." URL. A ':' outside brackets (i.e. an IPv4 with a
# port, or unbracketed IPv6) is rejected with a pointed hint.
if [ -n "$BD_NOVNC_IP" ]; then
    BD_NOVNC_IP="$(printf '%s' "$BD_NOVNC_IP" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
fi
if [ -n "$BD_NOVNC_IP" ]; then
    case "$BD_NOVNC_IP" in
        *[!]A-Za-z0-9._:[-]*)
            die "invalid noVNC host '${BD_NOVNC_IP}': use a bare IPv4," \
                "a hostname, or a bracketed IPv6 -- not a URL or a value" \
                "with spaces, quotes, or slashes." ;;
    esac
    case "$BD_NOVNC_IP" in
        *:*)
            case "$BD_NOVNC_IP" in
                \[*\]) : ;;   # bracketed IPv6, e.g. [2001:db8::1] -- OK
                *) die "noVNC host '${BD_NOVNC_IP}' contains ':' -- set the" \
                       "HOST only (the :PORT is added from NOVNC_PORT). For" \
                       "IPv6 use brackets, e.g. [2001:db8::1]." ;;
            esac ;;
    esac
fi

# Resolve the noVNC host: the operator override (already validated
# above) wins; else the first IP from `hostname -I`; else loopback.
# Single source of truth for --status, the baked BD_NOVNC_URL, and the
# final install summary, so all three always agree.
resolve_novnc_ip() {
    if [ -n "$BD_NOVNC_IP" ]; then
        printf '%s\n' "$BD_NOVNC_IP"
        return 0
    fi
    local ip
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [ -n "$ip" ] || ip="127.0.0.1"
    printf '%s\n' "$ip"
}

# >>> novnc_shadow_winner (v3.66.470 INSTALL-WARN-SHADOW; sliced by the unit test)
# systemd applies service drop-ins in lexical filename order, and for a repeated
# Environment=KEY the LAST file to set it WINS. This installer bakes BD_NOVNC_URL
# into 10-display.conf -- so any *.conf that sorts AFTER it and also sets
# BD_NOVNC_URL silently shadows our value (e.g. an operator `systemctl edit`
# override.conf: 'o' > '1'). Echo the WINNING shadowing filename (the lexically
# greatest such file) so the caller can warn; echo nothing when none shadows.
#   $1 = drop-in dir   $2 = this installer's own drop-in basename (10-display.conf)
novnc_shadow_winner() {
    local dir="$1" self="$2" f base winner=""
    [ -d "$dir" ] || return 0
    for f in "$dir"/*.conf; do
        [ -e "$f" ] || continue          # no-match glob guard
        base="$(basename "$f")"
        [ "$base" = "$self" ] && continue
        # only files that sort AFTER ours can win the Environment race
        [ "$base" \> "$self" ] || continue
        # an active (non-comment) BD_NOVNC_URL assignment?
        if grep -Eq '^[[:space:]]*Environment="?BD_NOVNC_URL=' "$f" 2>/dev/null; then
            winner="$base"               # keep the lexically-greatest (loop is sorted)
        fi
    done
    [ -n "$winner" ] && printf '%s\n' "$winner"
    return 0
}
# <<< novnc_shadow_winner

# -- basics --------------------------------------------------------
if [ "$(uname -s)" != "Linux" ]; then
    echo "ERROR: this script is Linux-only (uses systemd, apt, Xvfb)." >&2
    exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
    echo "ERROR: sudo is required." >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "ERROR: systemd is required (no systemctl found)." >&2
    exit 1
fi

APP_DIR="$(dirname "$(readlink -f "$0")")"

# If invoked via `sudo`, prefer the original operator account: the
# desktop services we install (x11vnc, openbox) need to run as the
# normal user so DBus, audio, theme, and the BulkDownloader Chromium
# session all use that user's environment. SUDO_USER carries that
# through. Fall back to whoami for the non-sudo case.
RUN_USER="${SUDO_USER:-$(whoami)}"
# Status mode is read-only (systemctl is-active + a file test) so it
# can run as anyone, including bare root in a recovery shell. Install
# and reset both write files into RUN_HOME and write systemd User= so
# they cannot proceed with RUN_USER=root.
if [ "$RUN_USER" = "root" ] && [ "$MODE" != "status" ]; then
    die "refusing to install with RUN_USER=root. Re-run as your normal"\
        " user account (with sudo, if needed)."
fi

# RUN_USER goes into four systemd unit files as User=. Linux permits
# some unusual usernames (e.g. with '$' at end, legal per NAME_REGEX,
# or weirder via `useradd --badname`); refuse them rather than embed
# into a unit that systemd will then reject or misparse. Like the
# root check above, only enforce on modes that actually write units.
if [ "$MODE" != "status" ]; then
    case "$RUN_USER" in
        *[' %$;'"'\\\""]*)
            die "RUN_USER contains a character systemd treats specially"\
                "('$RUN_USER'). Allowed in unit User= fields: letters,"\
                "digits, -, _, ."
            ;;
    esac
fi

# Resolve $RUN_USER's home from passwd, not '/home/$RUN_USER' -- the
# old path-guess broke on systems with custom $HOME layouts (e.g.
# /export/users/alice) and on `sudo` invocations that previously set
# RUN_USER=root and would have tried /home/root.
RUN_HOME="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6)"
if [ -z "$RUN_HOME" ]; then
    die "user '$RUN_USER' not found in passwd database. If this came"\
        "from SUDO_USER it may refer to a user that exists in the"\
        "calling shell's environment but not on this host."
fi
if [ ! -d "$RUN_HOME" ]; then
    die "user '$RUN_USER' exists but their home '$RUN_HOME' is not a"\
        "directory. Create it or fix the passwd entry, then re-run."
fi

# RUN_HOME ends up in a systemd unit's ExecStart= via VNC_PASSWD_FILE.
# Same constraint as install_service.sh's APP_DIR guard: characters
# the unit-file parser treats specially will corrupt the value.
# Skipped in status mode -- it doesn't write any unit files.
if [ "$MODE" != "status" ]; then
    case "$RUN_HOME" in
        *[' %$;'"'\\\""]*)
            die "user '$RUN_USER' has a home path containing a character"\
                "systemd treats specially ('$RUN_HOME')."\
                "Allowed: letters, digits, /, -, _, ., +."
            ;;
    esac
fi

DISPLAY_NUM=":99"
DISPLAY_GEOM="1920x1080x24"
VNC_PORT="5900"           # x11vnc default; only bound to 127.0.0.1
NOVNC_PORT="6080"         # the browser-facing port (LAN-bound)
VNC_PASSWD_FILE="${RUN_HOME}/.bd_vnc_passwd"
SERVICE_DROPIN_DIR="/etc/systemd/system/bulkdownloader.service.d"
SERVICE_DROPIN="${SERVICE_DROPIN_DIR}/10-display.conf"

XVFB_UNIT="/etc/systemd/system/bd-xvfb.service"
OPENBOX_UNIT="/etc/systemd/system/bd-openbox.service"
X11VNC_UNIT="/etc/systemd/system/bd-x11vnc.service"
NOVNC_UNIT="/etc/systemd/system/bd-novnc.service"

# -- helpers -------------------------------------------------------
# (die() and require_root_via_sudo() are defined at the top of the
# script, before arg parsing -- the RUN_USER guard depends on die.)

set_or_keep_vnc_password() {
    # Generate a random password if the file doesn't exist; reset it
    # if MODE=reset. The file is stored as x11vnc's binary format via
    # `x11vnc -storepasswd`.
    if [ -f "$VNC_PASSWD_FILE" ] && [ "$MODE" != "reset" ]; then
        echo "  VNC password already set at $VNC_PASSWD_FILE (use --reset-password to rotate)"
        return 0
    fi
    # Generate a 16-char alphanumeric password -- easy enough to type
    # on a phone, strong enough for LAN. Reading from /dev/urandom
    # directly into tr would normally pipe to `head -c 16`, but that
    # closes the read side after 16 bytes and sends SIGPIPE to tr,
    # which trips pipefail with exit 141. Pulling 256 bytes of raw
    # entropy first avoids the pipe; with P(byte in [A-Za-z0-9]) =
    # 62/256, expected yield is ~62 alphanumerics and P(fewer than
    # 16) is ~4.5e-15 -- effectively never.
    local pw raw
    raw="$(head -c 256 /dev/urandom 2>/dev/null | tr -dc 'A-Za-z0-9')"
    pw="${raw:0:16}"
    [ "${#pw}" -eq 16 ] || die "VNC password generation failed (got ${#pw} chars; expected 16)"
    x11vnc -storepasswd "$pw" "$VNC_PASSWD_FILE" >/dev/null 2>&1 \
        || die "x11vnc -storepasswd failed"
    # x11vnc -storepasswd writes 0600 by default (umask 077). chmod 600
    # is belt-and-suspenders. Make it fatal: the file contains an auth
    # credential gating access to the remote desktop session (which can
    # see the Chromium window with the operator's auth cookies). Silent
    # fallback to whatever x11vnc chose would be wrong here.
    chmod 600 "$VNC_PASSWD_FILE" \
        || die "chmod 600 $VNC_PASSWD_FILE failed; refusing to leave a"\
               "potentially world-readable VNC password on disk."
    # Verify post-chmod. The most likely failure mode is a filesystem
    # that doesn't honor unix perms (rare; e.g. some mounted FUSE FS).
    actual_perms="$(stat -c '%a' "$VNC_PASSWD_FILE" 2>/dev/null || echo unknown)"
    if [ "$actual_perms" != "600" ]; then
        die "VNC password file perms are $actual_perms after chmod 600;"\
            "filesystem may not support unix perms. Move RUN_HOME to a"\
            "POSIX-compliant filesystem and re-run."
    fi
    echo
    echo " ================================================================"
    echo "  VNC password (you will need this on your phone/laptop):"
    echo
    echo "    $pw"
    echo
    echo "  Stored at: $VNC_PASSWD_FILE"
    echo "  Rotate later with: $0 --reset-password"
    echo " ================================================================"
    echo
}

# -- status mode ----------------------------------------------------
if [ "$MODE" = "status" ]; then
    echo " ================================================================"
    echo "  Remote-teach status"
    echo " ================================================================"
    for u in bd-xvfb bd-openbox bd-x11vnc bd-novnc bulkdownloader; do
        state="$(systemctl is-active "$u" 2>/dev/null || echo unknown)"
        enabled="$(systemctl is-enabled "$u" 2>/dev/null || echo unknown)"
        printf "  %-20s active=%-10s enabled=%s\n" "$u" "$state" "$enabled"
    done
    echo
    port_in_use "$NOVNC_PORT"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        # Operator override (BD_NOVNC_IP / --novnc-ip) wins; else first
        # `hostname -I` IP; else loopback (only reachable on this host).
        ip="$(resolve_novnc_ip)"
        echo "  noVNC reachable at: http://${ip}:${NOVNC_PORT}/vnc.html"
    elif [ "$rc" -eq 1 ]; then
        echo "  noVNC is NOT listening on :${NOVNC_PORT}"
    else
        echo "  noVNC port check skipped (ss not installed; install iproute2)"
    fi
    if [ -f "$VNC_PASSWD_FILE" ]; then
        echo "  VNC password file present: $VNC_PASSWD_FILE"
    else
        echo "  VNC password file MISSING -- re-run installer."
    fi
    exit 0
fi

# -- reset mode just rotates the password --------------------------
if [ "$MODE" = "reset" ]; then
    require_root_via_sudo
    command -v x11vnc >/dev/null 2>&1 || die "x11vnc not installed; run installer first."
    set_or_keep_vnc_password
    # Restart x11vnc so the new password is picked up immediately.
    # Without this, the old x11vnc process keeps the old -rfbauth file
    # open until it next restarts on its own. Surface failures rather
    # than silently telling the operator "Done" when the rotation is
    # not yet active.
    echo "  Restarting x11vnc to pick up the new password..."
    if ! systemctl is-active --quiet bd-x11vnc 2>/dev/null; then
        echo "  WARNING: bd-x11vnc service is not active. The new password"
        echo "  is stored at $VNC_PASSWD_FILE but won't take effect until"
        echo "  the service is started: sudo systemctl start bd-x11vnc"
    elif sudo systemctl restart bd-x11vnc 2>/tmp/bd_x11vnc_restart.err; then
        echo "  Done. New password is active."
    else
        sed 's/^/    /' /tmp/bd_x11vnc_restart.err
        echo "  ERROR: bd-x11vnc restart failed (above). The new password"
        echo "  is stored at $VNC_PASSWD_FILE but is NOT yet active."
        echo "  Investigate with: journalctl -u bd-x11vnc -n 30"
        exit 1
    fi
    exit 0
fi

# -- install mode --------------------------------------------------
echo " ================================================================"
echo "  BulkDownloader - install remote-teach stack"
echo " ================================================================"
echo "  App directory : $APP_DIR"
echo "  Run as user   : $RUN_USER"
echo "  Display       : $DISPLAY_NUM ($DISPLAY_GEOM)"
echo "  noVNC port    : $NOVNC_PORT (LAN-bound)"
echo

require_root_via_sudo

echo "  Installing packages..."
sudo apt-get update -qq \
    || die "apt-get update failed"
# Note: deliberately NOT using --no-install-recommends here. websockify
# recommends python3-numpy, and without it falls back to a pure-Python
# byte-shuffling loop that's noticeably slower for the noVNC streams
# this stack exists to serve. The extra ~5 MB is worth it.
#
# iproute2 is technically a base package on Ubuntu, but minimal Docker
# images and stripped-down server images sometimes ship without it,
# which silently breaks the port_in_use helper below. Listed explicitly
# so apt confirms it's present.
# Suppress stdout (the progress chatter) but NOT stderr -- when apt fails,
# the operator needs to see why (held packages, locked dpkg, missing repo,
# etc) before the die() kicks in.
sudo apt-get install -y \
    xvfb x11vnc openbox novnc websockify iproute2 \
    >/dev/null \
    || die "apt-get install failed (see stderr above for details)"
echo "  Packages installed."

# After the install, ss really must be there -- otherwise port_in_use
# would silently report "not in use" for any port and we'd install
# units that fail to start because the port is actually taken.
command -v ss >/dev/null 2>&1 \
    || die "ss not on PATH after iproute2 install -- bailing"

# Verify the binaries we depend on actually landed, and resolve their
# real paths once. Using `command -v` instead of hard-coded /usr/bin/...
# means the unit files keep working if a future apt repo split moves a
# binary, or if the operator has a local override on PATH.
#
# Assumption: each resolved path is readable+executable by RUN_USER.
# This holds for apt-installed binaries in /usr/bin/ (world-readable),
# but would fail if the installer's PATH points command -v at a binary
# under the installer's $HOME that RUN_USER can't access. In practice
# the apt install above places the binaries in /usr/bin/, which takes
# precedence over $HOME/bin only if /usr/bin appears earlier in PATH
# -- which is the Ubuntu default. If you've prepended a personal bin
# dir to PATH and shadowed any of these, you'll see "Permission denied"
# at service start; resolve by `unset PATH override` and re-running.
XVFB_BIN="$(command -v Xvfb)"        || die "Xvfb not on PATH after install"
X11VNC_BIN="$(command -v x11vnc)"    || die "x11vnc not on PATH after install"
OPENBOX_BIN="$(command -v openbox)"  || die "openbox not on PATH after install"
WEBSOCKIFY_BIN="$(command -v websockify)" \
    || die "websockify not on PATH after install"
# noVNC ships its static files under /usr/share/novnc on Debian/Ubuntu;
# websockify --web=<dir> serves them. Sanity check.
if [ ! -d /usr/share/novnc ]; then
    die "/usr/share/novnc not found -- novnc package layout changed?"
fi

# -- VNC password --------------------------------------------------
set_or_keep_vnc_password

# -- port-conflict check -------------------------------------------
# By now ss is verified present (the iproute2 sanity check above), so
# the port_in_use 'ss missing' rc=2 path can't fire here.
port_in_use "$NOVNC_PORT"
rc=$?
if [ "$rc" -eq 0 ]; then
    if systemctl is-active --quiet bd-novnc; then
        echo "  Port $NOVNC_PORT is in use by our own bd-novnc service -- fine."
    else
        die "Port $NOVNC_PORT is already in use by something else. Stop it or edit this script."
    fi
elif [ "$rc" -eq 2 ]; then
    # Shouldn't happen given the sanity check above; bail loudly if it does.
    die "port_in_use returned 2 (ss missing) post-install -- environment is broken."
fi

# -- unit files ----------------------------------------------------
echo "  Writing systemd units (needs sudo)..."

# Helper: pipe heredoc to a privileged file via sudo tee, and bail on
# failure. The previous inline `sudo tee | >/dev/null <<UNIT ... UNIT`
# discarded failures (sudo refusal or non-writable target), so daemon-
# reload+enable would proceed vacuously and the script would still
# claim success while the units were missing or stale.
write_unit() {
    local path="$1"
    if ! sudo tee "$path" >/dev/null; then
        die "failed to write $path (sudo refused or target not writable?)"
    fi
}

write_unit "$XVFB_UNIT" <<UNIT
[Unit]
Description=BulkDownloader virtual display (Xvfb on ${DISPLAY_NUM})
After=network.target

[Service]
Type=simple
User=${RUN_USER}
ExecStart=${XVFB_BIN} ${DISPLAY_NUM} -screen 0 ${DISPLAY_GEOM} -nolisten tcp -ac
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

write_unit "$OPENBOX_UNIT" <<UNIT
[Unit]
Description=BulkDownloader window manager (openbox on ${DISPLAY_NUM})
After=bd-xvfb.service
Requires=bd-xvfb.service

[Service]
Type=simple
User=${RUN_USER}
Environment=DISPLAY=${DISPLAY_NUM}
ExecStart=${OPENBOX_BIN}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

# x11vnc -- bind to 127.0.0.1 so only the local websockify can reach
# it; noVNC websockify is what's exposed on the LAN. -forever keeps
# x11vnc running across client disconnects.
write_unit "$X11VNC_UNIT" <<UNIT
[Unit]
Description=BulkDownloader VNC export of ${DISPLAY_NUM}
After=bd-xvfb.service
Requires=bd-xvfb.service

[Service]
Type=simple
User=${RUN_USER}
Environment=DISPLAY=${DISPLAY_NUM}
ExecStart=${X11VNC_BIN} -display ${DISPLAY_NUM} -rfbauth ${VNC_PASSWD_FILE} -localhost -forever -shared -rfbport ${VNC_PORT} -quiet
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

# noVNC (websockify with the noVNC web dir). Listens on all
# interfaces by default; we want LAN-reachable, so 0.0.0.0 is right.
write_unit "$NOVNC_UNIT" <<UNIT
[Unit]
Description=BulkDownloader noVNC web frontend (port ${NOVNC_PORT})
After=bd-x11vnc.service
Requires=bd-x11vnc.service

[Service]
Type=simple
User=${RUN_USER}
ExecStart=${WEBSOCKIFY_BIN} --web=/usr/share/novnc ${NOVNC_PORT} 127.0.0.1:${VNC_PORT}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

# bulkdownloader drop-in: add DISPLAY=:99 so headed Chromium (used
# only by manual-teach) finds Xvfb. Soft dependency via Wants= so a
# down Xvfb doesn't keep BulkDownloader from starting -- Playwright's
# default mode is headless, and the rest of the app doesn't need
# the display.
sudo mkdir -p "$SERVICE_DROPIN_DIR" \
    || die "could not create $SERVICE_DROPIN_DIR (sudo refused?)"

# noVNC embed URL for the BulkDownloader cockpit /capture page. The
# cockpit's /cockpit/api/novnc returns BD_NOVNC_URL verbatim -- it does
# NOT inject query params -- so the LAN IP + resize must be baked here,
# or the embed shows "Set BD_NOVNC_URL..." and stays blank. The host is
# the operator override (BD_NOVNC_IP / --novnc-ip) if set, else the
# first LAN IP (hostname -I; the cockpit may be opened from another
# device, so 127.0.0.1 would not be reachable); fall back to 127.0.0.1
# (host-only). resize=scale fits the ${DISPLAY_GEOM} framebuffer into
# the cockpit embed with NO server-side RANDR dependency (use
# resize=remote only if x11vnc+Xvfb RANDR are wired). The VNC password
# is deliberately NOT placed in the URL (it would leak via the cockpit
# response + browser history); the operator enters it once. An operator
# `systemctl edit` override.conf still wins -- it loads after this file.
NOVNC_IP="$(resolve_novnc_ip)"
NOVNC_URL="http://${NOVNC_IP}:${NOVNC_PORT}/vnc.html?resize=scale&autoconnect=true"

write_unit "$SERVICE_DROPIN" <<DROPIN
# Added by install_remote_teach.sh -- provides DISPLAY for manual-teach
# and the noVNC embed URL for the BulkDownloader cockpit /capture page.
# Removing this file (and running daemon-reload) reverts the change.
[Unit]
Wants=bd-xvfb.service
After=bd-xvfb.service

[Service]
Environment=DISPLAY=${DISPLAY_NUM}
Environment="BD_NOVNC_URL=${NOVNC_URL}"
DROPIN

# v3.66.470 INSTALL-WARN-SHADOW: a higher-priority drop-in (one that sorts after
# 10-display.conf, e.g. an operator `systemctl edit` override.conf) that also sets
# BD_NOVNC_URL silently wins -- the cockpit embed would use ITS value, not the one
# baked above, leaving the embed unscaled/blank with no error. Warn loudly + name
# the exact file so it isn't a silent shadow.
SHADOW_CONF="$(novnc_shadow_winner "$SERVICE_DROPIN_DIR" "$(basename "$SERVICE_DROPIN")")"
if [ -n "$SHADOW_CONF" ]; then
    echo ""
    echo "  WARNING: BD_NOVNC_URL is ALSO set in a higher-priority drop-in:"
    echo "             ${SERVICE_DROPIN_DIR}/${SHADOW_CONF}"
    echo "           systemd applies drop-ins in lexical order and the LAST"
    echo "           Environment=BD_NOVNC_URL wins, so the cockpit embed will use"
    echo "           that file's value, NOT the one this installer just baked into"
    echo "           $(basename "$SERVICE_DROPIN"). If the embed is unscaled or blank,"
    echo "           fix it there: edit ${SHADOW_CONF} to set"
    echo "             Environment=\"BD_NOVNC_URL=${NOVNC_URL}\""
    echo "           (or remove its BD_NOVNC_URL line), then: sudo systemctl daemon-reload"
    echo ""
fi

echo "  Reloading systemd..."
sudo systemctl daemon-reload \
    || die "systemctl daemon-reload failed"

echo "  Enabling units..."
# Capture the enable output so a failure (e.g. malformed unit) is
# surfaced rather than swallowed by 2>&1 redirect.
if ! sudo systemctl enable bd-xvfb bd-openbox bd-x11vnc bd-novnc 2>/tmp/bd_enable.err; then
    sed 's/^/    /' /tmp/bd_enable.err
    die "systemctl enable failed; see output above"
fi

echo "  Starting units..."
# `systemctl restart` exits 0 on accepted command, non-zero on
# systemd-level rejection (unit missing, parse error, permission
# denied) -- distinct from "command accepted but unit failed to
# enter active state," which the verify loop below catches. Surface
# the systemd-level rejections explicitly so the operator knows
# which unit was the trouble.
restart_unit() {
    local u="$1"
    if ! sudo systemctl restart "$u" 2>/tmp/bd_restart.err; then
        echo "  ERROR: systemctl restart $u was rejected:"
        sed 's/^/    /' /tmp/bd_restart.err
        die "$u restart failed; check journalctl -u $u"
    fi
}
restart_unit bd-xvfb
sleep 1
restart_unit bd-openbox
restart_unit bd-x11vnc
sleep 1
restart_unit bd-novnc

# Restart bulkdownloader to pick up the drop-in (DISPLAY=:99). Only
# restart if it's currently active -- on a fresh box install_service.sh
# may not have run yet, and starting bulkdownloader here would fail
# with "Unit bulkdownloader.service not found" or run the app without
# the operator having configured it.
if systemctl is-active --quiet bulkdownloader; then
    echo "  Restarting bulkdownloader to pick up DISPLAY..."
    if ! sudo systemctl restart bulkdownloader 2>/tmp/bd_app_restart.err; then
        sed 's/^/    /' /tmp/bd_app_restart.err
        echo "  WARNING: bulkdownloader restart failed (above)."
        echo "  Remote-teach stack is installed, but the app is now in a"
        echo "  bad state. Investigate with:"
        echo "    sudo systemctl status bulkdownloader"
        echo "    journalctl -u bulkdownloader -n 30"
        # Don't die() -- the remote-teach install itself succeeded; bail
        # would lose that work. Make it loud and let the operator fix.
    fi
fi

# Wait up to 15s for the unit cascade to settle. With Requires= chain
# bd-xvfb -> bd-openbox/bd-x11vnc -> bd-novnc and RestartSec=3,
# cascade activation can take several seconds; the previous flat
# `sleep 2` falsely reported "NOT RUNNING" on slower boxes.
echo "  Waiting for units to settle..."
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    all_active=1
    any_failed=0
    for u in bd-xvfb bd-openbox bd-x11vnc bd-novnc; do
        state="$(systemctl is-active "$u" 2>/dev/null || true)"
        case "$state" in
            active) ;;
            failed) any_failed=1; all_active=0 ;;
            *)      all_active=0 ;;
        esac
    done
    if [ "$all_active" -eq 1 ] || [ "$any_failed" -eq 1 ]; then
        break
    fi
    sleep 1
done

# -- verify --------------------------------------------------------
echo
echo " ================================================================"
ok=1
for u in bd-xvfb bd-openbox bd-x11vnc bd-novnc; do
    state="$(systemctl is-active "$u" 2>/dev/null || true)"
    if [ "$state" = "active" ]; then
        echo "  $u : RUNNING"
    else
        echo "  $u : $state -- check: journalctl -u $u -n 30"
        ok=0
    fi
done

ip="$(resolve_novnc_ip)"
if [ "$ok" -eq 1 ]; then
    # Same resolution as --status and the baked URL: operator override
    # (BD_NOVNC_IP / --novnc-ip) wins, else first `hostname -I` IP, else
    # loopback (only reachable on this host).
    echo
    echo "  Open in any browser (phone or laptop) on the same LAN:"
    echo "    http://${ip}:${NOVNC_PORT}/vnc.html"
    if [ "$ip" = "127.0.0.1" ]; then
        echo "    (no LAN IP detected; that URL only works on this host."
        echo "     For remote access, substitute this machine's LAN IP.)"
    fi
    echo
    echo "  The BulkDownloader cockpit /capture page embeds this session"
    echo "  automatically -- BD_NOVNC_URL is set in the service drop-in:"
    echo "    ${NOVNC_URL}"
    echo
    echo "  Enter the VNC password printed above when prompted."
    echo "  You will see an empty grey desktop -- that's openbox on ${DISPLAY_NUM}."
    echo "  When you trigger manual-teach in BulkDownloader, the teach"
    echo "  Chromium window will appear in this desktop and you can click"
    echo "  through it from the same browser session."
fi
echo " ================================================================"
[ "$ok" -eq 1 ] || exit 1
