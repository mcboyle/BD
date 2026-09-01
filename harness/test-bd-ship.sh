#!/usr/bin/env bash
# Fault-injection test for bd-ship's pre-merge CI/base gate.
# It sources the real production functions but never invokes bd-ship's push or
# merge path, and every git remote/gh response belongs to this temporary tree.
set -euo pipefail

SHIP=${BD_SHIP_UNDER_TEST:-/home/mboyle/bd-persist/harness/bd-ship.sh}
root=$(mktemp -d /tmp/bd-ship-selftest.XXXXXX)
trap 'rm -rf -- "$root"' EXIT
repo=$root/repo
remote=$root/origin.git
fakebin=$root/bin
trusted_toolchain=$root/trusted-toolchain
checks=$root/checks.tsv
marker=$root/checks-seen
merge_log=$root/merge.argv
mkdir -p "$repo/toolchain/bin" "$repo/.github/workflows" "$fakebin" \
  "$trusted_toolchain"

git -C "$repo" init -q
git -C "$repo" config user.email ship-test@example.invalid
git -C "$repo" config user.name ship-test
install -m 755 /home/mboyle/BulkDownloader/toolchain/bin/bd-ci-verdict \
  "$repo/toolchain/bin/bd-ci-verdict"
install -m 755 /home/mboyle/BulkDownloader/toolchain/bin/bd-ci-wait \
  "$repo/toolchain/bin/bd-ci-wait"
install -m 755 /home/mboyle/BulkDownloader/toolchain/bin/bd-ci-verdict \
  "$trusted_toolchain/bd-ci-verdict"
install -m 755 /home/mboyle/BulkDownloader/toolchain/bin/bd-ci-wait \
  "$trusted_toolchain/bd-ci-wait"
# The fixture's origin/main deliberately carries the pre-bootstrap verifier:
# it accepts complete pass rows even when gh exits nonzero. The ship wrapper,
# not this fixture, must prevent that stale trusted implementation from merging.
python3 - "$repo/toolchain/bin/bd-ci-verdict" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()
start = text.index('    # `gh pr checks` normally exits nonzero for pending or failed rows;')
end = text.index('    counts = ', start)
p.write_text(text[:start] + text[end:])
PY

cat > "$repo/.github/workflows/ci.yml" <<'YAML'
name: CI
on:
  pull_request:
jobs:
  gates:
    runs-on: ubuntu-latest
    steps:
      - run: true
  gate-suites:
    name: gate-suites (${{ matrix.name }})
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - name: one
    steps:
      - run: true
YAML
printf 'base\n' > "$repo/subject.txt"
git -C "$repo" add .
git -C "$repo" commit -qm base
git -C "$repo" branch -M main
git init -q --bare "$remote"
git -C "$repo" remote add origin "$remote"
git -C "$repo" push -q -u origin main
git -C "$remote" symbolic-ref HEAD refs/heads/main
git -C "$repo" checkout -qb topic
printf 'candidate\n' >> "$repo/subject.txt"
git -C "$repo" add subject.txt
git -C "$repo" commit -qm candidate
git -C "$repo" push -q -u origin topic
candidate=$(git -C "$repo" rev-parse HEAD)

cat > "$fakebin/gh" <<'PY'
#!/usr/bin/env python3
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args[:2] == ["pr", "checks"]:
    sys.stdout.write(Path(os.environ["FAKE_CHECKS"]).read_text())
    Path(os.environ["FAKE_CHECKS_SEEN"]).touch()
    raise SystemExit(int(os.environ.get("FAKE_CHECKS_RC", "0")))
if args[:2] == ["run", "list"]:
    print("991 completed success")
    raise SystemExit(0)
if args[:2] == ["pr", "merge"]:
    Path(os.environ["FAKE_MERGE_LOG"]).write_text(" ".join(args) + "\n")
    raise SystemExit(0)
if args[:2] == ["pr", "view"] and "headRefOid" in " ".join(args):
    if (os.environ.get("FAKE_MOVE_AFTER_CHECKS") == "1"
            and Path(os.environ["FAKE_CHECKS_SEEN"]).exists()):
        print("f" * 40)
    else:
        print(os.environ["FAKE_PR_HEAD"])
    raise SystemExit(0)
if args[:2] == ["pr", "view"] and "mergeStateStatus" in " ".join(args):
    print(os.environ.get("FAKE_MERGE_STATE", "CLEAN"))
    raise SystemExit(0)
sys.stderr.write("unexpected fake gh invocation: " + repr(args) + "\n")
raise SystemExit(97)
PY
chmod 755 "$fakebin/gh"

passed=0
failed=0

