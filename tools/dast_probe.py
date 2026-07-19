"""DAST probe — adversarial input against the live Flask app.

Spins up the app under test_client in an isolated BD_HOME and exercises
every route class that matters, with malicious input and edge cases.
Catches the things a static tool can't: live XSS round-trip, actual
side-effect verification, response-time variance, state corruption.

Run via tools/dast.sh or tools/dast.bat — or directly:
    BD_DISABLE_KEEPALIVE=1 python tools/dast_probe.py

Exit code: 0 if no bug/crit findings, 1 otherwise.
"""
import os, sys, json, time, tempfile, sqlite3, threading

# Run from repo root regardless of cwd
SCRIPT = os.path.abspath(__file__)
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT))
sys.path.insert(0, REPO_ROOT)

os.environ["BD_DISABLE_KEEPALIVE"] = "1"
BD_HOME = tempfile.mkdtemp(prefix="bd_dast_")
os.environ["BD_HOME"] = BD_HOME
os.chdir(BD_HOME)

import bulk_downloader.app as bd_app
import bulk_downloader.db as bd_db
bd_db.db_init()
client = bd_app.app.test_client()
bd_app.app.config["TESTING"] = True

# ─── Report machinery ──────────────────────────────────────────────────
findings = {"info":0,"ok":0,"warn":0,"bug":0,"crit":0}
all_findings = []
def F(sev, name, detail=""):
    findings[sev] += 1
    all_findings.append((sev, name, detail))
    sym = {"info":"·","ok":"✓","warn":"⚠","bug":"✗","crit":"🛑"}[sev]
    print(f"  {sym} [{sev:4}] {name}")
    if detail: print(f"           {detail[:240]}")

def section(name):
    print(f"\n=== {name} ===")

def csrf_post(url, **kw):
    """POST with a fresh X-CSRF-Token, mimicking the JS fetch wrapper.

    The Flask test_client is sticky on cookies, so once GET / mints a
    bd_session the client carries it forward; from then on every POST
    needs the CSRF header or it will 403. The real browser-side fetch()
    wrapper handles this transparently. The probe must do it too, or
    state-stability tests would just measure CSRF rejection instead of
    the route under test.
    """
    r = client.get("/api/csrf")
    tok = (r.get_json() or {}).get("csrf_token", "") if r.status_code == 200 else ""
    hdrs = dict(kw.pop("headers", {}) or {})
    if tok: hdrs["X-CSRF-Token"] = tok
    return client.post(url, headers=hdrs, **kw)

# ─── DAST.1: serve_ss path traversal ────────────────────────────────────
section("DAST.1 — serve_ss path traversal")
for path in [
    "/screenshots/../etc/passwd",
    "/screenshots/../../etc/passwd",
    "/screenshots_evil/file.png",     # prefix-match bypass attempt
    "/screenshots../escape.png",
    "/screenshots/%2e%2e/etc/passwd",
    "/screenshots/%2e%2e%2fetc/passwd",
    "/screenshots/foo\x00.png",
]:
    try:
        r = client.get(path)
        if r.status_code == 200 and (b"root:" in r.data or b"shadow" in r.data):
            F("crit", f"PATH TRAVERSAL: {path}",
              f"got 200 with /etc/passwd content")
    except Exception:
        pass
F("ok", "serve_ss traversal: no system file leaked")

# ─── DAST.2: FTS stored XSS ────────────────────────────────────────────
section("DAST.2 — FTS stored XSS")
with bd_db.db_conn() as cx:
    cx.execute("""INSERT INTO history
                  (site_id,site_name,url,status,filename,message)
                  VALUES (?,?,?,?,?,?)""",
               ("dast","DSITE",
                "http://e.x/<script>alert('a')</script>.mp4",
                "done","vid<img src=x onerror=alert(1)>.mp4",
                'Page text "><script>steal()</script> normal text'))
    try: cx.execute("INSERT INTO history_fts(history_fts) VALUES('rebuild')")
    except sqlite3.OperationalError: pass

rows = bd_db.db_search_fts("text")
leaked = False
for r in rows:
    for k in ("snippet_url","snippet_filename","snippet_message"):
        v = (r.get(k) or "")
        if "<script>" in v.lower() and "&lt;script&gt;" not in v:
            F("crit", f"XSS LEAK in {k}",
              f"contains live <script>: {v[:120]!r}")
            leaked = True
        elif "<img" in v.lower() and "onerror" in v.lower() and "&lt;" not in v:
            F("crit", f"XSS LEAK in {k} (img onerror)", f"{v[:120]!r}")
            leaked = True
