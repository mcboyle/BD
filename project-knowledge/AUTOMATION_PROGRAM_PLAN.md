<!-- verified-against: v3.66.593 -->
# AUTOMATION PROGRAM — BUILD PLAN (A0–A9)

> **Current status (2026-07-21): BASE PROGRAM SHIPPED.** The base A0-A9
> sequence landed across v3.66.690-708; v3.66.708 records the connected A9
> capstone after the 706 restore rehearsal and 707 autonomous-cycle ceiling,
> and v3.66.709+ hardened the GUI write/read contract. Sections describing A0
> as partial are historical. Expansion tracks remain candidates unless a
> current tracker row and fresh source evidence say otherwise.

<!-- authored against live baseline v3.66.593. This is the sequenced BUILD PLAN that AUTOMATION_POLICY.md -->
<!-- references as "BD_FORWARD_ROADMAP.md" (which did not exist). It fulfills that reference for the -->
<!-- automation sequence. POLICY says WHAT/WHY + honest current-state labels; THIS says ORDER/DEPS/GATES. -->
<!-- No cut/build authorized by this doc; every execution step remains per-task gated. -->

Pairs with: `AUTOMATION_POLICY.md` (policy + supported/planned labels — the source of truth for
current-state honesty), `PROJECT_GOALS.md` (durable goals 1/2), the `TASK_TRACKER` (rows `A0`..`A9`).

---

## 0. The strategic frame (operator decision, unchanged)

Automation may **prepare + stage + act**, but every autonomous write must be **fully reversible**
and **evidence-staged**. Two non-negotiable gates sit under the whole program:

- **KEYSTONE (A0):** a *guaranteed* gold-backup-with-restore. **No autonomous write ships until A0 is
  real.** Today only best-effort/partial backups exist (`promote_draft` best-effort; `profile_sync`
  has a real timestamped move-aside+restore).
- **MASTER OFF-SWITCH + full reversibility proven** gates the capstone (A9) supervised-autonomy loop.

Approval boundary stays: unknown hosts / first-time templates may be prepared as drafts; **runtime
enablement of a new host still requires explicit approval.** Previously approved hosts may be
auto-applied; auto-refresh/auto-repair are what this program builds, gated on A0.

---

## 0.1 Autonomy dial — OPERATOR SET TO **L2** (@593 decision)

The operator picks an autonomy level; each level unlocks only after its reversibility is proven. The
Bucket-A hard lines hold at **every** level (no circumvention, no redistribution, credential floor,
new-host approval). What changes across levels is only *how far autonomy acts on **previously
approved** hosts.*

| Level | Behavior on approved hosts | Requires |
|---|---|---|
| L0 | Fully manual | — |
| L1 | Prepare drafts / staged bundles only; operator does every write | — |
| **L2 — CURRENT** | **Auto-act (refresh / promote) then NOTIFY; reversible via A0, no mandatory hold-window** | **A0 (verified backup)** |
| L3 | Auto-act with a mandatory undo-window before it takes effect | A0 + undo-window |
| L4 | Full supervised loop (operator sets policy once) | A0 + master off-switch + full A0–A5 reversibility |

**Consequence of choosing L2: A0 becomes the first thing to build.** "Act + notify" is an autonomous
write, and the standing rule is every autonomous write must be backed-up-and-restorable first. A0 is
currently PARTIAL, so **L2 is not yet safe to run** — it becomes legitimate the moment A0 (§2, verified
gold-backup) lands. Until then, BD operates effectively at L1 (stage + operator-confirm). The A2
(auto-refresh) and A5 (auto-promote) items are exactly the L2 actions; both are already A0-gated in §2.

**Fixed at L2 (and every level):** new-host runtime enablement still needs explicit approval; every
auto-action carries an undo handle + evidence bundle; drift/quarantine (A3) and rate/blast limits
(AR4) stay in force; findings reported as kinds/counts, never values.

---

## 1. Current building blocks @593 (what's on disk)

