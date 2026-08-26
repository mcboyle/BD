#!/usr/bin/env bash
# capture_instance.sh -- claim the two ports owned by one capture run.
#
# Port numbers are process resources, so a path-only run id is not enough.
# Each selected port is protected by a held flock for the lifetime of the
# capture and is also proved bindable before it is published to callers.

bd_capture_mode() {
  local arg
  for arg in "$@"; do
    if [ "$arg" = "--parallel" ]; then
      printf 'parallel'
      return 0
    fi
  done
  printf 'serial'
}

bd_capture_port_refuse() {
  printf 'CAPTURE-INSTANCE-PORT-REFUSED: %s\n' "$1" >&2
  return 73
}

bd_capture_validate_port() {
  local port="$1"
  case "$port" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "$port" -ge 1024 ] && [ "$port" -le 65535 ]
}

bd_capture_port_is_free() {
  local port="$1"
  local python="${CAPTURE_PORT_PROBE_PYTHON:-venv/bin/python}"
  "$python" -c 'import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
finally:
    s.close()
' "$port" >/dev/null 2>&1
}

bd_capture_prepare_port_lock_root() {
  local root="$1"
  local observed owner mode mode_bits

  if [ -z "${CAPTURE_PORT_LOCK_ROOT:-}" ] && [ ! -d "$root" ]; then
    (umask 077; mkdir -p -- "$root") 2>/dev/null || true
  fi
  observed=$(stat -c '%u:%a:%f' -- "$root" 2>/dev/null) || {
    bd_capture_port_refuse "port-lock directory is unavailable: $root"
    return 73
  }
  IFS=: read -r owner mode mode_bits <<<"$observed"
  if [ $((16#$mode_bits & 0170000)) -ne $((0040000)) ] || \
     [ "$owner" != "$EUID" ] || [ "$mode" != 700 ]; then
    bd_capture_port_refuse \
      "port-lock directory must be a real owner-only directory: $root"
    return 73
  fi
}

bd_capture_open_port_lock() {
  local root="$1" port="$2" destination="$3"
  local path="$root/$port.lock"
  local before after owner mode links mode_bits fd

  if ! stat -c '%d:%i:%u:%a:%h:%f' -- "$path" >/dev/null 2>&1; then
    (umask 077; set -o noclobber; : >"$path") 2>/dev/null || true
  fi
  before=$(stat -c '%d:%i:%u:%a:%h:%f' -- "$path" 2>/dev/null) || return 1
  IFS=: read -r _ _ owner mode links mode_bits <<<"$before"
  if [ $((16#$mode_bits & 0170000)) -ne $((0100000)) ] || \
     [ "$owner" != "$EUID" ] || [ "$mode" != 600 ] || [ "$links" != 1 ]; then
    return 1
  fi
  exec {fd}<>"$path" || return 1
  after=$(stat -Lc '%d:%i:%u:%a:%h:%f' -- "/proc/self/fd/$fd" 2>/dev/null) || {
    exec {fd}>&-
    return 1
  }
  if [ "$after" != "$before" ] || ! flock -n "$fd"; then
    exec {fd}>&-
    return 1
  fi
  printf -v "$destination" '%s' "$fd"
}

bd_capture_release_ports() {
  if [ -n "${CAPTURE_FIXTURE_PORT_LOCK_FD:-}" ]; then
    exec {CAPTURE_FIXTURE_PORT_LOCK_FD}>&-
    CAPTURE_FIXTURE_PORT_LOCK_FD=""
  fi
  if [ -n "${CAPTURE_APP_PORT_LOCK_FD:-}" ]; then
    exec {CAPTURE_APP_PORT_LOCK_FD}>&-
    CAPTURE_APP_PORT_LOCK_FD=""
  fi
}

bd_capture_try_port_pair() {
  local root="$1" app_port="$2" fixture_port="$3"

  CAPTURE_APP_PORT_LOCK_FD=""
  CAPTURE_FIXTURE_PORT_LOCK_FD=""
  bd_capture_open_port_lock "$root" "$app_port" \
    CAPTURE_APP_PORT_LOCK_FD || return 1
  if ! bd_capture_open_port_lock "$root" "$fixture_port" \
      CAPTURE_FIXTURE_PORT_LOCK_FD; then
    bd_capture_release_ports
    return 1
  fi
  if ! bd_capture_port_is_free "$app_port" || \
     ! bd_capture_port_is_free "$fixture_port"; then
    bd_capture_release_ports
    return 1
  fi
  CAPTURE_APP_PORT="$app_port"
  CAPTURE_FIXTURE_PORT="$fixture_port"
  return 0
}

bd_capture_claim_ports() {
  local requested_app="${CAPTURE_APP_PORT:-}"
  local requested_fixture="${CAPTURE_FIXTURE_PORT:-}"
  local root="${CAPTURE_PORT_LOCK_ROOT:-/tmp/bd-capture-${EUID}/ports}"
  local base="${CAPTURE_PORT_BASE:-20000}"
  local span="${CAPTURE_PORT_SPAN:-20000}"
  local seed="${CAPTURE_PORT_SEED:-$$}"
  local index app_port fixture_port

  bd_capture_prepare_port_lock_root "$root" || return 73
  if [ -n "$requested_app" ] || [ -n "$requested_fixture" ]; then
    if [ -z "$requested_app" ] || [ -z "$requested_fixture" ]; then
      bd_capture_port_refuse \
        "CAPTURE_APP_PORT and CAPTURE_FIXTURE_PORT must be supplied together"
      return 73
    fi
    if ! bd_capture_validate_port "$requested_app" || \
       ! bd_capture_validate_port "$requested_fixture" || \
       [ "$requested_app" = "$requested_fixture" ]; then
      bd_capture_port_refuse \
        "explicit ports must be distinct integers in 1024..65535"
      return 73
    fi
    if ! bd_capture_try_port_pair "$root" "$requested_app" "$requested_fixture"; then
      bd_capture_port_refuse \
        "requested app=$requested_app fixture=$requested_fixture pair is owned or busy"
      return 73
    fi
  else
    if ! bd_capture_validate_port "$base" || \
       ! bd_capture_validate_port "$span" || [ "$span" -lt 4 ] || \
       [ $((base + span - 1)) -gt 65535 ]; then
      bd_capture_port_refuse \
        "automatic port range must be within 1024..65535 and span at least four"
      return 73
    fi
    case "$seed" in ''|*[!0-9]*) seed=$$ ;; esac
    index=0
    while [ "$index" -lt "$span" ]; do
      app_port=$((base + ((seed + index * 2) % (span - 1))))
      fixture_port=$((app_port + 1))
      if bd_capture_try_port_pair "$root" "$app_port" "$fixture_port"; then
        break
      fi
      index=$((index + 1))
    done
    if [ -z "${CAPTURE_APP_PORT_LOCK_FD:-}" ]; then
      bd_capture_port_refuse \
        "no free app/fixture pair in $base..$((base + span - 1))"
      return 73
    fi
  fi

  CAPTURE_APP_ORIGIN="http://127.0.0.1:${CAPTURE_APP_PORT}"
  CAPTURE_FIXTURE_ORIGIN="http://127.0.0.1:${CAPTURE_FIXTURE_PORT}"
  export CAPTURE_APP_PORT CAPTURE_FIXTURE_PORT
  export CAPTURE_APP_ORIGIN CAPTURE_FIXTURE_ORIGIN
}

bd_capture_instance_init() {
  case "${CAPTURE_RUN_ID:-}" in
    ''|*[!A-Za-z0-9_.-]*)
      printf 'CAPTURE-INSTANCE-REFUSED: unsafe or empty run id: %s\n' \
        "${CAPTURE_RUN_ID:-<empty>}" >&2
      return 73
      ;;
  esac
  bd_capture_claim_ports || return $?
  CAPTURE_UNIT_TEMPLATE="bulkdownloader-capture@.service"
  CAPTURE_UNIT_INSTANCE="bulkdownloader-capture@${CAPTURE_RUN_ID}.service"
  export CAPTURE_UNIT_TEMPLATE CAPTURE_UNIT_INSTANCE
}
