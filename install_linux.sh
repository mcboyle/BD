#!/usr/bin/env bash
# install_linux.sh - set up BulkDownloader on Linux.
# Linux counterpart of install_windows.bat: creates a venv, installs
# requirements.txt (+ requirements-optional.txt if present, one pin per
# line so a dead optional package degrades gracefully), and installs
# the Playwright Chromium browser. Run from the project folder:
#     chmod +x install_linux.sh && ./install_linux.sh
#
# It also makes a BEST-EFFORT attempt at the system (apt) package tier, using
# the shared list in scripts/lib/system_deps.sh. That tier is never fatal and
# never required: an unprivileged operator with no sudo rights gets a printed
# hint and the install continues. Opt out entirely with:
#     BD_SKIP_SYSTEM_DEPS=1 ./install_linux.sh
set -u
set -o pipefail
cd "$(dirname "$(readlink -f "$0")")" || {
    echo "ERROR: could not cd to script directory" >&2
    exit 1
}
INSTALL_DIR="$(pwd)"
VENV_DIR="$INSTALL_DIR/venv"

echo " ================================================================"
echo "  BulkDownloader - Linux install"
echo " ================================================================"

# ── System packages (best-effort; never fatal) ───────────────────────────────
# BD's system-level package lists live in exactly ONE place -
# scripts/lib/system_deps.sh - so this script, scripts/provision_test_host.sh
# and scripts/cloud-setup.sh cannot drift apart about what "the system deps"
# are. Three private copies of a list is three different answers to "are the
# deps present?", and every one of them can report OK while the host is missing
# something another script considers mandatory.
#
# This tier deliberately runs BEFORE the interpreter detection below: apt is
# often what installs the Python this script then looks for.
#
# EVERY path here is non-fatal. This script must stay runnable by an ordinary
# unprivileged user with no sudo rights - the same contract install_windows.bat
# honors - so a missing package degrades to a copy-pasteable hint and the
# install continues. Opt out with BD_SKIP_SYSTEM_DEPS=1.
_sys_pkgs=""
if [ "${BD_SKIP_SYSTEM_DEPS:-0}" = "1" ]; then
    echo "  (system packages skipped: BD_SKIP_SYSTEM_DEPS=1)"
elif [ ! -r "$INSTALL_DIR/scripts/lib/system_deps.sh" ]; then
    echo "  (system packages skipped: scripts/lib/system_deps.sh not present)"
    echo "  This tree predates the shared dependency fragment. The deploy path"
    echo "  is an overlay that never deletes, so an older install can lack it;"
    echo "  the rest of this install is unaffected."
else
    # shellcheck source=scripts/lib/system_deps.sh
    . "$INSTALL_DIR/scripts/lib/system_deps.sh"
    # Sourcing cleanly proves nothing about what the file DEFINED - a fragment
    # that parses fine can still define no functions - so resolve the name.
    if ! declare -F bd_system_pkgs >/dev/null 2>&1; then
        echo "  WARNING: scripts/lib/system_deps.sh defined no bd_system_pkgs;"
        echo "  skipping the system package step. If a step below fails for a"
        echo "  missing library, install BD's system dependencies by hand."
    else
        # Capture FIRST, then refuse to run apt on an empty list. Command
        # substitution discards the non-zero exit, and apt-get install with
        # zero package arguments exits 0 - so an unchecked
        # `apt-get install -y $(bd_system_pkgs all)` would install nothing and
        # report success. An empty denominator reading as OK is the exact
        # failure this fragment exists to prevent.
        _sys_pkgs="$(bd_system_pkgs all)" || _sys_pkgs=""
        if [ -z "$_sys_pkgs" ]; then
            echo "  WARNING: bd_system_pkgs returned no packages; refusing to"
            echo "  run apt with an empty list. Skipping the system package"
            echo "  step - the rest of the install continues."
        elif [ "$(id -u)" = "0" ]; then
            echo "  Installing system packages (root) ..."
            apt-get update -qq \
                || echo "  (apt-get update failed - using the cached lists)"
            # Word splitting is the point: the fragment returns one
            # space-separated list, apt wants one arg each.
            # shellcheck disable=SC2086
            apt-get install -y $_sys_pkgs \
                || echo "  (system package install failed - continuing)"
        elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
            echo "  Installing system packages (sudo) ..."
            sudo -n apt-get update -qq \
                || echo "  (apt-get update failed - using the cached lists)"
            # Word splitting is the point here too.
            # shellcheck disable=SC2086
            sudo -n apt-get install -y $_sys_pkgs \
                || echo "  (system package install failed - continuing)"
        else
            # Not root, and sudo is either absent or would prompt. Prompting
            # here would hang an unattended install, so state the remedy and
            # move on.
            echo "  (system packages skipped: not root, and sudo is not"
            echo "  available non-interactively)"
            echo "  If a step below fails for a missing system library, run:"
            echo "    sudo apt-get install -y $_sys_pkgs"
            echo "  The rest of the install continues either way."
        fi
    fi
fi

# locate a Python interpreter
# Allow the operator to force a specific interpreter via env var, e.g.
# PYTHON_CMD=python3.11 ./install_linux.sh  -- useful on systems where
# `python3` points at an EOL Python but a newer one is installed.
PYTHON_CMD="${PYTHON_CMD:-}"
if [ -n "$PYTHON_CMD" ]; then
    if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
        echo "  ERROR: PYTHON_CMD='$PYTHON_CMD' not found on PATH."
        exit 1
    fi
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi
if [ -z "$PYTHON_CMD" ]; then
    echo "  ERROR: no Python found. Install Python 3.9+ (apt install"
    echo "  python3 python3-venv) and re-run."
    exit 1