`auto_ci.py`, `auto_detect.py`, `auto_onboard.py`, `auto_promote.py`, `auto_queue.py`,
`auto_recover.py`, `automation_controller.py`, plus `app_selector_drift.py` (drift module, not yet
scheduled), `profile_sync.sync_manual_to_runtime` (real backup+restore), `template_manager`
(`disable_reviewed`), `POST /api/sites/<sid>/template_onboard` (operator-triggered).

---

## 2. The A-items — status, dependency, scope

Status labels mirror `AUTOMATION_POLICY.md` (keep them honest per implementation).

| # | Item | Status @593 | Depends on |
|---|---|---|---|
| **A0** | **Guaranteed gold-backup-with-restore (KEYSTONE)** | PARTIAL | — |
| A1 | Auto-run drift checks (wire `app_selector_drift.py` to a scheduled gate) | PARTIAL | — (A0 to *act* on result) |
| A4 | Auto-run template onboarding on site create/update | PARTIAL | A0 (to stage/act) |
| A6 | Auto-run regression + release snapshots (CI) | PARTIAL | — |
| A2 | **Auto-refresh approved template when gates pass (headline)** | PLANNED | **A0**, A1 |
| A3 | Auto-disable/quarantine on drift + a real `quarantined` runtime state | PLANNED | A0, A1 |
| A5 | Auto-promote a reviewed candidate to enabled | PLANNED | A0, staged-diff |
| A9 | **Supervised-autonomy controller (capstone)** | PLANNED | ALL above + master off-switch + reversibility proof |

### Per-item scope

**A0 — gold-backup-with-restore (KEYSTONE). Build FIRST.**
- Guarantee: before any autonomous overwrite/staging-swap, capture a restorable gold snapshot of the
  affected artifact(s) (template + reviewed state + runtime enablement) with a tested `restore()`.
- Definition of done: a snapshot API + a restore path with a test that (a) mutates, (b) restores,
  (c) asserts byte-identical return to pre-mutation state; wired so every A2/A3/A5 write calls it and
  **fails closed** if the backup didn't land. Generalize `profile_sync`'s move-aside+restore pattern
  (already real) into the shared primitive; replace `promote_draft`'s best-effort backup with it.
- Risk: MODERATE (touches promote/staging paths). Highest leverage — unblocks A2/A3/A5.

**A1 — scheduled drift checks.**
- `app_selector_drift.py` exists; wire it into a scheduled/triggered gate that runs on a cadence (or
  on capture) and records a drift verdict per site. **Detection only** in A1 (route-to-review, don't
  act). Acting on drift is A3 (needs A0).
- DoD: a scheduler entry + a persisted drift verdict + a review surface; no autonomous mutation.

**A4 — auto-onboard on create/update.**
- Make `template_onboard` fire automatically on site create/update (currently operator-triggered),
  staging a review bundle rather than enabling. DoD: create/update path invokes onboarding, produces
  the staged bundle, operator confirms (no runtime enablement without approval — goal 2).

**A6 — CI + release snapshots.**
- `run_tests.py` + build scripts exist but are manual. DoD: a repeatable CI entry that runs the band
  and produces a release snapshot artifact. (Independent of A0; can proceed in parallel. Note the
  binding full band is on-stash `capture.sh` — CI here means *automating that invocation*, not
  replacing it.)

**A2 — auto-refresh approved template (HEADLINE). Gated on A0 + A1.**
- For an already-approved host: on a passing re-capture + successful A0 gold-backup, refresh the
  template **in place with no checkpoint** (apply is already IMPLEMENTED; autonomous refresh is the
  new part). DoD: gate chain = drift/gates pass (A1) → A0 backup lands → refresh → notify + undo
  handle. Fails closed at any gate.

**A3 — auto-disable/quarantine on drift + real `quarantined` state.**
- Add a distinct `quarantined` runtime state (today only `disabled` via manual `disable_reviewed`;
  `quarantined` behaves like "not enabled" until code distinguishes it). On drift/risky-content
  detection (A1), auto-transition to `quarantined` with evidence + operator notification + undo.
- DoD: the state exists in the runtime model, drift can set it, status surfaces it, restore via A0.

