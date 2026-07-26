<!-- verified-against: v3.66.267 -->
# OPV Audit + Step-by-Step Completion Guide — BulkDownloader v3.66.267

**This supersedes the v3.66.265/266 runbook.** It was produced by a second
**MAX, no-trust pass that fixed the one code defect in place, re-cut the
release (267), and re-verified entirely from the built artifact's source +
tests — not from any runbook.** The prior runbook's prose was treated as
non-authoritative throughout.

- **Fix landed:** FINDING 7 (the OPV-F1.4 silent-no-op footgun) — fixed in
  `runner.py`, RED→GREEN, cut into **3.66.267**, guards untouched.
- **Re-verification source of truth:** `BulkDownloader_v3_66_267.zip`
  (`verify_release` RESULT PASS, exit 0; band + OPV suites green **from the
  extracted zip**; all derivations re-grepped from the 267 tree).

---

# Part A — Audit & fix record

## A.1 What changed (code)

**FINDING 7 — F1.4 master-gate footgun (fixed).** On 266, `SiteRunner.maybe_preemptive_relogin`
returned at the very first line unless `auto_preemptive_relogin` was set — *before*
`predictive_relogin_enabled` was ever read. So setting only `predictive_relogin_enabled`
(what the old runbook told the operator to do) **silently did nothing**. Fixed so either flag
arms the feature:

```python
# bulk_downloader/runner.py — maybe_preemptive_relogin (267)
if not (self.config.get("auto_preemptive_relogin", False)
        or self.config.get("predictive_relogin_enabled", False)):
    return False
```

Backward-compatible (anyone already using `auto_preemptive_relogin` is unaffected; neither-set
stays byte-identical). **RED-first proven:** `test_hook_predictive_enabled_alone_arms_gate`
fails on pristine 266, passes on 267; two companion guards confirm the predictive decision still
governs and that the feature stays flag-gated (not data-gated).

No other code defect was found in the re-derivation. The auth-OFF behavior of F4.3 and the
`dom_provider`/AI dependency of F3.2 are **by-design prerequisites, not defects** (see A.4).

## A.2 The 267 cut (clean)

| Gate | Result |
|---|---|
| `tsc --noEmit` / `vite build` | clean |
| `spa_wired` delta vs 266 | **89 → 89 (+0)** — no route change |
| 3-part version bump | `__init__` + `CHANGELOG` + `test_settings_center_slice4.py` pin → 3.66.267 |
| 7 release-guard SHAs | **byte-identical to 266** (`d688aca9` / `2aa70253` / `b96f4af3` / `0559903d` / `1657d0a0` / `90bb8e0f` / `494aa319`) |
| Change surface vs 266 | **+0 / −0 files**, namelist clean (1392 entries) |
| Band from extracted zip | gui_parity 12 · contracts 17 · slice4 10 · **predictive_relogin 24/24** |
| `verify_release.py --zip` | **RESULT PASS**, exit 0 |

## A.3 No-trust re-verification (from the 267 artifact, not the doc)

OPV-backing suites, run from the **extracted 267 zip** — **223 tests, 0 failures**:

```
analyzer 23 · api_tokens+admin_reserved 24 · admission 24 · saved_search(+dedup+watch) 30
template_canary 19 · drift_repair 11 · capture_iframe_clipboard 2 · novnc_url_defaults 8
extraction_core 22 · cockpit_console 37 · predictive_relogin 24  (+ gui_parity/contracts/slice4)
```

Source anchors re-grepped from the 267 tree (every cited contract confirmed):
F1.4 fixed gate (4145) + log label `preemptive_relogin` (4179) + `DEFAULT_FRACTION=0.8`;
`global_config.get` flat (`return get_config().get(key, default)` — dotted key required);
`cockpit_core._embed_url_with_defaults` (resize/autoconnect only-if-absent);
`baselines_snapshot` emits `heartbeat_fail_7d` + `dup_url_fetch_7d`; token scope
`insufficient token scope` (app.py:445); SPA iframe `allow="clipboard-read; clipboard-write"`
(CaptureWorkflow.tsx:564); `collect_site_health` (app_data_layer.py:263);
`cookie_admission_enabled` per-site (admission.py); `drift_repair`/`template_canary` global
flat dotted `ENABLE_KEY`s + `_default_dom_provider`→None; saved-search `daily_cap` +
`skipped_duplicate`; `share_target` (manifest.json:81) + SSE `/api/stream` (app.py:9771).

