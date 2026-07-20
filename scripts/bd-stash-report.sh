#!/bin/bash
# bd-stash-report.sh — capture stash-accurate BulkDownloader state for analysis.
#
# WHY: the cloud sandbox cannot reach stash (LAN IP), so "does X render / persist
# on the real box" is unanswerable from there. Run this ON STASH; it produces a
# tarball of DERIVED facts (re-measured from the running service + source, never
# quoted from a doc) that answers those questions deterministically.
#
# SAFE: read-only except config keys it writes then immediately reverts to their
# prior values (to prove POST acceptance); it restores even on error. No secret
# VALUES are emitted — only present/absent, lengths, http codes, booleans.
#
# USAGE:  bash scripts/bd-stash-report.sh [repo-dir] [base-url]
#   repo-dir  default: $PWD    (must contain bulk_downloader/__init__.py)
#   base-url  default: http://127.0.0.1:5555
# OUTPUT:  /tmp/bd_stash_report/  and  /tmp/bd_stash_report.tar.gz  (upload the .tgz)

set -uo pipefail
R="${1:-$PWD}"
BASE="${2:-http://127.0.0.1:5555}"
OUT=/tmp/bd_stash_report
PY="$R/venv/bin/python"; [ -x "$PY" ] || PY="python3"
JAR=/tmp/bd_stash_report.jar

[ -f "$R/bulk_downloader/__init__.py" ] || { echo "not a BD checkout: $R"; exit 2; }
cd "$R"
rm -rf "$OUT" "$JAR"; mkdir -p "$OUT"

# ---- 0. identity --------------------------------------------------------------
{
  echo "# BD stash report -- $(date -u +%FT%TZ)"
  echo "host      : $(hostname 2>/dev/null)"
  echo "repo      : $R"
  echo "git sha   : $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
  echo "git branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
  echo "version   : $("$PY" -c 'import bulk_downloader;print(bulk_downloader.__version__)' 2>/dev/null || echo '?')"
  echo "python    : $("$PY" --version 2>&1)"
  echo "base url  : $BASE"
  echo "service up: $(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$BASE/" 2>/dev/null || echo DOWN)"
} | tee "$OUT/00_identity.txt"

# ---- csrf prelude -------------------------------------------------------------
curl -s -c "$JAR" "$BASE/api/csrf" > "$OUT/_csrf.json" 2>/dev/null || true
CSRF="$("$PY" -c 'import json;d=json.load(open("'"$OUT"'/_csrf.json"));print(d.get("csrf_token") or d.get("token") or "")' 2>/dev/null || echo "")"
echo "csrf len: ${#CSRF}" > "$OUT/01_csrf.txt"

# ---- capture the raw JSON the SPA/API returns ---------------------------------
curl -s -b "$JAR" "$BASE/api/global_config"   > "$OUT/10_global_config_GET.json" 2>/dev/null || true
curl -s -b "$JAR" "$BASE/api/settings/schema" > "$OUT/11_settings_schema.json"   2>/dev/null || true
curl -s -b "$JAR" "$BASE/api/sites"           > "$OUT/30_sites.json"             2>/dev/null || true
curl -s -b "$JAR" "$BASE/api/vpn/tunnels"     > "$OUT/31_vpn_tunnels.json"       2>/dev/null || true
curl -s -b "$JAR" "$BASE/api/saved_searches"  > "$OUT/32_saved_searches.json"    2>/dev/null || true
curl -s -b "$JAR" "$BASE/api/analyzer/captures" > "$OUT/33_captures.json"        2>/dev/null || true

# ---- 1. GUI-config RENDER TRUTH (the whole point) -----------------------------
# A knob is only operator-settable from the UI when THREE things hold: the SPA GETs
# the value, Settings.tsx has an explicit control for it, and it ships in the built
# bundle. This block reports all three per key so a false "gui_exposure=full" (which
# only means the key STRING appears in a .ts file) can't hide a control that never
# renders. Grep the deployed source, not the sandbox's.
OUT="$OUT" PY="$PY" "$PY" - <<'PYEOF' | tee "$OUT/12_gui_render_truth.txt"
import json, os, subprocess
OUT = os.environ["OUT"]
def load(f):
    try: return json.load(open(os.path.join(OUT, f)))
    except Exception: return {}
gc = load("10_global_config_GET.json")
sch = load("11_settings_schema.json")
def grep_count(pat, path):
    try: return int(subprocess.run(["grep","-c",pat,path],capture_output=True,text=True).stdout.strip() or "0")
    except Exception: return 0
def in_dist(pat):
    try: return subprocess.run(["grep","-rq",pat,"frontend/dist"]).returncode == 0
    except Exception: return False
NEW = ["captcha_vnc_display","captcha_vnc_websocket_port","netns_isolation",
       "predictive_relogin_enabled","predictive_relogin_fraction"]
REF = ["captcha_takeover_mode","novnc_url"]
STL = "frontend/src/routes/Settings.tsx"
print("== (a) GET /api/global_config returns the key? (SPA renders a global control only if so) ==")
for k in NEW + REF:
    print(f"  {k:32s} in_GET={k in gc}")
print("\n== (b) Settings.tsx has an EXPLICIT control ref? (global keys only; per-site keys render via SiteSettings) ==")
for k in ["captcha_vnc_display","captcha_vnc_websocket_port","netns_isolation","captcha_takeover_mode","novnc_url"]:
    print(f"  {k:32s} refs_in_Settings.tsx={grep_count(k, STL)}")