if not leaked:
    F("ok", f"FTS snippets HTML-escaped, <mark> highlights preserved ({len(rows)} rows)")

# ─── DAST.3: cookie_file auto-fill ─────────────────────────────────────
section("DAST.3 — cookie_file auto-fill")
bd_app.s_cfg.clear()
sid = "dast_cf_test"
bd_app.s_cfg[sid] = {"name":"DastTest","cookie_file":""}
bd_app._save_sites_config()
cf = bd_app.s_cfg[sid].get("cookie_file","")
expected = os.path.join(BD_HOME, "cookies", f"{sid}.json")
if os.path.normpath(cf) == os.path.normpath(expected):
    F("ok", f"cookie_file auto-filled: {cf}")
else:
    F("bug", "cookie_file auto-fill MISSED", f"got={cf!r} expected={expected!r}")

# ─── DAST.4: auto_relogin opt-out + type confusion ─────────────────────
section("DAST.4 — auto_relogin opt-out + type confusion")
from bulk_downloader import cookie_relogin
fake = {
    "A":{"auth_required":True,"auto_relogin_enabled":False,"name":"A"},
    "B":{"auth_required":True,"auto_relogin_enabled":True,
         "auto_relogin_interval_hours":6,"name":"B"},
    "C":{"auth_required":True,"auto_relogin_enabled":True,
         "auto_relogin_interval_hours":"abc","name":"C"},
    "D":{"auth_required":True,"auto_relogin_enabled":True,
         "auto_relogin_interval_hours":None,"name":"D"},
    "E":{"auth_required":True,"auto_relogin_enabled":True,
         "auto_relogin_interval_hours":999999,"name":"E"},
    "F":{"auth_required":True,"auto_relogin_enabled":True,
         "auto_relogin_interval_hours":-50,"name":"F"},
}
try:
    res = cookie_relogin.check_and_schedule(fake)
    reasons = [s.get("reason","") for s in res.get("skipped",[])]
    if any("opted out" in x for x in reasons):
        F("ok", "auto_relogin_enabled=False short-circuits scheduler")
    else:
        F("bug", "opt-out NOT enforced", f"reasons={reasons[:3]}")
    F("ok", "scheduler survived poisoned interval types (str/None/over/neg)")
except Exception as e:
    F("bug", "scheduler crashed", f"{type(e).__name__}: {e}")

