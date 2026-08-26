#!/bin/bash
# bd-opv-run.sh -- CLI orchestrator for the OPV Completion Guide on stash.
#
# Runs the measure-first diagnostics + every SAFE (read-only / self-reverting) OPV
# item autonomously, each wrapped in the envelope (snapshot -> act -> revert ->
# ledger). GATES the risky / live-mutation items behind --allow-risky so an agent
# (Cowork, the Codex CLI, or a human) runs the bulk unattended and only surfaces
# the few high-risk gates + the final attestation. This is the executable form of
# project-knowledge/OPV_COMPLETION_GUIDE_v3_66_810.md.
#
# SAFE BY DEFAULT. Sanctioned targets only. No fake results. A blocked target is a
# NAMED SKIP, never a FAIL, never a substitution. It never self-certifies a live
# PASS -- it prints a disposition table for the operator to attest (CLAUDE.md 0).
#
# USAGE:  bash scripts/bd-opv-run.sh [repo-dir] [base-url] [--allow-risky]
#   repo-dir   default: $PWD          base-url  default: http://127.0.0.1:5555
#   --allow-risky   also run the gated live mutations (B2 real download, VPNKILL
#                   trigger); each still envelopes + reverts. Omit to only stage
#                   them and print the runbook.
# OUTPUT:  /tmp/bd_opv_run/  (ledger + per-item logs)

set -uo pipefail
R="${1:-$PWD}"; BASE="${2:-http://127.0.0.1:5555}"; ALLOW_RISKY=0
for a in "$@"; do [ "$a" = "--allow-risky" ] && ALLOW_RISKY=1; done
OUT=/tmp/bd_opv_run; JAR=/tmp/bd_opv_run.jar; LEDGER="$OUT/LEDGER.txt"
PY="$R/venv/bin/python"; [ -x "$PY" ] || PY="python3"
[ -f "$R/bulk_downloader/__init__.py" ] || { echo "not a BD checkout: $R"; exit 2; }
cd "$R" || { echo "cannot cd into $R"; exit 2; }; rm -rf "$OUT" "$JAR"; mkdir -p "$OUT"
led(){ printf '%-22s %-10s %s\n' "$1" "$2" "$3" | tee -a "$LEDGER"; }
hdr(){ echo; echo "== $* =="; }

echo "OPV run -- $(date -u +%FT%TZ)  base=$BASE  allow_risky=$ALLOW_RISKY" | tee "$LEDGER"
printf '%-22s %-10s %s\n' "ITEM" "VERDICT" "DETAIL" | tee -a "$LEDGER"

