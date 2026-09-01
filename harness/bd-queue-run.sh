#!/bin/bash
# Serial integrate -> verify -> ship for every QA-green Codex row, AFTER the
# trio lane finishes. One row at a time because every cut writes the release
# trio; the merge lane additionally keeps one PR in CI at a time.
# A row that fails is SKIPPED and recorded -- it does not block the others,
# because a queue that stops on its first bad row converts one problem into
# thirteen.
set -u
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
CC=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts
L=/home/mboyle/fleet-run-artifacts/2026-08-25/QUEUE.log
say(){ echo "$(date -u +%H:%M:%S) $*" | tee -a "$L"; }

# row:version:slug:title  -- 1241/1242/1243 are the trio, 1244 is row 228
SPECS="
175:3.66.1254:parallel-capture-services:capture gains an explicit parallel mode with per-run routing
"

say "=== waiting for the trio lane to finish ==="
for _ in $(seq 1 480); do tmux has-session -t bd-tail 2>/dev/null || break; sleep 30; done
tmux has-session -t bd-tail 2>/dev/null && { say "trio still running after 4h -- queue not starting"; exit 2; }
say "trio lane done. main=$(git -C /home/mboyle/BulkDownloader ls-remote origin main | cut -c1-7)"

OK=0; SKIP=0
# READ LINE BY LINE. `for spec in $SPECS` word-splits on whitespace, and every
# title contains spaces -- so "the release gate sees prose above the newest
# header" became eight bogus rows, each reported "QA not green -- SKIPPED".
# The loop must consume RECORDS, not words.
printf '%s\n' "$SPECS" | while IFS= read -r spec; do
  [ -z "${spec// /}" ] && continue
  IFS=':' read -r row ver slug title <<<"$spec"
  [ -z "${row:-}" ] && continue
  # ALREADY-MERGED GUARD. The second pass re-runs this same script with a longer
  # SPECS list, so without this it would re-integrate every row the first pass
  # already merged -- building duplicate cuts for closed rows. The register on
  # origin/main is the authority, not a local marker file.
  FIRST="${row%% *}"
  if git -C /home/mboyle/BulkDownloader show origin/main:project-knowledge/IMPROVEMENT_BACKLOG.md 2>/dev/null \
     | grep -qE "^\|[[:space:]]*$FIRST[[:space:]]*\|[[:space:]]*(CLOSED|FIXED|MOOT)"; then
    say "row $row: already closed on main -- skipping"; continue; fi
  for qr in $row; do
    grep -q 'QA_RC=0' "$CC/row$qr.qa.log" 2>/dev/null || { say "row $qr: QA not green -- SKIPPED"; SKIP=$((SKIP+1)); continue 2; }
  done
  say "--- row $row -> v$ver ---"
  if ! bash /home/mboyle/bd-integrate-row.sh "$row" "$ver" "$slug" "$title" >> "$L" 2>&1; then
    say "row $row: INTEGRATION FAILED -- SKIPPED"; SKIP=$((SKIP+1)); continue; fi
  # READ BACK THE VERSION THE INTEGRATOR ACTUALLY USED. It re-derives from main,
  # so the planned number in SPECS can be stale after an out-of-order retry.
  HS="$A/.integrated-${row%% *}"
  if [ -s "$HS" ]; then
    ver=$(sed -n 1p "$HS"); NW=$(sed -n 2p "$HS")
    say "integrated as v$ver at $NW"
  else
    NW=/home/mboyle/bd-cuts/cut/${ver##*.}-$slug
  fi
  # BAND WIDTH IS A CHOICE ABOUT THE BOX, NOT ABOUT THE CUT -- except where the
  # cut's subject IS scheduling. 221 and 241 are load-behaviour rows, so they
  # keep the -n 12 shape their evidence was gathered at; everything else runs
  # -n 24 on a box whose load has dropped from 17 to 3 now the codex fleet is done.
  # 175 JOINS THE -n 12 LIST FOR A DIFFERENT REASON THAN 221/241. Those two are
  # load-behaviour rows whose SUBJECT is scheduling. 175's band is 699 files and
  # includes the Playwright e2e set, which drives a real browser: at -n 24 it
  # loses its timing and fails 11, at -n 12 the same tree passes 9822/0. The
  # width is a property of what the band CONTAINS, not of the cut's quality.
  case " $row " in *" 221 "*|*" 241 "*|*" 175 "*) BW=12;; *) BW=24;; esac
  # A cut whose subject IS scheduling gets the strict sequential shape too.
  case " $row " in *" 221 "*|*" 241 "*) SEQ=1;; *) SEQ=0;; esac
  TAG="${ver##*.}-r${row// /_}"
  SEQUENTIAL=$SEQ BAND_WORKERS=$BW bash /home/mboyle/bd-verify-cut.sh "$NW" "$TAG" > "$A/$TAG-verify.log" 2>&1
  grep -E '^(PRECUT_RC|PREPUSH_RC|BAND_FILES|BAND_RC|VERDICT)' "$A/$TAG-verify.log" | tee -a "$L"
  if ! grep -q 'ALL GREEN -- shippable' "$A/$TAG-verify.log"; then
    # A BAND FAILURE CONFINED TO THE KNOWN SCHEDULE-SENSITIVE W1 FAMILY IS
    # RETRIED ONCE, ALONE. Those tests exist on main and pass there; under a
    # loaded band a different member fails each run, and in isolation they pass
    # (measured: 8 passed, twice, on the node that had just failed). A single
    # retry distinguishes "flaky under load" from "broken" without weakening
    # anything -- any OTHER failing name still refuses immediately.
    OTHER=$(grep -hE '^FAILED' "$A/$TAG-band.log" 2>/dev/null \
            | grep -vc 'test_v3_66_1132_the_hunt_reaps' || true)
    SEEN=$(grep -hcE '^FAILED' "$A/$TAG-band.log" 2>/dev/null || echo 0)
    if [ "${SEEN:-0}" -gt 0 ] && [ "${OTHER:-1}" -eq 0 ]; then
      say "row $row: band failed only in the W1 family -- retrying that band once, alone"
      SEQUENTIAL=1 BAND_WORKERS=12 bash /home/mboyle/bd-verify-cut.sh "$NW" "$TAG-retry" \
        > "$A/$TAG-retry-verify.log" 2>&1
      grep -E '^(BAND_RC|VERDICT)' "$A/$TAG-retry-verify.log" | tee -a "$L"
      if ! grep -q 'ALL GREEN -- shippable' "$A/$TAG-retry-verify.log"; then
        say "row $row: NOT SHIPPABLE after retry -- SKIPPED"; echo skipped >> "$A/.queue-tally"; continue; fi
      say "row $row: retry clean -- W1 flake recorded on row 241"
    else
      say "row $row: NOT SHIPPABLE -- SKIPPED, nothing pushed"; echo skipped >> "$A/.queue-tally"; continue; fi
  fi
  if CHECK_FLOOR=20 /home/mboyle/bd-merge-lane.sh /home/mboyle/bd-ship.sh \
       "cut/${ver##*.}-$slug" "v$ver $title" "$A/pr-body-${row%% *}.md" >> "$L" 2>&1; then
    say "ROW $row MERGED (v$ver)"; echo merged >> "$A/.queue-tally"
  else say "row $row: SHIP FAILED -- PR left open"; echo skipped >> "$A/.queue-tally"; fi
done
say "=== queue complete: $(grep -c merged "$A/.queue-tally" 2>/dev/null || echo 0) merged, $(grep -c skipped "$A/.queue-tally" 2>/dev/null || echo 0) skipped ==="