## A.4 Verdict

**No remaining code-defect caveats.** After the F1.4 fix, **11 of 12 OPVs are READY** — the
only remaining steps are normal operator execution, each with a complete step-by-step below.

The **one honest residual is OPV-F3.2's *full* drift→candidate path**, which depends on
(1) a code-level **DOM provider** being wired (`_default_dom_provider` intentionally returns
`None`) and (2) **AI assist enabled** on stash (the diagnostics show it OFF). These are
**feature + config work, not a fixable defect** — wiring a real provider needs `cloakbrowser`
(absent from the sandbox) and could not be validated here, so it was **not faked**. F3.2's
on-stash-runnable behavior (flag-gated + AI-down-inert/no-crash) **is ready now**.

| Verdict | OPVs |
|---|---|
| ✅ Ready (operator executes per guide) | B, BASE, **F1.4 (fixed)**, F2.6, F2a, F1.3, F3.3, F3.1, F4.1/F4.5 |
| ✅ Ready, operator-only confirmation by nature | PICK (live click), B2 (real download), F4.3 (needs auth ON — your config choice) |
| 🟡 Ready (flag + inert path); full path needs provider+AI | F3.2 |

**Two cross-cutting prerequisites for live readiness:**
1. **Deploy 267 to stash** — the F1.4 fix lives in 267 (266 still has the footgun). Clipboard is already in 266; 267 adds the F1.4 fix only.
2. **F4.3 enforcement** requires `BD_AUTH_TOKEN` configured (auth ON). With auth OFF, scoping is bypassed by design.

---

# Part B — Step-by-step completion guide

> Run order: **deploy 267 → pre-flight → Section B (noVNC) → D1 → D2 → D3 → D4 → D5.**
> Screenshot at each pass moment, name `OPV-<id>_<what>.png`. **Never capture secret values.**
> Flip the row in `TASK_TRACKER.xlsx` after each pass.

## B.0 Deploy 267 (do this first)

```bash
cd ~/BulkDownloader && unzip -o /path/to/BulkDownloader_v3_66_267.zip
# cache clear is LOAD-BEARING (overlaid .py with old mtime else runs stale .pyc):
find ~/BulkDownloader -name '__pycache__' -type d -prune -exec rm -rf {} +
find ~/BulkDownloader -name '*.pyc' -delete
sudo systemctl restart bulkdownloader
curl -s localhost:5555/api/health        # CONFIRM "version":"3.66.267"
```
- **Exclude your live-edit file:** do **not** overlay `tools/cockpit_console.py` (merge it). After
  restart, one-line confirm its `api_novnc` still delegates to `cc.novnc_url()`.
- Backend check (the point of an install-path release):
  `venv/bin/python -c "from bulk_downloader import cloak; print(cloak.resolve_backend())"` → `cloakbrowser`.

## B.0.1 The corrected flag truth table (verified against 267 source)

| Flag (OPV) | Where read | How to set | Default |
|---|---|---|---|
| `cookie_admission_enabled` (F1.3) | per-site `self.config` | **per-site**, bare name (site Config tab / `sites_config.json`) | OFF |
| `predictive_relogin_enabled` (F1.4) | per-site `self.config` | **per-site**, bare name — **now sufficient on its own (267)** | OFF |
| `predictive_relogin_fraction` (F1.4) | per-site `self.config` | per-site, bare name | 0.8 |
| `auto_preemptive_relogin` (F1.4, alt) | per-site `self.config` | optional — fixed-age preemptive without prediction | OFF |
| `template_canary_enabled` (F3.3) | `global_config.get("automation.template_canary_enabled")` | **global** `app_config.json`, **literal flat dotted key** | OFF |
| `drift_repair_enabled` (F3.2) | `global_config.get("automation.drift_repair_enabled")` | **global**, flat dotted key | OFF |
| `daily_digest_enabled` | `global_config.get("automation.daily_digest_enabled")` | **global**, flat dotted key | OFF |
| `dom_provider` (F3.2) | code-level `Callable`, default `None` | **NOT a config flag** — wire in code (else sweep skips) | None |

