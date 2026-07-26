<!-- verified-against: v3.66.470 (top-of-headroom posture + boundaries); state-table labels carried from v3.66.185 except rows marked [re-verified @470] -- re-confirm the unmarked rows against source before trusting them. Auth-as-authorization: an authenticated operator session is the access signal; the floor restricts only circumventing a control or leaking a secret, never the operator's legitimate access. -->
# BulkDownloader — Automation Policy

**Canonical automation doc.** The charter, goals, and operating instructions reference this file;
they do not restate the lists below. This replaces the prior revision and adopts a **maximalist
automation-positive posture**: automate the entire operational lifecycle wherever the automated
path stays inside the hard boundaries, and keep humans only where a human's *authorization* is the
input the system genuinely lacks.

"Allowed / preferred" is *policy*, not a claim that something is already built. Every item below
carries its **current implementation state**:

- **IMPLEMENTED** — in source and exercised by tests / proven.
- **PARTIAL** — a piece exists, but not the full automated behavior described.
- **PLANNED / ROADMAP** — intended; not built yet.
- **NOT IMPLEMENTED** — explicitly absent today.

**Anything not yet proven in source/tests is PLANNED, not current behavior.** But PLANNED is an
invitation, not a wall: automate the preparation, evidence, validation, and review queue around it,
and build toward the item landing. The sequenced build plan lives in `BD_FORWARD_ROADMAP.md`.

## Posture (the maximalist default)

**Automate the whole loop; humans only at genuine trust boundaries.** For an already-approved host,
accessed through an authenticated operator session via site-provided playback/download controls, the
full lifecycle — capture → normalize → lint → test → backup → refresh → quarantine → promote →
retry → recover → roll back — should run **unattended when the gates pass**, with no per-action
checkpoint.

**Maximal automation makes the *safety* checks MORE aggressive, not less.** The credential /
signed-URL scrub, the secret-redaction floor, the SSRF guard, and the VPN egress fail-closed all run
on **every artifact, automatically, every time**. "Maximally automated" and "maximally enforced" are
the same direction — the machine does the checking, deterministically, with no manual step to
forget.

**Autonomy is safe because it is logged + reversible, not because it is timid.** Every autonomous
write emits a staged evidence bundle and is fully reversible (gold-backup restore / rollback
artifact). The aggressiveness above is only licensed alongside the master controls below.

### Top-of-headroom levers (the maximal autonomous defaults)

- **Authenticated access is the authorization signal.** An authenticated operator session for a host
  *is* the operator asserting "I have access here." Where such a session exists, automation does not
  ask a second time — including for a **first-time host** (the checkpoint set downgrades it from a
  blocking confirm to a notification + one-click undo). A blocking confirm survives only for a host
  with **no** authenticated session, because there nothing is asserting access.
- **Gray-area decisions default to AUTO, not review.** For an approved host, a borderline-but-passing
  decision (marginal drift under threshold, a resolution-floor edge, an ambiguous-but-clean diff)
  resolves toward auto-action with evidence staged, not toward a manual queue. Review is for genuine
  uncertainty (a failed gate), not for caution.
- **The controller auto-tunes within operator-set bounds.** Thresholds (drift tolerance, retry/backoff
  windows, concurrency, resolution floor) are operator-bounded ranges; the controller adjusts itself
  inside them from observed outcomes without asking — it cannot exceed the bounds, only move within.
- **Throughput opens up within site-provided flows.** Parallelism / concurrency / scheduling run as
  aggressively as the site-provided controls and the operator's tunnel/rate budget allow; the only
  ceiling is what the authenticated session is legitimately served.

## Automation-first tiebreak

**Default to automation.** A guardrail should be implemented as an automated check, lint, redaction
pass, drift detector, backup step, staged diff, or review queue wherever possible — never as a
reason to make a workflow manual by default.

**Approval checkpoints are lightweight confirmations, not manual workflows.** The system should
auto-capture, auto-build, auto-normalize, auto-lint, auto-test, auto-stage, auto-diff, and
auto-recommend — and auto-staging a review bundle with evidence is the *default*, not an opt-in. By
the time a human sees a checkpoint, the work is done and staged; the checkpoint is a quick yes at a
new trust boundary, not a place to redo automation's job by hand.

**Do not auto-satisfy a safety gate by skipping it.** If a check cannot prove the workflow is inside
policy, route the result to review with logs and evidence instead of guessing.

## Current state in one line

- Runtime **can** auto-detect and auto-apply an **already-enabled** reviewed template for a matching
  host. **[IMPLEMENTED]**
- Unknown / new hosts may be fully prepared by automation into a draft / review candidate; runtime
  enable requires explicit approval. **[IMPLEMENTED]** for the non-enabled state handling.
- The **write-side lifecycle** (auto-backup → drift-gate → refresh/repair → quarantine → promote) is
  the roadmap; the keystone (guaranteed gold-backup-with-restore) gates the autonomous writes. See
  `BD_FORWARD_ROADMAP.md`.

## Allowed automation (with current state)

| Automation | State | Note |
|---|---|---|
| Auto-detect approved reviewed templates for matching hosts | **IMPLEMENTED** | `template_registry.find_template_for_url` |
| Auto-apply reviewed templates at runtime | **IMPLEMENTED** | enabled-only; `template_registry` + `template_assist` |
| Auto-reject navigation-looking URLs as downloads | **IMPLEMENTED** | `candidate_filter` + `runner.gate_candidate_url` (tested, 146) |
| Auto-run selector lint + credential/signed-URL scrub | **IMPLEMENTED** | run inside `normalize_draft`; `promote_template` credential-fragment gate (`bad_terms.py` — see hard floor) |
| Auto-run WACZ build + normalization into drafts/candidates | **IMPLEMENTED** | chain CLIs + cockpit `POST /api/captures/normalize` (tested, 158) |
| Auto-generate capture commands/scripts | **IMPLEMENTED** | cockpit builds capture invocations; operator-initiated |
| Auto-stage review bundles with evidence and recommendations | **SUPPORTED / PLANNED** | the intended **default** for new hosts and drift results — automation stages the full bundle so the operator just confirms; label current code honestly per implementation |
| Auto-run drift checks | **PARTIAL** | `app_selector_drift.py` is a dedicated module; not yet wired into a scheduled gate (roadmap A1) |
| Auto-run template onboarding after site create/update | **PARTIAL** | `POST /api/sites/<sid>/template_onboard` exists but is operator-triggered, not auto-on-create (roadmap A4) |
| Auto-sync authenticated profile state after manual login | **PARTIAL** | `profile_sync.sync_manual_to_runtime` triggered; has a timestamped move-aside backup w/ restore |
| Auto-package offline dependencies | **PARTIAL** | offline / kit build process (on stash); not a one-button in-tree flow |
| Auto-run regression tests + produce release snapshots | **PARTIAL** | `run_tests.py` + build scripts exist; manual invocation, no CI (roadmap A6) |
| Auto-backup gold before overwrite / staging swap | **PARTIAL** `[re-verified @470]` | `promote_draft` does a *best-effort* backup; `profile_sync` has a real timestamped move-aside+restore. The guaranteed **gold-backup-with-restore** that gates autonomous writes is roadmap A0 (KEYSTONE) |
| Auto-refresh a previously approved template when gates pass | **PLANNED** | roadmap A2 (headline); gated on A0 backup — no checkpoint for an approved host when all gates pass |
| Auto-disable / quarantine a template on drift or risky content | **PLANNED** `[re-verified @470]` | manual `template_manager.disable_reviewed` exists; auto-on-drift + a real `quarantined` runtime state are roadmap A3 |
| Auto-promote a reviewed candidate to enabled | **PLANNED** | roadmap A5; after A0 backup + staged-diff; for a host under an authenticated session, auto + notify/undo (blocking confirm only for a no-session host) |
| Supervised-autonomy controller (operator sets policy once; loop runs unattended) | **PLANNED** | roadmap A9 (capstone); requires the master off-switch + full reversibility proven first |

## The checkpoint set (now mostly notify + undo, not blocking)

Automation prepares + stages the full bundle and **acts**; reversibility (gold-backup restore /
one-click undo) is the backstop, not a pre-action gate. The list is short **by principle** — each
item is a place where automation cannot *prove* it is inside policy. At the top of headroom, most are
a **notification + undo** after the fact; only the no-authorization case stays a blocking confirm:

1. **First-time host *with* an authenticated operator session → AUTO-ENABLE + notify + undo.** The
   authenticated session is the authorization; automation does not ask a second time. It enables,
   logs it, and surfaces a one-click undo. **First-time host with *no* authenticated session → still a
   blocking confirm** — nothing is asserting access, and auto-onboarding anonymous unknown hosts is
   the auto-scrape-anything shape.
2. **A new or changed API base host** not previously approved → notify + undo (the host is reachable
   under the operator's authenticated session); a blocking confirm only if no session covers it.
3. **A selector / path / API change that fails drift / staged-diff checks** → routes to review with
   evidence. A *failed* gate is genuine uncertainty; this stays a real review (not caution — proof).
4. **A credential / signed-URL fragment in reusable template material** → auto-scrubbed at normalize
   time (see floor); routed to review only if load-bearing to the pattern. A *secrets/correctness*
   boundary, not an access one.

## Hard boundaries for automation (the floor — ENFORCED, never relaxed)

The project automates normal authenticated browser workflows and approved site-provided flows. The
floor is narrow and **none of it limits the operator's legitimate access** — it draws the line only
at circumventing a control or leaking a secret. Maximalism runs every item here **by default, on
every artifact**; no autonomy setting turns them off.

- **Authenticated access is in scope; circumventing the auth is not.** Downloading what an
  authenticated operator session is legitimately served — behind a login, behind a paid account, or
  routed through the operator's own VPN — is *using the access you have*, not bypassing anything.
  Paywall / geo / login are **not** boundaries here. The floor is fabricating or circumventing the
  authentication itself.
- **No challenge / captcha *defeat* at scale.** Challenges may be detected, logged, attempted once,
  then paused and handed to the operator. Automating past a bot-check at scale is circumventing an
  access control — a different act from using a login — and is out of scope, permanently.
- **No DRM / stream-encryption circumvention.** The tool captures the site-provided playback the
  authenticated session is served; defeating stream encryption / DRM is a separate technical act
  (you can be logged in *and* the stream still be encrypted — different things) and is out of scope.
- **Credential / signed-URL scrub.** The narrow fragments — `token=`, `signature=`, `expires=`,
  `auth=`, `sig=` — are scrubbed from reusable template material on every artifact, fail-closed. This
  is **not a restriction on access**: it's correctness (a signed URL carries `expires=` and is
  broken-by-construction in a reusable template) plus anti-leak (a persisted token leaks via a shared
  template or evidence bundle). Never persist credentials, tokens, signed/expiring URLs, or challenge
  artifacts in templates.
- **Secret-redaction + SSRF + VPN floor.** The secret-redaction pass runs on every artifact,
  fail-closed; the SSRF transport guard and VPN egress fail-closed are on by default (a
  `vpn_required` host with a dead tunnel never egresses on the clear interface).
- **Don't treat navigation as media.** Generic links (`/`, `/movies`, `/settings`, broad `a[href]`)
  are not reusable media downloads — a correctness boundary (it produces broken templates), not an
  access one.

> **`bad_terms.py` clarification:** it is **template hygiene + credential scrub**, not a content /
> CSAM classifier (no content classifier of that kind exists in source today). Its bare-word
> noise-hygiene half (`comments`, `votes`, `experiments`, `banners`, CDN/telemetry hosts) is a
> *correctness/cleanliness* filter matched as a case-insensitive substring of the full URL, which
> false-positives on legitimate media paths — it is **relaxed** to a precise advisory auto-drop at
> normalize time, not a hard promote-blocker. The credential-fragment half is the real safety floor
> and is kept. If a genuine content-safety check is ever wanted at an autonomous auto-promote
> boundary, that is a *separate* deliberate decision — this doc does not claim `bad_terms.py`
> provides it.

> **`bad_terms.py` clarification:** it is **template hygiene + credential scrub**, not a content /
> CSAM classifier (no content classifier of that kind exists in source today). Its bare-word
> noise-hygiene half (`comments`, `votes`, `experiments`, `banners`, CDN/telemetry hosts) is a
> *correctness/cleanliness* filter matched as a case-insensitive substring of the full URL, which
> false-positives on legitimate media paths — it is **relaxed** to a precise advisory auto-drop at
> normalize time, not a hard promote-blocker. The credential-fragment half is the real safety floor
> and is kept. If a genuine content-safety check is ever wanted at an autonomous auto-promote
> boundary, that is a *separate* deliberate decision — this doc does not claim `bad_terms.py`
> provides it.

