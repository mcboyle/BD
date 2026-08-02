#!/usr/bin/env bash
# install_service.sh - run BulkDownloader as a systemd service so it
# starts on boot, restarts on crash, and survives logout.
#
# Ollama already installs its own systemd service; this covers the
# BulkDownloader app itself. Run from the project folder:
#
#     chmod +x install_service.sh && ./install_service.sh
#
# Afterwards:
#     sudo systemctl status  bulkdownloader
#     sudo systemctl restart bulkdownloader
#     sudo systemctl stop    bulkdownloader
#     journalctl -u bulkdownloader -f          (live logs)
set -u
set -o pipefail

APP_DIR="$(dirname "$(readlink -f "$0")")"

# If invoked via sudo, the service should still run as the operator's
# normal account -- not root. yt-dlp + Playwright running as root is a
# security smell, and the SQLite DB + sites_config.json files would
# become root-owned and unreadable by the same user the next time they
# `cd` in. SUDO_USER carries the original account through `sudo`.
#
# Note: unlike install_remote_teach.sh (which hard-fails on RUN_USER=
# root because it writes per-user files into RUN_HOME), this script
# only writes a single system-level unit file. A legitimate sysadmin
# logged in directly as root on a server may genuinely want the
# service to run as root -- so warn and pause rather than refuse.
RUN_USER="${SUDO_USER:-$(whoami)}"
# Reject usernames with systemd-special chars before we embed them
# in User=. Linux permits some unusual usernames (e.g. with '$' at
# the end, legal per NAME_REGEX) and `useradd --badname` allows even
# stranger ones. Apply the same character allowlist as APP_DIR below.
case "$RUN_USER" in
    *[' %$;'"'\\\""]*)
        echo "  ERROR: RUN_USER contains a character that systemd treats"
        echo "  specially ('$RUN_USER'). Allowed in unit User= fields:"
        echo "  letters, digits, -, _, ."
        exit 1
        ;;
esac
if [ "$RUN_USER" = "root" ]; then
    echo "  WARNING: about to install bulkdownloader to run as root."
    echo "  This is almost certainly NOT what you want -- the app's"
    echo "  data files would be root-owned and the browser would run"
    echo "  with full system privileges. Re-run as your normal user"
    echo "  (with sudo, if needed) to install correctly."
    echo "  Press Ctrl+C in 5s to cancel and re-run as a normal user."
    sleep 5
fi

# Verify RUN_USER exists on THIS host (systemd would otherwise refuse
# to start the unit with "Failed to determine user credentials: User
# foo doesn't exist"). The SUDO_USER env var can carry a username
# from a different system (e.g. shared NFS home, container handoff)
# that doesn't resolve here.
if ! getent passwd "$RUN_USER" >/dev/null 2>&1; then
    echo "  ERROR: user '$RUN_USER' is not in this host's passwd database."
    echo "  If this came from SUDO_USER, it may refer to a user that"
    echo "  exists in the calling shell's environment but not here."
    echo "  Either create the user first, or re-run as a user that exists."
    exit 1
fi

# systemd unit files do not tolerate unescaped spaces in paths used
# by ExecStart= / WorkingDirectory=. Bail clearly rather than write
# a unit that fails to parse at daemon-reload time.
#
# Other systemd-special characters can also corrupt unit-file values:
#   %  -- systemd's variable specifier (e.g. %h = home dir)
#   $  -- environment variable expansion
#   ;  -- command separator
#   \  -- escape character; pathological in raw heredoc
#   '  '" -- quoting that the parser will interpret
# Refuse them outright; legitimate install paths never contain these.
case "$APP_DIR" in
    *[' %$;'"'\\\""]*)
        echo "  ERROR: APP_DIR contains a character that systemd treats"
        echo "  specially ('$APP_DIR')."
        echo "  Allowed: letters, digits, /, -, _, ., +. Move the install"
        echo "  to a path that contains only those characters."
        exit 1
        ;;
esac

SERVICE_NAME="bulkdownloader"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
AI_SERVICE_NAME="bulkdownloader-ai-ready"
AI_UNIT_PATH="/etc/systemd/system/${AI_SERVICE_NAME}.service"

echo " ================================================================"
echo "  BulkDownloader - systemd service install"
echo " ================================================================"
echo "  App directory : $APP_DIR"
echo "  Run as user   : $RUN_USER"