Also required per-site for F1.4: `username` + `password` set on the site (relogin needs creds).
**Dotted-key rule:** `global_config.get` is a flat `dict.get` — a nested `{"automation":{…}}`
or a bare `{"template_canary_enabled":true}` **silently stays OFF**. Use `"automation.<flag>"`
as a top-level key. Restart after any `app_config.json` edit; re-confirm `/api/health`.

## B.0.2 Pre-flight gate (run once)

```bash
cd ~/BulkDownloader ; BASE=http://localhost:5555
curl -s $BASE/api/health | python3 -c 'import sys,json;d=json.load(sys.stdin);print("version:",d["version"],"db_ok:",d["db_ok"])'   # expect 3.66.267
venv/bin/python -c "from bulk_downloader import cloak; print('backend:', cloak.resolve_backend())"   # expect cloakbrowser
curl -s $BASE/cockpit/api/novnc | python3 -m json.tool                                               # Section B step 4
curl -s -o /dev/null -w 'api_tokens -> %{http_code}\n' $BASE/api/api_tokens   # 401=auth ON (F4.3 enforces); 200=auth OFF (note it)
PYTHONPATH=$PWD venv/bin/python tools/baselines_snapshot.py --help >/dev/null 2>&1 && echo "tools import OK" || echo "tools import FAIL -> prefix PYTHONPATH=\$PWD"
```
**API prelude** (for any POST/PATCH/DELETE):
```bash
BASE=http://localhost:5555 ; JAR=/tmp/bd.cookies
CSRF=$(curl -s -c $JAR "$BASE/api/csrf" | python3 -c 'import sys,json;print(json.load(sys.stdin)["csrf_token"])')
# add to POSTs:  -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' -H "Authorization: Bearer $BD_AUTH_TOKEN"
```

## Section B — noVNC URL (drop-in precedence)

The scaling param self-heals in 266+ (`novnc_url()` adds `resize=scale`+`autoconnect=true` if
absent), so a bare host URL is fine. **The remaining task is precedence — last drop-in per key wins:**
```bash
systemctl cat bulkdownloader | grep -n BD_NOVNC_URL      # 1. find every place it's set
sudo systemctl edit bulkdownloader                        # 2. settle ONE drop-in; delete others
#    [Service]
#    Environment=BD_NOVNC_URL=http://10.0.70.20:6080/vnc.html   (& is literal in systemd)
sudo systemctl daemon-reload && sudo systemctl restart bulkdownloader   # 3. apply
curl -s localhost:5555/cockpit/api/novnc | python3 -m json.tool         # 4. PASS: "configured":true + resize=scale + autoconnect=true
curl -s -o /dev/null -w '%{http_code}\n' http://10.0.70.20:6080/vnc.html # noVNC stack up = 200
```

---

## D1 · Quick CLI wins

### OPV-BASE (F1.5 baseline) — read-only
```bash
cd ~/BulkDownloader
PYTHONPATH=$PWD venv/bin/python tools/baselines_snapshot.py --out baselines_pre_f1a.json
venv/bin/python -m json.tool baselines_pre_f1a.json | head -40
```
**Pass:** valid JSON; `metrics.heartbeat_fail_7d` and `metrics.dup_url_fetch_7d` are **non-stub**
(real numbers, not `?`/null — populated from live history; idle-tab rate is an expected stub).
Re-run post-soak and diff. 📸 command + `ls` + head.

### OPV-F4.3 (scoped token enforcement) — needs auth ON
> Pre-flight showed **200 (auth OFF)** → scoping is bypassed by design; record that and skip the 403 step.
```bash
TOK=$(curl -s -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $BD_AUTH_TOKEN" -X POST "$BASE/api/api_tokens" \
  -d '{"scope":"enqueue","label":"opv-test"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
# test the token ALONE (NO -b $JAR — the cookie would authenticate before the scope gate = false pass):
curl -s -w '\nstatus=%{http_code}\n' -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -X POST "$BASE/api/retention/apply" -d '{"dry_run":true}'
# EXPECT 403 {"error":"insufficient token scope","required_scope":"admin"}
curl -s -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $BD_AUTH_TOKEN" -X POST "$BASE/api/api_tokens" -d '{"scope":"admin"}'
# EXPECT {"ok":false,"error":"scope 'admin' is reserved and cannot be minted"}
```
**Pass:** 403 + reserved. **Redact the token in the shot.** Delete it after: `DELETE /api/api_tokens/<id>`.