fi

# Enforce the minimum Python advertised above (3.9). On older
# systems pip + venv would succeed but installing modern deps
# (flask 3, playwright 1.61+, httpx 0.25+) fails with cryptic
# wheel-resolution errors much later. Better to bail clearly here.
if ! "$PYTHON_CMD" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    PY_VER="$($PYTHON_CMD -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)"
    echo "  ERROR: $PYTHON_CMD is Python $PY_VER; this app needs 3.9+."
    echo "  Install a newer Python (apt install python3.11 or similar)"
    echo "  and re-run with PYTHON_CMD=python3.11 ./install_linux.sh"
    exit 1
fi
echo "  Python: $($PYTHON_CMD --version 2>&1)"

# virtual environment.
# A venv is only usable if it has BOTH python AND pip. On Ubuntu a venv
# created without the python3-venv package present comes out with no
# pip - reusing such a venv fails later with "No module named pip". So
# verify pip, and rebuild the venv from scratch if it is incomplete.
_venv_ok() {
    [ -x "$VENV_DIR/bin/python" ] \
        && "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1
}
if _venv_ok; then
    echo "  Reusing existing venv at $VENV_DIR"
else
    if [ -e "$VENV_DIR" ]; then
        echo "  Existing venv at $VENV_DIR is incomplete (no pip) -"
        echo "  rebuilding it from scratch."
        rm -rf "$VENV_DIR"
    fi
    echo "  Creating virtual environment at $VENV_DIR ..."
    "$PYTHON_CMD" -m venv "$VENV_DIR" || {
        echo "  ERROR: failed to create venv. On Debian/Ubuntu install"
        echo "  the venv + pip packages first. The names come from the shared"
        echo "  fragment, so this command cannot drift out of date:"
        echo "    sudo apt-get install -y \$(. scripts/lib/system_deps.sh; bd_system_pkgs core)"
        exit 1
    }
    if ! _venv_ok; then
        echo "  ERROR: the new venv still has no pip. Install the"
        echo "  Ubuntu packages and re-run:"
        echo "    sudo apt-get install -y \$(. scripts/lib/system_deps.sh; bd_system_pkgs core)"
        exit 1
    fi
fi
VPYTHON="$VENV_DIR/bin/python"

# pip — always upgrade via 'python -m pip', never the pip executable.
# Non-fatal: an offline / air-gapped install can still proceed with the
# venv's bundled pip; we just warn so the operator notices.
#
# Capture stderr so the operator can see WHY the upgrade failed
# (TLS error vs. resolver error vs. proxy issue). Without this they
# get only the friendly "skipped" line and lose the diagnostic.
_pip_upgrade_err=/tmp/bd_pip_upgrade.err
if ! "$VPYTHON" -m pip install --upgrade pip 2>"$_pip_upgrade_err"; then
    echo "  (pip self-upgrade skipped — offline or pinned?)"
    if [ -s "$_pip_upgrade_err" ]; then
        echo "  Last lines of pip stderr:"
        tail -5 "$_pip_upgrade_err" | sed 's/^/    /'
    fi
fi
rm -f "$_pip_upgrade_err"

# core requirements
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    echo "  Installing requirements.txt ..."
    "$VPYTHON" -m pip install -r "$INSTALL_DIR/requirements.txt" || {
        echo "  ERROR: core pip install failed. Check network access"
        echo "  to pypi.org and that your Python version has wheels."
        exit 1
    }
else
    echo "  No requirements.txt; installing pinned core packages ..."
    "$VPYTHON" -m pip install "flask>=3.0" "playwright>=1.61,<2.0" \
        "httpx>=0.25,<1.0" || {
        echo "  ERROR: core pip install failed. Check network access"
        echo "  to pypi.org and that your Python version has wheels."
        exit 1
    }
fi

# optional requirements — one pin per line so a dead package (e.g. a
# PyPI extractor that 404s) degrades gracefully instead of aborting all.
# Tolerates Windows CRLF endings, inline `# comment` suffixes, and
# leading/trailing whitespace — all of which pip rejects with a cryptic
# "Invalid requirement" if passed through raw.
#
# Comment-stripping matches pip's rule (PEP 508 + pip docs): `#` only
# starts a comment when it is at the start of the line OR preceded by
# whitespace. A naive `${line%%#*}` would mangle PEP 508 URL specs like
# `mypkg @ https://x.com/y.tar.gz#sha256=...` by truncating the
# integrity hash.
#
# Not supported here: pip option lines (--index-url, -r nested.txt,
# --find-links, etc). Those belong in requirements.txt where the whole
# file is fed to one `pip install -r` invocation. Putting them in
# requirements-optional.txt will produce "ERROR: Invalid requirement"
# per line — annoying but not catastrophic.
if [ -f "$INSTALL_DIR/requirements-optional.txt" ]; then
    echo "  Installing optional requirements (failures are non-fatal) ..."
    while IFS= read -r line || [ -n "$line" ]; do
        # strip CR (Windows line endings)
        line="${line%$'\r'}"
        # Strip inline comments per pip's rule (PEP 508 + pip docs):
        # `#` starts a comment only when it is at the start of the
        # line OR preceded by whitespace. AND -- crucially -- the
        # FIRST such `#` wins, not the last. Bash regex with [[ =~ ]]
        # is greedy and matches the *last* `#`, which is wrong for
        # lines like `pkg # first # second` (should strip to `pkg`,
        # not `pkg # first`). sed handles this correctly with one
        # external fork per non-empty line, which is fine.
        line="$(printf '%s\n' "$line" | sed -E 's/(^|[[:space:]])#.*$//')"
        # trim leading/trailing whitespace
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [ -z "$line" ] && continue
        "$VPYTHON" -m pip install "$line" \
            || echo "  (skipped optional: $line)"
    done < "$INSTALL_DIR/requirements-optional.txt"
