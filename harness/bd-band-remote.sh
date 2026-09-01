#!/bin/bash
# RUN A BAND ON A CAPACITY BOX, NOT ON THE TREE BEING EDITED.
#
# CLAUDE.md A6: "Do not run capture on a host whose tree is being edited, and do
# not edit it during capture." On 2026-08-29 that rule was broken twice on row
# 374 -- the fix landed at 14:42, the candidate froze at 14:43 from a diff taken
# at 14:41, and the 623-file band spent 13 minutes judging a tree that never
# contained the fix. 26 minutes lost, no CPU contention, no failing test. Running
# the band on another machine at a FROZEN SHA makes that impossible rather than
# forbidden.
#
# OPT-IN AND FAIL-SOFT BY DESIGN. It prints REMOTE-UNAVAILABLE and exits 64 when
# no capacity box can take the work; the caller then runs locally exactly as
# before. A capacity box that is down, full or unreachable must never block a
# cut -- a verification lane that can refuse a good cut is worse than a slow one.
#
# Capacity boxes clone from a LOCAL bare repo (/home/mboyle/bd.git ON THAT HOST),
# not from GitHub, so they cannot see a new SHA until it is pushed to them.
# Measured 2026-08-29: `git fetch origin` there succeeded and still left the box
# at 4d636df, and the checkout failed "reference is not a tree". We push the
# exact object over SSH first; that is the whole mechanism.
#
#   bd-band-remote.sh <sha> <test-file...>      rc = pytest's rc, or 64 if remote
set -uo pipefail
SHA=${1:?usage: bd-band-remote.sh <sha> <test file...>}; shift
# band needs selectors; precut and prepush judge the whole tree and take none.
case "${BD_REMOTE_MODE:-band}" in
  band) [ $# -gt 0 ] || { echo "no tests given"; exit 2; };;
esac
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] \
  || { echo "REMOTE-UNAVAILABLE: invalid full lowercase SHA"; exit 64; }
ROLES=${BD_BAND_ROLES:-/home/mboyle/.config/bd/roles}
REPO=${BD_BAND_REPO:-/home/mboyle/BulkDownloader}
LOG=${BD_BAND_LOG:-/home/mboyle/fleet-run-artifacts/2026-08-25/inflight/band-remote-${SHA:0:8}.log}
git -C "$REPO" cat-file -e "$SHA^{commit}" 2>/dev/null \
  || { echo "REMOTE-UNAVAILABLE: SHA is not a local commit"; exit 64; }

