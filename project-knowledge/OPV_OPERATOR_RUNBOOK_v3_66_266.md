<!-- verified-against: v3.66.266 -->
# OPV Operator Runbook — BulkDownloader v3.66.265  (deep pass)

**Second, higher-effort verification pass.** Where the first pass confirmed the
surfaces *exist* and the contracts *hold*, this pass **drove the write-path OPVs
end-to-end** (seeded state, real endpoint calls, before/after state inspection),
**rendered `/capture` with a live session** (iframe mounted, pick mode armed), and
**stress-tested the noVNC embedding** for the failure modes a route inventory can't
see (CSP, clipboard permission, mixed-content). Two real findings came out of it —
see Section 0. Everything was re-derived from the 265 source; the tree was verified
byte-identical to the zip before and after (`bd-preflight` PASS).

---

## 0. Findings — read this first (bug-prevention)

### 🔴 FINDING 6 (HIGH — caught by a parallel audit, I'd missed it) — the flag-setting instructions were wrong
- The OPV guide §0 and my earlier D4 said "edit `app_config.json`, flip the key" for all
  seven flags. Verified against 265 source: wrong on **three** axes.
  - **F3.2/F3.3/digest** (`template_canary_enabled`, `drift_repair_enabled`,
    `daily_digest_enabled`) read via `global_config.get("automation.<flag>")`, and
    `global_config.get` is a **flat** dict lookup (no dotted-path resolution). So
    `app_config.json` needs the **literal flat dotted key** `"automation.template_canary_enabled": true`.
    A bare `template_canary_enabled` (what the guide implies) **silently no-ops** — the
    OPV would "fail" with no error.
  - **F1.3/F1.4** (`cookie_admission_enabled`, `predictive_relogin_*`) read from the
    **per-site** config (`self.config.get`), not global `app_config.json` — set them on the site.
  - **`dom_provider`** is a **code-level callable defaulting to `None`**, not a config flag.
  - **None** of the six are GUI-toggleable (none appear in SPA/settings-center source), so
    the guide's "toggle in Settings where exposed" is misleading.
- **Fixed in D4** (corrected table). This is the single most operator-impactful miss: following
  the old instructions, F3.2/F3.3 and the digest would silently stay OFF. Full credit to the
  parallel audit instance for catching it.

### 🟠 FINDING 1 (MEDIUM, real) — SPA `/capture` noVNC iframe is missing the clipboard `allow`
- **What:** the cockpit noVNC iframe (`cockpit_console.py`) is rendered with
  `allow="clipboard-read; clipboard-write"`. The **SPA `/capture` iframe**
  (`CaptureWorkflow.tsx`, ~line 561) is rendered with **no `allow` attribute** —
  empirically confirmed this pass: the live-mounted iframe's `allow` came back `None`.
- **Impact on OPV-PICK:** during the *manual login* step you do inside the noVNC
  pane, pasting a password from a manager via the browser clipboard API can be
  blocked by Permissions-Policy. **Workaround that always works:** use noVNC's own
  clipboard sidebar (the panel sends text to the remote as key events — independent
  of the `allow` attribute), or type the credential. So PICK is **not blocked**, but
  clipboard paste-through is degraded vs the cockpit page.
- **One-line fix** (frontend; needs a cut — *your call*, I did not deploy it):
  ```tsx
  // CaptureWorkflow.tsx — the live-session iframe
  <iframe
    title="live capture session"
    src={novncUrl}
    allow="clipboard-read; clipboard-write"   // <-- add this, matches the cockpit page
    className="h-full min-h-[78vh] w-full border-0"
  />
  ```

### 🟡 FINDING 2 (forward-looking) — noVNC URL will break under Phase-C TLS (mixed content)
- BD embeds `BD_NOVNC_URL` **verbatim** with no scheme rewrite. Today both BD and
  noVNC are HTTP on the LAN → fine. **When Phase C lands TLS** (BD → `https://`), an
  `http://…:6080` iframe becomes **mixed content** and browsers will block it.
- **Action when TLS ships (not now):** switch `BD_NOVNC_URL` to `https://` + the
  noVNC stack to `wss://`, or proxy noVNC under the same TLS origin. Park this with
  the Phase-C work; no action for these OPVs.

