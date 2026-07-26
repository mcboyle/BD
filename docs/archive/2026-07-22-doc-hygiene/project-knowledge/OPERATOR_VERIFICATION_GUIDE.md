<!-- verified-against: v3.66.262 -->
# Operator Verification Guide — BulkDownloader (OPV-* items)

*Derived from the v3.66.262 source tree (262 is **live on stash** — deploy confirmed 2026-06-16; 263 is build-hygiene-only and adds no operator-facing check). These are the human-only checks: the code is deployed + suite-exercised, but each needs a real browser / real site / real device on **stash** that the sandbox can't drive. Work top-to-bottom or cherry-pick; each section is self-contained.*

---

## 0. Common setup (read once)

**The box.** Stash is headless. Service runs at `localhost:5555` under systemd unit `bulkdownloader`; install dir `~/BulkDownloader` (user `mboyle`); service venv is `venv/bin/python` (NOT `.venv`). From another machine on the LAN use `http://10.0.70.20:5555`.

**The three surfaces:**
- **SPA** (the app): `http://10.0.70.20:5555/` — BrowserRouter, e.g. `/#`-free paths like `/settings`, `/queue`, `/history`, `/capture`.
- **Cockpit** (ops pages): `http://10.0.70.20:5555/cockpit/...` (see the map below).
- **API**: `http://10.0.70.20:5555/api/...`

**Cockpit page map (verified routes):**
| Page | URL |
|---|---|
| Reports home | `/cockpit/reports` |
| Site health | `/cockpit/reports/site_health` |
| System status | `/cockpit/reports/system_status` |
| DOM recorder status | `/cockpit/reports/dom_recorder_status` |
| Workflow analytics | `/cockpit/reports/workflow_analytics` |
| VPN / secrets status | `/cockpit/reports/vpn_secrets_status` |
| Capture diagnostics | `/cockpit/reports/capture_diagnostics` |
| Replay validation | `/cockpit/reports/replay_validation` |
| Settings | `/cockpit/settings` · `/cockpit/settings/secrets` · `/cockpit/settings/site/<sid>` |
| Template manager | `/cockpit/template-manager` |

**Auth + CSRF for API calls.** If `BD_AUTH_TOKEN` is set on the service, send `Authorization: Bearer <token>` (this is the *operator/master* bearer — it satisfies all scopes). For state-changing calls (POST/PATCH/DELETE) you also need a CSRF token. Reusable shell prelude on stash:

```bash
cd ~/BulkDownloader
BASE=http://localhost:5555
JAR=/tmp/bd.cookies
# get CSRF token + session cookie
CSRF=$(curl -s -c $JAR "$BASE/api/csrf" | python3 -c 'import sys,json;print(json.load(sys.stdin)["csrf_token"])')
# template for a POST:
#   curl -s -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
#        -H "Authorization: Bearer $BD_AUTH_TOKEN" -X POST "$BASE/api/..." -d '{...}'
```

**How to set a config flag.** The canonical store is `~/BulkDownloader/app_config.json` (mirrored to the in-memory `_app_cfg`). Two ways:
1. **GUI** — toggle it in the SPA **Settings** view / `/cockpit/settings` where exposed.
2. **File** — edit `~/BulkDownloader/app_config.json`, add/flip the key, then `sudo systemctl restart bulkdownloader`. (The 262 *overlay* deliberately omits `app_config.json`, and **263 drops it from the full release zip too**, so deploying never clobbers your flags or the api-token signing secret.)

Flags referenced below: `cookie_admission_enabled`, `predictive_relogin_enabled`, `predictive_relogin_fraction` (default `0.8`), `template_canary_enabled`, `drift_repair_enabled`, `dom_provider`, `daily_digest_enabled`.

