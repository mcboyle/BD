#!/usr/bin/env bash
#
# deploy.sh -- the GIT deploy path for BulkDownloader on the box.
#
# WHAT REPLACED WHAT. This file used to drive the F0.1 ZIP OVERLAY deploy
# (--zip, a sha256 gate over a release archive, `unzip -o`). CLAUDE.md section 7
# records that the box now updates with `git fetch origin main` +
# `git reset --hard origin/main` + a service restart, and that "there is no zip
# overlay and no zip fallback". A script that automates a deploy path nobody
# runs is a gate whose subject no longer exists.
#
# WHY IT IS NOT JUST THOSE THREE COMMANDS. A deploy MOVES FILES. IT DOES NOT
# MAKE THE RUNNING SYSTEM MATCH THEM. Every step below closes a gap that
# survived the move from `unzip -o` to git, because not one of them was ever a
# property of the overlay either:
#
#   * requirements.txt gains an entry and NOTHING on the deploy path runs pip
#     (the only `pip install -r` callers are install_linux.sh and
#     scripts/cloud-setup.sh, neither of which runs on a git deploy). lxml was
#     declared and the box never installed it.
#   * frontend/dist/ holds ZERO tracked files and is gitignored, so git never
#     delivers it. A missing or stale bundle is a silent 503.
#   * __pycache__/*.pyc are not cleared by `git reset --hard` (the v3.66.161
#     stale-bytecode footgun).
#   * reports/gui_parity_inventory.json is gitignored and build-time generated;
#     `git clean -fd` cannot evict it (that needs -x). A stale copy reads as
#     parity drift and failed an otherwise-green 13389-pass run at v3.66.818.
#   * the graph content pin lives OUTSIDE the repo under /var/lib/, so a reset
#     never delivers it either, and capture.sh step [2b] then reports drift
#     which capture_verdict.py turns into a whole-capture FAIL.
#   * whether that pin belongs to THIS deployed tree was not recorded at all, so
#     a capture in a never-deployed checkout could only misgrade drift as FAIL.
#   * the service is not restarted, so the process keeps running the old tree.
#
# THE DESIGN RULE (CLAUDE.md section 0). Every step VERIFIES what it did rather
# than assuming the command that exited 0 achieved it, and a step that cannot
# verify SAYS SO AND FAILS -- unknown is a third state. Concretely: pip exiting 0
# is not proof a requirement resolves; `npm run build` exiting 0 is not proof a
# bundle exists; `systemctl stop` exiting 0 is not proof the unit is inactive;
# --write-hash exiting 0 is not proof the gate can read the pin; and a health
# probe that cannot reach the port is UNKNOWN, not "down" and certainly not
# "up". The inverse rule applies with equal force: a deploy that changes
# nothing must say "already current" and exit 0, never manufacture drift -- a
# gate that cries wolf gets switched off.
#
# NOTHING IS DESTROYED SILENTLY. `git reset --hard` has no equivalent of
# `unzip -x`, so it discards operator live-edits the overlay was configured to
# preserve (docs/repo/FRESH_HOST_BRINGUP.md). This script REFUSES rather than
# resetting over them, and prints exactly what it would have destroyed.
#
# EXIT CODES (the caller's remedy differs for each; mirrors bd-guardcheck):
#   0  deployed-and-verified, OR already-current-and-verified
#   1  a step or a verification FAILED -- the state is NOT known good
#   2  refusal / precondition -- NOTHING was mutated; fix the inputs
#
# Flags:
#   --dir PATH        install dir (default: $BD_DEPLOY_DIR, else ~/BulkDownloader)
#   --expect-commit S the 40-hex commit this deploy is SUPPOSED to land
#   --discard-local   proceed over operator live edits, after listing them
#   --skip-graph-pin  skip the graph content re-pin (the next capture WILL drift)
#   --health-url URL  (default: http://localhost:5555/api/health)
#   --timeout SECS    health-poll budget    (default 120)
#   --interval SECS   health-poll interval  (default 2)
#   -h, --help        print this usage and exit 0 WITHOUT touching anything
#
# Env overrides (all four are already ledgered in reports/config_gui_manifest.json;
# do NOT introduce a new BD_-prefixed name here -- the config-surface scan globs
# scripts/*.sh for the BD_ prefix, so even a shell local enters the ledger
# denominator and reads as promoted-but-unledgered, which is how v3.66.836 failed
# the parity gate on the box):
#   BD_DEPLOY_DIR      default install dir
#   BD_VENV_PYTHON     venv python (default: "$DIR/venv/bin/python")
#   BD_GRAPH_HASH_PIN  graph content pin path; the default below MUST stay
#                      byte-identical to capture.sh's and provision_test_host.sh's
#   BD_RESTART_CMD     the zip era's whole-restart override. It cannot express
#                      this script's stopped window, so it is REFUSED rather than
#                      silently ignored -- see step [0].
#
# THE EXPECTED VERSION IS DERIVED; THE INTENDED COMMIT IS DECLARED. These are
# different questions and conflating them is what shipped a 21-version-stale
# host as DEPLOY OK on 2026-08-29. The VERSION the health gate demands is read
# out of the tree that was just reset to, so it can never disagree with what
# landed -- that stays derived and there is no flag for it. The COMMIT the
# deploy was FOR cannot be derived here at all on a host whose `origin` is a
# LOCAL BARE MIRROR (two fleet boxes clone from /home/mboyle/bd.git on that same
# box): `git fetch origin` succeeds, returns 0, delivers nothing, and leaves the
# checkout exactly where it was. Everything downstream then agreed with itself
# -- tree version == health version == the stale tree -- and the script printed
# success having compared the deployed commit to NOTHING.
#
# So step [1b] establishes an INTENDED COMMIT before anything is mutated, step
# [4] resets to THAT rather than to whatever origin/main names, and step [13]
# re-asserts the tree is still it before any verdict is printed. It comes from
# --expect-commit when the operator states it, or from origin/main when and
# only when `origin` is the official origin. Neither available means the
# intended commit is UNMEASURED, and unknown is a third state that FAILS
# (CLAUDE.md A2/A7) -- it refuses at step [1b], before the first side effect.
#
# Operator-executed. coreutils + git + curl; the only Python it runs is the
# tree's own tools under the venv interpreter.
#
set -euo pipefail

STEP=0
SERVICE_STOPPED=0
DEPLOY_SUCCEEDED=0
EXIT_HANDLER_RUNNING=0
# A FAILURE INSIDE THE STOPPED WINDOW MUST NOT LEAVE PRODUCTION DOWN.
# Steps 8-10 run with the unit deliberately inactive, so anything that dies in
# there parks the box with the service off -- measured at v3.66.1035, when an
# orphaned pytest on the target wrote .pyc files while step 9 removed them and
# test4 sat down until someone looked. Aborting was right; aborting silently was
# not. SERVICE_STOPPED is the recovery debt opened immediately before the stop
# request: even a failing systemctl call may have stopped the unit before it
# returned. A precondition refusal still never reaches that assignment and
# therefore never starts a unit the operator had deliberately left down.
#
# Recovery belongs to the EXIT guard below rather than to the die function.
# Errexit can terminate the shell without calling die, and an ERR trap misses
# valid shell contexts. The tree is already at the new commit by then, so a recovered
# service is running a PARTIAL deploy -- louder than a silent outage, and the
# message says to re-run.
die() {
  printf 'deploy.sh: FAIL [step %s]: %s\n' "$STEP" "$*" >&2
  exit 1
}
refuse() { printf 'deploy.sh: REFUSED [step %s]: %s\n' "$STEP" "$*" >&2; exit 2; }
note()   { printf 'deploy.sh: [step %s] %s\n' "$STEP" "$*"; }

