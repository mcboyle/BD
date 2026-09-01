#!/usr/bin/env bash
# Hermetic contract test for bd-verify-cut. It creates a local bare origin and
# a two-commit candidate, and substitutes only the expensive gate executors.
set -euo pipefail

script=${BD_VERIFY_CUT_SCRIPT:-/home/mboyle/bd-persist/harness/bd-verify-cut.sh}

root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
repo=$root/repo
origin=$root/origin.git
artifacts=$root/artifacts
bin=$root/bin
mkdir -p "$artifacts" "$bin"

git init --bare -q "$origin"
git init -q "$repo"
git -C "$repo" config user.email verify-cut@test.invalid
git -C "$repo" config user.name verify-cut-test
git -C "$repo" remote add origin "$origin"
mkdir -p "$repo/bulk_downloader" "$repo/tests" "$repo/toolchain/bin"
printf 'venv/\n' > "$repo/.gitignore"
printf 'BASE = 1\n' > "$repo/bulk_downloader/feature.py"
printf 'def test_contract_floor():\n    assert True\n' > "$repo/tests/test_contracts.py"
printf '%s\n' \
  '#!/usr/bin/env python3' \
  'import os, pathlib, sys' \
  'capture = os.environ.get("FAKE_PRECUT_CAPTURE")' \
  'if capture: pathlib.Path(capture).write_text("\n".join(sys.argv[1:]) + "\n")' \
  'raise SystemExit(int(os.environ.get("FAKE_PRECUT_RC", "0")))' \
  > "$repo/toolchain/bin/bd-precut"
printf '%s\n' \
  '#!/usr/bin/env python3' \
  'import json, os, pathlib, subprocess, sys' \
  'rc = int(os.environ.get("FAKE_DERIVE_RC", "0"))' \
  'if rc: raise SystemExit(rc)' \
  'args = sys.argv[1:]' \
  'start = args.index("--files") + 1' \
  'end = args.index("--json")' \
  'changed = args[start:end]' \
  'capture = os.environ.get("FAKE_DERIVE_CAPTURE")' \
  'if capture: pathlib.Path(capture).write_text("\n".join(changed) + "\n")' \
  'mode = os.environ.get("FAKE_DERIVE_MODE", "")' \
  'if mode == "drop-changed": changed = changed[1:]' \
  'if mode == "tamper-changed":' \
  '    pathlib.Path("../changed.z").write_bytes(b"tests/test_feature.py\0")' \
  '    changed = ["tests/test_feature.py"]' \
  'band = ["tests/test_contracts.py"]' \
  'band.extend(path for path in changed if path.startswith("tests/test") and path.endswith(".py"))' \
  'band = sorted(set(band))' \
  'if mode == "omit-own-test": band = [path for path in band if path != "tests/test_feature.py"]' \
  'if mode == "tamper-band":' \
  '    subprocess.Popen(["/bin/sh", "-c", "for n in $(seq 1 500); do if [ -e ../band.z ]; then printf tests/test_contracts.py\\\\000 > ../band.z; exit 0; fi; sleep 0.01; done"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)' \
  'print(json.dumps({"mode":"explicit", "changed":changed, "band":band, "band_cmd":"bd-band " + " ".join(band)}))' \
  > "$repo/toolchain/bin/bd-band-derive"
git -C "$repo" add .
git -C "$repo" commit -qm base
git -C "$repo" branch -M main
git -C "$repo" push -q -u origin main
base=$(git -C "$repo" rev-parse HEAD)

# Commit one changes production; commit two adds the cut's own test. HEAD~1
# therefore cannot see the production change, while merge-base..candidate must.
printf 'BASE = 2\n' > "$repo/bulk_downloader/feature.py"
git -C "$repo" add bulk_downloader/feature.py
git -C "$repo" commit -qm 'production half'
printf 'def test_feature():\n    assert True\n' > "$repo/tests/test_feature.py"
git -C "$repo" add tests/test_feature.py
git -C "$repo" commit -qm 'test half'
candidate=$(git -C "$repo" rev-parse HEAD)
candidate_tree=$(git -C "$repo" rev-parse 'HEAD^{tree}')
if git -C "$repo" diff --name-only HEAD~1 HEAD | grep -q 'bulk_downloader/feature.py'; then
  echo 'fixture invalid: final commit unexpectedly contains production change' >&2
  exit 1
