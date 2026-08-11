#!/usr/bin/env bash
# deploy_fleet.sh -- run scripts/deploy.sh on every host in the fleet.
#
# WHY THE HOST LIST IS NOT IN THIS REPO. The repo is public; the fleet's
# addresses are not the repo's business. The list lives in an UNTRACKED file and
# this script refuses (exit 2) without one, naming the path -- an empty fleet
# must never look like a successful deploy of nothing, which is section 0's
# whole subject.
#
# WHAT IT DOES NOT DO. It does not decide whether a host SHOULD be deployed, it
# does not roll back, and it does not stop at the first failure: it runs every
# host and reports each verdict, because "which of my three boxes is wrong" is
# the question an operator actually has. The exit code is nonzero if ANY host
# failed, so a green line per host and a red summary cannot disagree.
#
# Each host is deployed by deploy.sh itself, so every safety property lives in
# one place: the pytest preflight, the stopped-window recovery, the health gate.
# This is a loop, deliberately, and not a second implementation.
set -u

HOSTS_FILE="${HOSTS_FILE:-$HOME/.config/bd/hosts}"
DEPLOY_CMD='cd ~/BulkDownloader && bash scripts/deploy.sh'
FAILED=0

usage() {
  cat <<'USAGE'
deploy_fleet.sh -- deploy every host in the fleet.

  --hosts FILE   host list (default: ~/.config/bd/hosts)
  -n, --dry-run  print what would run, touch nothing
  -h, --help     print this and exit 0

The host file is one "label address" per line; blank lines and # comments are
ignored. A line whose label matches this machine's hostname is deployed
LOCALLY rather than over ssh. See docs/repo/hosts.example.

exit 0  every host deployed and verified
exit 1  at least one host failed -- the summary names which
exit 2  refusal: no host file, or it lists no hosts. Nothing was touched.
USAGE
}

DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --hosts) HOSTS_FILE="${2:-}"; shift 2;;
    -n|--dry-run) DRY=1; shift;;
    -h|--help) usage; exit 0;;
    *) printf 'deploy_fleet.sh: REFUSED: unknown argument: %s\n' "$1" >&2; exit 2;;
  esac
done

if [ ! -f "$HOSTS_FILE" ]; then
  printf 'deploy_fleet.sh: REFUSED: no host list at %s\n' "$HOSTS_FILE" >&2
  printf 'deploy_fleet.sh: it is deliberately UNTRACKED -- the fleet addresses\n' >&2
  printf 'deploy_fleet.sh: are not this public repo'"'"'s business. Copy\n' >&2
  printf 'deploy_fleet.sh: docs/repo/hosts.example to that path and edit it.\n' >&2
  exit 2
fi

# Parse first, act second: a malformed list must not deploy half a fleet.
LABELS=(); ADDRS=()
while read -r label addr _rest; do
  case "${label:-}" in ''|\#*) continue;; esac
  [ -n "${addr:-}" ] || { printf 'deploy_fleet.sh: REFUSED: %s: line for "%s" has no address\n' "$HOSTS_FILE" "$label" >&2; exit 2; }
  LABELS+=("$label"); ADDRS+=("$addr")
done < "$HOSTS_FILE"

# A ZERO-HOST FLEET IS A REFUSAL, NOT A SUCCESS. Without this an empty or
# all-commented file deploys nothing and exits 0 -- a gate reporting OK over an
# empty denominator, which is exactly the shape this repo keeps getting bitten by.
if [ "${#LABELS[@]}" -eq 0 ]; then
  printf 'deploy_fleet.sh: REFUSED: %s lists no hosts\n' "$HOSTS_FILE" >&2
  exit 2
fi

ME="$(hostname)"
printf 'deploy_fleet.sh: %d host(s) from %s\n' "${#LABELS[@]}" "$HOSTS_FILE"

i=0
while [ "$i" -lt "${#LABELS[@]}" ]; do
  label="${LABELS[$i]}"; addr="${ADDRS[$i]}"; i=$((i + 1))
  if [ "$label" = "$ME" ]; then where="local"; else where="$addr"; fi

  if [ "$DRY" -eq 1 ]; then
    printf '  %-10s %-16s would run: %s\n' "$label" "$where" "$DEPLOY_CMD"
    continue
  fi

  if [ "$label" = "$ME" ]; then
    out="$(bash "$(dirname "$0")/deploy.sh" 2>&1)"; rc=$?
  else
    out="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$addr" "$DEPLOY_CMD" 2>&1)"; rc=$?
  fi

  # The LAST line of deploy.sh is its verdict; anything else is progress.
  printf '  %-10s %-16s exit=%s  %s\n' "$label" "$where" "$rc" "$(printf '%s\n' "$out" | tail -1)"
  [ "$rc" -eq 0 ] || { FAILED=1; printf '%s\n' "$out" | sed 's/^/      | /' >&2; }
done

# A DRY RUN MUST NOT CLAIM A DEPLOY. The first version fell through to the
# success line and printed "all 3 host(s) deployed and verified" having touched
# nothing -- a false verdict in the summary, which is the one line an operator
# actually reads.
if [ "$DRY" -eq 1 ]; then
  printf 'deploy_fleet.sh: DRY RUN -- %d host(s) listed, NOTHING deployed\n' "${#LABELS[@]}"
  exit 0
fi

if [ "$FAILED" -ne 0 ]; then
  printf 'deploy_fleet.sh: AT LEAST ONE HOST FAILED -- full output above\n' >&2
  exit 1
fi
printf 'deploy_fleet.sh: all %d host(s) deployed and verified\n' "${#LABELS[@]}"
