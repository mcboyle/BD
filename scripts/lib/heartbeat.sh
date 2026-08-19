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
  if [ -n "${BD_HEARTBEAT_CLOSE_FD:-}" ]; then
    setsid bash -c 'fd=$1; shift; eval "exec ${fd}>&-"; exec "$@"' \
      bd-close-fd-exec "$BD_HEARTBEAT_CLOSE_FD" "$@" > "$logfile" 2>&1 &
  else
    setsid "$@" > "$logfile" 2>&1 &
  fi
  pid=$!
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
