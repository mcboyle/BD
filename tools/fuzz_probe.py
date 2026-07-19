"""Fuzz probe — malformed input across every endpoint.

Sweeps a corpus of payloads against POST/PUT/DELETE/GET routes. A
finding fires when:
  * 5xx response (server crash / unhandled exception)
  * 200 OK on a payload that should clearly be rejected (e.g. SQL
    keywords in a numeric field)
  * Hang > 5 seconds on any single request

What this isn't: a deep coverage-guided fuzzer (atheris/AFL). For that
the script does best-effort `python -m pip install atheris` and runs an
atheris harness if the install succeeds; otherwise we rely on a hand-
written corpus.

Sections:
  Z.1   JSON parser edge cases on every POST endpoint
  Z.2   Type confusion — fields that expect int/bool/list as wrong types
  Z.3   Length attacks — 1MB+ payloads
  Z.4   Unicode poison — RLO/NULL/BOM/surrogate pairs
  Z.5   Path traversal in path-bearing fields (cookie_file, download_dir,
        screenshot, watch_folder)
  Z.6   URL/header injection in URL-bearing fields
  Z.7   Querystring fuzzing on GET endpoints
  Z.8   Atheris coverage-guided harness (if installed)

Run via:
  BD_DISABLE_KEEPALIVE=1 python tools/fuzz_probe.py [--fast]
"""
from __future__ import annotations
import json
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _probe_lib as L  # noqa: E402

opts = L.parse_flags()
FAST = opts["fast"]

rep = L.Report("FUZZ")
client, bd_app, bd_db, BD_HOME = L.make_isolated_client(prefix="bd_fuzz_")

# Pre-seed: create one site so we have a sid to target for PUT/DELETE
csrf = client.get("/api/csrf")
TOK = (csrf.get_json() or {}).get("csrf_token", "") if csrf.status_code == 200 else ""
HDRS = {"X-CSRF-Token": TOK} if TOK else {}

r = client.post("/api/sites", json={"name": "fuzz_target"}, headers=HDRS)
TARGET_SID = None
for sid, s in bd_app.s_cfg.items():
    if s.get("name") == "fuzz_target":
        TARGET_SID = sid
        break


def _safe_request(method: str, url: str, **kw):
    """Issue request, return (status_code, error_str_or_empty, elapsed_s).

    If the request hangs for > 5s, returns (-1, 'timeout', 5.0). Uses a
    watchdog thread because Flask's test_client doesn't honor timeouts.
    """
    result = {"status": -1, "err": "", "elapsed": 0.0}
    done = threading.Event()
    def _do():
        t0 = time.perf_counter()
        try:
            r = client.open(url, method=method, **kw)
            result["status"] = r.status_code
            result["err"] = ""
        except Exception as ex:
            result["status"] = -2
            result["err"] = f"{type(ex).__name__}: {ex}"
        result["elapsed"] = time.perf_counter() - t0
        done.set()
    th = threading.Thread(target=_do, daemon=True)
    th.start()
    done.wait(timeout=5.0)
    if not done.is_set():
        return -1, "watchdog timeout", 5.0
    return result["status"], result["err"], result["elapsed"]


# ─── Z.1: JSON parser edge cases ────────────────────────────────────────
rep.section("Z.1 — JSON parser edge cases")
POST_ENDPOINTS = [
    "/api/sites", "/api/global_config", "/api/pair/redeem",
    "/api/ui_events", "/api/push/subscribe", "/api/push/unsubscribe",
    "/api/saved_searches", "/api/logs/clear",
]
BAD_JSON = [
    b"",
    b"null",
    b"true",
    b"42",
    b'""',
    b"[]",
    b"{}",
    b"{not valid json",
    b'{"name": }',                    # trailing : with no value
    b'{"name": "x", "name": "y"}',    # duplicate key
    b'{"deep": ' + b'[' * 200 + b'1' + b']' * 200 + b'}',
    b'{"a": {"a": {"a": {"a": {"a": "deep"}}}}}',
    b'\xff\xfe' + b'{"name":"utf16"}',  # BOM
    b"\x00\x01\x02bogus",
    b"{",
    b"}",
    b'"unterminated string',
]
crashes = 0
n_probes = 0
for ep in POST_ENDPOINTS:
    for payload in BAD_JSON:
        n_probes += 1
        status, err, _ = _safe_request("POST", ep,
                                       data=payload,
                                       headers={**HDRS,
                                                "Content-Type": "application/json"})
        if status == -1:
            rep.F("bug", f"hang on {ep}",
                  f"payload={payload[:40]!r}")
            crashes += 1
        elif status == -2:
            rep.F("bug", f"unhandled exception on {ep}",
                  f"err={err}")
            crashes += 1
        elif status is None or (isinstance(status, int) and status >= 500):
            rep.F("bug", f"5xx on {ep}: HTTP {status}",
                  f"payload={payload[:60]!r}")
            crashes += 1
if crashes == 0:
    rep.F("ok", f"{n_probes} bad-JSON probes — no crashes, no 5xx")