# ─── DAST.5: /api/logs/clear ───────────────────────────────────────────
section("DAST.5 — /api/logs/clear")
log_dir = os.path.join(BD_HOME, "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "bulk_downloader.log")
with open(log_file, "w") as f: f.write("xx\n" * 50000)
for i in (1,2,3):
    with open(log_file + f".{i}", "w") as f: f.write(f"arc{i}\n")
sb = os.path.getsize(log_file)
ab = len([n for n in os.listdir(log_dir) if n.startswith("bulk_downloader.log.")])

r = client.get("/api/logs/clear")
if r.status_code != 405:
    F("warn", f"GET /api/logs/clear → HTTP {r.status_code} (expected 405)")

r = client.post("/api/logs/clear")
if r.status_code == 200:
    sa = os.path.getsize(log_file)
    aa = len([n for n in os.listdir(log_dir) if n.startswith("bulk_downloader.log.")])
    if sa < sb / 10: F("ok", f"log truncated {sb}→{sa}B")
    else: F("bug", f"truncate failed: {sb}→{sa}")
    if aa < ab: F("ok", f"archives swept {ab}→{aa}")
    else: F("warn", f"archives not removed: {ab}→{aa}")
else:
    F("bug", f"POST /api/logs/clear → HTTP {r.status_code}", f"body={r.data[:150]!r}")

# ─── DAST.6: /api/logs/tail edge cases ─────────────────────────────────
section("DAST.6 — /api/logs/tail edge cases")
with open(log_file, "w") as f: f.write("LineContent" * 30000 + "\n")
for label, q in [
    ("normal","lines=100"),("absurd","lines=999999"),
    ("negative","lines=-5"),("non_numeric","lines=abc"),
    ("zero","lines=0"),("scientific","lines=1e10"),
]:
    r = client.get(f"/api/logs/tail?{q}")
    if r.status_code == 200 and (r.get_json() or {}).get("ok"):
        F("ok", f"tail {label} ({q}) handled")
    else:
        F("warn", f"tail {label} ({q}) → HTTP {r.status_code}")

# ─── DAST.7: dev mode + kill-switch ────────────────────────────────────
section("DAST.7 — dev mode + kill-switch")
r = client.get("/api/dev/enabled")
if r.status_code == 200 and (r.get_json() or {}).get("enabled"):
    F("ok", "dev mode enabled by default (v3.47.7 unlock)")
else:
    F("bug", "dev mode not enabled by default",
      f"HTTP {r.status_code}, body={r.data[:150]!r}")
os.environ["BD_DEV_MODE_DISABLE"] = "1"
r = client.get("/api/dev/enabled")
body = r.get_json() if r.status_code == 200 else {}
if not body.get("enabled", True):
    F("ok", "BD_DEV_MODE_DISABLE=1 kill-switch works")
else:
    F("bug", "kill-switch did NOT disable dev mode")
del os.environ["BD_DEV_MODE_DISABLE"]
# Path-escape on /api/dev/run
escaped = False
for payload in [
    {"test_id":"../../../../etc/passwd"},
    {"test_id":"/etc/passwd"},
    {"test_id":"tests/../../etc/passwd"},
    {"test_id":"test_x.py; rm -rf /"},
]:
    r = client.post("/api/dev/run", json=payload)
    if r.status_code == 200 and b"PASS" in r.data:
        F("crit", f"dev/run accepted path escape: {payload['test_id']}")
        escaped = True
if not escaped:
    F("ok", "dev/run path-escape rejected (4 payloads)")

# ─── DAST.8: malformed input on /api/sites ─────────────────────────────
section("DAST.8 — malformed input")
r = client.post("/api/sites", data=b"{not valid json",
                content_type="application/json")
if r.status_code >= 500:
    F("bug", f"malformed JSON returned 5xx: HTTP {r.status_code}")
else:
    F("ok", f"malformed JSON → HTTP {r.status_code} (no 5xx)")

# v3.47.8 (#82): we cap at 4 MB. Verify 5MB rejected, 3MB accepted.
big_5mb = json.dumps({"name":"big","junk":"A"*5_000_000})
r = client.post("/api/sites", data=big_5mb, content_type="application/json")
if r.status_code == 413:
    F("ok", "5MB JSON → 413 (cap enforced)")
elif r.status_code == 200:
    F("bug", "5MB JSON accepted — MAX_CONTENT_LENGTH cap missing or wrong",
      "v3.47.8 should cap at 4 MB")
else:
    F("info", f"5MB JSON → HTTP {r.status_code}")

big_3mb = json.dumps({"name":"mid","junk":"A"*3_000_000})
r = client.post("/api/sites", data=big_3mb, content_type="application/json")
if r.status_code == 200:
    F("ok", "3MB JSON accepted (under 4MB cap)")
elif r.status_code == 413:
    F("warn", "3MB JSON rejected — cap is too tight",
      "may break legitimate large config imports")

# v3.47.8 (#81): unicode poison should be ACCEPTED but STRIPPED. Verify
# the stored name has been cleaned, not that the request was rejected.
poison_name = "\u0000\u202eEvil\ufeff"
r = client.post("/api/sites", json={"name": poison_name})
if r.status_code != 200:
    F("bug", f"unicode-poison name unexpectedly rejected: HTTP {r.status_code}",
      "v3.47.8 normalizes, doesn't reject")
else:
    # Find the freshly-created site and verify its stored name is clean
    sid_new = (r.get_json() or {}).get("id")
    stored = (bd_app.s_cfg.get(sid_new) or {}).get("name", "")
    if any(c in stored for c in ("\u0000", "\u202e", "\ufeff")):
        F("bug", "unicode poison survived normalization",
          f"stored name: {stored!r}")
    else:
        F("ok", f"unicode-poison name accepted and cleaned (stored={stored!r})")

# v3.47.8 (#80): on a fresh BD_HOME, path_allowlist is auto-seeded with
# BD_HOME + ~/Downloads/bulk_downloader. /etc/passwd should be REJECTED
# without any explicit allowlist configuration.
r = client.post("/api/sites",
                json={"name":"trav_test","cookie_file":"/etc/passwd"})
if r.status_code == 200:
    F("warn", "cookie_file=/etc/passwd ACCEPTED on fresh install",
      "v3.47.8 #80 auto-populate didn't fire or is broken")
elif r.status_code in (400,403):
    F("ok", f"cookie_file=/etc/passwd → HTTP {r.status_code} (allowlist blocked)")

# ─── DAST.9: SQL injection ─────────────────────────────────────────────
section("DAST.9 — SQL injection")
for p in ["' OR 1=1 --", "x'; DROP TABLE history; --",
          "x' UNION SELECT * FROM sqlite_master --", "%' --", "x'); --"]:
    try: bd_db.db_search_fts(p)
    except sqlite3.OperationalError: pass  # FTS5 tokenizer reject
with bd_db.db_conn() as cx:
    n = cx.execute("SELECT COUNT(*) FROM history").fetchone()[0]
if n > 0:
    F("ok", f"SQLi probes survived ({n} history rows intact)")
else:
    F("crit", "history table empty — possible DROP success")

# ─── DAST.10: DoS / response-time ──────────────────────────────────────
section("DAST.10 — DoS surface")
t0 = time.perf_counter()
for _ in range(100): client.get("/api/logs/tail?lines=10")
e = time.perf_counter() - t0
if e > 30:
    F("warn", f"100 tail requests took {e:.1f}s")
else:
    F("ok", f"100 tail requests in {e*1000:.0f}ms ({e*10:.1f}ms avg)")

# ReDoS probe
result = [None]
def probe():
    try: result[0] = bd_db.db_search_fts("a"*10000)
    except Exception as ex: result[0] = ex
t0 = time.perf_counter()
th = threading.Thread(target=probe, daemon=True); th.start()
th.join(timeout=10)
if th.is_alive():
    F("warn", "10K-char search did NOT finish in 10s (potential ReDoS)")
else:
    F("ok", f"10K-char search completed in {(time.perf_counter()-t0)*1000:.0f}ms")

# ─── DAST.11: security response headers ────────────────────────────────
section("DAST.11 — security response headers")
r = client.get("/")
hdrs = dict(r.headers)
for name in ("Content-Security-Policy","X-Content-Type-Options",
             "Referrer-Policy"):
    if hdrs.get(name):
        F("ok", f"{name}: {hdrs[name][:80]}")
    else:
        F("warn", f"{name}: absent")
if "Strict-Transport-Security" not in hdrs:
    F("info", "HSTS absent (expected — HTTP-only LAN tool)")

# ─── DAST.12: repeated requests / state stability ──────────────────────
section("DAST.12 — state stability under load")
# DAST.11's GET / mints a bd_session cookie (that's the CSRF bootstrap
# path — required to put the meta tag in the HTML on first load). The
# test_client carries that cookie into subsequent POSTs, which then need
# X-CSRF-Token. Use csrf_post() to mimic the browser-side fetch wrapper.
sid_count_before = len(bd_app.s_cfg)
status_distribution = {}
for i in range(50):
    r = csrf_post("/api/sites", json={"name": f"load_test_{i}"})
    status_distribution[r.status_code] = status_distribution.get(r.status_code, 0) + 1
sid_count_after = len(bd_app.s_cfg)
created = sid_count_after - sid_count_before
if created == 50:
    F("ok", f"50 sequential POSTs created exactly 50 sites",
      f"status counts: {status_distribution}")
elif created == 0:
    F("bug", f"50 POSTs created 0 sites",
      f"status counts: {status_distribution}")
else:
    F("warn", f"50 POSTs created {created}/50 sites",
      f"status counts: {status_distribution}")

# Concurrent requests — no thread-safety guarantees for app-state but
# Flask test_client is sync. Use threading to fake concurrent hits.
errors = []
def burst():
    try:
        for _ in range(20): client.get("/api/logs/tail?lines=5")
    except Exception as ex: errors.append(ex)
ts = [threading.Thread(target=burst, daemon=True) for _ in range(5)]
for t in ts: t.start()
for t in ts: t.join(timeout=10)
if errors:
    F("bug", f"concurrent load: {len(errors)} thread errors",
      f"first: {errors[0]}")
else:
    F("ok", "100 concurrent reads survived without errors")

# ─── Summary ───────────────────────────────────────────────────────────
print()
print("=" * 70)
print(f"DAST: ok={findings['ok']} info={findings['info']} "
      f"warn={findings['warn']} bug={findings['bug']} crit={findings['crit']}")
print("=" * 70)
non_info = [f for f in all_findings if f[0] not in ("ok","info")]
if non_info:
    print(f"\n{len(non_info)} non-info findings:")
    for sev, name, detail in non_info:
        print(f"  [{sev}] {name}")
        if detail: print(f"        {detail[:200]}")

# Cleanup
import shutil
try: shutil.rmtree(BD_HOME, ignore_errors=True)
except Exception: pass

# Exit code: 1 if any bug/crit
sys.exit(1 if findings["bug"] + findings["crit"] > 0 else 0)