# THE BAND POPULATION IS `capacity` PLUS `runner`. Before the per-SHA
# worktree below, this lane ran `git checkout --detach` in the host's own
# BulkDownloader, which moves a SERVING host off main and destroys its deploy
# provenance -- so only non-serving capacity boxes could ever be listed. The
# band now runs in its own worktree and never touches the host checkout, so a
# host that serves BD can lend cores without lending its tree. `deploy` hosts
# stay out by default anyway: they are user-facing.
mapfile -t BOXES < <(awk '($1=="capacity" || $1=="runner") && NF>=3 {print $3}' "$ROLES" 2>/dev/null)
[ ${#BOXES[@]} -gt 0 ] || { echo "REMOTE-UNAVAILABLE: no capacity host in $ROLES"; exit 64; }
# Concurrent bands permitted per host. Each band takes -n 12 of 48 cores, so 4
# fills the box. A slot is a flock, and running out of slots is UNAVAILABLE --
# never a silent queue.
BAND_SLOTS=${BD_BAND_SLOTS:-4}
case "$BAND_SLOTS" in ''|*[!0-9]*|0) BAND_SLOTS=4;; esac

for ip in "${BOXES[@]}"; do
  ssh -o ConnectTimeout=6 -o BatchMode=yes "mboyle@$ip" true 2>/dev/null || continue
  # Never move main. Create the per-SHA ref only when it is absent; a lease on
  # absence closes the ls-remote/push race. An existing exact value is reused,
  # while any conflicting value makes this host unavailable without mutation.
  BAND_REF="refs/heads/bd-band/${SHA}"
  REMOTE="ssh://mboyle@$ip/home/mboyle/bd.git"
  observed=$(git -C "$REPO" ls-remote "$REMOTE" "$BAND_REF" 2>/dev/null) \
    || { echo "  $ip: immutable ref unreadable, trying next"; continue; }
  if [ -n "$observed" ]; then
    [ "$(printf '%s\n' "$observed" | awk 'NF {n++} END {print n+0}')" -eq 1 ] \
      || { echo "  $ip: ambiguous immutable ref, trying next"; continue; }
    read -r existing existing_ref extra <<< "$observed"
    if [ -n "${extra:-}" ] || [ "$existing_ref" != "$BAND_REF" ] \
        || [ "$existing" != "$SHA" ]; then
      echo "  $ip: conflicting immutable ref, trying next"; continue
    fi
  else
    git -C "$REPO" push -q --force-with-lease="$BAND_REF:" \
      "$REMOTE" "$SHA:$BAND_REF" 2>/dev/null \
      || { echo "  $ip: immutable ref creation failed, trying next"; continue; }
  fi
  observed=$(git -C "$REPO" ls-remote "$REMOTE" "$BAND_REF" 2>/dev/null) \
    || { echo "  $ip: immutable ref read-back failed, trying next"; continue; }
  [ "$(printf '%s\n' "$observed" | awk 'NF {n++} END {print n+0}')" -eq 1 ] \
    || { echo "  $ip: immutable ref read-back ambiguous, trying next"; continue; }
  read -r existing existing_ref extra <<< "$observed"
  if [ -n "${extra:-}" ] || [ "$existing_ref" != "$BAND_REF" ] \
      || [ "$existing" != "$SHA" ]; then
    echo "  $ip: immutable ref read-back mismatch, trying next"; continue
  fi

  # User-controlled selectors never become remote shell source. Each value is
  # base64-encoded into a nonempty shell-safe token; the fixed stdin script
  # decodes those tokens back into a Bash array and execs pytest with "${argv[@]}".
  # THREE MODES, ONE HOST-SELECTION MECHANISM. prepush and precut are the same
  # shape as the band -- read a frozen checkout at an exact SHA, run something,
  # return an exit code -- and they were the last two gates pinned to the
  # integrator, which is what capped concurrency at three cuts. Duplicating the
  # mirror/slot/worktree machinery to move them would have been the second
  # implementation A8 forbids, so it is one tool with a mode token.
  #
  # prepush lives OUTSIDE the repository, so its bytes are shipped with the
  # request; precut is in-repo and needs nothing shipped.
  MODE=${BD_REMOTE_MODE:-band}
  SHIPPED=""
  case "$MODE" in
    band|precut) ;;
    prepush)
      PREPUSH_SRC=${BD_REMOTE_PREPUSH:-/home/mboyle/bd-prepush.sh}
      [ -f "$PREPUSH_SRC" ] || { echo "REMOTE-UNAVAILABLE: no prepush script at $PREPUSH_SRC"; exit 64; }
      SHIPPED=$(base64 < "$PREPUSH_SRC" | tr -d '\n')
      ;;
    *) echo "REMOTE-UNAVAILABLE: unknown BD_REMOTE_MODE '$MODE'"; exit 64;;
  esac
  encoded=()
  for value in "$BAND_REF" "$SHA" "$ip" "$BAND_SLOTS" "$MODE" "${SHIPPED:-none}" "$@"; do
    token=$(printf '%s' "$value" | base64 | tr -d '\n')
    encoded+=("b$token")
  done
  remote_command="bash -s --"
  for token in "${encoded[@]}"; do remote_command+=" $token"; done
  ssh -o ConnectTimeout=10 "mboyle@$ip" "$remote_command" <<'REMOTE_SCRIPT' 2>&1 | tee "$LOG"
set -uo pipefail
decode() {
  local raw=$1
  [[ "$raw" == b* ]] || return 1
  printf '%s' "${raw#b}" | base64 -d
}
[ $# -ge 6 ] || exit 75
BAND_REF=$(decode "$1") || exit 75; shift
SHA=$(decode "$1") || exit 75; shift
ip=$(decode "$1") || exit 75; shift
SLOTS=$(decode "$1") || exit 75; shift
MODE=$(decode "$1") || exit 75; shift
SHIPPED=$(decode "$1") || exit 75; shift
case "$MODE" in band|precut|prepush) ;; *) exit 75;; esac
case "$SLOTS" in ''|*[!0-9]*|0) exit 75;; esac
[ "$SLOTS" -le 16 ] || exit 75
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || exit 75
[ "$BAND_REF" = "refs/heads/bd-band/$SHA" ] || exit 75
selectors=()
for token in "$@"; do
  value=$(decode "$token") || exit 75
  selectors+=("$value")
done
# band needs selectors; precut and prepush judge the whole tree and take none.
if [ "$MODE" = band ]; then
  [ ${#selectors[@]} -gt 0 ] || exit 75
fi
command -v flock >/dev/null 2>&1 || exit 75
# A SLOT, NOT A GLOBAL BUSY COUNT. The old rule refused the host when ANY
# pytest was running, which capped the box at one band and left 36 of 48 cores
# idle. Each slot is its own flock; exhausting them is UNAVAILABLE, so the
# caller tries the next host instead of queueing invisibly.
# BD_BAND_LOCKDIR is a TEST SEAM ONLY. ssh does not forward it, so a real
# remote run always uses /tmp; the fake-ssh harness runs in-process and can
# give each simulated host its own slot directory.
LOCKDIR=${BD_BAND_LOCKDIR:-/tmp}
slot=0
for i in $(seq 1 "$SLOTS"); do
  if exec 9>"$LOCKDIR/bd-band.slot$i.lock" && flock -n 9; then slot=$i; break; fi
done
[ "$slot" -gt 0 ] || exit 75
# ONE EXIT CODE FOR TEN CAUSES COST AN AFTERNOON. Until 2026-08-31 every
# refusal below was exit 75 and the caller printed "unavailable, busy, or
# subject proof failed" -- so when repointing the fleet at the authoritative
# origin broke the fetch, the message was indistinguishable from a busy host,
# every band silently ran on the integrator instead, and two concurrent
# verifies collided into seven false REDs. Each cause now has its own code and
# the caller NAMES it (A7: a diagnostic that collapses distinct failures costs
# the investigation, not just the message).
writers=$(ps -eo args= 2>/dev/null | grep -Ec '([c]odex|[c]laude)' || true)
case "$writers" in ''|*[!0-9]*) exit 76;; esac
[ "$writers" -eq 0 ] || exit 76
repo=$HOME/BulkDownloader
[ -d "$repo" ] || exit 77
[ -z "$(git -C "$repo" status --porcelain --untracked-files=no 2>/dev/null)" ] || exit 78
# FETCH FROM THE MIRROR BY PATH, NOT FROM `origin`. The candidate SHA exists
# only in the integrator's tree and in the per-host bare mirror it was just
# pushed into. On 2026-08-31 all twelve hosts were repointed at the
# authoritative origin -- right for deploys, and it made `fetch origin` here
# ask GitHub for a commit that was never pushed there.
mirror=$HOME/bd.git
[ -d "$mirror" ] || exit 79
git -C "$repo" fetch -q "$mirror" "$BAND_REF" 2>/dev/null || exit 80
fetched=$(git -C "$repo" rev-parse --verify 'FETCH_HEAD^{commit}' 2>/dev/null) || exit 80
[ "$fetched" = "$SHA" ] || exit 81
# THE BAND GETS ITS OWN WORKTREE. The host checkout is never moved, so this
# lane is safe on a host that is serving BD and two bands at different SHAs can
# share a box. The worktree is removed on every exit path; a leftover one is
# reused only when it is already at the exact SHA.
wt="$HOME/.bd-bands/$SHA"
if [ -d "$wt" ]; then
  at=$(git -C "$wt" rev-parse --verify 'HEAD^{commit}' 2>/dev/null || echo none)
  [ "$at" = "$SHA" ] || { git -C "$repo" worktree remove --force "$wt" 2>/dev/null; rm -rf "$wt"; }
fi
if [ ! -d "$wt" ]; then
  mkdir -p "$HOME/.bd-bands" || exit 82
  git -C "$repo" worktree add -q --detach "$wt" "$SHA" || exit 82
fi
got=$(git -C "$wt" rev-parse --verify 'HEAD^{commit}' 2>/dev/null) || exit 82
[ "$got" = "$SHA" ] || exit 82
[ -x "$repo/venv/bin/python" ] || exit 83
ln -sfn "$repo/venv" "$wt/venv"
[ -e "$repo/frontend/node_modules" ] && ln -sfn "$repo/frontend/node_modules" "$wt/frontend/node_modules"
cleanup() { cd "$repo" 2>/dev/null; git -C "$repo" worktree remove --force "$wt" 2>/dev/null || rm -rf "$wt"; }
trap cleanup EXIT
cd "$wt" || exit 82
echo "band-remote[$MODE]: $ip slot $slot at ${SHA:0:8}, ${#selectors[@]} selector(s)"
case "$MODE" in
  band)
    env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 \
      venv/bin/python -m pytest "${selectors[@]}" -n 12 --dist loadfile \
      --timeout=240 --timeout-method=signal --max-worker-restart=0 -p no:randomly
    band_rc=$?
    ;;
  precut)
    env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 \
      venv/bin/python toolchain/bin/bd-precut --gate
    band_rc=$?
    ;;
  prepush)
    # The shipped script runs against THIS worktree, which is a fresh checkout
    # at the exact candidate SHA -- so its regeneration and its drift check are
    # about the candidate and nothing else. The bytes are written under the
    # worktree so cleanup removes them with it.
    # OUTSIDE THE WORKTREE. Written inside it, the shipped script is an
    # UNTRACKED FILE, and prepush's own drift check counted it: the first
    # remote prepush failed with "untracked: .bd-prepush.shipped", a gate
    # reporting the harness that invoked it. Measured immediately.
    ship=$(mktemp "${TMPDIR:-/tmp}/bd-prepush-shipped.XXXXXX") || exit 84
    printf '%s' "$SHIPPED" | base64 -d > "$ship" || { rm -f "$ship"; exit 84; }
    chmod +x "$ship"
    bash "$ship" "$wt"
    band_rc=$?
    rm -f "$ship"
    ;;