usage() {
  # Printed verbatim. Deliberately NOT sliced out of this file's own comment
  # header by line number: a fixed-width / fixed-line window silently starts
  # quoting the wrong thing the moment anything above it moves.
  cat <<'USAGE'
deploy.sh -- the git deploy path for BulkDownloader.

  --dir PATH        install dir (default: $BD_DEPLOY_DIR, else ~/BulkDownloader)
  --expect-commit S the 40-hex commit this deploy is SUPPOSED to land. Required
                    when `origin` is not the official origin, because a fetch
                    from a mirror succeeds whether or not the mirror is current
  --discard-local   proceed over operator live edits, after listing them
  --skip-graph-pin  skip the graph content re-pin (the next capture WILL drift)
  --health-url URL  default http://localhost:5555/api/health
  --timeout SECS    health-poll budget    (default 120)
  --interval SECS   health-poll interval  (default 2)
  -h, --help        print this and exit 0 without touching anything

exit 0  deployed-and-verified, or already-current-and-verified
exit 1  a step or a verification FAILED -- the state is NOT known good
exit 2  refusal / precondition -- NOTHING was mutated
USAGE
}

# THE OFFICIAL ORIGIN. CLAUDE.md A1 names it: `mcboyle/BD`. This predicate is
# the only thing between "the fetch succeeded" and "the fetch reached the
# repository whose main IS the project's main", and on two fleet hosts those are
# different repositories: each clones from a LOCAL BARE MIRROR at
# /home/mboyle/bd.git on that same host, so a fetch that nothing has pushed into
# returns 0 and delivers nothing.
#
# EXACT MATCHES, NEVER GLOBS. A pattern loose enough to admit a fork
# (mcboyle/BD-fork) or a look-alike host would put the step [1b] refusal out of
# reach while still reading as a check -- a gate that cannot see its subject.
# Case is folded and one trailing slash and one .git suffix are stripped because
# all four spellings name the same GitHub repository; nothing else is accepted.
# The locals are unprefixed on purpose: a BD_-prefixed shell local enters
# tests/test_gui_parity.py's config-surface denominator (CLAUDE.md A8).
_origin_is_authoritative() {
  _oia_url="$(printf '%s' "${1:-}" | tr 'A-Z' 'a-z')"
  _oia_url="${_oia_url%/}"
  _oia_url="${_oia_url%.git}"
  case "$_oia_url" in
    https://github.com/mcboyle/bd)    return 0;;
    http://github.com/mcboyle/bd)     return 0;;
    ssh://git@github.com/mcboyle/bd)  return 0;;
    git@github.com:mcboyle/bd)        return 0;;
  esac
  return 1
}

DIR=""
DISCARD=0
EXPECT_COMMIT=""
SKIP_GRAPH=0
HEALTH_URL="http://localhost:5555/api/health"
TIMEOUT=120
INTERVAL=2
# Preserve the operator's exact argv for the post-reset handoff. This is a Bash
# array because paths and arguments may contain whitespace; rebuilding a shell
# command string here would turn the safety handoff into an injection boundary.
DEPLOY_ARGS=("$@")
handoff_expected="${DEPLOY_POST_RESET_EXPECT:-}"
unset DEPLOY_POST_RESET_EXPECT

while [ $# -gt 0 ]; do
  case "$1" in
    --dir)            DIR="${2:-}"; shift 2;;
    --expect-commit)  EXPECT_COMMIT="${2:-}"; shift 2;;
    --discard-local)  DISCARD=1; shift;;
    --skip-graph-pin) SKIP_GRAPH=1; shift;;
    --health-url)     HEALTH_URL="${2:-}"; shift 2;;
    --timeout)        TIMEOUT="${2:-}"; shift 2;;
    --interval)       INTERVAL="${2:-}"; shift 2;;
    -h|--help)        usage; exit 0;;
    *) refuse "unknown argument: $1";;
  esac
done

# ── [0] preconditions -- refuse before mutating anything ─────────────
# Everything here is checked BEFORE the first side effect, so exit 2 always
# means the tree, the service and the pin are exactly as they were.

case "$TIMEOUT" in ''|*[!0-9]*) refuse "--timeout must be a whole number of seconds, got: $TIMEOUT";; esac
case "$INTERVAL" in ''|*[!0-9]*) refuse "--interval must be a whole number of seconds, got: $INTERVAL";; esac
[ "$INTERVAL" -gt 0 ] || refuse "--interval must be greater than 0"

# A FULL SHA, NOT AN ABBREVIATION. The caller is automation that has just
# pushed the object and holds all 40 characters; an abbreviation buys nothing
# and becomes ambiguous as history grows, at which point rev-parse resolves it
# to the wrong commit or to none and this script would be arguing about a
# different thing than the operator was. Checked here, in the pre-mutation
# block, so a typo costs exit 2 and no side effect.
if [ -n "$EXPECT_COMMIT" ]; then
  EXPECT_COMMIT="$(printf '%s' "$EXPECT_COMMIT" | tr 'A-Z' 'a-z')"
  case "$EXPECT_COMMIT" in
    *[!0-9a-f]*) refuse "--expect-commit must be a full 40-character hex commit
  sha; got: $EXPECT_COMMIT";;
  esac
  [ "${#EXPECT_COMMIT}" -eq 40 ] || refuse "--expect-commit must be a full
  40-character hex commit sha, not an abbreviation; got: $EXPECT_COMMIT"
fi

# BD_RESTART_CMD is honoured by being REFUSED, not by being ignored. The zip
# deploy restarted in one command; this one needs a STOPPED WINDOW (the bytecode
# sweep and the parity regen are unsafe against a live service), so a
# whole-restart override cannot be expressed here. Silently dropping an override
# the operator deliberately set is the same class of defect as a gate that
# cannot see its subject: it reports success having ignored the question.
if [ -n "${BD_RESTART_CMD:-}" ]; then
  refuse "BD_RESTART_CMD is set ($BD_RESTART_CMD) but this deploy needs a STOPPED
  WINDOW, not a restart: the bytecode sweep (step 9) and the parity inventory
  regen (step 10) must run while the unit is confirmed inactive. It is refused
  rather than ignored so the setting cannot silently do nothing. Unset it; the
  service commands are 'sudo systemctl stop|start bulkdownloader'."
fi

# The cwd is deliberately NOT a fallback for --dir. A deploy that infers its
# target from wherever you happen to be standing will `git reset --hard` that
# tree, and the whole point of step [3] is that this script does not destroy
# work by surprise. An absent default dir is a refusal, which costs the operator
# one flag; guessing costs a work tree.
[ -n "$DIR" ] || DIR="${BD_DEPLOY_DIR:-$HOME/BulkDownloader}"
[ -d "$DIR" ] || refuse "install dir not found: $DIR (pass --dir, or set BD_DEPLOY_DIR)"
DIR="$(cd "$DIR" && pwd -P)"