### 🟢 FINDING 3 (nuance, not a bug) — OPV-F2a "worst site = top cluster" is not a hard invariant
- `collect_site_health` returns two independent lists: `clusters` (ranked purely by
  failure **count**) and `sites` (a **4-factor** health score: auth color + failure
  count + median lifetime + last-check age). The panel renders them side by side but
  **does not draw the correlation line**, and the code itself says they *"should"*
  correlate.
- Because "worst site" is multi-factor, it **can legitimately differ** from the top
  cluster's biggest contributor (e.g. a site is worst on stale-check + red auth while
  a *different* site drives the top cluster's count). **That is expected, not a bug.**
- **Refined pass condition** (use this instead of a naive "they must match"): the
  worst site's `by_type` top entry **equals the top cluster's failure type** →
  correlated, pass. If they diverge, confirm the divergence is explained by the score
  factors (note it in the shot) — still a pass; only an *unexplained* mismatch or a
  panel error is a fail.

### 🟡 FINDING 4 (LOW, deep pass III) — picked selectors can include CSS-modules build-hash classes
- The pick deriver (`bdPickSelector`) correctly rejects **hashed ids** (`ember1234deadbeef99`)
  and **pure-hash classes** (`9f3a9c2b1e44ab`), always produces a **unique** selector
  (falls through to a scoped `:nth-of-type` path), and flags hidden elements (decoy guard).
  **But** it does *not* strip *prefixed*-hash classes like `btn-7f3a9c2b1e44` /
  `Button_root__3xK9f` (CSS-modules style), because its filter only catches a
  *whole-token* hash. So a picked selector can carry a volatile build-hashed class that
  breaks on the target site's next deploy.
- **Contained:** PICK produces a `draft_review_required` draft, not an enabled template —
  you review + Test it before promoting. **Operator habit:** when a picked selector
  contains a hash-looking class token (random hex tail), prefer Pick on a stabler element
  or hand-edit the class out before promote.

### 🟡 FINDING 5 (LOW / INFO, deep pass III) — `redact_artifact` is pattern-based defense-in-depth
- The artifact-level redactor scrubs whole-string opaque tokens, `key=secret` pairs
  (`token=`/`session=`/`sig=`…), JWTs (`eyJ…`), signed-URL params (`signature`/`Expires`),
  userinfo, and emails. A credential embedded in **freeform prose with surrounding words**
  (e.g. a bare `"Bearer sk-live-…"` as a text value) can survive *this* pass.
- **Why it's low:** it's the *last* defense-in-depth chokepoint, not the only one — captured
  network headers are stripped structurally upstream (`capture_bodies`), and the cockpit
  panels show **labels/counts/state, never values** (verified: `vpn_secrets_status` literally
  states "Never displays secret values or key material"). **Operator rule stands:** screenshot
  the panel, never raw values.

### ✅ Confirmed safe this pass
- **CSP does NOT block the noVNC iframe.** BD's policy is
  `frame-ancestors 'self'; base-uri 'self'; form-action 'self'; object-src 'none'` —
  **no `frame-src`/`child-src`/`default-src`**, so embedding the cross-origin noVNC
  URL is allowed. `X-Frame-Options: SAMEORIGIN` / `frame-ancestors 'self'` only
  restrict who can frame *BD*, not what BD frames. (Verified on the live response.)
- **OPV-F1.4 log label correction:** the guide says look for `predictive_relogin_due`
  — that's the *internal* reason string. The **operator-visible log event** is
  `preemptive_relogin`. Grep for that (Section D4).

---

## A. What I verified — deep evidence

