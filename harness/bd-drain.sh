#!/bin/bash
# NO-HALT DRAIN. Never stops the batch for one bad cut: a failure is SKIPPED and
# recorded, the rest keep going. bd-fanout-lane/bd-serial-lane both abort the whole
# queue on the first refusal, which cost four relaunches tonight on four unrelated
# harness bugs.
set -uo pipefail
A=/home/mboyle/fleet-run-artifacts/2026-08-25; L="$A/FINISH.log"; R=/home/mboyle/BulkDownloader
say(){ printf '%s [drain] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
export KEEP_OPEN_ROWS="243,245,285"
ok=0; skip=0
for spec in "$@"; do
  IFS='|' read -r ROW SLUG TITLE <<<"$spec"
  V=$(git -C "$R" show origin/main:bulk_downloader/__init__.py 2>/dev/null | grep -oE '3\.66\.[0-9]+' | head -1 | cut -d. -f3)
  say "--- $ROW -> v3.66.$((V+1)) (main $V) ---"
  # ROW COHERENCE, AT THE REAL CHOKEPOINT (2026-08-29). The same check was added
  # to bd-night.sh's selection loop and NEVER RAN: bd-night does not launch a
  # chain, THIS FILE DOES, and row 243 walked straight past it to PR #634 with
  # SEVEN duplicate _EXPECTED_DECLARED_GATE_COUNT assignments -- the last one
  # wins, so merging it would have dropped the declared-gate denominator. The
  # empty audit log was the tell: the gate had never written one. A gate placed
  # where the subject does not pass is not a gate (CLAUDE.md A7).
  # A BATCH IS SEVERAL ROWS, AND bd-row-audit READS ROWS FROM SEPARATE ARGV
  # ENTRIES. Quoting "$ROW" made a 4-row batch ONE row id naming no worktree,
  # which audits as UNKNOWN -- a failing state -- so the whole batch was
  # skipped and batch-cap could never rise above 1. Split on whitespace and
  # pass each row as its own argument; a single-row batch is the same call it
  # always was. The log name joins the rows with '-' so a batch cannot write a
  # path containing a space.
  read -r -a ROW_ARGS <<<"$ROW"
  ROW_TAG=$(printf '%s' "${ROW_ARGS[*]}" | tr ' ' '-')
  if [ "${#ROW_ARGS[@]}" -eq 0 ]; then
    skip=$((skip+1)); say "SKIP empty row spec -- UNKNOWN, not OK"; continue
  fi
  if ! python3 /home/mboyle/bd-row-audit.py "${ROW_ARGS[@]}" \
       > "$A/codex-cuts/row$ROW_TAG.audit.log" 2>&1; then
    skip=$((skip+1))
    say "SKIP $ROW -- REFUSED by bd-row-audit, see $A/codex-cuts/row$ROW_TAG.audit.log"
    sed -n '1,12p' "$A/codex-cuts/row$ROW_TAG.audit.log" | tee -a "$L"
    continue
  fi
  if bash /home/mboyle/bd-row-chain.sh "$ROW" "$((V+1))" "$SLUG" "$TITLE" >>"$L" 2>&1; then
    ok=$((ok+1)); say "OK $ROW merged"
  else
    skip=$((skip+1)); say "SKIP $ROW (see $A/inflight/chain-$ROW.log) -- CONTINUING"
    # STOP THE PASS ON A VERSION-CLAIM REFUSAL. It is not this row's failure and
    # it will refuse EVERY remaining row identically -- walking the rest costs
    # one second each, burns an attempt on each, and floods the log with
    # twenty-three identical skips. Observed four times on 2026-08-27.
    # Break instead; bd-night prunes the stale claim and re-enters.
    if tail -n 8 "$A/inflight/integrate-$ROW.log" 2>/dev/null \
       | grep -qiE 'already claimed|claimed on the remote'; then
      say "version claim held elsewhere -- ending this pass early rather than refusing $(( $# - ok - skip )) more row(s) for the same reason"
      break
    fi
  fi
  git -C "$R" fetch origin main -q 2>/dev/null
done
say "DRAIN COMPLETE: $ok merged, $skip skipped"
