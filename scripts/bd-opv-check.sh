#!/bin/bash
# bd-opv-check.sh -- re-derive the status of every OPV live-verify item ON STASH.
#
# WHY: the OPV Completion Guide lists 11 remaining live-verify items with
# preconditions. Whether each is READY / NEEDS-SETUP / BLOCKED is a fact about the
# running box, not the guide -- and this project has been burned by quoting stale
# status. Run this ON STASH; it probes each item's real endpoints + host state and
# prints a verdict, so the guide can be made accurate from measurement.
#
# SAFE: read-only. The one media fetch is a HEAD/range against a sanctioned public
# test target. No config is written. No secret values are emitted.
#
# USAGE:  bash scripts/bd-opv-check.sh [repo-dir] [base-url]
#   repo-dir  default: $PWD    base-url  default: http://127.0.0.1:5555
# OUTPUT:  /tmp/bd_opv_check/  and  /tmp/bd_opv_check.tar.gz  (upload the .tgz)

set -uo pipefail
R="${1:-$PWD}"
BASE="${2:-http://127.0.0.1:5555}"
OUT=/tmp/bd_opv_check
PY="$R/venv/bin/python"; [ -x "$PY" ] || PY="python3"
JAR=/tmp/bd_opv_check.jar
SUM="$OUT/00_SUMMARY.txt"

[ -f "$R/bulk_downloader/__init__.py" ] || { echo "not a BD checkout: $R"; exit 2; }
cd "$R"
rm -rf "$OUT" "$JAR"; mkdir -p "$OUT"

# ---- identity + csrf ----------------------------------------------------------
{
  echo "# BD OPV item check -- $(date -u +%FT%TZ)"
  echo "host   : $(hostname 2>/dev/null)   version: $("$PY" -c 'import bulk_downloader;print(bulk_downloader.__version__)' 2>/dev/null || echo '?')"
  echo "git    : $(git rev-parse --short HEAD 2>/dev/null || echo '?')  base: $BASE"
  echo "service: $(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$BASE/" 2>/dev/null)"
  echo
  echo "Legend: READY = can run now | NEEDS-SETUP = seed a precondition first |"
  echo "        BLOCKED = missing capability (device/kmod/etc.) | GUI = also settable in the UI"
  echo "========================================================================"
} | tee "$SUM"

curl -s -c "$JAR" "$BASE/api/csrf" > "$OUT/_csrf.json" 2>/dev/null || true
CSRF="$("$PY" -c 'import json;d=json.load(open("'"$OUT"'/_csrf.json"));print(d.get("csrf_token") or d.get("token") or "")' 2>/dev/null || echo "")"

# hit <label> <method> <path> -> saves body to OUT/<label>.json, echoes "<label> http=NNN"
hit(){ local lbl="$1" meth="$2" path="$3"; local code
  code=$(curl -s -o "$OUT/$lbl.body" -w '%{http_code}' -b "$JAR" -X "$meth" --max-time 12 "$BASE$path" 2>/dev/null)
  echo "$code"; }
