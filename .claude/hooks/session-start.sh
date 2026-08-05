#!/bin/bash
#
# BD SessionStart hook -- restore the two invariants a container restart breaks.
#
# WHY THIS EXISTS. Measured 2026-08-04: this session's container rebooted
# mid-session (uptime 2h29m against a session hours older) and came back on a
# base image dated 2026-07-28. Two things reverted with it:
#
#   * the venv lost every package declared after the image was built -- lxml
#     and cssselect were declared at v3.66.843 and simply vanished, and
#     tools/check_requirements.py went from exit 0 to naming them missing;
#   * the checkout reappeared at an old commit, three separate times. Once, a
#     source read against that stale tree produced a confidently WRONG
#     conclusion about a fix that was in fact present on main.
#
# Neither symptom announces itself: a reverted container reports a perfectly
# healthy-looking tree. That is CLAUDE.md section 0 wearing a hypervisor -- the
# thing that would tell you is the thing that got reset.
#
# WHAT THIS DOES AND DELIBERATELY DOES NOT DO. It repairs DEPENDENCIES, which is
# always safe and idempotent. It only REPORTS git divergence -- it never resets
# the work tree. The hook also fires on `resume` and `compact`, where the
# checkout is legitimately ahead of origin and mid-cut work is uncommitted; a
# reset there would destroy exactly the work the session is doing.
set -euo pipefail

# Remote only. A laptop checkout provisions itself and has no image to revert to.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

# @866: the hook fires on startup|resume|clear|compact and the right repair
# differs. A full provision is 33 steps -- apt, npm build, playwright, nuclei,
# GTK -- so running it on every compact would make the session unusable.
# stdin is JSON; jq is NOT assumed (a base image that reverted may not have it),
# and malformed or empty stdin must yield "" rather than crash the hook.
HOOK_SOURCE="$(python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("source",""))
except Exception: print("")' 2>/dev/null <&0 || echo "")"

REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$REPO" || exit 0

# --- 1. session environment -------------------------------------------------
# These five were hand-entered in the web panel's env box -- the one part of the
# setup that lived nowhere in the repo and that a fresh session could not
# re-derive (CLAUDE.md section 5 had to write them down in prose). Sourcing them
# here makes the panel box redundant and keeps them under version control.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export BD_REPO=$REPO"
    echo "export BD_HOME=/tmp/bd_home"
    echo "export BD_SKIP_ARCHB=1"
    echo "export BD_SKIP_BROWSERS=1"
    echo "export BD_DISABLE_KEEPALIVE=1"
  } >> "$CLAUDE_ENV_FILE"
fi
mkdir -p /tmp/bd_home

# --- 2. dependency floor ----------------------------------------------------
# NOT `exit 0` when the venv is absent. An early return here would skip the
# checkout-identity report below, and the divergence warning is the more
# important of the two -- a stale tree produces confidently wrong answers, a
# missing venv produces loud import errors. Caught by testing the hook against
# a deliberately stale worktree, where `venv/` does not exist because it is
# gitignored (CLAUDE.md section 2b).
PY="$REPO/venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "session-start: no venv at $PY -- run scripts/cloud-setup.sh for a full provision" >&2
else

# ASK, do not assume. `pip install -r` exiting 0 is not proof a requirement is
# present, and `pip check` cannot see one that was never installed -- its
# denominator is what IS installed. That is why tools/check_requirements.py
# exists (CLAUDE.md section 5), and it is the cheap probe that decides whether
# the expensive repair is needed at all.
broken=""
for manifest in requirements.txt requirements-test.txt; do
  [ -f "$manifest" ] || continue
  rc=0; missing="$("$PY" tools/check_requirements.py "$manifest" 2>/dev/null)" || rc=$?
  if [ "$rc" -ne 0 ]; then
    broken="$broken $manifest(${missing:-unevaluable})"
  fi
done

