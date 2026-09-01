#!/usr/bin/env bash
# Measure one host's WAN capacity properly, and say what the number means.
#
# A single-stream CDN pull is NOT a capacity test on this network: the gateway
# load-balances two WANs (70/30), and one TCP flow pins to one uplink, so the
# single-stream number can never exceed the faster single link no matter how
# much aggregate bandwidth exists. Measure BOTH, and report them separately --
# 1-stream tells you per-flow ceiling (which is what a downloader actually
# gets on one file), N-stream tells you the pipe.
#
# Runs against ONE host. The caller serialises hosts; two hosts measuring at
# once would share the same WAN and each would report the other's contention
# as its own limit.
set -uo pipefail
HOST="$1"; STREAMS="${2:-8}"
URL="${3:-https://cachefly.cachefly.net/100mb.test}"

ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" "bash -s" <<REMOTE
set -uo pipefail
H=\$(hostname)
N=\$(ip -br link | awk '\$2=="UP"{print \$1; exit}')
LINK=\$(ethtool "\$N" 2>/dev/null | awk '/Speed:/{print \$2}')
MTU=\$(ip link show "\$N" | awk '{for(i=1;i<=NF;i++) if(\$i=="mtu") print \$(i+1)}')
CC=\$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null)
RMEM=\$(sysctl -n net.core.rmem_max 2>/dev/null)
QD=\$(ip link show "\$N" | grep -o 'qdisc [a-z_]*' | awk '{print \$2}')

# 1 stream
S1=\$(curl -o /dev/null -s -w '%{speed_download}' --max-time 25 "$URL" 2>/dev/null || echo 0)

# N streams, started together, wall-clock over the whole set. Each writes its
# own byte count; summing curl's per-process speed would over-report, because
# the processes do not all run for the same duration.
T0=\$(date +%s.%N)
for i in \$(seq 1 $STREAMS); do
  curl -o /dev/null -s -w '%{size_download}\n' --max-time 25 "$URL" >> /tmp/.bdnet.\$\$ 2>/dev/null &
done
wait
T1=\$(date +%s.%N)
BYTES=\$(awk '{s+=\$1} END{print s+0}' /tmp/.bdnet.\$\$ 2>/dev/null)
rm -f /tmp/.bdnet.\$\$
SN=\$(awk -v b="\$BYTES" -v t0="\$T0" -v t1="\$T1" 'BEGIN{d=t1-t0; printf "%.0f", (d>0? b/d : 0)}')

printf '%-7s nic=%-9s mtu=%-5s cc=%-8s rmem=%-9s qdisc=%-6s  1-stream=%6.1f MB/s  %d-stream=%6.1f MB/s  ratio=%.1fx\n' \
  "\$H" "\$LINK" "\$MTU" "\$CC" "\$RMEM" "\$QD" \
  \$(awk -v s="\$S1" 'BEGIN{printf "%.1f", s/1e6}') \
  $STREAMS \
  \$(awk -v s="\$SN" 'BEGIN{printf "%.1f", s/1e6}') \
  \$(awk -v a="\$S1" -v b="\$SN" 'BEGIN{printf "%.1f", (a>0? b/a : 0)}')
REMOTE
