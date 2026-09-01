#!/bin/bash
# Everything CI would tell me, told locally in ~2 minutes.
# Built 2026-08-24 after three CI round-trips on cut 1209 that were all locally
# detectable: LC_ALL not pinned in a new test's subprocess env (row 178 class),
# two new env vars unledgered in reports/config_gui_manifest.json, and a tracked
# mutant spec anchored on lines the cut replaced. Each cost ~5 minutes of CI plus
# a re-freeze; together they cost more than this script takes to run.
set -u
# 2026-08-25: took a WORK argument. The three tail cuts each live in their own
# worktree under /home/mboyle/bd-cuts, and a hardcoded cd silently ran every
# gate against main instead of the candidate -- a green answer about the wrong
# tree, which is exactly the stale-checkout failure A1 names.
WORK="${1:-/home/mboyle/BulkDownloader}"
cd "$WORK" || exit 1
echo "== bd-prepush :: $WORK =="
echo "   HEAD $(git rev-parse HEAD)  tree $(git rev-parse HEAD^{tree})"
FAIL=0
run() { printf '%-34s ' "$1"; shift
  if out=$("$@" 2>&1); then echo OK; else echo FAIL; FAIL=1
    echo "$out" | grep -E "^E |FAILED|AssertionError|error:" | head -3 | sed 's/^/    /'; fi; }

# Match CI's history scan rather than scanning only the final tree. The binary
# and version are pinned so an absent local tool or a PATH fallback cannot turn
# this release gate into a different scanner. Resolve immutable commits before
# invoking it; a missing remote-tracking ref or unrelated history has no safe
# PR range and therefore fails closed.
GITLEAKS=/home/mboyle/.cache/bd-tools/gitleaks/8.24.3/gitleaks
GITLEAKS_EXPECTED_VERSION=8.24.3
run_secret_scan() {
  printf '%-34s ' "secret scan"
  if [ ! -x "$GITLEAKS" ]; then
    echo FAIL
    echo "    error: pinned Gitleaks 8.24.3 is unavailable at $GITLEAKS"
    FAIL=1
    return
  fi
  if ! GITLEAKS_ACTUAL_VERSION=$("$GITLEAKS" version 2>/dev/null); then
    echo FAIL
    echo "    error: pinned Gitleaks version cannot be read from $GITLEAKS"
    FAIL=1
    return
  fi
  if [ "$GITLEAKS_ACTUAL_VERSION" != "$GITLEAKS_EXPECTED_VERSION" ]; then
    echo FAIL
    echo "    error: pinned Gitleaks version mismatch (expected $GITLEAKS_EXPECTED_VERSION, got $GITLEAKS_ACTUAL_VERSION)"
    FAIL=1
    return
  fi
  if [ ! -f .gitleaks.toml ]; then
    echo FAIL
    echo "    error: required Gitleaks config is unavailable: $PWD/.gitleaks.toml"
    FAIL=1
    return
  fi
  if [ ! -f .gitleaks-baseline.json ]; then
    echo FAIL
    echo "    error: required Gitleaks baseline is unavailable: $PWD/.gitleaks-baseline.json"
    FAIL=1
    return
  fi
  if ! GITLEAKS_HEAD=$(git rev-parse --verify 'HEAD^{commit}' 2>/dev/null); then
    echo FAIL
    echo "    error: cannot resolve HEAD for Gitleaks commit range"
    FAIL=1
    return
  fi
  if ! GITLEAKS_ORIGIN_MAIN=$(git rev-parse --verify 'refs/remotes/origin/main^{commit}' 2>/dev/null); then
    echo FAIL
    echo "    error: cannot resolve origin/main for Gitleaks commit range"
    FAIL=1
    return
  fi
  if ! GITLEAKS_BASE=$(git merge-base "$GITLEAKS_ORIGIN_MAIN" "$GITLEAKS_HEAD" 2>/dev/null) \
      || [ -z "$GITLEAKS_BASE" ]; then
    echo FAIL
    echo "    error: cannot resolve merge-base(origin/main,HEAD) for Gitleaks commit range"
    FAIL=1
    return
  fi
  if GITLEAKS_OUT=$("$GITLEAKS" detect \
      --redact \
      -v \
      --exit-code=2 \
      --config=.gitleaks.toml \
      --baseline-path=.gitleaks-baseline.json \
      "--log-opts=--no-merges --first-parent $GITLEAKS_BASE..$GITLEAKS_HEAD" \
      2>&1); then
    echo OK
  else
    GITLEAKS_RC=$?
    echo "FAIL (rc=$GITLEAKS_RC)"
    printf '%s\n' "$GITLEAKS_OUT" | head -12 | sed 's/^/    /'
    FAIL=1
  fi
}