**A5 — auto-promote reviewed candidate to enabled.**
- After A0 backup + a staged diff: for a host under an authenticated session, auto-promote +
  notify/undo; for a no-session host, keep the blocking confirm. DoD: staged-diff gate + session
  check + A0 backup + promote + undo handle.

**A9 — supervised-autonomy controller (CAPSTONE). LAST.**
- Operator sets policy once; the loop runs unattended within the approved envelope. **Requires the
  master off-switch + full reversibility proven across A0–A5 first.** DoD: a policy object, an
  unattended loop that only performs A2/A3/A5 actions inside the approved envelope, a single master
  kill that halts all autonomous writes immediately, and an audit trail of every autonomous action
  with its evidence bundle + undo handle.

---

## 3. Sequenced build order (dependency-correct)

```
Phase 1 (unblockers, parallel-safe):   A0 (keystone) ─┬─────────────► gates A2/A3/A5
                                        A1 (drift)  ───┤
                                        A6 (CI)     ───┘ (independent)
                                        A4 (onboard-on-create) — after A0 for staging

Phase 2 (autonomous actions, need A0+A1): A2 (refresh) → A3 (quarantine) → A5 (promote)

Phase 3 (capstone, needs all + off-switch): A9 (supervised autonomy)
```

Rationale: A0 is the single hard dependency for everything that *writes*; build it first and make
every autonomous write fail-closed on it. A1 and A6 are detection/CI and can land in parallel. A2 is
the headline value and comes first in Phase 2; A3/A5 follow. A9 is deferred until the whole envelope
is reversible and a master off-switch exists.

---

## 4. Standing constraints for EVERY automation cut

- **RED-first TDD**, guards byte-identical (none of the automation modules are in the 7 guards —
  confirm vs `STATE.guards` each cut; a new import edge needs the import-graph baseline re-freeze).
- **Every autonomous write is reversible** (A0 backup+restore) and **evidence-staged** (the operator
  gets a bundle + undo handle). A write that can't back up **does not run**.
- **Approval boundary intact**: no runtime enablement of a *new* host without explicit approval
  (goal 2). Auto-* applies to *previously approved* hosts.
- **Report drift/redaction findings as kinds/counts, never values.**
- Registry sites are adult cam sites — **never build fixtures mimicking them or test them live**;
  synthetic fixtures only.
- Full band is on-stash `capture.sh` (Matt runs it); sandbox is targeted suites only.

---

## 5. Definition of done (program)
A0 real and load-bearing; A1/A4/A6 wired; A2/A3/A5 landed each RED-first behind the A0 gate with
notify+undo; A9 running only inside an operator-set policy with a proven master off-switch and full
A0–A5 reversibility. `AUTOMATION_POLICY.md` labels updated to match at each landing (PLANNED →
PARTIAL → SUPPORTED honestly per implementation).

---

## 6. Expansion tracks — beyond A0–A9 (candidate, unprioritized)

**Autonomy in BD is bounded by design:** reversible, evidence-staged, approval-boundaried, no
ToS-violating or evasion action, credential floor ON. Every idea below means "more autonomous
**within that envelope**," never outside it. IDs are for the `TASK_TRACKER`.

### More ROBUST (safer autonomy)
- **A0+ — Verified backup, not just existence.** After A0 captures a gold snapshot, **RESTORE it into
  a scratch space and assert byte-identical BEFORE the write proceeds**. "A backup that can't be
  proven restorable doesn't count." Strengthens the keystone itself.
- **AR1 — Transactional multi-artifact writes.** An autonomous action touching template +
  reviewed-state + runtime-enablement is **all-or-nothing with a single rollback**, not per-artifact
  best-effort. Eliminates partial-write corruption.
- **AR2 — Canary-then-promote.** An auto-refresh applies to **one canary capture first**, verifies
  success, THEN promotes. Replaces refresh-then-hope with refresh-verify-promote.
- **AR3 — Health-gated autonomy interlock.** The loop acts only when health signals are green (disk,
  service up, recent capture success, no error spike); a red signal **de-escalates to draft-only**.