fi

# ── Playwright browsers ─────────────────────────────────────────────────────
#
# The ENGINE NAMES come from scripts/lib/system_deps.sh (bd_playwright_engines),
# never from a literal here. install_linux.sh, scripts/provision_test_host.sh
# and scripts/cloud-setup.sh all install browsers; a private copy of the list in
# each is the same drift that the apt lists above were consolidated to stop.
#
# TWO STEPS, and the split is load-bearing:
#
#   core (chromium)        REQUIRED. Every capture, login and download BD runs
#                          is chromium; nothing in the app asks playwright for
#                          another engine. Its failure gets a loud warning.
#   extra (firefox/webkit) OPTIONAL. Live check L4 stats all three engines and
#                          WARNs when any is absent, so an install that stops
#                          at chromium leaves a correctly provisioned host
#                          warning forever. But a webkit download failing over
#                          a flaky mirror must never be graded like losing
#                          chromium, so it is its own step with its own message.
#
# Running them separately is what buys that grading: one combined
# `playwright install chromium firefox webkit` reports a single exit status, so
# a webkit failure would be indistinguishable from a chromium failure.
# ── Per-user browser state: which user's HOME it lands in ────────────────────
#
# Playwright and CloakBrowser both keep their downloaded browsers in a PER-USER
# directory derived from $HOME. Both were read, not assumed:
#
#   playwright    venv/lib/python3.12/site-packages/playwright/driver/package/
#                 lib/coreBundle.js -- defaultCacheDirectory =
#                 process.env.XDG_CACHE_HOME || path.join(os.homedir(), '.cache');
#                 registryDirectory = <that>/ms-playwright, unless
#                 PLAYWRIGHT_BROWSERS_PATH is set.
#   cloakbrowser  cloakbrowser/config.py -- Path.home() / ".cloakbrowser"
#
# So `sudo ./install_linux.sh` downloaded them into /root/.cache/ms-playwright --
# mode 0700 on a stock Ubuntu, so the service user cannot even traverse it --
# while install_service.sh deliberately writes User=${SUDO_USER:-$(whoami)} into
# the unit, for the reason its own comment gives: "yt-dlp + Playwright running
# as root is a security smell". The deploy SPLITS the installing user from the
# service user on purpose, and this script was the half that did not know. It
# then printed "Installed: chromium" from the download's exit status alone:
# true about root, and useless about the account that runs the app.
#
# Six other tracked .sh files already resolve this same variable the same way
# (install_service.sh, install_remote_teach.sh and the four uninstallers). This
# is that variable, not a new policy.
_bd_run_user="${SUDO_USER:-$(whoami)}"

# Set BEFORE the section, so the closing banner can report it under `set -u`
# even on the branch where bd_playwright_engines is unavailable and nothing is
# installed at all. "unknown" is the correct starting value: on that branch
# nothing was installed and nothing is known.
_pw_reach="unknown"

# bd_as_run_user <cmd> [args...]
#
# Run a per-user browser download as _bd_run_user. A NO-OP when not elevated, or
# when _bd_run_user is already root, so the unprivileged path -- the one this
# script's header contract says must keep working with no sudo rights at all --
# behaves exactly as it did.
#
# ENVIRONMENT, NOT JUST IDENTITY. Two variables decide where the engines land,
# and the two de-escalation tools disagree about BOTH, so neither is left to the
# host's policy. Both behaviours were MEASURED, not assumed:
#
#   XDG_CACHE_HOME            Playwright prefers it over os.homedir(), so
#                             carrying root's value across the switch would send
#                             the engines to root's cache ANYWAY -- a fix that
#                             appears to work and does not. runuser PRESERVES it
#                             (it resets only HOME/SHELL/USER/LOGNAME); sudo's
#                             env_reset drops it. `env -u` drops it explicitly so
#                             the two branches agree. It is dropped ONLY when
#                             crossing a user boundary: an unprivileged
#                             operator's own value is theirs and is left alone.
#   PLAYWRIGHT_BROWSERS_PATH  The OPPOSITE case. When set, the registry is an
#                             explicit machine-wide pool (scripts/cloud-setup.sh
#                             provisions exactly that shape), so dropping it
#                             would move the engines OFF the pool the deployment
#                             resolves. runuser KEEPS it; sudo's env_reset
#                             STRIPS it -- so it is RE-STATED on the env(1)
#                             command line rather than inherited. Without that,
#                             the sudo branch silently reintroduces the very
#                             wrong-cache defect this function exists to fix, and
#                             the reach check below could not see it: it crosses
#                             the same boundary, so it would resolve the same
#                             wrong registry and report "ok".
#
# UNKNOWN IS A THIRD STATE. Elevated with neither runuser nor sudo present, this
# cannot de-escalate. It SAYS SO and runs the command as-is rather than quietly
# filling root's cache under a success message; the verification step below then
# reports the same run as unreachable, so the operator gets one story, not two.
bd_as_run_user() {
    if [ "$(id -u)" != "0" ] || [ "$_bd_run_user" = "root" ]; then
        "$@"
        return $?
    fi
    # ONE env(1) prefix, used by BOTH branches. A variable forwarded in only one
    # of them is a fix that works or not depending on which tool the host
    # happens to have on root's PATH.
    local _bd_env=(env -u XDG_CACHE_HOME)
    if [ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]; then
        _bd_env+=("PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_PATH")
    fi
    if command -v runuser >/dev/null 2>&1; then
        runuser -u "$_bd_run_user" -- "${_bd_env[@]}" "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo -u "$_bd_run_user" -H -- "${_bd_env[@]}" "$@"
    else
        echo "  WARNING: running as root with neither runuser nor sudo, so this"
        echo "  browser download CANNOT be de-escalated to $_bd_run_user. It will"
        echo "  land in root's cache, which the service user cannot read."
        echo "  Re-run this script as $_bd_run_user."
        "$@"
    fi
}

_pw_core=""
_pw_extra=""
if declare -F bd_playwright_engines >/dev/null 2>&1; then
    # Capture FIRST, then refuse on empty - same reasoning as the apt list
    # above, only sharper: `playwright install` with no engine arguments does
    # not fail, it installs every default browser.
    _pw_core="$(bd_playwright_engines core)" || _pw_core=""
    _pw_extra="$(bd_playwright_engines extra)" || _pw_extra=""
fi

if [ -z "$_pw_core" ]; then
    # UNKNOWN is a third state and it is named rather than papered over. This
    # branch is reachable on a tree whose fragment predates bd_playwright_engines
    # and when BD_SKIP_SYSTEM_DEPS=1 skipped the sourcing entirely. Nothing is
    # installed and nothing is claimed: a second, out-of-date copy of the engine
    # list here would be the exact drift the fragment exists to prevent, and a
    # silent skip would let a host reach the end of the install with no browser
    # and no message saying so.
    echo "  WARNING: bd_playwright_engines is unavailable (old or unsourced"
    echo "  scripts/lib/system_deps.sh), so no Playwright browser was"
    echo "  installed. BD cannot log in or download without one. Fix with:"
    echo "    $VPYTHON -m playwright install chromium"
else
    echo "  Installing Playwright browsers ($_pw_core) ..."
    # Word splitting is the point: the fragment returns one space-separated
    # list, playwright wants one argument per engine.
    # shellcheck disable=SC2086
    if bd_as_run_user "$VPYTHON" -m playwright install $_pw_core; then
        echo "  Installed: $_pw_core"
    else
        echo "  WARNING: installing $_pw_core failed. The app needs it"
        echo "  for logins/downloads."
    fi
fi

if [ -n "$_pw_extra" ]; then
    echo "  Installing additional Playwright engines ($_pw_extra) ..."
    echo "  (BD does not launch these; live check L4 audits their presence.)"
    # shellcheck disable=SC2086
    if bd_as_run_user "$VPYTHON" -m playwright install $_pw_extra; then
        echo "  Installed: $_pw_extra"
    else
        echo "  (optional: $_pw_extra not installed - BD runs without them;"
        echo "   live check L4 will report the install as incomplete)"
    fi
fi

# ── Did the engines land where the SERVICE user looks? ───────────────────────
#
# THREE OUTCOMES, and UNKNOWN is one of them.
#
# INSTRUMENT: `playwright install --dry-run <engines>`, run as _bd_run_user. It
# prints one "Install location:" line per artefact, exits 0, touches no network
# and needs no OS libraries -- and, unlike anything in the Python API, its output
# CONTAINS THE HEADLESS SHELL. Measured, playwright 1.61.0:
#
#   $ HOME=/tmp/x venv/bin/python -m playwright install --dry-run chromium
#     Install location:    /tmp/x/.cache/ms-playwright/chromium-1228
#     Install location:    /tmp/x/.cache/ms-playwright/ffmpeg-1011
#     Install location:    /tmp/x/.cache/ms-playwright/chromium_headless_shell-1228
#
# WHY NOT executable_path, WHICH WOULD BE THE OBVIOUS CHOICE. Measured on the
# same empty HOME, playwright 1.61.0:
#
#   p.chromium.executable_path        -> .../chromium-1228/chrome-linux64/chrome
#   p.chromium.launch(headless=True)  -> "Executable doesn't exist at
#       .../chromium_headless_shell-1228/chrome-headless-shell-linux64/..."
#
# DIFFERENT DIRECTORIES. A check that stats executable_path reports the browser
# PRESENT on a tree where headless launch cannot start. Live check L4 stats
# exactly that path, which is why L4 is not a substitute for this step and why
# this step does not reuse its predicate.
#
# WHY NOT LAUNCH. This script PRINTS the `playwright install-deps` hint (just
# below) but does not run it, and scripts/provision_test_host.sh runs it in a
# LATER step. So on the fresh headless Ubuntu this is written for, a launch here
# would routinely fail for a missing libnss3: a real problem, a DIFFERENT one,
# owned by a later step. Grading that as "the engines are in the wrong cache"
# would be the gate crying wolf, and a gate that cries wolf gets switched off.
#
# CORE ONLY, DELIBERATELY. $_pw_extra is graded optional above, precisely so a
# webkit download lost to a flaky mirror never costs the operator chromium.
# Verifying `all` here would report "BD cannot capture, log in or download" on a
# host that is completely fine.
#
# GRADED ON CONTENT, NOT ON $?. MEASURED: `runuser -u <nonexistent>` and
# `sudo -u <nonexistent>` BOTH exit 1 -- the same status the parser below uses
# for "missing". Reading the status alone therefore turns "de-escalation never
# reached the subject" into a DEFINITE missing verdict printed over an EMPTY
# list, and turns an interpreter that exits 0 saying nothing into a definite OK
# over an empty list. Both arms below require the output to be NON-EMPTY;
# everything else is UNKNOWN, which is a failure and not a pass.
if [ -n "$_pw_core" ]; then
    _pw_dry="${TMPDIR:-/tmp}/bd_pw_dryrun.$$"
    # shellcheck disable=SC2086
    bd_as_run_user "$VPYTHON" -m playwright install --dry-run $_pw_core \
        >"$_pw_dry" 2>&1 || :
    _pw_out="$(bd_as_run_user "$VPYTHON" -c 'import os, re, sys
try:
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except OSError:
    raise SystemExit(3)
locs = [m.group(1) for m in
        re.finditer(r"^[ \t]*Install location:[ \t]+(/.*\S)[ \t]*$", text, re.M)]
if not locs:
    raise SystemExit(3)
missing = [p for p in locs if not os.path.isdir(p)]
print("\n".join(missing or locs))
raise SystemExit(1 if missing else 0)' "$_pw_dry")" && _pw_rc=0 || _pw_rc=$?
    if [ "$_pw_rc" = "0" ] && [ -n "$_pw_out" ]; then
        _pw_reach="ok"
        echo "  Verified: $_bd_run_user's Playwright resolves $_pw_core on disk at"
        printf '%s\n' "$_pw_out" | sed 's/^/    /'
    elif [ "$_pw_rc" = "1" ] && [ -n "$_pw_out" ]; then
        _pw_reach="missing"
        echo "  ERROR: $_pw_core is NOT on disk where $_bd_run_user's Playwright"
        echo "  looks for it. Missing:"
        printf '%s\n' "$_pw_out" | sed 's/^/    /'
        echo "  BD cannot capture, log in or download until this is fixed."
        echo "  Re-run this script as $_bd_run_user."
    else
        _pw_reach="unknown"
        echo "  UNKNOWN: could not read an install location out of"
        echo "  'playwright install --dry-run $_pw_core' as $_bd_run_user, so this"
        echo "  install is NOT verified. De-escalation to $_bd_run_user may itself"
        echo "  have failed. Do not read the lines above as proof that"
        echo "  $_bd_run_user can reach a browser."
        tail -5 "$_pw_dry" | sed 's/^/    /'
    fi
    rm -f "$_pw_dry"
fi

# Always print the install-deps hint — on a headless Ubuntu Server,
# the download above succeeds but the browser fails to launch later with
# obscure libnss3/libxkbcommon errors. Better to surface it now. The hint
# covers EVERY engine that was installed, not just chromium: firefox and webkit
# need OS libraries chromium's dependency set does not contain (webkit alone
# pulls the gstreamer stack), so `install-deps chromium` would leave a
# downloaded webkit that cannot start.
echo "  On a headless server you may also need (one-time, as root):"
echo "    sudo $VPYTHON -m playwright install-deps \$(. scripts/lib/system_deps.sh; bd_playwright_engines all)"

# ── CloakBrowser stealth backend (optional; posture-sensitive) ───────────────
# Finding C (v3.66.162 live census): a from-scratch / rebuilt stash venv had
# no `cloakbrowser`, so every browser flow (capture / login / runner /
# keepalive) silently fell back to Playwright. This step puts the dependency
# on the normal install path. It is NON-FATAL and NON-DESTRUCTIVE by design:
# on any failure bulk_downloader/cloak.py's resolve_backend() simply keeps
# using the Playwright install above. This script ONLY provisions the
# dependency and its browser binary — cloak.py owns ALL backend selection and
# launch behavior, so no launch args / proxy / humanize / fingerprint flags
# belong here. cloakbrowser (Requires-Dist: httpx, playwright — both already
# core deps) drives the same Chromium `playwright install chromium` already
# provided; there is no separate engine and no npm layer for the Python
# backend (the cloakbrowser npm package is a separate JS distribution we do
# not use).
#
# Install method, in preference order (first that succeeds wins) — the OFFLINE
# paths are tried BEFORE the network so an air-gapped host never depends on the
# index when a local wheelhouse is present:
#   (a) vendored local wheels:  $INSTALL_DIR/wheels/        (--no-index --find-links)
#   (b) bd_cloak offline pack:  $BD_CLOAK_PACK/pip-wheels/  (--no-index --find-links)
#       (also honors ./bd_cloak_combined_offline_pack and ~/bd_cloak_combined_offline_pack)
#   (c) online package index:   pip install -U cloakbrowser[geoip]
echo
echo "  Installing CloakBrowser stealth backend (optional) ..."
_cloak_req="$INSTALL_DIR/requirements-cloak.txt"
_cloak_done=""

# (a) Vendored local wheelhouse in the tree (fully offline / air-gapped).
if [ -z "$_cloak_done" ] && ls "$INSTALL_DIR"/wheels/cloakbrowser-*.whl >/dev/null 2>&1; then
    echo "  -> offline: vendored wheels in $INSTALL_DIR/wheels/"
    if "$VPYTHON" -m pip install --no-index --find-links "$INSTALL_DIR/wheels" "cloakbrowser[geoip]"; then
        _cloak_done="vendored-wheels"
    else
        echo "  (vendored wheels present but install failed — trying next method)"
    fi
fi

# (b) bd_cloak combined offline pack (its pip-wheels/ is a complete wheelhouse:
#     cloakbrowser + httpx + playwright + geoip extras). Use the wheels rather
#     than the pack's own install_offline.sh so we do NOT pull in the pack's
#     build-time npm layer or cache restore (not needed for the Python backend).
if [ -z "$_cloak_done" ]; then
    for _pack in "${BD_CLOAK_PACK:-}" \
                 "$INSTALL_DIR/bd_cloak_combined_offline_pack" \
                 "$HOME/bd_cloak_combined_offline_pack"; do
        [ -n "$_pack" ] || continue
        if [ -d "$_pack/pip-wheels" ]; then
            echo "  -> offline: bd_cloak pack wheelhouse at $_pack/pip-wheels"
            if "$VPYTHON" -m pip install --no-index --find-links "$_pack/pip-wheels" "cloakbrowser[geoip]"; then
                _cloak_done="bd_cloak-pack"
                break
            else
                echo "  (bd_cloak pack present but install failed — trying next method)"
            fi
        fi
    done
fi

# (c) Online package index (the connected-stash default).
if [ -z "$_cloak_done" ]; then
    echo "  -> online: installing CloakBrowser from the package index"
    if [ -f "$_cloak_req" ]; then
        "$VPYTHON" -m pip install -U -r "$_cloak_req" && _cloak_done="index" \
            || echo "  (online CloakBrowser install failed)"
    else
        "$VPYTHON" -m pip install -U "cloakbrowser[geoip]" && _cloak_done="index" \
            || echo "  (online CloakBrowser install failed)"
    fi
fi

# Pre-download the stealth Chromium binary so the first capture does not block
# on a runtime fetch. Idempotent + non-fatal — CloakBrowser self-provisions on
# first launch if this is skipped.
bd_as_run_user "$VPYTHON" -m cloakbrowser install \
    || echo "  (CloakBrowser browser pre-download skipped — self-provisions on first launch)"
# Report backend status (informational, non-fatal).
bd_as_run_user "$VPYTHON" -m cloakbrowser info \
    || echo "  (cloakbrowser info unavailable)"
# Explicit availability verdict — never let a failed install be mistaken for
# success. NOTE: an import check cannot detect a MISSING RUNTIME BROWSER; use
# the launch-level check in the deploy runbook (cloak.resolve_backend()) to
# confirm the stealth backend is actually live.
if _cloak_path="$("$VPYTHON" -c "import cloakbrowser; print(cloakbrowser.__file__)" 2>/dev/null)"; then
    echo "  CloakBrowser available: $_cloak_path"
else
    echo "  CloakBrowser unavailable; browser flows will fall back to Playwright."
fi

# ── Vendored DOM-capture assets (rrweb + snapdom) — auto-restore if missing ──
# Runtime loads these FROM DISK (bulk_downloader/dom_recorder.py); they are NOT
# pip/npm runtime installs and must NEVER be CDN-loaded. Pinned per
# bulk_downloader/vendor/VENDOR.md: rrweb 2.0.1 -> umd/rrweb.min.js
# (window.rrweb); @zumer/snapdom 2.12.8 -> dist/snapdom.js (window.snapdom).
#
# This step is IDEMPOTENT and NON-DESTRUCTIVE: present + non-empty files are
# left untouched (override with BD_VENDOR_REFRESH=1); only a missing file is
# restored; the online npm path builds in a throwaway temp dir and leaves NO
# node_modules in the tree. Restore order: local/offline source first, then
# (online only) npm at install time.
_rrweb="$INSTALL_DIR/bulk_downloader/vendor/rrweb/rrweb.min.js"
_snapdom="$INSTALL_DIR/bulk_downloader/vendor/snapdom/snapdom.js"

# Offline source roots searched for the pinned relative paths
#   rrweb   -> rrweb/umd/rrweb.min.js
#   snapdom -> @zumer/snapdom/dist/snapdom.js
# Candidates: explicit override, bd_cloak pack node_modules, conventional pack
# locations, a repo-local vendor-source dir. (bd_cloak pack files are verified
# byte-identical to the shipped vendored assets.)
_vendor_roots="
${BD_VENDOR_SRC:-}
${BD_CLOAK_PACK:-}/npm-project/node_modules
$INSTALL_DIR/bd_cloak_combined_offline_pack/npm-project/node_modules
$HOME/bd_cloak_combined_offline_pack/npm-project/node_modules
$INSTALL_DIR/vendor_src
"

# Copy a pinned relative source ($1) -> target ($2) from the first offline root
# that actually has it. Never deletes; only writes the target.
_restore_vendor_offline() {
    _rel="$1"; _dest="$2"
    for _root in $_vendor_roots; do
        [ -n "$_root" ] || continue
        if [ -s "$_root/$_rel" ]; then
            mkdir -p "$(dirname "$_dest")"
            if cp "$_root/$_rel" "$_dest"; then
                echo "    restored $(basename "$_dest") (offline: $_root/$_rel)"
                return 0
            fi
        fi
    done
    return 1
}

# Online npm fallback — INSTALL-TIME ONLY. Builds into a throwaway temp dir,
# copies just the one runtime .js into the vendor tree, then removes the temp
# dir. Leaves no node_modules and creates no runtime dependency on npm/node.
_restore_vendor_npm() {
    _spec="$1"; _rel="$2"; _dest="$3"   # e.g. rrweb@2.0.1  rrweb/umd/rrweb.min.js  <dest>
    command -v npm >/dev/null 2>&1 || { echo "    (npm unavailable; cannot fetch $_spec)"; return 1; }
    _vtmp="$(mktemp -d)"
    if npm install --no-save --no-audit --no-fund --prefix "$_vtmp" "$_spec" >/dev/null 2>&1 \
        && [ -s "$_vtmp/node_modules/$_rel" ]; then
        mkdir -p "$(dirname "$_dest")"
        cp "$_vtmp/node_modules/$_rel" "$_dest"
        rm -rf "$_vtmp"          # throwaway temp dir only — never the deploy tree
        echo "    restored $(basename "$_dest") (npm install-time: $_spec)"
        return 0
    fi
    rm -rf "$_vtmp"
    echo "    (npm fetch failed for $_spec)"
    return 1
}

# Ensure one asset: skip if present+non-empty (unless refreshing), else offline,
# else online npm.
_ensure_vendor_asset() {
    _dest="$1"; _rel="$2"; _spec="$3"
    if [ -s "$_dest" ] && [ "${BD_VENDOR_REFRESH:-0}" != "1" ]; then
        return 0
    fi
    _restore_vendor_offline "$_rel" "$_dest" && return 0
    _restore_vendor_npm "$_spec" "$_rel" "$_dest" && return 0
    return 1
}

if [ -s "$_rrweb" ] && [ -s "$_snapdom" ] && [ "${BD_VENDOR_REFRESH:-0}" != "1" ]; then
    echo "  rrweb/snapdom vendored assets present"
else
    echo "  Restoring vendored DOM-capture assets (rrweb + snapdom) if missing ..."
    _ensure_vendor_asset "$_rrweb"   "rrweb/umd/rrweb.min.js"         "rrweb@2.0.1"          || true
    _ensure_vendor_asset "$_snapdom" "@zumer/snapdom/dist/snapdom.js" "@zumer/snapdom@2.12.8" || true
fi

# Verify after restore — a clear diagnostic; never pretend DOM capture works.
if [ -s "$_rrweb" ] && [ -s "$_snapdom" ]; then
    echo "  Vendored DOM-capture assets OK (rrweb + snapdom, local, offline, no CDN)."
else
    echo "  WARNING: vendored DOM-capture asset(s) STILL missing after restore:"
    [ -s "$_rrweb" ]   || echo "    missing/empty: $_rrweb  (rrweb 2.0.1 umd/rrweb.min.js)"
    [ -s "$_snapdom" ] || echo "    missing/empty: $_snapdom  (@zumer/snapdom 2.12.8 dist/snapdom.js)"
    echo "  DOM capture will be UNAVAILABLE until these are restored. The app"
    echo "  otherwise runs; dom_recorder raises a clear error at capture time."
    echo "  Restore offline (BD_VENDOR_SRC=<bd_cloak_pack>/npm-project/node_modules)"
    echo "  or online with Node/npm present, then re-run this script."
fi

