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

# pip install first, then ASK whether the requirements resolve. `pip install -r`
# exiting 0 is not proof a requirement is present, and `pip check` cannot see one
# that was never installed -- its denominator is what IS installed. That is the
# whole reason tools/check_requirements.py exists (CLAUDE.md section 5).
"$PY" -m pip install -q -r requirements.txt -r requirements-test.txt >/dev/null 2>&1 || true

rc=0; missing="$("$PY" tools/check_requirements.py 2>/dev/null)" || rc=$?
if [ "$rc" -ne 0 ]; then
  echo "session-start: requirements.txt does not resolve: ${missing:-<unevaluable>}" >&2
fi
rc=0; missing="$("$PY" tools/check_requirements.py requirements-test.txt 2>/dev/null)" || rc=$?
if [ "$rc" -ne 0 ]; then
  echo "session-start: requirements-test.txt does not resolve: ${missing:-<unevaluable>}" >&2
fi
fi

# --- 3. checkout identity: REPORT, never repair ------------------------------
# Silence here is the signal. A divergence line means the tree may be a stale
# image and anything read out of it is suspect until re-synced BY HAND.
if git rev-parse --git-dir >/dev/null 2>&1; then
  git fetch -q origin main 2>/dev/null || true
  head_sha="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  main_sha="$(git rev-parse --short origin/main 2>/dev/null || echo unknown)"
  if [ "$main_sha" != "unknown" ] && ! git merge-base --is-ancestor origin/main HEAD 2>/dev/null; then
    behind="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
    echo "session-start: WARNING checkout $head_sha is $behind commit(s) behind origin/main ($main_sha)." >&2
    echo "session-start: verify before trusting any source read; re-sync with 'git fetch origin && git checkout -B main origin/main'." >&2
  fi
fi

exit 0
