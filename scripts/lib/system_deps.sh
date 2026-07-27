#!/usr/bin/env bash
# scripts/lib/system_deps.sh - THE single source of truth for BD system packages.
#
# THIS FILE IS SOURCED, NEVER EXECUTED. There is no main. The shebang exists so
# shellcheck and editors pick the right dialect; running it directly defines the
# functions in a shell that immediately exits, which is useless but harmless.
#
# Consumers (all three must agree, by construction, forever):
#   * install_linux.sh                 - operator install on a fresh Linux host
#   * scripts/provision_test_host.sh   - operator-facing provisioner for the box
#   * scripts/cloud-setup.sh           - Claude-cloud session-start script
#
# WHY THIS FILE EXISTS (CLAUDE.md 0 and 8). Before it, the package list lived in
# cloud-setup.sh only, and the other two scripts either printed different advice
# or installed nothing. Three copies of a package list is a denominator that
# drifts: each script then answers "are the system deps present?" over its own
# private idea of what the deps ARE, and every one of them can report OK while
# the host is missing something another script considers mandatory. A gate that
# cannot see the thing it is asked about reports OK, and that is worse than no
# gate. One list, three callers, no way to disagree.
#
# PROVENANCE. The `gtk` group is lifted VERBATIM from the "GTK + Xvfb" step of
# scripts/cloud-setup.sh (section 7, extras):
#     step "GTK + Xvfb"   optional apt_i xvfb libgtk-3-0t64 gir1.2-gtk-3.0 \
#                                        python3-gi libcairo2 libgirepository-1.0-1
# Those six exist because tests/test_v3_43_80_modules::test_all_modules_import
# false-fails without BOTH the GTK typelibs and a live display (CLAUDE.md 5:
# environmental, never a code regression).
#
# RULES THIS FILE OBEYS, EACH LEARNED BY BREAKING IT:
#
#  1. NO `set -e`, `set -u`, `set -o pipefail`, or any other shell option at
#     file scope. Options set here leak into the sourcing script the instant it
#     sources us. install_linux.sh and cloud-setup.sh deliberately run WITHOUT
#     errexit so an optional step can degrade to a warning instead of aborting;
#     a stray `set -e` here would silently convert every `|| echo "(skipped)"`
#     in those files into a hard abort. Worse, the gate that forbids `set -e`
#     (tests/test_cloak_install_path.py) reads install_linux.sh ONLY, so it
#     would still report clean - a denominator that excludes its subject.
#
#  2. NO `BD_*` variable names. tools/config_surface_inventory.py::_scan_shell_env
#     harvests BD_[A-Z0-9_]+ from <root>/*.sh and <root>/scripts/*.sh - ONE level,
#     not recursive - so scripts/lib/ is structurally invisible to the ledger
#     that exists to make BD_* knobs impossible to miss. A knob defined here
#     would be un-ledgered while every parity gate still reported clean. Private
#     state in this file is lowercase and underscore-prefixed.
#
#  3. NO output on stdout except the value the caller asked for. Both functions
#     are meant to be captured with $(...). Diagnostics go to stderr.
#
#  4. Failure is loud and empty-handed: a bad group prints WHY on stderr and
#     returns non-zero WITHOUT echoing a package list. An empty list is the
#     dangerous case - `apt-get install -y -qq` with zero package arguments
#     exits 0, so a silently-empty list makes an installer report success while
#     installing nothing. Callers must therefore capture into a variable and
#     refuse to run apt on an empty string; `apt_i $(bd_system_pkgs gtk)` throws
#     the non-zero exit away and cannot see the failure.

# Double-source guard. Re-sourcing would merely redefine identical functions, so
# this is cheap insurance rather than a correctness fix - but a consumer that
# sources us in a loop should not pay for it. `return` is legal here because the
# file is sourced; the redirect+|| keeps a stray direct execution from printing
# "can only return from a function or sourced script" and reporting a failure.
if [ "${_bd_system_deps_sourced:-0}" = "1" ]; then
    return 0 2>/dev/null || true
fi
_bd_system_deps_sourced=1

# --- private helpers ---------------------------------------------------------

# _bd_dedup <name>... -> echoes the arguments, first occurrence wins, order kept.
# `all` is a concatenation of three lists; if a package ever appears in two of
# them, apt would be handed a duplicate. Harmless to apt, but it makes the
# "is X in the list" answer depend on how many times you ask.
_bd_dedup() {
    local out="" tok
    for tok in "$@"; do
        case " $out " in
            *" $tok "*) continue ;;
        esac
        out="${out:+$out }$tok"
    done
    printf '%s\n' "$out"
}

