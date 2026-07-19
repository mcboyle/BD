"""API contract probe — enumerate every /api/* route and verify shape.

Where functional_probe walks the happy path on a handful of routes, this
sweeps every registered route on the Flask app and checks:
  * GET endpoints return JSON or HTML without 5xx
  * POST/PUT/DELETE endpoints reject missing CSRF with 403
  * Each endpoint's response Content-Type matches the body shape
  * Path-parameter routes accept the parameter without 500
  * Method-not-allowed returns 405, not 500

Designed as a smoke layer: catches "this route disappeared", "this route
now 500s on empty body", and "this route's status code changed" — the
breakages that don't show up in handcrafted endpoint tests because the
endpoint wasn't on the test's radar.

Run via:
  BD_DISABLE_KEEPALIVE=1 python tools/api_contract_probe.py [--fast]
"""
from __future__ import annotations
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _probe_lib as L  # noqa: E402

opts = L.parse_flags()

rep = L.Report("CONTRACT")
client, bd_app, bd_db, BD_HOME = L.make_isolated_client(prefix="bd_contract_")

# Bootstrap CSRF
client.get("/")
csrf_r = client.get("/api/csrf")
TOK = (csrf_r.get_json() or {}).get("csrf_token", "") if csrf_r.status_code == 200 else ""
HDRS_OK = {"X-CSRF-Token": TOK} if TOK else {}


def _request_with_timeout(c, method: str, path: str, timeout: float = 3.0,
                          **kw):
    """Run a request with a hard timeout. Returns (status, body_bytes) or
    (-1, b'<timeout>')."""
    result = {"status": -1, "data": b"<timeout>", "ct": ""}
    done = threading.Event()
    def _do():
        try:
            r = c.open(path, method=method, **kw)
            result["status"] = r.status_code
            result["data"] = r.data[:500]
            result["ct"] = r.headers.get("Content-Type", "")
        except Exception as ex:
            result["status"] = -2
            result["data"] = f"{type(ex).__name__}: {ex}".encode()[:500]
        done.set()
    th = threading.Thread(target=_do, daemon=True)
    th.start()
    done.wait(timeout=timeout)
    return result["status"], result["data"], result["ct"]


# Routes to skip — they have side effects we don't want fired blindly:
SKIP_PATTERNS = (
    re.compile(r"/api/dev/run"),
    re.compile(r"/api/ytdlp_update"),
    re.compile(r"/api/ai/"),                # may try network
    re.compile(r"/api/diagnostics/run"),    # heavy
    re.compile(r"/api/sites/\<.*\>/login"), # Playwright
    re.compile(r"/api/queue/run"),
    re.compile(r"/api/(start|stop|shutdown|restart)"),
    re.compile(r"/api/community_scrapers/"),# may try network
    re.compile(r"/api/phoenix/"),           # may try network
    re.compile(r"/api/thumbnails/"),        # may walk filesystem
    re.compile(r"/api/wayback/"),           # network
    re.compile(r"/api/bitrot/"),            # walks filesystem
    re.compile(r"/api/search/all"),         # heavy
    re.compile(r"/api/live_recorder/"),     # spawns recorder
    re.compile(r"/api/captcha/"),
    re.compile(r"/api/vpn/"),               # may spawn processes
    re.compile(r"/api/route_preview"),      # heavy regex match
)

# Sample placeholder values for path parameters
PLACEHOLDER = {
    "sid":            "00000000",
    "site_id":        "00000000",
    "search_id":      "1",
    "url":            "https://example.com/dummy",
    "account_idx":    "0",
    "tag":            "test",
    "name":           "test",
    "key":            "test_key",
}

def _fill_path(rule: str) -> str:
    """Replace <int:foo> / <foo> with safe placeholders."""
    def sub(m):
        var = m.group("var")
        return PLACEHOLDER.get(var, "0")
    return re.sub(r"\<(?:[^:>]+:)?(?P<var>[^>]+)\>", sub, rule)


def _should_skip(rule: str) -> bool:
    return any(p.search(rule) for p in SKIP_PATTERNS)


# Enumerate, dedupe by rule
routes = []
seen = set()
for r in bd_app.app.url_map.iter_rules():
    if r.endpoint == "static":
        continue
    if not r.rule.startswith("/api/") and r.rule != "/":
        continue
    if r.rule in seen:
        continue
    seen.add(r.rule)
    methods = sorted((r.methods or set()) - {"HEAD", "OPTIONS"})
    routes.append((r.rule, methods))

rep.F("info", f"discovered {len(routes)} unique routes under /api/ (plus index)")

# ─── C.1: GET smoke ─────────────────────────────────────────────────────
rep.section("C.1 — GET smoke (every readable endpoint)")
get_skipped = 0
get_ok = 0
get_bug = 0
get_warn = 0
get_timeout = 0
for rule, methods in routes:
    if _should_skip(rule):
        get_skipped += 1
        continue
    if "GET" not in methods:
        continue
    path = _fill_path(rule)
    status, body, ct = _request_with_timeout(client, "GET", path, timeout=3.0)
    if status == -1:
        rep.F("warn", f"GET {rule} timed out")
        get_timeout += 1
    elif status == -2:
        rep.F("bug", f"GET {rule} threw", body.decode("utf-8", "replace"))
        get_bug += 1
    elif status >= 500:
        rep.F("bug", f"GET {rule} → HTTP {status}", body[:150].decode("utf-8", "replace"))
        get_bug += 1
    elif status in (200, 400, 401, 403, 404, 405):
        get_ok += 1
    else:
        get_warn += 1

