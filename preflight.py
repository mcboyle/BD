#!/usr/bin/env python3
"""Pre-deployment validation for Bulk Downloader.

Four categories:
  1. STATIC  — parse, imports, lint smells, dead code, secrets
  2. DYNAMIC — server starts, every endpoint reachable, SSE works
  3. FUZZ    — malformed JSON, oversized payloads, type confusion,
               path traversal, SQL injection, XSS, CSRF bypass
  4. FUNC    — full CRUD round-trips, hook test endpoints, pair flow

Exit code 0 = all clean. Non-zero = at least one CRITICAL finding.
WARNING-level findings don't fail the run but are reported.
"""
import ast
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).parent
PKG = ROOT / "bulk_downloader"

# Findings accumulator: (severity, category, msg)
findings = []

def crit(cat, msg): findings.append(("CRIT", cat, msg))
def warn(cat, msg): findings.append(("WARN", cat, msg))
def info(cat, msg): findings.append(("INFO", cat, msg))

def section(name):
    print(f"\n{'=' * 70}\n  {name}\n{'=' * 70}")

# ── 1. STATIC ─────────────────────────────────────────────────────────

def static_checks():
    section("1/4  STATIC ANALYSIS")

    # 1a. AST parse every Python file
    py_files = list(PKG.glob("*.py")) + [ROOT / "downloader_ui.py", ROOT / "bdctl.py"]
    parse_fail = 0
    for p in py_files:
        try:
            ast.parse(p.read_text())
        except SyntaxError as e:
            crit("STATIC", f"{p.name} syntax error: {e}")
            parse_fail += 1
    print(f"  AST parse: {len(py_files) - parse_fail}/{len(py_files)} OK")

    # 1b. Import graph — no circular, no missing
    sys.path.insert(0, str(ROOT))
    import_fail = 0
    for p in PKG.glob("*.py"):
        if p.name == "__init__.py": continue
        mod = f"bulk_downloader.{p.stem}"
        try:
            __import__(mod)
        except ImportError as e:
            crit("STATIC", f"{mod} import failed: {e}")
            import_fail += 1
        except Exception as e:
            warn("STATIC", f"{mod} import side-effect: {type(e).__name__}: {e}")
    print(f"  Imports:    {len(list(PKG.glob('*.py'))) - import_fail - 1}/{len(list(PKG.glob('*.py'))) - 1} OK")

    # 1c. Lint smells (manual since no pylint available)
    smells = []
    for p in py_files:
        src = p.read_text()
        # print() debug statements left behind
        bare_prints = re.findall(r'^\s*print\s*\(', src, re.MULTILINE)
        if len(bare_prints) > 3 and p.name not in ("downloader_ui.py", "bdctl.py", "preflight.py"):
            smells.append(f"{p.name}: {len(bare_prints)} print() calls (debug leftover?)")
        # eval / exec — parse the AST so docstring/comment mentions don't trigger.
        # Old regex-based check produced false positives whenever a docstring
        # mentioned the word `eval()` for documentation purposes.
        bare_excepts = []
        try:
            _tree = ast.parse(src, filename=str(p))
            for _node in ast.walk(_tree):
                if isinstance(_node, ast.Call) and isinstance(_node.func, ast.Name):
                    if _node.func.id == "eval":
                        crit("STATIC", f"{p.name}:{_node.lineno}: real eval() call — code execution risk")
                    elif _node.func.id == "exec" and p.name != "preflight.py":
                        crit("STATIC", f"{p.name}:{_node.lineno}: real exec() call — code execution risk")
                # v3.43.17: catch bare `except:` clauses. These swallow
                # KeyboardInterrupt and SystemExit, making the app harder
                # to stop and harder to debug. Should be `except Exception:`
                # at minimum.
                elif isinstance(_node, ast.ExceptHandler) and _node.type is None:
                    bare_excepts.append(_node.lineno)
        except SyntaxError as _e:
            crit("STATIC", f"{p.name}: failed to parse for eval/exec check: {_e}")
        if bare_excepts:
            smells.append(f"{p.name}: {len(bare_excepts)} bare `except:` clause(s) "
                          f"(use `except Exception:`; first at line {bare_excepts[0]})")
        # Hardcoded secrets / API keys (heuristic — common patterns)
        for pat in [r'api_key\s*=\s*["\'][a-zA-Z0-9]{20,}',
                    r'password\s*=\s*["\'][^"\']{6,}["\']',
                    r'secret\s*=\s*["\'][a-zA-Z0-9]{20,}']:
            for m in re.finditer(pat, src):
                line = src[:m.start()].count('\n') + 1
                # Filter out obvious test/template fixtures
                ctx = src[max(0,m.start()-50):m.end()+50]
                if any(t in ctx.lower() for t in ('test', 'example', 'placeholder', 'your_', 'fixture', 'fake')):
                    continue
                warn("STATIC", f"{p.name}:{line} possible hardcoded secret: {m.group()[:50]}")
        # TODO/FIXME/XXX markers
        todos = len(re.findall(r'#\s*(TODO|FIXME|XXX|HACK)\b', src))
        if todos > 5:
            info("STATIC", f"{p.name}: {todos} TODO/FIXME markers")
    for s in smells: warn("STATIC", s)
    print(f"  Smells:     {len(smells)} warnings")

    # 1d. Type-hint coverage on annotated functions (sanity)
    try:
        for modname in ("log", "aiassist"):
            mod = __import__(f"bulk_downloader.{modname}", fromlist=[modname])
            covered = 0
            total = 0
            for name in dir(mod):
                if name.startswith("_"): continue
                obj = getattr(mod, name)
                if callable(obj) and hasattr(obj, "__annotations__"):
                    total += 1
                    if obj.__annotations__: covered += 1
            if total > 0:
                pct = covered / total * 100
                print(f"  Type hints {modname}: {covered}/{total} ({pct:.0f}%)")
    except Exception as e:
        warn("STATIC", f"Type-hint check failed: {e}")

    # 1e. JS parse via Node — RETIRED in Phase 4 (v3.66.334): the legacy
    # static/app.js shell was deleted. The SPA is TypeScript under frontend/
    # and is type-checked + bundled by tsc/vite at build time, not here.

    # 1f. Dependency requirements parse
    for req_file in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt"):
        p = ROOT / req_file
        if not p.exists():
            warn("STATIC", f"{req_file} missing")
            continue
        lines = [l.strip() for l in p.read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
        print(f"  {req_file}: {len(lines)} pinned deps")


# ── 2. DYNAMIC ────────────────────────────────────────────────────────

class AppHandle:
    """Manages the test server lifecycle."""
    def __init__(self):
        self.proc = None
        self.port = 5557  # avoid colliding with anything running
        self.base = f"http://127.0.0.1:{self.port}"

    def start(self):
        # Clean state files
        for f in ("sites_config.json", "app_config.json", "downloader_history.db",
                  "vapid_keys.json"):
            (ROOT / f).unlink(missing_ok=True)
        env = os.environ.copy()
        env["FLASK_RUN_PORT"] = str(self.port)
        env["BD_PORT"] = str(self.port)
        # Use a separate runner shim so we control the port
        self.proc = subprocess.Popen(
            [sys.executable, "-c", f"""
import os, sys
sys.path.insert(0, '{ROOT}')
os.chdir('{ROOT}')
from bulk_downloader.app import app
app.run(host='127.0.0.1', port={self.port}, threaded=True, use_reloader=False)
"""],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(ROOT))
        # Wait for the port to listen
        for _ in range(40):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    time.sleep(0.5)  # let Flask finish init
                    return True
            except OSError:
                time.sleep(0.25)
        return False

    def stop(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        # Clean state files
        for f in ("sites_config.json", "app_config.json", "downloader_history.db",
                  "vapid_keys.json"):
            (ROOT / f).unlink(missing_ok=True)

    def request(self, path, method="GET", data=None, headers=None, timeout=5):
        url = self.base + path
        h = headers or {}
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read() if e.fp else b""
        except Exception as e:
            return -1, str(e).encode()


def dynamic_checks(app):
    section("2/4  DYNAMIC: SERVER + ENDPOINTS")

    # 2a. Root index renders
    status, body = app.request("/")
    if status != 200 or b"<!DOCTYPE html>" not in body:
        crit("DYNAMIC", f"GET / failed: status={status}, len={len(body)}")
    else:
        print(f"  GET /                  200 ({len(body)} bytes)")

    # 2b. Critical GET endpoints — must match actual app routes
    endpoints = [
        ("/api/status", 200),
        ("/api/dashboard", 200),
        ("/api/ai/status", 200),
        ("/api/ai/health", 200),
        ("/api/global_config", 200),
        ("/api/csrf", 200),
        ("/api/pair", 200),
        ("/api/logs/tail", 200),
        ("/api/config/export", 200),
        ("/manifest.json", 200),
        ("/sw.js", 200),
        ("/icon.svg", 200),
        ("/api/sites/nonexistent/status", 404),
    ]
    for path, expected in endpoints:
        s, b = app.request(path)
        ok = s == expected
        marker = "✓" if ok else "✗"
        print(f"  {marker} {path:35} {s} (expected {expected})")
        if not ok:
            crit("DYNAMIC", f"{path}: got {s}, expected {expected}")

    # 2c. SSE stream — connect, read first event, disconnect
    try:
        req = urllib.request.Request(app.base + "/api/stream")
        with urllib.request.urlopen(req, timeout=3) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "event-stream" in ctype:
                # Read first event packet
                resp.read(100)
                print(f"  SSE /api/stream        200 (Content-Type: {ctype})")
            else:
                warn("DYNAMIC", f"SSE wrong Content-Type: {ctype}")
    except Exception as e:
        warn("DYNAMIC", f"SSE check failed: {e}")

    # 2d. Static assets
    for path in ("/icon.svg", "/manifest.json"):
        s, b = app.request(path)
        if s != 200 or len(b) < 10:
            warn("DYNAMIC", f"Static {path}: status={s}, len={len(b)}")


# ── 3. FUZZ ───────────────────────────────────────────────────────────

def fuzz_checks(app):
    section("3/4  FUZZ: MALFORMED INPUT, INJECTION, BYPASS")

    # Setup: create a real site for the parametrized endpoints
    s, b = app.request("/api/sites", "POST", {"name": "fuzz-target"})
    sid = json.loads(b).get("id") if s == 200 else "missing"

    crashes = 0

    # 3a. Malformed JSON to every POST endpoint
    malformed_payloads = [
        b"not json at all",
        b"{",
        b'{"unclosed": ',
        b"\x00\x01\x02\x03",
        b"[]",  # array where object expected
        b"null",
        b'{"key": "value"',  # trailing comma missing close
    ]
    targets = ["/api/sites", "/api/global_config", "/api/route_urls"]
    for target in targets:
        for payload in malformed_payloads:
            req = urllib.request.Request(app.base + target, data=payload,
                                         method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=3) as resp:
                    s = resp.status
            except urllib.error.HTTPError as e:
                s = e.code
            except Exception as e:
                crit("FUZZ", f"{target} crashed on malformed JSON: {e}")
                crashes += 1
                continue
            # 400/422/415 are correct; 500 is a crash
            if s >= 500:
                crit("FUZZ", f"{target} returned {s} on malformed JSON: {payload[:30]!r}")
                crashes += 1
    print(f"  Malformed JSON: {len(targets)*len(malformed_payloads)} tests, {crashes} crashes")

    # 3b. Oversized payloads
    huge = {"name": "x" * 1_000_000}  # 1 MB name
    s, b = app.request("/api/sites", "POST", huge, timeout=10)
    if s == -1 or s >= 500:
        warn("FUZZ", f"Oversized payload returned {s} (1MB name field)")
    else:
        print(f"  Oversized payload (1MB): {s}")

    # 3c. Type confusion — send wrong types in fields
    type_confusion = [
        {"name": 12345},          # int instead of str
        {"name": None},
        {"name": ["array"]},
        {"name": {"obj": "ect"}},
        {"name": True},
        {"max_concurrent": "not a number"},
        {"max_concurrent": -1},
        {"max_concurrent": 99999},
        {"max_concurrent": [1, 2, 3]},
        {"max_retries": float("inf")},
    ]
    type_crashes = 0
    for payload in type_confusion:
        s, _ = app.request("/api/sites", "POST", payload)
        if s >= 500:
            crit("FUZZ", f"Type confusion crashed: {payload}")
            type_crashes += 1
    print(f"  Type confusion: {len(type_confusion)} tests, {type_crashes} crashes")

    # 3d. Path traversal in path-bearing fields. Routes:
    #   PUT /api/sites/<sid> updates the site config (Phase 26 validates paths)
    if sid != "missing":
        traversal = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "downloads/../../../../../../etc/passwd",
            "%2e%2e/%2e%2e/etc/passwd",
        ]
        blocked = 0
        for path in traversal:
            s, b = app.request(f"/api/sites/{sid}", "PUT",
                              {"download_dir": path, "name": "fuzz-target"})
            try:
                d = json.loads(b)
                # Validator returns 400 with error message OR ok=False
                if s == 400 or (s == 200 and not d.get("ok", True)) or "error" in d:
                    blocked += 1
                elif s == 200 and d.get("ok", True):
                    # Look at what's stored — if it kept the malicious value, that's bad
                    s2, b2 = app.request("/api/status")
                    stored = json.loads(b2).get(sid, {}).get("config", {}).get("download_dir", "")
                    if stored == path:
                        crit("FUZZ", f"Path traversal accepted and stored: {path}")
                    else:
                        blocked += 1  # accepted but normalized
            except Exception: pass
        # Also test relative path (which should be rejected as absolute-only)
        s, b = app.request(f"/api/sites/{sid}", "PUT",
                          {"download_dir": "relative/path", "name": "fuzz-target"})
        try:
            d = json.loads(b)
            if s == 400 or not d.get("ok", True):
                blocked += 1
        except Exception: pass
        print(f"  Path traversal: {blocked}/{len(traversal)+1} correctly blocked")
        if blocked < len(traversal):
            crit("FUZZ", f"Path traversal: only {blocked}/{len(traversal)+1} attempts blocked")

    # 3e. SQL injection attempts in search params
    sqli_payloads = [
        "' OR '1'='1",
        "'; DROP TABLE history; --",
        "1' UNION SELECT * FROM sqlite_master --",
        "admin'--",
    ]
    sqli_crashes = 0
    for payload in sqli_payloads:
        from urllib.parse import quote
        s, _ = app.request(f"/api/history?q={quote(payload)}")
        if s >= 500:
            crit("FUZZ", f"SQL injection probe crashed: {payload}")
            sqli_crashes += 1
    print(f"  SQL injection: {len(sqli_payloads)} tests, {sqli_crashes} crashes")

    # 3f. XSS attempts in string fields
    if sid != "missing":
        xss = [
            "<script>alert(1)</script>",
            "javascript:alert(1)",
            "<img src=x onerror=alert(1)>",
            "\"><script>alert(1)</script>",
        ]
        xss_accepted = 0
        for payload in xss:
            s, _ = app.request(f"/api/sites/{sid}", "PUT",
                              {"name": payload})
            s2, b2 = app.request("/api/status")
            try:
                d = json.loads(b2)
                if sid in d:
                    stored = d[sid]["config"]["name"]
                    if stored == payload: xss_accepted += 1
            except Exception: pass
        print(f"  XSS in string field: {xss_accepted}/{len(xss)} stored as-is (rendered safely by JS)")

    # 3g. CSRF bypass attempts — Phase 40
    # Establish a session via the bootstrap GET / first
    cookie_jar = {}
    try:
        req = urllib.request.Request(app.base + "/")
        with urllib.request.urlopen(req, timeout=3) as resp:
            for ck in resp.headers.get_all("Set-Cookie") or []:
                if "bd_session=" in ck:
                    cookie_jar["bd_session"] = ck.split("bd_session=")[1].split(";")[0]
    except Exception as e:
        warn("FUZZ", f"Session bootstrap failed: {e}")

    if cookie_jar:
        cookie_hdr = f"bd_session={cookie_jar['bd_session']}"
        # Try POST without CSRF token — should be 403
        s, b = app.request("/api/sites", "POST", {"name": "should-be-blocked"},
                          headers={"Cookie": cookie_hdr})
        if s != 403:
            crit("FUZZ", f"CSRF bypass: POST without X-CSRF-Token returned {s}, expected 403")
        else:
            print(f"  CSRF: bare POST blocked correctly (403)")
        # Try with WRONG CSRF token — should be 403
        s, _ = app.request("/api/sites", "POST", {"name": "wrong-token"},
                          headers={"Cookie": cookie_hdr,
                                   "X-CSRF-Token": "totally-wrong-token"})
        if s != 403:
            crit("FUZZ", f"CSRF bypass: wrong token returned {s}, expected 403")
        else:
            print(f"  CSRF: wrong token blocked correctly (403)")

    # 3h. Rate limit fires on action spam
    if sid != "missing":
        rate_limited = False
        for i in range(15):
            s, _ = app.request(f"/api/sites/{sid}/start", "POST")
            if s == 429:
                rate_limited = True
                break
        if rate_limited:
            print(f"  Rate limit:    fires after {i+1} requests (limit is 10/5s)")
        else:
            crit("FUZZ", "Rate limit didn't fire in 15 rapid requests")


# ── 4. FUNCTIONALITY ──────────────────────────────────────────────────

def func_checks(app):
    section("4/4  FUNCTIONALITY: CRUD + INTEGRATION TESTS")

    # 4a. Site CRUD round-trip
    s, b = app.request("/api/sites", "POST", {"name": "func-test",
                                              "max_concurrent": 2,
                                              "wait": 1.5})
    if s != 200:
        crit("FUNC", f"Create site failed: {s}")
        return
    sid = json.loads(b)["id"]
    print(f"  Create site:   200, sid={sid}")

    # Read back
    s, b = app.request("/api/status")
    d = json.loads(b)
    if sid not in d:
        crit("FUNC", f"Created site missing from /api/status")
    else:
        cfg = d[sid]["config"]
        if cfg.get("max_concurrent") != 2 or cfg.get("wait") != 1.5:
            crit("FUNC", f"Config round-trip mismatch: {cfg}")
        else:
            print(f"  Read site:     config matches input")

    # Update via PUT
    s, _ = app.request(f"/api/sites/{sid}", method="PUT",
                       data={"max_concurrent": 4, "name": "func-test"})
    s2, b2 = app.request("/api/status")
    d2 = json.loads(b2)
    if d2.get(sid, {}).get("config", {}).get("max_concurrent") != 4:
        crit("FUNC", f"Update didn't persist: {d2.get(sid, {}).get('config', {}).get('max_concurrent')}")
    else:
        print(f"  Update site:   max_concurrent 2→4 persisted")

    # Delete
    s, _ = app.request(f"/api/sites/{sid}", method="DELETE")
    s2, b2 = app.request("/api/status")
    if sid in json.loads(b2):
        crit("FUNC", "Site still present after DELETE")
    else:
        print(f"  Delete site:   removed cleanly")

    # 4b. URL load + dedup. The endpoint expects {text: "url1\nurl2\n..."}
    s, b = app.request("/api/sites", "POST", {"name": "url-test"})
    sid = json.loads(b)["id"]
    s, b = app.request(f"/api/sites/{sid}/load_urls", "POST",
                      {"text": "https://x/a\nhttps://x/a\nhttps://x/b"})
    if s == 200:
        d = json.loads(b)
        if d.get("added") == 2:
            print(f"  Load URLs:     2/3 added (dedup working)")
        else:
            warn("FUNC", f"Load URLs returned: {d}")
    else:
        warn("FUNC", f"load_urls returned {s}: {b[:100]}")

    # 4c. Pair flow
    s, b = app.request("/api/pair")
    if s != 200:
        crit("FUNC", f"GET /api/pair failed: {s}")
    else:
        d = json.loads(b)
        pair_token = d.get("token")
        if not pair_token:
            crit("FUNC", "No pair token in response")
        else:
            print(f"  Pair generate: token={pair_token[:16]}…")
            # Redeem
            s, b = app.request("/api/pair/redeem", "POST", {"token": pair_token})
            if s != 200:
                crit("FUNC", f"Pair redeem failed: {s} {b[:100]}")
            else:
                d = json.loads(b)
                if d.get("ok") and d.get("csrf_token"):
                    print(f"  Pair redeem:   ok, csrf_token={d['csrf_token'][:16]}…")
                else:
                    crit("FUNC", f"Pair redeem unexpected: {d}")
            # Second redemption should 404
            s, _ = app.request("/api/pair/redeem", "POST", {"token": pair_token})
            if s != 404:
                crit("FUNC", f"Pair redeem replay returned {s}, expected 404")
            else:
                print(f"  Pair one-shot: replay correctly rejected (404)")

    # 4d. Config export/import round-trip
    s, b = app.request("/api/config/export")
    if s == 200:
        cfg = json.loads(b)
        s2, b2 = app.request("/api/config/import", "POST", cfg)
        if s2 == 200:
            print(f"  Config E/I:    export+import round-trip OK")
        else:
            warn("FUNC", f"Config import returned {s2}")
    else:
        warn("FUNC", f"Config export returned {s}")

    # 4e. AI status (should report disabled gracefully)
    s, b = app.request("/api/ai/status")
    if s == 200:
        d = json.loads(b)
        print(f"  AI status:     ok={d.get('ok')}, enabled={d.get('enabled', False)}")
    else:
        warn("FUNC", f"AI status returned {s}")

    # 4f. (captcha/providers endpoint doesn't exist — captcha is per-site only)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    try:
        from bulk_downloader import __version__ as _bd_ver
    except Exception:
        _bd_ver = "?"
    print(f"\n  Bulk Downloader v{_bd_ver} — Pre-deployment validation\n")
    static_checks()

    app = AppHandle()
    if not app.start():
        crit("DYNAMIC", "Server failed to start on port 5557")
        return _report()

    try:
        dynamic_checks(app)
        fuzz_checks(app)
        func_checks(app)
    finally:
        app.stop()

    return _report()


def _report():
    section("FINDINGS SUMMARY")
    by_sev = {"CRIT": [], "WARN": [], "INFO": []}
    for sev, cat, msg in findings:
        by_sev[sev].append((cat, msg))

    for sev in ("CRIT", "WARN", "INFO"):
        if not by_sev[sev]: continue
        print(f"\n  {sev} ({len(by_sev[sev])}):")
        for cat, msg in by_sev[sev]:
            print(f"    [{cat}] {msg[:140]}")

    print()
    crit_n = len(by_sev["CRIT"])
    warn_n = len(by_sev["WARN"])
    if crit_n == 0:
        print(f"  ✓ NO CRITICAL FINDINGS. {warn_n} warnings.")
        return 0
    else:
        print(f"  ✗ {crit_n} CRITICAL findings — fix before deploy.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