esac
cleanup; trap - EXIT
exit "$band_rc"
REMOTE_SCRIPT
  remote_rc=${PIPESTATUS[0]}
  case "$remote_rc" in
    75)  echo "  $ip: no free band slot, or a malformed request; trying next"; continue;;
    76)  echo "  $ip: a local-model or agent writer is running there; trying next"; continue;;
    77)  echo "  $ip: no BulkDownloader checkout; trying next"; continue;;
    78)  echo "  $ip: its checkout has uncommitted tracked changes; trying next"; continue;;
    79)  echo "  $ip: NO BARE MIRROR at ~/bd.git -- create one with"; \
         echo "        ssh mboyle@$ip 'git init --bare ~/bd.git'"; continue;;
    80)  echo "  $ip: the candidate ref did not arrive from its mirror; trying next"; continue;;
    81)  echo "  $ip: the fetched object is not the candidate SHA; trying next"; continue;;
    82)  echo "  $ip: could not build a worktree at the candidate; trying next"; continue;;
    83)  echo "  $ip: no venv interpreter; trying next"; continue;;
    84)  echo "  $ip: the shipped prepush script could not be written; trying next"; continue;;
    255) echo "  $ip: ssh failed; trying next"; continue;;
  esac
  exit "$remote_rc"
done
echo "REMOTE-UNAVAILABLE: no capacity host free -- caller should run locally"
exit 64