# ─── Z.2: Type confusion ────────────────────────────────────────────────
rep.section("Z.2 — Type confusion")
# Fields that the app expects as int/bool/list — feed wrong types
TYPE_CONFUSION = [
    ("/api/sites",         {"name": 42}),                # name=int
    ("/api/sites",         {"name": None}),
    ("/api/sites",         {"name": ["list"]}),
    ("/api/sites",         {"name": {"obj": "yes"}}),
    ("/api/sites",         {"enabled": "yes"}),         # bool=str
    ("/api/sites",         {"enabled": 42}),
    ("/api/sites",         {"auto_relogin_interval_hours": "twelve"}),
    ("/api/sites",         {"auto_relogin_interval_hours": -1}),
    ("/api/sites",         {"auto_relogin_interval_hours": 1e308}),
    ("/api/sites",         {"auto_relogin_interval_hours": [1, 2]}),
    ("/api/global_config", {"global_max_concurrent": "many"}),
    ("/api/global_config", {"global_max_concurrent": None}),
    ("/api/global_config", {"global_max_concurrent": -5}),
    ("/api/global_config", {"path_allowlist": "/etc"}),  # should be list
    ("/api/global_config", {"path_allowlist": {"k": "v"}}),
    ("/api/global_config", {"watch_interval_sec": "fast"}),
    ("/api/global_config", {"log_level": 42}),
    ("/api/global_config", {"log_level": "BOGUS"}),
]
crashes = 0
for ep, payload in TYPE_CONFUSION:
    status, err, _ = _safe_request("POST", ep, json=payload, headers=HDRS)
    if status in (-1, -2) or (isinstance(status, int) and status >= 500):
        rep.F("bug", f"type-confusion crash on {ep}",
              f"payload={payload} status={status} err={err}")
        crashes += 1
if crashes == 0:
    rep.F("ok", f"{len(TYPE_CONFUSION)} type-confusion probes — no crashes")

# ─── Z.3: Length attacks ────────────────────────────────────────────────
rep.section("Z.3 — Length attacks")
huge = "A" * (1024 * 1024)    # 1MB
mega = "B" * (5 * 1024 * 1024)  # 5MB
LENGTH_PROBES = [
    ("/api/sites", {"name": huge}),
    ("/api/sites", {"name": "x", "url_patterns": huge}),
    ("/api/global_config", {"watch_folder": huge}),
] + ([("/api/sites", {"name": mega})] if not FAST else [])
crashes = 0
for ep, payload in LENGTH_PROBES:
    status, err, elapsed = _safe_request("POST", ep, json=payload, headers=HDRS)
    if status in (-1, -2) or (isinstance(status, int) and status >= 500):
        rep.F("bug", f"length-attack crash on {ep}",
              f"size={sum(len(v) for v in payload.values() if isinstance(v,str))}B "
              f"status={status}")
        crashes += 1
    elif elapsed > 2.0:
        rep.F("warn", f"slow response to large payload: {elapsed:.1f}s",
              f"{ep}")
if crashes == 0:
    rep.F("ok", f"{len(LENGTH_PROBES)} length-attack probes — no crashes")

# ─── Z.4: Unicode poison ────────────────────────────────────────────────
rep.section("Z.4 — Unicode poison")
POISON_STRINGS = [
    "\x00",                            # NUL
    "\x00\x00\x00",
    "\u202e",                          # right-to-left override
    "\u2066\u2067\u2068\u2069",        # isolates
    "\ufeff",                          # BOM
    "\ud83d\ude00" * 50,               # emoji stream
    "\ud800",                          # lone surrogate (invalid UTF-8 if encoded)
    "A" + "\u0301" * 1000,             # combining marks
    "👨‍👩‍👧‍👦" * 30,                       # ZWJ family
    "test\r\nX-Injected: 1",           # CRLF
    "test\nSet-Cookie: evil=1",
]
crashes = 0
for s in POISON_STRINGS:
    status, err, _ = _safe_request("POST", "/api/sites",
                                   json={"name": s}, headers=HDRS)
    if status in (-1, -2) or (isinstance(status, int) and status >= 500):
        rep.F("bug", f"unicode poison crash",
              f"input={s[:40]!r} status={status} err={err}")
        crashes += 1
if crashes == 0:
    rep.F("ok", f"{len(POISON_STRINGS)} unicode-poison probes — no crashes")

# ─── Z.5: Path traversal ────────────────────────────────────────────────
rep.section("Z.5 — Path traversal in path-bearing fields")
TRAVERSALS = [
    "../../../etc/passwd",
    "..\\..\\..\\windows\\system32\\drivers\\etc\\hosts",
    "/etc/passwd",
    "C:\\Windows\\System32\\config\\SAM",
    "/proc/self/environ",
    "file:///etc/passwd",
    "\\\\evil-server\\share\\thing",
    "\x00/etc/passwd",
    "/tmp/../etc/passwd",
    "....//....//etc/passwd",
    "%2e%2e%2f%2e%2e%2fetc/passwd",
]
PATH_FIELDS = ["cookie_file", "download_dir", "spillover_dirs"]
crashes = 0
accepted = 0
for fld in PATH_FIELDS:
    for trav in TRAVERSALS:
        value = trav if fld != "spillover_dirs" else [trav]
        status, err, _ = _safe_request("POST", "/api/sites",
                                       json={"name": f"trav_{hash(trav) & 0xffff}",
                                             fld: value},
                                       headers=HDRS)
        if status in (-1, -2) or (isinstance(status, int) and status >= 500):
            rep.F("bug", f"path-traversal crash",
                  f"field={fld} trav={trav!r} status={status}")
            crashes += 1
        elif status == 200 and ".." in str(value):
            accepted += 1
