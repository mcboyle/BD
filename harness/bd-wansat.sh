#!/usr/bin/env bash
# Saturate the WAN from the whole fleet at once and report aggregate throughput.
#
# One host with one stream measures a per-flow ceiling; this measures the PIPE.
# Every host starts its streams inside the same window and each stream loops for
# the full duration, so the number is bytes-actually-delivered over wall-clock,
# not a sum of per-process curl estimates (those over-report, because the
# processes do not all run for the same length of time).
#
# Endpoints are deliberately MIXED: a single CDN can cap or shape us and we
# would record its policy as our capacity.
set -uo pipefail
DUR="${DUR:-30}"; STREAMS="${STREAMS:-8}"
HOSTS="${HOSTS:-10.0.70.249 10.0.70.51 10.0.70.50 10.0.70.52 10.0.70.53 10.0.70.54}"
OUT=$(mktemp -d)

runner() {   # $1 = host label or LOCAL
  cat <<'INNER'
set -uo pipefail
D=${DUR}; S=${STREAMS}
URLS=(
  "https://cachefly.cachefly.net/100mb.test"
  "https://speed.cloudflare.com/__down?bytes=524288000"
  "https://cachefly.cachefly.net/100mb.test"
  "https://speed.cloudflare.com/__down?bytes=524288000"
)
W=$(mktemp -d)
T0=$(date +%s.%N)
for i in $(seq 1 $S); do
  ( U=${URLS[$(( (i-1) % ${#URLS[@]} ))]}
    END=$(( $(date +%s) + D ))
    TOT=0
    while [ "$(date +%s)" -lt "$END" ]; do
      B=$(curl -o /dev/null -s --max-time $D -w '%{size_download}' "$U" 2>/dev/null || echo 0)
      TOT=$((TOT + B))
    done
    echo "$TOT" > "$W/$i" ) &
done
wait
T1=$(date +%s.%N)
B=$(cat "$W"/* 2>/dev/null | awk '{s+=$1} END{print s+0}')
rm -rf "$W"
awk -v h="$(hostname)" -v b="$B" -v t0="$T0" -v t1="$T1" \
  'BEGIN{d=t1-t0; printf "%s %.0f %.2f\n", h, b, d}'
INNER
}

echo "saturating for ${DUR}s with ${STREAMS} streams/host across: $HOSTS + test5(local)"
for h in $HOSTS; do
  ( runner | sed "s/\${DUR}/$DUR/; s/\${STREAMS}/$STREAMS/" \
      | ssh -o BatchMode=yes -o ConnectTimeout=8 "$h" "bash -s" > "$OUT/$h" 2>/dev/null ) &
done
( runner | sed "s/\${DUR}/$DUR/; s/\${STREAMS}/$STREAMS/" | bash > "$OUT/local" 2>/dev/null ) &
wait

printf '\n%-10s %14s %8s %12s\n' HOST BYTES SECS "MB/s"
TOTAL=0
for f in "$OUT"/*; do
  read -r H B D < "$f" 2>/dev/null || continue
  [ -z "${B:-}" ] && continue
  printf '%-10s %14s %8s %12.1f\n' "$H" "$B" "$D" "$(awk -v b="$B" -v d="$D" 'BEGIN{printf "%.1f", (d>0?b/d/1e6:0)}')"
  TOTAL=$(awk -v t="$TOTAL" -v b="$B" -v d="$D" 'BEGIN{printf "%.1f", t + (d>0?b/d/1e6:0)}')
done
printf '%-10s %14s %8s %12s\n' "" "" "" "-----"
printf '%-10s %14s %8s %12.1f  = %.2f Gbit/s\n' "AGGREGATE" "" "" "$TOTAL" \
  "$(awk -v t="$TOTAL" 'BEGIN{printf "%.2f", t*8/1000}')"
rm -rf "$OUT"
