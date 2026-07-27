#!/usr/bin/env bash
# scripts/provision_test_host.sh - ONE command that takes a fresh Ubuntu 24.04
# host from a bare checkout to a green ./capture.sh, and RECORDS the outcome of
# every step it performs.
#
#     ./scripts/provision_test_host.sh            # repo found from this script
#     ./scripts/provision_test_host.sh /path/repo # or point it at a checkout
#
# WHY THIS FILE EXISTS. Provisioning the box was split across two scripts that
# never met:
#
#   * install_linux.sh does the REPO half (venv, requirements, playwright
#     browsers, cloakbrowser, vendored assets, SPA build) and deliberately never
#     touches the root tier -- when a system package is missing it PRINTS advice
#     and continues.
#   * scripts/cloud-setup.sh does the ROOT tier (system packages, a display) and
#     the gui-parity inventory regen -- but it is the Claude-cloud session-start
#     script, and the operator's box never runs it.
#
# So on the box nobody ran the root tier, nobody started a display, and nobody
# regenerated the gui-parity inventory. The last of those is not cosmetic: it is
# why a capture failed on
# tests/test_v3_66_302_gui_parity_reconcile.py::test_shipped_inventory_matches_live_regen_itemset
# with "only-regen=['pytest_capture_results']". See step [7/8].
#
# DESIGN RULES, each of them load-bearing:
#
#  1. `set -uo pipefail`, NOT `set -e`. A failed step must be RECORDED and the
#     run must continue: aborting at the first failure produces a host in an
#     unknown state and a console that explains nothing. The verdict at the end
#     is the gate, not the first non-zero exit. (Same reason cloud-setup.sh
#     gives at its own `set -uo pipefail`.)
#
#  2. FOUR result states, and UNKNOWN is one of them (CLAUDE.md 0):
#       OK      - the step ran and succeeded.
#       WARN    - an OPTIONAL capability is absent. Visible, does not block.
#                 A WARN is a capability you do NOT have, never a pass.
#       FAIL    - a LOAD-BEARING step failed. Blocks the verdict.
#       UNKNOWN - a load-bearing step could not be EVALUATED. Also blocks.
#                 A check that cannot see its subject must say so; reporting OK
#                 because nothing was examined is worse than having no check.
#     Nothing is ever skipped silently. If a step is not attempted, the reason
#     is recorded as a row.
#
#  3. NO apt package names in this file. They come from bd_system_pkgs() in
#     scripts/lib/system_deps.sh, which is the single denominator shared with
#     install_linux.sh and scripts/cloud-setup.sh. Three scripts with three
#     private copies of a package list is three different answers to "are the
#     system deps present?", each of which can report OK while the host is
#     missing something another script considers mandatory. Anti-drift is
#     enforced by tests/test_provision_test_host.py, which asserts the literal
#     names are ABSENT here.
#
#  4. NO BD_-prefixed variable names. tools/config_surface_inventory.py
#     harvests BD_[A-Z0-9_]+ from <root>/*.sh and <root>/scripts/*.sh -- this
#     file IS in that glob, so a knob introduced here manufactures a
#     config-surface item that ~20 parity tests consume, and re-using an
#     existing name makes the ledger's recorded source_file depend on glob
#     order. The optional repo path is a positional argument instead.
#
# Everything this script runs is idempotent: re-running it on a provisioned host
# is a no-op plus a fresh verdict.

set -uo pipefail   # deliberately NOT -e: see design rule 1 above.