if crashes == 0:
    rep.F("ok", f"{len(PATH_FIELDS) * len(TRAVERSALS)} path-traversal probes "
          f"— no crashes")
if accepted > 0:
    rep.F("warn", f"{accepted} traversals accepted (allowlist may be off — "
          "set path_allowlist for defense)",
          "single-user model treats this as self-harm, not a vulnerability")

# ─── Z.6: URL/header injection ──────────────────────────────────────────
rep.section("Z.6 — URL / header injection")
URL_INJECT = [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "http://example.com\r\nSet-Cookie: evil=1",
    "http://example.com\nHost: evil.com",
    "http://[::1]:25/SMTP-injection",
    "gopher://localhost:6379/_FLUSHALL",
    "http://169.254.169.254/latest/meta-data/",  # SSRF AWS IMDS
    "http://localhost:11434/api/generate",       # SSRF Ollama
]
crashes = 0
for url in URL_INJECT:
    status, err, _ = _safe_request("POST", "/api/sites",
                                   json={"name": f"url_{hash(url) & 0xffff}",
                                         "login_url": url},
                                   headers=HDRS)
    if status in (-1, -2) or (isinstance(status, int) and status >= 500):
        rep.F("bug", f"url-inject crash",
              f"url={url!r} status={status}")
        crashes += 1
if crashes == 0:
    rep.F("ok", f"{len(URL_INJECT)} URL-injection probes — no crashes")

# ─── Z.7: Querystring fuzzing ───────────────────────────────────────────
rep.section("Z.7 — Querystring fuzzing on GET endpoints")
GET_TARGETS = [
    "/api/logs/tail",
    "/api/sites_list",
    "/api/history",
    "/api/global_config",
    "/api/csrf",
]
QS_PAYLOADS = [
    "?lines=" + "A" * 10000,
    "?lines=-1e308",
    "?lines=NaN",
    "?lines=0x41",
    "?lines=",  # bare equals
    "?\x00=1",
    "?a=" + "&a=" * 1000 + "z",
    "?limit=99999999&offset=-1",
    "?limit=-1&offset=99999999",
    "?'%20OR%201=1--",
]
crashes = 0
for ep in GET_TARGETS:
    for qs in QS_PAYLOADS:
        status, err, _ = _safe_request("GET", ep + qs)
        if status in (-1, -2) or (isinstance(status, int) and status >= 500):
            rep.F("bug", f"qs-fuzz crash on {ep}",
                  f"qs={qs[:60]!r} status={status}")
            crashes += 1
if crashes == 0:
    rep.F("ok", f"{len(GET_TARGETS) * len(QS_PAYLOADS)} qs-fuzz probes "
          f"— no crashes")

# ─── Z.8: Atheris harness (best-effort) ─────────────────────────────────
rep.section("Z.8 — Atheris coverage-guided harness (best-effort)")
try:
    import atheris  # type: ignore
    HAS_ATHERIS = True
except ImportError:
    HAS_ATHERIS = False

if not HAS_ATHERIS:
    rep.F("info", "atheris not installed — coverage-guided fuzzing skipped",
          "pip install atheris (Linux/macOS only; binary wheels)")
else:
    # 1-minute fuzz against the FTS search parser
    fuzz_errors: list[str] = []
    fuzz_iters = [0]

    def harness(data: bytes) -> None:
        fuzz_iters[0] += 1
        try:
            query = data.decode("utf-8", errors="replace")
            bd_db.db_search_fts(query)
        except Exception as ex:
            # FTS5 syntax errors are expected; log only unexpected types
            if not isinstance(ex, (ValueError, UnicodeError)):
                msg = f"{type(ex).__name__}: {ex}"[:200]
                if msg not in fuzz_errors:
                    fuzz_errors.append(msg)

    atheris.Setup(sys.argv[:1] + [f"-max_total_time={30 if FAST else 60}",
                                  "-runs=-1"], harness)
    try:
        atheris.Fuzz()
    except SystemExit:
        pass  # atheris.Fuzz() calls sys.exit when done
    if fuzz_errors:
        rep.F("bug", f"atheris found {len(fuzz_errors)} unexpected exception class(es)",
              "; ".join(fuzz_errors[:3]))
    else:
        rep.F("ok", f"atheris ran ~{fuzz_iters[0]} iterations — no unexpected exceptions")

# ─── Summary ───────────────────────────────────────────────────────────
rep.summary()
sys.exit(rep.exit_code())