fi
mkdir -p "$repo/venv/bin"
ln -s "$(command -v python3)" "$repo/venv/bin/python"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -u' \
  'if [ "${FAKE_PREPUSH_MODE:-}" = mutate ]; then' \
  '  printf "# synthetic mutation\n" >> "$1/bulk_downloader/feature.py"' \
  'fi' \
  'if [ "${FAKE_PREPUSH_MODE:-}" = mutate-baseline ]; then' \
  '  printf "synthetic baseline mutation\n" >> "${FAKE_BASELINE:?}"' \
  'fi' \
  'if [ "${FAKE_PREPUSH_MODE:-}" = hold ]; then' \
  '  : > "${FAKE_PREPUSH_READY:?}"' \
  '  while [ ! -e "${FAKE_PREPUSH_RELEASE:?}" ]; do sleep 0.02; done' \
  'fi' \
  'exit "${FAKE_PREPUSH_RC:-0}"' \
  > "$bin/prepush"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'printf "%s\n" "$@" > "${FAKE_REMOTE_CAPTURE:?}"' \
  'exit "${FAKE_REMOTE_RC:-0}"' \
  > "$bin/remote"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'for arg in "$@"; do' \
  '  if [ "${FAKE_RM_FAIL_PATH:-}" = "$arg" ]; then exit 1; fi' \
  'done' \
  'exec /bin/rm "$@"' \
  > "$bin/rm"
chmod +x "$bin/prepush" "$bin/remote" "$bin/rm"

run_case() {
  local tag=$1 output=$2
  shift 2
  set +e
  env \
    BD_VERIFY_CUT_ARTIFACT_DIR="$artifacts" \
    BD_VERIFY_CUT_PREPUSH="$bin/prepush" \
    BD_VERIFY_CUT_BAND_REMOTE="$bin/remote" \
    FAKE_DERIVE_CAPTURE="$root/$tag-derive-input" \
    FAKE_PRECUT_CAPTURE="$root/$tag-precut-input" \
    FAKE_REMOTE_CAPTURE="$root/$tag-remote-input" \
    "$@" \
    bash "$script" "$repo" "$tag" > "$output" 2>&1
  CASE_RC=$?
  set -e
}

run_case multi "$root/multi.out"
[ "$CASE_RC" -eq 0 ] || { cat "$root/multi.out"; exit 1; }
grep -Fq "CANDIDATE_SHA=$candidate" "$root/multi.out"
grep -Fq "CANDIDATE_TREE=$candidate_tree" "$root/multi.out"
grep -Fq "BASE_SHA=$base" "$root/multi.out"
grep -Fq 'CHANGED_FILES=2' "$root/multi.out"
[ "$(grep -c '^BAND_FILES=' "$root/multi.out")" -eq 1 ]
grep -Fxq 'bulk_downloader/feature.py' "$root/multi-derive-input"
grep -Fxq 'tests/test_feature.py' "$root/multi-derive-input"
grep -Fxq "$candidate" "$root/multi-remote-input"
grep -Fxq 'tests/test_feature.py' "$root/multi-remote-input"
grep -Fq 'ALL GREEN -- shippable' "$root/multi.out"
grep -Fxq -- '--gate' "$root/multi-precut-input"
! grep -Fq -- '--baseline' "$root/multi-precut-input"
python3 - "$artifacts/multi-denominator.json" "$base" "$candidate" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["base_sha"] == sys.argv[2]
assert payload["candidate_sha"] == sys.argv[3]
assert payload["changed"] == ["bulk_downloader/feature.py", "tests/test_feature.py"]
assert "tests/test_feature.py" in payload["band"]
PY

# An explicit baseline is resolved before candidate code, recorded exactly, and
# forwarded to bd-precut by its canonical absolute path.
baseline_source=$root/baseline-source.txt
baseline_zip=$root/release-baseline.zip
printf 'baseline fixture\n' > "$baseline_source"
python3 - "$baseline_zip" "$baseline_source" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.write(sys.argv[2], "baseline-source.txt")
PY
baseline_path=$(realpath -e -- "$baseline_zip")
baseline_sha=$(sha256sum "$baseline_path" | awk '{print $1}')
run_case baseline "$root/baseline.out" "BD_VERIFY_CUT_BASELINE=$baseline_zip"
[ "$CASE_RC" -eq 0 ] || { echo "baseline forwarding case rc=$CASE_RC"; cat "$root/baseline.out"; exit 1; }
grep -Fxq "BASELINE_PATH=$baseline_path" "$root/baseline.out"
grep -Fxq "BASELINE_SHA256=$baseline_sha" "$root/baseline.out"
grep -Fxq -- '--baseline' "$root/baseline-precut-input"
grep -Fxq "$baseline_path" "$root/baseline-precut-input"
python3 - "$artifacts/baseline-denominator.json" "$baseline_path" "$baseline_sha" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["baseline_path"] == sys.argv[2]
assert payload["baseline_sha256"] == sys.argv[3]
PY