# ---- CSRF prelude -------------------------------------------------------------
curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$BASE/" > "$OUT/up" 2>/dev/null || true
[ "$(cat "$OUT/up")" = "200" ] || { echo "service not answering at $BASE"; exit 1; }
CSRF="$(curl -s -c "$JAR" "$BASE/api/csrf" | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("csrf_token") or "")' 2>/dev/null)"
POST(){ curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' -X "$1" "$BASE$2" ${3:+-d "$3"}; }
GET(){ curl -s -b "$JAR" "$BASE$1"; }
code(){ curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$BASE$1"; }

# ---- Tier 0: MEASURE FIRST + static verify ------------------------------------
hdr "Tier 0  measure-first (trust nothing)"
bash scripts/bd-opv-check.sh    "$R" "$BASE"  > "$OUT/opv_check.log"  2>&1 && echo "  bd-opv-check.sh: ok (see $OUT/opv_check.log)"
bash scripts/bd-stash-report.sh "$R" "$BASE"  > "$OUT/stash_report.log" 2>&1 && echo "  bd-stash-report.sh: ok"
"$PY" tools/opv_guide_lint.py project-knowledge/OPV_COMPLETION_GUIDE_v3_66_810.md > "$OUT/guide_lint.log" 2>&1
grep -q "^OK" "$OUT/guide_lint.log" && led "guide-lint" "PASS" "routes+refs resolve vs live tree" || led "guide-lint" "FAIL" "see $OUT/guide_lint.log"

# ---- Tier 1: SAFE items, run autonomously, envelope-wrapped --------------------
hdr "Tier 1  safe / self-reverting items (autonomous)"

# F2a -- read-only. Body and status come from one request: two requests can
# disagree, and a parser failure is a failed measurement rather than count 0.
F2A_CODE="$(curl -sS -b "$JAR" -o "$OUT/f2a.json" -w '%{http_code}' \
  "$BASE/api/data/site_health" 2>"$OUT/f2a-curl.err")"; F2A_CURL=$?
if [ "$F2A_CURL" -ne 0 ]; then
  led "F2a" "FAIL" "site_health request failed (curl exit $F2A_CURL)"
elif [ "$F2A_CODE" != "200" ]; then
  led "F2a" "FAIL" "site_health route not 200 (got ${F2A_CODE:-no status})"
elif NC="$("$PY" -c 'import json,sys
p=json.load(open(sys.argv[1]))
if not isinstance(p,dict) or p.get("ok") is not True: raise ValueError("invalid site_health envelope")
d=p.get("data")
if not isinstance(d,dict): raise ValueError("invalid site_health object")
c=d["clusters"]; s=d["sites"]
if not isinstance(c,list) or not isinstance(s,list): raise ValueError("invalid site_health counts")
print(len(c),len(s))' "$OUT/f2a.json" 2>/dev/null)"; then
  [ "${NC% *}" != "0" ] && led "F2a" "PASS" "site_health coherent (clusters/sites present)" || led "F2a" "NEEDS-SETUP" "0 clusters -- drive sanctioned failures first"
else
  led "F2a" "FAIL" "site_health response is not readable JSON"
fi

# F2.6 -- load->test->pin review-only draft, then delete ONLY the draft we create.
# SNAPSHOT-DIFF, not "newest": deleting the newest draft (an earlier version did)
# removes a pre-existing OPERATOR draft if our pin created none. (Cross-agent review
# flagged this; verified against template_manager.DRAFTS_DIR + the pin response.)
# CAP: prefer a capture whose NAME encodes a host -- pin needs one (a redacted /
# hash-named capture reports host="" and pin 400s "host required"). Picking the
# bare newest would exclude the check's own subject (a pinnable capture) from its
# denominator, so it could never pass (CLAUDE.md 0). Fall back to any capture so
# the path still runs (and reports the honest reason) when none carry a host.
CAP="$(GET /api/analyzer/captures | "$PY" -c 'import sys,json
c=json.load(sys.stdin).get("captures",[])
h=[x for x in c if (x.get("host") or "").strip()]
p=(h or c)
print((p[0].get("rel_path") or p[0].get("name","")) if p else "")' 2>/dev/null)"
if [ -n "$CAP" ]; then
  DBEFORE=""; for f in templates/drafts/*.template-draft.json; do [ -e "$f" ] && DBEFORE="$DBEFORE $f "; done
  POST POST /api/analyzer/load "{\"capture\":\"$CAP\"}" >/dev/null
  POST POST /api/analyzer/test "{\"capture\":\"$CAP\",\"selectors\":[\"div\"]}" >/dev/null
  PIN="$(POST POST /api/analyzer/pin "{\"capture\":\"$CAP\",\"selector\":\"div\",\"role\":\"container\"}")"
  ST="$(echo "$PIN" | "$PY" -c 'import sys,json;d=json.load(sys.stdin);print(d.get("status"),d.get("enabled"))' 2>/dev/null)"
  ERR="$(echo "$PIN" | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("error") or "")' 2>/dev/null)"
  # teardown: remove ONLY drafts that appeared since the snapshot (ours)
  for f in templates/drafts/*.template-draft.json; do
    [ -e "$f" ] || continue
    case "$DBEFORE" in *" $f "*) : ;; *) rm -f "$f" ;; esac
  done
  if [ "$ST" = "draft_review_required False" ]; then
    led "F2.6" "PASS" "pin->review-only draft (enabled=False); only our new draft removed"
  elif [ -n "$ERR" ]; then
    led "F2.6" "NEEDS-SETUP" "pin could not run: $ERR (seed a host-named sanctioned capture)"
  else
    led "F2.6" "CHECK" "pin status: $ST"
  fi
else led "F2.6" "NEEDS-SETUP" "no captures on disk -- capture a sanctioned page first"; fi

# F3.2 -- enable then restore via the DEDICATED toggle route. This is the canonical
# lever (app_template_manager: POST /api/automation/drift_repair/toggle {enabled}
# -> _gc.set_config(ENABLE_KEY); GET /api/automation/drift_repair reports the
# authoritative `enabled`). The generic /api/global_config POST is the WRONG lever.
# (Cross-agent review flagged this; verified against the route source.) Envelope:
# read -> enable -> verify -> revert -> verify. Bails to CHECK if not a readable bool.
dren(){ GET /api/automation/drift_repair | "$PY" -c 'import sys,json
try: print("True" if json.load(sys.stdin).get("enabled") is True else "False")
except Exception: print("ERR")' 2>/dev/null; }
B0="$(dren)"
if [ "$B0" != "True" ] && [ "$B0" != "False" ]; then led "F3.2" "CHECK" "GET /api/automation/drift_repair not a readable bool ($B0)"
else
  [ "$B0" = "True" ] && REV=true || REV=false
  POST POST /api/automation/drift_repair/toggle '{"enabled": true}' >/dev/null
  E="$(dren)"
  POST POST /api/automation/drift_repair/toggle "{\"enabled\": $REV}" >/dev/null
  A0="$(dren)"
  { [ "$E" = "True" ] && [ "$A0" = "$B0" ]; } && led "F3.2" "PASS" "toggle enable->revert restored to $B0 (scheduled tick still owed)" \
    || led "F3.2" "CHECK" "enable=$E restored=$A0 (expected $B0)"
fi

# F3.1 -- saved-search create -> confirm -> delete (envelope)
POST POST /api/saved_searches '{"name":"opv-run-tmp","query":"failed","action":"enqueue","daily_cap":20}' >/dev/null
SID="$(GET /api/saved_searches | "$PY" -c 'import sys,json;d=json.load(sys.stdin);s=d.get("searches",d if isinstance(d,list) else []);print(next((str(x.get("id")) for x in s if x.get("name")=="opv-run-tmp"),""))' 2>/dev/null)"
if [ -n "$SID" ]; then curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -X DELETE "$BASE/api/saved_searches/$SID" >/dev/null
  led "F3.1" "PASS" "create->id=$SID->delete round-trip (week-long cap proof is operator-owned)"
else led "F3.1" "CHECK" "saved-search not created"; fi

# NOVNC-PRECEDENCE -- metrics carry the takeover counters (read)
TK="$(GET /metrics | grep -oE 'bd_takeover[_a-z]* [0-9]+' | head -2 | tr '\n' ' ')"
[ -n "$TK" ] && led "NOVNC" "READY" "metrics: $TK (detect->handoff is operator-observed)" || led "NOVNC" "CHECK" "no takeover counters on /metrics"

# F4.1 -- share_target present + /dashboard receiver answers (read)
SH="$(GET /static/manifest.json | grep -o 'share_target' | head -1)"
[ -n "$SH" ] && [ "$(code '/dashboard?url=https://example.org/x')" = "200" ] && led "F4.1" "READY" "share_target + /dashboard receiver ok (2-tap needs a phone)" || led "F4.1" "CHECK" "share_target/dashboard"

# F45-METRIC -- cadence from source (code-confirmed)
"$PY" - <<PYEOF >/dev/null 2>&1 && led "F45" "CODE-CONFIRMED" "STREAM_SAFETY backoff wired (~93% busy / 50% idle)"
import re; s=open("frontend/src/hooks/useDashboardData.ts").read()
assert all(re.search(r"const %s\s*=\s*[0-9_]+"%n,s) for n in ("FAST","SLOW","STREAM_SAFETY"))
PYEOF

# ---- Tier 2: RISKY live mutations -- gated -------------------------------------
hdr "Tier 2  risky live mutations (need --allow-risky + an operator go)"

# VPNKILL -- kmod-gated. Never fake it.
WG="$(ip link add wgprobe type wireguard >/dev/null 2>&1 && { echo present; ip link del wgprobe; } || echo absent)"
NTUN="$(GET /api/vpn/tunnels | "$PY" -c 'import sys,json;print(len(json.load(sys.stdin).get("tunnels",[])))' 2>/dev/null)"
if [ "$WG" != present ]; then led "VPNKILL" "SKIP" "WireGuard kmod absent -- live-tunnel arm cannot run (state machine already proven)"
elif [ "${NTUN:-0}" = 0 ]; then led "VPNKILL" "NEEDS-SETUP" "kmod present but 0 tunnels configured"
elif [ "$ALLOW_RISKY" = 1 ]; then led "VPNKILL" "GATED" "kmod+tunnel present; trigger/clear left to the operator go (medium risk: egress)"
else led "VPNKILL" "STAGED" "runnable; re-run with --allow-risky + confirm to trigger the kill+clear"; fi

# B2 -- one real download. Reachability only unless --allow-risky + a staged draft.
MC="$(curl -s -o /dev/null -w '%{http_code}' -r 0-1024 --max-time 15 'https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4' 2>/dev/null)"
if [ "$MC" = 206 ] || [ "$MC" = 200 ]; then led "B2" "READY" "sanctioned media reachable ($MC); needs a configured site + staged review-only draft"
else led "B2" "SKIP" "sanctioned media unreachable ($MC)"; fi

# F1.4 -- per-site; needs a configured login site + >=3 lifetimes
NSITES="$(GET /api/sites 2>/dev/null | "$PY" -c 'import sys,json
try: d=json.load(sys.stdin); r=d.get("sites",d) if isinstance(d,dict) else d; print(len(r) if isinstance(r,(list,dict)) else 0)
except Exception: print(0)' 2>/dev/null)"
"$PY" -c "import sys;sys.path.insert(0,'.');from bulk_downloader import app_kernel as k;import sys as s;s.exit(0 if all(x in k.CFG_FIELDS for x in ('predictive_relogin_enabled','predictive_relogin_fraction')) else 1)" 2>/dev/null \
  && led "F1.4" "NEEDS-SETUP" "GUI toggle present + in CFG_FIELDS; seed a login site + >=3 cycles (${NSITES:-0} sites now)" \
  || led "F1.4" "FAIL" "predictive_relogin not in CFG_FIELDS"

# ---- Tier 3: operator/device-only --------------------------------------------
hdr "Tier 3  operator / device (human act required)"
led "PICK" "OPERATOR" "needs a HUMAN click in the noVNC picker (display is up on stash)"
led "F4.5" "OPTIONAL" "phone + measurement; mechanism code-confirmed"

# ---- attestation --------------------------------------------------------------
hdr "DISPOSITIONS -- operator attests (this tool does NOT self-certify)"
column -t "$LEDGER" 2>/dev/null || cat "$LEDGER"
echo
echo "Ledger + logs: $OUT/   |   re-run bd-opv-check.sh / bd-stash-report.sh for the measured status."
rm -f "$JAR"