# pick the interpreter: venv if present and functional, else system
# python3. A bare `-x` test passes for a broken symlink (executable
# bit on the link, target gone after a distro Python upgrade), which
# would let systemd write ExecStart= to a python that won't start.
# Probe the venv harder than install_linux.sh's _venv_ok does: the
# minimal `python -c 'import sys'` test passes even when the venv's
# site-packages dir has been orphaned by a system Python major-
# version upgrade (e.g. 3.10 -> 3.12, the lib/python3.10/site-packages
# dir is unreachable from a 3.12 interpreter). At service-start that
# would crash-loop with "ModuleNotFoundError: flask" while pyvenv.cfg
# still claims healthy. Verify a venv-prefixed site-packages dir
# actually exists.
_venv_python_ok() {
    [ -x "$APP_DIR/venv/bin/python" ] || return 1
    "$APP_DIR/venv/bin/python" - <<'PYEOF' >/dev/null 2>&1
import sys, os
sp = [p for p in sys.path
      if p.startswith(sys.prefix) and "site-packages" in p]
if not sp:
    sys.exit(1)
if not any(os.path.isdir(p) for p in sp):
    sys.exit(2)
sys.exit(0)
PYEOF
}
if _venv_python_ok; then
    PYEXE="$APP_DIR/venv/bin/python"
elif [ -x "$APP_DIR/venv/bin/python" ]; then
    # Broken venv. Refuse rather than silently falling back to system
    # python3: the venv carries Flask, Playwright, yt-dlp, and all the
    # site-specific adapters -- system python3 would have none of them
    # and the service would crash-loop with ModuleNotFoundError. Tell
    # the operator to rebuild and exit.
    echo "  ERROR: $APP_DIR/venv/bin/python exists but won't run."
    echo "  (Likely a stale venv after a system Python upgrade,"
    echo "  or a venv built with a Python that has since been removed.)"
    echo "  Rebuild the venv before installing the service:"
    echo "    rm -rf $APP_DIR/venv && cd $APP_DIR && ./install_linux.sh"
    exit 1
elif command -v python3 >/dev/null 2>&1; then
    # No venv at all. Allow system python3 -- but warn loudly that the
    # service will need the app's dependencies installed system-wide.
    # This path is mainly for development boxes; production should use
    # install_linux.sh first.
    PYEXE="$(command -v python3)"
    echo "  WARNING: no venv at $APP_DIR/venv. Will use $PYEXE directly."
    echo "  The service will crash unless Flask + Playwright + yt-dlp"
    echo "  are installed system-wide. Run ./install_linux.sh first"
    echo "  for the recommended setup."
else
    echo "  ERROR: no Python found. Run ./install_linux.sh first."
    exit 1
fi
echo "  Python        : $PYEXE"

if [ ! -f "$APP_DIR/downloader_ui.py" ]; then
    echo "  ERROR: downloader_ui.py not found in $APP_DIR."
    exit 1
fi

# PT11 -- write tools/deployed_version.txt once at install time using the
# same helper systemd runs on every service start. After this, the file
# is refreshed on every `systemctl restart` so it always reflects the
# version that was actually last started -- even after an unzip-over-
# the-top update that bypassed this installer.
HELPER="$APP_DIR/tools/write_deployed_version.sh"
if [ -f "$HELPER" ]; then
    if ! chmod +x "$HELPER" 2>/dev/null; then
        echo "  WARNING: could not chmod +x $HELPER"
        echo "  (read-only filesystem? permission denied?)"
        echo "  Skipping pre-install version write; systemd will retry"
        echo "  on every service start."
    else
        # F3 (v3.66.31): run the helper as RUN_USER, not as the current
        # (possibly root, under sudo) user. ExecStartPre later runs this
        # same helper as User=${RUN_USER}; if the install-time write was
        # done as root, the file is root-owned and the service-start
        # rewrite fails with EACCES (status=1). Matching the owner here
        # keeps the stamp writable on every subsequent start.
        if [ "$(id -u)" = "0" ] && [ "$RUN_USER" != "root" ]; then
            _HELPER_RUN=(sudo -u "$RUN_USER" -- "$HELPER")
        else
            _HELPER_RUN=("$HELPER")
        fi
        if "${_HELPER_RUN[@]}"; then
            echo "  Deployed version recorded -> $APP_DIR/tools/deployed_version.txt"
            # Belt-and-suspenders: if a prior install left a root-owned
            # stamp, hand it back to RUN_USER so future starts can write.
            if [ "$(id -u)" = "0" ] && [ "$RUN_USER" != "root" ]; then
                chown "$RUN_USER" "$APP_DIR/tools/deployed_version.txt" 2>/dev/null || true
            fi
        else
            echo "  WARNING: write_deployed_version.sh failed at install time."
        fi
    fi