usage() {
    cat <<'USAGE'
usage: provision_test_host.sh [REPO_PATH]

Provisions this host to run ./capture.sh: system packages (needs root or
sudo), the repo install, headless chromium system libraries, an X display,
and the gui-parity inventory regen. Prints a verdict and exits non-zero if a
load-bearing step failed or could not be evaluated.

REPO_PATH is optional. With no argument the repo is derived from this script's
own location, then from the current directory, and either way it is confirmed
by the marker bulk_downloader/__init__.py. When you DO pass it, it is an
ASSERTION rather than a hint: a path without that marker is fatal, never a
reason to fall back to some other checkout that happens to be nearby.
USAGE
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

# ---------------------------------------------------------------- [1/8] repo
# Resolve the repo by its MARKER, never by $0 alone. `dirname "$0"/..` is a
# guess: invoke this through a symlink, through `bash scripts/...`, or from a
# copied path and it resolves to something that is not the repo -- historically
# to "/", which then provisions the whole filesystem while every step still
# reports OK. Each candidate below is CONFIRMED by the marker before it is
# accepted, exactly as scripts/cloud-setup.sh does it.
#
# The bounded `find /` fallback cloud-setup.sh carries is deliberately NOT
# copied: that script is executed from a temp path (or stdin) by the setup
# panel, so it genuinely has no idea where the repo is. This one SHIPS INSIDE
# the repo, so ${BASH_SOURCE[0]} is a real location in the tree and the search
# would only add a way to provision the wrong checkout.
#
# An explicitly passed REPO_PATH is NOT one of the candidates. It is the
# operator naming the subject, so it is checked alone and a miss is fatal --
# see find_repo below for the measured reason.
MARKER="bulk_downloader/__init__.py"

find_repo() {
    local candidate here explicit="${1:-}"

    # An explicit REPO_PATH is an ASSERTION, not a candidate. This loop used to
    # take it as the FIRST of three guesses, so a path WITHOUT the marker fell
    # through to the script's own location and the run went on to provision a
    # DIFFERENT tree while row 1 still read "repo root OK". Measured on the
    # pre-fix function, run rather than reasoned about:
    #     find_repo /nope/xyz       -> rc=0  stdout='/home/user/BD'
    #     find_repo /also/not/here  -> rc=0  stdout='/home/user/BD'
    # The subject the operator stated was never examined and the row said the
    # opposite -- a check whose denominator excludes its subject reports OK.
    #
    # Exit 3 is reserved for this case so the caller can name the path it
    # REJECTED. The generic "no checkout found" advice would be actively
    # misleading here: it sends the operator hunting for a missing tree when the
    # tree is fine and the argument they typed is what is wrong.
    if [ -n "$explicit" ]; then
        if [ -f "$explicit/$MARKER" ]; then
            (cd "$explicit" && pwd)
            return 0
        fi
        return 3
    fi

    here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." 2>/dev/null && pwd)" || here=""
    for candidate in "$here" "$PWD"; do
        if [ -n "$candidate" ] && [ -f "$candidate/$MARKER" ]; then
            (cd "$candidate" && pwd)
            return 0
        fi
    done
    return 1
}

# `find_repo_rc` is lowercase on purpose (design rule 4) and is initialised
# before use because this file runs under `set -u`: an unset read here would
# abort at the very first step and destroy the verdict the script exists to
# print. Capturing the code at all depends on there being no `set -e`
# (design rule 1); the assignment is on the `||` side, so it survives either way.
REPO=""
find_repo_rc=0
REPO="$(find_repo "${1:-}")" || find_repo_rc=$?
if [ "$find_repo_rc" -eq 3 ]; then
    echo "FATAL: '${1:-}' is not a BulkDownloader checkout." >&2
    echo "  You named that path explicitly, so it is an assertion and not a" >&2
    echo "  hint: there is no $MARKER under it." >&2
    echo "  Refusing to quietly fall back to another tree that happens to be" >&2
    echo "  nearby -- that provisions one checkout and reports on a different" >&2
    echo "  one, which is the failure mode this check exists to prevent." >&2
    exit 2
fi
if [ -z "$REPO" ]; then
    echo "FATAL: no BulkDownloader checkout found." >&2
    echo "  Looked for the marker $MARKER next to this script," >&2
    echo "  and in \$PWD ($PWD)." >&2
    echo "  Pass the checkout explicitly:  $0 /path/to/BulkDownloader" >&2
    echo "  Refusing to provision against an unknown root." >&2
    exit 2
fi
cd "$REPO" || { echo "FATAL: could not cd to $REPO" >&2; exit 2; }

LOGDIR="/tmp/bd_provision"
mkdir -p "$LOGDIR" || { echo "FATAL: could not create $LOGDIR" >&2; exit 2; }

ROWS=""
BLOCKING=0
WARNS=0
START="$(date +%s)"

# record <label> <OK|WARN|FAIL|UNKNOWN> <detail>
# The ONLY way a step's outcome enters the verdict. Detail must not contain "|":
# that is the field separator the verdict table splits on.
record() {
    local label="$1" result="$2" detail="$3"
    ROWS="${ROWS}${label}|${result}|${detail}"$'\n'
    case "$result" in
        FAIL|UNKNOWN) BLOCKING=1 ;;
        WARN)         WARNS=$((WARNS + 1)) ;;
    esac
    printf '  [%-7s] %s%s\n' "$result" "$label" "${detail:+  -- $detail}"
}

