#!/usr/bin/env bash
# Install and start one transient systemd instance for capture.sh.
set -u
set -o pipefail

usage() {
  echo "usage: $0 start INSTANCE APP_PORT INSTALL_DIR [SECRETS_FILE]" >&2
  echo "       $0 stop INSTANCE" >&2
  exit 2
}

ACTION="${1:-}"
INSTANCE="${2:-}"
case "$INSTANCE" in ''|*[!A-Za-z0-9_.-]*) usage ;; esac

SERVICE_TEMPLATE="bulkdownloader-capture@.service"
SERVICE_INSTANCE="bulkdownloader-capture@${INSTANCE}.service"
UNIT_PATH="${CAPTURE_SERVICE_UNIT_PATH:-/etc/systemd/system/$SERVICE_TEMPLATE}"
RUNTIME_DIR="${CAPTURE_SERVICE_RUNTIME_DIR:-/run/bulkdownloader-capture}"
ENV_PATH="$RUNTIME_DIR/${INSTANCE}.env"

if [ "$ACTION" = "stop" ]; then
  stop_exit=0
  if ! sudo systemctl stop "$SERVICE_INSTANCE"; then
    if systemctl is-active --quiet "$SERVICE_INSTANCE" 2>/dev/null; then
      echo "capture service: $SERVICE_INSTANCE is still active" >&2
      exit 1
    fi
  fi
  sudo rm -f -- "$ENV_PATH" || stop_exit=$?
  sudo systemctl reset-failed "$SERVICE_INSTANCE" >/dev/null 2>&1 || true
  exit "$stop_exit"
fi
[ "$ACTION" = "start" ] || usage

APP_PORT="${3:-}"
INSTALL_DIR="${4:-}"
SECRETS_FILE="${5:-}"
case "$APP_PORT" in ''|*[!0-9]*) usage ;; esac
if [ "$APP_PORT" -lt 1024 ] || [ "$APP_PORT" -gt 65535 ]; then
  usage
fi
case "$INSTALL_DIR" in /*) ;; *) usage ;; esac

APP_DIR="$(dirname "$(readlink -f "$0")")/.."
APP_DIR="$(readlink -f "$APP_DIR")"
RUN_USER="${SUDO_USER:-$(whoami)}"
PYEXE="$APP_DIR/venv/bin/python"

case "$RUN_USER" in *[!A-Za-z0-9_.-]*) usage ;; esac
[[ "$APP_DIR" =~ ^[A-Za-z0-9_./+-]+$ ]] || usage
[ -x "$PYEXE" ] || { echo "capture service: missing interpreter $PYEXE" >&2; exit 1; }
[ -f "$APP_DIR/downloader_ui.py" ] || {
  echo "capture service: missing $APP_DIR/downloader_ui.py" >&2
  exit 1
}

sudo install -d -m 0700 -- "$RUNTIME_DIR" || exit 1
UNIT_TMP="${UNIT_PATH}.${INSTANCE}.tmp"
ENV_TMP="$RUNTIME_DIR/${INSTANCE}.env.tmp"
cleanup_temps() {
  sudo rm -f -- "$UNIT_TMP" "$ENV_TMP" >/dev/null 2>&1 || true
}
trap cleanup_temps EXIT
if ! sudo tee "$UNIT_TMP" >/dev/null <<UNIT
[Unit]
Description=BulkDownloader capture instance %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
EnvironmentFile=${RUNTIME_DIR}/%i.env
ExecStart=/bin/bash -c 'cd "\$BD_CAPTURE_APP_DIR" && exec "\$BD_CAPTURE_PYTHON" "\$BD_CAPTURE_APP_DIR/downloader_ui.py"'
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillMode=mixed
UNIT
then
  echo "capture service: could not write temporary template $UNIT_TMP" >&2
  exit 1
fi
sudo chmod 0644 "$UNIT_TMP" || exit 1
sudo mv -f -- "$UNIT_TMP" "$UNIT_PATH" || exit 1
[ -s "$UNIT_PATH" ] || { echo "capture service: empty template $UNIT_PATH" >&2; exit 1; }

{
  printf 'BD_CAPTURE_APP_DIR=%s\n' "$APP_DIR"
  printf 'BD_CAPTURE_PYTHON=%s\n' "$PYEXE"
  printf 'BD_CAPTURE_INSTANCE=%s\n' "$INSTANCE"
  printf 'BD_HOST=127.0.0.1\n'
  printf 'BD_PORT=%s\n' "$APP_PORT"
  printf 'BD_INSTALL_DIR=%s\n' "$INSTALL_DIR"
  if [ -n "$SECRETS_FILE" ]; then
    printf 'BD_SECRETS_FILE=%s\n' "$SECRETS_FILE"
    printf 'BD_CAPTURE_VAULT=1\n'
  fi
} | sudo tee "$ENV_TMP" >/dev/null || exit 1
sudo chmod 0600 "$ENV_TMP" || exit 1
sudo mv -f -- "$ENV_TMP" "$ENV_PATH" || exit 1

sudo systemctl daemon-reload || exit 1
sudo systemctl restart "$SERVICE_INSTANCE" || exit 1
printf 'capture service: started %s on 127.0.0.1:%s\n' \
  "$SERVICE_INSTANCE" "$APP_PORT"