run_secret_scan

run "regenerate deterministically" venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
run "repository freshness" venv/bin/python toolchain/bin/bd-freshcheck --repo-only
run "tree-wide + release gates" env -u BD_INSTALL_DIR bash -c 'BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest \
  tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py \
  tests/test_v3_66_1173_gate_scope_debt_is_paid.py \
  tests/test_v3_66_1164_one_task_authority.py \
  tests/test_v3_66_1052_the_backlog_is_machine_visible.py \
  tests/test_v3_66_1171_backlog_truth_is_current.py \
  tests/test_v3_66_1197_ambient_locale_into_subprocess.py \
  tests/test_v3_66_1184_mutation_specs_are_tracked.py \
  tests/test_gui_parity.py tests/test_pin_index_in_sync.py \
  tests/test_versync_gate.py tests/test_release_hygiene_gates.py \
  tests/test_settings_center_slice4.py tests/test_contracts.py \
  tests/test_all_sources_parse.py tests/test_source_windows_do_not_shift.py \
  tests/test_generated_artifacts_are_not_tracked.py \
  tests/test_import_graph_no_new_edges.py \
  -p no:randomly -q'
printf '%-34s ' "no UNSTAGED regen drift"
# Staged changes are the cut. What must be empty is UNSTAGED drift and
# untracked files -- regeneration writing something nobody staged. An earlier
# version counted staged files too and reported a clean, fully-staged cut as
# FAIL twice in a row.
UNSTAGED=$(git diff --name-only | wc -l)
# WORKTREE SCAFFOLDING IS NOT REGEN DRIFT. Each cut worktree gets `venv` and
# `frontend/node_modules` as SYMLINKS back to the main checkout. .gitignore
# carries `venv/` with a trailing slash, which matches a DIRECTORY only, so a
# symlink by that name is never ignored and git reports both as untracked. That
# turned a fully-verified 1241 into "PRE-PUSH FAILED -- do not push" while all
# three real gates read OK. They are excluded only when they are genuinely
# symlinks; a real directory of that name IS drift and still fails.
SCAFFOLD=""
# frontend/dist joined this list on 2026-08-26: bd-verify-cut scaffolds it so
# bands containing SPA tests do not 503 on an empty worktree. .gitignore has
# "dist/" with a trailing slash, which matches a DIRECTORY -- a symlink to one is
# not matched, so without this the scaffold reads as untracked drift.
for s in venv frontend/node_modules frontend/dist; do [ -L "$s" ] && SCAFFOLD="$SCAFFOLD $s"; done
UNTRACKED_LIST=$(git ls-files --others --exclude-standard | while read -r f; do
  case " $SCAFFOLD " in *" $f "*) ;; *) echo "$f";; esac; done)
UNTRACKED=$(printf '%s' "$UNTRACKED_LIST" | grep -c . )
[ -n "$SCAFFOLD" ] && echo "(scaffolding symlinks excluded:$SCAFFOLD)"
if [ "$UNSTAGED" -eq 0 ] && [ "$UNTRACKED" -eq 0 ]; then echo OK; else
  echo "FAIL (unstaged=$UNSTAGED untracked=$UNTRACKED)"
  git diff --name-only | head -4 | sed 's/^/    unstaged: /'
  printf '%s\n' "$UNTRACKED_LIST" | head -4 | sed 's/^/    untracked: /'
  FAIL=1
fi
echo
[ "$FAIL" -eq 0 ] && echo "PRE-PUSH OK -- CI should agree" || echo "PRE-PUSH FAILED -- do not push"
exit $FAIL