---

## D2 · noVNC click-throughs (after Section B green)

### OPV-PICK
1. `http://10.0.70.20:5555/capture` → enter Start URL → **Open session**. Pane shows **"HELD OPEN"**
   + the live page — **confirm it scales to fit** (Section-B proof; if it clips, the URL lacks `resize=scale`).
2. **Pick element** → amber "Pick mode" bar → click the target on the live page → a selector lands in
   the draft slot (field flips to "Picking…"; hidden elements come back flagged — the decoy guard).
   - **Clipboard (267):** the SPA pane now grants `clipboard-read/write`, so pasting a credential from
     a manager works (matches the cockpit page). noVNC's clipboard sidebar still works as a fallback.
   - **Selector hygiene:** if a picked selector carries a hash-looking class (random hex tail, e.g.
     `btn-7f3a9c2b1e44`), prefer a stabler element or hand-edit the class before promote (build-hash classes break on the site's next deploy).
3. **Test (live), persist OFF** on that draft (mechanics = OPV-B2) → one attempt, nothing persisted.

**Pass:** click → selector → draft; persist-OFF test; nothing enabled/persisted.
📸 pane+armed; draft+selector; test result. *(The live click is the operator action; everything up to it is verified.)*

---

## D3 · GUI panels

### OPV-B2 (test_extract override invariants)
1. Template Review Workbench (`/cockpit/template-manager` or SPA template view) → WowGirls jwplayer
   **draft** → **Test (live)**, **persist OFF**, one real member URL.
   API: `POST /api/template/test_extract {site_id, template:<draft>, url, persist:false}`.
2. Confirm **one real file downloads**.
3. Invariants (verified in code + 22 tests): I1 draft stays `draft_review_required`; I2 matcher
   (`find_template_for_url`) still returns `None`; I3 nothing persisted (override suppresses persist);
   I4 challenge → manual handoff.
4. Teardown: `POST /api/template/test_extract {"site_id":"<sid>","clear":true}`.

**Pass:** 1 real DL; invariants hold; override cleared. Redact member URL/token.
*(Needs member creds + cloakbrowser on stash — the download is the operator action.)*

### OPV-F2.6 (DOM Analyzer)
`/api/analyzer/{captures,load,tree,test,pin}` — load a capture → badge → tree → Test → **Pin**.
**Pass:** pin → **`draft_review_required`**, nothing enabled (verified 23/23 incl. signed-URL stripped on pin).
📸 badge+tree; Test match; post-pin draft.

### OPV-F2a (site health)
`…/cockpit/reports/site_health` (F2.1 clusters + F2.2 per-site health, one page).
**Refined pass (verified — not a hard invariant):** the worst site's `by_type` top entry **==** the top
cluster's failure type → correlated, pass. If they diverge, confirm it's explained by the 4 score factors
(auth color / failure count / median lifetime / last-check age) and note it — still a pass; only an
*unexplained* mismatch or a panel error fails. 📸 one shot.

---

## D4 · Config-flag + soak

> Set flags per the **B.0.1 truth table**. None of the six are GUI-toggleable; the per-site ones can be
> set on the site (Config tab / `sites_config.json`), the global ones as literal dotted keys in
> `app_config.json`. Restart + re-confirm `/api/health` after edits.

### OPV-F1.3 (cookie admission)
Set **per-site** `cookie_admission_enabled: true`. Use a site whose **dated** cookies are all expired
(no-expiry session cookies don't count — the gate needs `dated>0 and live==0`). Trigger a run.
**Pass:** held with status **`cookies_expired`**, **no Chromium spawn** (`pgrep -af chrome` shows nothing
new), **self-resolves** after a fresh jar. 📸 held row; optional `pgrep`; proceeding after re-login.

### OPV-F1.4 (predictive relogin) — **FIXED in 267**
Set **per-site** `predictive_relogin_enabled: true` (+ `predictive_relogin_fraction: 0.8`). Ensure the
site has `username`/`password`. **On 267 this single flag is sufficient** — you no longer need
`auto_preemptive_relogin` (that was the silent-no-op footgun, now fixed). Use a churn-prone site; let it
learn a median over a few runs.
**Pass:** a relogin fires proactively at ~0.8 × the learned median — **grep the logs for the
`preemptive_relogin` event** ("…triggering pre-emptive re-login"), **not** after a 401. With <3 learned
observations it falls back to the fixed-age threshold (expected). 📸 the log line (redact token URLs).

### OPV-F3.2 (drift repair) — partial on stash by design
Set **global** `"automation.drift_repair_enabled": true` (literal dotted key).
- **Runnable now (AI off / no provider):** the sweep is **inert** — no candidate, **no crash**. That is
  a valid pass for the AI-down scenario. 📸 the no-op/inert log.
- **Full drift→candidate** additionally needs (a) the **stash `dom_provider` wired in code** (default
  returns `None` → sweep skips, expected) and (b) **AI assist enabled** (currently OFF on stash). Once
  both are in place: induce/await a real drift → `draft_review_required` candidate (never enables).
  📸 the candidate. *(This is the one item gated on feature + config work — flagged honestly.)*

### OPV-F3.3 (template canary)
Provide HAR fixtures (format per `bulk_downloader/template_canary.py`) + set **global**
`"automation.template_canary_enabled": true` (literal dotted key).
**Pass:** offline daily replay (no live HTTP); a forced regression → apprise **drop alert**.
📸 pass-rate + alert.

### OPV-F3.1 (saved search enqueue + cap)
```bash
curl -s -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $BD_AUTH_TOKEN" -X POST "$BASE/api/saved_searches" \
  -d '{"name":"opv-enqueue","query":"failed","action":"enqueue","daily_cap":20}'
```
(name required + unique, query non-empty.)
**Pass:** over ≥1 week — `daily_cap` respected (UTC/day), gates applied, duplicates land
`skipped_duplicate` (0 duplicate bytes). 📸 config + week-end.

---

## D5 · Phone

### OPV-F4.1 / F4.5 (PWA + SSE)
Install the PWA from `http://10.0.70.20:5555/`. **Share a URL → BulkDownloader → prefilled (~2 taps)**
(the `share_target` manifest entry + the receiver in the SPA are present). Then idle a desktop tab in
DevTools → Network: the SSE channel on `/api/stream` should keep idle requests **>80% below** the old 5s
polling.
**Pass:** 2-tap share; idle −80%. 📸 share sheet + dashboard; idle network. *(Phone-measured by operator.)*

---

## Quick checklist

| # | OPV | Verdict | Operator action | Proof |
|---|---|---|---|---|
| B | noVNC URL | ✅ | settle drop-in | `/cockpit/api/novnc` → `resize=scale`+`autoconnect=true` |
| 1 | OPV-BASE | ✅ | run tool | non-stub `heartbeat_fail_7d`+`dup_url_fetch_7d` |
| 2 | OPV-F4.3 | ✅ (auth ON) | token-alone test | 403 insufficient scope; admin reserved |
| 3 | OPV-PICK | ✅ | live click | click→selector→draft; clipboard works (267) |
| 4 | OPV-B2 | ✅ | 1 real DL | 4 invariants; cleared |
| 5 | OPV-F2.6 | ✅ | load→test→pin | pin→`draft_review_required` |
| 6 | OPV-F2a | ✅ | 1 GUI shot | worst `by_type` == top cluster (refined) |
| 7 | OPV-F1.3 | ✅ | per-site flag + run | `cookies_expired`, no spawn, self-resolves |
| 8 | OPV-F1.4 | ✅ (fixed) | **`predictive_relogin_enabled` alone** | `preemptive_relogin` at ~0.8×median |
| 9 | OPV-F3.2 | 🟡 | global flag now; provider+AI later | inert/no-crash now; drift→candidate after wiring |
| 10 | OPV-F3.3 | ✅ | global flag + HAR | offline replay + drop alert |
| 11 | OPV-F3.1 | ✅ | saved search + week | cap, 0 dup, gates |
| 12 | OPV-F4.1/F4.5 | ✅ | install PWA, share | 2-tap; idle −80% |

**Bottom line:** deploy 267, then 11/12 OPVs complete cleanly via the steps above with no code-defect
caveats. F3.2's full drift→candidate is the single flagged item awaiting `dom_provider` wiring + AI
enablement (its inert path passes now).
> [!WARNING]
> **Historical archive - do not execute.** This file is a point-in-time record. Its commands, procedures, paths, versions, and acceptance criteria may be obsolete; use active documentation and current release gates instead.
