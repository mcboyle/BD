# shellcheck shell=bash
# heartbeat.sh -- run a long command quietly, report progress, and BOUND IT.
#
# WHY THIS LEFT capture.sh. It was inline, so the only thing any test could do
# was grep capture.sh for the string `run_with_heartbeat` -- three tests do
# exactly that today. A source check cannot tell a bound that fires from a
# bound that is written down, which is the distinction this file exists to make
# testable. Same move as scripts/lib/tree_state.sh (@1092) and
# scripts/lib/capture_run_dir.sh (@1099), for the same reason: a test can RUN
# it rather than grep it.
#
# THE DEFECT IT CLOSES, MEASURED 2026-08-13 (backlog 102). The polling loop was
# `while kill -0 "$pid"` with NO time bound, so a pytest master that wedges
# hangs the capture FOREVER. That is not hypothetical: a full suite at -n 48 on
# test6 was caught wedged at run test6-full-005-190725 -- `[gw28] node down:
# Not properly terminated` at 99%, then 726 seconds of silence at loadavg 0.27,
# with the master spinning in xdist's dsession.loop_once because
# `self._active_nodes` never empties and one worker sat unreaped as a zombie.
# It would have run until someone noticed. A capture that hangs writes no
# verdict, and backlog 102's own warning is that a NO-VERDICT RUN IS NOT A
# GREEN ONE: a log with no pytest summary contains no occurrence of the word
# `failed` and reads as clean to anything scanning for it.
#
# THE BOUND IS A WALL CLOCK, NOT A PER-TEST TIMEOUT, and the two are not
# substitutes. `--timeout=240 --timeout-method=thread` runs INSIDE the worker,
# so it cannot fire when the worker is the thing that died -- that is exactly
# why the wedge survives it. This bound lives in the parent, outside the
# process it bounds, because a limit that shares a fate with its subject is not
# a limit.
#
# EXIT 124 IS DELIBERATE: it is coreutils `timeout`'s convention, and
# tools/capture_verdict.py names it rather than reporting a bare number. An
# unfinished run is not a pass and not a failure of the code.

# WHY EVERY LAUNCH BELOW GOES THROUGH `env --default-signal=INT,QUIT`, measured
# 2026-08-23. All seven post-reboot fleet captures failed, and all seven failed
# in the serial lane on the same nine signal-sensitive tests. POSIX requires a
# NON-INTERACTIVE shell to start an ASYNCHRONOUS job with SIGINT and SIGQUIT set
# to SIG_IGN -- and both launch paths here are `... &`. `setsid` changes the
# session, not the dispositions; Python PRESERVES an inherited SIG_IGN instead
# of installing its usual default_int_handler; and every process pytest spawns
# inherits the ignore in turn. A test that sends SIGINT to its own subject
# therefore sends it into a void, and the subject walks down a non-cancellation
# path while the lane still reports a clean exit.
#
# This wrapper's job is to BOUND a lane and narrate it, not to change what the
# lane is. Handing a subject different signal semantics than it would have in
# the foreground makes the wrapper part of the experiment. The reset restores
# the foreground contract and nothing else -- session and process-group
# ownership are deliberately unchanged, because `_stop_process_group` and the
# INT/TERM/HUP traps below depend on the child leading its own group.
#
# BOTH paths need it SEPARATELY. They are two `setsid` call sites, so a reset on
# one leaves every capture that owns a vault descriptor defective while the
# other looks correct. tests/test_v3_66_1208_the_heartbeat_keeps_foreground_
# signal_semantics.py asserts each path on its own for exactly that reason.
#
# BD_HEARTBEAT_LAUNCH NAMES THE CALL SITE, and it is here so a test can assert
# WHICH path ran rather than infer it. The obvious proxy -- the length of
# _CAPTURE_CLOSE_FDS in the caller -- is not the same question: a mutation that
# changed the `-gt 0` branch predicate to `-ge 0` sent the zero-descriptor case
# through the descriptor-closing call site while that length stayed 0, and the
# "ordinary path" test passed having exercised the other branch. The variable
# is inert to the lane and costs one already-present `env`.

# Seconds any one heartbeat-wrapped stage may take before it is stopped.
# Default 5400 (90 min) is ~17x the slowest lane measured on this fleet (a full
# suite at -n 48 completes in 219-315s), so it bounds a hang without ever
# firing on a slow-but-live run -- CLAUDE.md section 0 counts a bound that
# fires on correct work as a soundness bug, not a safe default.
: "${CAPTURE_STAGE_CAP:=5400}"

_stop_process_group() {
  local child_pid="$1"
  local tick=0
  kill -TERM -- "-$child_pid" 2>/dev/null \
    || kill -TERM "$child_pid" 2>/dev/null \
    || true
  while [ "$tick" -lt 10 ] && kill -0 "$child_pid" 2>/dev/null; do
    sleep 1
    tick=$((tick + 1))
  done
  if kill -0 "$child_pid" 2>/dev/null; then
    kill -KILL -- "-$child_pid" 2>/dev/null \
      || kill -KILL "$child_pid" 2>/dev/null \
      || true
  fi
  wait "$child_pid" 2>/dev/null || true
}

