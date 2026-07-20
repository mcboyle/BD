<!-- verified-against: v3.66.810 -->
<!-- CURRENCY: procedures below are version-independent and unchanged. The version
     anchor, the per-item STATUS lines, and the "Session checks" section at the top
     were re-derived on 2026-07-20 from a running stash box via
     scripts/bd-stash-report.sh + scripts/bd-opv-check.sh (measured, not quoted).
     v3.66.808-810 made three OPV/Arch-B knobs config-declared; one of the three
     GUI surfaces is complete, two have a KNOWN pending FE control (see below). The
     keystroke procedures for every item remain valid as written. -->

# OPV Completion Guide — the 11 remaining live-verify items (v3.66.810)

**Target:** BulkDownloader v3.66.810 (procedures unchanged since v3.66.783) · stash `mboyle@10.0.70.20` · `bulkdownloader` @ localhost:5555
**Companion to:** COWORK_OPERATOR_AUTOMATION_GUIDE (the canon — envelope, grants, ethics floor)
**Status source:** measured 2026-07-20 on stash (service up, git @ v3.66.810) via the two diagnostic scripts named above — this supersedes any quoted status.

> **Read this first.** Every item below is reduced to the *minimum human keystroke* — usually a
> single paste block — with Cowork doing all the setup, verification, revert, and ledgering around it.
> The split is deliberate: an environment safety classifier may block Cowork from *executing* live
> security/automation mutations itself, so the mutation line is yours to paste; **everything else is
> Cowork's.** Where Cowork *can* run it directly (read-only checks, sandbox drives), it does.
>
> **The envelope still applies to every mutation** (snapshot → mutate → prove-by-behavior → run →
> revert → prove-revert → ledger). The ethics floor, secret-hygiene, and auth-off-needs-restart are
> unchanged — they protect the box regardless of who's driving.

---

## Session checks — v3.66.808-810 (added at the top per operator request, 2026-07-20)

**What changed since 805, and what was actually measured this session.** Everything here was
re-derived by running tools/tests + the two diagnostic scripts against the live stash box — not read
off a register. Where a claim is sandbox-only or box-only, it says so (CLAUDE.md §10).

### A. Three cuts made OPV/Arch-B knobs config-declared (merged to main, PR #2)

| Cut | Knob(s) | Backend (POST accepts) | GUI control renders? |
| --- | --- | --- | --- |
| v3.66.808 | `captcha_vnc_display`, `captcha_vnc_websocket_port` | ✅ 200 (was 400) | ❌ **PENDING** — no `Settings.tsx` JSX yet |
| v3.66.809 | `netns_isolation` | ✅ 200 (was 400) | ❌ **PENDING** — no toggle JSX yet |
| v3.66.810 | `predictive_relogin_enabled` / `_fraction` (per-site) | ✅ (in `CFG_FIELDS`) | ✅ renders via the schema-driven per-site editor (`SiteSettings.tsx`) |

