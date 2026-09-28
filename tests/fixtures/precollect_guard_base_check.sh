#!/bin/bash
# test_precollect_guard_base_check.sh -- ROW precollect-guard-base-check (T2).
# arg1 = guard script under test (default: deployed copy). Builds fully-sandboxed fake
# git repos under a tmpdir (BD_GUARD_CANON / BD_CARRY overrides) so nothing here touches
# the real BulkDownloader checkout or main.
#
# WHAT THIS PROVES, IN ORDER:
#  1. POSITIVE CONTROL: the guard CAN say REFUSE/STALE BASE for an ordinary case where the
#     declared base is behind CANON's own `main` (this already worked before any fix).
#  2. THE CLAIMED BUG (row841-class, UNVERIFIED per brief): CANON's own `main` branch can
#     itself be behind a remote-tracking ref it has already fetched (origin/main), e.g. when
#     something lands on the real remote without this host's checkout catching up. CHECK 5
#     trusts CANON's local `main` unconditionally as "the tip" -- if the declared base equals
#     that stale local main, the guard used to print OK ("declared base is current main")
#     while origin/main was already ahead. That is a real silent-loss mode: a collect right
#     now would take work built on a base the true trunk had already moved past.
#  3. NEGATIVE CONTROL: when CANON's local main is NOT behind any remote-tracking ref (the
#     ordinary, healthy case), the guard must still say OK -- the fix must not turn every
#     pass into a false refusal.
#  4. E1 FIX (review 2026-09-20T19:18Z, correctness lens): the first cut of the fix downgraded
#     every host-lag case to UNKNOWN (rc=4) and SKIPPED the BASE-vs-trunk comparison, even when
#     the true tip was measurable (its objects are right there in CANON). That made the O734.3
#     REFUSE arm unreachable during any host-lag window: a stale base with a genuine conflict
#     against the true trunk used to REFUSE and now silently UNKNOWNs through. Fixture 4 stacks
#     "local main is behind origin/main" (host lag) WITH "declared base conflicts with origin/main"
#     (real staleness) and requires a REFUSE naming the count against the TRUE tip, not a UNKNOWN.
# shellcheck disable=SC2034  # C2/U1/RC2 name fixture topology for the reader; commits are the side effect
set -u
GUARD=${1:-/home/mboyle/bd-persist/harness/bd-precollect-guard.sh}
PASS=0; FAIL=0
ok(){ if [ "$2" = "$3" ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); echo "FAIL $1: wanted [$3] got [$2]"; fi; }
has(){ if grep -qF "$3" <<<"$2"; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); echo "FAIL $1: output missing [$3]"; echo "$2" | sed 's/^/    /'; fi; }
lacks(){ if grep -qF "$3" <<<"$2"; then FAIL=$((FAIL+1)); echo "FAIL $1: output should NOT contain [$3]"; echo "$2" | sed 's/^/    /'; else PASS=$((PASS+1)); fi; }
D=$(mktemp -d /tmp/precollect-guard-base-check.XXXXXX)
# The guard's PREP-INFLIGHT check reads the fleet review.log; point it at an empty sandboxed
# log so the fixture neither depends on nor reads the live one (BH-bd-agy-audit-2-006).
: > "$D/review.log"; export BD_REVIEW_LOG="$D/review.log"
g(){ git -C "$1" -c user.name=t -c user.email=t@t "${@:2}"; }
mkcommit(){ # mkcommit <repo> <msg>
  echo "$2 $(date +%s%N)" >> "$1/f.txt"; g "$1" add f.txt >/dev/null; g "$1" commit -q -m "$2" >/dev/null; g "$1" rev-parse HEAD
}
mkwt(){ # mkwt <src-repo> <sha> <dst>  -- a self-contained clone of src-repo checked out at sha, no remotes
  git clone -q "$1" "$3" >/dev/null 2>&1
  g "$3" checkout -q "$2"
  g "$3" remote remove origin >/dev/null 2>&1
}
donemd(){ # donemd <wt> <base>
  cat > "$1/DONE.md" <<EOF
VERDICT: PATCH
BASE: $2
RED COMMAND: bash tests/fake.sh
EOF
}
stage_one(){ # stage_one <wt> -- gives the cut a non-empty declared path set (CHECK 4 needs PATHS != empty)
  echo "cut change" > "$1/cut_change.txt"
  g "$1" add cut_change.txt >/dev/null
}
mkcarry(){ # mkcarry <dir> -- one unrelated *.patch so CHECK 4's "no patches at all" UNKNOWN branch doesn't fire
  mkdir -p "$1"
  printf 'diff --git a/unrelated_%s.txt b/unrelated_%s.txt\n' "$RANDOM" "$RANDOM" > "$1/unrelated.patch"
}