# A DEPLOY AND A TEST RUN CANNOT BOTH WIN THE SAME __pycache__. Step 9 sweeps
# bytecode; a live suite recreates it faster than rm removes it, and the sweep
# then dies with "Directory not empty" -- INSIDE the stopped window, leaving the
# service down. Measured at v3.66.1035 on test4, twice, because the retry hits
# the same live writer.
#
# Placed AFTER $DIR is resolved, because the check is scoped to this install dir
# and cannot be asked before we know which one that is.
_running_pytest() {
  # WHAT THIS CAN AND CANNOT SEE -- read before trusting a clean answer.
  #
  # DETECTS: a pytest process whose CWD is inside $DIR. That is the shape of the
  # measured incident -- an xdist master started with `cd ~/BulkDownloader &&
  # pytest`, whose workers hammered $DIR's __pycache__ while the master sat in
  # $DIR.
  #
  # CANNOT DETECT: a SERIAL run, because tests/conftest.py chdirs every test
  # into tmp_path, so the one process has no cwd near $DIR by the time you ask.
  # Two other discriminators were tried and are worse than useless: the command
  # line alone answers DETECTED on an idle host (the invoking shell's own argv
  # says "-m pytest"), and /proc/<pid>/exe resolves a venv python to
  # /usr/bin/python3.12 -- OUTSIDE $DIR -- so an exe scope never fires at all.
  # Both were measured at v3.66.1037.
  #
  # So this is a cheap filter for the common case, NOT a guarantee. The EXIT
  # guard makes a collision survivable; this only avoids the collision when it
  # can be seen.
  local pid cwd
  for pid in $(ps -eo pid=,comm=,args= 2>/dev/null \
               | awk '$2 ~ /^python/ && $0 ~ /-m[ ]pytest/ { print $1 }'); do
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null)" || continue
    case "$cwd" in "$DIR"|"$DIR"/*) return 0;; esac
  done
  return 1
}
if _running_pytest; then
  refuse "a pytest run is in flight with its working directory inside $DIR.
  Step 9 sweeps __pycache__ inside the STOPPED window and a live suite recreates
  it faster than rm can remove it, so the sweep dies with the service already
  down. Wait for the run, or reap it, then re-run."
fi


[ -e "$DIR/.git" ] || refuse "not a git work tree: $DIR"
[ -f "$DIR/bulk_downloader/__init__.py" ] \
  || refuse "not a BulkDownloader tree (no bulk_downloader/__init__.py): $DIR"
command -v git >/dev/null 2>&1 || refuse "git is not on PATH"
command -v curl >/dev/null 2>&1 || refuse "curl is not on PATH"

VENV_PY="${BD_VENV_PYTHON:-$DIR/venv/bin/python}"
[ -x "$VENV_PY" ] || refuse "venv python is not executable: $VENV_PY"

GTMP=""
cleanup() { if [ -n "$GTMP" ]; then rm -rf -- "$GTMP"; fi; }
on_exit() {
  exit_status=$?
  # Disable the trap before any recovery command can itself exit. The explicit
  # state flag is a second re-entry barrier and is exercised independently.
  trap - EXIT
  if [ "${EXIT_HANDLER_RUNNING:-0}" = 1 ]; then
    return
  fi
  EXIT_HANDLER_RUNNING=1

  # Recovery and cleanup are best-effort work performed while preserving the
  # triggering status. In particular, errexit must not abort this handler.
  set +e
  if [ "${SERVICE_STOPPED:-0}" = 1 ] \
     && [ "${DEPLOY_SUCCEEDED:-0}" != 1 ]; then
    printf 'deploy.sh: *** EXIT AFTER SERVICE STOP REQUEST [step %s, status %s] ***\n' \
      "$STEP" "$exit_status" >&2
    # THE DEBT IS OPENED BEFORE THE STOP, SO IT CAN BE OWED OVER A LIVE UNIT.
    # Step 8 arms SERVICE_STOPPED before issuing the request, which is what lets
    # a failing-but-effective stop be recovered -- and the same widening means a
    # stop that never took (unit still active) also reaches here. Starting a
    # running service and announcing RESTARTED-PARTIAL-DEPLOY would reproduce
    # this row's own fail-open shape inside its remedy: a comforting banner
    # about a recovery that never happened. Skip the start ONLY on affirmative
    # evidence the unit is active; `is-active --quiet` exits 0 for exactly that
    # and nonzero for inactive, failed, unknown, and an unreachable systemctl,
    # so every unmeasured reading still attempts the recovery and FAILS CLOSED.
    if systemctl is-active --quiet bulkdownloader; then
      printf 'deploy.sh: *** SERVICE-NEVER-STOPPED ***\n' >&2
      printf 'deploy.sh: the unit is still active, so no emergency start was issued.\n' >&2
      printf 'deploy.sh: the tree may have moved and later steps did not finish. Re-run me.\n' >&2
    else
      printf 'deploy.sh: attempting emergency start of bulkdownloader\n' >&2
      sudo systemctl start bulkdownloader >/dev/null 2>&1
      if systemctl is-active --quiet bulkdownloader; then
        printf 'deploy.sh: *** RESTARTED-PARTIAL-DEPLOY ***\n' >&2
        printf 'deploy.sh: the tree moved and later steps did not finish. Re-run me.\n' >&2
      else
        printf 'deploy.sh: *** SERVICE-IS-DOWN ***\n' >&2
        printf 'deploy.sh: emergency start failed; restore the unit before leaving.\n' >&2
      fi
    fi
    # An exit before the recorded success point is never a successful deploy,
    # even if the command that left the stopped window happened to return zero.
    [ "$exit_status" -ne 0 ] || exit_status=1
  fi
  cleanup
  exit "$exit_status"
}

# Installed before any path can set SERVICE_STOPPED=1. There is deliberately no
# ERR trap: command substitutions, conditionals and pipelines do not share one
# reliable ERR inheritance model, while every ordinary shell exit reaches EXIT.
trap on_exit EXIT

cd "$DIR"
if [ -n "$handoff_expected" ]; then
  handoff_actual="$(git rev-parse HEAD 2>/dev/null || true)"
  [ "$handoff_actual" = "$handoff_expected" ] \
    || die "post-reset handoff expected tree $handoff_expected but the new
  script started at ${handoff_actual:-<unresolved>}; refusing to certify either
  body"
  note "post-reset handoff entered the new script body for $handoff_actual"
fi
note "preconditions OK: $DIR (venv python: $VENV_PY)"

# ── [1] fetch ───────────────────────────────────────────────────────
# Bare `--prune origin`, never a refspec-scoped prune. GitHub's auto-delete
# removes a merged head branch but the local origin/<branch> ref survives as a
# dead baseline; a refspec-scoped prune (`--prune origin main`) collects nothing
# else and leaves every other stale ref in place (CLAUDE.md section 2a).
STEP=1
git fetch --prune origin >/dev/null 2>&1 || die "git fetch --prune origin failed"
# THE URL IS EVIDENCE, NOT DECORATION. Exit 0 from fetch says a repository
# answered, never which repository, and step [1b] is about exactly that
# difference. Printed on every run so the operator reading a verdict can see
# what it was a verdict about.
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
ORIGIN_MAIN="$(git rev-parse origin/main 2>/dev/null || true)"
[ -n "$ORIGIN_MAIN" ] || die "origin/main does not resolve after a successful fetch"
note "fetched from ${ORIGIN_URL:-<no origin url>}; origin/main = $ORIGIN_MAIN"

# ── [1b] the commit this deploy is SUPPOSED to land ─────────────────
# Before this step existed, "what should be running here" had no representation
# anywhere in the script, so no later check could be wrong about it. Everything
# after this point lands $NEW and is verified against $NEW.
STEP=1b
if [ -n "$EXPECT_COMMIT" ]; then
  # The object must be PRESENT after the fetch. This is the stale-mirror shape
  # seen from the operator's side: they pushed nothing into the host's bare
  # repo, the fetch still returned 0, and the commit they asked for is simply
  # not here. Nothing but its absence can report that.
  git rev-parse --verify --quiet "${EXPECT_COMMIT}^{commit}" >/dev/null 2>&1 \
    || refuse "INTENDED-COMMIT-ABSENT: --expect-commit named $EXPECT_COMMIT, the
  fetch from ${ORIGIN_URL:-<no origin url>} SUCCEEDED, and that commit is still
  not in this repository -- origin/main there is $ORIGIN_MAIN. A fetch exiting 0
  is not evidence it delivered anything. If that remote is a local mirror, push
  the object into it first:
      git push <mirror> $EXPECT_COMMIT:refs/heads/main
  Nothing has been changed."
  NEW="$EXPECT_COMMIT"
  INTENDED_SOURCE="--expect-commit"
  if [ "$NEW" != "$ORIGIN_MAIN" ]; then
    note "NOTE: origin/main at ${ORIGIN_URL:-<no origin url>} is $ORIGIN_MAIN,
  which is NOT the intended commit. This deploy lands the INTENDED commit; the
  operator's stated target outranks whatever that remote's main happens to name."
  fi
elif _origin_is_authoritative "$ORIGIN_URL"; then
  NEW="$ORIGIN_MAIN"
  INTENDED_SOURCE="origin/main at the official origin"
else
  refuse "ORIGIN-NOT-AUTHORITATIVE: origin is ${ORIGIN_URL:-<no origin url>},
  which is not the official origin (mcboyle/BD), so origin/main here is a MIRROR
  of unknown currency and the commit this deploy should land is UNMEASURED. A
  fetch from a mirror succeeds whether or not anything has been pushed into it:
  on 2026-08-29 that shape certified a host whose tree was 21 versions stale.
  (The verdict strings this script prints on success are deliberately NOT
  quoted in this message: a refusal that contains them reads as one of them to
  anything grepping the output.)
  Unknown is a third state and it fails rather than guessing. Re-run with
  --expect-commit <40-hex sha> naming the commit this host is meant to run.
  Nothing has been changed."
fi
note "intended commit is $NEW (source: $INTENDED_SOURCE)"

# ── [2] show what is about to land, BEFORE any mutation ─────────────
STEP=2
OLD="$(git rev-parse HEAD)"
if [ "$OLD" = "$NEW" ]; then
  SAME=1
  note "source already current at $OLD"
else
  SAME=0
  note "incoming commits (HEAD..$NEW):"
  git log --oneline "HEAD..$NEW"
  note "incoming diffstat:"
  git diff --stat HEAD "$NEW"
fi

# ── [3] operator live-edit gate ─────────────────────────────────────
# Refusal, not a prompt: this must be safe to run non-interactively, and the
# operator's remedy is already written down in docs/repo/FRESH_HOST_BRINGUP.md.
STEP=3

# Untracked, non-ignored files are reported for information only. `git reset
# --hard` does not delete them, so they are not at risk and must not block a
# deploy -- blocking on something that cannot be lost is a gate firing on
# identity.
UNTRACKED="$(git ls-files --others --exclude-standard)"
if [ -n "$UNTRACKED" ]; then
  note "untracked files present (a reset will NOT delete these):"
  printf '%s\n' "$UNTRACKED"
fi

DIRTY="$(git status --porcelain --untracked-files=no)"

# Committed-but-unpushed work is destroyed by `git reset --hard` just as surely
# as an uncommitted edit, and `git status` is silent about it. Ask the question
# git actually answers: is HEAD an ancestor of what we are about to reset to?
LOCAL_COMMITS=""
if [ "$SAME" -eq 0 ] && ! git merge-base --is-ancestor HEAD "$NEW"; then
  LOCAL_COMMITS="$(git log --oneline "$NEW..HEAD")"
fi

if [ -n "$DIRTY" ] || [ -n "$LOCAL_COMMITS" ]; then
  if [ -n "$DIRTY" ]; then
    note "local modifications a reset would DESTROY:"
    printf '%s\n' "$DIRTY"
    git diff --stat || true
  fi
  if [ -n "$LOCAL_COMMITS" ]; then
    note "commits on HEAD that are NOT in the intended commit and would be DESTROYED:"
    printf '%s\n' "$LOCAL_COMMITS"
  fi
  if [ "$DISCARD" -eq 0 ]; then
    refuse "refusing to discard operator work listed above. Commit it (see
  docs/repo/FRESH_HOST_BRINGUP.md) and push, or re-run with --discard-local to
  destroy it deliberately. Nothing has been changed."
  fi
  note "--discard-local given: the work listed above is being DESTROYED"
fi

# ── [4] reset ───────────────────────────────────────────────────────
#
# SELF-MODIFICATION BOUNDARY. This script is one of the files `git reset --hard`
# replaces, but the
# running bash keeps reading from the file descriptor it opened at exec time,
# and git does not rewrite the file in place: it writes a new object and renames
# it over the path, so the path gets a NEW inode while the old one stays open
# behind our fd. Continuing here would execute steps [5]-[13] from the old body
# and could report success without ever running deploy logic delivered by this
# reset. The handoff below closes that boundary before the service stops.
#
# MEASURED, not reasoned about (2026-08-03): a two-commit reproduction in which
# the only difference between the deployed and the incoming script was a line
# AFTER the reset printed the OLD line's text while `grep` on the same file
# immediately afterwards showed the NEW text; `ls -i` went 1992621 -> 1992622
# across the reset, confirming the rename/new-inode mechanism.
#
STEP=4
# THE TARGET IS THE INTENDED COMMIT, NOT THE REF. Resetting to `origin/main`
# re-reads the remote-tracking ref and would silently land whatever it names --
# including a mirror's stale main, and including a commit that moved after step
# [1b] decided what this run was for.
git reset --hard "$NEW" >/dev/null || die "git reset --hard $NEW failed"
[ "$(git rev-parse HEAD)" = "$NEW" ] \
  || die "reset reported success but HEAD is $(git rev-parse HEAD), not $NEW"
note "tree reset to $NEW"

# A changed tree MUST execute the copy the reset just installed. This remains
# the operator-authorized origin/main path; the only alternative is certifying
# that tree using the pre-reset inode. No service has been stopped yet, so an
# exec failure cannot strand production. A moving origin may cause another
# reset/handoff in the child, which is correct: only the final current body may
# perform steps [5]-[13].
if [ "$SAME" -eq 0 ]; then
  note "post-reset handoff: executing deploy logic from $NEW before step 5"
  cleanup
  trap - EXIT
  DEPLOY_POST_RESET_EXPECT="$NEW" \
    exec "$BASH" "$DIR/scripts/deploy.sh" "${DEPLOY_ARGS[@]}" \
    || die "could not execute the post-reset deploy script for $NEW"
fi

# The version the health gate will demand is DERIVED from the tree that was just
# deployed, never passed in. An --expect flag can disagree with what actually
# landed; this cannot. Read with sed so it costs no interpreter start and cannot
# import a half-installed package.
TREE_VERSION="$(sed -n 's/^__version__[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
    bulk_downloader/__init__.py | head -1)"
[ -n "$TREE_VERSION" ] \
  || die "could not derive __version__ from bulk_downloader/__init__.py after the reset"
note "tree version is $TREE_VERSION"
DEPLOY_TREE="$(git rev-parse 'HEAD^{tree}' 2>/dev/null || true)"
[ -n "$DEPLOY_TREE" ] \
  || die "could not derive the exact Git tree SHA after the reset"
note "tree identity is $DEPLOY_TREE"

# ── [5] requirements converge ───────────────────────────────────────
# Keyed on the CHECK, not on the diff: a requirements change pulled in by an
# earlier unscripted deploy still converges here. `pip check` cannot answer this
# -- its denominator is the set of INSTALLED distributions, which structurally
# excludes an uninstalled requirement (CLAUDE.md section 5).
#
# BOTH MANIFESTS, and the second one is why this is a function (v3.66.862).
# Until then this step converged requirements.txt ONLY. v3.66.861 declared
# pyflakes in requirements-dev.txt to close a box capture failure, the next
# capture failed on pyflakes AGAIN, and the reason was structural: the
# declaration landed in a manifest nothing on the deploy path reads, so it
# could not have worked. The fix had reproduced the shape of the defect it was
# fixing (CLAUDE.md section 0).
#
# WHY A DEPLOY SCRIPT INSTALLS TEST DEPENDENCIES. The box is the gate (section
# 7) -- capture.sh runs the full suite there, and several gates in that suite
# shell out to dev tooling. bd-tool-smoke FAILS CLOSED without pyflakes
# ("this gate verified NOTHING ... refusing to report clean over an absent
# analyzer"), which is correct behaviour and turns an undeclared dependency
# into a red capture rather than a silent pass. So the suite's dependencies
# ARE deploy-path dependencies here, and converging them is this step's job.
STEP=5
DID_PIP=0

converge_reqs() {
  # $1 -- a requirements manifest, relative to $DIR. Converges it or dies.
  _req_file="$1"
  [ -f "$_req_file" ] \
    || die "$_req_file is tracked but absent after the reset -- the tree is not
  what this script believes it is. Unknown is a third state and it fails."
  _rc=0
  _missing="$("$VENV_PY" tools/check_requirements.py "$_req_file")" || _rc=$?
  if [ "$_rc" -eq 2 ]; then
    die "requirements check could not evaluate $_req_file -- treat as NOT
  satisfied. Unknown is a third state and it fails; rendering it as 'satisfied'
  is the defect this check exists to prevent."
  elif [ "$_rc" -ne 0 ]; then
    note "requirements that do not resolve in $_req_file: $_missing -- installing"
    "$VENV_PY" -m pip install -r "$_req_file" \
      || die "pip install -r $_req_file failed (missing: $_missing)"
    DID_PIP=1
    # pip exiting 0 is not resolution. Re-ask the same question with the same
    # instrument, against the same interpreter.
    _rc=0
    _missing="$("$VENV_PY" tools/check_requirements.py "$_req_file")" || _rc=$?
    if [ "$_rc" -eq 2 ]; then
      die "requirements check could not evaluate $_req_file after the install"
    elif [ "$_rc" -ne 0 ]; then
      die "still unresolved after pip install -r $_req_file: $_missing"
    fi
    note "$_req_file now resolves under $VENV_PY"
  else
    note "every $_req_file entry already resolves; pip skipped"
  fi
}

converge_reqs requirements.txt
# NOT requirements-dev.txt: that one also carries the packaging chain
# (pyinstaller, nuitka, zstandard) and nuitka needs gcc + patchelf. The
# suite's own dependencies live in requirements-test.txt precisely so a
# deploy can converge them without provisioning a build host.
converge_reqs requirements-test.txt

# ── [5b] CloakBrowser capability converge + reach proof ────────────
# requirements-cloak.txt remains posture-sensitive and is deliberately NOT a
# converge_reqs input: its package stays isolated from requirements.txt.  The
# manifest's own browser-install command is nevertheless mandatory here.  A
# successful download is then proved at the Playwright API boundary by loading
# a local page and reading a marker from the rendered DOM; binary presence or
# an import alone cannot establish browser reach.
STEP=5b
CAP_FILE=requirements-cloak.txt

install_cloak_browsers() {
  "$VENV_PY" -m cloakbrowser install
}

cloak_manifest_present() {
  [ -f "$CAP_FILE" ]
}

# CLOAK BROWSER REACH IS AN OPTIONAL CAPABILITY (operator ruling 2026-09-04).
# It was MANDATORY here until that ruling: an absent manifest and a failed
# launch/render probe each called die, so a host that had not yet converged
# requirements-cloak.txt could deploy NOTHING -- the trade the ruling rejected.
# Both are now NAMED, NON-FATAL degradations that set CLOAK_STATE and let the
# deploy of everything else finish.
#
# CLOAK_STATE starts UNKNOWN because an UNMEASURED optional capability is not
# OK, and nothing below can reach OK by falling through: only a rendered marker
# assigns it. An absent manifest or an unresolved specifier is ABSENT; anything
# measured and failed stays UNKNOWN. Steps [12] and [13] and the deployed
# capability record all read this one variable, so a degradation here is never
# silent on the three durable surfaces a later operator reads.
CLOAK_STATE=UNKNOWN
CLOAK_READY=0
CAP_MISSING=""
CAP_RC=0
if cloak_manifest_present; then
  CAP_MISSING="$("$VENV_PY" tools/check_requirements.py "$CAP_FILE" 2>/dev/null)" || CAP_RC=$?
  if [ "$CAP_RC" -ne 0 ] && [ "$CAP_RC" -ne 2 ]; then
    note "capability ($CAP_FILE) does not resolve: $CAP_MISSING -- installing"
    if "$VENV_PY" -m pip install -q -r "$CAP_FILE"; then
      note "install ($CAP_FILE) OK"
    else
      note "install ($CAP_FILE) WARN: optional install failed; evaluating actual capability state"
    fi
    CAP_RC=0
    CAP_MISSING="$("$VENV_PY" tools/check_requirements.py "$CAP_FILE" 2>/dev/null)" || CAP_RC=$?
  fi
  if [ "$CAP_RC" -eq 0 ]; then
    note "capability ($CAP_FILE) OK: every entry resolves in the venv"
    CLOAK_READY=1
  elif [ "$CAP_RC" -eq 2 ]; then
    CLOAK_STATE=UNKNOWN
    note "capability ($CAP_FILE) WARN: could not evaluate -- capability state UNKNOWN, which is not the same as absent"
  else
    CLOAK_STATE=ABSENT
    note "capability ($CAP_FILE) WARN: ABSENT: $(echo "$CAP_MISSING" | cut -c1-70)"
  fi
else
  CLOAK_STATE=ABSENT
  note "capability ($CAP_FILE) WARN: ABSENT: $CAP_FILE is absent after the reset, so
  cloak browser reach was never measured on this host. Reach is an OPTIONAL
  capability, so this deploy continues and reports cloak=$CLOAK_STATE instead of
  stopping the delivery of everything else."
fi

if [ "$CLOAK_READY" -eq 1 ]; then
  if install_cloak_browsers; then
    note "cloak browser install OK"
  else
    note "cloak browser install WARN: installer failed; probing existing browser reach"
  fi
if "$VENV_PY" -c '
from pathlib import Path
import tempfile

from cloakbrowser import launch

def _mismatch(rendered, expected):
    return rendered != expected


def probe_cloak_browser_reach():
    expected = "bulk-downloader-browser-reach"
    browser = None
    context = None
    try:
        with tempfile.TemporaryDirectory(prefix="bd-browser-reach-") as directory:
            page_path = Path(directory) / "reach.html"
            page_path.write_text(
                "<!doctype html><div id=\"bd-browser-reach\">" + expected + "</div>",
                encoding="utf-8",
            )
            browser = launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto(page_path.as_uri(), wait_until="load")
            rendered = page.locator("#bd-browser-reach").text_content()
            if _mismatch(rendered, expected):
                raise RuntimeError(
                    "rendered marker mismatch: expected %r, got %r" % (expected, rendered)
                )
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()


probe_cloak_browser_reach()
'; then
    CLOAK_STATE=OK
    note "cloak browser convergence verified: requirements-cloak.txt resolves and local render is OK; cloak=$CLOAK_STATE"
  else
    CLOAK_STATE=UNKNOWN
    note "cloak browser WARN: CloakBrowser Playwright launch/render probe failed after
  browser install, so reach is unproven rather than absent. Reach is an OPTIONAL
  capability, so this deploy continues and reports cloak=$CLOAK_STATE instead of
  stopping the delivery of everything else."
  fi
fi

# ── [6] frontend bundle, keyed on CONTENT ───────────────────────────
# frontend/dist/ is gitignored with zero tracked files, so the deploy never
# delivers it and its staleness is invisible to git. The marker records WHICH
# COMMIT the bundle was built from, so an unchanged tree never rebuilds and
# never reports drift. Attesting over content rather than over a timestamp is
# the anti-identity rule: a pin that hashed a wall-clock field once made an
# unchanged tree "change" on every run, and two sessions tried to reconcile a
# diff that did not exist.
STEP=6
MARKER="frontend/dist/.bd-built-from"
DID_BUILD=0
NEED_BUILD=0
if [ ! -f "$MARKER" ]; then
  NEED_BUILD=1
  note "no bundle marker at $MARKER; the bundle's provenance is UNKNOWN, rebuilding"
else
  MARKED="$(tr -d ' \t\n\r' < "$MARKER")"
  if ! git rev-parse --verify --quiet "${MARKED}^{commit}" >/dev/null 2>&1; then
    # Unknown provenance fails TOWARD doing the work, never toward skipping it.
    NEED_BUILD=1
    note "bundle marker names an unresolvable commit ($MARKED); rebuilding"
  elif ! git diff --quiet "$MARKED" HEAD -- frontend/; then
    NEED_BUILD=1
    note "frontend/ changed between $MARKED and HEAD; rebuilding"
  else
    note "bundle was built from $MARKED and frontend/ is unchanged; build skipped"
  fi
fi
if [ "$NEED_BUILD" -eq 1 ]; then
  command -v npm >/dev/null 2>&1 || die "npm is not on PATH but the bundle needs rebuilding"
  ( cd frontend && npm ci --no-audit --no-fund ) >/dev/null || die "npm ci failed"
  ( cd frontend && npm run build ) >/dev/null || die "npm run build failed"
  DID_BUILD=1
fi
# Read the artifact back on BOTH paths -- built and skipped. `tsc -b && vite
# build` can exit 0 having written nothing the app can serve, and the thing
# every consumer needs is the entry point. This is the same discipline as the
# parity read-back below and the graph pin's --check-hash.
[ -f frontend/dist/index.html ] \
  || die "frontend/dist/index.html is absent. Exit 0 from the build is not the
  property anyone depends on: bulk_downloader/app.py cannot serve an absent
  bundle, so every asset route answers 503 and nothing else says why."
if [ "$DID_BUILD" -eq 1 ]; then
  printf '%s\n' "$NEW" > "$MARKER"
  note "bundle rebuilt from $NEW and marker updated"
else
  note "frontend/dist/index.html present"
fi

# ── [7] graph content pin ───────────────────────────────────────────
# The pin lives OUTSIDE the repo, so `git reset --hard` never delivers it and a
# source change leaves it describing the previous tree. capture.sh step [2b]
# then reports drift and capture_verdict.py turns that stage exit into a
# whole-capture FAIL. This is the step that keeps getting left off the
# post-deploy checklist, which is why it is in the script and on by default.
STEP=7
PIN="${BD_GRAPH_HASH_PIN:-/var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256}"
PIN_DEPLOY_RECORD="${PIN}.deploy-tree"
PIN_CLOAK_RECORD="${PIN}.deploy-capabilities"
if [ "$SKIP_GRAPH" -eq 1 ]; then
  note "WARNING: --skip-graph-pin given. A present pin still describes the
  PREVIOUS tree, so after this deploy is recorded the next ./capture.sh step [2b]
  will report graph drift and fail. An absent pin will report NOT-APPLICABLE.
  Re-pin by hand before running capture."
else
  # This default MUST stay byte-identical to capture.sh's and
  # provision_test_host.sh's. If they drift, this step arms a file the gate
  # never reads and BOTH report success -- two green tools and no gate.
  GTMP="$(mktemp -d "${TMPDIR:-/tmp}/bd_deploy_graph.XXXXXX")" \
    || die "mktemp failed; cannot build a throwaway graph to pin against"
  GDB="$GTMP/KNOWLEDGE_GRAPH.db"

  # UNELEVATED. Running l0_extract under sudo builds with HOME=/root -- the
  # CLAUDE.md section 5 footgun -- and can drop root-owned files in the tree.
  "$VENV_PY" tools/l0_extract.py --root "$DIR" --db "$GDB" >/dev/null \
    || die "graph extract failed; the content pin was NOT updated"

  # sudo wraps ONLY the pin write and its directory plumbing. --write-hash sets
  # projection_mode false and returns before emitting any projection, so
  # elevating just this call cannot write anything into the work tree.
  sudo mkdir -p "$(dirname "$PIN")" 2>/dev/null || true
  sudo "$VENV_PY" tools/graph_build.py --db "$GDB" --hash-pin "$PIN" --write-hash >/dev/null \
    || die "graph content pin write failed: $PIN"
  # Root writes with root's umask; at 077 the pin lands 0600 and the gate then
  # takes its `[ ! -r ]` branch, which is UNKNOWN and returns 0 -- the silent
  # skip this step exists to remove, now with a pin that exists.
  sudo chmod 0644 "$PIN" 2>/dev/null || true

  # Exit 0 from --write-hash proves a WRITE, not that capture.sh can READ and
  # MATCH the result. Re-run the gate's own check AS THE INVOKING USER -- never
  # under sudo -- because that is the condition capture.sh will actually evaluate.
  "$VENV_PY" tools/graph_build.py --db "$GDB" --hash-pin "$PIN" --check-hash >/dev/null \
    || die "graph content pin --check-hash did not verify as $(id -un): $PIN.
  The pin is unreadable or does not match, so capture.sh step [2b] will skip or
  fail the graph gate."
  rm -rf -- "$GTMP"; GTMP=""
  note "graph content pin written and verified readable+matching as $(id -un)"
fi

# ── [8] stop the service ────────────────────────────────────────────
# `systemctl stop` returning 0 is a request that was accepted, not a unit that
# is down. Steps 9 and 10 are unsafe against a live service, so the stop is
# CONFIRMED before either runs. The recovery debt is recorded BEFORE the request
# because systemctl can stop the unit and still return nonzero; every exit after
# this point therefore makes one best-effort start unless step 11 clears it.
STEP=8
SERVICE_STOPPED=1
stop_exit=0
sudo systemctl stop bulkdownloader || stop_exit=$?
state_exit=0
service_state="$(systemctl is-active bulkdownloader 2>&1)" || state_exit=$?
case "$service_state" in
  inactive|failed)
    [ "$stop_exit" -eq 0 ] || die "sudo systemctl stop bulkdownloader failed
  (stop exit=$stop_exit) after the unit became $service_state (is-active
  exit=$state_exit).
  The unsafe stopped window was not entered; emergency recovery is required."
    ;;
  active|activating|deactivating|reloading)
    die "bulkdownloader is still $service_state after the stop request
  (stop exit=$stop_exit, is-active exit=$state_exit). The bytecode sweep and
  parity regen that follow are unsafe unless the unit is confirmed inactive."
    ;;
  *)
    die "unit state is UNKNOWN after the bulkdownloader stop request
  (state=${service_state:-<no output>}, is-active exit=$state_exit, stop
  exit=$stop_exit). The unsafe stopped window was not entered; emergency
  recovery is required."
    ;;
esac
note "service stopped and confirmed inactive"

# ── [9] bytecode sweep, in the stopped window ───────────────────────
# `git reset --hard` does not clear __pycache__, and stale bytecode outliving
# the source it was compiled from is the v3.66.161 footgun. venv/ and
# node_modules/ are PRUNED: deleting the interpreter's own cached bytecode is
# gratuitous, slow, and buys nothing -- those trees are not what the deploy moved.
STEP=9
_sweep_candidates() {
  find "$DIR" \
    \( -path "$DIR/venv" -o -path "$DIR/frontend/node_modules" -o -path "$DIR/.git" \) -prune \
    -o \( -name '__pycache__' -type d -o -name '*.pyc' -type f \) -print0 2>/dev/null \
    || true
}
_sweep_candidates | xargs -0 -r rm -rf --
# The verifier shares its denominator with the sweep -- literally the same
# producer -- so it cannot certify a region the sweep never visited.
LEFT="$(_sweep_candidates | tr -dc '\0' | wc -c | tr -d ' ')"
[ "$LEFT" -eq 0 ] || die "bytecode sweep left $LEFT entries behind under $DIR"
note "bytecode caches cleared (venv/, frontend/node_modules/ and .git/ pruned)"

# ── [10] parity inventory regen, in the stopped window ──────────────
# reports/gui_parity_inventory.json is gitignored and build-time generated, so
# a stale copy survives every deploy with nothing able to evict it, and the
# reconcile gate then reads it as inventory drift and fails the ENTIRE suite
# (observed at v3.66.818 as one failure on an otherwise-green 13389-pass run).
#
# THE POSITION IS LOAD-BEARING, exactly as capture.sh step [2a] documents: the
# tool does `import bulk_downloader.app`, and that module's TOP LEVEL runs
# db_init(), db_integrity_check() and five scheduler/thread groups on import.
# Run against a live service that is a SECOND process doing a SQLite integrity
# check against the database the service is serving. It must run only in the
# confirmed-inactive window opened by step [8], and after the sweep in step [9]
# so it cannot read stale bytecode.
STEP=10
"$VENV_PY" tools/gui_parity_inventory.py >/dev/null \
  || die "gui parity inventory regen failed; the stale copy is still on disk and
  will read as inventory drift"
PARITY_JSON="reports/gui_parity_inventory.json"
[ -f "$PARITY_JSON" ] \
  || die "the inventory regen exited 0 but $DIR/$PARITY_JSON does not exist"

# Exit 0 is NOT sufficient evidence of a good regen: the generator falls back to
# ENDPOINT_CATALOG.md when the app import raises, writes a catalog-derived item
# set, and STILL RETURNS 0. So the written file is read back. Two independent
# questions, deliberately not merged:
#   (a) does it PARSE? A half-written or truncated file can still contain the
#       route_source line while json.load raises -- and json.load is exactly what
#       the reconcile gate will do to it.
#   (b) is it APP-DERIVED? One spelling of this predicate, whitespace-tolerant,
#       so a change to how the tool serialises (indent, key separator) cannot
#       turn a perfectly good inventory into a reported failure. capture.sh
#       warns about the strict-literal form for precisely that reason.
"$VENV_PY" -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' \
    "$PARITY_JSON" >/dev/null 2>&1 \
  || die "$PARITY_JSON will not parse as JSON; the reconcile gate does json.load
  on this file, so it would fail the suite"
ROUTE_SOURCE="$(grep -o '"route_source"[[:space:]]*:[[:space:]]*"[^"]*"' "$PARITY_JSON" \
    | head -1 \
    | sed 's/.*:[[:space:]]*"\([^"]*\)".*/\1/')" || ROUTE_SOURCE=""
