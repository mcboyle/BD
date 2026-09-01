#!/bin/bash
# AUTHENTICATED CAPTURE HOST. Operator authorized the real jar on a throwaway VM
# (2026-08-28): isolated, never deployed, no live service to share -- which is
# the "authorized and isolated" bar CLAUDE.md A6 sets for authenticated contact.
# The jar was removed from test4 when this moved here; a serving host is the
# wrong place for 21 member sessions.
set -uo pipefail
IP=${1:?usage: bd-capture-site-setup.sh <ip>}
A=/home/mboyle/fleet-run-artifacts/2026-08-25/inflight
L="$A/capture-site-setup.log"
say(){ printf '%s [capsetup] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$L"; }
while ! grep -qE "VERDICT: READY" "$A/freshhost-50/provision.log" 2>/dev/null; do sleep 60; done
grep -qE 'VERDICT: READY' "$A/freshhost-50/provision.log" 2>/dev/null \
  || { say "bd not READY -- not placing credentials on an unproven host"; exit 1; }
say "placing the jar on $IP (0600, outside the repo)"
scp -q -o BatchMode=yes "/home/mboyle/session cookies.txt" "mboyle@$IP:/home/mboyle/.bd-session-jar.txt"
ssh -n -o BatchMode=yes "$IP" 'chmod 600 ~/.bd-session-jar.txt; ls -la ~/.bd-session-jar.txt' >>"$L" 2>&1
# split the multi-array export into one BD cookie file per site. BD's on-disk
# format IS a JSON array of {domain,name,value,expirationDate,...}, so each line
# of the operator's export is already a valid jar -- no conversion, just routing.
ssh -n -o BatchMode=yes "$IP" 'mkdir -p ~/BulkDownloader/cookies && python3 - <<PY
import json, collections, pathlib, re
src = pathlib.Path.home() / ".bd-session-jar.txt"
out = pathlib.Path.home() / "BulkDownloader" / "cookies"
seen = {}
for line in src.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line: continue
    arr = json.loads(line)
    doms = collections.Counter(c.get("domain","") for c in arr if isinstance(c, dict))
    primary = max(doms, key=lambda d: (doms[d], -len(d)))
    reg = ".".join(primary.lstrip(".").split(".")[-2:])
    sid = re.sub(r"[^a-z0-9]+", "_", reg.lower())
    seen[sid] = arr          # later export of the same site wins
for sid, arr in seen.items():
    p = out / (sid + ".json")
    p.write_text(json.dumps(arr), encoding="utf-8")
    p.chmod(0o600)
print(f"wrote {len(seen)} jars")
for sid in sorted(seen): print("  ", sid, len(seen[sid]), "cookies")
PY' >>"$L" 2>&1
say "jars: $(ssh -n -o BatchMode=yes $IP 'ls ~/BulkDownloader/cookies/*.json 2>/dev/null | wc -l' 2>&1 | tail -1)"
say "=== CAPTURE SITE SETUP COMPLETE ==="