_capture_add_close_fd() {
  local value="$1"
  local label="$2"
  local existing
  [ -n "$value" ] || return 0
  case "$value" in
    *[!0-9]*)
      printf '%s: invalid descriptor (decimal descriptor required); ignoring it safely\n' "$label" >&2
      return 0
      ;;
  esac
  for existing in "${_CAPTURE_CLOSE_FDS[@]}"; do
    [ "$existing" != "$value" ] || return 0
  done
  _CAPTURE_CLOSE_FDS+=("$value")
}

_start_capture_detached() {
  local logfile="$1"
  shift
  _CAPTURE_CLOSE_FDS=()
  case "${BD_HEARTBEAT_CLOSE_FD:-}" in
    '') ;;
    *[!0-9]*)
      printf 'run_with_heartbeat: invalid BD_HEARTBEAT_CLOSE_FD (decimal descriptor required); ignoring it safely\n' >&2
      ;;
    *) _capture_add_close_fd "$BD_HEARTBEAT_CLOSE_FD" "run_with_heartbeat" ;;
  esac
  _capture_add_close_fd "${CAPTURE_VAULT_DIR_FD:-}" "capture vault directory"
  _capture_add_close_fd "${CAPTURE_VAULT_DIR_LOCK_FD:-}" "capture vault lock"
  if [ "${#_CAPTURE_CLOSE_FDS[@]}" -gt 0 ]; then
    setsid env --default-signal=INT,QUIT BD_HEARTBEAT_LAUNCH=close-fd bash -c '
      count=$1; shift
      case "$count" in ""|*[!0-9]*) exit 73 ;; esac
      while [ "$count" -gt 0 ]; do
        fd=$1; shift
        case "$fd" in ""|*[!0-9]*) exit 73 ;; esac
        exec {fd}>&- || exit 73
        count=$((count - 1))
      done
      exec "$@"
    ' bd-close-fds-exec "${#_CAPTURE_CLOSE_FDS[@]}" \
      "${_CAPTURE_CLOSE_FDS[@]}" "$@" > "$logfile" 2>&1 &
  else
    setsid env --default-signal=INT,QUIT BD_HEARTBEAT_LAUNCH=ordinary "$@" > "$logfile" 2>&1 &
  fi
  CAPTURE_DETACHED_PID=$!
}

# Keep long commands quiet while reporting elapsed progress once a minute.
# Polling here avoids a second monitor process and delays completion by at most
# one second; the child command's complete output still lands in its artifact.
run_with_heartbeat() {
  local label="$1"
  local logfile="$2"
  shift 2
  local started pid elapsed last_report
  started=$(date +%s)
  last_report=$started
  _start_capture_detached "$logfile" "$@"
  pid="$CAPTURE_DETACHED_PID"
  trap '_stop_process_group "$pid"; trap - INT TERM HUP; exit 130' INT
  trap '_stop_process_group "$pid"; trap - INT TERM HUP; exit 143' TERM
  trap '_stop_process_group "$pid"; trap - INT TERM HUP; exit 129' HUP
  # ONE loop and ONE cap test, polled every second. An earlier draft nested a
  # 60-tick inner loop inside an outer one and tested the cap in BOTH, which a
  # mutation battery showed was not merely redundant: it made two mutants
  # UNREACHABLE. Deleting `-gt 0` (the zero-cap escape hatch) and deleting the
  # elapsed comparison both left a green band, because whichever copy of the
  # condition the mutant touched, the other still decided the outcome. Two
  # copies of a predicate are two things that can disagree, and here they hid
  # each other's failure.
  while kill -0 "$pid" 2>/dev/null; do
    sleep 1
    elapsed=$(($(date +%s) - started))
    if [ "$CAPTURE_STAGE_CAP" -gt 0 ] && [ "$elapsed" -ge "$CAPTURE_STAGE_CAP" ] \
       && kill -0 "$pid" 2>/dev/null; then
      # Say it in the log the stage owns as well as on stdout: the archive is
      # what survives, and a reader of 02_pytest_parallel.log must not have to
      # infer from a missing summary that the run was stopped.
      echo "CAPTURE-STAGE-CAP: $label exceeded ${CAPTURE_STAGE_CAP}s and was stopped (exit 124)" \
        | tee -a "$logfile"
      _stop_process_group "$pid"
      trap - INT TERM HUP
      return 124
    fi
    if [ $(($(date +%s) - last_report)) -ge 60 ] && kill -0 "$pid" 2>/dev/null; then
      last_report=$(date +%s)
      echo "  progress: $label still running (${elapsed}s elapsed)"
    fi
  done
  wait "$pid"
  local command_exit=$?
  trap - INT TERM HUP
  return "$command_exit"
}