### FIXTURE 1 -- CANON healthy, no origin remote at all: baseline positive control.
# The index must genuinely CONFLICT with what main moved on (same line, different content),
# else the O734.3 clean-merge arm correctly downgrades a stale-but-mergeable base to a NOTE,
# not a REFUSE -- that arm is intentional and not what this fixture is testing.
CANON1="$D/canon1"; mkdir -p "$CANON1"; g "$CANON1" init -q -b main
printf 'shared-line\n' > "$CANON1/shared.txt"; g "$CANON1" add shared.txt >/dev/null; g "$CANON1" commit -q -m base >/dev/null
C1=$(mkcommit "$CANON1" c1)
printf 'shared-line-upstream\n' > "$CANON1/shared.txt"; g "$CANON1" add shared.txt >/dev/null; g "$CANON1" commit -q -m c2 >/dev/null
C2=$(g "$CANON1" rev-parse HEAD)   # main tip, diverges shared.txt from C1
WT1="$D/wt1"; mkwt "$CANON1" "$C1" "$WT1"
donemd "$WT1" "$C1"; stage_one "$WT1"
printf 'shared-line-worker\n' > "$WT1/shared.txt"; g "$WT1" add shared.txt >/dev/null
CARRY_EMPTY="$D/carry-empty"; mkcarry "$CARRY_EMPTY"
OUT1=$(BD_GUARD_CANON="$CANON1" BD_CARRY="$CARRY_EMPTY" bash "$GUARD" local "$WT1" 2>&1); RC1=$?
ok '1 positive-control stale-base rc' "$RC1" 1
has '1 positive-control names STALE BASE' "$OUT1" "STALE BASE"

### FIXTURE 2 -- CANON's own main is BEHIND an already-fetched origin/main (row841 class).
UPSTREAM="$D/upstream"; mkdir -p "$UPSTREAM"; g "$UPSTREAM" init -q -b main
U1=$(mkcommit "$UPSTREAM" u1)
U2=$(mkcommit "$UPSTREAM" u2)   # this is "origin/main" at fetch time -- also the declared BASE
CANON2="$D/canon2"; git clone -q "$UPSTREAM" "$CANON2" >/dev/null 2>&1; g "$CANON2" checkout -q main >/dev/null 2>&1
# origin advances further (simulates a PR landing on the real remote without this host pulling)
U3=$(mkcommit "$UPSTREAM" u3)
g "$CANON2" fetch -q origin >/dev/null 2>&1   # CANON2's origin/main now == U3; CANON2's local main is still U2
[ "$(g "$CANON2" rev-parse main)" = "$U2" ] || { echo "FIXTURE BROKEN: CANON2 main moved"; FAIL=$((FAIL+1)); }
[ "$(g "$CANON2" rev-parse origin/main)" = "$U3" ] || { echo "FIXTURE BROKEN: CANON2 origin/main did not fetch"; FAIL=$((FAIL+1)); }
WT2="$D/wt2"; mkwt "$CANON2" "$U2" "$WT2"
donemd "$WT2" "$U2"; stage_one "$WT2"   # declares BASE == CANON2's local main -- the row841 shape
CARRY2="$D/carry2"; mkcarry "$CARRY2"
OUT2=$(BD_GUARD_CANON="$CANON2" BD_CARRY="$CARRY2" bash "$GUARD" local "$WT2" 2>&1); RC2=$?
# E1 tightened (review 2026-09-20T19:18Z): this fixture's staged change does not conflict with
# what origin/main moved (an unrelated new file), so the CORRECT outcome after the flip is a
# transparent NOTE measuring against origin/main and an eventual OK -- NOT a bare "any rc != 0"
# (that assertion could not tell a measured NOTE from an unmeasured UNKNOWN, E2). What must be
# true is that the guard MEASURED against the true tip, not the stale local one.
has '2 flips to measuring against origin/main (not silently UNKNOWN)' "$OUT2" "measuring base currency against refs/remotes/origin/main"
has '2 names the true tip origin/main, not a bare local-main OK' "$OUT2" "$U3"
# E3 (review 2026-09-20T19:37Z, row's own symptom): the OK line's base clause must be honest.
# BASE(U2) != the measured tip MAIN(U3) after the flip -- if the index merges clean, the O734.3
# NOTE arm fires (STALE BASE ... BEHIND ...) and the OK line must NOT then also claim
# "declared base is current main", which contradicts the line printed just above it.
has '2 OK line names BEHIND-the-tip clean-merge clause, not a false "current main"' "$OUT2" "declared base is BEHIND the tip by"
lacks '2 OK line does not falsely claim current main after a STALE BASE NOTE' "$OUT2" "declared base is current main"