| OPV | Depth this pass | Result |
|---|---|---|
| **B2** (`test_extract`) | **Drove the full cycle** against a seeded site+draft; inspected state before/after/clear | ✅ **I1** draft stays `draft_review_required`; **I2** `find_template_for_url`→`None` before *and* after; **I3** `_override_suppresses_persist()`=True, no `learned` written; teardown clears |
| **B2 / I2 structure** | Read matcher + loader | ✅ Triple-guarded: drafts dir not in `DEFAULT_TEMPLATE_DIRS`; draft suffix `.template-draft.json` ≠ scanned `*.template.json`; loader skips `status != "enabled"` |
| **F2.6** (analyzer) | Ran the endpoint + unit suites (planted capture → load→test→pin) | ✅ **23/23** incl. `test_load_and_pin_happy_path` (pin→`draft_review_required`, `enabled:False`, **signed URL stripped**), `test_gate_fails_closed_on_residual`, `test_resolve_rejects_traversal`, `test_domless_capture_is_graceful` |
| **PICK** (`/capture` noVNC) | **Rendered live session** (mocked start) | ✅ iframe **mounts** with server-supplied URL + **fills the 78vh pane**; "HELD OPEN" badge; host+`cloakbrowser` label; **pick mode arms** (amber bar, field→"Picking…"); only the live click is operator-gated |
| **F4.3** (token scope) | Booted **auth ON**, minted real tokens | ✅ enqueue token→`403 insufficient token scope` on `/api/retention/apply`; master bearer→not-403; admin mint→reserved (DEC-2) |
| **noVNC passthrough** | Live `/cockpit/api/novnc` with param URL | ✅ returns `resize=scale` + `autoconnect=true` verbatim; `configured:true` |
| **F1.3 / F1.4 / F3.2 / F3.3** | Ran the pinning suites + traced signals | ✅ admission/relogin/canary/drift all green; exact signals pinned (Section D) |
| **`/capture`, site_health, capture_diag, template_mgr** | Rendered | ✅ 0 console errors; only a Google-Fonts 403 (sandbox network-off, cosmetic) |
| **Targeted OPV test band** | 11 suites | ✅ **135/135** |

---

## B. noVNC setup for the new URL  ← do this FIRST

### Mechanic (verified): `BD_NOVNC_URL` (env only) → `/cockpit/api/novnc` → iframe `src`
**266 changed this:** `cockpit_core.novnc_url()` now **auto-fills `resize=scale` + `autoconnect=true`
when your URL omits them** (an explicit value is always preserved). So a bare
`BD_NOVNC_URL=http://10.0.70.20:6080/vnc.html` is enough — the endpoint returns it as
`…/vnc.html?resize=scale&autoconnect=true` and the `100%×78vh` pane fits instead of clipping.
The URL is still **config/env only** (never browser-supplied), and the password is **never** in
the URL, so `curl /cockpit/api/novnc` stays safe to screenshot.
> ⚠️ This only takes effect once **266 is deployed**, and only if your live (deploy-excluded)
> `cockpit_console.py`'s `api_novnc` still delegates to `cc.novnc_url()` (it does in 265 —
> one-line confirm after deploy). Until 266 ships, you still need the params in the URL by hand.

### Canonical URL (still fine to set explicitly; reconnect=true is NOT auto-added)
```
http://10.0.70.20:6080/vnc.html?autoconnect=true&resize=scale&reconnect=true
```
`resize=scale` = client-side fit · `autoconnect=true` = no Connect click (both now auto-added if
absent) · `reconnect=true` = auto-reconnect (set this one yourself if you want it).

### The setup that still matters: the drop-in precedence trap
The scaling param is now self-healing, but **which drop-in wins is not** — so this stays the real
Section-B task:
```bash
# 1. every place the var is set + which drop-in wins (last per key wins):
systemctl cat bulkdownloader | grep -n BD_NOVNC_URL

# 2. settle it in ONE drop-in; delete any other BD_NOVNC_URL line (a bare host URL is now OK):
sudo systemctl edit bulkdownloader
#    [Service]
#    Environment=BD_NOVNC_URL=http://10.0.70.20:6080/vnc.html
#    (or the full canonical URL above if you want reconnect=true)

# 3. apply:
sudo systemctl daemon-reload && sudo systemctl restart bulkdownloader

# 4. AUTHORITATIVE confirm — the running service's resolved env:
curl -s localhost:5555/cockpit/api/novnc | python3 -m json.tool
#    PASS (266) = "configured":true AND url contains resize=scale AND autoconnect=true
#                 (auto-added even if your BD_NOVNC_URL was a bare host URL)
#    FAIL = "configured":false -> no BD_NOVNC_URL resolved; or 266 not yet deployed / api_novnc
#           no longer delegates to cc.novnc_url(); back to step 1.
```

> The `&` in a systemd `Environment=` line is literal (no shell) — paste as-is.