**The render gap (honest, CLAUDE.md §0).** For the two *global* knobs, the backend accepts the key and
the key ships in the SPA bundle, but `Settings.tsx` renders global controls from **explicit
hand-written JSX** (e.g. `captcha_takeover_mode` is a literal `<select>`), and no JSX was added for the
new keys. The config-surface gate marked them `gui_exposure="full"` because the key STRING appears in
`settingsSchema.ts` — a check that verifies a string, not a rendered control. Measured on stash: all
three global keys `refs_in_Settings.tsx=0`, `in_GET=False`, `in_dist=PRESENT`. So today they are
**API-settable but not UI-settable**; the FE controls + a render-verifying RED test are the owed
follow-up (tracked in PR #3). The per-site predictive-relogin controls (Cut 3) DO render — different,
schema-driven path.

**One real bug fixed in Cut 3.** `predictive_relogin_*` was absent from `CFG_FIELDS`, so
`_load_sites_config`'s `{k: … for k in CFG_FIELDS}` rebuild **dropped it on restart** — the F1.4
feature could not persist per-site. Now in `CFG_FIELDS` + `DEFAULTS`; measured on stash:
`predictive_relogin in CFG_FIELDS = True`.

### B. Three sandbox mechanism arms were RUN this session (not just declared)

- **OPV-F2.6** — ran `dom_analyzer` end-to-end against real captures: scan → analyze (0 residual) →
  tree → test (matched selectors) → `pin_candidate` returned **`status=draft_review_required`,
  enabled=False**. Mechanism PASS in-sandbox; the operator draft-through-cockpit arm is still owed.
- **OPV-B2** — the exact check `test_download` applies: a real range fetch of the sanctioned Big Buck
  Bunny target returned **HTTP 206 + MP4 `ftyp` magic bytes**; the no-persist invariant
  (`_override_suppresses_persist`, gating BOTH the live-config and the draft writes) is code-confirmed.
  The full runner+browser integration remains box-only.
- **OPV-F45-METRIC** — computed from the wired cadence constants (`FAST=4s`, `SLOW=30s`,
  `STREAM_SAFETY=60s`): idle-request reduction **93% (busy) / 50% (idle)** when the SSE stream
  connects. The ~80% figure is in-band for the busy case; record the real numbers, not the round one.

### C. Two reproducible diagnostic scripts (committed, PR #3)

- `scripts/bd-stash-report.sh` — captures GUI-config render truth (GET-returns / explicit-control /
  in-bundle per key), backend POST-acceptance (written then reverted), the per-site editable
  descriptor, and OPV preconditions. Read-only apart from reverted writes; emits only
  booleans/counts/http-codes, no secret values.
- `scripts/bd-opv-check.sh` — probes all 11 OPV items' real endpoints + host state and prints a
  READY / NEEDS-SETUP / BLOCKED verdict per item, so this guide's status stays measured.

### D. Measured per-item state on stash (2026-07-20, v3.66.810)

| # | OPV | Measured status | Note |
|---|---|---|---|
| 1 | F2a | **NEEDS-SETUP** | site_health: 0 clusters, 0 per-site rows — no failure history yet |
| 2 | F2.6 | **READY** | 10 captures on disk; mechanism proven in-sandbox |
| 3 | F3.2-LIVE | **forced PASSED; scheduled owed** | last forced run: ran=True, repaired=1 (site `miru`), 7 drafts pending |
| 4 | F3.1/WK | **mechanism READY; week-bound** | 0 saved searches; the 7-day cap proof can't be compressed |
| 5 | F1.4-EN | **NEEDS-SETUP (now GUI-toggleable)** | per-site control renders (810); seed a login site + ≥3 cycles |
| 6 | B2 | **READY** | BBB target 206; real-fetch + no-persist proven in-sandbox |
| 7 | PICK | **display UP; human click owed** | noVNC/KasmVNC listening on :6080 + :8444; takeover enabled |
| 8 | NOVNC-PRECEDENCE | **READY to observe** | /metrics carries `bd_takeover_active=0`; enabled, mode=`remote` |
| 9 | VPNKILL | **BLOCKED** | WireGuard kmod **absent on stash**; netns knob backend-ready, FE control pending |
| 10 | F4.1/F4.5 | **mechanism READY; phone-bound** | manifest `share_target` present; `/dashboard?url=` resolves |
| 11 | F45-METRIC | **code-confirmed** | 93% (busy) / 50% (idle) request-rate drop when SSE connects |

---

## Ordering (do the cheap, zero-risk ones first)

| # | OPV | Gate | Human effort | Risk | Measured |
|---|---|---|---|---|---|
| 1 | **OPV-F2a** | GUI read | 1 tap | none (read-only) | NEEDS-SETUP |
| 2 | **OPV-F2.6** | GUI/API | 1 paste | none (review-only draft) | READY |
| 3 | **OPV-F3.2-LIVE** | cron tick | 1 paste + wait | none (review-only) | forced PASSED |
| 4 | **OPV-F3.1 / F3.1-WK** | week | 1 paste + 7-day wait | low (enqueue cap) | mechanism READY |
| 5 | **OPV-F1.4-EN** | live median | seed logins + 1 tap/paste | low (per-site flag) | NEEDS-SETUP |
| 6 | **OPV-B2** | real download | 1 paste | low (1 real DL, allowlisted) | READY |
| 7 | **OPV-PICK** | noVNC display | operator click | none (draft only) | display UP |
| 8 | **NOVNC-PRECEDENCE** | noVNC display | operator observe | none | READY to observe |
| 9 | **OPV-VPNKILL** | real tunnel | 1 paste | medium (egress fail-closed) | BLOCKED (kmod) |
| 10 | **OPV-F4.1 / F4.5** | phone | phone tap | none | mechanism READY |
| 11 | **OPV-F45-METRIC** | phone + measure | optional | none | code-confirmed |

Batch 1–4 in one session (all low-friction, no device). 5–11 as devices/sessions allow.

---

## 1 · OPV-F2a — site-health = top-failure cluster (GUI read, zero mutation)

**STATUS (810, measured):** NEEDS-SETUP — `/api/data/site_health` returns 0 clusters / 0 per-site rows;
there is no failure history to cluster yet. Run a few downloads that fail on a sanctioned target first,
then this becomes a 1-tap read.

**What it proves:** the worst site by failure count is the top cluster on the site-health report —
F2.1 clustering and F2.2 per-site health are one coherent page. (A tie or a recent-recovery can
legitimately break it, so read it as "the report is coherent," not "row 1 must equal cluster 1 always.")

- **Route (source-confirmed):** `GET /cockpit/reports/site_health` (`app_report_center.py:484`),
  data via `GET /api/data/site_health` (`app_data_layer.py:543`).
- **Prep (Cowork):** open `http://10.0.70.20:5555/cockpit/reports/site_health`, screenshot it, pull
  `/api/data/site_health` JSON for the cluster + per-site arrays.
- **Paste (you):** none — read-only; Cowork drives it. *(If the page needs a logged-in session Cowork
  can't hold, it hands you the URL and reads the result off your screen.)*
- **Observe:** the highest-`failure_count` site appears at/near the top of the cluster list.
- **Pass:** report renders + the worst site is the top (or joint-top) cluster; a documented tie/recovery
  exception is still a PASS with a one-line note.
- **Teardown:** none. **Ledger:** `OPV-F2a coherent (site X = top cluster)` or the noted exception.

---

## 2 · OPV-F2.6 — DOM-Analyzer workbench (API path, review-only draft)

**STATUS (810, measured):** READY — 10 captures on disk. The mechanism (load → tree → test → pin →
`draft_review_required`, never enabled) was run end-to-end in-sandbox this session and PASSED; the
operator arm below is the same flow on stash.

**What it proves:** load a capture → build the DOM tree → test operator selectors → pin a
**review-only** draft (`status='draft_review_required'`, never enabled).

- **Routes (source-confirmed, `app_analyzer.py`):** `GET /api/analyzer/captures` · `POST /api/analyzer/load`
  · `POST /api/analyzer/tree` · `POST /api/analyzer/test` · `POST /api/analyzer/pin`.
- **Prep (Cowork):** CSRF prelude; pick an existing capture **name** from `/api/analyzer/captures`
  (returns `{"captures":[{name,host,...}]}`) for a **sanctioned** site. Snapshot the drafts list.
- **Paste (you)** — one block (CSRF re-minted just before; if it 401s, re-run the prelude). Contract:
  `load`/`test`/`pin` all key on `capture` (the **name**); `test` takes `selectors` as a **list**;
  `pin` takes a **single** `selector` + a required `role`:
```bash
CAP=$(curl -s -b "$JAR" "$BASE/api/analyzer/captures" | python3 -c 'import sys,json;print(json.load(sys.stdin)["captures"][0]["name"])')
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/analyzer/load" -d "{\"capture\":\"$CAP\"}" -o /dev/null -w 'load=%{http_code}\n'
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/analyzer/test" -d "{\"capture\":\"$CAP\",\"selectors\":[\"h1\"]}" | python3 -m json.tool | head -20
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/analyzer/pin" -d "{\"capture\":\"$CAP\",\"selector\":\"h1\",\"role\":\"title\"}" | python3 -m json.tool
```
- **Observe:** the `pin` response carries `status: "draft_review_required"` and `review_required: true`
  — **no** `enabled: true`.
- **Pass:** load=200, test returns match info, pin lands a **review-only** draft — never an enabled template.
- **Teardown (Cowork):** delete the review draft it created, diff the drafts list back to the snapshot.
  **Ledger:** `OPV-F2.6 pin→draft_review_required, draft removed`.

---

## 3 · OPV-F3.2-LIVE — scheduled drift-repair daily run (cron tick)

**STATUS (810, measured):** forced-run path **PASSED** (`/api/automation/drift_repair` last run:
ran=True, repaired=1 on site `miru`, 7 drafts pending). Only the *scheduled* cron-tick observation
remains — it needs the flag enabled + a real day boundary (time-bound, operator-owned).

**What it proves:** the *scheduled* (not forced) daily drift-repair sweep runs and lands review-only
drafts.

- **Prep (Cowork):** confirm a drift-flagged sanctioned site exists (or stage one — capture a demo,
  set a knowably-wrong selector, drive to 5 consecutive failures). Snapshot `drafts_pending` from
  `GET /api/automation/drift_repair`.
- **Paste (you)** — enable the flag (a §5.6 mutation):
```bash
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/global_config" -d '{"automation.drift_repair_enabled": true}' -o /dev/null -w 'enable=%{http_code}\n'
```
- **Observe (after the next daily tick — Cowork polls):** `last_run` advances past the enable time and
  `drafts_pending` increments (or holds if nothing drifted).
- **Pass:** `last_run` shows a scheduled (not `force`) run after enable; any new drafts are review-only.
- **Teardown (you, one paste — flag OFF is a plain flip, no restart):**
```bash
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/global_config" -d '{"automation.drift_repair_enabled": false}' -o /dev/null -w 'disable=%{http_code}\n'
```
- **Cowork then:** restores the staged selector, deletes any drafts it caused, verifies
  `app_config.json` back to baseline. **Ledger:** `OPV-F3.2-LIVE scheduled run observed, reverted`.

---

## 4 · OPV-F3.1 / F3.1-WK — saved-search enqueue lane (week-long)

**STATUS (810, measured):** mechanism READY (0 saved searches configured; endpoints answer 200). The
7-day cap/dedup proof is wall-clock-bound and cannot be compressed.

**What it proves:** a saved search with `action=enqueue` + `daily_cap` holds the cap, produces 0
duplicates, and applies the gates — verified over a week.

- **Route (source-confirmed, `app_saved_searches.py`):** `POST /api/saved_searches` (name required +
  unique, query non-empty), `POST /api/saved_searches/<id>/run`, `GET /api/saved_searches/digest`.
- **Prep (Cowork):** snapshot existing saved searches; pick a query that hits sanctioned targets only.
- **Paste (you)** — create the search:
```bash
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/saved_searches" \
     -d '{"name":"opv-enqueue-wk","query":"failed","action":"enqueue","daily_cap":20}' | python3 -m json.tool
```
- **Observe (Cowork, over 7 days):** each daily `run` respects `daily_cap` (≤20/day), `digest` shows 0
  duplicate URLs, gates applied.
- **Pass:** cap held every day, 0 dups across the week, gates applied.
- **Teardown (you, one paste at week end):**
```bash
SID=$(curl -s -b "$JAR" "$BASE/api/saved_searches" | python3 -c 'import sys,json;d=json.load(sys.stdin);print([s["id"] for s in d if s["name"]=="opv-enqueue-wk"][0])')
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -X DELETE "$BASE/api/saved_searches/$SID" -o /dev/null -w 'delete=%{http_code}\n'
```
- **Ledger:** `OPV-F3.1-WK cap held 7/7 days, 0 dups, search deleted`.

---

## 5 · OPV-F1.4-EN — predictive relogin LIVE (learned median)

**STATUS (810, measured):** NEEDS-SETUP — no sites configured yet. **New in 810:** this is now a
first-class **per-site GUI toggle** (predictive-relogin enable + fraction render in the schema-driven
site editor), AND the drop-on-reload bug is fixed (`predictive_relogin_*` is now in `CFG_FIELDS`, so a
per-site setting survives a restart). Confirmed on stash: `predictive_relogin in CFG_FIELDS = True`.
You can set it from the site editor UI now — the paste block below is the equivalent API path.

**What it proves:** the predictor fires proactively at ~0.8×median of *learned* session lifetimes on a
real site. The sandbox test proved the math; this proves it against `db.session_lifetime_observations()`
real history.

- **Source-confirmed:** the median comes from `db.session_lifetime_observations(site_id, account_idx)`
  (`db.py:1382`); the predictor needs **≥3 learned lifetimes** or it returns "no opinion."
- **Prep (Cowork):** on the sanctioned practice site (`practicetestautomation.com`,
  `student`/`Password123`), confirm the site is configured with a real login flow. F1.4 needs history,
  so **seed 3+ normal login cycles first** (Cowork can script the login drives — authorized use).
- **Set it (you)** — either flip *Predictive relogin* in the **site editor UI** (new in 810), or paste:
```bash
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X PUT "$BASE/api/sites/<sid>" \
     -d '{"predictive_relogin_enabled": true, "predictive_relogin_fraction": 0.8}' | python3 -m json.tool | head
```
- **Observe (Cowork, log-watch):** after ≥3 learned lifetimes, `log_event("preemptive_relogin", …)`
  fires at ~0.8×median, **before** the session would expire. (It now survives a restart — verify by
  restarting the service and confirming the flag is still set.)
- **Pass:** relogin fires proactively at the fraction×median point; before enough history it correctly
  holds "no opinion" (falls back to fixed threshold).
- **Teardown (you):** toggle it off in the UI, or:
```bash
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X PUT "$BASE/api/sites/<sid>" -d '{"predictive_relogin_enabled": false}' -o /dev/null -w 'off=%{http_code}\n'
```
- **Ledger:** `OPV-F1.4-EN preemptive_relogin at 0.8×median (n≥3), persists across restart, reverted`.

---

## 6 · OPV-B2 — real "Test (live)" draft override (one real download)

**STATUS (810, measured):** READY — the sanctioned media target is reachable (BBB 206). This session
proved the extraction half (real fetch → HTTP 206 + MP4 `ftyp` magic) and the no-persist invariant in
code; the operator arm below runs it end-to-end through the runner on stash.

**What it proves:** a draft-test override runs **one real extraction** without persisting selectors or
enabling the draft — and the 4 safety invariants hold.

- **Route (source-confirmed):** `POST /api/sites/<sid>/teach_test_download` (`app_sites_teach.py:382`)
  → body key is **`selectors`** → `runners[sid].teach_test_download(picks)` → `test_download`
  (`runner_manual.py:501`, dry-run real fetch ~2 MB). The no-persist gate is
  `_override_suppresses_persist` (`runner_teach.py:256`), which suppresses BOTH the live-config write
  (`runner_manual.py:606`) and the draft write (`runner_teach.py:128`).
- **Prep (Cowork):** on a sanctioned media target (the `bd-dltest` set — Big Buck Bunny MP4), stage a
  review-only draft with a selector pick. Snapshot the site's persisted selectors + enabled state.
- **Paste (you)** — run the live test off the draft (Cowork fills the exact selector):
```bash
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/sites/<sid>/teach_test_download" \
     -d '{"selectors":{"media":"video source"}}' | python3 -m json.tool
```
- **Observe:** response `ok:true` with the extraction detail; **then** Cowork diffs — the site's
  persisted selectors and enabled flag are **unchanged**.
- **Pass:** one real extraction attempt ran; nothing persisted; draft still `draft_review_required`;
  the 4 invariants hold.
- **Teardown (Cowork):** delete the staged draft, confirm selectors/enabled back to snapshot.
  **Ledger:** `OPV-B2 1 live DL, 0 persisted, 4 invariants hold`.

---

## 7 · OPV-PICK — live element-pick click (noVNC display)

**STATUS (810, measured):** display UP — noVNC/KasmVNC is listening on stash (:6080 + :8444),
`captcha_takeover_enabled=true`. This genuinely needs a **human click**, so it stays operator-owned.

**What it proves:** a real operator click in the picker derives a stable selector and lands a
review-only draft.

- **Prep (Cowork):** bring up the noVNC display (wired on stash). Open the cockpit picker on a
  **sanctioned** capture/site. Write the exact step-sequence on screen with the pass-condition visible.
- **Paste/act (you):** in the noVNC pane, click an element in the picker (the human act).
- **Observe (Cowork):** `inspect_pick.build_selector` derives a stable selector server-side; a
  review-only draft is created. Then run a **Test (live) persist-OFF** off that draft (mechanics =
  OPV-B2) → one attempt, nothing persisted.
- **Pass:** click → stable selector → review-only draft → persist-OFF test runs clean.
- **Teardown (Cowork):** delete the draft, tear down the noVNC display (ephemeral — never leave it up).
  **Ledger:** `OPV-PICK click→selector→draft, persist-OFF verified, display torn down`.

---

## 8 · NOVNC-PRECEDENCE — noVNC precedence live verify (display)

**STATUS (810, measured):** READY to observe — `/metrics` carries `bd_takeover_active=0`/
`bd_takeover_total=0`, `captcha_takeover_enabled=true, mode=remote`. Stage a detection on an official
challenge test page and watch the handoff.

**What it proves:** the noVNC operator-handoff path takes precedence when a challenge is detected
(detect→handoff, never solve — the ethics floor's enabled path).

- **Prep (Cowork):** confirm `captcha_takeover_enabled=true, mode=remote` live (pinned). Open the noVNC
  pane; stage a detection on an *official challenge test page* (never a real gate).
- **Act (you):** observe the handoff surface appear in the noVNC pane when the challenge is detected.
- **Observe:** the remote-takeover channel opens (`bd_takeover_active` increments on `/metrics`); the
  handoff UI renders; **no auto-solve** is attempted.
- **Pass:** detect → handoff channel opens → operator surface renders → zero solve attempt.
- **Teardown (Cowork):** close the channel, tear down noVNC. **Ledger:** `NOVNC-PRECEDENCE detect→handoff, no solve`.

---

## 9 · OPV-VPNKILL — egress fails closed on a REAL tunnel

**STATUS (810, measured):** **BLOCKED** — the WireGuard kernel module is **absent on stash**
(`ip link add … type wireguard` fails), and 0 tunnels are configured. The state machine is already
proven; the live-tunnel arm needs `modprobe wireguard` (or a kernel with it) + a configured tunnel.
Note: the related `netns_isolation` egress-confinement knob is now backend-declared (POST 200) but its
GUI toggle is the pending FE control (see Session-checks A).

**What it proves:** when the VPN tunnel drops, egress fails **closed** end-to-end. **Medium risk** —
you're deliberately killing egress; run when nothing else needs the network.

- **Routes (source-confirmed, `app_vpn_api.py`):** `GET /api/vpn/tunnels` · `GET /api/vpn/kill_switch/state`
  (lists only already-killed tunnels — empty before a kill, so the id comes from `/tunnels`) ·
  `POST /api/vpn/kill_switch/<tunnel_id>/trigger` · `.../clear` · `PUT /api/vpn/kill_switch/auto_recover`.
- **Prep (Cowork):** a real tunnel must be **configured and up** first (device/tunnel gate; also load
  the WG kmod). Read `GET /api/vpn/tunnels` for the id; snapshot `auto_recover`; confirm a safe window.
- **Paste (you)** — trigger the kill (id from `/tunnels`, not `/state`):
```bash
TUN=$(curl -s -b "$JAR" "$BASE/api/vpn/tunnels" | python3 -c 'import sys,json;print(json.load(sys.stdin)["tunnels"][0]["id"])')
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/vpn/kill_switch/$TUN/trigger" -d '{"reason":"OPV-VPNKILL test"}' -o /dev/null -w 'trigger=%{http_code}\n'
```
- **Observe (Cowork):** `GET /api/vpn/kill_switch/state` shows the tunnel `state: "killed"`; a real
  egress attempt is **refused** — fails closed. The callback fires exactly once (idempotent).
- **Pass:** kill → state `killed` → egress refused → single idempotent callback.
- **Teardown (you):**
```bash
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -X POST "$BASE/api/vpn/kill_switch/$TUN/clear" -o /dev/null -w 'clear=%{http_code}\n'
```
- **Cowork:** confirms `state` back to normal, egress restored, `auto_recover` back to snapshot.
  **Ledger:** `OPV-VPNKILL fail-closed on real tunnel, cleared`.

---

## 10 · OPV-F4.1 / F4.5 — phone share-target (device)

**STATUS (810, measured):** mechanism READY — `static/manifest.json` carries `share_target`,
`/dashboard?url=` resolves (200). The 2-tap flow needs a **phone** with the PWA installed (device-bound).

**What it proves:** a 2-tap phone share into BD lands a URL in the resolve/add flow via the PWA
share-target, and (F4.5) idle polling backs off while the SSE stream is live.

- **Mechanism (source-confirmed — NOT `/api/shares`).** The manifest `share_target` is a **GET to
  `/dashboard`** with params `{title, text, url}`. A phone "share to BD" opens `/dashboard?url=…`; the
  Dashboard's `RouteLookupPanel` (`Dashboard.tsx`) pulls the shared URL into the resolve box and strips
  the query params. **Two taps:** (1) share from the OS share sheet, (2) tap **Resolve** — which calls
  `/api/route_urls` to resolve which site the URL routes to. **F4.5** is the SSE-safety backoff: while
  `/api/stream` is connected, dashboard polling drops to `STREAM_SAFETY=60s` (see §11).
- **Prep (Cowork):** confirm the PWA is installed on your phone; confirm the share-target is registered
  (open `/dashboard?url=https://example.org/x` and see the resolve box pre-fill).
- **Act (you):** on the phone, share a **sanctioned** URL into BD via the OS share sheet, then tap
  **Resolve** — the 2-tap flow.
- **Observe (Cowork):** the resolve box arrives pre-filled (params stripped from the address bar);
  Resolve returns a `/api/route_urls` result mapping the URL to a site (or "no match" — still correct).
- **Pass:** 2-tap share pre-fills the resolve box and Resolve returns a routing result end-to-end.
- **Teardown:** none. **Ledger:** `OPV-F4.1 2-tap share→/dashboard→resolve, routed`.

---

## 11 · OPV-F45-METRIC — idle-request reduction measurement (optional)

**STATUS (810, measured):** code-confirmed. From the wired cadence constants (`FAST=4s`, `SLOW=30s`,
`STREAM_SAFETY=60s`): connecting the SSE stream cuts the `/api/dashboard` poll rate by **93% from the
busy cadence** (900→60 req/hr) and **50% from the idle cadence** (120→60 req/hr). A live wall-clock
measurement is the optional confirmation.

**What it proves:** the F4.5 idle-request reduction (dashboard polling backs off to `STREAM_SAFETY=60s`
while the SSE stream is connected, vs the fast/adaptive cadence when it's not) is actually measurable.

- **Prep (Cowork):** capture the dashboard request rate over a quiet window with the SSE stream
  **disconnected**, then over a quiet window with `/api/stream` **connected**. Both from server
  request logs or a `/metrics` counter delta.
- **Act (you):** none beyond having a dashboard open in each state.
- **Observe (Cowork):** compute the delta between the two windows.
- **Pass:** the measured reduction is in the ballpark of the code-derived 93%/50%; record the *actual*
  number, don't assert a round figure.
- **Teardown:** none. **Ledger:** `OPV-F45-METRIC idle requests −N% (SSE-connected vs polling, measured)`.

---

## Session-close for a completion batch

1. Every envelope closed (any unclosed = FINDING).
2. Move each PASS from "remaining" to "completed" in the live status doc with the evidence pointer.
3. Propagate to the tracker (C→D), roadmap, deferred as applicable.
4. Assemble the evidence pack addendum; **STOP** — present PASS/FAIL dispositions; **Matt attests** (§0).
5. The stale-corpus cleanup is a prerequisite for any run that would touch the template corpus.

**What stays true across all of it:** the ethics floor (sanctioned targets, detect-not-solve, no
adult/piracy/registry), secret-hygiene (anchored greps, no value echoing), and auth-off-needs-restart
are non-negotiable regardless of who pastes the command.

---

## Reproduce this status yourself

```bash
cd ~/BulkDownloader
bash scripts/bd-stash-report.sh ~/BulkDownloader http://127.0.0.1:5555   # GUI-config render truth
bash scripts/bd-opv-check.sh    ~/BulkDownloader http://127.0.0.1:5555   # per-item READY/NEEDS-SETUP/BLOCKED
# upload the two /tmp/*.tar.gz for analysis
```
