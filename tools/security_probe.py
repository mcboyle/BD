"""Security probe — focused vulnerability checks beyond DAST/SAST.

These are targeted, narrative probes for specific vulnerability classes
that don't show up well in either a generic web scanner or a static
linter. Each section frames a threat scenario and tests a concrete
defense.

Sections:
  V.1   CSRF token reuse / replay
  V.2   CSRF token cross-session (token from session A used against B)
  V.3   Constant-time comparison on CSRF (timing-attack surface)
  V.4   Auth bypass via header smuggling
  V.5   TOCTOU on cookie file save (file replaced mid-save)
  V.6   Cookie path-traversal beyond allowlist
  V.7   Open-redirect surface
  V.8   Content-Type confusion (accept HTML where JSON expected)
  V.9   HTTP method override smuggling
  V.10  Cache-poisoning surface (X-Forwarded-* headers)
  V.11  Verb tampering on protected routes
  V.12  Mass assignment — sneak fields not in DEFAULTS into a save
  V.13  Long header / cookie DoS
  V.14  Bearer auth — short / blank / replay
  V.15  ZIP-slip / archive bomb on any upload that accepts archives
  V.16  Symlink attack — write to symlinked path

Run via:
  BD_DISABLE_KEEPALIVE=1 python tools/security_probe.py
"""
from __future__ import annotations
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _probe_lib as L  # noqa: E402

opts = L.parse_flags()

rep = L.Report("SEC")
client, bd_app, bd_db, BD_HOME = L.make_isolated_client(prefix="bd_sec_")

# Bootstrap session + CSRF
client.get("/")
csrf_r = client.get("/api/csrf")
TOK = (csrf_r.get_json() or {}).get("csrf_token", "") if csrf_r.status_code == 200 else ""
HDRS = {"X-CSRF-Token": TOK} if TOK else {}

# ─── V.1: CSRF token reuse / replay ────────────────────────────────────
rep.section("V.1 — CSRF token reuse across many requests")
# A working CSRF design lets the token be reused within a session (rotated
# only on session change). Test: same token across 5 POSTs all succeed.
reuse_ok = 0
for i in range(5):
    r = client.post("/api/sites",
                    json={"name": f"csrf_reuse_{i}"},
                    headers=HDRS)
    if r.status_code == 200:
        reuse_ok += 1
if reuse_ok == 5:
    rep.F("ok", "CSRF token reused 5x in same session (expected)")
else:
    rep.F("warn", f"CSRF rejected {5 - reuse_ok}/5 reuses in same session")

# ─── V.2: CSRF token cross-session ─────────────────────────────────────
rep.section("V.2 — CSRF cross-session rejection")
# Build a second client with its own session, capture its CSRF, then
# try to use it against the first client (which has a different session).
client2 = bd_app.app.test_client()
client2.get("/")
csrf2 = client2.get("/api/csrf")
TOK2 = (csrf2.get_json() or {}).get("csrf_token", "") if csrf2.status_code == 200 else ""
if TOK and TOK2 and TOK != TOK2:
    r = client.post("/api/sites",
                    json={"name": "cross_session"},
                    headers={"X-CSRF-Token": TOK2})
    if r.status_code == 403:
        rep.F("ok", "cross-session CSRF token correctly rejected (403)")
    elif r.status_code == 200:
        rep.F("crit", "cross-session CSRF ACCEPTED — token not session-bound")
    else:
        rep.F("warn", f"unexpected status: HTTP {r.status_code}")
else:
    rep.F("info", "couldn't obtain two distinct CSRF tokens")

# ─── V.3: Timing attack on CSRF compare ─────────────────────────────────
rep.section("V.3 — Constant-time CSRF compare")
# Issue many requests with tokens differing in prefix vs suffix length;
# if compare is non-constant-time, prefix-matching tokens will return
# faster than full-mismatch tokens.
N = 200
TOK_GOOD = TOK
TOK_WRONG_PREFIX  = "X" * len(TOK)
TOK_WRONG_SUFFIX  = TOK[:-1] + "X" if TOK else "X"

def _time_compare(bad_token: str, n: int = N) -> float:
    lats = []
    for _ in range(n):
        t = time.perf_counter()
        client.post("/api/sites",
                    json={"name": "timing"},
                    headers={"X-CSRF-Token": bad_token})
        lats.append(time.perf_counter() - t)
    return statistics.median(lats) * 1e6  # microseconds