### FIXTURE 4 -- E1 FIX: host lag (local main behind origin/main) STACKED with a real conflict
# against the true trunk. Before the fix this REFUSED correctly (fixture 1 shape); the first
# cut of this fix regressed it to UNKNOWN. Must REFUSE, and the count must be against the TRUE
# tip (origin/main), not the stale local main.
CANON4="$D/canon4"; mkdir -p "$CANON4"; g "$CANON4" init -q -b main
printf 'shared-line\n' > "$CANON4/shared.txt"; g "$CANON4" add shared.txt >/dev/null; g "$CANON4" commit -q -m u0 >/dev/null
V0=$(g "$CANON4" rev-parse HEAD)   # this will be the declared BASE
printf 'shared-line-mainlag\n' > "$CANON4/shared.txt"; g "$CANON4" add shared.txt >/dev/null; g "$CANON4" commit -q -m u1 >/dev/null
V1=$(g "$CANON4" rev-parse HEAD)   # CANON4's local main tip
UP4="$D/upstream4"; git clone -q "$CANON4" "$UP4" >/dev/null 2>&1   # a second clone to advance as "the real remote"
printf 'shared-line-truetip\n' > "$UP4/shared.txt"; g "$UP4" add shared.txt >/dev/null; g "$UP4" commit -q -m u2 >/dev/null
V2=$(g "$UP4" rev-parse HEAD)
g "$CANON4" remote add origin "$UP4" >/dev/null 2>&1; g "$CANON4" fetch -q origin >/dev/null 2>&1
[ "$(g "$CANON4" rev-parse main)" = "$V1" ] || { echo "FIXTURE BROKEN: CANON4 main moved"; FAIL=$((FAIL+1)); }
[ "$(g "$CANON4" rev-parse origin/main)" = "$V2" ] || { echo "FIXTURE BROKEN: CANON4 origin/main did not fetch"; FAIL=$((FAIL+1)); }
WT4="$D/wt4"; mkwt "$CANON4" "$V0" "$WT4"
donemd "$WT4" "$V0"; stage_one "$WT4"
printf 'shared-line-worker\n' > "$WT4/shared.txt"; g "$WT4" add shared.txt >/dev/null   # conflicts with BOTH V1 and V2
CARRY4="$D/carry4"; mkcarry "$CARRY4"
OUT4=$(BD_GUARD_CANON="$CANON4" BD_CARRY="$CARRY4" bash "$GUARD" local "$WT4" 2>&1); RC4=$?
ok '4 E1 fix: host-lag + real conflict REFUSEs (not UNKNOWN)' "$RC4" 1
has '4 names STALE BASE' "$OUT4" "STALE BASE"
has '4 counts against the TRUE tip (2 commits, V0..V2), not local main (1, V0..V1)' "$OUT4" "BEHIND main $V2 by 2 commit(s)"

### FIXTURE 3 -- NEGATIVE CONTROL: CANON's main IS current (equal to every remote-tracking ref); must still OK.
CANON3="$D/canon3"; git clone -q "$UPSTREAM" "$CANON3" >/dev/null 2>&1; g "$CANON3" checkout -q main >/dev/null 2>&1
g "$CANON3" fetch -q origin >/dev/null 2>&1   # now CANON3 main == origin/main == U3, both current
WT3="$D/wt3"; mkwt "$CANON3" "$U3" "$WT3"
donemd "$WT3" "$U3"; stage_one "$WT3"
CARRY3="$D/carry3"; mkcarry "$CARRY3"
OUT3=$(BD_GUARD_CANON="$CANON3" BD_CARRY="$CARRY3" bash "$GUARD" local "$WT3" 2>&1); RC3=$?
ok '3 negative control: current base still passes rc' "$RC3" 0
has '3 negative control names OK' "$OUT3" "OK local"
has '3 negative control: genuinely current base still says current main (E3 not over-corrected)' "$OUT3" "declared base is current main"

rm -rf "$D"
echo "RESULT PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