# _bd_display_active <display> -> 0 when an X server really owns that display.
#
# The check this REPLACES was `pgrep -x Xvfb`, which is the wrong denominator in
# both directions: it tests a PROCESS NAME while the subject is a DISPLAY. An
# Xvfb on :0 makes it report ":99 is up" when nothing holds :99; a non-Xvfb X
# server on :99 (kasmvnc, which cloud-setup.sh installs) makes it report ":99 is
# free" when it is not - which is exactly how the operator got
# "Fatal server error: Server is already active for display 99".
#
# Three probes, most authoritative first. A lock file alone is NOT proof: a
# killed server leaves /tmp/.X99-lock behind, and treating a stale lock as
# "active" would report OK for a display nothing is serving.
_bd_display_active() {
    local disp="$1"
    local num="${disp#:}"
    local lock="/tmp/.X${num}-lock"
    local sock="/tmp/.X11-unix/X${num}"
    local pid=""

    # 1. Ask the server itself. Only available when x11-utils is installed.
    if command -v xdpyinfo >/dev/null 2>&1; then
        if DISPLAY="$disp" xdpyinfo >/dev/null 2>&1; then
            return 0
        fi
    fi

    # 2. Lock file whose recorded pid is still alive. /proc is checked before
    #    `kill -0` because kill fails with EPERM for a live process owned by
    #    another user - reading that as "dead" would start a second server on an
    #    occupied display, which is the failure this function exists to prevent.
    if [ -r "$lock" ]; then
        read -r pid < "$lock" 2>/dev/null || pid=""
        case "$pid" in
            ''|*[!0-9]*) pid="" ;;
        esac
        if [ -n "$pid" ]; then
            if [ -d "/proc/$pid" ] || kill -0 "$pid" 2>/dev/null; then
                return 0
            fi
        fi
    fi

    # 3. A live listener on the unix socket. Last resort: xdpyinfo may be absent
    #    and the lock may be unreadable, but a connectable socket still means
    #    somebody is serving the display.
    if [ -S "$sock" ] && command -v ss >/dev/null 2>&1; then
        if ss -lx 2>/dev/null | grep -q "[[:space:]]${sock}\$"; then
            return 0
        fi
    fi

    return 1
}

# --- public API --------------------------------------------------------------

# bd_system_pkgs <core|node|gtk|all> -> space-separated apt package list on stdout.
#
# Returns non-zero and echoes NOTHING for a missing or unknown group.
bd_system_pkgs() {
    # The lists. This is the whole point of the file: edit them here, nowhere else.
    local core="git python3.12 python3.12-venv python3-pip"
    local node="nodejs npm"
    local gtk="xvfb libgtk-3-0t64 gir1.2-gtk-3.0 python3-gi libcairo2 libgirepository-1.0-1"

    if [ "$#" -eq 0 ]; then
        printf 'bd_system_pkgs: no package group given (expected one of: core node gtk all)\n' >&2
        return 1
    fi

    case "$1" in
        core) printf '%s\n' "$core" ;;
        node) printf '%s\n' "$node" ;;
        gtk)  printf '%s\n' "$gtk" ;;
        # Word splitting is deliberate here: _bd_dedup wants one argument per
        # package, not three strings.
        # shellcheck disable=SC2086
        all)  _bd_dedup $core $node $gtk ;;
        *)
            printf 'bd_system_pkgs: unknown package group %s (expected one of: core node gtk all)\n' \
                "'$1'" >&2
            return 1
            ;;
    esac
}

# bd_start_display [display] -> echoes the usable DISPLAY value on stdout.
#
# Idempotent by contract: if an X server already owns the display, this does NOT
# start a second one and does NOT fail - it echoes the display and returns 0.
# Returns non-zero (with a reason on stderr) when no display can be provided.
# The caller decides what to do with that; it does not export DISPLAY for you.
bd_start_display() {
    local disp="${1:-:99}"
    local num tries

    # Accept ":99" and "99" alike - the two spellings would otherwise pick
    # different lock files while naming the same display.
    num="${disp#:}"
    case "$num" in
        ''|*[!0-9]*)
            printf 'bd_start_display: %s is not a valid display (expected e.g. :99)\n' \
                "'$disp'" >&2
            return 1
            ;;
    esac
    disp=":$num"

    # Already up: the idempotent path. Checked BEFORE the Xvfb probe on purpose -
    # a display served by some other X server (kasmvnc, a real session) is a
    # usable display even on a host with no Xvfb installed.
    if _bd_display_active "$disp"; then
        printf '%s\n' "$disp"
        return 0
    fi

    if ! command -v Xvfb >/dev/null 2>&1; then
        printf 'bd_start_display: Xvfb is not installed, cannot provide %s (apt install %s)\n' \
            "$disp" "$(bd_system_pkgs gtk)" >&2
        return 1
    fi

    # setsid detaches the server from this shell's process group so a Ctrl-C or
    # a SIGHUP at the end of the provisioning run does not take the display with
    # it. Plain `&` is the fallback; both write /tmp/.X<n>-lock, which is what
    # the readiness poll below actually reads.
    if command -v setsid >/dev/null 2>&1; then
        setsid Xvfb "$disp" -screen 0 1024x768x24 </dev/null >/dev/null 2>&1 &
    else
        Xvfb "$disp" -screen 0 1024x768x24 </dev/null >/dev/null 2>&1 &
    fi

    # Poll for readiness instead of `sleep 2`. A fixed sleep is a guess: it is
    # simultaneously too long on a fast host and a false OK on a slow one, and
    # the row it feeds cannot see whether the server actually came up.
    tries=0
    while [ "$tries" -lt 20 ]; do
        if _bd_display_active "$disp"; then
            printf '%s\n' "$disp"
            return 0
        fi
        sleep 0.25
        tries=$((tries + 1))
    done

    printf 'bd_start_display: Xvfb did not bring up %s within 5s\n' "$disp" >&2
    return 1
}

# Sourcing must succeed. Consumers write `. scripts/lib/system_deps.sh || <warn>`,
# and the exit status of `.` is the status of the LAST command it ran - so a file
# ending on anything falsy would report "could not source" to a caller that just
# sourced it perfectly well.
:
