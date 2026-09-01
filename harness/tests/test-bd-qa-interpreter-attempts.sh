#!/usr/bin/env bash
# Row 403 behavioral regression: QA distinguishes runnable pass/failure from a
# worktree that cannot start its interpreter.
set -euo pipefail

canonical=${BD_QA_UNDER_TEST:-/home/mboyle/bd-persist/harness/bd-qa-row.sh}
night=${BD_NIGHT_UNDER_TEST:-/home/mboyle/bd-persist/harness/bd-night.sh}
root=$(mktemp -d /tmp/bd-qa-interpreter.XXXXXX)
trap 'rm -rf -- "$root"' EXIT
fakebin=$root/bin
mkdir -p "$fakebin"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'while [ "${1:-}" = "-C" ]; do shift 2; done' \
  'case "${1:-}" in' \
  '  status) echo " M tests/test_case.py" ;;' \
  '  add) : ;;' \
  '  *) exit 97 ;;' \
  'esac' > "$fakebin/git"
chmod 700 "$fakebin/git"

run_case() {
  local name=$1 mode=$2 row=403 base work artifacts qa rc
  base=$root/$name/home/mboyle
  work=$base/bd-codex-wt/row$row
  artifacts=$base/fleet-run-artifacts/2026-08-25/codex-cuts
  qa=$artifacts/row$row.qa.log
  mkdir -p "$work/tests" "$artifacts"
  : > "$work/tests/test_case.py"
  sed "s|/home/mboyle|$base|g" "$canonical" > "$base/bd-qa-row.sh"
  chmod 700 "$base/bd-qa-row.sh"

  if [ "$mode" != missing ]; then
    mkdir -p "$work/venv/bin"
    if [ "$mode" = pass ]; then
      printf '%s\n' '#!/usr/bin/env bash' 'echo "1 passed"' 'exit 0' > "$work/venv/bin/python"
    else
      printf '%s\n' '#!/usr/bin/env bash' 'echo "1 failed"' 'exit 1' > "$work/venv/bin/python"
    fi
    chmod 700 "$work/venv/bin/python"
  fi

  set +e
  PATH="$fakebin:$PATH" /bin/bash "$base/bd-qa-row.sh" "$row" >/dev/null 2>&1
  rc=$?
  set -e
  [ "$rc" -eq 0 ] || { printf 'FAIL %s: wrapper rc=%s\n' "$name" "$rc" >&2; return 1; }
  case "$mode" in
    pass)
      grep -qx 'QA_STATE=PASS' "$qa"
      grep -qx 'QA_RC=0' "$qa"
      ;;
    fail)
      grep -qx 'QA_STATE=TEST_FAILURE' "$qa"
      grep -qx 'QA_RC=1' "$qa"
      ;;
    missing)
      grep -qx 'QA_STATE=NOT_RUNNABLE' "$qa"
      grep -Fqx "QA_REASON=missing worktree interpreter $work/venv/bin/python" "$qa"
      grep -qx 'QA_RC=NOT_RUNNABLE' "$qa"
      ;;
  esac
  printf 'ok - %s\n' "$name"
}

run_case missing-interpreter missing
run_case runnable-pass pass
run_case genuine-test-failure fail

# The drain's classifier is the separate boundary that decides whether the
# finite row attempt budget moves.  Source only its helper, never its live loop.
BD_NIGHT_LIBRARY=1 source "$night"
for state in PASS NOT_RUNNABLE RUNNER_ERROR PYTEST_ERROR; do
  printf 'QA_STATE=%s\nQA_RC=127\n' "$state" > "$root/$state.log"
  if qa_failure_consumes_attempt "$root/$state.log"; then
    printf 'FAIL %s consumed an attempt\n' "$state" >&2
    exit 1
  fi
done
printf 'QA_STATE=TEST_FAILURE\nQA_RC=1\n' > "$root/test-failure.log"
qa_failure_consumes_attempt "$root/test-failure.log"
printf 'QA_RC=1\n' > "$root/legacy-failure.log"
qa_failure_consumes_attempt "$root/legacy-failure.log"
printf 'night attempt classifier: PASS\n'
printf 'qa interpreter state regression: PASS\n'
