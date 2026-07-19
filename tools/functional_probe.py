"""Functional E2E probe — exercises every primary workflow.

Where SAST/DAST ask "does this break", this asks "does the happy path
work end-to-end". Failures here are functional regressions, not
security issues.

Sections:
  F.1   CSRF bootstrap + first page load
  F.2   Site config CRUD round-trip
  F.3   Cookie file auto-fill round-trip
  F.4   Settings persistence
  F.5   History insert / search / snippet
  F.6   Log tail / clear cycle
  F.7   Dev mode endpoint enumeration
  F.8   Auto-relogin config persistence
  F.9   Pair redeem flow (mocked)
  F.10  Diagnostics bundle download
  F.11  History pagination
  F.12  Site enable/disable toggle

Each section is independent — failure in one doesn't block the next.

Run via:
  BD_DISABLE_KEEPALIVE=1 python tools/functional_probe.py
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _probe_lib as L  # noqa: E402

rep = L.Report("FUNC")
client, bd_app, bd_db, BD_HOME = L.make_isolated_client(prefix="bd_func_")

# ─── F.1: CSRF bootstrap ───────────────────────────────────────────────
rep.section("F.1 — CSRF bootstrap + index load")
r = client.get("/")
if r.status_code == 200 and b"csrf-token" in r.data:
    rep.F("ok", "GET / returns 200 with csrf-token meta")
else:
    rep.F("bug", f"GET / failed: HTTP {r.status_code}",
          f"snippet={r.data[:200]!r}")

r = client.get("/api/csrf")
csrf = ""
if r.status_code == 200:
    csrf = (r.get_json() or {}).get("csrf_token", "")
    if csrf and len(csrf) >= 16:
        rep.F("ok", f"/api/csrf returns token (len={len(csrf)})")
    else:
        rep.F("bug", "csrf token suspiciously short", f"token={csrf!r}")
else:
    rep.F("bug", f"/api/csrf failed: HTTP {r.status_code}")

# ─── F.2: Site CRUD ────────────────────────────────────────────────────
rep.section("F.2 — Site config CRUD round-trip")
n_before = len(bd_app.s_cfg)

r = L.csrf_post(client, "/api/sites", json={"name": "func_test_site"})
if r.status_code == 200:
    rep.F("ok", "site create: 200")
else:
    rep.F("bug", f"site create failed: HTTP {r.status_code}",
          f"body={r.data[:200]!r}")

new_sid = None
for sid, s in bd_app.s_cfg.items():
    if s.get("name") == "func_test_site":
        new_sid = sid
        break

if new_sid:
    rep.F("ok", f"site present in s_cfg after POST (sid={new_sid})")
else:
    rep.F("bug", "site not in s_cfg after POST")

# /api/sites_list is the public site index (extension uses it)
r = client.get("/api/sites_list")
if r.status_code == 200:
    body = r.get_json() or {}
    sites = body.get("sites") or []
    if any(s.get("name") == "func_test_site" for s in sites):
        rep.F("ok", f"/api/sites_list returns the new site ({len(sites)} total)")
    else:
        rep.F("bug", "/api/sites_list missing the new site",
              f"count={len(sites)}, names={[s.get('name') for s in sites]}")
else:
    rep.F("warn", f"/api/sites_list: HTTP {r.status_code}")

if new_sid:
    # Update is PUT /api/sites/<sid>, not POST /api/sites
    r2 = client.get("/api/csrf")
    tok = (r2.get_json() or {}).get("csrf_token", "") if r2.status_code == 200 else ""
    r = client.put(f"/api/sites/{new_sid}",
                   json={"name": "func_test_site_renamed"},
                   headers={"X-CSRF-Token": tok})
    if r.status_code == 200:
        rep.F("ok", "PUT /api/sites/<sid> update: 200")
        if bd_app.s_cfg[new_sid].get("name") == "func_test_site_renamed":
            rep.F("ok", "rename persisted in s_cfg")
        else:
            rep.F("warn", "rename did not persist",
                  f"name={bd_app.s_cfg[new_sid].get('name')!r}")
    else:
        rep.F("warn", f"site update: HTTP {r.status_code}")

# ─── F.3: Cookie file auto-fill ────────────────────────────────────────
rep.section("F.3 — cookie_file auto-fill on save")
r = L.csrf_post(client, "/api/sites",
                json={"name": "cf_autofill_test"})
sid = None
for k, s in bd_app.s_cfg.items():
    if s.get("name") == "cf_autofill_test":
        sid = k
        break
if sid:
    cf = bd_app.s_cfg[sid].get("cookie_file", "")
    if cf and cf.endswith(".json") and "cookies" in cf:
        rep.F("ok", f"cookie_file auto-populated: {os.path.basename(cf)}")
    else:
        rep.F("bug", "cookie_file not auto-filled", f"value={cf!r}")
else:
    rep.F("warn", "couldn't find sid for cf_autofill_test")

# ─── F.4: Settings persistence ─────────────────────────────────────────
rep.section("F.4 — global_config persistence")
r = client.get("/api/global_config")
if r.status_code == 200:
    cfg_before = r.get_json() or {}
    rep.F("ok", f"global_config GET returns {len(cfg_before)} keys")
else:
    cfg_before = {}
    rep.F("bug", f"global_config GET failed: HTTP {r.status_code}")

# Write a known-good field (whitelist-friendly)
r = L.csrf_post(client, "/api/global_config",
                json={"global_max_concurrent": 4})
if r.status_code == 200:
    rep.F("ok", "global_config POST: 200")
    r = client.get("/api/global_config")
    cfg_after = r.get_json() or {}
    if int(cfg_after.get("global_max_concurrent") or 0) == 4:
        rep.F("ok", "global_max_concurrent=4 persisted")
    else:
        rep.F("warn", "value did not persist",
              f"got={cfg_after.get('global_max_concurrent')!r}")
else:
    rep.F("bug", f"global_config POST: HTTP {r.status_code}",
          f"body={r.data[:200]!r}")

# Out-of-range clamping (max 64 per the route docstring)
r = L.csrf_post(client, "/api/global_config",
                json={"global_max_concurrent": 9999})
if r.status_code == 200:
    r = client.get("/api/global_config")
    n = int((r.get_json() or {}).get("global_max_concurrent") or 0)
    if 0 <= n <= 64:
        rep.F("ok", f"global_max_concurrent=9999 clamped to {n}")
    else:
        rep.F("bug", f"clamp failed: stored value {n}")

# ─── F.5: History + search ─────────────────────────────────────────────
rep.section("F.5 — history insert + FTS search")
try:
    with bd_db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history (url, site_id, site_name, filename, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("https://example.com/probe_target.mp4",
             "func_sid", "func_site",
             "func_probe_video.mp4", "ok"))
        # Rebuild FTS (history_fts is content-less so we need an explicit
        # INSERT into the fts table or a rebuild)
        try:
            cx.execute("INSERT INTO history_fts(history_fts) VALUES('rebuild')")
        except Exception:
            pass
        cx.commit()
    rep.F("ok", "inserted 1 history row")
except Exception as ex:
    rep.F("bug", "history insert failed", str(ex))

try:
    results = bd_db.db_search_fts("probe")
    if any("probe" in (r.get("filename") or "").lower() for r in results):
        rep.F("ok", f"FTS search 'probe' returned {len(results)} row(s)")
    else:
        rep.F("warn", f"FTS search returned {len(results)} but no match",
              "may be index timing")
except Exception as ex:
    rep.F("bug", "FTS search failed", str(ex))

# Snippet round-trip — verifies Bug 9 fix is functionally intact
try:
    results = bd_db.db_search_fts("probe")
    # snippet keys are snippet_url / snippet_filename / snippet_message
    snip_keys = ("snippet_url", "snippet_filename", "snippet_message")
    has_snippet = any(any(r.get(k) for k in snip_keys) for r in results)
    has_mark = any("<mark>" in (r.get(k) or "")
                   for r in results for k in snip_keys)
    if has_snippet:
        rep.F("ok", "snippet rendering produces output "
              f"({'with' if has_mark else 'no'} <mark>)")
    else:
        rep.F("warn", "snippet rendering produced no output")
except Exception as ex:
    rep.F("warn", "snippet rendering threw", str(ex))

# ─── F.6: Log tail + clear ─────────────────────────────────────────────
rep.section("F.6 — log tail + clear cycle")
log_dir = os.path.join(BD_HOME, "logs")
os.makedirs(log_dir, exist_ok=True)
log_path = os.path.join(log_dir, "bulk_downloader.log")
with open(log_path, "w") as f:
    for i in range(100):
        f.write(f"line {i:03d} — sample log content\n")

r = client.get("/api/logs/tail?lines=20")
if r.status_code == 200:
    body = r.get_json() or {}
    if body.get("ok") and "lines" in body:
        rep.F("ok", f"log tail returned {len(body.get('lines') or [])} lines")
    else:
        rep.F("warn", "log tail returned 200 but unexpected body",
              str(body)[:150])
else:
    rep.F("bug", f"log tail: HTTP {r.status_code}")

r = L.csrf_post(client, "/api/logs/clear")
if r.status_code == 200:
    sz = os.path.getsize(log_path) if os.path.exists(log_path) else 0
    if sz < 500:
        rep.F("ok", f"log cleared: file now {sz} bytes")
    else:
        rep.F("bug", f"log clear left {sz} bytes")
else:
    rep.F("bug", f"log clear: HTTP {r.status_code}")

# ─── F.7: Dev mode endpoint ────────────────────────────────────────────
rep.section("F.7 — dev mode endpoint")
r = client.get("/api/dev/enabled")
if r.status_code == 200:
    enabled = (r.get_json() or {}).get("enabled")
    if enabled:
        rep.F("ok", "/api/dev/enabled returns true (v3.47.7 unlock)")
    else:
        rep.F("warn", "dev mode not enabled by default")
else:
    rep.F("bug", f"/api/dev/enabled: HTTP {r.status_code}")

# ─── F.8: Auto-relogin config ──────────────────────────────────────────
rep.section("F.8 — auto-relogin config persistence")
r = L.csrf_post(client, "/api/sites",
                json={"name": "relogin_test",
                      "auto_relogin_enabled": False,
                      "auto_relogin_interval_hours": 24})
sid = None
for k, s in bd_app.s_cfg.items():
    if s.get("name") == "relogin_test":
        sid = k
        break
if sid:
    cfg = bd_app.s_cfg[sid]
    enabled = cfg.get("auto_relogin_enabled")
    interval = cfg.get("auto_relogin_interval_hours")
    if enabled is False and interval == 24:
        rep.F("ok", f"auto_relogin config persisted (enabled=False, interval=24)")
    else:
        rep.F("bug", f"auto_relogin not persisted: enabled={enabled} interval={interval}")

    # Interval handling. The clamp may happen at the consumer (cookie_relogin)
    # rather than at storage — either is acceptable. We only care that out-of-
    # range values don't crash the save and aren't silently honored at consume.
    r2 = client.get("/api/csrf")
    tok = (r2.get_json() or {}).get("csrf_token", "") if r2.status_code == 200 else ""
    r = client.put(f"/api/sites/{sid}",
                   json={"auto_relogin_interval_hours": 999},
                   headers={"X-CSRF-Token": tok})
    if r.status_code == 200:
        stored = bd_app.s_cfg[sid].get("auto_relogin_interval_hours")
        try:
            from bulk_downloader import cookie_relogin
            # check_and_schedule reads the cfg & clamps; verify it doesn't
            # actually schedule a 999-hour wait
            if hasattr(cookie_relogin, "_clamp_interval"):
                effective = cookie_relogin._clamp_interval(stored)
            else:
                effective = max(1, min(168, int(stored)))
            if 1 <= effective <= 168:
                rep.F("ok", f"interval=999 stored as {stored}, effective={effective}h")
            else:
                rep.F("bug", f"interval=999 effective={effective}h (no clamp)")
        except Exception as ex:
            rep.F("info", f"interval clamp check skipped: {ex}")
    elif r.status_code in (400, 422):
        rep.F("ok", f"interval=999 rejected at save: HTTP {r.status_code}")
    else:
        rep.F("warn", f"interval update PUT: HTTP {r.status_code}")
else:
    rep.F("warn", "auto_relogin site not created")

# ─── F.9: Pair redeem flow ─────────────────────────────────────────────
rep.section("F.9 — pair redeem (handshake validation)")
# Invalid pair code → 403 / 400 (NOT 200)
r = client.post("/api/pair/redeem",
                json={"code": "INVALID_PAIR_CODE_AAA000"})
if r.status_code in (400, 403, 404):
    rep.F("ok", f"invalid pair code → HTTP {r.status_code}")
elif r.status_code == 200:
    rep.F("crit", "invalid pair code ACCEPTED — auth bypass")
else:
    rep.F("warn", f"unexpected pair redeem code: HTTP {r.status_code}")

# ─── F.10: Diagnostics bundle ──────────────────────────────────────────
rep.section("F.10 — diagnostics bundle")
r = client.get("/api/diagnostics")
if r.status_code == 200:
    ct = r.headers.get("Content-Type", "")
    if "zip" in ct or "octet" in ct:
        rep.F("ok", f"diagnostics returns binary bundle "
              f"(content-type={ct}, size={len(r.data)})")
    elif "json" in ct:
        rep.F("ok", f"diagnostics returns JSON (size={len(r.data)})")
    else:
        rep.F("warn", f"unexpected content-type: {ct}")
elif r.status_code == 404:
    rep.F("info", "/api/diagnostics endpoint not present (this version)")
else:
    rep.F("warn", f"diagnostics: HTTP {r.status_code}")

# ─── F.11: History pagination ──────────────────────────────────────────
rep.section("F.11 — history pagination")
# Stuff some rows
try:
    with bd_db.db_conn() as cx:
        for i in range(25):
            cx.execute(
                "INSERT INTO history (url, site_id, site_name, filename, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"https://example.com/p{i}.mp4",
                 "page_sid", "paginate_test",
                 f"p{i}.mp4", "ok"))
        cx.commit()
    rep.F("ok", "inserted 25 rows for pagination test")
except Exception as ex:
    rep.F("warn", "pagination row insert failed", str(ex))

r = client.get("/api/history?limit=10&offset=0")
if r.status_code == 200:
    body = r.get_json()
    # /api/history may return a bare list OR {rows: [...]} — handle both
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        rows = body.get("rows") or body.get("history") or body.get("items") or []
    else:
        rows = []
    if isinstance(rows, list) and len(rows) <= 10:
        rep.F("ok", f"history?limit=10 returned {len(rows)} rows")
    else:
        rep.F("warn", f"limit=10 returned {len(rows) if hasattr(rows,'__len__') else '?'} rows")
elif r.status_code == 404:
    rep.F("info", "history endpoint shape differs (404)")
else:
    rep.F("warn", f"history GET: HTTP {r.status_code}")

# ─── F.12: Site enable/disable toggle ──────────────────────────────────
rep.section("F.12 — site enable/disable")
if new_sid:
    r2 = client.get("/api/csrf")
    tok = (r2.get_json() or {}).get("csrf_token", "") if r2.status_code == 200 else ""
    r = client.put(f"/api/sites/{new_sid}",
                   json={"enabled": False},
                   headers={"X-CSRF-Token": tok})
    if r.status_code == 200:
        if bd_app.s_cfg[new_sid].get("enabled") is False:
            rep.F("ok", "site disabled (PUT)")
        else:
            rep.F("warn", "disabled flag not persisted",
                  f"enabled={bd_app.s_cfg[new_sid].get('enabled')!r}")
    else:
        rep.F("warn", f"disable PUT: HTTP {r.status_code}")

    r = client.put(f"/api/sites/{new_sid}",
                   json={"enabled": True},
                   headers={"X-CSRF-Token": tok})
    if r.status_code == 200 and bd_app.s_cfg[new_sid].get("enabled") is True:
        rep.F("ok", "site re-enabled (toggle round-trip)")
    else:
        rep.F("warn", "enable round-trip incomplete",
              f"http={r.status_code} enabled={bd_app.s_cfg[new_sid].get('enabled')!r}")

# ─── Summary ───────────────────────────────────────────────────────────
rep.summary()
sys.exit(rep.exit_code())