## Master controls (required by the maximalist posture)

The aggressiveness of this policy is only licensed alongside all of:

- **Instant master off-switch** — one operator action reverts the entire system to manual
  immediately. Always available; not gated behind any state.
- **Full reversibility** — every autonomous write is restorable (gold-backup, roadmap A0) or
  rollback-able; nothing autonomous is irreversible.
- **Complete audit trail** — every autonomous action logs what / why / evidence and is replayable.
- **Operator-set policy, once** — drift tolerance, resolution floor, content rules, and the
  trusted-auto host set are operator-configured; the loop runs *within* that policy and surfaces only
  the trust-boundary exceptions.

## Choosing automated vs manual (the automation-first rule)

Prefer the automated path over a manual one when the automated path:

1. uses an authenticated operator session,
2. uses site-provided playback/download controls or approved site API endpoints,
3. does not persist secrets or signed URLs in templates,
4. passes lint / credential-scrub / drift checks,
5. has logs, tests, evidence, and rollback,
6. fails into review on uncertainty instead of guessing.

Lint, drift detection, secret redaction, backup creation, staging diffs, tests, runtime use of
approved templates, and review-bundle creation are all automation's job — and run by default. Manual
input is reserved for the final confirmation at the narrow trust boundaries above and the rare
exception automation cannot prove safe.

## Challenge-system posture

Challenge systems may be detected, logged, attempted once, and surfaced to the operator. Automation
may attempt once, pause, preserve context, and hand control to the operator when normal authenticated
access requires manual action. Automation whose purpose is to defeat the challenge at scale is out of
scope (hard floor).

## Template states

- `draft` — generated from capture; never runtime-enabled. **[IMPLEMENTED]**
- `review_candidate` — normalized and linted; never runtime-enabled. **[IMPLEMENTED]**
- `reviewed_not_enabled` — human-reviewed but not active (`promote_template` without `--enable`).
  **[IMPLEMENTED]**
- `enabled` — approved runtime template; the only status the runtime loads. **[IMPLEMENTED]**
- `disabled` — intentionally inactive. **[PARTIAL]** — `disable_reviewed` can set it, but the runtime
  gate is binary (anything ≠ `enabled` is simply not loaded), so `disabled` currently behaves like
  "not enabled."
- `quarantined` — blocked by drift / risk checks. **[PLANNED]** — no distinct quarantine state yet
  (roadmap A3); would behave like "not enabled" until code distinguishes it.

## Default behavior

- **Known approved host:** apply the reviewed (enabled) template first, then safe fallbacks; on a
  passing re-capture + successful gold-backup, **auto-refresh in place with no checkpoint** (roadmap
  A2). **[IMPLEMENTED]** for apply; **[PLANNED]** for autonomous refresh.
- **Unknown host *with* an authenticated operator session:** automate capture / build / normalize /
  lint / test / stage **and auto-enable**, with notify + one-click undo (the session is the
  authorization). **Unknown host with *no* session:** stage into a draft / review candidate and
  require explicit approval before runtime enable. **[IMPLEMENTED]** for non-enabled state
  handling; auto-onboard-on-create is **PARTIAL** (roadmap A4).
- **Uncertain detection / failed drift:** fail into review with evidence. **[IMPLEMENTED]** for
  nav/selector rejection; drift-triggered review is **PARTIAL** (roadmap A1).

---

*Verify states against source:* `bulk_downloader/template_registry.py`, `template_assist.py`,
`candidate_filter.py`, `runner.py` (`gate_candidate_url`), `template_normalize.py`,
`tools/promote_template.py`, `template_manager.py`, `profile_sync.py`, `app_selector_drift.py`,
`bad_terms.py`. **Re-label here when an item lands in source + tests.** The state-table labels not
marked `[re-verified @470]` were carried from the prior revision (v3.66.185) — re-confirm them
against source before trusting them (stale-copy-of-derived-fact applies). The build sequence for the
PLANNED items is `BD_FORWARD_ROADMAP.md`.
> [!WARNING]
> **Historical archive - do not execute.** This file is a point-in-time record. Its commands, procedures, paths, versions, and acceptance criteria may be obsolete; use active documentation and current release gates instead.
