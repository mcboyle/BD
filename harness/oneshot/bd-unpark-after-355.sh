#!/bin/bash
# Unpark the five rows the moment 355 merges. 1187/1190 fail in ANY wide band
# until 355 lands, so shipping them earlier just burns ~11min verify cycles.
set -u
L=/home/mboyle/fleet-run-artifacts/2026-08-25/FINISH.log
N=$(wc -l < "$L")
while :; do
  tail -n +$((N+1)) "$L" | grep -qE '\[chain [0-9 ]*355[0-9 ]*\] MERGED' && break
  sleep 30
done
/home/mboyle/BulkDownloader/venv/bin/python - <<'PY'
import pathlib
p=pathlib.Path("/home/mboyle/bd-night-spec.txt"); s=p.read_text()
tag="# HELD until 355 merges: 1187/1190 fail in ANY wide band until then\n# "
for l in ("339|walls-need-real-headroom","344|capture-prune-proves-its-target",
          "347|provider-band-gate-catches-its-mutant","353|gate-count-mutant-anchor-is-stable",
          "354|capture-verdict-separates-an-inapplicable-pin"):
    s=s.replace(tag+l, l, 1)
p.write_text(s)
print("UNPARKED 339 344 347 353 354")
PY
for r in 339 344 347 353 354; do echo 0 > /home/mboyle/fleet-run-artifacts/2026-08-25/night/att-$r; done
echo "$(date -u +%H:%M:%S) [unpark] five rows released after 355" >> "$L"