### Confirm the noVNC stack itself is up (BD only embeds it)
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://10.0.70.20:6080/vnc.html   # expect 200
ss -ltnp | grep 6080                                                        # listener present
```

---

## C. Stash pre-flight gate (run once — catches problems before you click)
```bash
cd ~/BulkDownloader ; BASE=http://localhost:5555
curl -s $BASE/api/health | python3 -c 'import sys,json;d=json.load(sys.stdin);print("version:",d["version"],"db_ok:",d["db_ok"])'   # expect 3.66.265
venv/bin/python -c "from bulk_downloader import cloak; print('backend:', cloak.resolve_backend())"   # expect cloakbrowser
curl -s $BASE/cockpit/api/novnc | python3 -m json.tool                                               # Section B step 4
curl -s -o /dev/null -w 'api_tokens -> %{http_code}\n' $BASE/api/api_tokens   # 401=auth ON (F4.3 enforces) ; 200=auth OFF (scoping bypassed by design — note it)
PYTHONPATH=$PWD venv/bin/python tools/baselines_snapshot.py --help >/dev/null 2>&1 && echo "tools import OK" || echo "tools import FAIL -> prefix PYTHONPATH=\$PWD"
```
**API prelude** (for any POST/PATCH/DELETE):
```bash
cd ~/BulkDownloader ; BASE=http://localhost:5555 ; JAR=/tmp/bd.cookies
CSRF=$(curl -s -c $JAR "$BASE/api/csrf" | python3 -c 'import sys,json;print(json.load(sys.stdin)["csrf_token"])')
# add to POSTs:  -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' -H "Authorization: Bearer $BD_AUTH_TOKEN"
```

---

## D. The tasks — ordered (quick wins → noVNC → GUI → soak → phone)

Screenshot at the pass moment; name `OPV-<id>_<what>.png`. **Never capture secret
values.** Flip the row in `TASK_TRACKER.xlsx` after each pass. No redeploys.

### D1 · Quick CLI wins

**OPV-BASE — F1.5 baseline** *(read-only)*
```bash
cd ~/BulkDownloader
PYTHONPATH=$PWD venv/bin/python tools/baselines_snapshot.py --out baselines_pre_f1a.json
ls -la baselines_pre_f1a.json ; venv/bin/python -m json.tool baselines_pre_f1a.json | head -40
```
Pass: valid JSON, non-stub `heartbeat_fail_7d` + `dup_url_fetch_7d` (idle-tab rate is an expected stub). Re-run post-soak and diff. 📸 command + stderr one-liner + `ls` + head.

**OPV-F4.3 — scoped token enforcement** *(gate proven green w/ auth ON)*
> If pre-flight showed **200 (auth OFF)**, scoping is bypassed by design — record that, skip the 403.
```bash
TOK=$(curl -s -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $BD_AUTH_TOKEN" -X POST "$BASE/api/api_tokens" \
  -d '{"scope":"enqueue","label":"opv-test"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -w '\nstatus=%{http_code}\n' -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -X POST "$BASE/api/retention/apply" -d '{"dry_run":true}'
# EXPECT 403 {"error":"insufficient token scope","required_scope":"admin"}
curl -s -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $BD_AUTH_TOKEN" -X POST "$BASE/api/api_tokens" -d '{"scope":"admin"}'
# EXPECT {"ok":false,"error":"scope 'admin' is reserved and cannot be minted"}
```
Pass: 403 + reserved. **Redact the token in the shot.** Delete it after (`DELETE /api/api_tokens/<id>`).

### D2 · noVNC click-throughs (after Section B green)

**OPV-PICK** *(rendered working this pass up to the live click)*
1. `http://10.0.70.20:5555/capture` → Start URL → **Open session**. The pane shows
   "HELD OPEN" and the live page — **confirm it's scaled to fit** (your Section-B proof).
   If it clips/scrolls, the URL is missing `resize=scale`.
2. **Pick element** → amber "Pick mode" bar → click the target on the live page → a
   selector lands in the draft slot (the field flips to "Picking…" while armed; hidden
   elements come back flagged — the decoy guard).
   - *Clipboard note (Finding 1): if you must paste a login credential, use noVNC's
     clipboard sidebar or type it — the SPA pane doesn't grant clipboard-paste-through.*
3. **Test (live), persist OFF** off that draft (mechanics = OPV-B2) → one attempt, nothing persisted.
Pass: click→selector→draft; persist-OFF test; nothing enabled/persisted. 📸 pane+armed; draft+selector; test result.

**OPV-B2** *(4 invariants proven empirically this pass)*
1. Template Review Workbench (`/cockpit/template-manager` or SPA template view) → WowGirls
   jwplayer **draft** → **Test (live)**, **persist OFF**, one real member URL.
   API: `POST /api/template/test_extract {site_id, template:<draft>, url, persist:false}`.
2. Confirm **one real file downloads**.
3. Invariants (all verified in code+state): I1 draft stays `draft_review_required`; I2 only the
   explicit override drove it (matcher still can't return it); I3 nothing persisted; I4 challenge→manual handoff.
4. Teardown: `POST /api/template/test_extract {"site_id":"<sid>","clear":true}`.
Pass: 1 real DL; invariants hold; override cleared. Redact member URL/token.

### D3 · GUI panels

**OPV-F2.6** — DOM Analyzer: load capture → badge → tree → Test → **Pin**. APIs `/api/analyzer/{captures,load,tree,test,pin}`.
Pass: pin → **`draft_review_required`**, nothing enabled (verified 23/23 incl. signed-URL redaction). 📸 badge+tree; Test match; post-pin draft.

**OPV-F2a** — `…/cockpit/reports/site_health`. F2.1 clusters + F2.2 per-site health are **one page**.
**Use the refined pass (Finding 3):** worst site's `by_type` top entry == top cluster's failure type → correlated.
If they diverge, confirm it's explained by the score factors (auth color / fail count / lifetime / check-age) and note it — still a pass; only an unexplained mismatch or a panel error fails. 📸 one shot.

### D4 · Config-flag + soak

> ⚠️ **CORRECTED (was wrong) — the flags use THREE different mechanisms, not one.**
> The guide §0 (and the earlier draft of this runbook) said "edit `app_config.json`,
> flip the key" for all of them. Verified against 265 source — that is wrong on three
> axes. **None of these flags are GUI-toggleable** (none appear in the SPA or
> settings-center source), so the file/site path is the only one:
>
> | Flag (OPV) | Read from | Set it as |
> |---|---|---|
> | `cookie_admission_enabled` (F1.3) | the **site** config (`self.config.get`) | a **per-site** key on that site (site Config tab / its entry in `sites_config.json`), bare name |
> | `predictive_relogin_enabled`, `predictive_relogin_fraction` (F1.4) | the **site** config | **per-site**, bare name (fraction default 0.8) |
> | `template_canary_enabled` (F3.3) | `global_config.get("automation.template_canary_enabled")` | **global** `app_config.json`, as the **literal flat dotted key** `"automation.template_canary_enabled": true` |
> | `drift_repair_enabled` (F3.2) | `global_config.get("automation.drift_repair_enabled")` | **global**, flat dotted key `"automation.drift_repair_enabled": true` |
> | `daily_digest_enabled` | `global_config.get("automation.daily_digest_enabled")` | **global**, flat dotted key |
> | `dom_provider` (F3.2) | a **code-level** callable, default `None` | **NOT a config flag** — needs the stash DOM provider wired in code; until then the drift sweep just skips (expected) |
>
> **Why the dotted key matters:** `global_config.get(k, default)` is a *flat* dict
> lookup (`get_config().get(k, default)`, no dotted-path resolution). Since
> `ENABLE_KEY = "automation.template_canary_enabled"`, a nested `{"automation": {…}}`
> OR a bare `{"template_canary_enabled": true}` both **silently leave the feature OFF**.
> After any edit: `sudo systemctl restart bulkdownloader`, re-confirm `/api/health`=265.
> (263+ keeps `app_config.json` out of the zip, so deploys don't clobber flags.)
> *Credit: this was caught by a parallel audit instance; I'd missed it.*

**OPV-F1.3** — set `cookie_admission_enabled` ON **on the site** (per-site config). Site with all *dated* cookies expired (no-expiry session cookies don't count — gate needs `dated>0 and live==0`). Trigger a run.
Pass: held with status **`cookies_expired`**, **no Chromium spawn** (`pgrep -af chrome` shows nothing new), **self-resolves** after a fresh jar. 📸 held row; optional `pgrep`; proceeding after re-login.

**OPV-F1.4** — set `predictive_relogin_enabled` ON **on the site**, `predictive_relogin_fraction`=0.8 (DEFAULT_FRACTION confirmed). Churn-prone site; learn a median over a few runs.
Pass: a relogin fires proactively at ~0.8× the learned median — **grep the logs for the `preemptive_relogin` event** ("…triggering pre-emptive re-login"), **not** after a 401. (With <3 observations it falls back to the fixed threshold — expected.) 📸 the log line (redact token URLs).

**OPV-F3.2** — wire the stash `dom_provider` **in code** (it's not a config key; until wired the sweep skips — expected) + set `"automation.drift_repair_enabled": true` in **global** `app_config.json`. Induce/await a real drift.
Pass (verified: tests confirm): drift → `draft_review_required` candidate; never enables; AI-down = inert (no crash). 📸 the candidate.

**OPV-F3.3** — HAR fixtures (format per `bulk_downloader/template_canary.py`) + set `"automation.template_canary_enabled": true` in **global** `app_config.json` (the literal dotted key).
Pass: offline daily replay (no live HTTP); a forced regression → apprise drop alert. 📸 pass-rate + alert.

**OPV-F3.1** — saved search `action=enqueue` + `daily_cap` (name required+unique, query non-empty):
```bash
curl -s -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $BD_AUTH_TOKEN" -X POST "$BASE/api/saved_searches" \
  -d '{"name":"opv-enqueue","query":"failed","action":"enqueue","daily_cap":20}'
```
Pass: ≥1 week — cap respected (UTC/day), gates applied, duplicates `skipped_duplicate`, 0 dup bytes. 📸 config + week-end.

### D5 · Phone

**OPV-F4.1 / F4.5** — install the PWA from `http://10.0.70.20:5555/`; Share a URL → BulkDownloader → prefilled (~2 taps). Idle desktop tab in DevTools→Network: SSE on `/api/stream` → idle requests **>80%** below old 5s polling.
Pass: 2-tap share; idle −80%. 📸 share sheet + dashboard; idle network.

---

## E. Checklist

| # | Item | Surface | Depth-verified | One-line proof |
|---|---|---|---|---|
| B | noVNC URL | systemd | mechanic proven | `/cockpit/api/novnc` url ends `…&resize=scale…` |
| 1 | OPV-BASE | CLI | tool runs | valid JSON snapshot |
| 2 | OPV-F4.3 | CLI | **gate driven, auth ON** | enqueue→403; admin mint→reserved |
| 3 | OPV-PICK | noVNC | **live render + arm** | click→selector→draft; persist-OFF test |
| 4 | OPV-B2 | GUI/CLI | **4 invariants by state** | 1 real DL; invariants; cleared |
| 5 | OPV-F2.6 | GUI | **23/23 incl. redaction** | pin→`draft_review_required` |
| 6 | OPV-F2a | GUI | logic traced | worst `by_type` = top cluster (refined) |
| 7 | OPV-F1.3 | GUI+CLI | signal pinned | `cookies_expired`, no spawn, self-resolves |
| 8 | OPV-F1.4 | logs | label corrected | `preemptive_relogin` at ~0.8× median |
| 9 | OPV-F3.2 | GUI | tests green | drift→`draft_review_required`; AI-down inert |
| 10 | OPV-F3.3 | GUI | tests green | offline replay + drop alert |
| 11 | OPV-F3.1 | GUI+CLI | contract green | week: cap, 0 dup, gates |
| 12 | OPV-F4.1/F4.5 | phone | — | 2-tap share; idle −80% |

---

## F. Open items for your decision (not deployed — gated)
1. **Finding 1 fix** — add `allow="clipboard-read; clipboard-write"` to the SPA `/capture`
   iframe. One-line frontend change; fold into the next cut if you want clipboard paste-through
   in the SPA pane to match the cockpit page. (PICK works without it.)
2. **Finding 2** — when Phase-C TLS lands, move `BD_NOVNC_URL` to `https`/`wss`. Park with Phase C.
3. **Auth posture** — if pre-flight step 4 returns 200, decide whether to configure `BD_AUTH_TOKEN`
   on stash so OPV-F4.3 scoping actually enforces (otherwise it's bypassed by design).

---

## G. Appendix — deep-pass III empirical evidence

Everything below was driven against the 265 source (booted app / unit calls /
Playwright); the tree was re-verified byte-identical (`bd-preflight` PASS) afterward.

### OPV-PICK — the deriver actually resolves a click to a robust selector
Drove `bdPickSelector` against a synthetic DOM:
| Input element | Derived selector | unique | visible |
|---|---|---|---|
| stable `a#dl` | `a#dl` | yes | yes |
| hashed id + pure-hash class | `button.primary-action` (id+hash-class dropped) | yes | yes |
| 1st of two `.thumb` | `div:nth-of-type(1) > span:nth-of-type(1)` (fell through to unique path) | yes | yes |
| nested `li.target` | `li.target` (minimal) | yes | yes |
| `display:none` link | `a#hiddenlink` | yes | **no (decoy-flagged)** |
| prefixed-hash class | `button.btn-7f3a9c2b1e44.primary` | yes | yes — *see Finding 4* |

Bridge state machine (filesystem sentinel): `fresh→False`, `arm→is_armed True`,
write `PICK_RESULT.json` → `consume_result` returns it → file gone → `disarm→False`. ✅

### OPV screenshots — `serve_ss` CWE-22 boundary (all vectors)
| Request | Result |
|---|---|
| `site1/shot.png` (legit) | **200** |
| `../etc/passwd` | **400** |
| `../screenshots_evil/secret.png` (textual-prefix attack) | **400** |
| `../../../../etc/passwd` | **400** |
| `%2e%2e%2fetc%2fpasswd` (url-encoded) | **400** |
| `site1/nope.png` (missing) | **404** (clean, not 500) |

### OPV-F4.3 — full scope matrix + the vacuous-pass footgun (auth ON)
| Token | Route | Result |
|---|---|---|
| read | `/api/history` (read) | **200** |
| read | `/api/queue/v2/add_url` (enqueue) | **403** |
| enqueue | `/api/queue/v2/add_url` (enqueue) | **reached** (400 body-validation, *not* 403 → scope allowed) |
| read | `/api/retention/apply` (admin) | **403** |
| enqueue | `/api/retention/apply` (admin) | **403** |
| enqueue | `/api/sites` (UNMAPPED) | **403** (fail-closed) |

**Vacuous-pass proof:** enqueue token **alone** → `/api/retention/apply` = **403**;
enqueue token **+ session cookie** = **200**. The cookie authenticates before the scope
gate. → **Test the token ALONE (no `-b $JAR`)** or you'll get a false pass.

### OPV-F1.3 — admission gate exact boundary (`_cookies_all_expired` ⇒ hold iff `dated>0 AND live==0`)
| Jar | Hold? |
|---|---|
| all dated cookies expired | **True** (hold `cookies_expired`) |
| one dated cookie still live | False (admit) |
| only session cookies (no expiry) | False (can't prove expired) |
| expired dated + session mix | **True** |
| empty jar | False |

Gate is **opt-in** (`cookie_admission_enabled` OFF ⇒ no hold) and **fail-open** (errors ⇒ admit).

### OPV-F1.4 — predictive relogin exact timing (`fraction × median`, fail-to-fixed on thin data)
median([3600,3600,3600,7200,7200]) = 3600s; 0.8 × 3600 = **2880s threshold**:
| Cookie age | `due` |
|---|---|
| 1800s | False |
| 2844s (just under) | False |
| 2916s (just over) | **True** ← fires here |
| 7200s | True |
| 1 observation only | **None** → "insufficient observations (1<3)" → falls back to fixed/reactive threshold |

So: needs **≥3 learned lifetimes** before predictive engages; below that it's the old reactive path. The operator-visible log event is **`preemptive_relogin`**.

### F2 redaction posture
`redact_artifact` scrubs whole-string tokens (`<scrubbed>`), `key=secret` pairs, JWTs,
signed-URL `signature`/`Expires`, userinfo, emails — **selector shapes + host preserved**.
Freeform `"Bearer …"` prose survives that pass (Finding 5 — defense-in-depth corner).
`vpn_secrets_status` renders **labels/counts/state only** ("Never displays secret values or
key material") — safe to screenshot.