# run_step <slug> <label> <core|optional> <command...>
# Streams to the console AND keeps a log, then derives the row from the REAL
# exit code via PIPESTATUS. `tee` is used rather than a quiet redirect because
# the long steps here take minutes and a silent console is indistinguishable
# from a hang. Returns the command's exit code so a caller can branch on it.
run_step() {
    local slug="$1" label="$2" kind="$3"; shift 3
    local log="$LOGDIR/$slug.log" rc=0
    printf '\n--- %s\n' "$label"
    "$@" 2>&1 | tee "$log"
    rc="${PIPESTATUS[0]}"
    if [ "$rc" -eq 0 ]; then
        record "$label" "OK" "log: $log"
    elif [ "$kind" = core ]; then
        record "$label" "FAIL" "exit $rc, log: $log"
    else
        record "$label" "WARN" "exit $rc, capability ABSENT, log: $log"
    fi
    return "$rc"
}

echo "=== BulkDownloader host provisioning ==="
echo "  repo   : $REPO"
echo "  logs   : $LOGDIR"
echo "  user   : $(id -un) (uid $(id -u))"
echo
record "repo root" "OK" "$REPO (confirmed by $MARKER)"

# ------------------------------------------------------------ [2/8] fragment
echo
echo "=== [2/8] shared system-dependency fragment ==="
if [ ! -r "$REPO/scripts/lib/system_deps.sh" ]; then
    echo "FATAL: $REPO/scripts/lib/system_deps.sh is missing or unreadable." >&2
    echo "  It is the single source of truth for BD's system packages. Without" >&2
    echo "  it this script has no package list, and guessing one is precisely" >&2
    echo "  the drift the fragment exists to prevent." >&2
    echo "  (Note the deploy path is an overlay: unzip -o adds and overwrites," >&2
    echo "  never deletes, so a box that predates this cut needs one more" >&2
    echo "  deploy before the fragment is present.)" >&2
    exit 2
fi
# The path is written out literally rather than through a variable so that a
# reader -- and the anti-drift test -- can see WHICH file is sourced. A line
# reading `. "$SOMEVAR"` names nothing.
# shellcheck source=scripts/lib/system_deps.sh
. "$REPO/scripts/lib/system_deps.sh"
# Sourcing cleanly proves NOTHING about what was defined: a file that parses can
# still define no functions at all, and `.` returns the status of the last
# command it ran. Check the names.
if ! declare -F bd_system_pkgs >/dev/null 2>&1 \
   || ! declare -F bd_start_display >/dev/null 2>&1; then
    echo "FATAL: sourced scripts/lib/system_deps.sh but bd_system_pkgs and/or" >&2
    echo "  bd_start_display are undefined. The fragment is present but broken;" >&2
    echo "  refusing to continue with an unknown package denominator." >&2
    exit 2
fi
record "system_deps fragment" "OK" "sourced; bd_system_pkgs and bd_start_display defined"

# -------------------------------------------------------- [3/8] system tier
echo
echo "=== [3/8] system package tier (needs root) ==="
# Unlike install_linux.sh, installing the root tier IS this script's job, so a
# host where it cannot be done is a hard stop rather than a printed hint.
SUDO=""
if [ "$(id -u)" -eq 0 ]; then
    record "elevation" "OK" "running as root"
elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
    if sudo -n true 2>/dev/null; then
        record "elevation" "OK" "not root; sudo available without a prompt"
    else
        record "elevation" "OK" "not root; using sudo (it will prompt for a password)"
    fi
else
    echo "FATAL: not running as root and sudo is not installed." >&2
    echo "  Installing the system tier is this script's whole job -- there is" >&2
    echo "  no degraded mode that leaves the host provisioned." >&2
    echo "  Re-run as root, or install sudo first." >&2
    exit 2
fi
export DEBIAN_FRONTEND=noninteractive

# Index refresh is optional: a stale-but-present index can still satisfy the
# installs, and when it cannot, the group steps below fail and say so.
# shellcheck disable=SC2086  # $SUDO is empty when already root
run_step 03a_apt_update "package index refresh" optional $SUDO apt-get update || true

