#!/usr/bin/env bash
# Focused self-test for the pinned, fail-closed pre-push Gitleaks gate.
set -euo pipefail

canonical=/home/mboyle/bd-persist/harness/bd-prepush.sh
live=/home/mboyle/bd-prepush.sh
pinned=/home/mboyle/.cache/bd-tools/gitleaks/8.24.3/gitleaks

fail() {
  printf 'prepush Gitleaks self-test: FAIL: %s\n' "$*" >&2
  exit 1
}

cmp -s "$canonical" "$live" || fail "managed copies differ"
grep -Fqx "GITLEAKS=$pinned" "$canonical" \
  || fail "missing exact pinned scanner declaration"

root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT

cat > "$root/fake-gitleaks" <<'SH'
#!/bin/sh
{
  printf 'CALL\n'
  printf '%s\n' "$@"
} >> "${CAPTURE_FILE:?}"
if [ "${1:-}" = version ]; then
  printf '%s\n' "${FAKE_GITLEAKS_VERSION:-8.24.3}"
  exit "${FAKE_GITLEAKS_VERSION_RC:-0}"
fi
if [ "${1:-}" = detect ]; then
  exit "${FAKE_GITLEAKS_SCAN_RC:-0}"
fi
exit 97
SH
chmod 700 "$root/fake-gitleaks"

make_repo() {
  case_name=$1
  base_shape=$2
  CASE_REPO="$root/$case_name/repo"
  mkdir -p "$CASE_REPO"
  git -C "$CASE_REPO" init -q
  git -C "$CASE_REPO" config user.email prepush-test@example.invalid
  git -C "$CASE_REPO" config user.name prepush-test
  printf 'venv/\n' > "$CASE_REPO/.gitignore"
  printf '[extend]\nuseDefault = true\n' > "$CASE_REPO/.gitleaks.toml"
  printf '[]\n' > "$CASE_REPO/.gitleaks-baseline.json"
  printf 'base\n' > "$CASE_REPO/tracked.txt"
  git -C "$CASE_REPO" add .
  git -C "$CASE_REPO" commit -qm base
  CASE_BASE=$(git -C "$CASE_REPO" rev-parse HEAD)
  case "$base_shape" in
    known)
      git -C "$CASE_REPO" update-ref refs/remotes/origin/main "$CASE_BASE"
      ;;
    missing)
      ;;
    unrelated)
      unrelated=$(printf 'unrelated\n' \
        | git -C "$CASE_REPO" commit-tree "$(git -C "$CASE_REPO" rev-parse 'HEAD^{tree}')")
      git -C "$CASE_REPO" update-ref refs/remotes/origin/main "$unrelated"
      ;;
    *)
      fail "unknown base shape $base_shape"
      ;;
  esac
  printf 'candidate\n' >> "$CASE_REPO/tracked.txt"
  git -C "$CASE_REPO" add tracked.txt
  git -C "$CASE_REPO" commit -qm candidate
  CASE_HEAD=$(git -C "$CASE_REPO" rev-parse HEAD)
  mkdir -p "$CASE_REPO/venv/bin"
  cat > "$CASE_REPO/venv/bin/python" <<'SH'
#!/bin/sh
exit 0
SH
  chmod 700 "$CASE_REPO/venv/bin/python"
}

make_runner() {
  case_name=$1
  scanner=$2
  CASE_RUNNER="$root/$case_name/bd-prepush.sh"
  sed "s|^GITLEAKS=.*$|GITLEAKS=$scanner|" "$canonical" > "$CASE_RUNNER"
  grep -Fqx "GITLEAKS=$scanner" "$CASE_RUNNER" \
    || fail "could not install fake scanner into test subject"
  chmod 700 "$CASE_RUNNER"
}

run_case() {
  output_file=$1
  capture_file=$2
  shift 2
  if env CAPTURE_FILE="$capture_file" "$@" bash "$CASE_RUNNER" "$CASE_REPO" \
      > "$output_file" 2>&1; then
    CASE_RC=0
  else
    CASE_RC=$?
  fi
}

expect_failure() {
  [ "$CASE_RC" -ne 0 ] || fail "$1 unexpectedly passed"
  grep -Fq "$2" "$3" || fail "$1 lacked diagnostic: $2"
}

# Success captures the complete scanner contract and exact immutable range.
make_repo success known
make_runner success "$root/fake-gitleaks"
: > "$root/success.capture"
run_case "$root/success.out" "$root/success.capture"
[ "$CASE_RC" -eq 0 ] || fail "valid scanner/range failed"
{
  printf 'CALL\nversion\nCALL\ndetect\n'
  printf '%s\n' \
    '--redact' \
    '-v' \
    '--exit-code=2' \
    '--config=.gitleaks.toml' \
    '--baseline-path=.gitleaks-baseline.json' \
    "--log-opts=--no-merges --first-parent $CASE_BASE..$CASE_HEAD"
} > "$root/success.expected"
cmp -s "$root/success.expected" "$root/success.capture" \
  || { diff -u "$root/success.expected" "$root/success.capture" >&2 || true; fail "scanner arguments differ"; }