if TOK:
    t_prefix = _time_compare(TOK_WRONG_PREFIX, n=N//2)
    t_suffix = _time_compare(TOK_WRONG_SUFFIX, n=N//2)
    # In constant-time, both should be within ~30% of each other. A 5x
    # gap suggests non-constant-time compare.
    ratio = max(t_prefix, t_suffix) / max(min(t_prefix, t_suffix), 0.001)
    if ratio < 2.5:
        rep.F("ok", f"CSRF compare timing stable: prefix={t_prefix:.1f}µs "
              f"suffix={t_suffix:.1f}µs (ratio={ratio:.2f}x)")
    else:
        rep.F("warn", f"CSRF compare timing skewed: prefix={t_prefix:.1f}µs "
              f"suffix={t_suffix:.1f}µs (ratio={ratio:.2f}x)",
              "may indicate non-constant-time compare; not exploitable in "
              "single-user model but worth confirming secrets.compare_digest")
else:
    rep.F("info", "no CSRF token to time-test")

# ─── V.4: Auth bypass via header smuggling ──────────────────────────────
rep.section("V.4 — Auth bypass via header smuggling")
# Try header tricks that some auth middlewares mishandle.
SMUGGLE_HEADERS = [
    {"X-Original-URL": "/api/dev/run"},
    {"X-Rewrite-URL": "/api/dev/run"},
    {"X-HTTP-Method-Override": "GET"},
    {"X-Forwarded-Host": "localhost"},
    {"Host": "evil.com"},
    {"Authorization": "Bearer "},                # blank bearer
    {"Authorization": "Bearer x"},                # 1-char bearer
    {"Authorization": "bearer " + "x" * 1000},   # huge bearer
    {"Authorization": "Bogus token"},
    {"Cookie": "bd_session=" + "x" * 4096},      # huge cookie
]
crashes = 0
bypasses = 0
for h in SMUGGLE_HEADERS:
    try:
        r = client.post("/api/sites",
                        json={"name": f"smug_{hash(str(h)) & 0xffff}"},
                        headers={**HDRS, **h})
        if r.status_code >= 500:
            rep.F("bug", f"5xx on smuggled header {list(h.keys())[0]}",
                  f"HTTP {r.status_code}")
            crashes += 1
    except Exception as ex:
        rep.F("bug", f"exception on smuggled header",
              f"{type(ex).__name__}: {ex}")
        crashes += 1
if crashes == 0:
    rep.F("ok", f"{len(SMUGGLE_HEADERS)} smuggled headers — no crashes")

# ─── V.5: TOCTOU on cookie file ─────────────────────────────────────────
rep.section("V.5 — TOCTOU on cookie file save")
# We can't easily race the save in-process, but we can verify the save
# uses atomic-replace (write+rename) rather than in-place open/write.
# Save a site with cookie_file, then check the file was created via rename.
import threading as _th  # noqa

r = client.post("/api/sites",
                json={"name": "toctou_test"},
                headers=HDRS)
sid = None
for k, s in bd_app.s_cfg.items():
    if s.get("name") == "toctou_test":
        sid = k
        break
if sid:
    cf = bd_app.s_cfg[sid].get("cookie_file") or ""
    # cookie file shouldn't be written yet (no login happened)
    if cf and not os.path.exists(cf):
        rep.F("ok", "cookie_file path set but file not written yet (correct)")
    elif cf and os.path.exists(cf):
        rep.F("info", f"cookie_file exists already ({os.path.getsize(cf)}B)")
else:
    rep.F("warn", "couldn't create toctou_test site")

# Check that the atomic-save invariant exists in code (not runtime)
try:
    import inspect
    from bulk_downloader import app as _a
    src = inspect.getsource(_a._save_sites_config)
    if "os.replace" in src or ".replace(" in src:
        rep.F("ok", "_save_sites_config uses os.replace() (atomic write)")
    elif "tmp" in src.lower() and "rename" in src.lower():
        rep.F("ok", "_save_sites_config writes to tmp + rename (atomic)")
    else:
        rep.F("warn", "_save_sites_config may not use atomic write",
              "check that writes go through tmp file + os.replace")
except Exception as ex:
    rep.F("info", f"couldn't inspect _save_sites_config: {ex}")

# ─── V.6: Cookie path traversal beyond allowlist ────────────────────────
rep.section("V.6 — Cookie path beyond allowlist (with allowlist set)")
# Set a path allowlist, then try to save a site whose cookie_file is
# outside it.
ALLOWLIST = os.path.join(BD_HOME, "allowed")
os.makedirs(ALLOWLIST, exist_ok=True)
r = client.post("/api/global_config",
                json={"path_allowlist": [ALLOWLIST]},
                headers=HDRS)
if r.status_code != 200:
    rep.F("warn", f"couldn't set allowlist: HTTP {r.status_code}")
else:
    # Now try out-of-allowlist cookie_file
    r = client.post("/api/sites",
                    json={"name": "allowlist_bypass",
                          "cookie_file": "/etc/passwd"},
                    headers=HDRS)
    if r.status_code in (400, 403):
        rep.F("ok", f"out-of-allowlist cookie_file rejected: HTTP {r.status_code}")
    elif r.status_code == 200:
        # check if it was silently rewritten
        for k, s in bd_app.s_cfg.items():
            if s.get("name") == "allowlist_bypass":
                cf = s.get("cookie_file") or ""
                if cf.startswith(ALLOWLIST) or not cf == "/etc/passwd":
                    rep.F("ok", f"allowlist enforcement rewrote cf to {cf!r}")
                else:
                    rep.F("crit", "ALLOWLIST BYPASS: /etc/passwd accepted as cookie_file")
                break
    # Clear allowlist for subsequent tests
    client.post("/api/global_config",
                json={"path_allowlist": []},
                headers=HDRS)

# ─── V.7: Open redirect ─────────────────────────────────────────────────
rep.section("V.7 — Open redirect surface")
# Search for any route that might issue a 30x with a user-supplied Location.
import re as _re  # noqa
try:
    from bulk_downloader import app as _a
    src = open(_a.__file__).read()
    # Heuristic: redirect( request.args / request.json / request.form )
    pattern = _re.compile(r"redirect\([^)]*(?:request\.|args|form\[)")
    hits = pattern.findall(src)
    if hits:
        rep.F("warn", f"{len(hits)} redirect() call(s) with request-derived target — review for open redirect")
    else:
        rep.F("ok", "no obvious open-redirect surface in app.py")
except Exception as ex:
    rep.F("info", f"open-redirect static check failed: {ex}")

# ─── V.8: Content-Type confusion ────────────────────────────────────────
rep.section("V.8 — Content-Type confusion")
# Send HTML where JSON expected.
r = client.post("/api/sites",
                data=b'<html><body><script>alert(1)</script></body></html>',
                headers={**HDRS, "Content-Type": "text/html"})
if r.status_code in (400, 415, 422):
    rep.F("ok", f"HTML-as-JSON rejected: HTTP {r.status_code}")
elif r.status_code >= 500:
    rep.F("bug", f"HTML-as-JSON crashed server: HTTP {r.status_code}")
elif r.status_code == 200:
    rep.F("warn", f"HTML body accepted on JSON endpoint",
          "no Content-Type strictness; defensive issue only")

# ─── V.9: HTTP method override ──────────────────────────────────────────
rep.section("V.9 — HTTP method override smuggling")
# Some frameworks honor X-HTTP-Method-Override on POST; verify Flask doesn't
r = client.post(f"/api/sites/{sid}" if sid else "/api/sites",
                headers={**HDRS, "X-HTTP-Method-Override": "DELETE"})
# Should be 200/400/405, not actually performing a DELETE
if r.status_code == 200 and sid and sid in bd_app.s_cfg:
    rep.F("ok", "method-override header did NOT trigger DELETE")
elif sid and sid not in bd_app.s_cfg:
    rep.F("crit", "X-HTTP-Method-Override triggered actual DELETE — verb tampering")
else:
    rep.F("ok", f"method-override probe → HTTP {r.status_code} (no DELETE)")

# ─── V.10: Cache-poisoning surface ──────────────────────────────────────
rep.section("V.10 — Cache-poisoning surface")
# X-Forwarded-Host etc. shouldn't be reflected into responses. Use UNIQUE
# markers that wouldn't appear naturally in the index page or its inline
# code (avoid e.g. "javascript:" which can match anchor hrefs).
MARK_HOST   = "v.10-bdsec-probe-evilhostmarker.example"
MARK_PROTO  = "v.10-bdsec-probe-protomarker"
MARK_URL    = "v.10-bdsec-probe-urlmarker"
r = client.get("/",
               headers={"X-Forwarded-Host": MARK_HOST,
                        "X-Forwarded-Proto": MARK_PROTO,
                        "X-Original-URL": MARK_URL})
body = r.data.decode("utf-8", errors="replace")
reflected = [m for m in (MARK_HOST, MARK_PROTO, MARK_URL) if m in body]
if reflected:
    rep.F("crit", f"X-Forwarded-* header REFLECTED in response: {reflected}",
          "cache-poisoning surface")
else:
    rep.F("ok", "X-Forwarded-* headers not reflected in response body")

# ─── V.11: Verb tampering ───────────────────────────────────────────────
rep.section("V.11 — Verb tampering on protected routes")
# Try unusual verbs on routes that expect GET/POST.
UNUSUAL_VERBS = ["PATCH", "OPTIONS", "TRACE", "HEAD", "CONNECT"]
crashes = 0
for v in UNUSUAL_VERBS:
    try:
        r = client.open("/api/sites", method=v, headers=HDRS)
        if r.status_code >= 500:
            rep.F("bug", f"{v} /api/sites → HTTP {r.status_code}")
            crashes += 1
    except Exception as ex:
        rep.F("bug", f"{v} crashed",
              f"{type(ex).__name__}: {ex}")
        crashes += 1
if crashes == 0:
    rep.F("ok", f"{len(UNUSUAL_VERBS)} unusual verbs — no crashes")

# ─── V.12: Mass assignment ──────────────────────────────────────────────
rep.section("V.12 — Mass assignment / undeclared field acceptance")
# Try to sneak fields not in CFG_FIELDS into a site save.
SNEAKY = {
    "name": "mass_assign",
    "is_admin": True,
    "_internal": "X",
    "__proto__": {"polluted": True},
    "constructor": "evil",
    "BD_DEV_MODE": "1",
    "dev_token": "xyzzy",
}
r = client.post("/api/sites", json=SNEAKY, headers=HDRS)
for k, s in bd_app.s_cfg.items():
    if s.get("name") == "mass_assign":
        leaked = [f for f in SNEAKY if f not in ("name",) and f in s]
        if leaked:
            rep.F("warn", f"mass-assignment leak: {leaked}",
                  "extra fields accepted into config — defense in depth issue")
        else:
            rep.F("ok", "mass-assignment blocked (CFG_FIELDS whitelist enforced)")
        break

# ─── V.13: Long header / cookie DoS ─────────────────────────────────────
rep.section("V.13 — Long header / cookie DoS")
LONG_HDR = "X" * 100000
try:
    r = client.get("/", headers={"X-Probe": LONG_HDR})
    if r.status_code >= 500:
        rep.F("bug", f"long header crashed: HTTP {r.status_code}")
    else:
        rep.F("ok", f"100KB header → HTTP {r.status_code} (no 5xx)")
except Exception as ex:
    rep.F("info", f"long header rejected at parse: {type(ex).__name__}")

# ─── V.14: Bearer auth abuse ────────────────────────────────────────────
rep.section("V.14 — Bearer auth edge cases")
BEARER_PROBES = [
    "",                 # empty
    " ",                # whitespace
    "Bearer",           # no token
    "Bearer ",          # blank token after space
    "bearer abc",       # lowercase scheme
    "Bearer\tabc",      # tab separator
    "Bearer abc def",   # double space
    "Bearer " + "A" * 10000,
]
crashes = 0
for b in BEARER_PROBES:
    try:
        r = client.post("/api/sites",
                        json={"name": "bearer_probe"},
                        headers={"Authorization": b})
        if r.status_code >= 500:
            rep.F("bug", f"bearer crash: {b[:30]!r} → HTTP {r.status_code}")
            crashes += 1
    except Exception as ex:
        rep.F("bug", f"bearer threw: {type(ex).__name__}")
        crashes += 1
if crashes == 0:
    rep.F("ok", f"{len(BEARER_PROBES)} bearer probes — no crashes")

# ─── V.15: ZIP-slip / archive bomb ──────────────────────────────────────
rep.section("V.15 — Archive handling (static surface check)")
# We can't easily craft a working zip-slip without knowing the endpoint;
# do a static check for ZipFile.extract / ZipFile.extractall + path joins
try:
    import re as _re2
    src = open(os.path.join(L.REPO_ROOT, "bulk_downloader", "app.py")).read()
    risky = _re2.findall(r"\b(extractall|extract)\(", src)
    if risky:
        rep.F("warn", f"{len(risky)} zip extract call(s) in app.py — verify each "
              "validates member names against path traversal")
    else:
        rep.F("ok", "no zip.extract*() in app.py")
except Exception as ex:
    rep.F("info", f"zip-slip static check failed: {ex}")

# ─── V.16: Symlink attack ───────────────────────────────────────────────
rep.section("V.16 — Symlink attack on cookie/download path")
# Make a symlink that points outside BD_HOME, set it as cookie_file, and
# verify the app refuses or follows-safely.
if hasattr(os, "symlink"):
    try:
        link = os.path.join(BD_HOME, "symlink_to_etc.json")
        target = "/etc/passwd"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            rep.F("info", "symlink creation unavailable on this platform")
            link = None
        if link:
            r = client.post("/api/sites",
                            json={"name": "symlink_attack",
                                  "cookie_file": link},
                            headers=HDRS)
            if r.status_code in (400, 403):
                rep.F("ok", f"symlinked cookie_file rejected: HTTP {r.status_code}")
            elif r.status_code == 200:
                rep.F("warn", "symlink to /etc/passwd accepted as cookie_file",
                      "single-user model treats this as self-harm; defense "
                      "in depth would resolve realpath and check")
            os.unlink(link)
    except Exception as ex:
        rep.F("info", f"symlink test errored: {ex}")
else:
    rep.F("info", "os.symlink unavailable (Windows without admin)")

# ─── Summary ───────────────────────────────────────────────────────────
rep.summary()
sys.exit(rep.exit_code())