# install_group <slug> <label> <core|node|gtk> <core|optional>
#
# ONE transaction PER GROUP, never one transaction for `all`.
#
# WHY. The installer is ALL-OR-NOTHING, measured on this host rather than
# assumed: a simulate run (-s, which installs nothing) of one available name
# plus one nonexistent name exits 100 with "E: Unable to locate package ..." and
# plans ZERO Inst lines, while the same simulate of the available name ALONE
# exits 0 and plans seven. The available name is not installed either. So
# handing over the `all` list -- 13 names when this was measured, and a count
# nobody should quote without re-running bd_system_pkgs -- meant ONE unavailable
# name installed NOTHING: no interpreter, no package manager for the SPA
# toolchain, no display libraries. And the run reported that as a single failed
# row that could not say which name was to blame. Both ways of hitting it are
# real rather than hypothetical: the t64 library renames make some of these
# names wrong on 22.04, and the pinned interpreter needs a PPA on older
# releases. scripts/cloud-setup.sh has always kept its "GTK + Xvfb" step in a
# transaction of its own; this restores that granularity here instead of
# regressing it.
#
# CRITICALITY IS PER GROUP, and each choice is load-bearing:
#
#   core  -> `core`.     Every step from [4/8] to [8/8] gates on
#                        venv/bin/python, and the venv cannot be built without
#                        the interpreter and its own package manager. Nothing
#                        downstream of a failure here is meaningful.
#
#   node  -> `core`.     capture.sh exits 2 with "FATAL:
#                        frontend/dist/index.html is missing" BEFORE it collects
#                        a single test, and that file is produced by the SPA
#                        toolchain this group installs. A host that cannot build
#                        the SPA cannot reach the green ./capture.sh this script
#                        promises, so a failure is not a missing extra.
#
#   gtk   -> `optional`. Mirrors scripts/cloud-setup.sh, which grades its
#                        equivalent step optional deliberately. Two reasons, and
#                        they are inverses of each other. The capability is
#                        probed BY DOING at step [6/8] -- bd_start_display tests
#                        the DISPLAY, not the installer -- so a host that
#                        already has a usable X server and the typelibs from
#                        elsewhere is fully capable even when this step fails,
#                        and blocking the verdict on it would be a gate firing
#                        on identity, which CLAUDE.md 0 calls a soundness bug in
#                        its own right. And what it costs is bounded and already
#                        named: tests/test_v3_43_80_modules::test_all_modules_import
#                        false-fails, which CLAUDE.md 5 classifies as
#                        environmental. A WARN row is still a capability this
#                        host does NOT have, never a step that passed.
#
# AN EMPTY OR FAILED LOOKUP BLOCKS, FOR EVERY GROUP INCLUDING gtk. Command
# substitution DISCARDS the function's exit status, and the installer given zero
# package arguments exits 0 -- measured here: exit 0, "0 newly installed". So
# the obvious one-liner installs nothing and records OK, which is exactly how an
# installer reports success while installing nothing. Capture first, then refuse.
#
# The grading of that refusal deliberately does NOT follow the group's install
# criticality, because what failed is the DENOMINATOR and not the capability:
# the script does not know what this group's deps ARE, which is a different
# statement from "this host lacks them". Step [2/8] has already hard-exited
# unless the fragment sourced AND both of its functions resolved, so no benign
# explanation is left for a group that answers empty. UNKNOWN is the state this
# file reserves for a step that could not be EVALUATED, and it blocks.
# (cloud-setup.sh records WARN for its own empty-list case because it has no
# such hard exit and genuinely runs on hosts where the fragment is absent.)
install_group() {
    local slug="$1" label="$2" group="$3" kind="$4"
    local pkgs=""

    if ! pkgs="$(bd_system_pkgs "$group")"; then
        pkgs=""
    fi
    if [ -z "$pkgs" ]; then
        record "$label" "UNKNOWN" "bd_system_pkgs $group returned nothing, so this group's package list is unknown; refusing to run the installer on an empty list because that exits 0 having installed nothing"
        return 1
    fi

    echo "  $group: $pkgs"
    # Two intentional word splits: $SUDO is empty when already root, and $pkgs
    # must arrive as one argument per package.
    # shellcheck disable=SC2086
    run_step "$slug" "$label" "$kind" $SUDO apt-get install -y $pkgs || true
    return 0
}