if [ -n "$broken" ]; then
  echo "session-start: requirements do not resolve:$broken" >&2
  # @866: DELEGATE the repair. The previous draft ran its own
  # `pip install -r requirements.txt -r requirements-test.txt`, which is a
  # second home for what scripts/cloud-setup.sh already does -- and a second
  # home for one answer is this repo's signature failure (three TEXT_EXT sets,
  # three copies of the test-dep list). cloud-setup.sh is the provisioner; it
  # is idempotent, it honours the BD_SKIP_* flags exported above, and it
  # verifies each step rather than trusting an exit code.
  case "$HOOK_SOURCE" in
    startup|resume|"")
      if [ -x scripts/cloud-setup.sh ]; then
        echo "session-start: running scripts/cloud-setup.sh to repair (source=${HOOK_SOURCE:-unknown})" >&2
        BD_SKIP_ARCHB=1 BD_SKIP_BROWSERS=1 BD_HOME=/tmp/bd_home BD_REPO="$REPO" \
          bash scripts/cloud-setup.sh >&2 || \
          echo "session-start: cloud-setup.sh exited non-zero -- provision by hand" >&2
      else
        echo "session-start: scripts/cloud-setup.sh absent; cannot repair" >&2
      fi
      ;;
    *)
      # compact/clear happen MID-SESSION. A 33-step provision here would stall
      # a running session for minutes, so say what is wrong and let the
      # operator choose. Reporting a real problem is not the same as fixing it,
      # and silently doing neither is what this hook exists to prevent.
      echo "session-start: source=$HOOK_SOURCE -- NOT auto-provisioning mid-session." >&2
      echo "session-start: run 'bash scripts/cloud-setup.sh' when convenient." >&2
      ;;
  esac
fi
fi

# --- 3. checkout identity: repair when PROVABLY lossless, else report ---------
# @873. This section used to only ever REPORT, and the reason given was sound as
# far as it went: resume/compact fire mid-session, where the checkout is
# legitimately ahead of origin and carries uncommitted work, and a reset there
# would destroy exactly what the session is doing.
#
# But that conflated two different situations under one refusal. "The tree has
# work I would lose" and "the tree is a reverted image with nothing of its own"
# are distinguishable, and the second is the one that keeps happening -- four
# times now, and once it produced a confidently wrong conclusion about a fix
# that was present on main all along.
#
# So the predicate is provable losslessness, not the hook's trigger source:
#
#   * no MODIFIED TRACKED files  -- `git reset --hard` only touches tracked
#     paths, so untracked scratch files are irrelevant to the question and are
#     deliberately not counted (--untracked-files=no). Counting them would
#     refuse to repair the common case for no safety gain.
#   * zero commits ahead of origin/main -- every commit reachable from HEAD is
#     already on the remote.
#
# Both true means every byte the reset would discard is reachable from
# origin/main by construction, so there is nothing to lose. Either false and it
# refuses and SAYS WHICH, because a repair that could eat a cut is worse than
# the rollback it fixes.
if git rev-parse --git-dir >/dev/null 2>&1; then
  git fetch -q origin main 2>/dev/null || true
  head_sha="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  main_sha="$(git rev-parse --short origin/main 2>/dev/null || echo unknown)"
  if [ "$main_sha" != "unknown" ] && ! git merge-base --is-ancestor origin/main HEAD 2>/dev/null; then
    behind="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
    dirty="$(git status --porcelain --untracked-files=no 2>/dev/null || echo UNKNOWN)"
    ahead="$(git rev-list --count origin/main..HEAD 2>/dev/null || echo '?')"
    if [ -z "$dirty" ] && [ "$ahead" = "0" ]; then
      echo "session-start: checkout $head_sha is $behind commit(s) behind origin/main ($main_sha)" >&2
      echo "session-start: no modified tracked files and 0 commits ahead -- this is the reverted-image signature, and a fast-forward is lossless. Repairing." >&2
      if git reset --hard -q origin/main 2>/dev/null; then
        echo "session-start: REPAIRED -- checkout now at $(git rev-parse --short HEAD)" >&2
      else
        echo "session-start: reset FAILED -- re-sync by hand before trusting any source read" >&2
      fi
    else
      # Refusing is the right answer here, and it has to say why: an operator
      # who sees only "behind" will re-run the same command by hand and lose
      # the very work this branch protected.
      echo "session-start: WARNING checkout $head_sha is $behind commit(s) behind origin/main ($main_sha)." >&2
      echo "session-start: NOT repairing -- ${dirty:+modified tracked files present; }${ahead:+$ahead commit(s) ahead of origin/main; }a reset would discard work." >&2
      echo "session-start: verify before trusting any source read; re-sync deliberately once the local work is safe." >&2
    fi
  fi
fi

exit 0