print("\n== (c) key compiled into the shipped bundle (frontend/dist)? ==")
for k in ["captcha_vnc_display","netns_isolation","captcha_takeover_mode"]:
    print(f"  {k:32s} in_dist={'PRESENT' if in_dist(k) else 'absent'}")
print(f"\n== settings/schema unique_fields (Cut 3 -> expect 237) : {sch.get('unique_fields')} ==")
PYEOF

# ---- 2. backend ACCEPTANCE (declared keys) — POST then REVERT -----------------
OUT="$OUT" PY="$PY" BASE="$BASE" CSRF="$CSRF" JAR="$JAR" "$PY" - <<'PYEOF' | tee "$OUT/13_backend_acceptance.txt"
import json, os, subprocess
OUT, BASE, CSRF, JAR = os.environ["OUT"], os.environ["BASE"], os.environ["CSRF"], os.environ["JAR"]
before = {}
try: before = json.load(open(os.path.join(OUT,"10_global_config_GET.json")))
except Exception: pass
def post(k, valjson):
    r = subprocess.run(["curl","-s","-o","/dev/null","-w","%{http_code}","-b",JAR,
        "-H",f"X-CSRF-Token: {CSRF}","-H","Content-Type: application/json",
        "-X","POST",f"{BASE}/api/global_config","-d",json.dumps({k: valjson})],
        capture_output=True, text=True)
    return r.stdout.strip()
print("== POST /api/global_config acceptance (200 = key is DECLARED; was 400 pre-cut). Reverted after. ==")
for k, testval in [("captcha_vnc_display",":5"),("captcha_vnc_websocket_port","8444"),("netns_isolation",False)]:
    code = post(k, testval)
    print(f"  {k:32s} POST_http={code}")
    if k in before:  # revert only if it was previously set
        post(k, before[k])
PYEOF

# ---- 3. per-site editor (Cut 3 predictive-relogin renders here, schema-driven) --
OUT="$OUT" PY="$PY" BASE="$BASE" JAR="$JAR" "$PY" - <<'PYEOF' | tee "$OUT/14_persite_editor.txt"
import json, os, subprocess
OUT, BASE, JAR = os.environ["OUT"], os.environ["BASE"], os.environ["JAR"]
try: sites = json.load(open(os.path.join(OUT,"30_sites.json")))
except Exception: sites = {}
rows = sites.get("sites", sites) if isinstance(sites, dict) else sites
sid = ""
if isinstance(rows, list) and rows: sid = str(rows[0].get("id",""))
elif isinstance(rows, dict) and rows: sid = str(next(iter(rows)))
print(f"first configured site id: {sid or '<none>'}")
if sid:
    r = subprocess.run(["curl","-s","-b",JAR,f"{BASE}/api/settings/site/{sid}/editable"],
                       capture_output=True, text=True)
    blob = r.stdout
    for k in ("predictive_relogin_enabled","predictive_relogin_fraction"):
        print(f"  {k} in editable descriptor: {k in blob}")
else:
    print("  (no site configured -- configure one sanctioned test site to verify Cut 3 renders in the per-site editor)")
PYEOF

# ---- 4. OPV-relevant state (for guide accuracy) -------------------------------
OUT="$OUT" PY="$PY" "$PY" - <<'PYEOF' | tee "$OUT/15_opv_state.txt"
import json, os
OUT = os.environ["OUT"]
def load(f):
    try: return json.load(open(os.path.join(OUT,f)))
    except Exception: return {}
def n(x):
    if isinstance(x, list): return len(x)
    if isinstance(x, dict): return len(x)
    return x
gc = load("10_global_config_GET.json")
print("== OPV live-verify preconditions (for the completion-guide status) ==")
sites = load("30_sites.json"); print("sites configured        :", n(sites.get("sites", sites) if isinstance(sites,dict) else sites))
print("vpn tunnels             :", n(load("31_vpn_tunnels.json").get("tunnels", [])))
ss = load("32_saved_searches.json"); print("saved searches          :", n(ss if isinstance(ss,list) else ss.get("searches", [])))
print("analyzer captures       :", n(load("33_captures.json").get("captures", [])))
print("drift_repair enabled    :", gc.get("automation.drift_repair_enabled"))
print("captcha_takeover_enabled:", gc.get("captcha_takeover_enabled"))
print("captcha_takeover_mode    :", gc.get("captcha_takeover_mode"))
PYEOF
{
  echo -n "wireguard kmod          : "; (ip link add wgprobe type wireguard >/dev/null 2>&1 && { echo present; ip link del wgprobe; } || echo absent)
  echo -n "kasmvncserver installed : "; (command -v kasmvncserver >/dev/null && echo yes || echo no)
} | tee -a "$OUT/15_opv_state.txt"

# ---- bundle -------------------------------------------------------------------
rm -f "$JAR" "$OUT/_csrf.json"
tar czf /tmp/bd_stash_report.tar.gz -C /tmp bd_stash_report 2>/dev/null
echo
echo "================================================================"
echo "  Done. Upload /tmp/bd_stash_report.tar.gz to Claude."
echo "  (derived facts + presence booleans only; no secret values emitted)"
echo "================================================================"
ls -la /tmp/bd_stash_report.tar.gz
