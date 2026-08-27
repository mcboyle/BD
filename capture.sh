#!/usr/bin/env bash
# capture.sh — fresh-machine validation for BulkDownloader
#
# Runs a 9-step diagnostic against ~/BulkDownloader, writes all output
# under /tmp/bd_capture/, and bundles the result into a tarball you can
# upload back to Claude for analysis.
#
# Maintenance edit (capture.sh-only; NOT a release cut — no version bump):
#   - step [0] now clears __pycache__/*.pyc before any step, so the suite,
#     the CSRF import and the freshly-installed service can never validate
#     stale bytecode after a bare overlay (the v3.66.161 stale-.pyc footgun)
#   - step [7] probe `sse_smoke` corrected to `sse_status` — `sse_smoke` is
#     not a registered route, so it returned {"error":"endpoint not found"}
#     on every run; the real dev SSE diagnostic is /api/dev/sse_status
#   - --workers=N is parsed and forwarded to real pytest via pytest-xdist
#     (was hardcoded --workers=4, so `./capture.sh --workers=90` was a no-op);
#     unknown args (e.g. a bare --summary) are accepted and ignored
#   - private capture fixtures remain opt-in. Callers can export the absolute
#     roots documented by capture_test_fixtures.py; step [2] inherits them.
#     No private path is autodetected.
#   - new step [2a] regenerates reports/gui_parity_inventory.{json,md} AFTER
#     the systemd service is stopped and confirmed inactive, and BEFORE the
#     pytest lanes. That file is gitignored yet SHIPS in the zip, so a stale
#     copy from an earlier overlay deploy outlives `git clean -fd` (ignored
#     files need -x) and the reconcile gate read it as inventory drift,
#     failing the whole suite. Regenerating makes the gate compare
#     fresh-vs-fresh. The ordering against the service stop is not cosmetic:
#     the generator does `import bulk_downloader.app`, whose module body
#     unconditionally runs db_init(), db_integrity_check() and five
#     scheduler/thread starters, so run before the stop it put a second
#     process on the live BD_HOME database. It is never fatal: it records
#     PARITY_EXIT (4 = skipped because the service was still up), warns
#     loudly, and hands the code to the verdict.
#
# Updated for v3.63.6:
#   - capture.sh now ships in the release zip (was previously stash-local,
#     so KB-documented fixes had to be hand-applied after every deploy)
#   - step [3] CSRF diag uses module-level `app` (the create_app factory
#     does not exist; the canonical shape is `from bulk_downloader.app
#     import app`)
#   - step [6] LIVE_IDS now includes L3 (mv3-extension-service-worker);
#     the v3.63.5 extension fix (channel="chromium" + the three icon
#     PNGs) means L3 PASSes cleanly — it is no longer a known wedge
#
# Earlier change history:
#   v3.63.3:
#     - dropped step [6] live_check.py (file removed from repo)
#     - step [6] now runs live_tests with --per-check-timeout 90
#     - capture dir renamed /tmp/v3_63_1_capture -> /tmp/bd_capture
#     - explicit deactivate of the service before suite, restart after,
#       to avoid the port-5555 collision

set -u  # don't set -e: we want all 9 steps to run even if one errors

# ── An inherited install-dir override poisons the whole suite ────────────────
#
# MEASURED TWICE, and it costs a whole capture each time.
#
#   2026-08-09  the operator was told to run ad-hoc probes with an exported
#               install-dir override, ran `export` in the interactive shell,
#               and ./capture.sh inherited it: 89 tests failed -- 13 "database
#               is locked", a UNIQUE violation, `assert 10 == 1`. A clean
#               re-run was 12833 passed. 12744 + 89 = 12833: EVERY failure was
#               the variable.
#   2026-08-10  the same shape reproduced six MOD3 Postgres failures, and was
#               reconstructed to the digit (an "empty" source migrating exactly
#               the 7 rows two earlier files had inserted).
#
# WHY: db._resolve_db_path() prefers this variable over the cwd, so ONE value
# makes every test in the run share ONE SQLite history database. That defeats
# the per-test cwd isolation tests/conftest.py's autouse isolated_bd_home
# provides. Fresh per invocation, SHARED within it. There is no legitimate way
# to run the suite with it set process-wide, which is why this REFUSES rather
# than unsetting it quietly: a silent fix would hide a broken shell that will
# poison the operator's next ad-hoc probe too.
#
# It fails in the first second, before any work, not fifteen minutes in.
if [ -n "${BD_INSTALL_DIR:-}" ]; then
  cat >&2 <<EOF
capture.sh REFUSING TO RUN: BD_INSTALL_DIR is set in this shell.

    BD_INSTALL_DIR=${BD_INSTALL_DIR}

It is inherited by every test in the run, and db._resolve_db_path() prefers it
over the working directory -- so all ~15000 tests share ONE SQLite history
database and the per-test isolation conftest provides is defeated. Measured
consequence: 89 false failures in one capture (13 "database is locked", a
UNIQUE violation, assert 10 == 1). The clean re-run passed 12833.

Fix, then re-run:

    unset BD_INSTALL_DIR && ./capture.sh $*

For a one-shot probe, PREFIX it instead of exporting it:

    BD_INSTALL_DIR="\$(mktemp -d)" venv/bin/python -c '...'
EOF
  exit 2
fi

BD_HOME="${BD_HOME:-$HOME/BulkDownloader}"