# A missing requested baseline must be terminal before candidate code runs.
missing_baseline=$root/missing-baseline.zip
run_case missing-baseline "$root/missing-baseline.out" "BD_VERIFY_CUT_BASELINE=$missing_baseline"
[ "$CASE_RC" -eq 2 ] || { echo "missing baseline case rc=$CASE_RC"; cat "$root/missing-baseline.out"; exit 1; }
grep -Fq 'baseline cannot be resolved' "$root/missing-baseline.out"
[ ! -e "$root/missing-baseline-precut-input" ]

# Candidate-controlled prepush can mutate an external baseline after it was
# pinned. Revalidation immediately before precut must catch the changed hash.
mutated_baseline=$root/mutated-baseline.zip
cp -- "$baseline_zip" "$mutated_baseline"
run_case mutated-baseline "$root/mutated-baseline.out" \
  "BD_VERIFY_CUT_BASELINE=$mutated_baseline" FAKE_PREPUSH_MODE=mutate-baseline \
  "FAKE_BASELINE=$mutated_baseline"
[ "$CASE_RC" -eq 2 ] || { echo "mutated baseline case rc=$CASE_RC"; cat "$root/mutated-baseline.out"; exit 1; }
grep -Fq 'baseline changed or is no longer a readable regular ZIP before precut' "$root/mutated-baseline.out"
[ ! -e "$root/mutated-baseline-precut-input" ]

run_case red "$root/red.out" FAKE_PRECUT_RC=1
[ "$CASE_RC" -eq 1 ] || { echo "red case rc=$CASE_RC"; cat "$root/red.out"; exit 1; }
grep -Fq 'PRECUT_RC=1' "$root/red.out"
grep -Fq 'NOT SHIPPABLE' "$root/red.out"

run_case unknown "$root/unknown.out" FAKE_DERIVE_RC=7
[ "$CASE_RC" -eq 2 ] || { echo "unknown case rc=$CASE_RC"; cat "$root/unknown.out"; exit 1; }
grep -Fq 'BAND_DERIVE_RC=7' "$root/unknown.out"
grep -Fq 'UNKNOWN, not permission' "$root/unknown.out"

run_case dropped "$root/dropped.out" FAKE_DERIVE_MODE=drop-changed
[ "$CASE_RC" -eq 2 ] || { echo "dropped accounting case rc=$CASE_RC"; cat "$root/dropped.out"; exit 1; }
grep -Fq 'deriver did not account for the exact changed set' "$root/dropped.out"

run_case omitted "$root/omitted.out" FAKE_DERIVE_MODE=omit-own-test
[ "$CASE_RC" -eq 2 ] || { echo "omitted own-test case rc=$CASE_RC"; cat "$root/omitted.out"; exit 1; }
grep -Fq 'band omits changed test path' "$root/omitted.out"

# The candidate can see ../changed.z from its disposable checkout.  Its JSON
# must still be compared to a trusted, post-candidate recomputation of the cut.
run_case tampered-changed "$root/tampered-changed.out" FAKE_DERIVE_MODE=tamper-changed
[ "$CASE_RC" -eq 2 ] || { echo "tampered changed-set case rc=$CASE_RC"; cat "$root/tampered-changed.out"; exit 1; }
grep -Fq 'deriver did not account for the exact changed set' "$root/tampered-changed.out"

# A detached child can race the old ../band.z handoff.  The actual executor
# must receive the audited in-memory band, including the cut's own test.
run_case tampered-band "$root/tampered-band.out" FAKE_DERIVE_MODE=tamper-band
[ "$CASE_RC" -eq 0 ] || { echo "tampered band race case rc=$CASE_RC"; cat "$root/tampered-band.out"; exit 1; }
grep -Fxq 'tests/test_feature.py' "$root/tampered-band-remote-input"