install_group 03b_pkgs_core "system packages (core)" core core     || true
install_group 03c_pkgs_node "system packages (node)" node core     || true
install_group 03d_pkgs_gtk  "system packages (gtk)"  gtk  optional || true
# optional: a box without shellcheck still captures cleanly -- the suite's
# parse gates announce themselves unrunnable rather than reporting OK, so the
# failure mode is a visible absence rather than a false pass.
install_group 03e_pkgs_lint "system packages (lint)" lint optional || true
install_group 03f_pkgs_media "system packages (media)" media optional || true

# ------------------------------------------------------ [4/8] repo install
echo
echo "=== [4/8] repo install (install_linux.sh) ==="
echo "  venv, requirements (pytest + pytest-xdist), optional reqs, playwright"
echo "  chromium, cloakbrowser, vendored rrweb/snapdom, SPA build. Minutes, not"
echo "  seconds."
if [ ! -f "$REPO/install_linux.sh" ]; then
    record "install_linux.sh" "FAIL" "missing at $REPO/install_linux.sh"
else
    # Invoked through `bash` rather than as ./install_linux.sh: the deploy path
    # is an unzip overlay and the executable bit does not always survive it.
    run_step 04_install_linux "install_linux.sh" core bash ./install_linux.sh || true
fi

# ------------------------------------------- [5/8] headless chromium system libs
echo
echo "=== [5/8] headless chromium system libraries ==="
# playwright's own dependency installer. Best effort: on a host where it fails,
# headed/headless chromium may still run, and the capture will say so far more
# precisely than this script can. Recorded either way -- never skipped quietly.
if [ ! -x "$REPO/venv/bin/python" ]; then
    record "playwright system libs" "WARN" "not attempted: no venv at venv/bin/python (step 4 must succeed first)"
else
    # shellcheck disable=SC2086  # $SUDO is empty when already root
    run_step 05_playwright_deps "playwright system libs" optional \
        $SUDO ./venv/bin/python -m playwright install-deps chromium || true
fi

# ------------------------------------------------------------ [6/8] display
echo
echo "=== [6/8] X display :99 ==="
# bd_start_display is called DIRECTLY, never through run_step: run_step pipes
# stdout into tee, which would swallow the value the function echoes. It is
# idempotent and tests the DISPLAY rather than the process name -- `pgrep -x
# Xvfb` is the wrong denominator in both directions, and starting a second
# server on an occupied display is what produced "Fatal server error: Server is
# already active for display 99".
DISPLAY_VALUE=""
if DISPLAY_VALUE="$(bd_start_display :99)"; then
    export DISPLAY="$DISPLAY_VALUE"
    record "X display" "OK" "$DISPLAY_VALUE is active"
else
    DISPLAY_VALUE=""
    record "X display" "WARN" "no X display; tests/test_v3_43_80_modules::test_all_modules_import will false-fail (environmental, not a code regression)"
fi

# ------------------------------------------------- [7/8] gui-parity inventory
echo
echo "=== [7/8] gui-parity inventory regen ==="
#
# THIS IS THE STEP THAT FIXES THE OPERATOR'S FAILING TEST.
#
# tests/test_v3_66_302_gui_parity_reconcile.py::test_shipped_inventory_matches_live_regen_itemset
# compares the ON-DISK reports/gui_parity_inventory.json against a fresh regen.
# That file is BUILD-TIME GENERATED and GITIGNORED (.gitignore "reports/*"), so:
#   * git never delivers a current copy, and `git clean -fd` cannot remove a
#     stale one -- ignored files need -x;
#   * the deploy is an unzip overlay, so a copy built from a tree that differs
#     from the box survives on the box indefinitely.
# A stale copy therefore reads as parity DRIFT and fails the whole suite. It
# showed up as "only-shipped=[] only-regen=['pytest_capture_results']".
# Regenerating here, in the deploy venv, makes shipped-vs-regen match by
# construction. This is the same regen scripts/cloud-setup.sh does in section
# 7c; the point of this file is that the box actually runs it.
if [ ! -x "$REPO/venv/bin/python" ]; then
    record "gui-parity inventory" "UNKNOWN" "no venv at venv/bin/python; the regen that fixes the reconcile gate did NOT run"
elif [ ! -f "$REPO/tools/gui_parity_inventory.py" ]; then
    record "gui-parity inventory" "UNKNOWN" "tools/gui_parity_inventory.py is missing; cannot regenerate"