**Running CLI tools (important).** Several `tools/*.py` import the `bulk_downloader` package. Running them by path — `python tools/foo.py` — puts `tools/` (the script's own dir) on `sys.path`, **not** the repo root, so the import fails with `ModuleNotFoundError: No module named 'bulk_downloader'`. Always set the repo root on the path:
```bash
cd ~/BulkDownloader
PYTHONPATH=$PWD venv/bin/python tools/<tool>.py [args]
```
(Stdlib-only tools also run under the system `python3`; venv is always safe.)

**📸 Screenshots — the rule.** Capture proof at the moment the pass-condition is on screen. For GUI checks screenshot the cockpit/SPA panel; for CLI checks a clean screenshot (or copy-paste) of the command + its output. **F2 hygiene: never capture secret values** — cookie/token/storage values, signed URLs, JWTs, raw `app_config.json` secret lines. The cockpit panels are already built to show labels/counts/states, not values; keep it that way in your shots. Name files `OPV-<id>_<what>.png`.

---

## 1. OPV-BASE — capture the F1.5 HEAD-probe baseline

**Goal:** capture the pre-F1 metrics snapshot on stash (hourly-stats/site, 7-day auth-fail = the F1.3 signal, 7-day dup-URL fetch = the F1.5 signal). It feeds the F1.5 HEAD-probe default decision (see `F1_5_HEAD_PROBE_DEFAULT_DECISION.md`). Tool shipped @217. **Read-only on the DB.** Run it **once before** the F1 soak and **again after**, then diff the two snapshots.

**Steps (CLI on stash):**
```bash
cd ~/BulkDownloader
# the tool defaults to STDOUT — pass --out to write a file; PYTHONPATH lets it auto-resolve the live DB
PYTHONPATH=$PWD venv/bin/python tools/baselines_snapshot.py --out baselines_pre_f1a.json
#   (alt, no PYTHONPATH needed — point it straight at the DB:)
#   venv/bin/python tools/baselines_snapshot.py --db <live_db_path> --out baselines_pre_f1a.json
ls -la baselines_pre_f1a.json
venv/bin/python -m json.tool baselines_pre_f1a.json | head -40
```
It prints a one-line summary to stderr: `baselines: N site(s) | auth-fail(7d)=X | dup-urls(7d)=Y -> baselines_pre_f1a.json`.

**Pass:** the file is written + valid JSON, with non-stub `heartbeat_fail_7d` and `dup_url_fetch_7d` counts (idle-tab rate is an explicit not-instrumented stub — expected). Re-run post-soak and diff to show movement.

**📸 Screenshot:** the terminal showing the run, the stderr one-liner with the real counts, `ls -la baselines_pre_f1a.json`, and the first ~20 lines of JSON. One shot. (No secrets here — it's counts/timestamps only.)

---

## 2. OPV-F1.3 — cookie-expiry admission gate

**Goal:** with `cookie_admission_enabled` ON, a site whose cookie jar has **all dated cookies expired** is **held before start** (status `cookies_expired`, no browser spawn) and **self-resolves** once a fresh jar lands. Shipped @217.

**Steps:**
1. Enable `cookie_admission_enabled` (§0) and restart.
2. Pick a logged-in site; let its cookies lapse, or hand-edit the jar so every *dated* cookie is in the past (session cookies with no expiry don't count — the gate treats those as "can't prove expired" and won't hold).
3. Trigger that site's run from the SPA **Queue/Sites** (or `/cockpit/actions/site`).
4. Observe the run is **held**, not started — status `cookies_expired`, and **no Chromium process spawns** (`pgrep -af chrome` shows nothing new for that run).
5. Re-login / drop a fresh jar → the next attempt **self-resolves** and proceeds.

**Pass:** held with `cookies_expired` + zero browser spawn while expired; auto-proceeds after a fresh jar.

**📸 Screenshot:** (a) the site/queue row showing `cookies_expired` held state; (b) optional terminal `pgrep -af chrome` showing no spawn during the hold; (c) the same row proceeding after re-login.

---

## 3. OPV-F1.4 — predictive relogin

**Goal:** with `predictive_relogin_enabled` ON, a churn-prone (short-session) site triggers a **proactive relogin at ≈ `predictive_relogin_fraction` × median learned session lifetime** (default 0.8), *before* the session would expire. Shipped @218.

**Steps:**
1. Enable `predictive_relogin_enabled` (and confirm `predictive_relogin_fraction` = 0.8) (§0), restart.
2. Use a site known to expire sessions quickly so a median lifetime is learned over a few runs.
3. Watch the logs / run timeline: a relogin should fire at ~0.8× the learned median, labelled as predictive (`predictive_relogin_due`), not as a reactive recovery after a 401/redirect.

**Pass:** relogin fires proactively at ~0.8× median (not after an auth failure).

**📸 Screenshot:** the log/timeline line showing the predictive relogin firing with its timing relative to the learned median (redact any URL with embedded tokens).

---

## 4. OPV-F2.6 — DOM Analyzer Workbench click-through

**Goal:** the post-hoc DOM Analyzer lets you load a capture, see the **badge**, walk the **tree**, run a **test**, and **pin** a selector — and pinning routes to **`draft_review_required`** (review-only; it never auto-enables). Shipped @216.

**Steps (GUI):**
1. Open the DOM Analyzer (SPA analyzer view / from the Template/Capture area). It is backed by these APIs: `/api/analyzer/captures`, `/api/analyzer/load`, `/api/analyzer/tree`, `/api/analyzer/test`, `/api/analyzer/pin`.
2. Load a recent capture → confirm the **badge** renders.
3. Expand the **tree**; pick an element; hit **Test** → see the match result.
4. **Pin** the selector → confirm the result is a **`draft_review_required`** draft (NOT an enabled template).

**Pass:** badge → tree → test → pin all work; pin produces `draft_review_required`, nothing enabled.

**📸 Screenshot:** (a) loaded capture with badge + tree; (b) a Test match result; (c) the post-pin state showing `draft_review_required`.

---

## 5. OPV-F2a — site health correlates with top failure cluster

**Goal:** on `/cockpit/reports/site_health`, the **worst-scoring site** correlates with the **top failure cluster** from the clustering view (F2.1/F2.2). Shipped @219.

**Steps (GUI):**
1. Open `http://10.0.70.20:5555/cockpit/reports/site_health`.
2. Note the worst-ranked site(s).
3. Cross-check against the failure-cluster surfacing (same reports area) — the worst site should map to the dominant cluster.

**Pass:** the worst site and the top cluster point at the same underlying problem.

**📸 Screenshot:** the `site_health` panel with the worst site visible, plus the cluster view — ideally one shot showing both (or two named shots).

---

## 6. OPV-F3.1 — saved-search **enqueue** lane (week-long)

**Goal:** a saved search with `action="enqueue"` feeds new history matches into the **normal download pipeline** with the **daily cap**, all **gates**, and **F1.5 dedup** applied — proven over ~a week unattended. Shipped @223.

**Steps:**
1. Create a saved search with `action="enqueue"` and a `daily_cap`. `name` is REQUIRED (unique) and `query` non-empty. GUI: SPA **History → saved search → action picker** (the PATCH lane). API equivalent:
   ```bash
   # create
   curl -s -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/saved_searches" -d '{"name":"opv-enqueue","query":"failed","action":"enqueue","daily_cap":20}'
   # later, adjust via PATCH
   curl -s -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X PATCH "$BASE/api/saved_searches/<id>" -d '{"daily_cap":10}'
   ```
   (e.g. a "failed-alphas → requeue" rule.)
2. Let it run for ~a week.
3. Confirm: new matches get enqueued, the **daily cap** holds (no run exceeds it/day, UTC), normal gates apply, and duplicates are **skipped** (`skipped_duplicate` from F1.5).

**Pass:** ≥1 week unattended, cap respected, 0 dup bytes, all gates applied.

**📸 Screenshot:** (a) the saved-search row showing `action=enqueue` + the cap; (b) after a week, the History/Queue showing enqueued items capped per day and `skipped_duplicate` entries. Capture at the start (config) and at the end (week-long result).

---

## 7. OPV-F3.2 — drift → AI repair candidate

**Goal:** with a stash `dom_provider` wired and `drift_repair_enabled` ON, a real selector **drift** produces an AI **repair candidate** that lands as **`draft_review_required`** (never auto-applied; inert if AI is down). Shipped @224.

**Steps:**
1. Set `dom_provider` to your stash DOM provider and enable `drift_repair_enabled` (§0); restart. (Until `dom_provider` is wired the sweep just skips — that's expected.)
2. Induce or wait for a real drift on a template (a site whose markup changed so the learned selector goes stale).
3. Confirm the drift→repair path emits a **`draft_review_required`** candidate (via `pin_candidate`), and that with AI disabled the path is simply inert (no crash, no auto-change).

**Pass:** drift yields a review-required repair candidate; nothing auto-enabled; AI-down = inert.

**📸 Screenshot:** the resulting `draft_review_required` repair candidate in the template/review view, with the drifted site identifiable (no secret values).

---

## 8. OPV-F3.3 — template canary

**Goal:** with HAR fixtures added and `template_canary_enabled` ON, the daily synthetic-fixture replay runs **HTTP-free** and **alerts on a pass-rate drop**. Shipped @222.

**Steps:**
1. Add HAR fixtures for the templates you want canaried (per the canary's fixtures location/format — check `template_canary.py`).
2. Enable `template_canary_enabled` (§0), restart.
3. Let the daily canary run (or trigger it). Confirm it replays the fixtures with **no live HTTP** and, if you deliberately break a fixture's expected outcome, it raises a **drop alert** (apprise).

**Pass:** canary replays offline daily; a forced regression produces a drop alert.

**📸 Screenshot:** the canary status/result (pass-rate) and, if you forced a regression, the drop-alert notification.

---

## 9. OPV-F4.1 / F4.5 — phone share-target → BD

**Goal:** the installed PWA accepts an OS **Share** to BulkDownloader (≈2 taps from a phone) and prefills the Dashboard; and the SSE-for-SPA push means an **idle tab's request volume drops > 80%** vs the old polling. Shipped @220.

**Steps:**
1. On a phone, install the BD PWA (Add to Home Screen) from `http://10.0.70.20:5555/`.
2. From a browser/app, **Share** a URL → choose BulkDownloader → confirm it lands in BD with the URL prefilled (≈2 taps).
3. Leave a desktop tab **idle** and watch network activity (DevTools → Network): with SSE on `/api/stream`, idle request volume should be **> 80% lower** than the old 5s polling.

**Pass:** share-to-BD works in ~2 taps; idle-tab requests down > 80%.

**📸 Screenshot:** (a) the phone share sheet showing BulkDownloader as a target + the prefilled Dashboard; (b) DevTools Network on an idle tab showing the low request rate (SSE stream open, few/no polls).

---

## 10. OPV-F4.3 — scoped API tokens enforce

**Goal:** an **`enqueue`** token works on its allowed routes but gets **403 on an admin route** (`/api/retention/apply`). Shipped @227. *(262 is live: also confirm `admin` is no longer mintable — DEC-2.)*

**Steps (CLI on stash):**
```bash
# (0) FIRST confirm token auth is even configured — else scoping is bypassed BY DESIGN
#     ("auth not configured -> allow all" in _check_token):
curl -s -o /dev/null -w 'no-auth admin route=%{http_code}\n' "$BASE/api/api_tokens"
#     401 = auth ON (good, proceed) ; 200 = auth OFF (scoping not enforced — that is the finding)

# (1) mint an enqueue token WITH your session (cookie + CSRF needed for the mint):
TOK=$(curl -s -b $JAR -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $BD_AUTH_TOKEN" -X POST "$BASE/api/api_tokens" \
  -d '{"scope":"enqueue","label":"opv-test"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# (2) TEST THE TOKEN ALONE on an ADMIN route. CRITICAL: NO -b $JAR (a session cookie
#     authenticates BEFORE the token-scope gate and the test passes vacuously), NO X-CSRF
#     (Bearer bypasses CSRF), WITH Content-Type (else you get 415 from the view, not 403):
curl -s -w '\nstatus=%{http_code}\n' -H "Authorization: Bearer $TOK" \
  -H 'Content-Type: application/json' -X POST "$BASE/api/retention/apply" -d '{"dry_run":true}'
# expect: 403 {"error":"insufficient token scope","required_scope":"admin"}

# (262, live) minting admin is REJECTED as reserved:
curl -s -H "Authorization: Bearer $BD_AUTH_TOKEN" -H "X-CSRF-Token: $CSRF" -b $JAR \
  -H 'Content-Type: application/json' -X POST "$BASE/api/api_tokens" -d '{"scope":"admin"}'
# expect: {"ok":false,"error":"scope 'admin' is reserved and cannot be minted"}

# clean up the test token
curl -s -H "Authorization: Bearer $BD_AUTH_TOKEN" -H "X-CSRF-Token: $CSRF" -b $JAR \
  -X DELETE "$BASE/api/api_tokens/<token_id_from_mint>"
```

**Pass:** enqueue token → **403** on `/api/retention/apply`; admin mint → **reserved** error (DEC-2, 262 live).

**📸 Screenshot:** the terminal showing the `403` from the enqueue token and the `reserved` rejection. **Redact the minted token string** in the shot.

---

## 11. OPV-PICK — live element-pick click-through (capture console)

**Goal:** on the **`/capture`** console, pick an element **directly on the live noVNC-forwarded page** (over live CDP), see that selector flow into a **draft**, then run a **persist-OFF** test extraction off it — all without enabling/persisting anything. Bridge shipped @250/251.

**Steps (GUI — this is the human-only one):**
1. Open the **`/capture`** route in the SPA (`http://10.0.70.20:5555/capture`) and start/attach a capture session; the live page renders in the embedded noVNC pane.
2. Enter **pick** mode and click the target element on the live page. Confirm a **selector is captured into a draft** (the in-page pick over live CDP).
3. Run a **Test (live) with persist OFF** off that draft (see OPV-B2 for the mechanics) and confirm one extraction attempt runs **without** persisting selectors or enabling the draft.

**Pass:** click on live page → selector → draft; persist-OFF test runs; nothing enabled/persisted.

**📸 Screenshot:** (a) `/capture` with the noVNC pane + pick mode active on the target element; (b) the draft showing the captured selector; (c) the persist-OFF test result. This sequence is the proof the live click→selector→draft bridge works end-to-end.

---

## 12. OPV-B2 — real-extraction "Test (live)" draft override

**Goal:** drive **one real download** from an **unreviewed draft** (e.g. the WowGirls jwplayer draft) via the Test-(live) override, and confirm the **4 enforced invariants** hold. Code deployed since 241.

**Endpoint:** `POST /api/template/test_extract` — body: `site_id` (required), `template` (the flat draft object, required for a set), `draft_file` (optional, only used for persist-ON writeback), `persist` (optional bool, **default False**), `url` (optional single http(s) URL), `clear` (optional — teardown).

**Steps:**
1. From the **Template Review Workbench** (`/cockpit/template-manager` or the SPA template view), select the WowGirls jwplayer **draft** and use **Test (live)** with **persist OFF**, supplying one real member URL. (Equivalent API: POST the draft object + `site_id` + `url`, `persist:false`.)
2. Confirm **one real file downloads**.
3. Verify the **4 invariants** (from the route contract):
   - **(I1) Enables nothing** — the draft's status stays `draft_review_required`; it is NOT promoted to `reviewed`/`enabled`.
   - **(I2) Normal matcher still ignores the draft** — `find_template_for_url` cannot return it; only the explicit per-site `draft_test_override` branch drove this run.
   - **(I3) persist OFF ⇒ nothing persists** — neither the live site config nor the draft is written (with `persist:true` it would write to both — leave it OFF here).
   - **(I4) Challenge handling unchanged** — any challenge falls open to manual handoff; B2 adds no auto-solve and no access bypass.
4. **Teardown:** clear the standing override — `POST /api/template/test_extract {"site_id":"<sid>","clear":true}` (or the GUI clear). Confirm the override is removed.

**Pass:** one real download from the unreviewed draft; all four invariants hold; override cleared afterward.

**📸 Screenshot:** (a) the Test-(live) invocation with **persist OFF** selected; (b) the completed real download; (c) proof of I1/I3 — the draft still `draft_review_required` and the site config unchanged after the run; (d) the override cleared. Redact any member-session URL/token in the shots.

---

## Quick checklist

| # | Item | Surface | One-line proof |
|---|---|---|---|
| 1 | OPV-BASE | CLI | `baselines_pre_f1a.json` written + valid |
| 2 | OPV-F1.3 | GUI+CLI | held `cookies_expired`, no spawn, self-resolves |
| 3 | OPV-F1.4 | logs | relogin at ~0.8× median, proactive |
| 4 | OPV-F2.6 | GUI | badge/tree/test/pin → `draft_review_required` |
| 5 | OPV-F2a | GUI | worst site = top cluster on `site_health` |
| 6 | OPV-F3.1 | GUI+CLI | week: cap held, 0 dup, gates applied |
| 7 | OPV-F3.2 | GUI | drift → `draft_review_required` candidate |
| 8 | OPV-F3.3 | GUI | offline daily replay + drop alert |
| 9 | OPV-F4.1/F4.5 | phone+GUI | 2-tap share; idle requests −80% |
| 10 | OPV-F4.3 | CLI | enqueue token → 403 on retention/apply |
| 11 | OPV-PICK | GUI (noVNC) | live click → selector → draft, persist-OFF test |
| 12 | OPV-B2 | GUI/CLI | 1 real DL from draft; 4 invariants hold |

*After each pass, flip the matching OPV row's status in `TASK_TRACKER.xlsx` and attach the screenshot reference. None of these require a redeploy.*
> [!WARNING]
> **Historical archive - do not execute.** This file is a point-in-time record. Its commands, procedures, paths, versions, and acceptance criteria may be obsolete; use active documentation and current release gates instead.