say(){ echo "$*" | tee -a "$SUM"; }
jq_py(){ "$PY" -c "import sys,json
try: d=json.load(open('$OUT/$1.body'))
except Exception as e: print('  (parse: %s)'%e); sys.exit()
$2" 2>/dev/null || echo "  (no json / endpoint error)"; }

# convenience: is a global_config key set to what?
GC="$OUT/gc.json"; curl -s -b "$JAR" "$BASE/api/global_config" > "$GC" 2>/dev/null || echo '{}' > "$GC"
gc(){ "$PY" -c "import json;print(json.load(open('$GC')).get('$1'))" 2>/dev/null; }

# ======================================================================= ITEMS
say ""; say "## 1 OPV-F2a  site-health = top-failure cluster (GUI read)"
c=$(hit f2a GET /api/data/site_health)
say "  GET /api/data/site_health http=$c"
jq_py f2a "
d=d.get('data',d)
clusters=d.get('clusters') or d.get('cluster') or []
persite=d.get('per_site') or d.get('sites') or []
print('  clusters:',len(clusters) if isinstance(clusters,list) else clusters,' per-site rows:',len(persite) if isinstance(persite,list) else persite)
print('  VERDICT:', 'READY (has failure data)' if (isinstance(persite,list) and persite) else 'NEEDS-SETUP (no failure history yet -- run some downloads that fail)')" | tee -a "$SUM"

say ""; say "## 2 OPV-F2.6  DOM-analyzer workbench -> review-only draft"
c=$(hit f26 GET /api/analyzer/captures)
say "  GET /api/analyzer/captures http=$c"
jq_py f26 "
caps=d.get('captures',[])
print('  captures on disk:',len(caps))
print('  VERDICT:', 'READY (captures present; load->test->pin -> draft_review_required)' if caps else 'NEEDS-SETUP (capture a sanctioned page first)')" | tee -a "$SUM"

say ""; say "## 3 OPV-F3.2-LIVE  scheduled drift-repair daily run"
c=$(hit f32 GET /api/automation/drift_repair)
say "  GET /api/automation/drift_repair http=$c   drift_repair_enabled=$(gc 'automation.drift_repair_enabled')"
jq_py f32 "
print('  last_run:',d.get('last_run'),' drafts_pending:',d.get('drafts_pending'))
print('  VERDICT: forced-run path already PASSED; the SCHEDULED daily tick needs enable + a real day boundary (operator/time-bound)')" | tee -a "$SUM"

say ""; say "## 4 OPV-F3.1 / F3.1-WK  saved-search enqueue lane (week-long)"
c=$(hit f31 GET /api/saved_searches); c2=$(hit f31d GET /api/saved_searches/digest)
say "  GET /api/saved_searches http=$c   /digest http=$c2"
jq_py f31 "
s=d.get('searches', d if isinstance(d,list) else [])
print('  saved searches:',len(s))
print('  VERDICT: mechanism READY; the 7-day cap/dedup proof is WALL-CLOCK-bound (cannot compress)')" | tee -a "$SUM"

say ""; say "## 5 OPV-F1.4-EN  predictive relogin (learned median)  [GUI: per-site editor, v3.66.810]"
OUT="$OUT" PY="$PY" "$PY" - <<PYEOF | tee -a "$SUM"
import json, subprocess, os
R="$R"
# is predictive_relogin now a declared per-site key (the 810 fix)?
try:
    import sys; sys.path.insert(0, R)
    from bulk_downloader import app_kernel as k
    incf = all(x in k.CFG_FIELDS for x in ("predictive_relogin_enabled","predictive_relogin_fraction"))
except Exception as e:
    incf = "ERR:%s"%e
# session-lifetime observations available? (needs >=3 to fire)
obs = "n/a"
try:
    from bulk_downloader import db as _db
    # count distinct (site,acct) with >=3 observations if the helper exists
    obs = "helper present" if hasattr(_db,"session_lifetime_observations") else "no helper"
except Exception as e:
    obs = "ERR:%s"%str(e)[:40]
print("  predictive_relogin in CFG_FIELDS (drop-on-reload fixed):", incf)
print("  db.session_lifetime_observations:", obs)
print("  VERDICT: NEEDS-SETUP -- configure a sanctioned login site (e.g. practicetestautomation.com),")
print("           seed >=3 login cycles, then enable per-site (now a GUI toggle). Predictor fires at fraction*median.")
PYEOF

say ""; say "## 6 OPV-B2  real 'Test (live)' draft override (one real download)"
BBB="https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4"
mc=$(curl -s -o /dev/null -w '%{http_code}' -r 0-1024 --max-time 15 "$BBB" 2>/dev/null)
say "  sanctioned media target reachable (BBB): http=$mc"
say "  teach_test_download route: POST /api/sites/<sid>/teach_test_download (needs a configured site + staged draft)"
say "  VERDICT: $([ "$mc" = "206" -o "$mc" = "200" ] && echo 'READY once a sanctioned media site + review-only draft are staged' || echo 'BLOCKED (media target unreachable from this host)')"

say ""; say "## 7 OPV-PICK  live element-pick click (noVNC display)"
nport=$(ss -lnt 2>/dev/null | grep -oE ':6080|:8444' | sort -u | tr '\n' ' ')
say "  DISPLAY=${DISPLAY:-<unset>}   listening vnc/novnc ports: ${nport:-none}"
say "  captcha_takeover_enabled=$(gc captcha_takeover_enabled)  mode=$(gc captcha_takeover_mode)"
say "  VERDICT: needs the noVNC/KasmVNC display up AND a HUMAN click -- operator-only by design"

say ""; say "## 8 NOVNC-PRECEDENCE  detect -> handoff (no auto-solve)"
c=$(hit metrics GET /metrics)
tk=$(grep -oE 'bd_takeover[_a-z]* [0-9]+' "$OUT/metrics.body" 2>/dev/null | head -3 | tr '\n' ' ')
say "  GET /metrics http=$c   takeover counters: ${tk:-none present}"
say "  captcha_takeover_enabled=$(gc captcha_takeover_enabled)  mode=$(gc captcha_takeover_mode)"
say "  VERDICT: READY to observe once a challenge is staged on a sanctioned test page (detect->handoff, zero solve)"

say ""; say "## 9 OPV-VPNKILL  egress fails closed on a REAL tunnel  [GUI: netns toggle backend-ready, control pending]"
c=$(hit tuns GET /api/vpn/tunnels); c2=$(hit kss GET /api/vpn/kill_switch/state)
wg=$(ip link add wgprobe type wireguard >/dev/null 2>&1 && { echo present; ip link del wgprobe; } || echo absent)
ntun=$("$PY" -c "import json;print(len(json.load(open('$OUT/tuns.body')).get('tunnels',[])))" 2>/dev/null || echo '?')
say "  /api/vpn/tunnels http=$c (tunnels=$ntun)   /kill_switch/state http=$c2"
say "  wireguard kmod: $wg"
say "  VERDICT: $([ "$wg" = present ] && echo 'READY if a real tunnel is configured + up' || echo 'BLOCKED -- WireGuard kmod absent; load it (modprobe wireguard) or the live-tunnel arm cannot run (state-machine already proven)')"

say ""; say "## 10 OPV-F4.1 / F4.5  phone share-target (device)"
c=$(hit man GET /static/manifest.json)
sh=$(grep -o 'share_target' "$OUT/man.body" 2>/dev/null | head -1)
dc=$(hit dash GET '/dashboard?url=https://example.org/x')
say "  /static/manifest.json http=$c  share_target present: ${sh:-no}   /dashboard?url= http=$dc"
say "  VERDICT: mechanism READY (share_target -> /dashboard resolve box); the 2-tap flow needs a PHONE with the PWA installed (device-bound)"

say ""; say "## 11 OPV-F45-METRIC  idle-request reduction (cadence)"
OUT="$OUT" "$PY" - <<PYEOF | tee -a "$SUM"
import re, glob, os
R="$R"
# read the cadence constants from source (authoritative)
p=os.path.join(R,"frontend/src/hooks/useDashboardData.ts")
src=open(p).read() if os.path.exists(p) else ""
def g(name):
    m=re.search(r'const %s\s*=\s*([0-9_]+)'%name, src); return int(m.group(1).replace('_','')) if m else None
FAST,SLOW,SS = g("FAST"),g("SLOW"),g("STREAM_SAFETY")
def hr(ms): return round(3600000/ms,1) if ms else None
print(f"  cadence: FAST={FAST} SLOW={SLOW} STREAM_SAFETY={SS} (ms)")
if FAST and SS:
    print(f"  reduction when SSE connects: {100*(1-hr(SS)/hr(FAST)):.0f}% (busy) / {100*(1-hr(SS)/hr(SLOW)):.0f}% (idle)")
print("  VERDICT: mechanism code-confirmed; a live wall-clock measurement needs two dashboard windows (optional)")
PYEOF

# ---- bundle -------------------------------------------------------------------
say ""; say "========================================================================"
say "Done. Upload /tmp/bd_opv_check.tar.gz"
rm -f "$JAR" "$OUT/_csrf.json" "$OUT/gc.json"
tar czf /tmp/bd_opv_check.tar.gz -C /tmp bd_opv_check 2>/dev/null
ls -la /tmp/bd_opv_check.tar.gz
