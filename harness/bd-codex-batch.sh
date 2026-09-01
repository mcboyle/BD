#!/bin/bash
# Wait for the timing-sensitive 1241 band to finish, then dispatch the Codex
# batch in parallel. Overnight the agent cap was 2 because concurrent test runs
# contaminated timing measurements; 1241's band contains the wedge-hunt family,
# which is the one band in this queue where that is a live risk. 183/184/242 are
# gate work with no timing claim, so once 1241 is done they can all run at once.
set -u
V=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight/1241-rebased-r2-verify.log
D=/home/mboyle/fleet-run-artifacts/2026-08-25/codex-cuts
mkdir -p "$D"
echo "waiting for 1241 band to clear..."
for _ in $(seq 1 360); do grep -q '^VERDICT 1241-rebased-r2' "$V" 2>/dev/null && break; sleep 20; done
grep -q '^VERDICT 1241-rebased-r2' "$V" 2>/dev/null \
  && echo "1241 verify done: $(grep '^VERDICT 1241-rebased-r2' "$V")" \
  || echo "1241 verify did NOT finish within 2h -- dispatching anyway (rows are independent)"
date -u +'dispatch start %Y-%m-%dT%H:%M:%SZ'
for r in 184 183 242; do
  nohup bash /home/mboyle/bd-codex-cut.sh "$r" "/home/mboyle/bd-codex-briefs/row$r.md" \
    > "$D/row$r.dispatch.log" 2>&1 &
  echo "  dispatched row $r (pid $!)"
  sleep 5
done
wait
date -u +'dispatch end %Y-%m-%dT%H:%M:%SZ'
for r in 184 183 242; do
  echo "== row $r tail =="; tail -15 "$D/row$r.txt" 2>/dev/null || echo "  (no output)"
done
