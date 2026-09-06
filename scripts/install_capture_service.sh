#!/usr/bin/env bash
# Install and start one transient systemd instance for capture.sh.
set -u
set -o pipefail

usage() {
  echo "usage: $0 start INSTANCE APP_PORT INSTALL_DIR [SECRETS_FILE] [--install-dir PATH]" >&2
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
  sudo systemctl stop "$SERVICE_INSTANCE" || stop_exit=$?
  state_exit=0
  unit_state="$(systemctl is-active "$SERVICE_INSTANCE" 2>&1)" \
    || state_exit=$?
  case "$unit_state" in
    inactive|failed)
      ;;
    active|activating|deactivating|reloading)
      echo "capture service: $SERVICE_INSTANCE is still $unit_state after stop" >&2
      exit 1
      ;;
    *)
      echo "capture service: unit state is UNKNOWN for $SERVICE_INSTANCE" \
           "(state=${unit_state:-<no output>}, is-active exit=$state_exit," \
           "stop exit=$stop_exit); preserving $ENV_PATH" >&2
      exit 1
      ;;
  esac
  sudo rm -f -- "$ENV_PATH" || exit $?
  sudo systemctl reset-failed "$SERVICE_INSTANCE" >/dev/null 2>&1 || true
  exit 0
fi
[ "$ACTION" = "start" ] || usage

APP_PORT="${3:-}"
INSTALL_DIR="${4:-}"
SECRETS_FILE=""
case "$APP_PORT" in ''|*[!0-9]*) usage ;; esac
if [ "$APP_PORT" -lt 1024 ] || [ "$APP_PORT" -gt 65535 ]; then
  usage
fi
case "$INSTALL_DIR" in /*) ;; *) usage ;; esac

APP_DIR="$(dirname "$(readlink -f "$0")")/.."
APP_DIR="$(readlink -f "$APP_DIR")"

# INSTALL_DIR above is capture data; authorize the code tree separately.
INSTALL_DIR_SOURCE=canonical
AUTHORIZED_DIR="${BD_DEPLOY_DIR:-$HOME/BulkDownloader}"
[ -z "${BD_DEPLOY_DIR:-}" ] || INSTALL_DIR_SOURCE=BD_DEPLOY_DIR
shift 4
if [ "$#" -gt 0 ] && [ "$1" != "--install-dir" ]; then
  SECRETS_FILE="$1"
  shift
fi
while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then
        echo "capture service: WorkingDirectory=$APP_DIR INSTALL_DIR_SOURCE=--install-dir"
        echo "capture service: INSTALL-DIR-REFUSED: --install-dir requires PATH" >&2
        exit 1
      fi
      AUTHORIZED_DIR="$2"
      INSTALL_DIR_SOURCE=--install-dir
      shift 2
      ;;
    *)
      echo "capture service: WorkingDirectory=$APP_DIR INSTALL_DIR_SOURCE=$INSTALL_DIR_SOURCE"
      echo "capture service: INSTALL-DIR-REFUSED: unknown argument $1" >&2
      exit 1
      ;;
  esac
done
AUTHORIZED_DIR="$(readlink -f -- "$AUTHORIZED_DIR")"
echo "capture service: WorkingDirectory=$APP_DIR INSTALL_DIR_SOURCE=$INSTALL_DIR_SOURCE"
if [ -z "$APP_DIR" ] || [ "$APP_DIR" != "$AUTHORIZED_DIR" ]; then
  echo "capture service: INSTALL-DIR-REFUSED: $APP_DIR is not the authorized tree $AUTHORIZED_DIR." >&2
  echo "Run from that tree, or explicitly name this tree with BD_DEPLOY_DIR or --install-dir PATH." >&2
  exit 1
fi

# The capture template changes directory inside ExecStart, via this instance's
# environment file; systemd's WorkingDirectory alone does not identify it.
if ! UNIT_PROPERTIES="$(systemctl show "$SERVICE_INSTANCE" --all --property=LoadState,ActiveState,WorkingDirectory 2>&1)"; then
  echo "capture service: UNIT-DIR-UNKNOWN: cannot inspect $SERVICE_INSTANCE: $UNIT_PROPERTIES" >&2
  exit 1
fi
PREINSTALL_STATE="$(printf '%s\n' "$UNIT_PROPERTIES" | sed -n 's/^ActiveState=//p')"
case "$PREINSTALL_STATE" in
  inactive|failed) ;;
  active|activating|reloading|deactivating)
    UNIT_DIR="$(sudo sed -n 's/^BD_CAPTURE_APP_DIR=//p' "$ENV_PATH" 2>/dev/null)"
    case "$UNIT_DIR" in
      /*) UNIT_DIR="$(readlink -f -- "$UNIT_DIR")" ;;
      *) UNIT_DIR= ;;
    esac
    if [ -z "$UNIT_DIR" ]; then
      echo "capture service: UNIT-DIR-UNKNOWN: cannot resolve the code tree from $ENV_PATH" >&2
      exit 1
    fi
    if [ "$UNIT_DIR" != "$APP_DIR" ]; then
      echo "capture service: RUNNING-UNIT-DIR-REFUSED: $SERVICE_INSTANCE belongs to $UNIT_DIR, not $APP_DIR." >&2
      echo "Stop that instance explicitly and review its environment before changing trees." >&2
      exit 1
    fi
    ;;
  *) echo "capture service: UNIT-DIR-UNKNOWN: $SERVICE_INSTANCE state is ${PREINSTALL_STATE:-unavailable}" >&2; exit 1 ;;
esac
RUN_USER="${SUDO_USER:-$(whoami)}"
PYEXE="${CAPTURE_SERVICE_PYTHON:-$APP_DIR/venv/bin/python}"

case "$RUN_USER" in *[!A-Za-z0-9_.-]*) usage ;; esac
[[ "$APP_DIR" =~ ^[A-Za-z0-9_./+-]+$ ]] || usage
case "$PYEXE" in /*) ;; *) usage ;; esac
[[ "$PYEXE" =~ ^[A-Za-z0-9_./+-]+$ ]] || usage
[ -x "$PYEXE" ] || { echo "capture service: missing interpreter $PYEXE" >&2; exit 1; }
[ -f "$APP_DIR/downloader_ui.py" ] || {
  echo "capture service: missing $APP_DIR/downloader_ui.py" >&2
  exit 1
}
command -v curl >/dev/null 2>&1 || {
  echo "capture service: curl is required to verify /api/health" >&2
  exit 1
}
READY_TRIES="${CAPTURE_SERVICE_READY_TRIES:-40}"
case "$READY_TRIES" in ''|*[!0-9]*) usage ;; esac
[ "$READY_TRIES" -gt 0 ] || usage

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
ready=0
last_health_code="000"
last_health_exit=0
attempt=1
while [ "$attempt" -le "$READY_TRIES" ]; do
  state_exit=0
  unit_state="$(systemctl is-active "$SERVICE_INSTANCE" 2>&1)" \
    || state_exit=$?
  case "$unit_state" in
    active)
      health_code=""
      health_exit=0
      health_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 \
          "http://127.0.0.1:${APP_PORT}/api/health" 2>/dev/null)" \
        || health_exit=$?
      [ -n "$health_code" ] || health_code="000"
      last_health_code="$health_code"
      last_health_exit="$health_exit"
      if [ "$health_exit" -eq 0 ] && [ "$health_code" = "200" ]; then
        ready=1
        break
      fi
      ;;
    activating)
      ;;
    inactive|failed|deactivating)
      echo "capture service: $SERVICE_INSTANCE became $unit_state before" \
           "127.0.0.1:$APP_PORT served /api/health" >&2
      exit 1
      ;;
    *)
      echo "capture service: unit state is UNKNOWN for $SERVICE_INSTANCE" \
           "(state=${unit_state:-<no output>}, is-active exit=$state_exit)" >&2
      exit 1
      ;;
  esac
  if [ "$attempt" -lt "$READY_TRIES" ]; then
    sleep 1
  fi
  attempt=$((attempt + 1))
done
[ "$ready" -eq 1 ] || {
  echo "capture service: $SERVICE_INSTANCE is active but /api/health did not" \
       "answer HTTP 200 on 127.0.0.1:$APP_PORT after $READY_TRIES attempts" \
       "(last HTTP $last_health_code, curl exit $last_health_exit)" >&2
  exit 1
}
printf 'capture service: started %s on 127.0.0.1:%s\n' \
  "$SERVICE_INSTANCE" "$APP_PORT"
