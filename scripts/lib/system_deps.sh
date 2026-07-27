#!/usr/bin/env bash
# scripts/lib/system_deps.sh - THE single source of truth for BD system packages.
#
# THIS FILE IS SOURCED, NEVER EXECUTED. There is no main. The shebang exists so
# that linters and editors pick the right dialect; running it directly defines
# the functions in a shell that immediately exits, which is useless but harmless.
#
# COMMENT TRAP, DO NOT UNDO. No comment line in this file may BEGIN with the
# word "shellcheck" unless it really is a directive. The linter reads the first
# word after "# " as a directive key and ABORTS THE WHOLE FILE when it does not
# parse (SC1073/SC1072). The previous wording of the paragraph above opened a
# line with that word as prose, so the single source of truth carried ZERO lint
# coverage while every consumer still looked fine: consumers that parse report
# SC1094 "parsing of sourced file failed" and drop us, and consumers that fail
# to parse for their own reasons report nothing at all. A gate that cannot see
# the thing it is asked about reports OK.
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
# PROVENANCE. The FIRST SIX names of the `gtk` group are lifted VERBATIM from
# the "GTK + Xvfb" step of scripts/cloud-setup.sh (section 7, extras):
#     step "GTK + Xvfb"   optional apt_i xvfb libgtk-3-0t64 gir1.2-gtk-3.0 \
#                                        python3-gi libcairo2 libgirepository-1.0-1
# Those six exist because tests/test_v3_43_80_modules::test_all_modules_import
# false-fails without BOTH the GTK typelibs and a live display (CLAUDE.md 5:
# environmental, never a code regression).
#
# x11-utils is the ONE ADDITION, appended last so the six above stay contiguous
# and the "lifted verbatim" claim stays checkable rather than quietly false. It
# is not cosmetic: xdpyinfo is the only probe in _bd_display_active() that
# proves an X SERVER - not merely a listener - is answering, and without it that
# function degrades to weaker probes that cannot distinguish a served display
# from a stale claim on one. A host provisioned from this file must be able to
# run this file's own best check; shipping the check and withholding the tool it
# needs is a gate that cannot reach its subject.
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
#     are meant to be captured with $(...). Diagnostics go to stderr - including
#     the replayed Xvfb failure text in bd_start_display, which is captured to a
#     FILE and only ever surfaced on stderr.
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
# Three probes, most authoritative first - and the ORDER is load-bearing. A lock
# file alone is NOT proof: a killed server leaves /tmp/.X99-lock behind, and a
# server writes that lock BEFORE it binds anything, so the lock is evidence of a
# CLAIM on the display and never of service. It therefore answers LAST, and only
# when no probe that can observe service was available at all.
_bd_display_active() {
    local disp="${1:-}"
    local num="${disp#:}"
    local lock="/tmp/.X${num}-lock"
    local pid=""
    local comm=""
    # Whether ANY probe that can actually observe service was available. It is
    # the difference between "no" and "could not tell", and probe 3 below is
    # only allowed to answer the second one.
    local competent=0

    # The display number is interpolated into a grep -E pattern below, so a
    # non-numeric argument would arrive as a REGEX rather than as a display.
    # Validating here keeps the pattern's subject equal to the caller's.
    case "$num" in
        ''|*[!0-9]*) return 1 ;;
    esac

    # 1. Ask the server itself. The only probe that proves an X SERVER - not
    #    merely a listener - is serving the display, and the only one that works
    #    through the socket FILE, so it is also the only one that survives the
    #    server living in a different network namespace. It needs xdpyinfo,
    #    which is why x11-utils is in `bd_system_pkgs gtk`: a host this file
    #    provisions must be able to run this file's own best check.
    if command -v xdpyinfo >/dev/null 2>&1; then
        competent=1
        if DISPLAY="$disp" xdpyinfo >/dev/null 2>&1; then
            return 0
        fi
    fi

    # 2. A live listener on the display's unix socket.
    #
    #    THE PATTERN IS THE WHOLE PROBE, so it is spelled out. Measured `ss -lx`
    #    rows for a live :77 - both are emitted, always:
    #
    #      u_str LISTEN 0  0  @/tmp/.X11-unix/X77 7541  * 0
    #      u_str LISTEN 0  0   /tmp/.X11-unix/X77 7542  * 0
    #
    #    * `@?` is not optional: the abstract socket is '@'-prefixed, and a
    #      pattern that accepts only the filesystem form goes blind wherever
    #      only the abstract one is published.
    #    * The path is FOLLOWED by an inode and two peer columns, so anchoring
    #      it to end-of-line - which this probe used to do - matches zero rows,
    #      ever. Measured against the rows above while :77 was genuinely up,
    #      `grep -q "[[:space:]]/tmp/.X11-unix/X77$"` exits 1. The probe was
    #      dead code and every caller fell silently through to `return 1`.
    #    * The trailing boundary is load-bearing in the other direction too:
    #      without it a query about X7 matches X77 and calls a free display busy.
    #    * The '.' of '.X11-unix' is escaped. Unescaped it matches ANY
    #      character, so /tmp/XX11-unix/X42 answers a question about :42.
    #      Measured: the unescaped pattern exits 0 against that decoy line.
    #
    #    `[ -S /tmp/.X11-unix/X<n> ]` is deliberately NOT a precondition. The
    #    socket file can be absent while the abstract socket is served, and a
    #    precondition that excludes a live case is the blind denominator this
    #    file exists to avoid. It was never needed for the stale case either:
    #    /tmp/.X11-unix/X99 outlives its dead server as a socket file, and ss
    #    does not list it - measured.
    if command -v ss >/dev/null 2>&1; then
        competent=1
        if ss -lx 2>/dev/null \
            | grep -Eq "(^|[[:space:]])@?/tmp/\.X11-unix/X${num}([[:space:]]|\$)"; then
            return 0
        fi
    fi

    # 3. FALLBACK ONLY - never a tiebreaker. Measured: a live process holding
    #    /tmp/.X62-lock while listening on nothing made this function return 0
    #    and bd_start_display report success for a display nobody was serving,
    #    because the lock answered before the socket probe could. So if probe 1
    #    or probe 2 was available and said no, the answer is no; this branch
    #    exists only for the case where neither could look.
    if [ "$competent" -eq 0 ] && [ -r "$lock" ]; then
        read -r pid < "$lock" 2>/dev/null || pid=""
        case "$pid" in
            ''|*[!0-9]*) pid="" ;;
        esac
        # /proc is checked before `kill -0` because kill fails with EPERM for a
        # live process owned by another user - reading that as "dead" would
        # start a second server on an occupied display, which is the failure
        # this function exists to prevent.
        if [ -n "$pid" ] && { [ -d "/proc/$pid" ] || kill -0 "$pid" 2>/dev/null; }; then
            # Pids are recycled, so "alive" alone certifies nothing. Where /proc
            # will say WHAT the process is, require it to look like an X server -
            # Xvfb, Xorg, Xvnc and Xwayland all begin with a capital X. Where it
            # will NOT say (hidepid, or another user's process) keep the old
            # conservative answer: this probe's job is to stop a second server
            # landing on an occupied display, and a wrong "free" is the
            # expensive direction to be wrong in.
            read -r comm < "/proc/$pid/comm" 2>/dev/null || comm=""
            case "$comm" in
                ''|X*) return 0 ;;
            esac
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
    #
    # ARRAYS, not strings, so `all` hands _bd_dedup ONE ARGUMENT PER PACKAGE
    # without an unquoted expansion. The string form needed a
    # `# shellcheck disable=SC2086`, and that suppression was two defects at
    # once. Parked in front of a single `case` BRANCH it is SC1124 - a directive
    # is only valid before a complete command such as the whole `case` - which
    # aborted the parse of this entire file and cost every consumer its
    # cross-file analysis. And it was hiding a REAL bug rather than a false
    # positive: with a consumer that had changed IFS, `_bd_dedup $core $node
    # $gtk` handed over 3 arguments instead of one per package (measured: 3
    # against 12 on the pre-x11-utils list), so dedup silently stopped
    # deduplicating while stdout stayed byte-identical. Nothing could see it.
    local core=(git python3.12 python3.12-venv python3-pip)
    local node=(nodejs npm)
    local gtk=(
        xvfb
        libgtk-3-0t64
        gir1.2-gtk-3.0
        python3-gi
        libcairo2
        libgirepository-1.0-1
        x11-utils
    )

    # "${arr[*]}" joins on the FIRST character of IFS, so pin IFS locally: the
    # contract is a space-separated list regardless of what the caller left set.
    # `local` scoping restores the caller's IFS on return.
    local IFS=' '

    if [ "$#" -eq 0 ]; then
        printf 'bd_system_pkgs: no package group given (expected one of: core node gtk all)\n' >&2
        return 1
    fi

    case "$1" in
        core) printf '%s\n' "${core[*]}" ;;
        node) printf '%s\n' "${node[*]}" ;;
        gtk)  printf '%s\n' "${gtk[*]}" ;;
        all)  _bd_dedup "${core[@]}" "${node[@]}" "${gtk[@]}" ;;
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
    # `${1:-:99}` was wrong: it treats an EMPTY argument as an ABSENT one, so
    # `bd_start_display ""` silently answered about :99 - a different display
    # from the one the caller named - while `bd_start_display abc` correctly
    # failed. bd_system_pkgs already rejects "" (an empty group is not the
    # default group); two functions in one file disagreeing about what ""
    # means is precisely the drift this file exists to stop. $# is the only
    # thing that separates unset from empty.
    local disp
    if [ "$#" -eq 0 ]; then
        disp=":99"
    else
        disp="$1"
    fi

    local num tries comm
    local sink="/dev/null"
    local errlog=""
    local xvfb_pid=""

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

    # Xvfb's diagnosis of its own failure is the most useful string this
    # function will ever have, and it used to go to /dev/null - so a real
    # failure surfaced only as "did not bring up :99 within 5s", manufacturing
    # an unknown where a knowable cause existed. Measured, this is what was
    # being destroyed at the exact moment it was needed:
    #
    #   _XSERVTransMakeAllCOTSServerListeners: server already running
    #   Fatal server error:
    #   (EE) Cannot establish any listening sockets - Make sure an X server
    #   isn't already running
    #
    # Rule 3 survives: the capture is a FILE, replayed on stderr only on the
    # failure path. A failing mktemp degrades to the old /dev/null rather than
    # aborting a start that would otherwise have worked. The template is
    # lowercase and hyphenated so it cannot be mistaken for a BD_* knob by the
    # config-surface harvest described in rule 2.
    errlog="$(mktemp "${TMPDIR:-/tmp}/bd-xvfb-${num}-XXXXXX" 2>/dev/null)" || errlog=""
    if [ -n "$errlog" ]; then
        sink="$errlog"
    fi

    # setsid detaches the server from this shell's process group so a Ctrl-C or
    # a SIGHUP at the end of the provisioning run does not take the display with
    # it. Plain `&` is the fallback; both write /tmp/.X<n>-lock, which is one of
    # the things the readiness poll reads.
    if command -v setsid >/dev/null 2>&1; then
        setsid Xvfb "$disp" -screen 0 1024x768x24 </dev/null >"$sink" 2>&1 &
    else
        Xvfb "$disp" -screen 0 1024x768x24 </dev/null >"$sink" 2>&1 &
    fi
    xvfb_pid="$!"

    # Poll for readiness instead of `sleep 2`. A fixed sleep is a guess: it is
    # simultaneously too long on a fast host and a false OK on a slow one, and
    # the row it feeds cannot see whether the server actually came up.
    tries=0
    while [ "$tries" -lt 20 ]; do
        if _bd_display_active "$disp"; then
            printf '%s\n' "$disp"
            rm -f "$errlog"
            return 0
        fi
        sleep 0.25
        tries=$((tries + 1))
    done

    # Final adjudication - and the reason repairing the socket probe alone does
    # NOT close the competing-server path. Between the pre-check and the spawn
    # another process can take the display, and on a host with neither xdpyinfo
    # nor ss every probe is blind, so "not active" there meant "could not tell".
    # In both cases OUR Xvfb loses the race and exits; the caller asked for a
    # usable display, not for a process it owns, so ask once more before
    # reporting a failure that is not one.
    if _bd_display_active "$disp"; then
        printf '%s\n' "$disp"
        rm -f "$errlog"
        return 0
    fi

    # Nobody serves the display and we may still have a half-started server
    # holding it. Reap it: Xvfb writes /tmp/.X<n>-lock BEFORE it binds, so a
    # wedged server left running makes the NEXT call's probe 3 report the
    # display active on a lock whose pid is alive and serving nothing.
    #
    # $! is the Xvfb pid only when job control is OFF - which is every consumer
    # of this file, all three non-interactive scripts. Measured both ways: under
    # `set -m`, setsid forks and $! is the parent, which has already exited. So
    # the pid is a HINT and is acted on only when /proc still calls it an Xvfb;
    # killing the bare pid would, under job control, signal a recycled stranger.
    # That makes the reap best-effort by construction, which is the honest
    # degradation - "no orphan" is not an invariant this function can promise.
    if [ -n "$xvfb_pid" ] && [ -r "/proc/$xvfb_pid/comm" ]; then
        read -r comm < "/proc/$xvfb_pid/comm" 2>/dev/null || comm=""
        if [ "$comm" = "Xvfb" ]; then
            kill "$xvfb_pid" 2>/dev/null
        fi
    fi

    printf 'bd_start_display: Xvfb did not bring up %s within 5s\n' "$disp" >&2
    if [ -n "$errlog" ] && [ -s "$errlog" ]; then
        printf 'bd_start_display: last 10 lines of Xvfb output follow --\n' >&2
        tail -n 10 "$errlog" >&2
    else
        printf 'bd_start_display: Xvfb produced no output to explain itself\n' >&2
    fi

    # Unknown is a third state and it has to be named. With neither xdpyinfo nor
    # ss present the readiness poll degrades to the lock file alone, which
    # cannot tell an X server from a recycled pid - so "did not come up" may
    # mean "came up and I could not see it".
    if ! command -v xdpyinfo >/dev/null 2>&1 && ! command -v ss >/dev/null 2>&1; then
        printf 'bd_start_display: UNKNOWN - neither xdpyinfo (x11-utils) nor ss\n' >&2
        printf 'bd_start_display: (iproute2) is installed, so readiness was judged\n' >&2
        printf 'bd_start_display: from /tmp/.X%s-lock alone, which cannot see a server.\n' \
            "$num" >&2
    fi

    rm -f "$errlog"
    return 1
}

# Sourcing must succeed. Consumers write `. scripts/lib/system_deps.sh || <warn>`,
# and the exit status of `.` is the status of the LAST command it ran - so a file
# ending on anything falsy would report "could not source" to a caller that just
# sourced it perfectly well.
:
