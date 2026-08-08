# bdenv.sh -- BulkDL sandbox env + service supervisor.
#
# Sourced automatically by /usr/local/bin/bd. Idempotent. Uses setsid
# so spawned services survive parent shell exit.

# --- env ---
export PATH=/tmp/tools_bin:/tmp/media/tools_bin:/home/claude/.local/node/bin:/home/claude/.local/bin:$PATH
export PYTHONPATH="/tmp/prestaged_site_packages:${PYTHONPATH:-}"
export BD_HOME=/home/claude/bd_home
export BD_DISABLE_KEEPALIVE=1
export DISPLAY=:99
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/home/claude/.cache/ms-playwright}"
export GTK_ROOT=/home/claude/.local/gtk
export LD_LIBRARY_PATH="$GTK_ROOT/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="$GTK_ROOT/usr/lib/x86_64-linux-gnu/girepository-1.0:${GI_TYPELIB_PATH:-}"
export XDG_DATA_DIRS="$GTK_ROOT/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

# --- helper: is a process matching $1 actually running?
# Excludes our own shell + the pgrep itself by matching only the
# *first* word of /proc/PID/cmdline, which is the executable, never
# our bash_tool command line.
_bd_running() {
    local pattern="$1"
    for pid in $(pgrep -f "$pattern" 2>/dev/null); do
        [ "$pid" = "$$" ] && continue
        [ "$pid" = "$BASHPID" ] && continue
        # Read the full cmdline (NUL-separated) and check it contains
        # the pattern in something that *is* the spawned process, not
        # a parent shell. The reliable signal: the cmdline contains
        # python3 OR Xvfb, not /bin/sh or bash.
        local cmd
        cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null)
        case "$cmd" in
            *"/bin/sh -c"*|*"bash -c"*|*"bash -i"*) continue ;;
            *"$pattern"*) return 0 ;;
        esac
    done
    return 1
}

# --- helper: spawn $@ detached (survives parent shell exit) ---
_bdspawn() {
    local logfile="$1"; shift
    setsid "$@" </dev/null >"$logfile" 2>&1 &
}

# --- Xvfb (display :99) ---
if ! _bd_running "Xvfb :99"; then
    _bdspawn /tmp/xvfb.log Xvfb :99 -screen 0 1024x768x24
    sleep 1
fi

# --- Apprise fake webhook receiver (port 8765) ---
APPRISE_BIN=/home/claude/apprise_kit/bin/fake_webhook_server.py
if [ -f "$APPRISE_BIN" ] && ! _bd_running "fake_webhook_server.py"; then
    _bdspawn /tmp/apprise.log python3 "$APPRISE_BIN"
fi

# --- Mock servers (Plex 32400, Jellyfin 8096, Stash 9999) ---
MOCKS_DIR=/home/claude/mocks_kit/bin
if [ -d "$MOCKS_DIR" ]; then
    for mock in plex jellyfin stash; do
        script="$MOCKS_DIR/mock_${mock}.py"
        if [ -f "$script" ] && ! _bd_running "mock_${mock}.py"; then
            _bdspawn /tmp/mock_${mock}.log python3 "$script"
        fi
    done
fi