else
    echo "  WARNING: $HELPER not found; deployed_version.txt will"
    echo "  be written on first service start instead."
fi

# Build the unit. WorkingDirectory MUST be the app dir: the SQLite DB
# and sites_config.json are resolved relative to the working directory.
# Only the independent AI readiness companion is ordered after Ollama;
# the main app remains available regardless of AI backend readiness.
echo
echo "  Writing $UNIT_PATH (needs sudo)..."
if ! sudo tee "$UNIT_PATH" >/dev/null <<UNIT
[Unit]
Description=BulkDownloader (Flask + Playwright video downloader)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
# Bucket 2 (.env editor): honor an optional ${APP_DIR}/.env for env vars set via
# the GUI Environment editor. The leading "-" makes it optional (no file = no
# error). The app-side loader (bulk_downloader/_envfile) reads the same file even
# without this line; EnvironmentFile keeps env semantics consistent for tools
# systemd spawns outside the app process. Real unit/shell env still wins.
EnvironmentFile=-${APP_DIR}/.env
# PT11 -- re-write tools/deployed_version.txt on every service start
# so the file always reflects the live deployed version, even after
# an unzip-over-the-top update that bypassed this installer.
# ExecStartPre runs before ExecStart; the leading "-" swallows
# nonzero exit so a write failure does NOT block the service --
# version surfacing is a diagnostic, not a precondition for serving.
ExecStartPre=-${APP_DIR}/tools/write_deployed_version.sh
ExecStart=${PYEXE} ${APP_DIR}/downloader_ui.py
Restart=on-failure
RestartSec=5
# give the app time to stop its browser/keeper threads cleanly
TimeoutStopSec=30
KillMode=mixed

[Install]
WantedBy=multi-user.target
UNIT
then
    echo "  ERROR: failed to write $UNIT_PATH"
    echo "  (sudo refused, or /etc/systemd/system/ not writable?)"
    exit 1
fi

# Belt-and-suspenders: `sudo tee` can exit 0 in pathological cases
# (rare; bind-mounted /etc/systemd/system/ on tmpfs that swallows
# writes, write-through filesystems with stale-cache bugs, FUSE
# mounts that lose data) where the destination still doesn't have
# the content. Verify the unit landed and has a non-zero size
# before reloading systemd against it.
if [ ! -s "$UNIT_PATH" ]; then
    echo "  ERROR: $UNIT_PATH is missing or empty after sudo tee."
    echo "  Filesystem may not have honored the write. Check that"
    echo "  /etc/systemd/system/ is on a real filesystem, not a"
    echo "  tmpfs or pathological FUSE mount."
    exit 1
fi

echo "  Writing $AI_UNIT_PATH (needs sudo)..."
if ! sudo tee "$AI_UNIT_PATH" >/dev/null <<UNIT
[Unit]
Description=BulkDownloader AI GPU boot readiness
After=network-online.target ollama.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${APP_DIR}/.env
ExecStart=${PYEXE} -m bulk_downloader.ai_boot_readiness
Restart=on-failure
RestartSec=60
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
UNIT
then
    echo "  ERROR: failed to write $AI_UNIT_PATH"
    exit 1
fi
if [ ! -s "$AI_UNIT_PATH" ]; then
    echo "  ERROR: $AI_UNIT_PATH is missing or empty after sudo tee."
    exit 1
fi

echo "  Reloading systemd..."
sudo systemctl daemon-reload \
    || { echo "  ERROR: systemctl daemon-reload failed"; exit 1; }

echo "  Enabling ${SERVICE_NAME} (start on boot)..."
# Don't swallow enable errors; a malformed unit (e.g. parse error)
# fails here and we want the operator to see why.
_svc_enable_err="$(mktemp)"
if ! sudo systemctl enable "${SERVICE_NAME}" 2>"${_svc_enable_err}"; then
    sed 's/^/    /' "${_svc_enable_err}"
    rm -f "${_svc_enable_err}"
    echo "  ERROR: systemctl enable failed; see output above"
    exit 1
fi
rm -f "${_svc_enable_err}"

echo "  Enabling ${AI_SERVICE_NAME} (start on boot)..."
if ! sudo systemctl enable "${AI_SERVICE_NAME}"; then
    echo "  ERROR: systemctl enable ${AI_SERVICE_NAME} failed"
    exit 1
fi

echo "  Starting ${SERVICE_NAME}..."
sudo systemctl restart "${SERVICE_NAME}" \
    || { echo "  ERROR: initial restart failed; check journalctl"; exit 1; }

