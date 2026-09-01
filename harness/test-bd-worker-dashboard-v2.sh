#!/usr/bin/env bash
# Focused self-test for managed-task truthfulness; no live state is changed.
set -euo pipefail

root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
mkdir -p "$root/repo" "$root/artifacts/vm-agent-results" "$root/bin"
git -C "$root/repo" init -q
git -C "$root/repo" config user.email dashboard@test.invalid
git -C "$root/repo" config user.name dashboard-test
mkdir -p "$root/repo/bulk_downloader"
printf '__version__ = "test"\n' > "$root/repo/bulk_downloader/__init__.py"
git -C "$root/repo" add . && git -C "$root/repo" commit -qm init
git -C "$root/repo" branch -M main
git -C "$root/repo" update-ref refs/remotes/origin/main HEAD
printf 'row402-final-gates\tRUNNING\tdeclared gate\nroot-integration\tRUNNING\tdeclared root\n' > "$root/managed.tsv"
printf '0\n' > "$root/artifacts/row402-final-gates.log.rc"
printf 'integrator test5 127.0.0.1\n' > "$root/roles"
printf 'integration test5 127.0.0.1 0 integrate\n' > "$root/worker-roles"

output=$(PATH="$root/bin:$PATH" BD_DASH_ONCE=1 BD_DASH_NO_CLEAR=1 BD_WORKER_DASH_INTERVAL=0 \
  BD_DASH_REPO="$root/repo" BD_DASH_MANAGED="$root/managed.tsv" \
  BD_DASH_ARTIFACT_ROOT="$root/artifacts" BD_DASH_ROLES="$root/roles" \
  BD_DASH_WORKER_ROLES="$root/worker-roles" BD_DASH_PROBE=/bin/false \
  /home/mboyle/bd-persist/harness/bd-worker-dashboard-v2.sh | sed -r 's/\x1B\[[0-9;]*[mK]//g')

grep -Eq 'row402-final-gates[[:space:]]+COMPLETE' <<<"$output"
grep -Eq 'root-integration[[:space:]]+STALE' <<<"$output"
grep -Fq 'rc=0' <<<"$output"
grep -Fq 'no local process/artifact' <<<"$output"
printf 'dashboard truthfulness self-test: PASS\n'