# ── where this run writes (backlog 5) ────────────────────────────
# This was a FIXED /tmp/bd_capture, so consecutive captures overwrote each
# other in place and the evidence from a failing round survived only if
# somebody copied it out before the next one started. That happened twice by
# hand on 2026-08-13, and the round that FAILED was the one that would have
# been most expensive to lose.
#
# PRUNE ON THE WAY IN, NOT ON THE WAY OUT. A run that crashes leaves the most
# valuable directory on the box; pruning at the end would delete it as part of
# the failure. Pruning here means the newest few always survive -- including a
# crashed one -- until that many more runs have happened.
#
# The predicate lives in a library so a test can RUN it rather than grep it:
# "keep the newest five" has an off-by-one, an mtime-versus-name ordering
# question and a does-it-delete-the-current-run question, none of which are
# visible in source text.
# `env --default-signal` is what keeps a heartbeat-wrapped lane able to OBSERVE
# a signal at all (scripts/lib/heartbeat.sh). Without it every lane still runs,
# and every signal-sensitive test in it fails for a reason that has nothing to
# do with the code under test.
#
# THIS CHECK IS DELIBERATELY THE FIRST THING CAPTURE DOES. An earlier placement
# sat AFTER classified-root garbage collection and evidence pruning, so a host
# that could not run a valid capture would still have destroyed old evidence
# before refusing. A refusal that deletes evidence on the way to saying no is
# not a refusal.
#
# IT RESOLVES THE EXTERNAL `env` ON PURPOSE. The runtime path is
# `setsid env ...`, which necessarily execs a PATH binary; a shell FUNCTION
# named env could satisfy a bare `env` here and pass a host whose real binary
# then returns 125 on every lane. tools/capture_verdict.py names 124 and not
# 125, so that failure would reach the operator as a bare number three stages
# later. --default-signal arrived in coreutils 8.31 (2019).
_bd_env_bin="$(command -v -- env 2>/dev/null || true)"
case "$_bd_env_bin" in
  /*) ;;
  *) echo "FATAL: no external env binary on PATH; heartbeat-wrapped lanes cannot" >&2
     echo "       preserve foreground signal semantics" >&2; exit 2 ;;
esac
if ! "$_bd_env_bin" --default-signal=INT,QUIT true >/dev/null 2>&1; then
  echo "FATAL: $_bd_env_bin lacks --default-signal, which heartbeat-wrapped lanes" >&2
  echo "       need to keep foreground signal semantics (coreutils 8.31+); found:" >&2
  echo "       $("$_bd_env_bin" --version 2>&1 | head -1)" >&2
  exit 2
fi
unset _bd_env_bin

# RE-EXEC ONCE IF THIS SCRIPT ITSELF WAS HANDED IGNORED SIGNALS.
#
# A shell CANNOT un-ignore a signal it inherited as SIG_IGN at startup: `trap`
# silently does nothing, so every trap capture.sh and run_with_heartbeat
# install never arms, and a stopped lane outlives its wrapper. Measured at
# 674f98d9: 4 lane processes still alive with the wrapper gone, and nothing
# said so. scripts/lib/heartbeat.sh now ANNOUNCES that state, but announcing is
# not fixing, and the only place it CAN be fixed is here -- before the traps
# that depend on it exist.
#
# `env --default-signal` resets a CHILD's dispositions, so the repair is to
# become that child exactly once. Capture is launched detached by design
# (nohup, ssh without a tty, systemd, a CI runner), so this is the ordinary
# case and not an exotic one.
#
# BOUNDED TO ONE HOP by an exported guard: if the re-exec somehow failed to
# clear the ignores, a second attempt would loop forever, and a capture that
# spins is worse than one that runs unarmed. The guard is checked before the
# probe, so a second pass falls through and proceeds -- heartbeat.sh's
# CAPTURE-HEARTBEAT-UNARMED line then names what could not be repaired.
#
# Placed AFTER the env capability check above, because re-execing through an
# `env` that rejects the option would replace a clear refusal with exit 125.
if [ -z "${BD_CAPTURE_SIGNAL_REEXEC:-}" ]; then
  # Read the kernel's view rather than bash's: SigIgn in /proc/self/status is a
  # 64-bit mask where signal N is bit N-1. INT=2, QUIT=3, TERM=15, HUP=1.
  _bd_sigign="$(awk '/^SigIgn:/ {print $2}' /proc/self/status 2>/dev/null)"
  if [ -n "${_bd_sigign:-}" ] && [ "$(( 0x$_bd_sigign & 0x4007 ))" -ne 0 ]; then
    echo "capture.sh: inherited ignored signals (SigIgn=0x$_bd_sigign); re-execing" >&2
    echo "            once through env --default-signal so stage bounds can arm" >&2
    export BD_CAPTURE_SIGNAL_REEXEC=1
    exec env --default-signal=INT,QUIT,TERM,HUP "$0" "$@"
  fi
  unset _bd_sigign
fi

# shellcheck source=scripts/lib/capture_run_dir.sh
. "$(dirname "$0")/scripts/lib/capture_run_dir.sh"
# shellcheck source=scripts/lib/capture_instance.sh
. "$(dirname "$0")/scripts/lib/capture_instance.sh"
CAPTURE_KEEP="${CAPTURE_KEEP:-5}"
bd_test_root_gc "$(dirname "$0")" || true
bd_capture_prune "$CAPTURE_KEEP" || true
CAPTURE_RUN_ID="$(bd_capture_run_id "$(dirname "$0")")"
OUT="/tmp/bd_capture-${CAPTURE_RUN_ID}"
ARCHIVE="/tmp/bd_capture-${CAPTURE_RUN_ID}.tar.gz"
CAPTURE_MODE="$(bd_capture_mode "$@")" || exit $?

# ── Arg parsing ──────────────────────────────────────────────────
# Before this edit capture.sh ignored ALL args, so `./capture.sh
# --workers=90` silently ran the hardcoded --workers=4. Now --workers
# is forwarded to step [2]'s pytest-xdist run. --parallel opts into per-run
# service and port ownership; without it, the v3.66.1191 singleton path stays
# unchanged. Unknown flags (e.g. a bare --summary) are accepted and ignored
# for backward compatibility.
WORKERS=4
while [ $# -gt 0 ]; do
  case "$1" in
    --workers=*) WORKERS="${1#*=}" ;;
    --workers)   shift; WORKERS="${1:-4}" ;;
    --parallel)  ;;
    *)           ;;   # ignore unknown args (back-compat)
  esac
  shift
done

case "$WORKERS" in
  ''|*[!0-9]*) echo "FATAL: --workers must be a positive integer" >&2; exit 2 ;;
esac
if [ "$WORKERS" -lt 1 ]; then
  echo "FATAL: --workers must be at least 1" >&2
  exit 2
fi
if ! command -v setsid >/dev/null 2>&1; then
  echo "FATAL: setsid is required for safe worker cleanup" >&2
  exit 2
fi

# ── working tree must be safe to measure against ─────────────────
# Step [2b] rebuilds the source graph and compares it to a pin written at
# deploy time, so an edited tree fails that gate and capture_verdict.py grades
# the WHOLE run FAIL -- twice, over a green suite. Ask now, when it costs a
# second, rather than forty minutes in. The predicate lives in a library
# because backlog 35 asks the same question before a commit.
# shellcheck source=scripts/lib/tree_state.sh
. "$(dirname "$0")/scripts/lib/tree_state.sh"
_tree_rc=0
bd_tree_state_check "$(dirname "$0")" || _tree_rc=$?
if [ "$_tree_rc" -eq 1 ]; then
  echo "FATAL: refusing to capture against an unclean tree." >&2
  exit 2
elif [ "$_tree_rc" -ne 0 ]; then
  # UNKNOWN is NOT the hazard, and refusing on it was wrong. The measured
  # failure is uncommitted edits drifting the graph pin -- which cannot happen
  # where there is no repository to be uncommitted against. Refusing here also
  # broke thirteen existing tests that build a synthetic capture directory,
  # which is the honest signal that a non-repo run is a legitimate shape.
  echo "WARNING: tree state UNKNOWN -- continuing. Only a DIRTY tree is refused." >&2
fi

# ── record the tree we are about to measure (backlog 100) ────────
# The check above is PREFLIGHT ONLY, so it cannot see the failure it was written
# for: a tree that goes dirty DURING the run passes it untouched. That is what
# invalidated test5 at the 1082 round -- nine files edited mid-run, the graph pin
# drifted against a tree collection had already read, and a green suite was
# graded FAIL. Snapshot now; compare at the end.
#
# DELIBERATELY OUTSIDE THE CAPTURE_ALLOW_DIRTY BRANCH ABOVE. That override means
# "this tree is dirty and I meant it" -- consent to a KNOWN state at t=0. A tree
# that shifts underneath a run in progress is a different event, and an override
# covering both would re-open this hole for anyone who sets the flag by habit.
_TREE_SNAPSHOT=""
_TREE_SNAPSHOT_RC=0
_TREE_SNAPSHOT="$(bd_tree_state_snapshot "$(dirname "$0")")" || _TREE_SNAPSHOT_RC=$?
if [ "$_TREE_SNAPSHOT_RC" -ne 0 ]; then
  # UNKNOWN, and it must NOT be silently treated as "no drift" -- two unreadable
  # snapshots compare equal, which is the section 0 failure this check exists to
  # prevent. Record that the question went unasked; the comparison is skipped.
  echo "WARNING: tree snapshot UNKNOWN -- mid-run drift will NOT be detected." >&2
fi

# ── capture vault (optional, prompted once) ──────────────────────
#
# This step stops the service and step [4] starts a FRESH process, and the
# master key is in-memory only -- so the vault is necessarily LOCKED when the
# seeder runs at [5a], and an operator unlocking beforehand cannot survive the
# restart. L6/L8 were unsatisfiable here no matter what the operator did.
#
# So the capture gets its OWN vault, holding only the fixture's published test
# credential. The operator's secrets.json is never opened. The password is
# asked for ONCE, here at t=0 rather than 35 minutes in at [5a], so the run
# stays unattended after a single keystroke.
#
# It is deliberately never written down. capture.sh:739 tars the whole of
# $OUT and the operator ships that bundle to third parties -- so the vault
# lives OUTSIDE $OUT and the password reaches curl on stdin, never in argv
# (which /proc publishes to every user on the box).
#
# Blank, or no TTY, means skip: L6/L8 then WARN exactly as they do today.
# A prompt that blocked an unattended run would turn a capture into a hang.
CAPTURE_VAULT=0
CAPTURE_VAULT_LOCK_FD=""
CAPTURE_VAULT_GLOBAL_DIR_FD=""
CAPTURE_VAULT_DIR_FD=""
CAPTURE_VAULT_DIR_LOCK_FD=""
CAPTURE_VAULT_FD_PATH=""
CAPTURE_INSTANCE_CLAIMED=0
CAPTURE_INSTANCE_STARTED=0
CAPTURE_INSTANCE_TEARDOWN_EXIT=0
CAPTURE_INSTALL_DIR=""
CAPTURE_LEGACY_SINGLETON=1
# DO NOT CLOBBER AN INHERITED VALUE. This line was a bare
# CAPTURE_VAULT_PW="" and it ran BEFORE the branch that reads the
# variable, so `CAPTURE_VAULT_PW=x ./capture.sh` was wiped to empty and
# the unattended path could never fire. Measured on test5 @1065: the run
# printed "no TTY and no CAPTURE_VAULT_PW" with the variable set in its
# own environment. Initialising a variable and honouring an inherited one
# are different operations, and `:-` is the difference.
CAPTURE_VAULT_PW="${CAPTURE_VAULT_PW:-}"
CAPTURE_VAULT_DIR="/tmp/bd_capture_vault-${CAPTURE_RUN_ID:-$$}"
CAPTURE_VAULT_FILE="$CAPTURE_VAULT_DIR/secrets.json"
CAPTURE_VAULT_DROPIN="/etc/systemd/system/bulkdownloader.service.d/20-capture-vault.conf"

capture_vault_claim() {
  local lock="${CAPTURE_VAULT_GLOBAL_LOCK:-/tmp/bd-capture-${EUID}/capture-vault.lock}"
  local lock_dir="${lock%/*}"
  local lock_name="${lock##*/}"
  local lock_fd_path dir_before dir_after file_before file_after
  local dev ino owner mode links mode_bits
  local holder="unknown"

  capture_vault_global_refuse() {
    echo "CAPTURE-VAULT-CONCURRENCY-REFUSED: $1: $lock" >&2
    [ -z "$CAPTURE_VAULT_LOCK_FD" ] || { exec {CAPTURE_VAULT_LOCK_FD}>&-; CAPTURE_VAULT_LOCK_FD=""; }
    [ -z "$CAPTURE_VAULT_GLOBAL_DIR_FD" ] || { exec {CAPTURE_VAULT_GLOBAL_DIR_FD}>&-; CAPTURE_VAULT_GLOBAL_DIR_FD=""; }
    return 73
  }

  case "$lock" in
    /*) ;;
    *) capture_vault_global_refuse "singleton lock path must be absolute"; return 73 ;;
  esac
  case "$lock_name" in
    ''|.|..) capture_vault_global_refuse "singleton lock name is invalid"; return 73 ;;
  esac

  if [ -z "${CAPTURE_VAULT_GLOBAL_LOCK:-}" ] && [ ! -d "$lock_dir" ]; then
    (umask 077; mkdir -- "$lock_dir") 2>/dev/null || true
  fi
  dir_before=$(stat -c '%d:%i:%u:%a:%f' -- "$lock_dir" 2>/dev/null) || {
    capture_vault_global_refuse "singleton lock directory is unavailable"
    return 73
  }
  IFS=: read -r dev ino owner mode mode_bits <<<"$dir_before"
  if [ $((16#$mode_bits & 0170000)) -ne $((0040000)) ] || \
     [ "$owner" != "$EUID" ] || [ "$mode" != 700 ]; then
    capture_vault_global_refuse "singleton lock directory must be a real owner-only directory"
    return 73
  fi
  if ! exec {CAPTURE_VAULT_GLOBAL_DIR_FD}<"$lock_dir/."; then
    capture_vault_global_refuse "cannot bind singleton lock directory"
    return 73
  fi
  dir_after=$(stat -Lc '%d:%i:%u:%a:%f' -- "/proc/self/fd/$CAPTURE_VAULT_GLOBAL_DIR_FD" 2>/dev/null) || {
    capture_vault_global_refuse "cannot verify singleton lock directory descriptor"
    return 73
  }
  if [ "$dir_after" != "$dir_before" ]; then
    capture_vault_global_refuse "singleton lock directory identity changed"
    return 73
  fi

  lock_fd_path="/proc/self/fd/$CAPTURE_VAULT_GLOBAL_DIR_FD/$lock_name"
  if ! stat -c '%d:%i:%u:%a:%h:%f' -- "$lock_fd_path" >/dev/null 2>&1; then
    (umask 077; set -o noclobber; : >"$lock_fd_path") 2>/dev/null || true
  fi
  file_before=$(stat -c '%d:%i:%u:%a:%h:%f' -- "$lock_fd_path" 2>/dev/null) || {
    capture_vault_global_refuse "singleton lock file is unavailable"
    return 73
  }
  IFS=: read -r dev ino owner mode links mode_bits <<<"$file_before"
  if [ $((16#$mode_bits & 0170000)) -ne $((0100000)) ]; then
    capture_vault_global_refuse "singleton lock must be a regular file"
    return 73
  fi
  if [ "$owner" != "$EUID" ] || [ "$mode" != 600 ] || [ "$links" != 1 ]; then
    capture_vault_global_refuse "singleton lock must be owner-only with one name"
    return 73
  fi
  if ! exec {CAPTURE_VAULT_LOCK_FD}<>"$lock_fd_path"; then
    capture_vault_global_refuse "cannot open singleton lock descriptor"
    return 73
  fi
  file_after=$(stat -Lc '%d:%i:%u:%a:%h:%f' -- "/proc/self/fd/$CAPTURE_VAULT_LOCK_FD" 2>/dev/null) || {
    capture_vault_global_refuse "cannot verify singleton lock descriptor"
    return 73
  }
  if [ "$file_after" != "$file_before" ]; then
    capture_vault_global_refuse "singleton lock identity changed during open"
    return 73
  fi
  exec {CAPTURE_VAULT_GLOBAL_DIR_FD}>&-
  CAPTURE_VAULT_GLOBAL_DIR_FD=""

  if ! flock -n "$CAPTURE_VAULT_LOCK_FD"; then
    read -r holder _ <&"$CAPTURE_VAULT_LOCK_FD" || holder="unknown"
    case "$holder" in ''|*[!0-9]*) holder="unknown" ;; esac
    echo "CAPTURE-VAULT-CONCURRENCY-REFUSED: another capture owns the singleton service/drop-in; holder_pid=$holder; inspect with: ps -fp $holder; fuser $lock" >&2
    exec {CAPTURE_VAULT_LOCK_FD}>&-
    CAPTURE_VAULT_LOCK_FD=""
    return 73
  fi
  if ! printf '%-32s\n' "$$" >&"$CAPTURE_VAULT_LOCK_FD"; then
    echo "CAPTURE-VAULT-CONCURRENCY-REFUSED: cannot publish holder pid to $lock" >&2
    exec {CAPTURE_VAULT_LOCK_FD}>&-; CAPTURE_VAULT_LOCK_FD=""
    return 73
  fi
  BD_HEARTBEAT_CLOSE_FD="$CAPTURE_VAULT_LOCK_FD"
  export BD_HEARTBEAT_CLOSE_FD
}

capture_vault_setup_refuse() {
  local why="$1"
  local removed=0
  echo "CAPTURE-VAULT-SETUP-REFUSED: $why; refusing to write secrets: $CAPTURE_VAULT_DIR" >&2
  [ -z "$CAPTURE_VAULT_DIR_LOCK_FD" ] || { exec {CAPTURE_VAULT_DIR_LOCK_FD}>&-; CAPTURE_VAULT_DIR_LOCK_FD=""; }
  if [ -n "$CAPTURE_VAULT_DIR_FD" ] && [ -x venv/bin/python ] && \
     venv/bin/python toolchain/bin/bd-gc --finish-capture-vault \
       "$CAPTURE_VAULT_DIR" --owned-fd "$CAPTURE_VAULT_DIR_FD" >/dev/null 2>&1; then
    removed=1
  fi
  [ -z "$CAPTURE_VAULT_DIR_FD" ] || { exec {CAPTURE_VAULT_DIR_FD}>&-; CAPTURE_VAULT_DIR_FD=""; }
  if [ "$removed" != 1 ]; then
    echo "CAPTURE-VAULT-SETUP-PRESERVED: identity-bound cleanup unavailable or refused; public pathname untouched" >&2
  fi
  exit 73
}

capture_vault_open_dir() {
  exec {CAPTURE_VAULT_DIR_FD}<"$CAPTURE_VAULT_DIR/."
}

capture_vault_open_lock() {
  exec {CAPTURE_VAULT_DIR_LOCK_FD}<>"$CAPTURE_VAULT_FD_PATH/.bd-capture-vault.lock"
}

capture_vault_dir_claim() {
  local dir_created dir_opened dir_public lock_before lock_after
  local dev ino owner mode links mode_bits
  if ! (umask 077; mkdir "$CAPTURE_VAULT_DIR"); then
    echo "CAPTURE-VAULT-OWNERSHIP-REFUSED: keyed vault already exists: $CAPTURE_VAULT_DIR" >&2
    exit 73
  fi
  dir_created=$(stat -c '%d:%i:%u:%a:%f' -- "$CAPTURE_VAULT_DIR" 2>/dev/null) || \
    capture_vault_setup_refuse "cannot record created directory identity"
  capture_vault_open_dir || capture_vault_setup_refuse "directory descriptor open failed"
  CAPTURE_VAULT_FD_PATH="/proc/self/fd/$CAPTURE_VAULT_DIR_FD"
  dir_opened=$(stat -Lc '%d:%i:%u:%a:%f' -- "$CAPTURE_VAULT_FD_PATH" 2>/dev/null) || \
    capture_vault_setup_refuse "cannot verify directory descriptor identity"
  if [ "$dir_opened" != "$dir_created" ]; then
    capture_vault_setup_refuse "created directory identity changed during descriptor open"
  fi
  chmod 700 "$CAPTURE_VAULT_FD_PATH" || capture_vault_setup_refuse "chmod 0700 failed"
  dir_public=$(stat -c '%d:%i:%u:%a:%f' -- "$CAPTURE_VAULT_DIR" 2>/dev/null) || \
    capture_vault_setup_refuse "keyed vault public identity became unavailable"
  if [ "$dir_public" != "$dir_created" ]; then
    capture_vault_setup_refuse "keyed vault public identity changed after descriptor bind"
  fi
  if ! (umask 077; set -o noclobber; : > "$CAPTURE_VAULT_FD_PATH/.bd-capture-vault.lock") 2>/dev/null; then
    capture_vault_setup_refuse "lock creation failed"
  fi
  lock_before=$(stat -c '%d:%i:%u:%a:%h:%f' -- \
    "$CAPTURE_VAULT_FD_PATH/.bd-capture-vault.lock" 2>/dev/null) || \
    capture_vault_setup_refuse "lock identity unavailable"
  IFS=: read -r dev ino owner mode links mode_bits <<<"$lock_before"
  if [ $((16#$mode_bits & 0170000)) -ne $((0100000)) ] || \
     [ "$owner" != "$EUID" ] || [ "$mode" != 600 ] || [ "$links" != 1 ]; then
    capture_vault_setup_refuse "lock must be an owner-only regular file with one name"
  fi
  capture_vault_open_lock || capture_vault_setup_refuse "lock descriptor open failed"
  lock_after=$(stat -Lc '%d:%i:%u:%a:%h:%f' -- \
    "/proc/self/fd/$CAPTURE_VAULT_DIR_LOCK_FD" 2>/dev/null) || \
    capture_vault_setup_refuse "cannot verify lock descriptor identity"
  if [ "$lock_after" != "$lock_before" ]; then
    capture_vault_setup_refuse "lock identity changed during descriptor open"
  fi
  flock -n "$CAPTURE_VAULT_DIR_LOCK_FD" || capture_vault_setup_refuse "lock acquisition failed"
  dir_public=$(stat -c '%d:%i:%u:%a:%f' -- "$CAPTURE_VAULT_DIR" 2>/dev/null) || \
    capture_vault_setup_refuse "keyed vault public identity unavailable after lock setup"
  if [ "$dir_public" != "$dir_created" ]; then
    capture_vault_setup_refuse "keyed vault public identity changed during lock setup"
  fi
  # The service opens secrets and metadata through the capture process's held
  # directory descriptor. A later pathname substitution therefore cannot
  # redirect credential bytes into a replacement directory.
  CAPTURE_VAULT_FILE="/proc/$$/fd/$CAPTURE_VAULT_DIR_FD/secrets.json"
}

# The proven operator path stays serial by default. Only an explicit
# --parallel selects a template instance and a distinct port pair. The
# default expansion also keeps the executable v3.66.1191 vault seam faithful
# when that block is driven independently of argument parsing.
case "${CAPTURE_MODE:-serial}" in
  parallel) CAPTURE_LEGACY_SINGLETON=0 ;;
  serial) capture_vault_claim || exit 73 ;;
esac

# AN UNATTENDED CAPTURE MUST REACH THE SAME CHECKS AS AN ATTENDED ONE (@1064).
#
# This block used to gate solely on `[ -t 0 ]`, so every capture launched by
# ssh, nohup, systemd or cron skipped L6/L8 -- the same shape as the mod3 DSN
# above: a capability the box HAS, invisible to the automated gate, with the
# verdict still reading PASS. An operator comparing an attended run against an
# unattended one on the same host was comparing two different denominators.
#
# The password is taken from CAPTURE_VAULT_PW when there is no TTY. It is
# deliberately NOT defaulted to a literal in this file: section 7 records that
# a file naming a credential becomes a place that credential lives, and
# gitleaks scans the PR range. Unset means skip, exactly as a blank prompt does,
# so the default behaviour is unchanged.
#
# The name carries no BD_ prefix on purpose -- section 4: a BD_-prefixed name
# enters test_gui_parity's config-surface ledger, and this is a capture-time
# argument, not a runtime config key.
if [ -n "${CAPTURE_VAULT_PW:-}" ]; then
  CAPTURE_VAULT=1
  echo "  capture vault ENABLED from CAPTURE_VAULT_PW -- the operator vault is not opened"
elif [ -t 0 ]; then
  printf 'Capture-vault password (blank = skip the L6/L8 login checks): ' >&2
  read -rs CAPTURE_VAULT_PW
  printf '\n' >&2
  if [ -n "$CAPTURE_VAULT_PW" ]; then
    CAPTURE_VAULT=1
    echo "  capture vault ENABLED -- the operator vault is not opened"
  else
    echo "  capture vault skipped -- L6/L8 will WARN as before"
  fi
else
  echo "  no TTY and no CAPTURE_VAULT_PW: capture vault skipped -- L6/L8 will WARN"
fi

if [ "$CAPTURE_LEGACY_SINGLETON" = "0" ]; then
  # The descriptor-owned directory is the complete per-run application state,
  # not only the optional credential file. Separate systemd instances that
  # shared downloader_history.db would still delete one another's seeded rows.
  capture_vault_dir_claim
  CAPTURE_INSTANCE_CLAIMED=1
  mkdir "$CAPTURE_VAULT_FD_PATH/state" || \
    capture_vault_setup_refuse "per-run application state creation failed"
  CAPTURE_INSTALL_DIR="/proc/$$/fd/$CAPTURE_VAULT_DIR_FD/state"
  bd_capture_instance_init || exit $?
else
  CAPTURE_APP_ORIGIN="http://localhost:5555"
  CAPTURE_FIXTURE_ORIGIN="http://127.0.0.1:8899"
  CAPTURE_FIXTURE_PORT=8899
  CAPTURE_UNIT_INSTANCE="bulkdownloader.service"
  CAPTURE_INSTALL_DIR="$BD_HOME"
fi

# `systemctl restart` returns once systemd has STARTED the unit, NOT once the
# app is serving: waitress needs roughly three more seconds to bind :5555. A
# boot journal shows "Started bulkdownloader.service" at 19:00:17 and
# "[waitress] Serving on http://0.0.0.0:5555" at 19:00:20. Steps [7] and [9]
# curl that port as soon as the teardown below returns, so with no wait they
# meet a REFUSED connection -- seen on a real capture as
#   curl: (7) Failed to connect to localhost port 5555 after 0 ms
# on BOTH ("after 0 ms" is a refusal, not a timeout), which turned an otherwise
# fine run into dev-tools exit=1; http-smoke exit=1 and CAPTURE VERDICT: FAIL.
#
# BOUNDED, deliberately: an unbounded wait would convert a restart that never
# comes back into a hung capture, which is strictly worse than the two failed
# steps it replaces. 40 attempts x 0.5s is ~20s of waiting against the ~3s
# actually needed; each probe is itself capped by --max-time 2, so even if the
# port accepts and then stalls on every attempt the loop cannot run past ~100s.
#
# AND NEVER SILENT: on timeout it warns and leaves SERVICE_READY_EXIT=1, which
# the verdict reads as a stage exit. A probe that never saw the app serving
# does not know that it is, and capture.sh does not abort mid-run by design --
# so the verdict is the only place an unknown can fail (CLAUDE.md 0).
#
# The probe uses the SAME origin steps [7] and [9] use. Polling 127.0.0.1 while
# they use localhost would certify a socket they may never reach.
CAPTURE_READY_URL="${CAPTURE_APP_ORIGIN}/api/health"
CAPTURE_READY_TRIES=40
SERVICE_READY_EXIT=0
wait_for_service_ready() {
  local tries=0
  local started
  started=$(date +%s)
  while [ "$tries" -lt "$CAPTURE_READY_TRIES" ]; do
    if curl -sSf -o /dev/null --max-time 2 "$CAPTURE_READY_URL" 2>/dev/null
    then
      # Deliberately does NOT reset to 0. SERVICE_READY_EXIT is plumbed to
      # the capture verdict as a stage exit, and this helper now has two call
      # sites -- step [4] before the vault unlock, and the vault restore after
      # [6]. A plain assignment would let a later success erase an earlier
      # failure, so the capture would report a clean stage over an unlock that
      # had fired into a closed socket. Once the service was not known to be
      # serving at a point where the capture ACTED on it, that is true for the
      # run.
      echo "  service serving after $(( $(date +%s) - started ))s"
      return 0
    fi
    sleep 0.5
    tries=$((tries + 1))
  done
  SERVICE_READY_EXIT=1
  echo "  WARNING: no answer from $CAPTURE_READY_URL after" \
       "$CAPTURE_READY_TRIES attempts over $(( $(date +%s) - started ))s;" \
       "steps [7]-[9] are about to probe an app that is not known to be" \
       "serving" >&2
  return 1
}

# Removing the drop-in is NOT enough: systemd passes Environment= at start, so
# the RUNNING service keeps the capture vault until something restarts it. Skip
# the restart and the operator's BD would keep reporting an empty credential
# store -- their real passwords would look like they had vanished. Idempotent,
# because it is reached both explicitly after [6] and from the EXIT trap.
#
# The readiness wait lives INSIDE this branch, not beside the call site after
# [6]: a capture with a blank password or no TTY never wrote a drop-in and
# never restarts anything, so it must not pay for a wait. Keeping the two
# together makes it impossible to restart without waiting, or to wait without
# having restarted -- reachability derived rather than asserted.
#
# It therefore also runs on the EXIT-trap path, where nothing later needs the
# app. That is wanted, not tolerated: an interrupt is exactly when the operator
# most needs to know their BD came back off the capture vault, and the wait
# ends the moment it answers.
cleanup_capture_vault() {
  if [ "$CAPTURE_LEGACY_SINGLETON" = "1" ] && [ "$CAPTURE_VAULT" = "1" ]; then
    CAPTURE_VAULT=0
    CAPTURE_VAULT_PW=""
    sudo rm -f "$CAPTURE_VAULT_DROPIN" 2>/dev/null || true
    sudo systemctl daemon-reload 2>/dev/null || true
    sudo systemctl restart bulkdownloader 2>/dev/null || true
    if [ -n "$CAPTURE_VAULT_DIR_LOCK_FD" ]; then
      exec {CAPTURE_VAULT_DIR_LOCK_FD}>&-
      CAPTURE_VAULT_DIR_LOCK_FD=""
    fi
    if [ -n "$CAPTURE_VAULT_DIR_FD" ] && \
       venv/bin/python toolchain/bin/bd-gc \
         --finish-capture-vault "$CAPTURE_VAULT_DIR" \
         --owned-fd "$CAPTURE_VAULT_DIR_FD"; then
      echo "  descriptor-owned capture vault removed"
    else
      echo "  WARNING: capture vault removal refused; preserved for bounded forensics: $CAPTURE_VAULT_DIR" >&2
    fi
    if [ -n "$CAPTURE_VAULT_DIR_FD" ]; then
      exec {CAPTURE_VAULT_DIR_FD}>&-
      CAPTURE_VAULT_DIR_FD=""
    fi
    echo "  service restarted on the operator vault"
    wait_for_service_ready || true
  fi
}

cleanup_capture_instance() {
  local cleanup_exit=0
  [ "$CAPTURE_LEGACY_SINGLETON" = "0" ] || return 0
  if [ "$CAPTURE_INSTANCE_STARTED" = "1" ]; then
    if "$BD_HOME/scripts/install_capture_service.sh" stop "$CAPTURE_RUN_ID" \
        >> "$OUT/04_service_install.log" 2>&1; then
      CAPTURE_INSTANCE_STARTED=0
    else
      cleanup_exit=$?
      echo "  WARNING: capture instance stop failed; state and vault preserved: $CAPTURE_UNIT_INSTANCE" >&2
      return "$cleanup_exit"
    fi
  fi
  CAPTURE_VAULT_PW=""
  if [ -n "$CAPTURE_VAULT_DIR_LOCK_FD" ]; then
    exec {CAPTURE_VAULT_DIR_LOCK_FD}>&-
    CAPTURE_VAULT_DIR_LOCK_FD=""
  fi
  if [ "$CAPTURE_INSTANCE_CLAIMED" = "1" ]; then
    CAPTURE_INSTANCE_CLAIMED=0
    if [ -n "$CAPTURE_VAULT_DIR_FD" ] && \
       venv/bin/python toolchain/bin/bd-gc \
         --finish-capture-vault "$CAPTURE_VAULT_DIR" \
         --owned-fd "$CAPTURE_VAULT_DIR_FD"; then
      echo "  descriptor-owned capture instance removed"
    else
      echo "  WARNING: capture instance removal refused; preserved for bounded forensics: $CAPTURE_VAULT_DIR" >&2
      [ "$cleanup_exit" -ne 0 ] || cleanup_exit=74
    fi
  fi
  if [ -n "$CAPTURE_VAULT_DIR_FD" ]; then
    exec {CAPTURE_VAULT_DIR_FD}>&-
    CAPTURE_VAULT_DIR_FD=""
  fi
  bd_capture_release_ports
  return "$cleanup_exit"
}

release_capture_singleton() {
  if [ -n "$CAPTURE_VAULT_LOCK_FD" ]; then
    exec {CAPTURE_VAULT_LOCK_FD}>&-
    CAPTURE_VAULT_LOCK_FD=""
  fi
}

# Armed HERE, not down in the seed section, because the drop-in is written at
# step [4] and an EXIT trap registered after that leaves a window in which an
# interrupt strands it -- BD would restart on the CAPTURE vault and the
# operator's real credentials would look like they had vanished. That window
# was 109 lines wide and covered steps [5] and [5a]; it was found on the box
# minutes after this feature shipped, by an interrupt that happened to land
# before the drop-in was written rather than after.
#
# bash keeps ONE EXIT trap per shell, so this aggregate is also how the seed
# teardown is reached: a second `trap ... EXIT` further down would REPLACE
# this one rather than add to it. cleanup_live_seed is defined much later, so
# it is called only once it exists -- an interrupt before that point has no
# seed state to remove, but may well have a drop-in to clear. Ordering is
# load-bearing: the seed teardown talks to the app over HTTP, and
# cleanup_capture_vault restarts the service, so the seed half must go first.
cleanup_all() {
  if declare -F cleanup_live_seed >/dev/null 2>&1; then
    cleanup_live_seed
  fi
  cleanup_capture_instance || true
  cleanup_capture_vault
  release_capture_singleton
  bd_capture_release_ports
}
trap cleanup_all EXIT

# shellcheck source=scripts/lib/heartbeat.sh
. "$(dirname "$0")/scripts/lib/heartbeat.sh"

# Which tree did this bundle grade? Nothing else in the archive answers that:
# the banner goes to stdout and only $OUT is tarred, and 09_http_smoke.log
# carries a sha only when the service stage ran, so a capture that dies at
# step [2] used to carry no identity at all.
#
# THE WALK-UP TRAP, and it is the reason for the toplevel comparison. `git
# rev-parse HEAD` searches UPWARD, so when BD_HOME sits below some other
# checkout this would emit a valid-looking sha about a DIFFERENT tree. A
# confident wrong answer is worse than the honest silence it replaces, so
# compare the repository root against the directory we actually ran in and
# refuse to report rather than report something plausible.
#
# `source` is reported for the same reason app_health.build_identity reports
# it: a fallback is indistinguishable from a live read unless it says so.
# Never wired to --stage-exit -- a non-git tree is a legitimate way to run
# this script, and gating the release verdict on it would fail for a reason no
# code change can fix.
emit_commit_identity() {
  local sha top branch when state here
  here="$(pwd -P)"
  if ! sha="$(git rev-parse HEAD 2>/dev/null)" || [ -z "$sha" ]; then
    echo "commit    : UNKNOWN (not a git work tree; capture cannot identify its source)"
    echo "ran in    : $here"
    echo "source    : unknown"
    return 0
  fi
  top="$(git rev-parse --show-toplevel 2>/dev/null)" || top=""
  if [ -z "$top" ] || [ "$(cd "$top" 2>/dev/null && pwd -P)" != "$here" ]; then
    echo "commit    : MISMATCH -- $sha is the head of $top, but capture ran in $here"
    echo "ran in    : $here"
    echo "source    : unknown (the answer was about a different tree)"
    return 0
  fi
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" || branch="?"
  when="$(git log -1 --format=%cI 2>/dev/null)" || when="?"
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then state="dirty"; else state="clean"; fi
  echo "commit    : $sha"
  echo "branch    : $branch"
  echo "toplevel  : $top"
  echo "committed : $when"
  echo "tree      : $state"
  echo "source    : git"
}

# Private WACZ/JSON evidence is intentionally outside the release. Validate
# opt-in roots once, before the long run, so a typo cannot silently turn the
# integration lane back into dozens of skips. The helper owns these test-only
# settings; they are not operator-facing service configuration.
cd "$BD_HOME" || { echo "FATAL: $BD_HOME not found"; exit 2; }
if ! venv/bin/python - <<'PY'
import sys
from capture_test_fixtures import validate_capture_fixture_roots

try:
    validate_capture_fixture_roots()
except ValueError as exc:
    print(f"FATAL: {exc}", file=sys.stderr)
    raise SystemExit(2)
PY
then
  exit 2
fi

# The Flask root and real-browser tests require the built SPA, and packaging
# declares this directory as required runtime data. A clean source checkout
# intentionally ignores frontend/dist, so fail before stopping services or
# running thousands of tests with misleading 503s. Build it explicitly from
# the checked-in lockfile first: `(cd frontend && npm ci && npm run build)`.
if [ ! -f "$BD_HOME/frontend/dist/index.html" ]; then
  echo "FATAL: frontend/dist/index.html is missing." >&2
  echo "Build the SPA before capture:" >&2
  echo "  (cd \"$BD_HOME/frontend\" && npm ci && npm run build)" >&2
  exit 2
fi

if [ "$CAPTURE_MODE" = "parallel" ]; then
  if [ -e "$OUT" ] || ! (umask 077; mkdir "$OUT"); then
    echo "FATAL: capture output already exists or cannot be claimed: $OUT" >&2
    exit 73
  fi
  if [ -e "$ARCHIVE" ]; then
    echo "FATAL: capture archive already exists; refusing to overwrite: $ARCHIVE" >&2
    exit 73
  fi
else
  rm -rf "$OUT" "$ARCHIVE"
  mkdir -p "$OUT"
fi

# Clear stale bytecode BEFORE any step runs. An overlaid .py with an mtime
# older than an existing __pycache__/*.pyc makes CPython run the STALE
# bytecode (observed at v3.66.161: on-disk __init__.py=161 but the process
# reported 160 until caches were cleared). Without this, the suite (step 2),
# the CSRF import (step 3) and the freshly-installed service (steps 4-9) can
# all load old bytecode and the capture would validate a stale tree. Cheap
# and idempotent — safe to run unconditionally.
find "$BD_HOME" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
find "$BD_HOME" -name '*.pyc' -delete 2>/dev/null

VERSION=$(grep '^__version__' bulk_downloader/__init__.py 2>/dev/null | head -1)
echo "================================================================"
echo "  BulkDownloader capture — $(date -Iseconds)"
echo "  bd_home : $BD_HOME"
echo "  version : $VERSION"
echo "  workers : $WORKERS"
echo "  instance: $CAPTURE_UNIT_INSTANCE"
echo "  app     : $CAPTURE_APP_ORIGIN"
echo "  fixture : $CAPTURE_FIXTURE_ORIGIN"
echo "  output  : $OUT/  -> $ARCHIVE"
echo "================================================================"

# ── [1/9] System fingerprint ──────────────────────────────────────
echo "=== [1/9] System fingerprint ==="
{
 echo "--- date ---"; date -Iseconds
 echo "--- commit ---"; emit_commit_identity
 echo "--- uname ---"; uname -a
 # v3.66.1025: a STABLE host identity, because uname's hostname is not one.
 # Two deploy hosts were stood up with the same hostname and their captures
 # became byte-indistinguishable -- one commit after CLAUDE.md told readers a
 # finding is about a HOST as well as a commit and pointed at this very file to
 # tell them apart.
 #
 # HASHED, NEVER RAW, and no LAN address either: this bundle is shipped to
 # third parties (it is why the capture vault lives outside $OUT). The digest
 # answers "same box or not" and publishes nothing further; the raw id is a
 # durable machine fingerprint and an IP is internal topology.
 #
 # Degrades to `unknown` rather than failing -- a capture must not die over a
 # fingerprint, and an absent one must not read as a present one.
 echo "--- host identity ---"
 hostname 2>/dev/null || echo "hostname: unknown"
 if [ -r /etc/machine-id ]; then
   printf 'machine-id(sha256/12): %s\n' "$(sha256sum /etc/machine-id | cut -c1-12)"
 else
   echo "machine-id(sha256/12): unknown"
 fi
 echo "--- os-release ---"; cat /etc/os-release 2>/dev/null
 echo "--- python ---"; venv/bin/python --version 2>&1
 echo "--- pip freeze (top 30) ---"; venv/bin/pip freeze 2>/dev/null | head -30
 echo "--- disk free ---"; df -h "$BD_HOME" /tmp /var 2>/dev/null
 echo "--- memory ---"; free -h 2>/dev/null
 echo "--- listening ports ---"; ss -tlnp 2>/dev/null | head -20
 echo "--- version we installed ---"; head -3 bulk_downloader/__init__.py
 head -10 CHANGELOG.md
} > "$OUT/01_sysinfo.log" 2>&1
echo "  done"

# ── [2/9] Full suite (service must NOT be running) ────────────────
# ── optional capability env (@1064, backlog 96) ──────────────────
#
# The mod3 suites read MOD3_PG_TEST_DSN from the ENVIRONMENT and nothing else.
# It used to be exported only from ~/.bashrc, below that file's non-interactive
# early-return, so a capture launched by ssh/nohup/systemd saw it UNSET and 18
# tests SKIPPED while the verdict still said PASS -- a capability present on the
# box and invisible to the gate measuring it. Sourced here so an automated
# capture and a human one measure the SAME denominator.
if [ -r "$HOME/.config/bd/mod3.env" ]; then
  # shellcheck source=/dev/null
  . "$HOME/.config/bd/mod3.env"
  echo "  optional capability env: sourced $HOME/.config/bd/mod3.env"
else
  echo "  optional capability env: none at $HOME/.config/bd/mod3.env -- mod3 suites will SKIP"
fi

echo "=== [2/9] Full test suite (5-15 min) ==="
if [ "$CAPTURE_LEGACY_SINGLETON" = "1" ]; then
  sudo systemctl stop bulkdownloader 2>/dev/null
  STOP_REQUEST_EXIT=$?
  if systemctl is-active --quiet bulkdownloader 2>/dev/null; then
    SERVICE_STOP_EXIT=1
  else
    SERVICE_STOP_EXIT=0
  fi
  echo "  service stop request exit=$STOP_REQUEST_EXIT; inactive=$((1 - SERVICE_STOP_EXIT))"
  sleep 1
else
  # The default capture app is a transient template instance that does not
  # exist yet. The operator's singleton is neither the subject nor a resource
  # this run may stop; stateful imports below are redirected to this run's
  # descriptor-owned install directory instead.
  STOP_REQUEST_EXIT=0
  SERVICE_STOP_EXIT=0
  echo "  operator service untouched; capture instance not started yet"
fi

# ── [2a/9] GUI-parity inventory regen (service down) ──────────────
#
# WHY THIS EXISTS: reports/gui_parity_inventory.json is BUILD-TIME GENERATED
# and GITIGNORED (.gitignore `reports/*`), yet it ships inside the release zip.
# The deploy path is an overlay — `unzip -o` overwrites and adds but never
# deletes — and `git clean -fd` cannot remove an ignored file (that needs -x).
# So a copy generated on a build host whose tree differed from this box can sit
# here indefinitely with nothing able to evict it. The suite's reconcile gate
# (tests/test_v3_66_302_gui_parity_reconcile.py) diffs the on-disk inventory
# against a live regen, so that stale copy reads as inventory drift and fails
# the entire capture — observed on the box as
# "only-shipped=[] only-regen=['pytest_capture_results']".
#
# Regenerating HERE puts the gate on fresh-vs-fresh. THE POSITION IS
# LOAD-BEARING ON THREE SIDES. If you came here to tidy the ordering, this is
# the paragraph you need:
#
#   * AFTER the __pycache__ purge above. The tool imports bulk_downloader.app,
#     so a regen placed earlier would read stale bytecode.
#
#   * AFTER `sudo systemctl stop bulkdownloader` AND after the is-active
#     confirmation above. THIS IS THE ONE THAT LOOKS LIKE COSMETICS AND IS NOT.
#     tools/gui_parity_inventory.py does `import bulk_downloader.app` (inside
#     _routes_from_app, the "live url_map" path this block demands below), and
#     that module's TOP LEVEL runs, unconditionally, on import:
#         db_init()                        app.py L80
#         db_integrity_check()             app.py L89
#         _start_session_keepers()         app.py L1494
#         _start_watch_folder_threads()    app.py L1569
#         _start_window_scheduler()        app.py L1638
#         _start_storage_tier_scheduler()  app.py L1658
#         _start_watcher()                 app.py L2062
#     Run while the service is live, that is a SECOND process running a SQLite
#     integrity check and starting five scheduler/thread groups against the
#     same BD_HOME database the service is serving. BD_DISABLE_KEEPALIVE=1
#     (which the tool sets for itself) suppresses ONE of those subsystems, not
#     the other six. Until this cut the block sat 24 lines ABOVE the stop and
#     did exactly that on every capture.
#
#   * BEFORE the pytest lanes below — a regen after them proves nothing.
#
# If the stop did not take, the regen is SKIPPED rather than run anyway: the
# collision above is the whole reason for this position, so "the service is
# still active" makes the inventory UNKNOWN (PARITY_EXIT=4), not
# stale-or-fresh. Unknown is a third state and it fails. That costs nothing in
# verdict terms — SERVICE_STOP_EXIT is already a --stage-exit, so a box whose
# service would not stop was failing the capture regardless.
#
# Exit 0 is NOT sufficient evidence of a good regen: the generator falls back
# to ENDPOINT_CATALOG.md when the app import raises, writes a
# catalog-derived item set, and still returns 0. The written JSON is therefore
# read back for `"route_source": "live url_map"`; missing file or any other
# source is UNKNOWN, and unknown is a third state that fails. Never fatal —
# every remaining step still runs and PARITY_EXIT goes to the verdict.
echo "=== [2a/9] GUI-parity inventory regen ==="
PARITY_JSON="$BD_HOME/reports/gui_parity_inventory.json"
PARITY_EXIT=0
if [ "$SERVICE_STOP_EXIT" -ne 0 ]; then
  echo "  SKIPPED: bulkdownloader is still active. Importing the app now would" >&2
  echo "  run a second db_integrity_check and start five scheduler groups" >&2
  echo "  against the live database. The inventory is therefore UNKNOWN, not" >&2
  echo "  stale-or-fresh." >&2
  PARITY_EXIT=4
else
  env ${CAPTURE_INSTALL_DIR:+BD_INSTALL_DIR=$CAPTURE_INSTALL_DIR} \
    venv/bin/python tools/gui_parity_inventory.py \
     > "$OUT/02a_gui_parity_inventory.log" 2>&1
  PARITY_EXIT=$?
  if [ "$PARITY_EXIT" -ne 0 ]; then
    echo "  WARNING: gui-parity inventory regen FAILED (exit=$PARITY_EXIT)." >&2
    echo "  reports/gui_parity_inventory.json was not refreshed, so the parity" >&2
    echo "  reconcile gate may report inventory drift against a stale copy and" >&2
    echo "  fail the suite. See $OUT/02a_gui_parity_inventory.log" >&2
  elif [ ! -f "$PARITY_JSON" ]; then
    echo "  WARNING: regen reported success but $PARITY_JSON is missing." >&2
    echo "  UNKNOWN state: the parity reconcile gate may report inventory drift." >&2
    PARITY_EXIT=2
  # ONE spelling of this predicate now, in capture.sh, install_linux.sh and
  # scripts/provision_test_host.sh: PARSE the JSON, never grep for the
  # substring `"route_source": "live url_map"`. The substring form is hostage
  # to how tools/gui_parity_inventory.py serialises (json.dumps(...,
  # indent=2)) — change the indent or the key separator and the grep starts
  # calling a perfectly good inventory degraded, which is a gate firing on
  # identity. install_linux.sh carried the strict literal and this file a
  # whitespace-tolerant regex; they agreed by luck, and the strict one would
  # have gone wrong first. A parse cannot be wrong about formatting. A file
  # that will not parse is NOT a pass: json.load raising exits non-zero and
  # lands in the same branch as a wrong route_source, because both mean "not
  # proven app-derived".
  elif ! venv/bin/python -c 'import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:
    print("gui_parity_inventory.json will not parse: %s" % exc, file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0 if d.get("route_source") == "live url_map" else 1)' "$PARITY_JSON"; then
    echo "  WARNING: inventory was built from the ENDPOINT_CATALOG.md fallback," >&2
    echo "  not the live url_map — the app import degraded silently and the tool" >&2
    echo "  still exited 0. The item set is catalog-derived, so the parity" >&2
    echo "  reconcile gate may report inventory drift." >&2
    PARITY_EXIT=3
  fi
fi
echo "  exit=$PARITY_EXIT"

# THE TWO OBSERVABILITY FLAGS ARE LOAD-BEARING AND NEITHER IS A STYLE CHOICE.
# Backlog 147, applied to the GATE at @1130 after @1126 fixed the same pair in
# the sanctioned whole-suite form and in both tools that implement it. This
# script was named as unaudited in SESSION_CARRY 15.96 and kept both blindfolds.
#
# NO QUIET FLAG: it would set verbose == -1, and pytest-xdist guards its entire
# crash-recovery narration on verbose >= 0 (DSession.report_line). The worker-
# down line writes UNGUARDED, so a quiet lane shows the wedge symptom and hides
# the response. Dropping the flag is SUFFICIENT; -v is not required and costs
# ~16k lines a run.
#
# PYTHONUNBUFFERED: a run that never exits never flushes, so a wedged lane
# strands its last ~4KB -- measured as 15 of 15 wedged logs ending mid-line
# against 642 of 642 completed logs ending clean, and that tail is exactly where
# the recovery narration lands. It is spelled as an ENV VAR and not as an
# interpreter flag on purpose: an interpreter flag never reaches sys.argv, so
# no gate comparing a built command against pytest's argv could ever see it.
#
# NEITHER CHANGES WHAT IS EXECUTED. Same markers, same distribution, same
# collection; the capture VERDICT is read from the junit XML by
# tools/pytest_capture_results.py and never from these logs.
run_with_heartbeat "parallel-safe pytest lane" "$OUT/02_pytest_parallel.log" \
   env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest \
   tests --tb=short \
   -m capture_parallel \
   -n "$WORKERS" --dist loadfile \
   --junitxml="$OUT/02_pytest_parallel.xml"
PARALLEL_EXIT=$?
# The serial lane carries the same pair. It runs at -n 0, so the xdist half
# cannot bite it -- but the buffering half can (a hang here strands its tail
# identically), and a lane that differs from its sibling for no stated reason is
# the next reader's puzzle. Kept symmetric deliberately.
run_with_heartbeat "serial pytest lane" "$OUT/02_pytest_serial.log" \
   env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest \
   tests --tb=short \
   -m capture_serial \
   -n 0 \
   --junitxml="$OUT/02_pytest_serial.xml"
SERIAL_EXIT=$?
{
  echo "=== parallel-safe pytest lane (exit=$PARALLEL_EXIT, workers=$WORKERS) ==="
  cat "$OUT/02_pytest_parallel.log"
  echo
  echo "=== serial pytest lane (exit=$SERIAL_EXIT, workers=0) ==="
  cat "$OUT/02_pytest_serial.log"
} > "$OUT/02_suite_run.log"
venv/bin/python tools/pytest_capture_results.py \
   --junit "$OUT/02_pytest_parallel.xml" \
   --junit "$OUT/02_pytest_serial.xml" \
   --json "$OUT/02_test_results.json" \
   --summary "$OUT/02_SUMMARY.txt" \
   >> "$OUT/02_suite_run.log" 2>&1
RESULTS_EXIT=$?
venv/bin/python tools/check_skip_baseline.py \
   --junit "$OUT/02_pytest_parallel.xml" \
   --junit "$OUT/02_pytest_serial.xml" \
   >> "$OUT/02_suite_run.log" 2>&1
SKIP_BASELINE_EXIT=$?
if [ "$RESULTS_EXIT" -ne 0 ]; then
  SUITE_EXIT=$RESULTS_EXIT
elif [ "$PARALLEL_EXIT" -ne 0 ]; then
  SUITE_EXIT=$PARALLEL_EXIT
elif [ "$SERIAL_EXIT" -ne 0 ]; then
  SUITE_EXIT=$SERIAL_EXIT
else
  SUITE_EXIT=$SKIP_BASELINE_EXIT
fi
echo "  --- tail of suite run ---"
tail -25 "$OUT/02_suite_run.log"
echo "  Summary written: $OUT/02_SUMMARY.txt"

# ── [2b/9] deployment-local source-graph gate ─────────────────────────
# The trust anchor is a small content pin OUTSIDE the install tree.  Rebuild the
# graph from the currently deployed source into a throwaway directory, compare
# canonical node/edge rows, and delete the SQLite database on every exit path.
# A missing pin is UNKNOWN by default and a hard failure for release/OPV runs
# that set BD_REQUIRE_GRAPH_HASH=1.  Pin generation is a separate, deliberate
# deployment-acceptance step; never write a pin immediately before this check.
# bd_graph_gate_function_begin
run_graph_hash_gate() (
  graph_pin="${BD_GRAPH_HASH_PIN:-/var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256}"
  graph_required="${BD_REQUIRE_GRAPH_HASH:-0}"

  if [ ! -f "$graph_pin" ]; then
    echo "graph content pin: MISSING -- $graph_pin"
    if [ "$graph_required" = "1" ]; then
      echo "graph content pin is required (BD_REQUIRE_GRAPH_HASH=1)"
      return 1
    fi
    echo "graph content pin: UNKNOWN -- optional check not armed"
    return 0
  fi
  if [ ! -r "$graph_pin" ]; then
    echo "graph content pin: UNREADABLE -- $graph_pin"
    if [ "$graph_required" = "1" ]; then
      echo "graph content pin must be readable (BD_REQUIRE_GRAPH_HASH=1)"
      return 1
    fi
    echo "graph content pin: UNKNOWN -- optional check not armed"
    return 0
  fi
  if [ ! -x venv/bin/python ] || [ ! -f tools/l0_extract.py ] \
      || [ ! -f tools/graph_build.py ]; then
    echo "graph content pin: CANNOT EVALUATE -- graph tools or venv missing"
    return 2
  fi

  graph_tmp=$(mktemp -d "${TMPDIR:-/tmp}/bd_graph.XXXXXX") || {
    echo "graph content pin: CANNOT EVALUATE -- mktemp failed"
    return 2
  }
  cleanup_graph_tmp() {
    if [ -n "${graph_tmp:-}" ]; then
      rm -rf -- "$graph_tmp"
    fi
  }
  trap cleanup_graph_tmp EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  graph_db="$graph_tmp/KNOWLEDGE_GRAPH.db"

  venv/bin/python tools/l0_extract.py --root "$PWD" --db "$graph_db" || return $?
  venv/bin/python tools/graph_build.py --db "$graph_db" \
      --hash-pin "$graph_pin" --check-hash
)
# bd_graph_gate_function_end

echo "=== [2b/9] graph content-hash gate (P2) ==="
GRAPH_EXIT=0
run_graph_hash_gate > "$OUT/02b_graph_checkhash.log" 2>&1
GRAPH_EXIT=$?
echo "graph-gate exit: $GRAPH_EXIT" >> "$OUT/02b_graph_checkhash.log"
tail -5 "$OUT/02b_graph_checkhash.log" | sed 's|^|  |'

# ── [3/9] CSRF diagnostic (no app running) ───────────────────────
#
# v3.63.6: imports the module-level `app` directly. The factory
# `create_app()` does not exist in this codebase — `bulk_downloader/
# app.py` builds the Flask app at module scope (`app = Flask(...)`).
# Pre-v3.63.6 capture.sh imported a nonexistent `create_app`, so this
# step ImportErrored on every run.
#
# Cookie redaction: this log is tarred and shipped (capture.sh:1005), and
# bd_session is the CSRF-bound auth session (app.py:1074) -- its value is a live
# credential. Step [3] used to print h[:120] of every Set-Cookie, so it shipped
# one. The diagnostic facts are: a session WAS minted, how many headers, which
# flags, what TTL -- the value is not one of them, and is replaced by its length
# (the same rule the capture vault follows: status recorded, body never).
# Unknown attributes are named but their values omitted -- never emit what this
# diagnostic does not control. This rationale lives OUT here, not inside the
# python -c string, because _strip_shell_comments (provisioner regen ordering)
# asserts no comment line survives inside capture.sh's shell body.
#
# The two "contains meta tag" / "contains marker" probes were deleted. The
# Jinja shell they probed went at v3.66.334 -- bulk_downloader/templates/ no
# longer exists and / is served either as the installer 503 or as the static
# frontend/dist/index.html, so both booleans were structural constants False
# on BOTH reachable branches. Nothing read them: capture_verdict.py consumes
# only the stage EXIT code handed to it via --stage-exit "csrf=$CSRF_EXIT",
# never the log body. Two lines that could not fail and could not pass, in a
# bundle that is tarred and shipped. The exit code and the redacted cookie
# facts below are what step [3] genuinely carries.
#
echo "=== [3/9] CSRF diagnostic ==="
env ${CAPTURE_INSTALL_DIR:+BD_INSTALL_DIR=$CAPTURE_INSTALL_DIR} \
  BD_DISABLE_KEEPALIVE=1 venv/bin/python -c "
from bulk_downloader.app import app
with app.test_client() as c:
   r = c.get('/')
   print('status:', r.status_code)
   print('body length:', len(r.data))
   print('Set-Cookie headers:', len(r.headers.getlist('Set-Cookie')))
   _flag_attrs = ('expires', 'max-age', 'domain', 'path', 'secure',
                  'httponly', 'samesite', 'partitioned', 'priority')
   for h in r.headers.getlist('Set-Cookie'):
       name, _, rest = h.partition('=')
       value, _, attrs = rest.partition(';')
       shown = []
       for a in attrs.split(';'):
           a = a.strip()
           if not a:
               continue
           k, sep, v = a.partition('=')
           k = k.strip()
           if not sep:
               shown.append(k)
           elif k.lower() in _flag_attrs:
               shown.append(k + '=' + v.strip())
           else:
               shown.append(k + '=<omitted>')
       print('  cookie:', name.strip(), 'value_len:', len(value.strip()),
             'attrs:', ','.join(shown) or '(none)')
print('=' * 60)
print('END diagnostic')
print('=' * 60)
" > "$OUT/03_csrf_diag.log" 2>&1
CSRF_EXIT=$?
echo "  done"

# ── [4/9] Install + start systemd service ─────────────────────────
echo "=== [4/9] Install + start systemd service ==="

# The legacy drop-in must exist BEFORE install_service.sh, because that runs
# daemon-reload and starts the unit. Written after, it would not reach the
# running process and the seeder would meet the operator's locked vault
# exactly as before -- while the capture reported it had set one up.
#
# A drop-in rather than .env on purpose: _envfile.py records ".env, not a
# systemd drop-in" for OPERATOR configuration, which is the GUI editor's
# persistence target. This is machine-written scaffolding removed inside the
# same run. The deciding factor is the failure mode -- a stale .env line is
# invisible to the GUI editor's model, while a stale drop-in is the first
# thing `systemctl cat bulkdownloader` shows.
if [ "$CAPTURE_LEGACY_SINGLETON" = "1" ]; then
  if [ "$CAPTURE_VAULT" = "1" ]; then
    capture_vault_dir_claim
    sudo mkdir -p "$(dirname "$CAPTURE_VAULT_DROPIN")"
    sudo tee "$CAPTURE_VAULT_DROPIN" >/dev/null <<DROPIN
[Service]
Environment=BD_SECRETS_FILE=$CAPTURE_VAULT_FILE
Environment=BD_CAPTURE_VAULT=1
DROPIN
    echo "  capture-vault drop-in written -> $CAPTURE_VAULT_DROPIN"
  fi

  ./install_service.sh > "$OUT/04_service_install.log" 2>&1
  INSTALL_EXIT=$?
else
  _capture_secret_arg=""
  [ "$CAPTURE_VAULT" = "1" ] && _capture_secret_arg="$CAPTURE_VAULT_FILE"
  # Mark before acting: a start command can create/start the instance and then
  # fail while reporting it. Cleanup must still name and stop that exact unit.
  CAPTURE_INSTANCE_STARTED=1
  "$BD_HOME/scripts/install_capture_service.sh" start \
    "$CAPTURE_RUN_ID" "$CAPTURE_APP_PORT" "$CAPTURE_INSTALL_DIR" \
    "$_capture_secret_arg" > "$OUT/04_service_install.log" 2>&1
  INSTALL_EXIT=$?
fi
# NOT `sleep 3`. install_service.sh polls `systemctl is-active` and reports
# RUNNING the moment the unit goes active, but Type=simple means "the process
# was spawned", not "waitress has bound :5555". A fixed sleep is a guess at that
# gap, and when the guess is wrong the unlock below POSTs into a closed socket
# and records HTTP 000 next to `service: active` -- two visible facts that
# disagree, neither of them wrong. Ask the app instead.
wait_for_service_ready || true
systemctl status "$CAPTURE_UNIT_INSTANCE" --no-pager > "$OUT/04_service_status.log" 2>&1
journalctl -u "$CAPTURE_UNIT_INSTANCE" -n 50 --no-pager > "$OUT/04_service_boot.log" 2>&1
ACTIVE=$(systemctl is-active "$CAPTURE_UNIT_INSTANCE" 2>&1)
if [ "$ACTIVE" = "active" ]; then SERVICE_EXIT=0; else SERVICE_EXIT=1; fi
echo "  service: $ACTIVE"

# The AI readiness companion is deliberately independent of the main unit.
# Observe it only after the app is serving, and preserve the structured result
# even when it cannot certify readiness so the final verdict can fail closed.
venv/bin/python -m bulk_downloader.ai_boot_observation \
  --output "$OUT/05_ai_boot_observation.json" \
  --timeout "${AI_BOOT_OBSERVE_TIMEOUT:-300}" \
  --interval "${AI_BOOT_OBSERVE_INTERVAL:-5}"
AI_BOOT_EXIT=$?

# Unlock the CAPTURE vault, after the service is up and before [5a] seeds --
# the seeder refuses on a locked vault, which is the whole reason this exists.
# The password goes in on stdin (--data-binary @-), never in argv: /proc makes
# a process's command line readable by every user on the box. Only the HTTP
# code is recorded; the response body is discarded so nothing about the
# credential can reach $OUT, which is tarred into the shared bundle.
# Gated on SERVING, not merely on `is-active`: the unlock is an HTTP POST, so
# its precondition has to be an HTTP fact.
if [ "$CAPTURE_VAULT" = "1" ] && [ "$ACTIVE" = "active" ] \
     && [ "$SERVICE_READY_EXIT" = "0" ]; then
  UNLOCK_CODE=$(printf '{"password":"%s"}' "$CAPTURE_VAULT_PW" \
    | curl -sS -o /dev/null -w '%{http_code}' \
        -X POST "$CAPTURE_APP_ORIGIN/api/secrets/unlock" \
        -H 'Content-Type: application/json' --data-binary @- 2>/dev/null)
  echo "  capture-vault unlock: HTTP ${UNLOCK_CODE:-000}" \
    | tee -a "$OUT/04_service_status.log"
  if [ "${UNLOCK_CODE:-000}" != "200" ]; then
    echo "  WARNING: capture vault did not unlock; L6/L8 will WARN and the" \
         "seeder will refuse the login half" >&2
  fi
fi

# ── [5/9] Ollama status ──────────────────────────────────────────
echo "=== [5/9] Ollama status ==="
{
 echo "--- systemctl status ---"
 systemctl status ollama --no-pager 2>&1 | head -10
 echo "--- journalctl tail ---"
 journalctl -u ollama -n 20 --no-pager 2>&1 | tail -20
 echo "--- ollama list ---"
 ollama list 2>&1
 echo "--- API check ---"
 curl -sS http://localhost:11434/api/tags 2>&1
} > "$OUT/05_ollama.log" 2>&1
echo "  done"

# ── [5a/9] Seed synthetic input for the live checks ──────────────
#
# Several live checks WARN because nothing has exercised them: no queued
# URLs, no completed downloads. Those warnings are honest, and since
# v3.66.818 they no longer fail the verdict — but on a capture host they
# are permanently unactionable, because only a human queueing real work
# clears them. tools/live_seed.py supplies that work.
#
# WHAT IS AND IS NOT BEING CLAIMED: the seed is INPUT, never OUTPUT. It
# queues marked URLs pointing at the LOCAL fixture origin; BD still has to
# accept, persist, rehydrate and report every one of them, and all of that
# is BD's own work. A check that went green because the seeder handed it
# the answer would be vacuous — worse than the warning it replaced — so
# the seeder writes only through the app's HTTP API and marks everything
# it creates. See tools/live_seed.py for the full contract.
#
# The seeder refuses to run on a host that already holds real work, and
# refuses when it cannot tell, so pointing capture.sh at a live box does
# not quietly mix synthetic rows into real ones.
#
# ENTIRELY NON-FATAL: any failure here degrades to the live checks' own
# existing WARNs. Aborting the capture because an optional convenience
# failed would be strictly worse than the warning it was meant to remove.
echo "=== [5a/9] Seed synthetic live-check input ==="
FIXTURE_PID=""
SEEDED=0

# Teardown must run whether the live suite passed, failed, or the operator
# pressed Ctrl-C. Synthetic state left behind is read as REAL work by the
# next run — including by the seeder's own preflight, which refuses a host
# holding real entries — so one interrupted capture would otherwise wedge
# every later one. Idempotent: safe to call twice.
cleanup_live_seed() {
  if [ "$SEEDED" = "1" ]; then
    SEEDED=0
    # BD_SEED_CLEAR_HISTORY is OPT-IN and defaults to 0, for the same reason
    # BD_SEED_FORCE is: this is the UNATTENDED caller. The seeder's clear
    # predicate is the marker across ALL history, not this run's nonce, so
    # the first armed run deletes every bdseed row accumulated by every
    # previous capture (64 at v3.66.844). Arming it is an explicit operator
    # act, and the first run should be:
    #   venv/bin/python tools/live_seed.py --teardown --clear-history --dry-run
    _clear_flag=""
    [ "${BD_SEED_CLEAR_HISTORY:-0}" = "1" ] && _clear_flag="--clear-history"
    venv/bin/python tools/live_seed.py --base-url "$CAPTURE_APP_ORIGIN" \
      --fixture-origin "$CAPTURE_FIXTURE_ORIGIN" \
      --teardown $_clear_flag \
      >> "$OUT/05a_live_seed.log" 2>&1
    _teardown_exit=$?
    echo "live_seed: TEARDOWN-EXIT=$_teardown_exit" >> "$OUT/05a_live_seed.log"
    if [ "$_teardown_exit" -ne 0 ]; then
      echo "  WARNING: seed teardown exited $_teardown_exit -- synthetic rows" \
           "may remain; run: venv/bin/python tools/live_seed.py --teardown" \
           "$_clear_flag" >&2
      # Select on the diagnostic marker, never on position, and do it HERE.
      # The identical grep at step [5a] runs at SEED time against a log the
      # teardown has not written to yet (the seed truncates with `>`, the
      # teardown appends with `>>` about 125 lines later), so it can never
      # carry a teardown reason. `tail`, not `head`: the teardown's lines
      # are the last ones in the file.
      grep 'live_seed: ' "$OUT/05a_live_seed.log" 2>/dev/null \
        | tail -12 | sed 's/^/    /' >&2
    fi
  fi
  if [ -n "$FIXTURE_PID" ]; then
    _stop_process_group "$FIXTURE_PID"
    FIXTURE_PID=""
  fi
}
# NOTE: run_graph_hash_gate defines its own `trap cleanup_graph_tmp EXIT`,
# but it is a SUBSHELL function — `run_graph_hash_gate() (` with parens —
# so that trap is scoped to the subshell and this global one does not
# collide with it. bash keeps only one EXIT trap per shell; converting
# that function to braces would silently disable one of the two.
#
# The capture vault rides the SAME trap for exactly that reason: a second
# `trap ... EXIT` would not add a handler, it would REPLACE this one, silently
# disabling the seed teardown. Both cleanups are idempotent, so the explicit
# calls after [6] and this backstop can both fire.
#
# cleanup_all is defined and ARMED at the top of this file, next to
# cleanup_capture_vault -- it has to be, because the drop-in is written at step
# [4] and a trap armed here would leave steps [4]-[5a] unprotected. It reaches
# cleanup_live_seed by name once this definition exists.

if [ -f "$BD_HOME/tools/live_seed.py" ] && [ -f "$BD_HOME/tools/fixture_site.py" ]; then
  # The seeded URLs point at the local fixture origin, so it has to be
  # serving before anything is queued. setsid detaches it into its own
  # process group so _stop_process_group can take the whole tree down.
  _start_capture_detached "$OUT/05a_fixture_site.log" \
    venv/bin/python tools/fixture_site.py --port "$CAPTURE_FIXTURE_PORT"
  FIXTURE_PID="$CAPTURE_DETACHED_PID"
  _fixture_up=0
  _tries=0
  while [ "$_tries" -lt 20 ]; do
    if curl -sSf -o /dev/null "$CAPTURE_FIXTURE_ORIGIN/" 2>/dev/null; then
      _fixture_up=1
      break
    fi
    sleep 0.25
    _tries=$((_tries + 1))
  done
  if [ "$_fixture_up" = "1" ]; then
    echo "  fixture site up on :$CAPTURE_FIXTURE_PORT (pid $FIXTURE_PID)"
    # --login is best-effort on top of --seed: it needs an unlocked, encrypted
    # secrets backend to store the fixture password, and refuses cleanly when
    # that precondition is absent. SEEDED is set either way so teardown always
    # runs against whatever did get created.
    # BD_SEED_FORCE=1 passes --force through to the seeder's preflight, which
    # otherwise REFUSES when the queue already holds real work ("seeding could
    # be mistaken for the operator's own work and teardown would be
    # ambiguous"). That refusal is correct as a default and it fired on a real
    # run, leaving the seeded live checks unexercised with no way to ask for
    # them short of emptying the operator's queue.
    #
    # Overriding it is safe because teardown is MARKER-matched, not
    # queue-wide: `_is_seeded()` is `SEED_MARKER in entry["url"]`, so nothing
    # without `bdseed` in its URL can be removed. The guard is conservative
    # about a hazard teardown does not actually have.
    _seed_force=""
    [ "${BD_SEED_FORCE:-0}" = "1" ] && _seed_force="--force"
    # --start is what makes L11 / L12 / L14 exercisable AT ALL. They gate on a
    # COMPLETED download, and until now nothing anywhere started one: the
    # seeder placed URLs and stopped, so the last capture found them sitting in
    # `waiting` with `running=0` and all three checks reported "no completed
    # downloads yet" — which reads as BD failing to download, when BD was never
    # asked to. live_tests/ structurally cannot ask (Context is read-only by
    # design), so the seeder does.
    #
    # It only ever starts a site whose NAME carries the marker — the same
    # predicate teardown uses — so an operator site is never drained, and it
    # refuses outright if it cannot prove ownership.
    #
    # --start-timeout is explicit rather than defaulted because THIS is the
    # unattended caller: the number a reader needs is the one bounding the
    # capture, and it belongs where they will look for it. The fixture serves
    # 4-16 KB files, so the normal path settles in seconds; the bound is for
    # the abnormal one. A timeout exits non-zero and is reported per URL, which
    # lands in the `else` branch below as a warning, not a capture failure.
    # --vpn-tunnel is what gives L30 (vpn-tunnel-inventory-consistent) a
    # subject. It is NOT implied by --seed: main() branches on args.vpn_tunnel
    # separately, so omitting it left L30 reporting "no VPN tunnels configured
    # — nothing to verify" on the box, truthfully, because nothing had ever
    # asked for the thing it checks. The tunnel it creates is inert with
    # respect to egress — state defaults to "down", no SOCKS port is allocated
    # until start_tunnel() which the seeder never calls, and no site routes to
    # it — so it is observable without being live.
    # -u is load-bearing: live_seed writes its plan JSON to stdout and its
    # REFUSED/TIMEOUT diagnostic to stderr. Merged into one file with 2>&1,
    # stdout is block-buffered while stderr is write-through, so without -u
    # the two do not interleave in source order and the reason surfaces at
    # whichever end the buffer happened to flush.
    if venv/bin/python -u tools/live_seed.py --seed --start --start-timeout 180 \
         --base-url "$CAPTURE_APP_ORIGIN" \
         --fixture-origin "$CAPTURE_FIXTURE_ORIGIN" \
         --login --vpn-tunnel --count 3 $_seed_force \
         > "$OUT/05a_live_seed.log" 2>&1; then
      SEEDED=1
      echo "  seeded 3 marked URLs + started the queue + fixture login + an"
      echo "  inert VPN tunnel — queue, auth and VPN checks are exercising"
      echo "  SYNTHETIC input; see $OUT/05a_live_seed.log"
    else
      # A refusal can still have created something before it stopped, so mark
      # the run as seeded regardless -- teardown is idempotent and removing
      # nothing is cheaper than stranding marked state on the box.
      SEEDED=1
      echo "  seeding declined or failed (not a capture failure):"
      # Select on the diagnostic marker, never on position. Both exit paths
      # emit a `live_seed: ` line (REFUSED, and TIMEOUT which then adds one
      # line per unresolved URL), so the reason is variable-length AND at an
      # unpredictable offset -- a fixed `tail -N` printed the tail of the plan
      # JSON instead, which is how "}", "}", "]" reached the operator while
      # the real sentence sat on line 1.
      _seed_why="$(grep -n 'live_seed: ' "$OUT/05a_live_seed.log" 2>/dev/null \
                   | head -1 | cut -d: -f1)"
      if [ -n "$_seed_why" ]; then
        sed -n "${_seed_why},\$p" "$OUT/05a_live_seed.log" 2>/dev/null \
          | head -12 | sed 's/^/    /'
      else
        # No marker: say the log could not be read for a reason rather than
        # printing an arbitrary slice of it and letting it read as the cause.
        echo "    (no 'live_seed:' diagnostic in the log -- full output is in"
        echo "     $OUT/05a_live_seed.log)"
        head -5 "$OUT/05a_live_seed.log" 2>/dev/null | sed 's/^/    /'
      fi
    fi
  else
    echo "  fixture site did not come up on :$CAPTURE_FIXTURE_PORT — skipping seed"
    _stop_process_group "$FIXTURE_PID"
    FIXTURE_PID=""
  fi
else
  echo "  tools/live_seed.py or tools/fixture_site.py absent — skipping seed"
fi

# ── [5b/9] Display for the headed-browser check ──────────────────
#
# L2 (headed-browser-launch) opens a VISIBLE Chromium — headless=False is a
# DANGER_MAP invariant, so the check exists to prove the interactive-login
# path works on this deployment. Without an X server it WARNs, which is
# honest but permanently unactionable.
#
# WHY THIS EXISTS: scripts/provision_test_host.sh already starts Xvfb and
# exports DISPLAY — but that export dies with the provisioner's process.
# capture.sh runs later, in a different shell, and had no DISPLAY of its
# own, so L2 warned even on a correctly provisioned box unless the operator
# had exported DISPLAY by hand. The capability was provisioned and then
# never handed over. This closes that handoff.
#
# This is PROVISION, not seeding: it supplies a real X server so a real
# headed browser really launches. L2's assertion is untouched — if the
# browser cannot start, L2 still fails.
#
# NON-FATAL BY DESIGN: a headless box with no Xvfb is a legitimate
# deployment. Absence degrades to L2's existing WARN (informational since
# the capture verdict stopped gating on warnings); it must never abort the
# capture, which would turn an honest warning into a broken run.
#
# bd_start_display comes from the shared fragment so the launch, the
# idempotency and the "is this display actually served" probing all live in
# one place. Re-implementing an Xvfb launch here would recreate the
# three-copies-that-drift problem the fragment was built to end.
echo "=== [5b/9] Display for headed-browser check ==="
if [ -n "${DISPLAY:-}" ]; then
  echo "  DISPLAY already set to '$DISPLAY' — leaving it alone"
elif [ -r "$BD_HOME/scripts/lib/system_deps.sh" ]; then
  # shellcheck source=scripts/lib/system_deps.sh
  . "$BD_HOME/scripts/lib/system_deps.sh" 2>/dev/null || true
  if declare -F bd_start_display >/dev/null 2>&1; then
    if _cap_display="$(bd_start_display :99 2>/tmp/bd_display.err)"; then
      export DISPLAY="$_cap_display"
      echo "  DISPLAY=$DISPLAY (headed-browser checks can run)"
    else
      echo "  no display available — L2 will WARN (not a failure):"
      sed 's/^/    /' /tmp/bd_display.err 2>/dev/null | tail -3
    fi
    rm -f /tmp/bd_display.err
  else
    echo "  system_deps.sh sourced but bd_start_display undefined — L2 will WARN"
  fi
else
  echo "  scripts/lib/system_deps.sh not readable — L2 will WARN"
fi

# ── [6/9] Live-test suite (running app) ──────────────────────────
#
# v3.63.6: L3 (mv3-extension-service-worker) is back in LIVE_IDS.
# The v3.63.5 extension fix means it PASSes cleanly now: the three
# placeholder PNG icons (extension/icon{16,48,128}.png) let Chromium
# load the extension at all, and channel="chromium" in checks.py
# swaps from the headless-shell binary (which cannot host MV3 SWs)
# to the full Chromium-for-Testing binary. The earlier "Chromium MV3
# known wedge" rationale was wrong — see LESSONS_LEARNED A1/A17.
#
# Per-check timeout stays at 90s (T55 contains the wedge; L34
# also benefits from the larger budget).
#
echo "=== [6/9] Live-test suite ==="
LIVE_IDS="L1,L2,L3,L4,L5,L6,L7,L8,L9,L10,L11,L12,L13,L14,L15,L16,L17,L18,L19,L20,L21,L22,L23,L24,L25,L26,L27,L28,L29,L30,L31,L32,L33,L34,L35,L36,L37"
# EXPECTED_LIVE_TESTS comes from the REGISTRY, never from LIVE_IDS.
#
# It used to be `awk -F, NF` over LIVE_IDS itself, which made the completeness
# gate compare the selection with itself: it caught an ID that was requested and
# did not run, and could never catch an ID that existed and was never requested.
# L36 and L37 sat in that gap and had never run in any capture -- and L36 covers
# the stale-or-half-built frontend/dist/ class that a git deploy silently does
# not deliver, which no other check sees.
#
# Reading the registry gives the verdict an independent denominator, so adding a
# check to live_tests/checks.py without adding it here now fails the capture
# instead of passing quietly. Importing `checks` is what registers them; harness
# alone yields an empty list.
EXPECTED_LIVE_TESTS=$(venv/bin/python -c \
  'from live_tests import checks, harness; print(len(harness.registry()))' \
  2>"$OUT/06_registry_count.err")
if ! printf '%s' "$EXPECTED_LIVE_TESTS" | grep -qE '^[1-9][0-9]*$'; then
  # Unknown is a third state and it fails. A blank or zero count here would
  # otherwise reach capture_verdict.py as a number that cannot be compared,
  # and the one thing this must never do is let the verdict pass unverified.
  echo "  BD-GATE-UNRUNNABLE: could not read the live-check registry"
  echo "  (got '${EXPECTED_LIVE_TESTS:-<empty>}'; see $OUT/06_registry_count.err)"
  EXPECTED_LIVE_TESTS=-1
fi
echo "  registry reports $EXPECTED_LIVE_TESTS check(s); requesting $(printf '%s\n' "$LIVE_IDS" | awk -F, '{print NF}')"
run_with_heartbeat "live-test suite" "$OUT/06_live_tests.log" \
   env BD_DISABLE_KEEPALIVE=1 venv/bin/python -u -m live_tests.run \
   --url "$CAPTURE_APP_ORIGIN" \
   --bd-home "$BD_HOME" \
   --db-path "$CAPTURE_INSTALL_DIR/downloader_history.db" \
   --results-dir "$OUT/06_live_results_source" \
   --only "$LIVE_IDS" \
   --per-check-timeout 90
LIVE_EXIT=$?
echo "  --- tail of live tests ---"
tail -10 "$OUT/06_live_tests.log"
echo "  exit=$LIVE_EXIT"

# ── [6b] collect the per-check logs the runner already wrote ──────
#
# The 55-line summary above states each check's VERDICT and discards the
# evidence for it. Measured 2026-08-10: two captures both failed L34 with
# "N route(s) UNPROBED (phase-1 deadline)", and neither could answer whether
# the operator surface had grown or a subset of routes was pathological --
# because L34 logs both its denominator ("1001 routes total; 264 operator
# parameter-free GET routes to gate") and every per-route timing, into
# live_tests/results/L34.log, which nothing collected. The operator had to
# `cat` it by hand off the box for a finding the archive was supposed to carry.
#
# live_tests/harness.py's artifact contract is the source of the filenames:
# "<id>.log  full verbose log of the test -- ALWAYS written, overwritten each
# run", plus <id>.fail.txt on FAIL and an APPEND-only SUMMARY.txt. The summary
# accumulates across every run the box has ever done, so it is tailed rather
# than copied whole, and the destination name says so.
#
# SAFE TO SHIP, MEASURED, NOT ASSUMED. These logs leave the box, so the rule is
# step [3]'s: status recorded, body never. An AST scan of live_tests/ found 177
# ctx.log call sites and exactly one deriving from a ctx.get() response body --
# `body.get('version')`, a named scalar. That is a property of today's checks,
# not a guarantee, so tests/test_v3_66_1009_live_results_are_bundled.py pins it:
# the next check that logs a whole response fails there instead of shipping one.
#
# Runs BEFORE cleanup_live_seed and before the bundle. A copy after `tar czf`
# leaves a directory on the box that no archive contains, which is
# indistinguishable from success from inside this script.
echo "=== [6b/9] Live-check per-check logs ==="
LIVE_RESULTS_SRC="$OUT/06_live_results_source"
LIVE_RESULTS_DST="$OUT/06_live_results"
mkdir -p "$LIVE_RESULTS_DST"
LIVE_RESULTS_N=0
if [ -d "$LIVE_RESULTS_SRC" ]; then
  for f in "$LIVE_RESULTS_SRC"/*.log "$LIVE_RESULTS_SRC"/*.fail.txt; do
    [ -f "$f" ] || continue
    cp -p "$f" "$LIVE_RESULTS_DST"/ 2>/dev/null && LIVE_RESULTS_N=$((LIVE_RESULTS_N + 1))
  done
  if [ -f "$LIVE_RESULTS_SRC/SUMMARY.txt" ]; then
    tail -400 "$LIVE_RESULTS_SRC/SUMMARY.txt" > "$LIVE_RESULTS_DST/SUMMARY.tail.txt" \
      && LIVE_RESULTS_N=$((LIVE_RESULTS_N + 1))
  fi
fi
if [ "$LIVE_RESULTS_N" -eq 0 ]; then
  {
    echo "no live-check results collected -- $LIVE_RESULTS_SRC is absent or empty."
    echo "This is UNKNOWN, not clean: the verdicts in 06_live_tests.log have no"
    echo "supporting evidence in this archive."
  } | tee "$LIVE_RESULTS_DST/NOTHING_COLLECTED.txt" | sed 's|^|  |'
  echo "  (recorded in 06_live_results/NOTHING_COLLECTED.txt)"
else
  echo "  collected $LIVE_RESULTS_N file(s), $(du -sh "$LIVE_RESULTS_DST" 2>/dev/null | cut -f1) total"
fi

# Remove the synthetic state now that the checks that needed it have run, so
# the remaining steps and the operator see the box as they found it. The EXIT
# trap is the backstop for an interrupt; this is the normal path, and running
# it here means the state is gone before steps 7-9 inspect anything.
# cleanup_live_seed is idempotent, so the trap firing later is harmless.
cleanup_live_seed
# Restore the operator vault here rather than at EXIT: steps [7]-[9] run
# against the live app, and they should see the box as the operator keeps it,
# not as the capture staged it. Idempotent, so the EXIT backstop still covers
# an interrupt before this point.
cleanup_capture_vault

# ── [7/9] Dev-tool routes against the live app ───────────────────
echo "=== [7/9] Dev-tool routes ==="
DEV_EXIT=0
{
 for route in enabled test_timing dead_css storage_tier_status \
              maintenance_mode i18n_coverage model_pull_check \
              feature_flags golden_files request_replay_list \
              login_flows mem_audit threads leak_scan routes \
              sse_status; do
   echo "--- /api/dev/$route ---"
   response=$(curl -fsS --max-time 30 \
       "$CAPTURE_APP_ORIGIN/api/dev/$route" 2>&1)
   route_exit=$?
   printf '%s\n' "$response" | head -40
   if [ "$route_exit" -ne 0 ]; then
     DEV_EXIT=1
     echo "ERROR: route probe exit=$route_exit"
   fi
   echo
 done
} > "$OUT/07_dev_tools.log" 2>&1
echo "  done"

# ── [7b/9] Live selftest battery ─────────────────────────────────
# Until this stage existed, every selftest check on the box sat permanently
# outside the capture's denominator: no bundle could confirm or refute ANY
# selftest change. A session once grepped a bundle for a WARN, got silence, and
# reported the warning as gone -- true, and worthless.
#
# It is its own stage rather than a row in step [7] because that loop is
# hardcoded to the /api/dev/ prefix, and the grading is NOT `curl && echo ok`
# because curl exits 0 on a 200 carrying {"error":"endpoint not found"} -- the
# defect this script's own header records against sse_smoke. The body is graded
# by tools/selftest_verdict.py, which fails on an empty denominator and passes
# on WARNs.
echo "=== [7b/9] Live selftest battery ==="
SELFTEST_EXIT=0
: "${CAPTURE_APP_ORIGIN:=http://localhost:5555}"
curl -fsS --max-time 120 "$CAPTURE_APP_ORIGIN/api/selftest" \
    > "$OUT/07b_selftest.json" 2> "$OUT/07b_selftest.err"
SELFTEST_CURL_EXIT=$?
if [ "$SELFTEST_CURL_EXIT" -ne 0 ]; then
  SELFTEST_EXIT="$SELFTEST_CURL_EXIT"
  {
    echo "CANNOT EVALUATE: curl exit=$SELFTEST_CURL_EXIT probing /api/selftest"
    cat "$OUT/07b_selftest.err" 2>/dev/null
  } > "$OUT/07b_selftest.log" 2>&1
else
  venv/bin/python tools/selftest_verdict.py "$OUT/07b_selftest.json" \
      > "$OUT/07b_selftest.log" 2>&1
  SELFTEST_EXIT=$?
fi
echo "  exit=$SELFTEST_EXIT"

# ── [8/9] T51 regenerate_goldens dry-run ─────────────────────────
echo "=== [8/9] T51 regenerate_goldens dry-run ==="
venv/bin/python tools/regenerate_goldens.py > "$OUT/08_t51_dryrun.log" 2>&1
T51_EXIT=$?
echo "  exit=$T51_EXIT"

# ── [9/9] Quick HTTP smoke ───────────────────────────────────────
echo "=== [9/9] HTTP smoke ==="
SMOKE_EXIT=0
{
 echo "--- GET / ---"
 if ! curl -fsS -o /dev/null -w "status=%{http_code}  size=%{size_download}  time=%{time_total}s\n" \
     "$CAPTURE_APP_ORIGIN/"; then SMOKE_EXIT=1; fi
 echo "--- GET /api/health ---"
 if ! curl -fsS --max-time 5 "$CAPTURE_APP_ORIGIN/api/health" 2>&1; then
   SMOKE_EXIT=1
 fi
 echo "--- GET /api/dev/routes (count) ---"
 if ! curl -fsS --max-time 30 "$CAPTURE_APP_ORIGIN/api/dev/routes" 2>&1 \
     | venv/bin/python -c "import sys,json; d=json.load(sys.stdin); print('routes:', len(d.get('routes', [])))" 2>&1; then
   SMOKE_EXIT=1
 fi
} > "$OUT/09_http_smoke.log" 2>&1
echo "  done"

# The transient app is no longer needed after the final HTTP probe. Teardown is
# part of the verdict rather than an EXIT-only best effort: an instance whose
# unit, environment, state directory, or port claims cannot be released is an
# UNKNOWN/failed capture, even if every functional check was green.
if [ "$CAPTURE_LEGACY_SINGLETON" = "0" ]; then
  cleanup_capture_instance || CAPTURE_INSTANCE_TEARDOWN_EXIT=$?
fi

# Compute the certification result before bundling, but never stop here: a
# failed run is most useful when all diagnostics still make it into the archive.
# ── did the tree move while we measured it? (backlog 100) ────────
# Written BEFORE the verdict so the grader can read it, and into $OUT so it
# travels in the archive with the run it describes.
#
# The file is created only when drift is real: an ABSENT file means "not
# recorded", which is the state every bundle archived before this cut is in, and
# the grader must not read those as invalid. An EMPTY file means "asked, and the
# answer was no".
TREE_DRIFT_FILE="$OUT/00_tree_drift.txt"
if [ "$_TREE_SNAPSHOT_RC" -eq 0 ]; then
  _DRIFT_OUT=""
  _DRIFT_RC=0
  _DRIFT_OUT="$(bd_tree_state_drift "$_TREE_SNAPSHOT" "$(dirname "$0")")" \
    || _DRIFT_RC=$?
  if [ "$_DRIFT_RC" -eq 1 ]; then
    printf '%s\n' "$_DRIFT_OUT" > "$TREE_DRIFT_FILE"
    echo "WARNING: the working tree CHANGED during this run:" >&2
    sed 's/^/    /' "$TREE_DRIFT_FILE" >&2
  elif [ "$_DRIFT_RC" -eq 0 ]; then
    : > "$TREE_DRIFT_FILE"
  else
    echo "WARNING: end-of-run tree snapshot UNKNOWN -- drift not judged." >&2
  fi
fi

echo "=== [verdict] Certification result ==="
venv/bin/python tools/capture_verdict.py \
  --tree-drift-file "$TREE_DRIFT_FILE" \
  --tests-json "$OUT/02_test_results.json" \
  --live-log "$OUT/06_live_tests.log" \
  --suite-exit "$SUITE_EXIT" \
  --live-exit "$LIVE_EXIT" \
  --expected-live-tests "$EXPECTED_LIVE_TESTS" \
  --stage-exit "service-stopped=$SERVICE_STOP_EXIT" \
  --stage-exit "parity-inventory=$PARITY_EXIT" \
  --stage-exit "graph=$GRAPH_EXIT" \
  --stage-exit "csrf=$CSRF_EXIT" \
  --stage-exit "service-install=$INSTALL_EXIT" \
  --stage-exit "service-active=$SERVICE_EXIT" \
  --stage-exit "service-ready=$SERVICE_READY_EXIT" \
  --stage-exit "ai-boot-observation=$AI_BOOT_EXIT" \
  --stage-exit "dev-tools=$DEV_EXIT" \
  --stage-exit "selftest=$SELFTEST_EXIT" \
  --stage-exit "goldens=$T51_EXIT" \
  --stage-exit "http-smoke=$SMOKE_EXIT" \
  --stage-exit "capture-instance-teardown=$CAPTURE_INSTANCE_TEARDOWN_EXIT" \
  > "$OUT/10_VERDICT.txt" 2>&1
FINAL_EXIT=$?
cat "$OUT/10_VERDICT.txt"

# ── Blind spots ─────────────────────────────────
#
# WHY THIS EXISTS (backlog 54, @1058). A verdict that does not say what it
# could not look at reports OK truthfully and uselessly -- CLAUDE.md section 0.
# The socket recorder is the model: it prints its own blind spots on every run
# rather than burying them in a README, because a caveat the reader can skip is
# a caveat nobody reads. This block is emitted AFTER the verdict, deliberately:
# the last line printed is the only line anybody reads.
#
# It is `tee`d rather than echoed so it also lands in the tarball the operator
# uploads -- a warning that scrolls out of a terminal is not evidence.
BLIND_SPOTS="$OUT/11_BLIND_SPOTS.txt"
{
  echo "=== [blind spots] what this verdict does NOT cover ==="
  echo
  echo "  * CROSS-FILE STATE LEAKS -- INVISIBLE HERE."
  echo "    This capture runs two lanes: a parallel lane"
  echo "    (-m capture_parallel -n N --dist loadfile) and a serial lane"
  echo "    (-n 0). Neither reproduces the co-batching of a whole-suite"
  echo "    'pytest tests/' run, so a test that wipes bulk_downloader.* from"
  echo "    sys.modules and orphans a later module's import-time binding"
  echo "    cannot fire in either lane."
  echo "    MEASURED at v3.66.1034: test6 passed capture at 15547 pass /"
  echo "    0 fail while the tree carried all 14 known leakers, and a plain"
  echo "    'pytest tests/' on the same commit failed between 5 and 35."
  echo "    A green capture is not evidence about that class in EITHER"
  echo "    direction. The instrument for it is a full 'pytest tests/' under"
  echo "    matched load, not this script."
  echo
  echo "  * TIMEZONE-DEPENDENT DEFECTS -- CANNOT REPRODUCE HERE."
  echo "    This host runs $(timedatectl show -p Timezone --value 2>/dev/null || echo UNKNOWN)."
  echo "    A defect that only appears where the local date differs from the"
  echo "    UTC date is dormant on a UTC box. Tests for that class must force"
  echo "    TZ and exercise both signs, or they prove nothing here."
  echo
  echo "  A PASS above is evidence about what these two lanes CAN see."
  echo "  It is not evidence about the classes named here."
} | tee "$BLIND_SPOTS"

# ── Bundle ───────────────────────────────────────────────────────
echo "================================================================"
echo "  Bundling..."
tar czf "$ARCHIVE" -C /tmp "$(basename $OUT)"
BUNDLE_EXIT=$?
if [ "$BUNDLE_EXIT" -eq 0 ]; then
  ls -la "$ARCHIVE"
  echo "  Archive: $ARCHIVE"
  echo "  Contents:"
  tar tzf "$ARCHIVE" | sed 's|^|    |'
else
  echo "  ERROR: could not create $ARCHIVE (exit=$BUNDLE_EXIT)" >&2
  FINAL_EXIT=2
fi
echo "================================================================"
if [ "$FINAL_EXIT" -eq 0 ]; then
  echo "  PASS. Upload $ARCHIVE for the complete evidence bundle."
elif [ "$BUNDLE_EXIT" -ne 0 ]; then
  echo "  FAIL (exit=$FINAL_EXIT). No archive was created."
else
  echo "  FAIL (exit=$FINAL_EXIT). Upload $ARCHIVE for diagnosis."
fi
echo "================================================================"
exit "$FINAL_EXIT"