# D3 U1: Frontend SPA build. Non-fatal — install completes even if
# Node is missing. /m2 returns 503 with a clear "Node missing" message
# until this step succeeds. Old /m and / are unaffected either way.
#
# D5 (v3.64.4): if frontend/dist/index.html already exists, skip the
# build entirely. Pairs with `tools/build_release.py --prebuild-spa`
# (D5a, v3.64.3) which produces dist-included zips on a Node-equipped
# machine for fresh-PC installs that don't have Node. To force a
# rebuild from a dist-included install, delete frontend/dist/ before
# re-running this script.
if [ -d "$INSTALL_DIR/frontend" ]; then
    echo
    echo "  Frontend (D3 React SPA at /m2) ..."
    if [ -f "$INSTALL_DIR/frontend/dist/index.html" ]; then
        echo "  Using existing frontend/dist/ (built ahead of install)."
        echo "  To force a rebuild: rm -rf $INSTALL_DIR/frontend/dist"
        echo "  and re-run this script with Node.js 18+ on PATH."
    elif command -v npm >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
        NODE_VER="$(node --version 2>&1)"
        echo "  Node: $NODE_VER"
        # Enforce Node 18+ as the D3 stack uses ESM, top-level await,
        # and Vite 5, which all require it. Without this check the
        # operator gets a cryptic syntax error from `npm run build`.
        # `node --version` prints `v22.22.2` -> strip leading 'v',
        # take major segment.
        NODE_MAJOR="${NODE_VER#v}"
        NODE_MAJOR="${NODE_MAJOR%%.*}"
        SKIP_BUILD="no"
        case "$NODE_MAJOR" in
            ''|*[!0-9]*)
                echo "  WARNING: could not parse Node version from '$NODE_VER';"
                echo "  proceeding anyway. If the build fails with a syntax"
                echo "  error, upgrade to Node 18+."
                ;;
            *)
                if [ "$NODE_MAJOR" -lt 18 ]; then
                    echo "  WARNING: Node $NODE_VER is older than the required"
                    echo "  18.x. Skipping frontend build -- /m2 will return"
                    echo "  503 until you upgrade Node and re-run this script."
                    echo "  The existing UIs at / and /m are unaffected."
                    SKIP_BUILD="yes"
                fi
                ;;
        esac

        if [ "$SKIP_BUILD" = "no" ]; then
            (
                cd "$INSTALL_DIR/frontend" || exit 1
                # Use `npm ci` when a lockfile exists (reproducible),
                # `npm install` otherwise (first build, lockfile gets
                # written for next time). Distinct exit codes so the
                # outer block can give a specific error message.
                if [ -f package-lock.json ]; then
                    npm ci || {
                        echo "  (npm ci failed -- lockfile may be out of sync;"
                        echo "  try 'rm package-lock.json && npm install' to regenerate)"
                        exit 2
                    }
                else
                    npm install || exit 2
                fi
                # Defensive: a leftover deprecated DT stub
                # (@types/testing-library__jest-dom) or a stale tsc -b
                # incremental cache makes the build fail with TS2688. The
                # durable fix is the "types" field in the tsconfigs; this
                # clears the collateral on boxes that still carry the stub.
                rm -f ./*.tsbuildinfo
                rm -rf node_modules/@types/testing-library__jest-dom
                npm run build || exit 3
            )
            FE_RC=$?
            if [ "$FE_RC" -eq 0 ]; then
                echo "  Frontend built: $INSTALL_DIR/frontend/dist/"
            elif [ "$FE_RC" -eq 2 ]; then
                echo "  WARNING: Frontend dependency install failed."
                echo "  /m2 will return 503 until 'cd frontend && npm ci'"
                echo "  succeeds. The existing UIs at / and /m are unaffected."
            elif [ "$FE_RC" -eq 3 ]; then
                echo "  WARNING: Frontend build step (vite/tsc) failed."
                echo "  /m2 will return 503 until 'cd frontend && npm run build'"
                echo "  succeeds. The existing UIs at / and /m are unaffected."
            else
                echo "  WARNING: Frontend build failed (exit $FE_RC)."
                echo "  /m2 will return 503 until the build succeeds. The"
                echo "  existing UIs at / and /m are unaffected."
            fi
        fi
    else
        echo "  Node.js / npm not found — SKIPPING frontend build."
        echo "  /m2 (the new D3 UI) will return 503 with instructions"
        echo "  until you install Node 18+ and re-run this script. The"
        echo "  existing UIs at / and /m are unaffected."
    fi
fi

# ── GUI-parity inventory regen ──────────────────────────────────────────────
# reports/gui_parity_inventory.json is GITIGNORED (.gitignore `reports/*`) AND
# build-time generated. Two consequences that bite together:
#   * no git operation ever delivers a fresh one, and `git clean -fd` cannot
#     remove a stale one (that needs -x);
#   * the deploy path is an unzip overlay - it overwrites and adds but never
#     deletes - so a copy built against a different tree survives on the box.
# tests/test_v3_66_302_gui_parity_reconcile.py compares the SHIPPED inventory
# against a fresh regen, so that stale copy reads as inventory drift and fails
# the whole suite. That is the operator's actual observed failure:
#   "inventory drift - only-shipped=[] only-regen=['pytest_capture_results']"
# Regenerating here, in the deploy venv, makes shipped-vs-regen match by
# construction. Mirrors scripts/cloud-setup.sh section 7c.
#
# Non-fatal: the app runs fine without the report; only the parity gate reads it.
if [ -x "$VPYTHON" ] && [ -f "$INSTALL_DIR/tools/gui_parity_inventory.py" ]; then
    echo
    echo "  Regenerating the GUI-parity inventory ..."
    # The generator wraps its `import bulk_downloader.app` in a bare except and
    # SILENTLY falls back to ENDPOINT_CATALOG.md, still exiting 0 - so exit 0
    # alone does not mean the inventory came from the live route map. Read the
    # route_source field back; a degraded inventory is an unknown, not a pass.
    _parity_err=/tmp/bd_gui_parity_inventory.err
    if ! "$VPYTHON" tools/gui_parity_inventory.py >"$_parity_err" 2>&1; then
        echo "  WARNING: GUI-parity inventory regen failed; any stale copy on"
        echo "  disk is still there and will read as drift in the test suite."
        if [ -s "$_parity_err" ]; then
            tail -5 "$_parity_err" | sed 's/^/    /'
        fi
    # ONE spelling of this predicate now, in capture.sh, install_linux.sh and
    # scripts/provision_test_host.sh: PARSE the JSON, never grep for the
    # substring '"route_source": "live url_map"'. The substring form is hostage
    # to how tools/gui_parity_inventory.py serialises (json.dumps(...,
    # indent=2)) - change the indent or the key separator and the grep starts
    # calling a perfectly good inventory degraded, which is a gate firing on
    # identity. This file carried the STRICT LITERAL (no whitespace tolerance at
    # all) and capture.sh a whitespace-tolerant regex; they agreed by luck, and
    # this one would have gone wrong first. A parse cannot be wrong about
    # formatting. A file that will not parse is NOT a pass: json.load raising
    # exits non-zero and lands in the same branch as a wrong route_source,
    # because both mean "not proven app-derived".
    elif "$VPYTHON" -c 'import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:
    print("gui_parity_inventory.json will not parse: %s" % exc, file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0 if d.get("route_source") == "live url_map" else 1)' \
            "$INSTALL_DIR/reports/gui_parity_inventory.json"; then
        echo "  GUI-parity inventory regenerated from the live route map."
    else
        echo "  WARNING: the GUI-parity inventory was rebuilt WITHOUT the live"
        echo "  route map (the app import failed and it fell back to the"
        echo "  endpoint catalog). The parity gate would compare against a"
        echo "  degraded inventory - re-run this script once the app imports."
    fi
    rm -f "$_parity_err"
fi

echo
echo " ================================================================"
echo "  Install complete."
# The banner is the last thing the operator reads and it used to be
# unconditional, so it survived a completely browserless install intact. It now
# REPEATS the browser verdict rather than restating it, so the two cannot
# disagree. The UNVERIFIED wording deliberately does not point at "the UNKNOWN
# above": _pw_reach is also "unknown" on the bd_playwright_engines-unavailable
# path (BD_SKIP_SYSTEM_DEPS=1), where nothing named UNKNOWN was ever printed.
case "$_pw_reach" in
    ok)      echo "  Browser engines: reachable by $_bd_run_user." ;;
    missing) echo "  Browser engines: NOT reachable by $_bd_run_user - see the"
             echo "                   ERROR above. BD cannot capture or download." ;;
    *)       echo "  Browser engines: UNVERIFIED - no Playwright engine was"
             echo "                   installed or checked; see the messages above." ;;
esac
echo "  Start the app:  ./start_linux.sh"
echo "  Run the tests:  ./run_all_tests.sh"
echo " ================================================================"