if [ "$ROUTE_SOURCE" != "live url_map" ]; then
  die "the parity inventory was built from '${ROUTE_SOURCE:-<no route_source>}',
  not the live url_map: the app import degraded silently and the tool still
  exited 0, so the item set is catalog-derived and the reconcile gate will
  report inventory drift."
fi
note "parity inventory regenerated from the live url_map"

# capture.sh reads "\$BD_HOME/reports/gui_parity_inventory.json" after cd'ing to
# BD_HOME, i.e. it assumes BD_HOME IS the install dir. Where that does not hold,
# the copy refreshed above is not the copy the suite reads, and the v3.66.818
# staleness comes straight back. Say so rather than assume it away.
#
# The comparison is against capture.sh's EFFECTIVE BD_HOME, not against the
# environment. capture.sh:55 is `BD_HOME="${BD_HOME:-$HOME/BulkDownloader}"` --
# it DEFAULTS the variable, so the question "will the suite read the copy just
# refreshed here" has an answer whether or not BD_HOME is exported. Gating the
# warning on `[ -n "$BD_HOME" ]` asked a different question and reported clean
# in the default case, which is the common one: an operator exports BD_HOME by
# hand only when they already suspect a mismatch, i.e. exactly when the warning
# is least needed (CLAUDE.md section 0). The name is lowercase on purpose --
# a BD_-prefixed shell local enters tests/test_gui_parity.py's scan denominator
# and reads as promoted-but-unledgered (CLAUDE.md section 4).
capture_home="${BD_HOME:-$HOME/BulkDownloader}"
capture_home_real="$(cd "$capture_home" 2>/dev/null && pwd -P || true)"
if [ -z "$capture_home_real" ]; then
  note "WARNING: capture.sh will read the parity inventory from
  $capture_home/reports/ (\$BD_HOME, defaulting to \$HOME/BulkDownloader), and that
  directory does not exist -- so it is not this install dir $DIR, and the copy
  just refreshed here is not the one the suite will read."