echo "  Starting ${AI_SERVICE_NAME} in the background..."
if ! sudo systemctl restart "${AI_SERVICE_NAME}"; then
    echo "  WARNING: ${AI_SERVICE_NAME} did not become ready."
    echo "  AI readiness will retry after boot; BulkDownloader remains available."
fi

# Poll up to 15s for the service to settle. Flask + Playwright +
# Ollama-probe on cold start can easily take 5-10s; the previous
# hard-coded `sleep 2` reported "did not come up" on slower boxes
# that were actually fine. Also distinguish 'failed' from 'activating'
# so a true crash is reported promptly without waiting the full window.
SERVICE_STATE="unknown"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    SERVICE_STATE="$(systemctl is-active "${SERVICE_NAME}" 2>/dev/null || true)"
    case "$SERVICE_STATE" in
        active)     break ;;
        failed)     break ;;   # crashed during start; no point waiting
        *)          sleep 1 ;;
    esac
done

# v3.66.836: `active` on a Type=simple unit means the process was SPAWNED,
# not that it is serving -- systemd reports it the moment exec succeeds,
# while Flask/waitress still has to bind. capture.sh records a window in
# which exactly this is false. Certifying RUNNING off is-active alone tells
# the operator a service is up when nothing answers on the port, so ask the
# app itself. Failure here is reported, never fatal: the installer's job is
# to tell the truth about the service, not to gate on it.
SERVING="unknown"
if [ "$SERVICE_STATE" = "active" ]; then
    # BD_PORT is a GUI-editable key delivered via EnvironmentFile=-${APP_DIR}/.env,
    # so honour the environment first and that file second before the default.
    PROBE_PORT="${BD_PORT:-}"
    if [ -z "$PROBE_PORT" ] && [ -r "${APP_DIR}/.env" ]; then
        PROBE_PORT="$(sed -n 's/^[[:space:]]*BD_PORT[[:space:]]*=[[:space:]]*//p' \
                         "${APP_DIR}/.env" | tail -n 1 | tr -d '"'"'"' \r')"
    fi
    [ -n "$PROBE_PORT" ] || PROBE_PORT=5555
    if command -v curl >/dev/null 2>&1; then
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
            if curl -sSf --max-time 2 \
                 "http://127.0.0.1:${PROBE_PORT}/api/health" >/dev/null 2>&1; then
                SERVING="yes"
                break
            fi
            sleep 1
        done
        [ "$SERVING" = "yes" ] || SERVING="no"
    fi
fi

echo
echo " ================================================================"
if [ "$SERVICE_STATE" = "active" ] && [ "$SERVING" = "yes" ]; then
    echo "  ${SERVICE_NAME} is RUNNING and enabled on boot."
    echo "  (serving on 127.0.0.1:${PROBE_PORT})"
elif [ "$SERVICE_STATE" = "active" ] && [ "$SERVING" = "unknown" ]; then
    echo "  WARNING: ${SERVICE_NAME} is active, but whether it is SERVING is"
    echo "  unverified -- curl is not installed, so the health endpoint could"
    echo "  not be probed. Check by hand:"
    echo "    curl -sS http://127.0.0.1:${PROBE_PORT}/api/health"
elif [ "$SERVICE_STATE" = "active" ]; then
    echo "  WARNING: ${SERVICE_NAME} is active but is NOT serving:"
    echo "  nothing answered http://127.0.0.1:${PROBE_PORT}/api/health"
    echo "  within 15s. The process started; the app did not come up. Check:"
    echo "    sudo systemctl status ${SERVICE_NAME}"
    echo "    journalctl -u ${SERVICE_NAME} -n 50"
elif [ "$SERVICE_STATE" = "failed" ]; then
    echo "  ERROR: ${SERVICE_NAME} entered the 'failed' state. Check:"
    echo "    sudo systemctl status ${SERVICE_NAME}"
    echo "    journalctl -u ${SERVICE_NAME} -n 50"
else
    echo "  WARNING: ${SERVICE_NAME} did not become active within 15s"
    echo "  (current state: $SERVICE_STATE). Check:"
    echo "    sudo systemctl status ${SERVICE_NAME}"
    echo "    journalctl -u ${SERVICE_NAME} -n 50"
fi
echo
echo "  Manage it with:"
echo "    sudo systemctl status  ${SERVICE_NAME}"
echo "    sudo systemctl restart ${SERVICE_NAME}"
echo "    sudo systemctl stop    ${SERVICE_NAME}"
echo "    journalctl -u ${SERVICE_NAME} -f"
echo " ================================================================"
