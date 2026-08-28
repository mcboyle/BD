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
# There is no --expect flag: the expected version is DERIVED from the tree this
# script just reset to, so it cannot disagree with what was deployed.
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
# not. SERVICE_STOPPED is what makes the recovery conditional: a precondition
# refusal must never start a unit the operator had deliberately left down.
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

DIR=""
DISCARD=0
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
    printf 'deploy.sh: *** EXIT WITH SERVICE STOPPED [step %s, status %s] ***\n' \
      "$STEP" "$exit_status" >&2
    printf 'deploy.sh: attempting emergency start of bulkdownloader\n' >&2
    sudo systemctl start bulkdownloader >/dev/null 2>&1
    if systemctl is-active --quiet bulkdownloader; then
      printf 'deploy.sh: *** RESTARTED-PARTIAL-DEPLOY ***\n' >&2
      printf 'deploy.sh: the tree moved and later steps did not finish. Re-run me.\n' >&2
    else
      printf 'deploy.sh: *** SERVICE-IS-DOWN ***\n' >&2
      printf 'deploy.sh: emergency start failed; restore the unit before leaving.\n' >&2
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
NEW="$(git rev-parse origin/main 2>/dev/null || true)"
[ -n "$NEW" ] || die "origin/main does not resolve after a successful fetch"
note "fetched; origin/main = $NEW"

# ── [2] show what is about to land, BEFORE any mutation ─────────────
STEP=2
OLD="$(git rev-parse HEAD)"
if [ "$OLD" = "$NEW" ]; then
  SAME=1
  note "source already current at $OLD"
else
  SAME=0
  note "incoming commits (HEAD..origin/main):"
  git log --oneline HEAD..origin/main
  note "incoming diffstat:"
  git diff --stat HEAD origin/main
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
if [ "$SAME" -eq 0 ] && ! git merge-base --is-ancestor HEAD origin/main; then
  LOCAL_COMMITS="$(git log --oneline origin/main..HEAD)"
fi

if [ -n "$DIRTY" ] || [ -n "$LOCAL_COMMITS" ]; then
  if [ -n "$DIRTY" ]; then
    note "local modifications a reset would DESTROY:"
    printf '%s\n' "$DIRTY"
    git diff --stat || true
  fi
  if [ -n "$LOCAL_COMMITS" ]; then
    note "commits on HEAD that are NOT in origin/main and would be DESTROYED:"
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
git reset --hard origin/main >/dev/null || die "git reset --hard origin/main failed"
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
# CONFIRMED before either runs.
STEP=8
sudo systemctl stop bulkdownloader || die "sudo systemctl stop bulkdownloader failed"
SERVICE_STOPPED=1        # the EXIT guard owes a restart until step 11 clears it
if systemctl is-active bulkdownloader >/dev/null 2>&1; then
  die "systemctl stop bulkdownloader returned success but the unit is STILL
  ACTIVE. The bytecode sweep and the parity regen that follow are unsafe against
  a live service, so this deploy stops here rather than running them anyway."
fi
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
while :; do
  bodyf="$(mktemp)"
  code="$(curl -s -o "$bodyf" -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null)" || true
  [ -n "$code" ] || code="000"
  body="$(cat "$bodyf")"
  rm -f "$bodyf"

  # A 503 is a DEFINITE answer, not a not-yet: bulk_downloader/app.py serves it
  # when the SPA bundle is absent. Polling a definite answer for the full budget
  # only wastes the operator's time.
  if [ "$code" = "503" ]; then
    die "GET $HEALTH_URL returned 503 -- the SPA bundle was not found. frontend/dist
  did not deliver; rebuild it with (cd frontend && npm ci && npm run build).
  Not retried: 503 here is an answer, not a slow start."
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
note "health verified: /api/health version $TREE_VERSION, GET / = $rcode"

# ── [13] summary ────────────────────────────────────────────────────
# An unchanged tree that verified clean says so and exits 0. It does NOT
# manufacture a change: the lxml case is exactly a box whose HEAD was already
# current and whose ENVIRONMENT had not converged, which is why "already
# current" is still a full verification and not an early return.
STEP=13
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
if [ "$SAME" -eq 1 ] && [ -z "$handoff_expected" ] \
   && [ "$DID_PIP" -eq 0 ] && [ "$DID_BUILD" -eq 0 ]; then
  note "ALREADY CURRENT -- verified, $TREE_VERSION ($NEW)"
else
  note "DEPLOY OK -- $DIR now running $TREE_VERSION ($NEW)"
fi
DEPLOY_SUCCEEDED=1
exit 0