run_gate() {
  PATH="$fakebin:$PATH" \
  BD_SHIP_SOURCE_ONLY=1 \
  BD_SHIP_PYTHON="$(command -v python3)" \
  BD_SHIP_WAIT_MAX=1 \
  FAKE_CHECKS="$checks" \
  FAKE_CHECKS_SEEN="$marker" \
  FAKE_PR_HEAD="$candidate" \
  FAKE_MOVE_AFTER_CHECKS="${1:-0}" \
  FAKE_MERGE_STATE="${2:-CLEAN}" \
  FAKE_CHECKS_RC="${3:-0}" \
  SHIP="$SHIP" REPO="$repo" CANDIDATE="$candidate" \
  bash -c '
    source "$SHIP"
    L=/dev/null
    bd_ship_premerge_gate "$REPO" "$CANDIDATE" 17
  '
}

run_malicious_candidate_gate() {
  PATH="$fakebin:$PATH" \
  BD_SHIP_SOURCE_ONLY=1 \
  BD_SHIP_PYTHON="$(command -v python3)" \
  BD_SHIP_WAIT_MAX=1 \
  FAKE_CHECKS="$checks" \
  FAKE_CHECKS_SEEN="$marker" \
  FAKE_PR_HEAD="$candidate" \
  FAKE_MOVE_AFTER_CHECKS=0 \
  SHIP="$SHIP" REPO="$repo" CANDIDATE="$candidate" \
  bash -c '
    printf "%s\\n" "#!/usr/bin/env python3" \
      "print('\''VERDICT: MERGE-SAFE forged'\'')" \
      > "$REPO/toolchain/bin/bd-ci-verdict"
    chmod 755 "$REPO/toolchain/bin/bd-ci-verdict"
    [ "$("$REPO/toolchain/bin/bd-ci-verdict")" = "VERDICT: MERGE-SAFE forged" ] \
      || exit 98
    git -C "$REPO" add toolchain/bin/bd-ci-verdict
    git -C "$REPO" commit -qm forged-verdict
    git -C "$REPO" push -q origin topic
    forged=$(git -C "$REPO" rev-parse HEAD)
    export FAKE_PR_HEAD="$forged"
    source "$SHIP"
    L=/dev/null
    bd_ship_premerge_gate "$REPO" "$forged" 17
  '
}

run_base_assertion() {
  BD_SHIP_SOURCE_ONLY=1 \
  SHIP="$SHIP" REPO="$repo" CANDIDATE="$candidate" \
  bash -c '
    source "$SHIP"
    L=/dev/null
    _bd_ship_assert_fresh_base "$REPO" "$CANDIDATE"
  '
}

run_merge() {
  PATH="$fakebin:$PATH" \
  BD_SHIP_SOURCE_ONLY=1 \
  FAKE_MERGE_LOG="$merge_log" \
  SHIP="$SHIP" CANDIDATE="$candidate" \
  bash -c '
    source "$SHIP"
    _bd_ship_merge 17 "$CANDIDATE"
  '
}

expect_pass() {
  local name=$1 token=$2 payload=$3 output rc
  rm -f -- "$marker"
  printf '%s' "$payload" > "$checks"
  set +e
  output=$(run_gate 0 2>&1)
  rc=$?
  set -e
  if [ "$rc" -eq 0 ] && grep -Fq "$token" <<<"$output"; then
    printf 'ok - %s\n' "$name"
    passed=$((passed + 1))
  else
    printf 'not ok - %s (rc=%s, missing %q)\n%s\n' "$name" "$rc" "$token" "$output"
    failed=$((failed + 1))
  fi
}

expect_refuse() {
  local name=$1 token=$2 payload=$3 move=${4:-0} state=${5:-CLEAN} check_rc=${6:-0} output rc
  rm -f -- "$marker"
  printf '%s' "$payload" > "$checks"
  set +e
  output=$(run_gate "$move" "$state" "$check_rc" 2>&1)
  rc=$?
  set -e
  if [ "$rc" -ne 0 ] && grep -Fq "$token" <<<"$output"; then
    printf 'ok - %s\n' "$name"
    passed=$((passed + 1))
  else
    printf 'not ok - %s (rc=%s, missing %q)\n%s\n' "$name" "$rc" "$token" "$output"
    failed=$((failed + 1))
  fi
}

expect_malicious_candidate_refuse() {
  local output rc
  rm -f -- "$marker"
  printf '%s' "$missing" > "$checks"
  set +e
  output=$(run_malicious_candidate_gate 2>&1)
  rc=$?
  set -e
  if [ "$rc" -ne 0 ] && grep -Fq 'REFUSE-MISSING-CHECK' <<<"$output"; then
    printf 'ok - candidate verifier cannot forge the exact-name verdict\n'
    passed=$((passed + 1))
  else
    printf 'not ok - candidate verifier cannot forge the exact-name verdict (rc=%s)\n%s\n' "$rc" "$output"
    failed=$((failed + 1))
  fi
}