elif [ "$capture_home_real" != "$DIR" ]; then
  note "WARNING: capture.sh will read the parity inventory from
  $capture_home/reports/ (\$BD_HOME, defaulting to \$HOME/BulkDownloader), which
  is not this install dir $DIR, so the copy just refreshed here is not the one
  the suite will read."
fi

# ── [10b] the download directories the config names ─────────────────
# A configured download_dir that does not exist fails the box selftest with a
# FileNotFoundError naming a path, and NOTHING created it -- not the
# provisioner, not install_linux.sh, not the app, which reads the key in four
# places and creates it in none. Measured 2026-08-14: copying one host's site
# config to four others took a capture round to 2/6 on exactly that, with unit
# 15942 pass / 0 fail and live 0 fail on every host.
#
# BEFORE the service starts, deliberately: a service that comes up unable to
# write its downloads is the state this exists to prevent. Never fatal -- see
# the fragment's header for why a deploy must not abort over a directory.
STEP=10
. "$(dirname "$0")/lib/download_dirs.sh"
bd_ensure_download_dirs "$VENV_PY" "$DIR/sites_config.json"

# ── [11] start the service ──────────────────────────────────────────
# No verification here on purpose: `systemctl start` exiting 0 says the unit was
# launched, not that the app is serving the tree we just deployed. Step [12] is
# this step's verification.
STEP=11
sudo systemctl start bulkdownloader || die "sudo systemctl start bulkdownloader failed"
# The stopped window is over. Cleared BEFORE the health gate deliberately: from
# here the unit is up, so a step-12 failure is a sick service, not a stopped
# one. Restarting it in the EXIT guard would tell the operator a comforting lie
# about a box that is actually unhealthy.
SERVICE_STOPPED=0
note "service start requested; verifying by health probe"