- **AR4 — Blast-radius / rate limits.** At most N autonomous writes per window; exceeding **halts +
  notifies**. Prevents a runaway loop.
- **AR5 — Anomaly-aware drift.** Learn each site's normal drift signal; flag outliers instead of a
  fixed threshold → fewer false auto-quarantines. *Transparent + operator-overridable.*
- **AR6 — Dead-man's heartbeat.** If the operator doesn't acknowledge periodic autonomy summaries,
  the loop **auto-de-escalates to draft-only**.

### More FEATURE-RICH
- **AF1 — Confidence-scored decisions.** Each candidate action gets a score (gates passed + drift
  magnitude + capture-success history, via BD's existing SCORING surface); **high → auto, low →
  review**. Smarter than a binary gate.
- **AF2 — Policy-as-config (autonomy DSL).** The operator *declares* the envelope: per-site
  (auto-refresh yes / auto-promote no), global rate limits, quiet hours, confidence thresholds. This
  declared policy is the **ceiling** for everything autonomous.
- **AF3 — Auto-recovery playbooks.** Formalize `auto_recover.py` into ordered remediations on a
  capture failure (re-auth via `profile_sync` → retry w/ backoff → fall back to prior template)
  before escalating to the operator.
- **AF4 — Gold-backup retention policy.** Keep last N per artifact, prune older (with its own
  safety); prevents A0 snapshots accumulating unbounded.
- **AF5 — Autonomy timeline / audit dashboard.** A cockpit panel (ties to plugin `@ui_panel`) showing
  every autonomous action, its evidence, confidence, and one-click undo. Operator oversight.
- **AF6 — What-if / dry-run loop.** Run the whole loop against current state and show what it *would*
  do without acting; the operator builds trust before enabling. (Pairs with plugin P-R5.)
- **AF7 — Operator digest.** A daily summary (via `daily_digest.py`) of all autonomous actions,
  drift, quarantines, and pending approvals — **one touchpoint instead of many notifications.**

### More AUTONOMOUS (the capstone and beyond)
- **AA1 — Tiered autonomy levels (L0–L4).** L0 manual → L1 prepare-drafts → L2 act-on-approved-hosts
  + notify → L3 act-with-undo-window → L4 full supervised loop. The operator **dials the level**, and
  each level unlocks only after its reversibility is proven. This is the clean, safe framing for A9.
- **AA2 — Self-tuning cadences.** Drift-check / refresh cadence adapts to each site's observed change
  rate (a weekly-changer checked weekly; a stable one monthly). Less manual tuning.
- **AA3 — Cross-site orchestration.** The loop schedules captures across sites respecting concurrency
  / politeness / rate limits **within site-provided flows** (throughput within approved flows —
  goal-aligned).
- **AA4 — Learning from operator decisions.** Record approve/reject on staged bundles; bias the AF1
  confidence model toward the operator's revealed preferences over time. **RISK: a feedback loop —
  keep it transparent, inspectable, operator-overridable; it must NEVER silently widen the envelope
  (AF2's declared policy is the ceiling).**
- **AA5 — Graceful master off-switch.** Beyond a hard kill: "**finish in-flight, take no new actions,
  drop to draft-only.**" A safer capstone control than a blunt halt.

### Cross-program bridge
- **AX1 — Plugin-driven automation.** The plugin `@scheduled` (P-A1) and `@automation_policy` (P-A2)
  kinds let operators extend the loop with their own cadenced tasks and policies — **inside the same
  A0-gated, reversible, approval-bounded envelope.** The two programs compound: automation provides
  the *safe execution envelope*; plugins provide *extensibility*.

### Honest guardrails (the line that must hold)
- "More autonomous" **NEVER** means "acts outside the approved envelope." Every new autonomous
  capability stays **A0-gated (verified backup), reversible (undo handle), evidence-staged, and
  rate/blast-limited.**
- **Approval boundary intact:** no runtime enablement of a NEW host without explicit approval —
  autonomy applies to *previously approved* hosts (goal 2).
- **Learning loops (AR5, AA2, AA4) must be transparent + overridable** and must not silently widen
  the envelope; the operator's declared policy (AF2) is the ceiling.
- Registry sites are adult cam sites — nothing autonomous circulates a real capture or mimics them in
  fixtures.

### Suggested priority for the expansion set
Harden the keystone first: **A0+ verified backup → AR1 transactional writes → AR3 health interlock →
AR4 blast-radius limits** (these make *all* later autonomy safe). Then the trust-builders **AF6
dry-run loop + AF7 operator digest + AF5 timeline** (operator sees what the loop would/did do). Then
the smarts: **AF1 confidence scoring + AF2 policy-as-config**, enabling **AA1 tiered levels** as the
safe path to the A9 capstone. Defer **AA4 learning** until the envelope + oversight are mature (it's
the highest-leverage but highest-care item).

---

## 7. Deeper autonomy — items that close the loop (from the L2 + broader-discovery decisions)

Beyond §6, three capabilities push autonomy meaningfully further AND map directly to the two
decisions just made (L2 act-on-approved-hosts; broader candidate discovery). All three stay **A0-gated,
reversible, evidence-staged, new-host-approval-intact.**

- **A-REPAIR — auto-repair on drift (the headline autonomy gap).** Today A3 *detects and disables*
  (quarantine) on drift. A-REPAIR *fixes*: on drift, **re-derive the template from a fresh capture →
  re-lint → if gates pass + A0 backup lands → auto-apply (L2 act+notify)**; only fall back to A3
  quarantine if the repair fails its gates. This is the difference between "autonomously notices the
  site changed" and "autonomously heals it" — the single biggest jump in unattended uptime.
  `AUTOMATION_POLICY.md` already lists auto-*repair* as roadmap (distinct from A2 auto-*refresh*: A2
  re-applies on passing re-capture; A-REPAIR is **drift-triggered re-derivation**). Sequence:
  needs A0 + A1 (drift) + A2 (the apply mechanism); it composes them into a closed loop. Highest-value
  addition at L2.
- **A-DISCO — enumerate → triage → auto-queue (activates the level-4 scope decision).** The scope
  choice is **full enumeration of approved hosts**; A-DISCO makes it autonomous: **enumerate an approved
  host to any depth, auto-score candidates (AF1), and auto-queue the high-confidence ones for capture**
  (low-confidence → review). Bounded by the compensating guardrails from the charter revision:
  **review-on-uncertainty** (nav-looking / generic-selector candidates rejected) and
  **politeness/rate-limiting** (enumerating a whole library must not hammer the host).
  **AR4 blast-radius/rate limits matter MORE at full enumeration** — a large host could yield thousands
  of candidates in one pass; cap per-run enqueue + throttle discovery so a single enumeration can't
  flood the queue or the site. Stays within already-approved hosts only; never auto-approves a new host.
- **A-PIPE — pipeline orchestration (the capstone architecture).** Have `automation_controller` run
  the full chain **capture → build → lint → blocked-term scan → drift/stage → (L2) apply** as ONE
  checkpointed autonomous flow with a rollback point at each stage, instead of discrete operator-poked
  steps. Each checkpoint is A0-backed and abortable; a failed stage halts + stages evidence. This is
  what A9 (supervised autonomy) actually *runs* once A-REPAIR + A-DISCO exist — the loop that ties
  every A-item into a single unattended pipeline the operator supervises via the AF5 timeline + AF7
  digest.

**How these three change the build order:** A0+ (verified backup) is still first. Then A1 (drift) and
A2 (apply) as before — but now their real payoff is **A-REPAIR** (drift→heal loop), which becomes the
L2 headline. **A-DISCO** lands alongside the broader-discovery scope change. **A-PIPE** is the
capstone wiring (with A9), gated on the master off-switch + proven full reversibility.

**Guardrails unchanged:** every auto-repair / auto-queue / pipeline stage is A0-backed + reversible +
evidence-staged; new-host enablement still needs explicit approval; discovery stays within approved
hosts, bounded by review-on-uncertainty + politeness; findings as kinds/counts, never values.