# An artifact that cannot be cleared is stale evidence, not permission to
# continue. The prior evidence remains physically present but cannot be used.
printf 'stale evidence\n' > "$artifacts/stale-cleanup-denominator.json"
run_case stale-cleanup "$root/stale-cleanup.out" \
  "PATH=$bin:$PATH" "FAKE_RM_FAIL_PATH=$artifacts/stale-cleanup-denominator.json"
[ "$CASE_RC" -eq 2 ] || { echo "stale cleanup case rc=$CASE_RC"; cat "$root/stale-cleanup.out"; exit 1; }
grep -Fq 'cannot clear stale artifact' "$root/stale-cleanup.out"
grep -Fxq 'stale evidence' "$artifacts/stale-cleanup-denominator.json"

# A tag owns one artifact set.  Once the first invocation has entered its
# prepush gate, a second invocation of the same tag must fail closed.
same_ready=$root/same-tag-ready
same_release=$root/same-tag-release
set +e
env \
  BD_VERIFY_CUT_ARTIFACT_DIR="$artifacts" \
  BD_VERIFY_CUT_PREPUSH="$bin/prepush" \
  BD_VERIFY_CUT_BAND_REMOTE="$bin/remote" \
  FAKE_DERIVE_CAPTURE="$root/same-first-derive-input" \
  FAKE_REMOTE_CAPTURE="$root/same-first-remote-input" \
  FAKE_PREPUSH_MODE=hold \
  FAKE_PREPUSH_READY="$same_ready" \
  FAKE_PREPUSH_RELEASE="$same_release" \
  bash "$script" "$repo" same-tag > "$root/same-first.out" 2>&1 &
same_first_pid=$!
set -e
for _ in $(seq 1 250); do
  [ -e "$same_ready" ] && break
  sleep 0.02
done
[ -e "$same_ready" ] || { echo 'first same-tag verifier never reached prepush' >&2; exit 1; }
set +e
env \
  BD_VERIFY_CUT_ARTIFACT_DIR="$artifacts" \
  BD_VERIFY_CUT_PREPUSH="$bin/prepush" \
  BD_VERIFY_CUT_BAND_REMOTE="$bin/remote" \
  FAKE_DERIVE_CAPTURE="$root/same-second-derive-input" \
  FAKE_REMOTE_CAPTURE="$root/same-second-remote-input" \
  bash "$script" "$repo" same-tag > "$root/same-second.out" 2>&1
same_second_rc=$?
set -e
: > "$same_release"
set +e
wait "$same_first_pid"
same_first_rc=$?
set -e
[ "$same_first_rc" -eq 0 ] || { echo "first same-tag case rc=$same_first_rc"; cat "$root/same-first.out"; exit 1; }
[ "$same_second_rc" -eq 2 ] || { echo "second same-tag case rc=$same_second_rc"; cat "$root/same-second.out"; exit 1; }
grep -Fq 'verification tag is already active' "$root/same-second.out"

# A nominally-green gate that mutates the disposable checkout is UNKNOWN. The
# source candidate remains byte/tree exact because readers never shared it.
run_case mutation "$root/mutation.out" FAKE_PREPUSH_MODE=mutate
[ "$CASE_RC" -eq 2 ] || { echo "mutation case rc=$CASE_RC"; cat "$root/mutation.out"; exit 1; }
grep -Fq 'isolated checkout changed' "$root/mutation.out"
[ "$(git -C "$repo" rev-parse HEAD)" = "$candidate" ]
[ "$(git -C "$repo" rev-parse 'HEAD^{tree}')" = "$candidate_tree" ]
[ -z "$(git -C "$repo" status --porcelain=v1 --untracked-files=no)" ]

printf 'stale evidence\n' > "$artifacts/dirty-denominator.json"
printf '# tracked source dirt\n' >> "$repo/bulk_downloader/feature.py"
run_case dirty "$root/dirty.out"
[ "$CASE_RC" -eq 2 ] || { echo "dirty source case rc=$CASE_RC"; cat "$root/dirty.out"; exit 1; }
grep -Fq 'candidate source tracked tree is dirty' "$root/dirty.out"
[ ! -e "$artifacts/dirty-denominator.json" ]

printf 'bd-verify-cut self-test: PASS\n'