# ── [12] health gate ────────────────────────────────────────────────
# Three failures look identical from a bare "it didn't come up" and have three
# different remedies, so they are diagnosed separately and never conflated.
STEP=12
ROOT_URL="${HEALTH_URL%/api/health}/"
deadline=$(( $(date +%s) + TIMEOUT ))
got=""
code=""
health_serving_degraded=0

# A master-password vault deliberately locks on every process restart.  That
# makes /api/health answer 503 even though this exact service is listening and
# serving the SPA.  Recognise only the complete, nonempty structured condition:
# a bare 503, a sibling degradation, or a payload with no affected credentials
# remains a failure.  JSON is parsed rather than grepped because accepting a
# coincidental string from an error page would turn an unavailable measurement
# into deploy permission.
_locked_vault_version() {
  printf '%s' "$1" | "$VENV_PY" -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
if not isinstance(payload, dict):
    raise SystemExit(1)
credentials = payload.get("credentials")
download_hold = payload.get("download_hold")
exact_int = lambda value: type(value) is int
nonnegative = lambda value: exact_int(value) and value >= 0
positive = lambda value: exact_int(value) and value > 0
reference_count = credentials.get("reference_count") if isinstance(credentials, dict) else None
stored_count = credentials.get("stored_count") if isinstance(credentials, dict) else None
unavailable_count = credentials.get("unavailable_count") if isinstance(credentials, dict) else None
hold_state = download_hold.get("state") if isinstance(download_hold, dict) else None
observed = (
    payload.get("ok") is False
    and payload.get("degraded") == "credential_vault_locked"
    and payload.get("db_ok") is True
    and nonnegative(payload.get("queue_depth"))
    and nonnegative(payload.get("active_downloads"))
    and nonnegative(payload.get("sites_loaded"))
    and isinstance(download_hold, dict)
    and hold_state in {"clear", "held"}
    and download_hold.get("downloads_allowed") is (hold_state == "clear")
    and isinstance(credentials, dict)
    and credentials.get("backend") == "master_password"
    and credentials.get("ok") is False
    and credentials.get("state") == "locked"
    and credentials.get("is_initialized") is True
    and credentials.get("is_unlocked") is False
    and type(credentials.get("missing_count")) is int
    and credentials.get("missing_count") == 0
    and type(credentials.get("resolved_count")) is int
    and credentials.get("resolved_count") == 0
    and positive(reference_count)
    and positive(stored_count)
    and positive(unavailable_count)
    and unavailable_count == reference_count
)
version = payload.get("version")
if not observed or not isinstance(version, str) or not version:
    raise SystemExit(1)
sys.stdout.write(version)
'
}
while :; do
  bodyf="$(mktemp)"
  code="$(curl -s -o "$bodyf" -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null)" || true
  [ -n "$code" ] || code="000"
  body="$(cat "$bodyf")"
  rm -f "$bodyf"

  # A 503 is a DEFINITE answer, not a not-yet.  It is not, however, proof that
  # the SPA is absent: /api/health also uses 503 for structured degradation and
  # does not serve frontend/dist at all.  Only the exact restart-locked-vault
  # condition can proceed, and GET / still independently proves the SPA below.
  if [ "$code" = "503" ]; then
    locked_version=""
    if locked_version="$(_locked_vault_version "$body")"; then
      if [ "$locked_version" != "$TREE_VERSION" ]; then
        die "health gate: $HEALTH_URL reported the structured
  credential_vault_locked state from version $locked_version, expected
  $TREE_VERSION. A degraded sibling tree is still the wrong deployment."
      fi
      got="$locked_version"
      health_serving_degraded=1
      note "SERVING-DEGRADED: Credential vault is LOCKED after the service restart.
  The intended service is listening, but stored credentials are unavailable
  until a human opens Settings -> Secrets -> Unlock. This explicit unlock is
  required after every service restart. Verifying GET / separately."
      break
    fi
    die "health gate: GET $HEALTH_URL returned HTTP 503, and its response did
  not prove the exact structured credential_vault_locked serving-degraded
  condition. Expected HTTP 200 or that explicit restart state; not retried
  because 503 is a definite answer rather than a slow start."
  fi

  # A body is health evidence only when it came with HTTP 200. In particular,
  # framework error responses can carry the running version in their JSON;
  # accepting that version from a 500 turns an observed failure into a false
  # verified deploy. Other non-200 states may still be transient, so retain the
  # existing polling budget while refusing to parse their bodies as readiness.
  if [ "$code" != "200" ]; then
    got=""
    [ "$(date +%s)" -lt "$deadline" ] || break
    sleep "$INTERVAL"
    continue
  fi

  # The health body is read with a whitespace-tolerant match rather than a JSON
  # parser on purpose: this loop must keep working when the app is mid-restart
  # and answering with anything at all.
  got="$(printf '%s' "$body" \
      | grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' \
      | head -1 \
      | sed 's/.*:[[:space:]]*"\([^"]*\)".*/\1/')" || got=""

  [ "$got" != "$TREE_VERSION" ] || break
  [ "$(date +%s)" -lt "$deadline" ] || break
  sleep "$INTERVAL"
done

if [ "$got" != "$TREE_VERSION" ]; then
  if [ "$code" = "000" ]; then
    die "nothing was listening on that port: curl reported 000 for $HEALTH_URL
  throughout the ${TIMEOUT}s budget. Check the PORT before concluding the service
  is down -- the app binds 5555 (BD_PORT) and there is no /api/version route.
  This is UNKNOWN, not 'down', and unknown fails."
  fi
  if [ "$code" != "200" ]; then
    die "health gate: GET $HEALTH_URL returned HTTP $code, expected 200, after
  ${TIMEOUT}s. Any version string in a non-200 response body is error context,
  not readiness evidence, and was deliberately ignored."
  fi
  die "health gate: /api/health reported version '${got:-<none>}', expected
  $TREE_VERSION, after ${TIMEOUT}s (HTTP $code). The tree is at $TREE_VERSION, so
  the running process is not it: either stale bytecode survived, or the restart
  did not take."
fi

rcode="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$ROOT_URL" 2>/dev/null)" || true
[ -n "$rcode" ] || rcode="000"
if [ "$rcode" = "503" ]; then
  die "GET $ROOT_URL returned 503 -- /api/health is fine but the SPA bundle is not
  being served. frontend/dist did not deliver."
fi
[ "$rcode" = "200" ] || die "GET $ROOT_URL returned $rcode, expected 200"
# Echo the code that was RECEIVED, never the literal the check demands. A
# success note that restates its own constant cannot contradict a weakened
# check: it would print "GET / = 200" over a 404 and read as a clean pass.
if [ "$health_serving_degraded" -eq 1 ]; then
  note "serving-degraded verified: /api/health version $TREE_VERSION with
  credential_vault_locked, GET / = $rcode; Settings -> Secrets unlock required"
else
  note "health verified: /api/health version $TREE_VERSION, GET / = $rcode"
fi
# THE HEALTH STEP REPORTS CLOAK REACH IN EVERY STATE, OK INCLUDED. This is the
# surface an operator reads to learn what a deploy actually achieved, and an
# optional capability left out of it made a degraded reach byte-indistinguishable
# from a verified one. UNKNOWN and ABSENT are NAMED degradations here, not
# failures: they are reported and they do not change this step's verdict.
note "cloak browser capability (optional): cloak=$CLOAK_STATE"

# ── [13] summary ────────────────────────────────────────────────────
# An unchanged tree that verified clean says so and exits 0. It does NOT
# manufacture a change: the lxml case is exactly a box whose HEAD was already
# current and whose ENVIRONMENT had not converged, which is why "already
# current" is still a full verification and not an early return.
STEP=13
# THE VERDICT IS CHECKED AGAINST THE TREE THAT IS THERE NOW. Step [4] asserted
# HEAD immediately after its own reset, which says nothing about steps [5]-[12]
# -- pip, a bundle build, a bytecode sweep and a parity regen all run inside
# this work tree, and any of them (or an operator, or a second writer) can move
# it. The health gate cannot see that: two commits routinely carry the same
# __version__, so a tree moved to a sibling commit answers the version probe
# correctly and reports OK for a commit this deploy was told not to land.
FINAL_HEAD="$(git rev-parse HEAD 2>/dev/null || true)"
[ "$FINAL_HEAD" = "$NEW" ] \
  || die "DEPLOYED-TREE-IS-NOT-THE-INTENDED-COMMIT: this deploy set out to land
  $NEW ($INTENDED_SOURCE) and $DIR is at ${FINAL_HEAD:-<unresolved>} now. The
  tree moved after step [4]; every verification above described a tree that is
  no longer on disk, so none of them certifies this host."
note "deployed tree is still the intended commit $NEW"

# The record is written LAST, after both health and GET / verified. Writing it
# beside the pin at step 7 would let a later failed deploy claim this tree had
# deployed successfully. It is the exact Git TREE rather than a version string:
# tools/deployed_version.txt is rewritten on service start and cannot distinguish
# two commits (or trees) that carry the same release version.
GTMP="$(mktemp "${TMPDIR:-/tmp}/bd_deploy_tree.XXXXXX")" \
  || die "mktemp failed; cannot record deployed tree provenance"
printf '%s\n' "$DEPLOY_TREE" > "$GTMP" \
  || die "could not prepare deployed tree provenance"
sudo mkdir -p "$(dirname "$PIN_DEPLOY_RECORD")" \
  || die "could not create graph-pin provenance directory: $(dirname "$PIN_DEPLOY_RECORD")"
sudo install -m 0644 "$GTMP" "$PIN_DEPLOY_RECORD" \
  || die "could not record deployed tree provenance: $PIN_DEPLOY_RECORD"
rm -f -- "$GTMP"; GTMP=""
recorded_tree="$(tr -d '\r\n' < "$PIN_DEPLOY_RECORD" 2>/dev/null || true)"
[ "$recorded_tree" = "$DEPLOY_TREE" ] \
  || die "deployed tree record did not read back as $DEPLOY_TREE: $PIN_DEPLOY_RECORD"
note "deployed tree provenance recorded beside graph pin: $DEPLOY_TREE"

# THE CAPABILITY DISPOSITION IS ITS OWN RECORD, NOT A SECOND LINE IN .deploy-tree.
# capture.sh's run_graph_hash_gate reads that record with `tr -d '\r\n'` and
# compares the whole result to the current tree SHA, so an appended line would
# concatenate into "<tree>cloak=OK" and report EVERY host NOT-APPLICABLE. The
# disposition therefore lands in an adjacent file that no existing reader parses.
# It is written here, after health, for the same reason the tree record is: a
# record written at step 7 would let a later failed deploy claim this state.
# GTMP is reused deliberately -- it is the variable the EXIT cleanup() at the top
# of this script removes, so a die between mktemp and rm leaves no temp behind.
GTMP="$(mktemp "${TMPDIR:-/tmp}/bd_deploy_cap.XXXXXX")" \
  || die "mktemp failed; cannot record deployed capability provenance"
printf 'cloak=%s\n' "$CLOAK_STATE" > "$GTMP" \
  || die "could not prepare deployed capability provenance"
sudo install -m 0644 "$GTMP" "$PIN_CLOAK_RECORD" \
  || die "could not record deployed capability state: $PIN_CLOAK_RECORD"
rm -f -- "$GTMP"; GTMP=""
recorded_cloak="$(tr -d '\r\n' < "$PIN_CLOAK_RECORD" 2>/dev/null || true)"
[ "$recorded_cloak" = "cloak=$CLOAK_STATE" ] \
  || die "deployed capability record did not read back as cloak=$CLOAK_STATE: $PIN_CLOAK_RECORD"
note "deployed capability provenance recorded beside graph pin: cloak=$CLOAK_STATE"
# THE SUMMARY DISPOSITION IS DERIVED ONCE, not repeated in both branches. Two
# copies of the same field drift: a later edit to one summary line silently
# leaves the other reporting nothing, and only one of the two branches is
# exercised by any single deploy, so a test can be green on the branch that
# still carries it. One site, both branches.
SUMMARY_CLOAK=", cloak=$CLOAK_STATE"
if [ "$SAME" -eq 1 ] && [ -z "$handoff_expected" ] \
   && [ "$DID_PIP" -eq 0 ] && [ "$DID_BUILD" -eq 0 ]; then
  note "ALREADY CURRENT -- verified, $TREE_VERSION ($NEW), intended via $INTENDED_SOURCE$SUMMARY_CLOAK"
else
  note "DEPLOY OK -- $DIR now running $TREE_VERSION ($NEW), intended via $INTENDED_SOURCE$SUMMARY_CLOAK"
fi
DEPLOY_SUCCEEDED=1
exit 0