# The pinned path is mandatory; PATH fallback is forbidden.
make_repo missing-scanner known
make_runner missing-scanner "$root/absent-gitleaks"
: > "$root/missing-scanner.capture"
run_case "$root/missing-scanner.out" "$root/missing-scanner.capture"
expect_failure missing-scanner "pinned Gitleaks 8.24.3 is unavailable" "$root/missing-scanner.out"
[ ! -s "$root/missing-scanner.capture" ] || fail "missing scanner was invoked"

# A binary at the pinned path must identify as the exact pinned version.
make_repo wrong-version known
make_runner wrong-version "$root/fake-gitleaks"
: > "$root/wrong-version.capture"
run_case "$root/wrong-version.out" "$root/wrong-version.capture" \
  FAKE_GITLEAKS_VERSION=8.25.0
expect_failure wrong-version "pinned Gitleaks version mismatch" "$root/wrong-version.out"
[ "$(grep -c '^CALL$' "$root/wrong-version.capture")" -eq 1 ] \
  || fail "wrong-version scanner reached detect"

# A scanner whose version command fails is unknown even if it prints a version.
make_repo unreadable-version known
make_runner unreadable-version "$root/fake-gitleaks"
: > "$root/unreadable-version.capture"
run_case "$root/unreadable-version.out" "$root/unreadable-version.capture" \
  FAKE_GITLEAKS_VERSION_RC=19
expect_failure unreadable-version "pinned Gitleaks version cannot be read" "$root/unreadable-version.out"
[ "$(grep -c '^CALL$' "$root/unreadable-version.capture")" -eq 1 ] \
  || fail "unreadable-version scanner reached detect"

# An unborn/deleted branch ref cannot supply the immutable range endpoint.
make_repo missing-head known
head_ref=$(git -C "$CASE_REPO" symbolic-ref HEAD)
git -C "$CASE_REPO" update-ref -d "$head_ref"
make_runner missing-head "$root/fake-gitleaks"
: > "$root/missing-head.capture"
run_case "$root/missing-head.out" "$root/missing-head.capture"
expect_failure missing-head "cannot resolve HEAD" "$root/missing-head.out"
[ "$(grep -c '^CALL$' "$root/missing-head.capture")" -eq 1 ] \
  || fail "missing-head case reached detect"

# A missing remote-tracking base must not degrade to a tree-only scan.
make_repo missing-base missing
make_runner missing-base "$root/fake-gitleaks"
: > "$root/missing-base.capture"
run_case "$root/missing-base.out" "$root/missing-base.capture"
expect_failure missing-base "cannot resolve origin/main" "$root/missing-base.out"
[ "$(grep -c '^CALL$' "$root/missing-base.capture")" -eq 1 ] \
  || fail "missing-base case reached detect"

# Existing but unrelated histories have no trustworthy PR range.
make_repo unrelated-base unrelated
make_runner unrelated-base "$root/fake-gitleaks"
: > "$root/unrelated-base.capture"
run_case "$root/unrelated-base.out" "$root/unrelated-base.capture"
expect_failure unrelated-base "cannot resolve merge-base(origin/main,HEAD)" "$root/unrelated-base.out"
[ "$(grep -c '^CALL$' "$root/unrelated-base.capture")" -eq 1 ] \
  || fail "unrelated-base case reached detect"

# Config and baseline are required inputs, not optional defaults.
make_repo missing-config known
rm -- "$CASE_REPO/.gitleaks.toml"
make_runner missing-config "$root/fake-gitleaks"
: > "$root/missing-config.capture"
run_case "$root/missing-config.out" "$root/missing-config.capture"
expect_failure missing-config "required Gitleaks config is unavailable" "$root/missing-config.out"

make_repo missing-baseline known
rm -- "$CASE_REPO/.gitleaks-baseline.json"
make_runner missing-baseline "$root/fake-gitleaks"
: > "$root/missing-baseline.capture"
run_case "$root/missing-baseline.out" "$root/missing-baseline.capture"
expect_failure missing-baseline "required Gitleaks baseline is unavailable" "$root/missing-baseline.out"

# Both an ordinary scanner error and the configured leak exit code fail closed.
for scan_rc in 1 2; do
  make_repo "scan-rc-$scan_rc" known
  make_runner "scan-rc-$scan_rc" "$root/fake-gitleaks"
  : > "$root/scan-rc-$scan_rc.capture"
  run_case "$root/scan-rc-$scan_rc.out" "$root/scan-rc-$scan_rc.capture" \
    FAKE_GITLEAKS_SCAN_RC="$scan_rc"
  expect_failure "scan-rc-$scan_rc" "secret scan" "$root/scan-rc-$scan_rc.out"
done

printf 'prepush Gitleaks self-test: PASS\n'