expect_merge_matches_head() {
  local output rc expected actual
  rm -f -- "$merge_log"
  set +e
  output=$(run_merge 2>&1)
  rc=$?
  set -e
  expected="pr merge 17 --merge --delete-branch --match-head-commit $candidate"
  actual=$(cat "$merge_log" 2>/dev/null)
  if [ "$rc" -eq 0 ] && [ "$actual" = "$expected" ]; then
    printf 'ok - merge is bound to the reviewed head SHA\n'
    passed=$((passed + 1))
  else
    printf 'not ok - merge is bound to the reviewed head SHA (rc=%s)\n%s\n' "$rc" "$output"
    failed=$((failed + 1))
  fi
}

expect_base_refuse() {
  local output rc
  set +e
  output=$(run_base_assertion 2>&1)
  rc=$?
  set -e
  if [ "$rc" -ne 0 ] && grep -Fq 'REBASE_REQUIRED' <<<"$output"; then
    printf 'ok - immediate pre-merge base recheck refuses stale main\n'
    passed=$((passed + 1))
  else
    printf 'not ok - immediate pre-merge base recheck refuses stale main (rc=%s)\n%s\n' "$rc" "$output"
    failed=$((failed + 1))
  fi
}

green=$'gates\tpass\t1s\thttps://example.invalid/gates\t\n'
green+=$'gate-suites (one)\tpass\t1s\thttps://example.invalid/one\t\n'
# The explicitly advisory context must not enter the required-check verdict.
green+=$'CodeRabbit\tpending\t0s\thttps://example.invalid/advisory\t\n'

missing=$'gates\tpass\t1s\thttps://example.invalid/gates\t\n'
missing+=$'foreign-check\tpass\t1s\thttps://example.invalid/foreign\t\n'
missing+=$'CodeRabbit\tpass\t1s\thttps://example.invalid/advisory\t\n'

skipping=$'gates\tpass\t1s\thttps://example.invalid/gates\t\n'
skipping+=$'gate-suites (one)\tskipping\t1s\thttps://example.invalid/one\t\n'

neutral=$'gates\tpass\t1s\thttps://example.invalid/gates\t\n'
neutral+=$'gate-suites (one)\tneutral\t1s\thttps://example.invalid/one\t\n'

near_coderabbit=$'gates\tpass\t1s\thttps://example.invalid/gates\t\n'
near_coderabbit+=$'gate-suites (one)\tpass\t1s\thttps://example.invalid/one\t\n'
near_coderabbit+=$'CodeRabbit (advisory)\tpending\t0s\thttps://example.invalid/advisory\t\n'
near_coderabbit+=$'CodeRabbit \tpending\t0s\thttps://example.invalid/advisory-space\t\n'

expect_refuse 'missing required name cannot be replaced by a foreign pass' \
  'REFUSE-MISSING-CHECK' "$missing"
expect_refuse 'skipping required check is not green' 'skipping=1' "$skipping"
expect_refuse 'neutral required check is an unknown status, not green' \
  'REFUSE-UNKNOWN-STATUS' "$neutral"
expect_refuse 'PR head moving during exact-name query refuses' \
  'PR HEAD MOVED DURING EXACT-NAME VERDICT' "$green" 1
expect_refuse 'nonzero gh with complete passing rows is bootstrap-refused' \
  'REFUSE-GH-FAILED' "$green" 0 CLEAN 4
expect_refuse 'GitHub reports the PR base is behind and refuses the merge lane' \
  'PR BASE NOT CURRENT' "$green" 0 BEHIND
expect_pass 'all required names pass while exact CodeRabbit context is advisory' \
  'MERGE-SAFE' "$green"
expect_refuse 'near CodeRabbit names remain required check evidence' \
  'pending=2' "$near_coderabbit"
expect_malicious_candidate_refuse
install -m 755 "$trusted_toolchain/bd-ci-verdict" "$repo/toolchain/bin/bd-ci-verdict"
expect_merge_matches_head

# Advance main only on the remote. The production gate must freshly fetch it;
# comparing against the caller's stale origin/main would incorrectly pass.
git clone -q "$remote" "$root/advance"
git -C "$root/advance" config user.email ship-test@example.invalid
git -C "$root/advance" config user.name ship-test
printf 'main advanced\n' >> "$root/advance/subject.txt"
git -C "$root/advance" add subject.txt
git -C "$root/advance" commit -qm 'advance main'
git -C "$root/advance" push -q origin main
expect_refuse 'candidate based on stale main requires rebase' 'REBASE_REQUIRED' "$green"
expect_base_refuse

printf 'bd-ship pre-merge selftest: %d passed, %d failed\n' "$passed" "$failed"
[ "$failed" -eq 0 ]