else
    run_step 07_gui_parity_inventory "gui-parity inventory" core \
        ./venv/bin/python tools/gui_parity_inventory.py || true

    # Exit 0 is NOT sufficient here. The generator wraps its `import
    # bulk_downloader.app` in a bare except and falls back to parsing
    # ENDPOINT_CATALOG.md, writing a DIFFERENT item set and still returning 0.
    # A regen that silently degraded would leave the box failing the same
    # reconcile test with a confidently-wrong artifact. The written JSON records
    # which source it used, so read it back rather than trusting the exit code.
    ROUTE_SOURCE="$(./venv/bin/python -c 'import json
try:
    d = json.load(open("reports/gui_parity_inventory.json"))
except Exception as exc:
    print("UNREADABLE: %s" % exc)
    raise SystemExit(0)
print(d.get("route_source") or "<absent>")' 2>&1)"
    case "$ROUTE_SOURCE" in
        "live url_map")
            record "inventory route source" "OK" "live url_map (app import succeeded)"
            ;;
        *)
            record "inventory route source" "UNKNOWN" "route_source is '${ROUTE_SOURCE:-<no output>}', expected 'live url_map'; the generator exits 0 after falling back to the endpoint catalog, so exit 0 alone does not prove the inventory is app-derived"
            ;;
    esac
fi

# ------------------------------------------------------------- [8/8] verdict
echo
echo "=== [8/8] VERDICT ==="
# Installers exiting 0 is not proof that anything imports. Prove it, and prove
# the thing that imported is the tree in front of us.
IMPORTED=""
DECLARED=""
if [ -x "$REPO/venv/bin/python" ]; then
    IMPORTED="$(./venv/bin/python -c 'import bulk_downloader; print(bulk_downloader.__version__)' 2>/dev/null)" || IMPORTED=""
fi
DECLARED="$(grep -oE '__version__ *= *"[^"]+"' bulk_downloader/__init__.py 2>/dev/null \
            | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')" || DECLARED=""
if [ -n "$IMPORTED" ] && [ "$IMPORTED" = "$DECLARED" ]; then
    record "import check" "OK" "bulk_downloader $IMPORTED matches the tree"
elif [ -z "$IMPORTED" ] || [ -z "$DECLARED" ]; then
    record "import check" "UNKNOWN" "imported '${IMPORTED:-<none>}', tree declares '${DECLARED:-<none>}'; could not compare"
else
    record "import check" "FAIL" "imported '$IMPORTED' but the tree declares '$DECLARED'"
fi

verdict_report() {
    local elapsed=$(( $(date +%s) - START ))
    echo
    printf '  %-26s %-8s %s\n' "STEP" "RESULT" "DETAIL"
    printf '  %-26s %-8s %s\n' "--------------------------" "--------" "--------------------------------"
    printf '%s' "$ROWS" | while IFS='|' read -r label result detail; do
        [ -n "$label" ] || continue
        printf '  %-26s %-8s %s\n' "$label" "$result" "$detail"
    done
    echo
    if [ "$BLOCKING" -eq 1 ]; then
        echo "VERDICT: INCOMPLETE  (${elapsed}s, $WARNS warning(s))"
        echo
        echo "A load-bearing step FAILED or could not be EVALUATED. This host is not"
        echo "ready: do not read a capture from it as evidence about the code until"
        echo "the rows above are clean. Logs: $LOGDIR"
        return 0
    fi
    echo "VERDICT: READY  (${elapsed}s, $WARNS warning(s))"
    echo
    if [ "$WARNS" -gt 0 ]; then
        echo "Read every WARN row before concluding a suite is green. A WARN is a"
        echo "capability this host does NOT have, not a step that passed."
        echo
    fi
    echo "Next:"
    echo "    export DISPLAY=${DISPLAY_VALUE:-:99}"
    # shellcheck disable=SC2016  # not expanding is the point: this line is meant
    # to be pasted verbatim, and $(nproc) is correct on the host that pastes it.
    echo '    ./capture.sh --workers=$(nproc)'
    echo
    echo "Not attempted, by design: the test suite itself. Running it is the"
    echo "operator's gate, and this script provisions the host for it rather than"
    echo "standing in for it."
}

verdict_report | tee "$LOGDIR/00_VERDICT.txt"

# The script's own exit status is the verdict: green host, or a reason.
if [ "$BLOCKING" -eq 1 ]; then
    exit 1
fi
exit 0