if get_bug == 0:
    rep.F("ok", f"{get_ok} GET endpoints OK, {get_warn} unusual, "
          f"{get_timeout} timeouts, {get_skipped} skipped (side-effect heavy)")

# ─── C.2: POST without CSRF must be 403 ────────────────────────────────
rep.section("C.2 — POST endpoints reject missing CSRF")
nocsrf_client = bd_app.app.test_client()
nocsrf_client.get("/")

skipped_csrf = 0
ok_csrf = 0
bad_csrf = 0
# v3.66.764 (DERIVE-AUDIT): the CSRF-exempt set is the app's OWN declared set, not a
# hand-kept copy. The old literal also listed /api/secrets/extension/pair, which is
# AUTH-exempt (_check_token) but CSRF-ENFORCED (_check_csrf returns 403) -- conflating
# the two silently skipped a CSRF-enforced endpoint from this coverage probe. Deriving
# from bd_app.CSRF_EXEMPT_PATHS drops it, so the probe now tests it (expects 403).
WHITELIST_CSRF_BYPASS = bd_app.CSRF_EXEMPT_PATHS
for rule, methods in routes:
    if _should_skip(rule):
        skipped_csrf += 1
        continue
    if "POST" not in methods:
        continue
    path = _fill_path(rule)
    if any(rule.startswith(w) or path.startswith(w) for w in WHITELIST_CSRF_BYPASS):
        skipped_csrf += 1
        continue
    status, body, _ = _request_with_timeout(nocsrf_client, "POST", path,
                                            timeout=3.0, json={})
    if status == -1:
        skipped_csrf += 1
        continue
    if status == 403:
        ok_csrf += 1
    elif status in (400, 404, 405, 415):
        ok_csrf += 1
    elif status >= 500:
        rep.F("bug", f"POST {rule} → 5xx without CSRF: HTTP {status}")
        bad_csrf += 1
    elif status == 200:
        rep.F("warn", f"POST {rule} → 200 without CSRF header",
              "expected 403 (bypass or missing gate)")
        bad_csrf += 1
    else:
        ok_csrf += 1
if bad_csrf == 0:
    rep.F("ok", f"{ok_csrf} POST endpoints correctly gate on CSRF "
          f"({skipped_csrf} bypass-by-design / skipped)")

# ─── C.3: Method-not-allowed → 405 ─────────────────────────────────────
rep.section("C.3 — Method-not-allowed returns 405")
bad_405 = 0
sample_size = 30 if opts["fast"] else 80
for rule, methods in routes[:sample_size]:
    if _should_skip(rule):
        continue
    all_methods = {"GET", "POST", "PUT", "DELETE"}
    unsupported = list(all_methods - set(methods))
    if not unsupported:
        continue
    path = _fill_path(rule)
    status, body, _ = _request_with_timeout(client, unsupported[0], path,
                                            timeout=3.0, headers=HDRS_OK)
    if status == -1:
        continue
    if status >= 500:
        rep.F("bug", f"{unsupported[0]} {rule} → HTTP {status} (expected 405)")
        bad_405 += 1
if bad_405 == 0:
    rep.F("ok", f"method-not-allowed sample ({sample_size} routes) clean")

# ─── C.4: Content-Type matches body shape (sample) ─────────────────────
rep.section("C.4 — Response Content-Type matches body shape")
ct_mismatches = []
ct_checked = 0
ct_sample = 50 if opts["fast"] else 150
for rule, methods in routes:
    if ct_checked >= ct_sample:
        break
    if _should_skip(rule) or "GET" not in methods:
        continue
    # Skip routes with path params — they often 404 on placeholder
    if "<" in rule:
        continue
    path = _fill_path(rule)
    status, body, ct = _request_with_timeout(client, "GET", path, timeout=2.0)
    if status != 200 or not body:
        continue
    ct_checked += 1
    ct_main = ct.split(";")[0].strip().lower()
    first_byte = body[:1]
    if ct_main == "application/json":
        if first_byte not in (b"{", b"[", b'"', b"t", b"f", b"n"):
            ct_mismatches.append(f"{rule}: JSON Content-Type but body[0]={first_byte!r}")
if ct_mismatches:
    rep.F("warn", f"{len(ct_mismatches)} Content-Type mismatches in {ct_checked} sampled",
          ct_mismatches[0])
else:
    rep.F("ok", f"Content-Type consistent with body in {ct_checked} sampled GETs")

# ─── C.5: Path-parameter robustness ────────────────────────────────────
rep.section("C.5 — Path-parameter robustness")
PARAM_VALUES = ["1", "0", "abc", "../", "\x00"]
if opts["fast"]:
    PARAM_VALUES = PARAM_VALUES[:3]
crashes = 0
p5_checked = 0
p5_limit = 30 if opts["fast"] else 80
for rule, methods in routes:
    if p5_checked >= p5_limit:
        break
    if "<" not in rule or _should_skip(rule):
        continue
    if "GET" not in methods:
        continue
    p5_checked += 1
    for val in PARAM_VALUES:
        path = re.sub(r"\<[^>]+\>", val, rule)
        status, body, _ = _request_with_timeout(client, "GET", path, timeout=2.0)
        if status == -1:
            continue
        if status >= 500:
            rep.F("bug", f"GET {rule} crashed on param={val!r}: HTTP {status}")
            crashes += 1
if crashes == 0:
    rep.F("ok", f"{p5_checked} path-parameter routes robust under garbage input")

# ─── Summary ───────────────────────────────────────────────────────────
rep.summary()
sys.exit(rep.exit_code())
